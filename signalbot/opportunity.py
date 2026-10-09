"""Read-only market context for /analyze. Not an execution or order recommendation engine."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from math import isfinite


def _fmt(n):
    return f"{n:,.6g}" if n is not None and isfinite(n) else "невідомо"


def summarize(rows, interval):
    """Use only CLOSED candles. Last candle may still be open and is excluded by caller."""
    if len(rows) < 30:
        raise ValueError(f"{interval}: недостатньо свічок ({len(rows)})")
    closes = [r[4] for r in rows]
    recent = rows[-20:]
    last = closes[-1]
    ma20 = sum(closes[-20:])/20
    ma50 = sum(closes[-50:])/50 if len(rows) >= 50 else None
    high = max(r[2] for r in recent)
    low = min(r[3] for r in recent)
    prior_high = max(r[2] for r in rows[-21:-1])
    prior_low = min(r[3] for r in rows[-21:-1])
    trend = "up" if ma50 is not None and last > ma20 > ma50 else "down" if ma50 is not None and last < ma20 < ma50 else "mixed"
    return dict(interval=interval, close=last, ma20=ma20, ma50=ma50, high20=high, low20=low,
                prior_high=prior_high, prior_low=prior_low, trend=trend, last_high=rows[-1][2], last_low=rows[-1][3], previous_close=closes[-2])


def assess(sig, price, frames):
    """Never invent entry, TP or SL; return conservative diagnostics only."""
    if price is None or price <= 0:
        return "Недостатньо даних для оцінки: актуальна ціна недоступна. Ордерів немає."
    lines = ["📊 ОЦІНКА МОЖЛИВОСТІ (тільки спостереження)"]
    if not frames:
        return "\n".join(lines + ["Свічки недоступні; оцінити структуру ринку не можна.", "Статус: NO_DATA · без ордерів."])
    for key in ("15m", "1h", "4h"):
        frame = frames.get(key)
        if not frame:
            continue
        trend = {"up":"зростання", "down":"падіння", "mixed":"змішаний"}[frame['trend']]
        lines.append(f"{key}: {trend} · close {_fmt(frame['close'])} · MA20 {_fmt(frame['ma20'])} · діапазон 20 свічок {_fmt(frame['low20'])}–{_fmt(frame['high20'])}")
    if sig.side not in ('long','short'):
        lines.append("Напрямок невизначений; нового входу немає.")
        return "\n".join(lines + ["Статус: WAIT / NEEDS_CONTEXT · без ордерів."])
    directions = [frames[k]['trend'] for k in ('1h','4h') if k in frames]
    aligned = len(directions) == 2 and all(t == ('down' if sig.side=='short' else 'up') for t in directions)
    lines.append('1H/4H: ' + ('узгоджені з напрямком сценарію' if aligned else 'НЕ підтверджують узгоджений напрямок'))
    entries = [v for v in (sig.entry_low, sig.entry_high) if v is not None and v > 0]
    if entries:
        lo,hi = min(entries),max(entries)
        distance = 0 if lo <= price <= hi else min(abs(price-lo),abs(price-hi))/price*100
        lines.append(f"Авторський Entry {_fmt(lo)}–{_fmt(hi)} · відстань від ціни {distance:.2f}%")
        if sig.side == 'short' and price < lo or sig.side == 'long' and price > hi:
            lines.append('Рух уже пішов у прибутковому напрямку: не наздоганяти MARKET без нового плану.')
        elif sig.side == 'short' and price > hi or sig.side == 'long' and price < lo:
            lines.append('Ціна по інший бік Entry: перевірити інвалідацію і чи потрібен тригер, не ставити LIMIT автоматично.')
    else:
        lines.append('Авторський Entry не визначено; не підміняємо його локальними рівнями ринку.')
    sl = sig.stop_loss
    targets = sig.take_profits or []
    valid_stop = sl is not None and (sl < price if sig.side=='long' else sl > price)
    valid_tps = [tp for tp in targets if tp > price] if sig.side=='long' else [tp for tp in targets if tp < price]
    if valid_stop and valid_tps:
        risk = abs(price-sl)
        rr = max(abs(tp-price)/risk for tp in valid_tps) if risk else 0
        lines.append(f"Поточний приблизний gross R:R до найвіддаленішого доступного TP: {rr:.2f} (без комісій/проскальзування)")
    else:
        lines.append('Немає повного актуального Entry/SL/TP з правильним порядком рівнів: обґрунтований R:R неможливий.')
    lines.append('Статус: ' + ('WATCH / MANUAL_REVIEW' if aligned else 'WAIT / MANUAL_REVIEW') + ' · MARKET/LIMIT не рекомендуються автоматично; без ордерів.')
    lines.append('Це індикатори, не підтвердження Double Top або пробою neckline; ордерів немає.')
    return "\n".join(lines)


async def inspect(exchange, symbol, market, sig, price):
    if not symbol:
        return '📊 ОЦІНКА МОЖЛИВОСТІ\nНевідома торгова пара. Без ордерів.'
    now = datetime.now(timezone.utc)
    end = int(now.timestamp()*1000)
    frames = {}
    errors = []
    for interval,minutes in (("15m",15),("1h",60),("4h",240)):
        # Request 90 prior candles, drop current still-forming candle.
        start = end - 91*minutes*60*1000
        try:
            rows = await exchange.klines(symbol, market, interval, start, end, limit=100)
            closed = [r for r in rows if r[0]+minutes*60*1000 <= end and all(isfinite(x) and x>0 for x in r[1:])]
            frames[interval] = summarize(closed,interval)
        except Exception as exc:
            errors.append(f"{interval}: {type(exc).__name__}: {str(exc)[:75]}")
    report = assess(sig,price,frames)
    if errors:
        report += "\nПомилки даних: " + '; '.join(errors)
    return report


async def watch_snapshot(exchange, symbol, market):
    """Read-only snapshot using CLOSED market candles, with no execution access."""
    now = datetime.now(timezone.utc)
    end = int(now.timestamp()*1000)
    frames = {}
    for interval, minutes in (("15m",15),("1h",60),("4h",240)):
        rows = await exchange.klines(symbol, market, interval, end - 91*minutes*60*1000, end, limit=100)
        closed = [r for r in rows if r[0]+minutes*60*1000 <= end and all(isfinite(x) and x>0 for x in r[1:])]
        frames[interval] = summarize(closed, interval)
        frames[interval]['last_closed_start'] = int(closed[-1][0])
        frames[interval]['previous_close'] = closed[-2][4]
    return frames
