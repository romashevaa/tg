"""Conservative, read-only audit of BingX futures split entries.

Do not infer actual protective coverage from API acknowledgement or an order plan.
Conditional stop orders may be hidden from the ordinary openOrders endpoint.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation


def dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def _order_type(order):
    return str(order.get('type') or order.get('origType') or '').upper()


def classify(plan_legs: list[dict], exchange_entries: list[dict | None],
             positions: list[dict], open_orders: list[dict], symbol: str) -> dict:
    """Only classify observed facts; never claim stop protection is verified."""
    if len(plan_legs) != 2 or len(exchange_entries) != 2:
        return {'severity': 'ALERT', 'reason': 'Expected exactly two 80/20 entry legs'}
    sides = {str(leg.get('side', '')).lower() for leg in plan_legs}
    if len(sides) != 1 or next(iter(sides)) not in ('long', 'short'):
        return {'severity': 'ALERT', 'reason': 'Missing or inconsistent entry sides'}
    side = next(iter(sides)).upper()
    q1, q2 = (dec(leg.get('quantity')) for leg in plan_legs)
    if q1 <= 0 or q2 <= 0:
        return {'severity': 'ALERT', 'reason': 'Invalid planned leg quantities'}
    total = q1 + q2
    ratio = q1 / total
    ratio_ok = abs(ratio - Decimal('0.8')) <= Decimal('0.02')  # rounding tolerance
    states = [str(order.get('status') or 'UNKNOWN').upper() if order else 'UNVERIFIED' for order in exchange_entries]
    filled = [dec((order or {}).get('executedQty') or (order or {}).get('cumQty') or 0) for order in exchange_entries]
    live_positions = [p for p in positions if p.get('symbol') == symbol and str(p.get('positionSide', '')).upper() == side and dec(p.get('positionAmt')) != 0]
    live_qty = sum((abs(dec(p.get('positionAmt'))) for p in live_positions), Decimal('0'))
    visible = [o for o in open_orders if o.get('symbol') == symbol and str(o.get('positionSide', '')).upper() == side]
    sl = [o for o in visible if 'STOP' in _order_type(o) and 'PROFIT' not in _order_type(o)]
    tp = [o for o in visible if 'TAKE_PROFIT' in _order_type(o)]
    pending = any(s in ('NEW', 'PARTIALLY_FILLED', 'PENDING', 'UNKNOWN', 'UNVERIFIED') for s in states)
    issues = []
    if not ratio_ok: issues.append('Planned order sizes are not approximately 80/20')
    if any(s == 'UNVERIFIED' for s in states): issues.append('At least one entry cannot be queried')
    if any(f > 0 for f in filled) and live_qty == 0: issues.append('Entry fills seen, but no matching open position (possibly already closed)')
    if live_qty > 0:
        issues.append('Open position present: attached SL/TP coverage NOT independently verified')
    if pending:
        issues.append('Pending/uncertain entry may still fill; check author scenario and cancel stale LIMIT manually')
    return {
        'severity': 'ALERT' if issues else 'REVIEW',
        'symbol': symbol, 'position_side': side,
        'planned_80_20': ratio_ok,
        'entry_statuses': states,
        'filled_quantities': [str(v) for v in filled],
        'exchange_position_quantity': str(live_qty),
        'visible_stop_count': len(sl), 'visible_take_profit_count': len(tp),
        'protection_confirmed': False,
        'issues': issues or ['Order accepted/closed does not independently establish TP/SL coverage'],
        'next_action': 'Inspect conditional TP/SL orders, trigger prices, sizes and side directly on BingX; do not auto-resubmit.',
    }
