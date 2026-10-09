"""Read-only entry-price alignment against historical candles. No order execution."""
import re
from datetime import datetime, timezone
from math import isfinite
from urllib.parse import urlparse, unquote

STEP = 300_000
PAIR_RE = re.compile(r'^[A-Z0-9]{2,25}USDT(?:\.P)?$', re.I)


def resolve_symbol(record):
    """Use AI symbol when valid; fall back to the TradingView chart symbol in URL.

    Do not silently map forex/indices or cross-quoted symbols to USDT markets.
    """
    ai = record.get('ai') or {}
    candidates = [ai.get('symbol')]
    path = urlparse(record.get('url') or '').path
    match = re.search(r'/chart/([^/]+)/', path, re.I)
    if match:
        candidates.append(unquote(match.group(1)))
    for value in candidates:
        if not isinstance(value, str):
            continue
        symbol = value.strip().upper().split(':')[-1]
        if PAIR_RE.fullmatch(symbol):
            return symbol.removesuffix('.P')
    return None


def compare_entry(entry, rows, published_ms, tolerance=0.01):
    """Compare chart/text entry to first 5m candle opening AFTER publication.

    Not proof of visual anchor or actual fill. The opening quote is a proxy.
    """
    if entry is None or not isfinite(float(entry)) or float(entry) <= 0:
        return {'status': 'NO_ENTRY'}
    if not rows:
        return {'status': 'NO_PRICE_DATA'}
    first = (published_ms // STEP + 1) * STEP
    selected = next((r for r in sorted(rows) if first <= r[0] < first + STEP), None)
    if selected is None:
        return {'status': 'NO_PRICE_DATA'}
    quote = float(selected[1]); drift = abs(quote - float(entry)) / float(entry)
    return {'status': 'NEAR_PUBLISHED_PRICE' if drift <= tolerance else 'PRICE_MISMATCH',
            'chart_entry': float(entry), 'candle_open': quote,
            'difference_pct': round(drift * 100, 3),
            'candle_start_ms': int(selected[0]), 'tolerance_pct': tolerance * 100}


async def verify(prices, record, tolerance=0.01):
    ai = record.get('ai') or {}
    entry = ai.get('entry_low')
    if entry is None:
        entry = ai.get('entry_high')
    if not record.get('published_at'):
        return {'status': 'NO_DATE'}
    try:
        dt = datetime.fromisoformat(record['published_at'].replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        stamp = int(dt.timestamp() * 1000)
        symbol = resolve_symbol(record)
        if not symbol:
            return {'status': 'NO_SYMBOL'}
        first = (stamp // STEP + 1) * STEP
        rows, source = await prices.candles(symbol, first, first + STEP, '5m')
        result = compare_entry(entry, rows, stamp, tolerance)
        result.update(source=source, symbol=symbol)
        return result
    except (TypeError, ValueError, OverflowError):
        return {'status': 'INVALID_SOURCE_DATA'}
