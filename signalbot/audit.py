"""Read-only manual signal audit. Does not submit, approve, or save orders."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Channel, TgMessage
from .telegram import to_message
from .tradingview import find_tv_links
from .validator import symbol_for
from .opportunity import inspect
from .scenario import plan, describe
from .history_audit import historical_check
from .monthly import record as record_monthly_audit

# Manual historical audits must separate what the author originally posted from
# whether the setup can still be entered today. This never modifies live ingestion.
DIRECT_CALL_RE = re.compile(r"\b(?:selling|buying|sell|buy)\s+(?:\$?[A-Z0-9]+\s+)?here\b|\b(?:enter|entry|open)\s+(?:a\s+)?(?:long|short)\b", re.I)
TRADE_LEVEL_RE = re.compile(r"\b(?:entry|enter|buy\s*zone|sell\s*zone)\s*(?:price|level|zone)?\s*[:=@-]?\s*\$?\d|\b(?:stop\s*loss|stoploss|sl)\s*[:=@-]?\s*\$?\d", re.I)
RESULT_RE = re.compile(r"\b(?:tp\s*\d*\s*(?:hit|reached)|target\s*(?:hit|reached)|closed\s*(?:in|at)\s*profit|already\s*(?:hit|reached)|as\s+predicted|since\s+we\s+told|profit\s+recap)\b", re.I)


def original_source_message(msg, ideas, direct_tv):
    """Replace synthetic /analyze invocation with source idea and source timestamp.

    Never used for current Telegram posts, replies or forwards.
    """
    if not direct_tv or not ideas:
        return msg
    original = next((i for i in ideas if i.title or i.description), ideas[0])
    return TgMessage(channel_id=0, message_id=0,
                     date=original.published or msg.date,
                     text=(original.title + "\n" + original.description[:3500]).strip(),
                     links=[original.url])


def classification_facets(sig, *, direct_tv, parent=None, forwarded=False, original_date=None):
    """Read-only faceted audit; never alters the live order classifier."""
    kind = sig.kind
    original = 'TRADE_SIGNAL' if kind == 'new_signal' else kind.upper()
    context = ('DIRECT_ORIGINAL_IDEA' if direct_tv else
               'REPLY_TO_PREVIOUS' if parent is not None else
               'FORWARDED_MESSAGE' if forwarded else 'TELEGRAM_MESSAGE')
    # Context safety: a reply / forwarded message does not get original permissions.
    execution = 'HISTORICAL_REVIEW_ONLY' if direct_tv else 'READ_ONLY_AUDIT'
    return original, context, execution



def historical_idea_classification(sig, ideas, *, direct_tv: bool):
    """Narrow audit-only correction for explicit author calls in historical TV ideas.

    Intentionally never upgrades a Telegram repost, reply, analysis or a chart
    based only on position-tool rectangles or inferred bearish/bullish direction.
    """
    if not direct_tv or sig.kind not in ("analysis", "demo_or_promo") or sig.side not in ("long", "short"):
        return sig
    if sig.entry_low is None or sig.entry_high is None:
        return sig
    if sig.stop_loss is None or not sig.take_profits:
        return sig
    source = " ".join(" ".join((i.title or "", i.description or "")) for i in ideas)
    if RESULT_RE.search(source):
        return sig
    if not (DIRECT_CALL_RE.search(source) or TRADE_LEVEL_RE.search(source)):
        return sig
        return sig
    sig.kind = "new_signal"
    sig.reason = "Original TradingView idea explicitly calls for entry; historical age is assessed separately from original message type. " + sig.reason
    return sig


def historical_status(sig, current_price, *, historic: bool):
    """Informational only; current price does not establish historical fill/TP sequence."""
    if not historic:
        return None
    if sig.kind != "new_signal":
        return "ORIGINAL_NOT_A_DIRECT_SIGNAL; no automatic entry"
    if current_price is None:
        return "HISTORICAL_SIGNAL; live price unavailable; NO_NEW_ENTRY"
    if sig.side == "short" and sig.take_profits and current_price <= max(sig.take_profits):
        return "HISTORICAL_SIGNAL / CURRENT_PRICE_BEYOND_TP; SKIP; TP timing not verified"
    if sig.side == "long" and sig.take_profits and current_price >= min(sig.take_profits):
        return "HISTORICAL_SIGNAL / CURRENT_PRICE_BEYOND_TP; SKIP; TP timing not verified"
    return "HISTORICAL_SIGNAL / REQUIRES_HISTORICAL_CANDLES; NO_NEW_ENTRY"


POST_RE = re.compile(r"https?://t\.me/(?:(?:c/)(\d+)|([A-Za-z]\w{3,}))/([0-9]+)(?:[/?#]|$)", re.I)


def format_audit(sig, ideas, image_count: int, current_price=None, source="", price_error=None, historic=False, facets=None) -> str:
    def val(x):
        return "невідомо" if x is None else str(x)
    lines = ["🔎 РУЧНИЙ АНАЛІЗ — БЕЗ ОРДЕРІВ", f"Джерело: {source or 'повідомлення'}",
             f"Тип: {sig.kind} · впевненість у розпізнаванні: {sig.confidence:.0%}",
             f"Пара: {sig.base_asset or '?'}{sig.quote_asset or 'USDT'} · напрямок: {sig.side}",
             f"Вхід: {sig.entry_type} · {val(sig.entry_low)} — {val(sig.entry_high)}",
             f"SL: {val(sig.stop_loss)} · TP: {', '.join(map(str, sig.take_profits)) or 'невідомо'}",
             f"Ціна BingX: {val(current_price)}", "", f"Пояснення AI: {sig.reason}"]
    if facets:
        original, context, execution = facets
        lines.extend([f"Оригінальний задум: {original}", f"Контекст джерела: {context}",
                      f"Статус виконання: {execution}"])
    if price_error:
        lines.append(f"Причина відсутності ціни: {price_error}")
    if sig.stale_hints:
        lines.append("Ознаки застарілості: " + "; ".join(sig.stale_hints[:5]))
    lines.append(f"TradingView: {len(ideas)} · завантажених зображень: {image_count}")
    for idea in ideas:
        lines.append(f"TV {idea.status}: {idea.title[:110] or idea.url}")
        if idea.error:
            lines.append(f"Помилка TV: {idea.error}")
    lines += ["", "⚠️ Це класифікація, НЕ торговий сигнал бота. Ордери не відкриваються."]
    status = historical_status(sig, current_price, historic=historic)
    if status:
        lines.append("Історична ідея / актуальність зараз: " + status)
        lines.append("Рішення: НЕ ВХОДИТИ зараз за історичною ідеєю; потрібні історичні свічки для оцінки виконання.")
    elif sig.kind != 'new_signal':
        lines.append("Рішення: НЕ ВХОДИТИ за цим повідомленням. Для нової угоди потрібні окремі правила входу.")
    elif sig.stop_loss is None or not sig.take_profits:
        lines.append("Рішення: ПОТРІБНА РУЧНА ПЕРЕВІРКА — немає повних SL/TP.")
    else:
        lines.append("Рішення: ПАРАМЕТРИ РОЗПІЗНАНО; виконуваність і R:R не перевірені, НЕ ВІДКРИВАТИ автоматично.")
    return "\n".join(lines)


async def analyze(pipeline, ref: str = "", reply_message=None, *, return_details=False):
    """Analyze a Telegram post URL, TradingView idea URL, or a replied-to message in private bot chat."""
    p = pipeline
    ref = ref.strip()
    match = POST_RE.search(ref)
    direct_tv = not match and bool(find_tv_links([ref]))
    if match:
        channel_ref = f"-100{match.group(1)}" if match.group(1) else f"@{match.group(2)}"
        channel = await p.tg.resolve(channel_ref, join=False)
        raw = await p.tg.client.get_messages(channel.channel_id, ids=int(match.group(3)))
        if not raw:
            raise ValueError("Повідомлення не доступне Telegram-акаунту читача")
        msg = to_message(raw, channel.channel_id)
        source = ref
    elif find_tv_links([ref]):
        channel = Channel(channel_id=0, title="TradingView manual audit", username=None, enabled=False)
        msg = TgMessage(channel_id=0, message_id=0, date=datetime.now(timezone.utc), text=ref, links=[ref])
        source = ref
    elif reply_message is not None:
        channel = Channel(channel_id=0, title="Forwarded Telegram message", username=None, enabled=False)
        # A forwarded post may lose its original links if Telegram strips entities: preserve the raw text.
        msg = to_message(reply_message, 0)
        source = "відповідь на переслане повідомлення"
    else:
        raise ValueError("Приклад: /analyze https://t.me/c/123456789/123 або /analyze https://www.tradingview.com/chart/…; також можна відповісти /analyze на переслане повідомлення")

    saved = p.storage.channel(channel.channel_id) if channel.channel_id else None
    if saved:
        channel = saved
    ideas, images = await p._enrich(msg, channel)
    if msg.has_photo and not images and p.tg and msg.channel_id:
        photo = await p.tg.photo(msg.channel_id, msg.message_id)
        if photo:
            images.insert(0, photo)
    context = []
    parent = None
    if msg.reply_to and msg.channel_id:
        raw_parent = await p.tg.client.get_messages(msg.channel_id, ids=msg.reply_to)
        if raw_parent:
            parent = to_message(raw_parent, msg.channel_id)
            context.append(parent)
            parent_ideas, parent_images = await p._enrich(parent, channel)
            ideas = parent_ideas + ideas
            images = parent_images + images
    # Direct TV URL is a request to inspect the original idea, NOT a Telegram repost
    # timestamped at the time of /analyze. Preserve real current-message context for posts.
    original_msg = original_source_message(msg, ideas, direct_tv)
    sig = await p.ai.parse(channel, original_msg, context, [], ideas, images[:3], datetime.now(timezone.utc))
    sig = historical_idea_classification(sig, ideas, direct_tv=direct_tv)
    # A retrospective reply does not become a fresh call just because it references
    # a directional chart. Keep it tied to its parent and make the label repeatable.
    if parent is not None and sig.kind in ("analysis", "result", "update"):
        comment = (msg.text or "").lower()
        retrospective = ("since the day" in comment or "we told you" in comment
                         or "as predicted" in comment or "as we said" in comment
                         or "as expected" in comment)
        if retrospective and not any(word in comment for word in ("enter now", "open short", "open long", "buy now", "sell now")):
            sig.kind = "result"
            sig.refers_to_message_id = parent.message_id
            sig.reason = (f"Retrospective follow-up to message #{parent.message_id}: "
                          "author reports an earlier prediction playing out; no fresh entry instruction.")
            if sig.side == "none" and ("bearish" in parent.text.lower() or "double top" in parent.text.lower()):
                sig.side = "short"
    price = None
    price_error = None
    # A market quote is useful for bearish/bullish analysis too, even when AI sets side=none.
    if sig.base_asset:
        symbol = symbol_for(sig)
        if symbol:
            try:
                price = await p.exchange.price(symbol, "futures" if sig.market != "spot" else "spot")
            except Exception as exc:
                price_error = f"{type(exc).__name__}: {str(exc)[:180]}"
        else:
            price_error = "неможливо побудувати торгову пару"
    facets = classification_facets(sig, direct_tv=direct_tv, parent=parent,
                                   forwarded=bool(msg.fwd_date),
                                   original_date=ideas[0].published if ideas else None)
    report = format_audit(sig, ideas, len(images), price, source, price_error,
                          historic=direct_tv, facets=facets)
    # Historic backtest is separate from classification; no order requests.
    history = None
    if direct_tv and ideas and sig.kind == 'new_signal':
        history = await historical_check(p.prices, symbol_for(sig), sig, ideas[0].published)
        if history:
            record_monthly_audit(p.storage.db, ideas[0].url, ideas[0].published, sig, history)
    if direct_tv:
        def fnum(value):
            return '—' if value is None else f'{value:.8g}'
        entries = (fnum(sig.entry_low) if sig.entry_low==sig.entry_high else
                   f'{fnum(sig.entry_low)}–{fnum(sig.entry_high)}')
        tps = ', '.join(fnum(tp) for tp in sig.take_profits) or '—'
        kind = 'СИГНАЛ' if sig.kind=='new_signal' else sig.kind.upper()
        outcome = (history or {'status':'NOT_CHECKED','detail':'Результат не перевірявся'})
        alternatives = outcome.get('alternatives') or {}
        alternative_lines = [f'  {name.upper()}: {value["status"]} — {value["detail"]}'
                             for name, value in alternatives.items()]
        lines = [f'🔎 {sig.base_asset or "?"}{sig.quote_asset or "USDT"} · {sig.side.upper()} · {kind}',
                 f'Entry {entries} ({sig.entry_type}) · SL {fnum(sig.stop_loss)}',
                 f'TP: {tps}',
                 f'📈 Історія: {outcome["status"]} — {outcome["detail"]}',
                 *alternative_lines,
                 '⚠️ Це моделювання за OHLC, не підтверджений прибуток. ' +
                 ('Джерело: '+outcome['source'] if outcome.get('source') else 'Дані обмежені.'),
                 '⛔ Зараз: SKIP — історична ідея, без ордерів.']
        report = '\n'.join(lines)
    else:
        def fnum(value):
            return '—' if value is None else f'{value:.8g}'
        entries = (fnum(sig.entry_low) if sig.entry_low == sig.entry_high else
                   f'{fnum(sig.entry_low)}–{fnum(sig.entry_high)}')
        tps = ', '.join(fnum(tp) for tp in sig.take_profits) or '—'
        report = '\n'.join([
            f'🔎 {sig.base_asset or "?"}{sig.quote_asset or "USDT"} · {sig.side.upper()} · {sig.kind.upper()}',
            f'Entry {entries} ({sig.entry_type}) · SL {fnum(sig.stop_loss)}',
            f'TP: {tps} · BingX: {fnum(price)}',
            f'🧭 Джерело: {facets[1] if facets else "TELEGRAM_MESSAGE"}',
            f'📌 {sig.reason[:210]}',
            '⛔ Без ордерів; новий вхід не підтверджений.'
        ])
    # Strictly read-only analysis. No executor, storage mutation, or order endpoint.
    symbol = symbol_for(sig) if sig.base_asset else None
    market = "spot" if sig.market == "spot" else "futures"
    try:
        context_report = await inspect(p.exchange, symbol, market, sig, price)
    except Exception as exc:
        context_report = f"📊 ОЦІНКА МОЖЛИВОСТІ\nПомилка: {type(exc).__name__}: {str(exc)[:150]} · без ордерів."
    # Telegram supports up to 4096 characters per message.
    # Do not present an old original call as a presently actionable scenario.
    scenario_report = ("🧭 Історичний сценарій: рівні автора збережені, але новий ордер за старою ідеєю заборонений."
                       if direct_tv else describe(plan(sig)))
    full_report = report[:4050]
    if return_details:
        return full_report, sig, symbol, market
    return full_report
