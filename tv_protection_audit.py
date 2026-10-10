"""READ-ONLY BingX LIVE audit for every persisted TradingView execution attempt.

Run in Railway: python /app/tv_protection_audit.py
Does not submit, cancel, or modify orders. Exit code 2 means needs review.
"""
import asyncio
import json
import os
import sqlite3
from pathlib import Path

from signalbot.bingx import BingX
from signalbot.config import load_config
from signalbot.protection_audit import classify


async def main():
    path = Path(os.getenv('SIGNALBOT_DATA_DIR', '/data')) / 'tv_updates_monitor.sqlite'
    if not path.exists():
        print('NO_DATABASE: no live execution history available')
        return 0
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT idea_id,status,payload FROM tv_live_orders ORDER BY created_utc DESC").fetchall() if db.execute("SELECT name FROM sqlite_master WHERE name='tv_live_orders'").fetchone() else []
    if not rows:
        print('NO_ATTEMPTS: cannot certify exchange protection; read-only audit')
        return 0
    cfg = load_config('config.toml')
    ex = BingX(cfg.secrets.bingx_api_key, cfg.secrets.bingx_secret_key)
    needs_review = False
    try:
        for iid, status, raw in rows:
            print(f'\nIDEA {iid} db_status={status}')
            try:
                payload = json.loads(raw or '{}')
                symbol = payload.get('symbol')
                legs = payload.get('legs') or []
                if not symbol or len(legs) != 2:
                    print('ALERT: no verifiable symbol/two-leg plan'); needs_review = True; continue
                entries = []
                for leg in legs:
                    client_id = leg.get('client_id')
                    if not client_id:
                        entries.append(None); continue
                    try:
                        entries.append(await ex.futures_order(symbol, client_order_id=client_id))
                    except Exception as err:
                        print(f'ENTRY_LOOKUP_UNVERIFIED client_id={client_id} error={type(err).__name__}')
                        entries.append(None)
                positions = await ex.futures_positions(symbol)
                orders = await ex.futures_open_orders(symbol)
                report = classify(legs, entries, positions, orders, symbol)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                needs_review = needs_review or report['severity'] == 'ALERT'
            except Exception as err:
                print(f'ALERT: reconciliation failed: {type(err).__name__}: {str(err)[:160]}')
                needs_review = True
    finally:
        await ex.close()
    print('\nREAD_ONLY: no orders placed, modified, or cancelled')
    print('NOTE: attached TP/SL may not appear in standard openOrders; manual BingX conditional order check is still required.')
    return 2 if needs_review else 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
