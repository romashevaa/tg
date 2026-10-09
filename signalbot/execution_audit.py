"""Read-only BingX futures entry audit. Never infers protection from a submitted plan.

This is a diagnostic, not a trade executor. A status of UNKNOWN requires manual
confirmation on BingX before enabling live execution.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

TERMINAL = {'FILLED', 'CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED'}


def _decimal(value):
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal('0')


def analyze_entry(order: dict, positions: list[dict], orders: list[dict], symbol: str, side: str) -> dict:
    """Return verifiable facts about an order. No write operations."""
    if side not in ('long', 'short'):
        raise ValueError('side must be long or short')
    status = str(order.get('status') or order.get('state') or 'UNKNOWN').upper()
    qty = _decimal(order.get('origQty') or order.get('quantity') or order.get('origQuantity'))
    executed = _decimal(order.get('executedQty') or order.get('cumQty') or order.get('filledQty'))
    leg = side.upper()
    matching = [p for p in positions if p.get('symbol') == symbol and
                str(p.get('positionSide', '')).upper() == leg and
                _decimal(p.get('positionAmt')) != 0]
    actual = sum((abs(_decimal(p.get('positionAmt'))) for p in matching), Decimal('0'))
    side_orders = [o for o in orders if o.get('symbol') == symbol and
                   str(o.get('positionSide', '')).upper() == leg]
    sl = [o for o in side_orders if 'STOP' in str(o.get('type', '')).upper() and
          'PROFIT' not in str(o.get('type', '')).upper()]
    tp = [o for o in side_orders if 'TAKE_PROFIT' in str(o.get('type', '')).upper()]
    return {
        'order_status': status,
        'requested_qty': str(qty),
        'executed_qty': str(executed),
        'exchange_leg_qty': str(actual),
        'fully_filled': status == 'FILLED' and qty > 0 and executed >= qty,
        'partially_filled': (executed > 0 and qty > 0 and executed < qty) or status == 'PARTIALLY_FILLED',
        'stop_orders_visible': len(sl),
        'take_profit_orders_visible': len(tp),
        # BingX may manage attached protection separately from ordinary open orders.
        # Even visible orders need trigger, leg and quantity inspection.
        'protection_confirmed': False,
        'protection_status': 'UNVERIFIED: inspect BingX conditional/TP-SL orders and triggers',
        'position_match': len(matching) == 1 and executed > 0 and actual == executed,
    }
