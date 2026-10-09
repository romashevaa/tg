from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import median, pstdev
from types import SimpleNamespace

from .models import HistSignal
from .risk import PlanError, choose_entry

# Fixed stop: exit everything at one target, or in equal parts at every target.
# Moving stop: after the first target the stop goes to the entry ("be"), or follows one target
# behind the price ("trail"), as many channels advise their readers to do.
STRATEGIES = ("first", "middle", "last", "ladder", "ladder_be", "ladder_trail", "last_be")
# Statuses that count as a real trade with a known outcome.
RESOLVED = ("stopped", "targets", "expired")
# 2.45 / 1.64: the 90% margin widened for a choice among seven exits.
STRICT = 1.5


@dataclass
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float


@dataclass
class Trade:
    message_id: int
    symbol: str
    side: str
    status: str
    entry: float = 0.0
    tps_hit: int = 0
    tps_total: int = 0
    sl_pct: float = 0.0
    rr_first: float = 0.0
    rr_last: float = 0.0
    price0: float = 0.0
    order: str = ""
    r: dict = field(default_factory=dict)


def levels_ok(sig: HistSignal) -> bool:
    if sig.stop_loss is None or not sig.take_profits:
        return False
    levels = sig.entries + [sig.stop_loss] + sig.take_profits
    if any(x <= 0 for x in levels):
        return False
    if not sig.entries:
        return True
    low, high = min(sig.entries), max(sig.entries)
    if sig.side == "long":
        return sig.stop_loss < low and all(tp > high for tp in sig.take_profits)
    return sig.stop_loss > high and all(tp < low for tp in sig.take_profits)


def simulate(sig: HistSignal, symbol: str, candles: list[Candle], tol_pct: float, limit_when_ran: bool,
             fee_pct: float, complete: bool, limit_expiry_hours: float = 24, strict: bool = False) -> Trade:
    """Replay one call on price history the way the live bot would have traded it.

    Candles start at the post time. When a stop and a target fall inside the same candle the
    stop is taken first, so results lean pessimistic. `complete` says the candles cover the
    whole evaluation horizon; if not, an unresolved trade is reported as still open.
    """
    trade = Trade(sig.message_id, symbol, sig.side, "invalid", tps_total=len(sig.take_profits))
    if sig.stop_loss is None:
        trade.status = "no_stop"
        return trade
    if not levels_ok(sig):
        return trade
    if not candles:
        trade.status = "no_data"
        return trade

    long = sig.side == "long"
    sl, tps = sig.stop_loss, sig.take_profits
    p0 = candles[0].o
    trade.price0 = p0
    # Same rule as the live validator: a call is dead once price is beyond the stop, and a
    # market call (no entry price) is dead once price is beyond the first target. With an entry
    # price, a price beyond the target simply means the entry order waits for a pullback.
    past_stop = p0 <= sl if long else p0 >= sl
    past_target = p0 >= tps[0] if long else p0 <= tps[0]
    # `strict` is for calls read from a picture: a screenshot of someone else's trade is often
    # posted after it has already paid, so a price beyond the first target ends it too.
    if past_stop:
        trade.status = "stale"
        return trade
    if past_target and (strict or not sig.entries):
        trade.status = "late"
        return trade

    shape = SimpleNamespace(
        side=sig.side,
        entry_low=min(sig.entries) if sig.entries else None,
        entry_high=max(sig.entries) if sig.entries else None,
    )
    try:
        order_type, entry = choose_entry(shape, p0, tol_pct, limit_when_ran)
    except PlanError:
        trade.status = "missed"
        return trade

    trade.order = order_type
    fill = 0
    if order_type == "LIMIT":
        # A resting entry order is cancelled after `limit_expiry_hours`.
        deadline = candles[0].t + limit_expiry_hours * 3_600_000
        fill = next((i for i, c in enumerate(candles)
                     if c.t <= deadline and (c.l <= entry if long else c.h >= entry)), -1)
        if fill < 0:
            waited_out = complete or candles[-1].t >= deadline
            trade.status = "not_filled" if waited_out else "open"
            return trade

    risk = abs(entry - sl)
    if risk <= 0:
        return trade
    limit = order_type == "LIMIT"
    hit, stopped, rest = _walk(candles, fill, limit, long, entry, sl, tps, "static")
    if not stopped and not all(hit) and not complete:
        trade.status = "open"
        return trade

    rr = [abs(tp - entry) / risk for tp in tps]
    fee = fee_pct / 100 * entry / risk
    mid = (len(tps) - 1) // 2
    hit_be, _, rest_be = _walk(candles, fill, limit, long, entry, sl, tps, "be")
    hit_tr, _, rest_tr = _walk(candles, fill, limit, long, entry, sl, tps, "trail")

    def parts(hits: list[bool], remainder: float) -> float:
        return sum(rr[k] if hits[k] else remainder for k in range(len(tps))) / len(tps)

    trade.status = "stopped" if stopped else ("targets" if all(hit) else "expired")
    trade.entry = entry
    trade.tps_hit = sum(hit)
    trade.sl_pct = risk / entry * 100
    trade.rr_first, trade.rr_last = rr[0], rr[-1]
    trade.r = {
        "first": (rr[0] if hit[0] else rest) - fee,
        "middle": (rr[mid] if hit[mid] else rest) - fee,
        "last": (rr[-1] if hit[-1] else rest) - fee,
        "ladder": parts(hit, rest) - fee,
        "ladder_be": parts(hit_be, rest_be) - fee,
        "ladder_trail": parts(hit_tr, rest_tr) - fee,
        "last_be": (rr[-1] if hit_be[-1] else rest_be) - fee,
    }
    return trade


def _walk(candles: list[Candle], fill: int, limit: bool, long: bool, entry: float, sl: float,
          tps: list[float], mode: str) -> tuple[list[bool], bool, float]:
    """Follow price from the fill. Returns (targets reached, stopped out, result of the unsold rest in R).

    `mode` is how the stop behaves once targets are reached: "static" never moves it, "be" moves
    it to the entry after the first target, "trail" keeps it one target behind the last one reached.
    """
    risk = abs(entry - sl)
    sign = 1 if long else -1
    stop = sl
    hit = [False] * len(tps)
    for j in range(fill, len(candles)):
        c = candles[j]
        if c.l <= stop if long else c.h >= stop:
            return hit, True, (stop - entry) * sign / risk
        # In the candle that fills a limit order the later path is unknown: no targets counted.
        if j == fill and limit:
            continue
        reached = False
        for k, tp in enumerate(tps):
            if not hit[k] and (c.h >= tp if long else c.l <= tp):
                hit[k] = reached = True
        if all(hit):
            return hit, False, 0.0
        if reached and mode != "static":
            top = max(k for k in range(len(tps)) if hit[k])
            stop = entry if (mode == "be" or top == 0) else tps[top - 1]
            # The same candle may have come back through the new stop after touching the target.
            if c.c <= stop if long else c.c >= stop:
                return hit, True, (stop - entry) * sign / risk
    return hit, False, (candles[-1].c - entry) * sign / risk


def structure(signals: list[HistSignal]) -> dict:
    """What the calls look like on paper, before any price history: stop distance, reward to risk."""
    rows = []
    for sig in signals:
        if not levels_ok(sig) or not sig.entries:
            continue
        entry = max(sig.entries) if sig.side == "long" else min(sig.entries)
        risk = abs(entry - sig.stop_loss)
        rows.append((risk / entry * 100, abs(sig.take_profits[0] - entry) / entry * 100,
                     abs(sig.take_profits[0] - entry) / risk, abs(sig.take_profits[-1] - entry) / risk,
                     sig.leverage or 0))
    if not rows:
        return {}
    rr_first = median(r[2] for r in rows)
    levs = [r[4] for r in rows if r[4]]
    return {
        "calls": len(rows),
        "sl_pct": round(median(r[0] for r in rows), 2),
        "tp1_pct": round(median(r[1] for r in rows), 2),
        "rr_first": round(rr_first, 2),
        "rr_last": round(median(r[3] for r in rows), 2),
        # Share of calls that must reach the first target just to break even when exiting there.
        "breakeven_first": round(100 / (1 + rr_first)),
        "leverage": median(levs) if levs else 0,
        # With isolated margin a position is liquidated after roughly 100/leverage % against it.
        "stop_beyond_liquidation": sum(1 for r in rows if r[4] and r[0] * r[4] >= 100),
    }


def summarize(trades: list[Trade], found: int, messages: int, forwards: int, start: datetime, end: datetime,
              min_trades: int, dropped: dict | None = None) -> dict:
    counts: dict[str, int] = {}
    for t in trades:
        counts[t.status] = counts.get(t.status, 0) + 1
    done = [t for t in trades if t.status in RESOLVED]
    days = max((end - start).total_seconds() / 86400, 1.0)

    strategies = {}
    for name in STRATEGIES:
        rs = [t.r[name] for t in done]
        avg = sum(rs) / len(rs) if rs else 0.0
        # 90% confidence interval of the average result per trade.
        margin = 1.64 * pstdev(rs) / len(rs) ** 0.5 if len(rs) > 1 else 0.0
        strategies[name] = {
            "wins": sum(1 for r in rs if r > 0),
            "total_r": round(sum(rs), 2),
            "avg_r": round(avg, 3),
            "low_r": round(avg - margin, 3),
            "high_r": round(avg + margin, 3),
            # The best of several exits is picked afterwards, which flatters the result; the
            # verdict therefore asks for a wider margin than the range shown for one exit alone.
            "strict_low_r": round(avg - margin * STRICT, 3),
            "strict_high_r": round(avg + margin * STRICT, 3),
        }
    best = max(STRATEGIES, key=lambda n: strategies[n]["avg_r"]) if done else None

    no_pair = counts.get("no_pair", 0)
    if found == 0:
        verdict = "no_signals"
    elif not done and no_pair >= 0.8 * found:
        verdict = "not_on_exchange"
    elif len(done) < min_trades:
        verdict = "insufficient"
    elif strategies[best]["strict_low_r"] > 0:
        verdict = "good"
    elif strategies[best]["strict_high_r"] < 0:
        verdict = "bad"
    else:
        verdict = "neutral"

    return {
        "at": end.isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "days": round(days, 1),
        "messages": messages,
        "forward_share": round(forwards / messages * 100) if messages else 0,
        "found": found,
        "per_day": round(found / days, 1),
        "tested": len(done),
        "counts": counts,
        "dropped": dropped or {},
        "stopped": counts.get("stopped", 0),
        "tp1_reached": sum(1 for t in done if t.tps_hit >= 1),
        "all_tps_reached": counts.get("targets", 0),
        "median_sl_pct": round(median(t.sl_pct for t in done), 2) if done else 0.0,
        "median_rr_first": round(median(t.rr_first for t in done), 2) if done else 0.0,
        "median_rr_last": round(median(t.rr_last for t in done), 2) if done else 0.0,
        "strategies": strategies,
        "best": best,
        "verdict": verdict,
        "trades": [asdict(t) for t in trades],
    }


def drift(side: str, candles: list[Candle], hours: tuple[int, ...] = (24, 72)) -> dict | None:
    """What price did after a call that gives a direction but no levels.

    Returns the move in the called direction, in percent, at each checkpoint, plus the best and
    the worst point reached before the last checkpoint. None when history is too short.
    """
    if not candles:
        return None
    sign = 1 if side == "long" else -1
    p0, t0 = candles[0].o, candles[0].t
    last = t0 + max(hours) * 3_600_000
    if candles[-1].t < last - 3_600_000:
        return None
    out = {}
    for h in hours:
        at = next((c for c in candles if c.t >= t0 + h * 3_600_000), candles[-1])
        out[f"h{h}"] = (at.o - p0) / p0 * 100 * sign
    window = [c for c in candles if c.t <= last]
    high, low = max(c.h for c in window), min(c.l for c in window)
    best, worst = (high, low) if sign > 0 else (low, high)
    out["best"] = (best - p0) / p0 * 100 * sign
    out["worst"] = (worst - p0) / p0 * 100 * sign
    return out


def summarize_drift(rows: list[dict], total: int, with_photo: int) -> dict:
    if not rows:
        return {"total": total, "with_photo": with_photo, "tested": 0}
    keys = [k for k in rows[0] if k.startswith("h")]
    out = {"total": total, "with_photo": with_photo, "tested": len(rows),
           "best": round(median(r["best"] for r in rows), 1), "worst": round(median(r["worst"] for r in rows), 1)}
    for k in keys:
        values = [r[k] for r in rows]
        out[k] = round(median(values), 1)
        out[k + "_up"] = round(sum(1 for v in values if v > 0) / len(values) * 100)
    return out
