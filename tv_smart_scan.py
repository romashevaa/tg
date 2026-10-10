"""Persistent read-only Smart Entry monitor. No orders, no LLM calls."""
import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tv_auto_alerts as alerts
import tv_last_hour
import tv_author_settings as authors_db
from tv_full_review import Evidence
from tv_timeframe_policy import normalize_timeframe
from tv_timeframe_recovery import recover
from tv_smart_entry import inspect
from signalbot.bingx import BingX


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tv_smart_states
       (idea_id TEXT PRIMARY KEY, author TEXT NOT NULL, state TEXT NOT NULL,
        reason TEXT NOT NULL, checked_utc TEXT NOT NULL, price REAL, interval TEXT)''')
    db.commit()


def save(db, iid, author, decision):
    db.execute('''INSERT INTO tv_smart_states VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(idea_id) DO UPDATE SET state=excluded.state,
        reason=excluded.reason, checked_utc=excluded.checked_utc,
        price=excluded.price, interval=excluded.interval''',
        (iid, author, decision.status, decision.reason,
         datetime.now(timezone.utc).isoformat(), decision.price, decision.interval))
    db.commit()


async def scan(hours=16, data_dir='/data', *, attempt_live=False):
    data_dir = Path(data_dir)
    with sqlite3.connect(str(data_dir / 'signalbot.db'), timeout=20) as db:
        enabled = authors_db.enabled_authors(db)
    now = datetime.now(timezone.utc)
    checked = 0
    attempts = 0
    # Real-order retries are opt-in separately from the read-only Smart Entry worker.
    live_allowed = (attempt_live and os.getenv('TRADING_MODE') == 'live'
                    and os.getenv('TV_LIVE_EXECUTION') == 'YES'
                    and os.getenv('TV_SMART_ENTRY_LIVE_GATE') == 'YES'
                    and os.getenv('TV_SMART_AUTO_EXECUTION') == 'YES')
    cfg = None
    from signalbot.config import load_config
    # Config is also needed to recover a timeframe for previously cached AI reviews.
    # Loading it does not submit any orders.
    cfg = load_config('config.toml')
    exchange = BingX('', '')
    try:
        with sqlite3.connect(str(data_dir / 'tv_updates_monitor.sqlite'), timeout=20) as db:
            init(db)
            for author in enabled:
                if authors_db.suspension_info(db, author):
                    continue
                try:
                    entries = alerts.fetch_latest(author_name=author)
                except Exception as exc:
                    print(f'@{author}: TradingView недоступний ({type(exc).__name__})')
                    continue
                for item in entries:
                    unpacked = alerts.unpack(item, author_name=author)
                    published = tv_last_hour.published_at(item)
                    if not unpacked or published is None:
                        continue
                    iid = unpacked[0]
                    age = (now - published).total_seconds() / 3600
                    if age < 0 or age > hours:
                        continue
                    row = db.execute('SELECT result_json FROM tv_full_chart_reviews WHERE idea_id=?', (iid,)).fetchone()
                    if not row:
                        continue
                    # Author cancellation/closing always invalidates new entries.
                    closed_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='tv_closed_ideas'").fetchone()
                    if closed_table and db.execute('SELECT 1 FROM tv_closed_ideas WHERE idea_id=?', (iid,)).fetchone():
                        from tv_smart_entry import Decision
                        decision = Decision('INVALID', 'Автор закрив ідею')
                    else:
                        evidence = Evidence.model_validate(json.loads(row[0]))
                        if not normalize_timeframe(evidence.timeframe):
                            try:
                                evidence, source = await recover(db, iid, item, unpacked[1], evidence, cfg)
                            except Exception as exc:
                                source = f'recovery_error_{type(exc).__name__}'
                            print(f'SMART_TF_RECOVERY @{author} {iid}: TF={evidence.timeframe or "unknown"} source={source}')
                        try:
                            decision = await inspect(exchange, evidence)
                        except Exception as exc:
                            from tv_smart_entry import Decision
                            decision = Decision('CHECK', f'Не вдалося перевірити ринок: {type(exc).__name__}')
                    save(db, iid, author, decision)
                    # ONLY a fresh READY_REVIEW may trigger execution. The executor
                    # re-fetches candles and performs full market/risk checks.
                    if live_allowed and decision.status == 'READY_REVIEW':
                        from tv_live_engine import execute
                        try:
                            status, reason = await execute(db, iid, author, evidence, published, cfg)
                            attempts += 1
                            print(f'SMART_AUTO @{author} {iid}: {status}: {reason}')
                        except Exception as exc:
                            print(f'SMART_AUTO @{author} {iid}: ERROR {type(exc).__name__}: {str(exc)[:160]}')
                    checked += 1
                    print(f'@{author} {iid}: age={age:.1f}h {decision.status}: {decision.reason}; '
                          f'BingX={decision.price if decision.price is not None else "—"} TF={decision.interval or "—"}')
    finally:
        await exchange.close()
    print(f'Smart Entry: checked={checked} attempts={attempts} mode={"LIVE_OPT_IN" if live_allowed else "READ_ONLY"}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--hours', type=float, default=16)
    p.add_argument('--data-dir', default=os.getenv('SIGNALBOT_DATA_DIR', '/data'))
    p.add_argument('--execute-live', action='store_true', help='Explicit live attempt; also requires TV_SMART_AUTO_EXECUTION=YES and LIVE guards')
    args = p.parse_args()
    asyncio.run(scan(args.hours, args.data_dir, attempt_live=args.execute_live))
