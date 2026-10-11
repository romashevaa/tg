"""Single-entry, two-target *preflight only*. Never sends an exchange order.

A single fill with attached SL and two conditional TP orders requires a persistent
order/position reconciler before it can safely be enabled on a live account.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class OneEntryPreview:
    symbol: str
    side: str
    entry_quantity: str
    tp1_quantity: str
    tp2_quantity: str
    stop_loss: str
    tp1_price: str
    tp2_price: str
    can_stage_exits: bool
    blocker: str
    exit_mode: str = 'SPLIT_80_20'

    def asdict(self):
        return asdict(self)


def preview(plan, market_info, tp1: float, tp2: float) -> OneEntryPreview:
    """Validate sizes after lot rounding; never increase planned risk.

    Quantities for BOTH target exits must be supported on their own. 80/20 is
    a target: indivisible minimum lot may result in a small deviation.
    """
    q = Decimal(str(plan.quantity))
    step = Decimal('1').scaleb(-int(market_info.qty_precision))
    minimum = Decimal(str(market_info.min_qty))
    min_notional = Decimal(str(market_info.min_notional))
    entry_price = Decimal(str(plan.entry_price))
    t1 = Decimal(str(tp1)); t2 = Decimal(str(tp2))
    stop = Decimal(str(plan.stop_loss))
    q1 = (q * Decimal('0.8')).quantize(step, rounding=ROUND_DOWN)
    q2 = q - q1
    reason = ''
    if q <= 0 or q1 <= 0 or q2 <= 0:
        reason = 'Некоректний або надто малий обсяг'
    elif q1 < minimum or q2 < minimum:
        reason = 'TP1 або TP2 нижче мінімальної кількості BingX'
    elif q1 * t1 < min_notional or q2 * t2 < min_notional:
        reason = 'TP1 або TP2 нижче мінімального номіналу BingX'
    elif plan.side == 'long' and not (stop < entry_price < t1 < t2):
        reason = 'Некоректна послідовність LONG ENTRY/SL/TP'
    elif plan.side == 'short' and not (stop > entry_price > t1 > t2):
        reason = 'Некоректна послідовність SHORT ENTRY/SL/TP'
    elif plan.side not in ('long','short'):
        reason = 'Невідомий напрямок'
    mode = 'SPLIT_80_20'
    # A full-size TP1 can replace both partial exits when the 20% remainder
    # is below the exchange minimum. This preserves the original quantity,
    # and never leaves an unclosable dust remainder. It does not claim BE.
    if reason and ('нижче мінімальної' in reason or 'нижче мінімального' in reason):
        if q >= minimum and q * t1 >= min_notional and (
            (plan.side == 'long' and stop < entry_price < t1 < t2) or
            (plan.side == 'short' and stop > entry_price > t1 > t2)
        ):
            q1, q2 = q, Decimal('0')
            mode = 'SINGLE_TP_100'
            reason = ''
    return OneEntryPreview(plan.symbol,plan.side,str(q),str(q1),str(q2),str(stop),str(t1),str(t2),
        not bool(reason), reason or 'Розміри пройшли перевірку; захист потрібен на біржі', mode)
