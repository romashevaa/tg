"""Read-only, candle-close based TradingView entry review.

This intentionally does not send orders or create resting LIMIT orders. An AI
chart interpretation is not an instruction to enter. Fail closed on poor data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from tv_trade_diagnostics import bingx_pair
from tv_timeframe_policy import normalize_timeframe

INTERVAL_MS = {'1m': 60_000, '5m': 300_000, '15m': 900_000,
               '30m': 1_800_000, '1h': 3_600_000, '4h': 14_400_000,
               '1d': 86_400_000}


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    pair: str | None = None
    price: float | None = None
    interval: str | None = None


def review_candles(evidence, candles, now_ms: int, *, interval: str, proximity_pct: float = 0.35) -> Decision:
    """Closed-candle confirmation. Never interpret a still-forming candle as confirmed."""
    pair = bingx_pair(evidence.symbol)
    if interval not in INTERVAL_MS:
        return Decision('CHECK', 'Невідомий таймфрейм для аналізу свічок', pair)
    span = INTERVAL_MS[interval]
    rows = sorted(candles)
    closed = [r for r in rows if len(r) >= 5 and r[0] + span <= now_ms]
    if len(closed) < 4:
        return Decision('CHECK', 'Недостатньо закритих свічок BingX', pair, interval=interval)
    if closed[-1][0] + 2 * span < now_ms:
        return Decision('CHECK', 'Останні свічки BingX застаріли', pair, interval=interval)
    if any(not all(math.isfinite(float(v)) for v in r[1:5]) or
           float(r[3]) <= 0 or float(r[3]) > float(r[2]) or
           not (float(r[3]) <= float(r[4]) <= float(r[2])) for r in closed[-4:]):
        return Decision('CHECK', 'Некоректні OHLC дані', pair, interval=interval)
    entry = evidence.entry
    if entry is None or not evidence.stop_loss or not evidence.targets:
        return Decision('INVALID', 'Немає повних ENTRY, SL, TP', pair, interval=interval)
    entry, sl = float(entry), float(evidence.stop_loss)
    price = float(closed[-1][4])
    if entry <= 0 or sl <= 0:
        return Decision('INVALID', 'Некоректні рівні', pair, price, interval)
    side = evidence.direction
    target = float(evidence.targets[0])
    if side not in ('LONG', 'SHORT'):
        return Decision('INVALID', 'Не визначено напрямок', pair, price, interval)
    if side == 'LONG' and (price <= sl or price >= target):
        return Decision('INVALID', 'Ціна вже поза допустимим діапазоном SL/TP', pair, price, interval)
    if side == 'SHORT' and (price >= sl or price <= target):
        return Decision('INVALID', 'Ціна вже поза допустимим діапазоном SL/TP', pair, price, interval)
    distance = abs(price - entry) / entry * 100
    if distance > proximity_pct:
        return Decision('WAITING', f'До ENTRY {distance:.2f}%; чекаємо зону, ордер не ставимо', pair, price, interval)
    last, prev = closed[-1], closed[-2]
    # Require an actual closed-candle reversal/rejection near original ENTRY.
    # This is a conservative heuristic, not proof that the author's setup survives.
    if side == 'LONG':
        confirmed = (last[4] > last[1] and last[4] > prev[4] and
                     last[3] <= entry * (1 + proximity_pct / 100) and last[4] >= entry)
    else:
        confirmed = (last[4] < last[1] and last[4] < prev[4] and
                     last[2] >= entry * (1 - proximity_pct / 100) and last[4] <= entry)
    if not confirmed:
        return Decision('CONFIRMING', 'Ціна біля ENTRY, але немає підтвердження закритою свічкою', pair, price, interval)
    return Decision('READY_REVIEW', 'Є підтверджувальна закрита свічка; потрібні перевірки повної історії, автора й біржового захисту', pair, price, interval)


async def inspect(exchange, evidence, *, now=None):
    """Returns a non-executing verdict from public BingX candles."""
    tf = normalize_timeframe(getattr(evidence, 'timeframe', None))
    interval = tf if tf in INTERVAL_MS else None
    pair = bingx_pair(evidence.symbol)
    if not pair:
        return Decision('INVALID', 'Немає відповідного крипто USDT-символу')
    if not interval:
        return Decision('CHECK', 'TF невідомий або не підтримується для підтвердження', pair)
    if (str(evidence.category).upper() not in ('NEW_CALL', 'CONDITIONAL')
        or str(evidence.confidence).upper() != 'HIGH'):
        return Decision('INVALID', 'Не підтверджений новий торговий сигнал', pair, interval=interval)
    if evidence.entry is None or evidence.stop_loss is None or not evidence.targets:
        return Decision('INVALID', 'Немає повних ENTRY/SL/TP', pair, interval=interval)
    if evidence.direction == 'LONG' and not (evidence.stop_loss < evidence.entry < min(evidence.targets)):
        return Decision('INVALID', 'Некоректна геометрія LONG', pair, interval=interval)
    if evidence.direction == 'SHORT' and not (max(evidence.targets) < evidence.entry < evidence.stop_loss):
        return Decision('INVALID', 'Некоректна геометрія SHORT', pair, interval=interval)
    info = await exchange.market_info(pair, 'futures')
    if info is None:
        return Decision('INVALID', 'Контракт BingX недоступний', pair, interval=interval)
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    start = now_ms - INTERVAL_MS[interval] * 12
    try:
        bars = await exchange.klines(pair, 'futures', interval, start, now_ms, limit=15)
    except Exception as exc:
        return Decision('CHECK', f'Помилка історії BingX: {type(exc).__name__}', pair, interval=interval)
    return review_candles(evidence, bars, now_ms, interval=interval)
