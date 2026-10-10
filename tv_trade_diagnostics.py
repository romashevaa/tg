"""Read-only checks explaining why a TradingView review did not become a BingX order.

This module deliberately NEVER sends an order and does not classify an AI chart
interpretation as a confirmed exchange execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re


def age_label(published: datetime | None, now: datetime | None = None) -> str:
    if published is None:
        return 'час публікації невідомий'
    now = now or datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    seconds = (now - published).total_seconds()
    if seconds < -300:
        return 'час публікації у майбутньому (невірна дата)'
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f'{seconds} с тому'
    if seconds < 3600:
        return f'{seconds // 60} хв тому'
    if seconds < 86400:
        return f'{seconds // 3600} год {seconds % 3600 // 60} хв тому'
    return f'{seconds // 86400} д {seconds % 86400 // 3600} год тому'


def bingx_pair(symbol: str) -> str | None:
    symbol = (symbol or '').upper().replace('/', '').replace('-', '').strip()
    symbol = re.sub(r'\.P$', '', symbol)
    if not re.fullmatch(r'[A-Z0-9]{4,24}', symbol):
        return None
    if symbol.endswith('USDT'):
        return symbol[:-4] + '-USDT'
    if symbol.endswith('USD'):
        return symbol[:-3] + '-USDT'  # only a candidate; contract lookup mandatory
    return None


def evaluate(evidence, market_price: float | None = None, listed: bool | None = None) -> tuple[str, str]:
    category = str(evidence.category).upper()
    if category not in ('NEW_CALL', 'CONDITIONAL'):
        return 'SKIP', 'Це аналіз, оновлення або історичний результат, а не новий торговий виклик'
    if str(evidence.confidence).upper() != 'HIGH':
        return 'SKIP', 'AI не підтвердив торгові рівні з достатньою впевненістю'
    if evidence.direction not in ('LONG', 'SHORT'):
        return 'SKIP', 'Не визначено напрямок LONG/SHORT'
    if evidence.entry is None or evidence.stop_loss is None:
        return 'SKIP', 'Немає явного ENTRY або Stop Loss'
    if not evidence.targets:
        return 'SKIP', 'Автор не задав жодного Take Profit'
    entry, sl = float(evidence.entry), float(evidence.stop_loss)
    tps = [float(x) for x in evidence.targets]
    if not all(math.isfinite(n) and n > 0 for n in [entry, sl, *tps]):
        return 'SKIP', 'Некоректне числове значення ціни'
    long = evidence.direction == 'LONG'
    if not ((sl < entry < min(tps)) if long else (max(tps) < entry < sl)):
        return 'SKIP', 'SL/TP розташовані не з того боку ENTRY'
    if not bingx_pair(evidence.symbol):
        return 'SKIP', 'Не підтверджено USDT-ф’ючерсний символ BingX'
    if listed is False:
        return 'SKIP', 'BingX не має доступного USDT-ф’ючерсу для цього символу'
    if listed is None:
        return 'CHECK', 'Доступність контракту BingX ще не перевірена'
    if market_price is None or not math.isfinite(market_price) or market_price <= 0:
        return 'CHECK', 'Немає підтвердженої поточної ціни BingX'
    # Do not automatically market-enter after a target or stop was already touched.
    if long and market_price >= min(tps):
        return 'SKIP', 'Поточна ціна вже досягла або перевищила TP1; запізнілий вхід'
    if not long and market_price <= max(tps):
        return 'SKIP', 'Поточна ціна вже досягла або пройшла TP1; запізнілий вхід'
    if long and market_price <= sl:
        return 'SKIP', 'Поточна ціна вже нижче SL; умова сигналу порушена'
    if not long and market_price >= sl:
        return 'SKIP', 'Поточна ціна вже вище SL; умова сигналу порушена'
    return 'READY_FOR_EXECUTION_CHECK', 'Контракт і поточна ціна перевірені; реальне виконання та захисні ордери ще не підключені'


def targets_80_20(evidence) -> tuple[float, float] | None:
    """Illustrative TP schedule only. Never submit computed targets automatically."""
    if evidence.entry is None or not evidence.targets:
        return None
    entry = float(evidence.entry)
    raw = [float(t) for t in evidence.targets]
    if len(raw) == 1:
        return (entry + raw[0]) / 2, raw[0]
    return raw[0], raw[1]
