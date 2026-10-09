"""Read-only reconciliation of Signalbot's local signals vs BingX exchange positions.

Does not call any trading endpoints. Intended for operator review after restart.
"""
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from signalbot.bingx import BingX

async def main():
    path = Path(os.getenv('SIGNALBOT_DB_PATH','/data/signalbot.db'))
    if not path.exists():
        raise SystemExit('Signalbot DB not found')
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        db.row_factory=sqlite3.Row
        signals=[dict(x) for x in db.execute(
            "SELECT id, symbol, side, status, plan FROM signals WHERE status IN ('executed', 'shadow', 'awaiting') ORDER BY id")]
    api=BingX(os.environ['BINGX_API_KEY'], os.environ['BINGX_SECRET_KEY'])
    try:
        positions=await api.futures_positions()
        orders=await api.futures_open_orders()
        print('Read-only restart reconciliation')
        print('Exchange positions:', len(positions),'Exchange open orders:', len(orders))
        print('Locally active/pending signal records:',len(signals))
        for row in signals:
            plan=json.loads(row['plan']) if row.get('plan') else {}
            exchange_match=[p for p in positions if p.get('symbol') == row['symbol']
                            and str(p.get('positionSide','')).lower() == str(row['side']).lower()]
            print(f"signal_id={row['id']} status={row['status']} symbol={row['symbol']} side={row['side']} "
                  f"planned_qty={plan.get('quantity')} matching_exchange_legs={len(exchange_match)}")
        for p in positions:
            print('EXCHANGE_POSITION',p.get('symbol'),p.get('positionSide'),'qty=',p.get('positionAmt'))
        for o in orders:
            print('EXCHANGE_ORDER',o.get('symbol'),o.get('positionSide'),o.get('type'),o.get('status'),
                  'client_id=',o.get('clientOrderId'))
        print('RECONCILE_READ_ONLY_OK (no DB writes, no trading requests)')
    finally:
        await api.close()

if __name__=='__main__':
    asyncio.run(main())
