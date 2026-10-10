"""Fail-closed timeframe-aware freshness and exchange candle replay.

A cached TradingView idea is not a fresh trade. For delayed entry, check ALL
available post-publication BingX candles for prior entry/stop/target touches.
"""
from __future__ import annotations
import math
import re
from datetime import datetime, timezone

# Windows are eligibility ceilings, not promises that setups remain active.
WINDOWS = {'1m': 30, '3m': 60, '5m': 90, '15m': 120, '30m': 360,
           '1h': 720, '2h': 1440, '4h': 2880, '6h': 4320, '8h': 5760,
           '12h': 8640, '1d': 10080, '3d': 20160, '1w': 43200}


def normalize_timeframe(value):
    if not value:
        return None
    v = str(value).strip().lower()
    lookup = {'d': '1d', 'day': '1d', 'daily': '1d', 'w': '1w', 'weekly': '1w',
              'h': '1h', 'hourly': '1h', '60': '1h', '240': '4h', '15': '15m'}
    v = lookup.get(v, v)
    m = re.fullmatch(r'(\d+)\s*(m|min|minute|minutes|h|hr|hour|hours|d|day|days|w|week|weeks)', v)
    if m:
        n, unit = m.groups()
        v = n + ('m' if unit.startswith('m') else 'h' if unit.startswith('h') else 'd' if unit.startswith('d') else 'w')
    return v if v in WINDOWS else None


def freshness(published, timeframe, now=None, unknown_minutes=20):
    if published is None:
        return False, 'Невідомий час публікації'
    now = now or datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = (now - published).total_seconds() / 60
    if age < 0:
        return False, 'Дата ідеї у майбутньому'
    tf = normalize_timeframe(timeframe)
    max_minutes = WINDOWS[tf] if tf else unknown_minutes
    if age > max_minutes:
        return False, f'Ідеї {age:.0f} хв; TF={tf or "невідомий"}, максимальна актуальність {max_minutes} хв'
    return True, f'TF={tf or "невідомий"}; вік {age:.0f} хв / {max_minutes} хв'


def replay_interval(age_minutes):
    if age_minutes <= 720: return '1m'
    if age_minutes <= 2880: return '5m'
    if age_minutes <= 10080: return '15m'
    return '1h'


async def untouched_since_publication(exchange, pair, evidence, published, now=None):
    """Verify no stop/target/entry was hit since publication; fail on missing bars.

    Candle history is compared against all possible fills; without tick-level
    sequencing no older traded-through entry can be treated as a new limit fill.
    """
    now = now or datetime.now(timezone.utc)
    age = (now - published).total_seconds() / 60
    interval = replay_interval(age)
    # Start from the publication candle. Conservative: a touch in same candle blocks.
    ms_start = int(published.timestamp() * 1000)
    ms_end = int(now.timestamp() * 1000)
    data = await exchange._request('GET', '/openApi/swap/v3/quote/klines', {
        'symbol': pair, 'interval': interval, 'startTime': ms_start,
        'endTime': ms_end, 'limit': 1440}, signed=False)
    if not isinstance(data, list) or not data:
        return False, 'BingX не повернув історію свічок після публікації'
    candles = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            stamp, high, low = int(row[0]), float(row[2]), float(row[3])
        elif isinstance(row, dict):
            stamp, high, low = int(row['time']), float(row['high']), float(row['low'])
        else:
            return False, 'Некоректний формат свічок BingX'
        if not all(math.isfinite(x) and x > 0 for x in (high, low)) or low > high:
            return False, 'Некоректні OHLC BingX'
        candles.append((stamp, high, low))
    candles.sort()
    # API may return a truncated recent range. Verify a candle starts near the
    # beginning; otherwise do not pretend history was exhaustively checked.
    interval_ms = {'1m':60000,'5m':300000,'15m':900000,'1h':3600000}[interval]
    if candles[0][0] > ms_start + interval_ms:
        return False, 'Недостатньо старих свічок BingX для перевірки всієї історії'
    if candles[-1][0] < ms_end - 2 * interval_ms:
        return False, 'Історія свічок BingX не охоплює поточний момент'
    entry, sl = float(evidence.entry), float(evidence.stop_loss)
    tps = [float(x) for x in evidence.targets]
    for _, high, low in candles:
        if low <= entry <= high:
            return False, 'Ціна вже торкалася ENTRY після публікації — можливий пропущений вхід'
        if low <= sl <= high or (evidence.direction == 'LONG' and low <= sl) or (evidence.direction == 'SHORT' and high >= sl):
            return False, 'Ціна вже торкалася SL після публікації'
        if any(low <= tp <= high or (evidence.direction == 'LONG' and high >= tp) or (evidence.direction == 'SHORT' and low <= tp) for tp in tps):
            return False, 'Ціна вже досягала TP після публікації'
    return True, f'Історію свічок BingX перевірено ({len(candles)} свічок, {interval}); ENTRY/SL/TP не торкалися'
