"""Read-only reconciliation for TradingView order attempts. No writes to BingX."""
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from signalbot.bingx import BingX
from signalbot.config import load_config
from tv_live_engine import schema

async def main():
    cfg=load_config('config.toml')
    path=Path(os.getenv('SIGNALBOT_DATA_DIR','/data'))/'tv_updates_monitor.sqlite'
    if not path.exists():
        print('No TradingView database exists');return
    with sqlite3.connect(path) as db:
        schema(db)
        rows=db.execute("SELECT idea_id,status,payload FROM tv_live_orders ORDER BY created_utc DESC LIMIT 25").fetchall()
    if not rows:
        print('No live TradingView attempts recorded');return
    x=BingX(cfg.secrets.bingx_api_key,cfg.secrets.bingx_secret_key)
    try:
        for iid,status,raw in rows:
            data=json.loads(raw);sym=data.get('symbol')
            if not sym: continue
            print('\n',iid,sym,'RECORDED',status)
            try:
                orders=await x.futures_open_orders(sym)
                pos=await x.futures_positions(sym)
                print('LIVE_POSITIONS',[(p.get('positionSide'),p.get('positionAmt')) for p in pos])
                print('OPEN_ORDER_COUNT',len(orders))
                for leg in data.get('legs',[]):
                    client=leg.get('client_id')
                    if not client: continue
                    try:
                        obj=await x.futures_order(sym,client_order_id=client)
                        print('ENTRY',client,'STATUS',obj.get('status'),'FILLED',obj.get('executedQty'))
                    except Exception as exc:
                        print('ENTRY_NOT_VERIFIED',client,str(exc)[:150])
                print('ATTACHED_SL_TP_NOT_INDEPENDENTLY_VERIFIED; check BingX app')
            except Exception as exc:
                print('ERROR',str(exc)[:150])
    finally: await x.close()

if __name__=='__main__':
    asyncio.run(main())
