"""Opt-in single-entry Hedge executor. Experimental; exchange protection is NOT certified.

Never retries ambiguous writes. Every order is pre-recorded in the shared SQLite
idea ledger. An entry always carries BingX attached SL; two TP conditional
orders are sent only after verifying a complete fill and the exact position size.
Any mismatch is an operator alert, not an instruction to repeat an order.
"""
from __future__ import annotations
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal

from .one_entry import preview


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(db, iid, author, status, reason, payload):
    stamp = _now()
    db.execute('''INSERT INTO tv_live_orders
      (idea_id,author,created_utc,updated_utc,status,reason,payload)
      VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(idea_id) DO UPDATE SET updated_utc=excluded.updated_utc,
        status=excluded.status,reason=excluded.reason,payload=excluded.payload''',
      (iid,author,stamp,stamp,status,reason,json.dumps(payload,ensure_ascii=False,default=str)))
    db.commit()


def _reserve(db, iid, author, payload):
    stamp=_now()
    cursor=db.execute('''INSERT OR IGNORE INTO tv_live_orders
       (idea_id,author,created_utc,updated_utc,status,reason,payload)
       VALUES(?,?,?,?,?,?,?)''',
       (iid,author,stamp,stamp,'ONE_ENTRY_SUBMITTING',
        'Unknown until exchange verified; never auto retry',json.dumps(payload,default=str)))
    db.commit()
    return cursor.rowcount == 1


def _amount(position):
    return abs(Decimal(str(position.get('positionAmt') or '0')))


def _matching_positions(positions, symbol, side):
    return [p for p in positions if p.get('symbol')==symbol
            and str(p.get('positionSide') or '').upper()==side.upper()
            and _amount(p)>0]


def exit_order_params(symbol, side, qty, stop_price):
    """No reduceOnly in Hedge Mode; SELL closes LONG, BUY closes SHORT."""
    if side not in ('long','short') or Decimal(str(qty)) <= 0:
        raise ValueError('Invalid closing side or amount')
    return {'symbol':symbol, 'side':'SELL' if side=='long' else 'BUY',
            'positionSide':side.upper(),'type':'TAKE_PROFIT_MARKET',
            'quantity':str(qty),'stopPrice':str(stop_price),'workingType':'MARK_PRICE'}


async def run(db, iid, author, exchange, plan, info, tp1, tp2, margin_type):
    """One entry plus two conditional TP requests. A pending LIMIT needs later
    reconciliation; here it is never treated as filled. This function is NOT a
    certification that attached SL is active on BingX.
    """
    report=preview(plan,info,tp1,tp2)
    if not report.can_stage_exits:
        return 'SKIP', report.blocker
    if plan.order_type != 'MARKET':
        return 'CHECK','One Entry: LIMIT requires durable fill watcher; not implemented in this mode'
    if not await exchange.hedge_mode():
        return 'SKIP','One-entry mode requires verified BingX Hedge Mode'
    if await exchange.futures_positions(plan.symbol) or await exchange.futures_open_orders(plan.symbol):
        return 'SKIP','Existing position/order on symbol: no co-mingling'
    payload={'strategy':'ONE_ENTRY','symbol':plan.symbol,'side':plan.side,
             'entry':asdict(plan),'sizing':report.asdict(),'tp1':tp1,'tp2':tp2,
             'protective_stop_verified':False,'exits':[],'exit_mode':report.exit_mode}
    if not _reserve(db,iid,author,payload):
        return 'SKIP','Idea already claimed by another executor'
    # The attached SL parameter is sent as part of the SAME entry request.
    # Never send unprotected entry. The entry carries NO take-profit because
    # partial TP orders will be staged after exact full fill verification.
    entry_plan=replace(plan,take_profit=None)
    try:
        response=await exchange.open_futures(entry_plan,margin_type,configure=True)
        payload['entry_ack']=response
        _write(db,iid,author,'ONE_ENTRY_VERIFY','Entry request sent; exchange fill unverified',payload)
    except Exception as exc:
        _write(db,iid,author,'MANUAL_RECONCILIATION',
               f'Entry request uncertain: {type(exc).__name__}',payload)
        return 'MANUAL_RECONCILIATION','Entry outcome uncertain; inspect BingX, do not retry'
    try:
        order=await exchange.futures_order(plan.symbol,client_order_id=plan.client_id)
        payload['entry_order']=order
        pos=_matching_positions(await exchange.futures_positions(plan.symbol), plan.symbol,plan.side)
        status=str(order.get('status') or '').upper()
        quantity=Decimal(str(plan.quantity))
        actual=sum((_amount(p) for p in pos),Decimal('0'))
        if status != 'FILLED' or len(pos)!=1 or actual!=quantity:
            # Never place full-sized TP exits against a partially filled position.
            _write(db,iid,author,'ONE_ENTRY_PENDING_FILL',
                f'entry={status}, position={actual}; wait for reconciliation',payload)
            return 'ONE_ENTRY_PENDING_FILL','Entry not fully and exactly filled; linked TP exits not yet staged'
    except Exception as exc:
        _write(db,iid,author,'MANUAL_RECONCILIATION',
             f'Order/position could not be verified: {type(exc).__name__}',payload)
        return 'MANUAL_RECONCILIATION','Cannot verify position; inspect exchange urgently'
    exits_to_stage = [(report.tp1_quantity, tp1)]
    if report.exit_mode == 'SPLIT_80_20':
        exits_to_stage.append((report.tp2_quantity, tp2))
    for idx,(qty,price) in enumerate(exits_to_stage,start=1):
        # Durable pre-write BEFORE each non-idempotent TP call. No clientOrderId
        # exists for conditional orders in BingX official spec.
        params=exit_order_params(plan.symbol,plan.side,qty,price)
        payload['exits'].append({'number':idx,'params':params,'status':'SUBMITTING'})
        _write(db,iid,author,'ONE_ENTRY_PROTECTING',
               f'TP{idx} request pending; do not resubmit after timeout',payload)
        try:
            reply=await exchange.place_futures_conditional(params)
            payload['exits'][-1]['response']=reply
            payload['exits'][-1]['status']='ACKNOWLEDGED_UNVERIFIED'
            _write(db,iid,author,'ONE_ENTRY_PROTECTING',f'TP{idx} acknowledged; not yet verified',payload)
        except Exception as exc:
            _write(db,iid,author,'MANUAL_RECONCILIATION',
                f'TP{idx} placement uncertain: {type(exc).__name__}',payload)
            return 'MANUAL_RECONCILIATION','Exit may be absent or duplicate; inspect BingX; no automatic retry'
    try:
        current=await exchange.futures_open_orders(plan.symbol)
        payload['open_orders_snapshot']=current
        ids=[str((e.get('response') or {}).get('orderId') or '') for e in payload['exits']]
        visible={str(o.get('orderId')):o for o in current}
        matched=all(i and i in visible for i in ids)
        # Open-orders responses do not reliably expose the attached SL.
        payload['protective_stop_verified']=False
        payload['take_profits_visible']=matched
        _write(db,iid,author,'ONE_ENTRY_REVIEW',
            'TP requests acknowledged; SL NOT independently verified',payload)
    except Exception:
        _write(db,iid,author,'MANUAL_RECONCILIATION',
            'TP read-back failed; SL/TP not certified',payload)
        return 'MANUAL_RECONCILIATION','TP read-back failed; manual exchange review necessary'
    return 'ONE_ENTRY_REVIEW','Entry + TP requests submitted, but SL/TP protection requires exchange-side verification'
