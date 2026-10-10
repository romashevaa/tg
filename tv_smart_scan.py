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


async def scan(hours=16, data_dir='/data'):
    data_dir = Path(data_dir)
    with sqlite3.connect(str(data_dir / 'signalbot.db'), timeout=20) as db:
        enabled = authors_db.enabled_authors(db)
    now = datetime.now(timezone.utc)
    checked = 0
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
                        try:
                            decision = await inspect(exchange, evidence)
                        except Exception as exc:
                            from tv_smart_entry import Decision
                            decision = Decision('CHECK', f'Не вдалося перевірити ринок: {type(exc).__name__}')
                    save(db, iid, author, decision)
                    checked += 1
                    print(f'@{author} {iid}: age={age:.1f}h {decision.status}: {decision.reason}; '
                          f'BingX={decision.price if decision.price is not None else "—"} TF={decision.interval or "—"}')
    finally:
        await exchange.close()
    print(f'Smart Entry: checked={checked} (read-only; no orders)')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--hours', type=float, default=16)
    p.add_argument('--data-dir', default=os.getenv('SIGNALBOT_DATA_DIR', '/data'))
    args = p.parse_args()
    asyncio.run(scan(args.hours, args.data_dir))
