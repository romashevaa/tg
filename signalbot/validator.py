from __future__ import annotations

import re
from datetime import datetime

from .config import Config
from .models import Decision, MarketInfo, ParsedSignal, TgMessage, TvIdea
from .numbers import ungrounded
from .risk import PlanError, build_plan, pick_take_profit
from .storage import Storage


def symbol_for(sig: ParsedSignal) -> str | None:
    base = re.sub(r"[^A-Z0-9]", "", sig.base_asset.upper())
    quote = re.sub(r"[^A-Z]", "", (sig.quote_asset or "USDT").upper()) or "USDT"
    # Coin-margined and "USD" quotes name the same market the bot trades against USDT.
    if quote == "USD":
        quote = "USDT"
    if not base:
        return None
    if base.endswith(quote) and len(base) > len(quote):
        base = base[: -len(quote)]
    return f"{base}-{quote}"


def market_for(sig: ParsedSignal, profile_market: str | None, default: str) -> str:
    if sig.market in ("futures", "spot"):
        return sig.market
    if profile_market in ("futures", "spot"):
        return profile_market
    return default


def client_id(msg: TgMessage) -> str:
    return f"sb{abs(msg.channel_id)}m{msg.message_id}"[:40]


def source_age_minutes(msg: TgMessage, ideas: list[TvIdea], now: datetime) -> float:
    """Age of the oldest origin of the call: the post, the forwarded original or the TV idea."""
    dates = [msg.date]
    if msg.fwd_date:
        dates.append(msg.fwd_date)
    dates += [i.published for i in ideas if i.published]
    return (now - min(dates)).total_seconds() / 60


def decide(
    sig: ParsedSignal,
    msg: TgMessage,
    ideas: list[TvIdea],
    source_text: str,
    info: MarketInfo | None,
    equity: float,
    open_positions: int,
    cfg: Config,
    storage: Storage,
    now: datetime,
    force: bool = False,
) -> Decision:
    """Turn a parsed new_signal into execute / manual / reject.

    Hard problems reject. Soft doubts send the call to manual approval. `force` is used when the
    owner approves a call by hand: soft doubts and the age check are skipped, hard checks stay.
    """
    v, r = cfg.validator, cfg.risk
    hard: list[str] = []
    soft: list[str] = []

    if sig.kind != "new_signal":
        return Decision("ignore", [f"тип повідомлення: {sig.kind}"])
    if sig.side not in ("long", "short") or not sig.base_asset:
        return Decision("reject", ["немає монети або напрямку"])
    if info is None:
        return Decision("reject", [f"пари {symbol_for(sig)} немає на біржі або торгівля закрита"])
    if info.market == "spot" and sig.side == "short":
        return Decision("reject", ["шорт на споті неможливий"])

    long = sig.side == "long"
    price = info.price
    sl = sig.stop_loss
    tps = sig.take_profits
    entries = [e for e in (sig.entry_low, sig.entry_high) if e is not None]
    levels = entries + ([sl] if sl is not None else []) + tps
    if any(x <= 0 for x in levels):
        return Decision("reject", ["нульовий або від'ємний рівень"])

    # Consistency of levels between themselves.
    ref_low = min(entries) if entries else price
    ref_high = max(entries) if entries else price
    if sl is not None and (sl >= ref_low if long else sl <= ref_high):
        hard.append("стоп з неправильного боку від входу")
    if tps and any((tp <= ref_high if long else tp >= ref_low) for tp in tps) and entries:
        hard.append("тейк з неправильного боку від входу")

    # Every number must literally be in the text unless it was read from a picture.
    if not sig.numbers_from_image:
        missing = ungrounded(levels, source_text)
        if missing:
            hard.append("чисел немає в тексті повідомлення: " + ", ".join(f"{m:g}" for m in missing))

    # Live price against the call: the strongest staleness test.
    if sl is not None and (price <= sl if long else price >= sl):
        hard.append(f"ціна {price:g} вже за стопом {sl:g}")
    # With an entry price, a price beyond the first target means the entry order waits for a
    # pullback. A market call with no entry price has nothing to wait for: it is spent.
    # A screenshot or a forward of someone else's trade is often shown after it has already paid,
    # so for those a price beyond the first target ends the call as well.
    secondhand = sig.numbers_from_image or msg.fwd_date is not None
    if tps and (secondhand or not entries) and (price >= tps[0] if long else price <= tps[0]):
        hard.append(f"ціна {price:g} вже дійшла до першої цілі {tps[0]:g}")

    if not force:
        age = source_age_minutes(msg, ideas, now)
        if age > v.max_age_minutes:
            hard.append(f"сигналу {age:.0f} хв, ліміт {v.max_age_minutes:g} хв")
        if msg.fwd_date and v.reject_forwards:
            hard.append("переслане повідомлення")

    entry_ref = (ref_low + ref_high) / 2
    dup = storage.find_duplicate(info.symbol, sig.side, entry_ref, v.dedup_hours, v.dedup_price_tolerance_pct)
    if dup is not None and not (dup["channel_id"] == msg.channel_id and dup["message_id"] == msg.message_id):
        hard.append(f"дублікат сигналу #{dup['id']}")
    if storage.message_was_traded(msg.channel_id, msg.message_id):
        hard.append("за цим повідомленням угода вже відкрита")
    if open_positions >= r.max_open_positions:
        hard.append(f"відкрито {open_positions} позицій, ліміт {r.max_open_positions}")

    if hard:
        return Decision("reject", hard)

    # Soft doubts.
    threshold = cfg.ai.min_confidence_image if sig.numbers_from_image else cfg.ai.min_confidence
    if sig.confidence < threshold:
        soft.append(f"впевненість {sig.confidence:.2f} нижча за {threshold:.2f}")
    if sig.stale_hints:
        soft.append("ознаки старого сигналу: " + "; ".join(sig.stale_hints[:3]))
    if sig.entry_type == "breakout":
        soft.append("вхід на пробій автоматично не виставляється")
    if sl is None and v.require_stop_loss:
        soft.append("немає стоп-лосу")

    try:
        plan = build_plan(sig, info, equity, r, v.entry_tolerance_pct, client_id(msg))
    except PlanError as e:
        return Decision("reject", [str(e)])

    if plan.stop_loss is not None:
        dist_pct = abs(plan.entry_price - plan.stop_loss) / plan.entry_price * 100
        if dist_pct < v.min_sl_distance_pct:
            soft.append(f"стоп за {dist_pct:.2f}% від входу, занадто близько")
        if dist_pct > v.max_sl_distance_pct:
            soft.append(f"стоп за {dist_pct:.1f}% від входу, занадто далеко")
        tp = pick_take_profit(tps, r.take_profit)
        if tp is not None:
            rr = abs(tp - plan.entry_price) / abs(plan.entry_price - plan.stop_loss)
            if rr < v.min_rr:
                soft.append(f"R:R {rr:.2f} нижче за {v.min_rr:g}")

    if soft and not force:
        return Decision("manual", soft, plan)
    return Decision("execute", soft, plan)
