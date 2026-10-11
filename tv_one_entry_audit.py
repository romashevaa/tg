"""Read-only BingX one-entry risk audit. No position or order mutation."""
import asyncio
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from signalbot.bingx import BingX
from signalbot.config import load_config

async def audit():
    path=Path(os.getenv('SIGNALBOT_DATA_DIR','/data'))/'tv_updates_monitor.sqlite'
    if not path.exists():
        print('NO_DATABASE: no one-entry history');return 0
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='tv_live_orders'").fetchone():
            print('NO_ATTEMPTS');return 0
        rows=db.execute("SELECT idea_id,status,payload FROM tv_live_orders ORDER BY updated_utc DESC").fetchall()
    rows=[(iid,status,json.loads(raw)) for iid,status,raw in rows if raw and json.loads(raw).get('strategy')=='ONE_ENTRY']
    if not rows:
        print('NO_ONE_ENTRY_ATTEMPTS');return 0
    cfg=load_config('config.toml')
    ex=BingX(cfg.secrets.bingx_api_key,cfg.secrets.bingx_secret_key)
    alerts=0
    try:
        for iid,status,rec in rows:
            symbol=rec['symbol']; side=rec['side'].upper()
            print(f'IDEA {iid} status={status} symbol={symbol} side={side}')
            try:
                entry=await ex.futures_order(symbol,client_order_id=rec['entry']['client_id'])
                positions=await ex.futures_positions(symbol)
                orders=await ex.futures_open_orders(symbol)
                active=[p for p in positions if p.get('symbol')==symbol and str(p.get('positionSide')).upper()==side and Decimal(str(p.get('positionAmt') or 0))!=0]
                qty=sum((abs(Decimal(str(p['positionAmt']))) for p in active),Decimal(0))
                ids=[str((e.get('response') or {}).get('orderId') or '') for e in rec.get('exits',[])]
                openids={str(o.get('orderId')) for o in orders}
                observed=[x for x in ids if x and x in openids]
                print(f'  entry={entry.get("status")} position_qty={qty} TP_visible={len(observed)}/{len(ids)}')
                print('  SL_VERIFIED=NO: attached stop not certified by read-only openOrders')
                if qty>0 or str(entry.get('status') or '').upper() in ('NEW','PARTIALLY_FILLED'):
                    print('  ALERT: unresolved live exposure or resting entry; inspect SL and TP on BingX')
                    alerts+=1
                elif ids and len(observed)>0:
                    print('  ALERT: no position but conditional TP appears open; review stale orders')
                    alerts+=1
            except Exception as exc:
                print(f'  ALERT: exchange reconciliation failed ({type(exc).__name__})');alerts+=1
    finally:
        await ex.close()
    print('READ_ONLY: no orders submitted/cancelled')
    return 2 if alerts else 0

if __name__=='__main__':
    raise SystemExit(asyncio.run(audit()))
