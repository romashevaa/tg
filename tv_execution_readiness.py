"""Deterministic, no-order TradingView -> BingX 80/20 entry readiness.

Kept independent of model output to prevent an LLM from authorizing orders.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from math import isfinite
from tv_trade_diagnostics import evaluate, targets_80_20, bingx_pair


@dataclass(frozen=True)
class Readiness:
    status: str
    reason: str
    symbol: str | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp1_pct: int = 80
    tp2_pct: int = 20
    calculated_tp1: bool = False


def check(ev, published, price, listed, *, closed=False, now=None, max_age_minutes=20):
    now = now or datetime.now(timezone.utc)
    if published is None:
        return Readiness('SKIP', 'Невідома дата публікації; не можна вважати сигнал новим')
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = (now - published).total_seconds() / 60
    if age < -5 or age > max_age_minutes:
        return Readiness('SKIP', f'Сигналу {max(0,age):.0f} хв; дозволено не більше {max_age_minutes:.0f} хв для нового входу')
    if closed:
        return Readiness('SKIP', 'Автор уже закрив або скасував ідею')
    status, reason = evaluate(ev, price, listed)
    if status != 'READY_FOR_EXECUTION_CHECK':
        return Readiness(status, reason)
    split = targets_80_20(ev)
    if not split:
        return Readiness('SKIP', 'Немає двох доступних рівнів виходу')
    tp1,tp2 = split
    entry, sl = float(ev.entry),float(ev.stop_loss)
    nums = [entry,sl,tp1,tp2]
    if not all(isfinite(x) and x > 0 for x in nums):
        return Readiness('SKIP','Недійсні цінові рівні')
    long = ev.direction == 'LONG'
    if not ((sl < entry < tp1 < tp2) if long else (tp2 < tp1 < entry < sl)):
        return Readiness('SKIP','Цілі TP1/TP2 або SL розташовані неправильно')
    if abs(float(price)-entry)/entry > 0.004:
        return Readiness('WAIT_ENTRY', 'Ціна поза зоною входу ±0.4%; не відкривати MARKET навздогін',bingx_pair(ev.symbol),tp1,tp2,calculated_tp1=len(ev.targets)==1)
    return Readiness('READY_FOR_MANUAL_ORDER_TEST',
                     'Торговий план пройшов первинну перевірку, але виконання й захисні ордери BingX ще не верифіковані',
                     bingx_pair(ev.symbol),tp1,tp2,calculated_tp1=len(ev.targets)==1)


def split_quantity(total, precision, min_qty=0):
    """Split by exchange quantity precision; refuse a dust remainder."""
    if precision < 0 or precision > 12 or not isfinite(float(total)) or total <= 0:
        raise ValueError('Некоректна кількість або precision')
    step = Decimal(1).scaleb(-precision)
    qty = Decimal(str(total)).quantize(step,rounding=ROUND_DOWN)
    first=(qty*Decimal('0.8')).quantize(step,rounding=ROUND_DOWN)
    second=qty-first
    if first < Decimal(str(min_qty)) or second < Decimal(str(min_qty)):
        raise ValueError('TP 80/20 неможливі: частина менша за мінімум контракту')
    return float(first),float(second)
