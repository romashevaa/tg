"""Review the last N hours and safely reconsider cached, recent TradingView signals.

Caution: cached reviews are NOT force-traded. The exchange engine owns the
freshness, closed-idea, price, symbol, and idempotency gates.
"""
import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from signalbot.config import load_config
from tv_full_review import Evidence, run
from tv_live_engine import execute
from tv_timeframe_recovery import recover
from tv_trade_diagnostics import bingx_pair
import tv_author_settings as author_settings
import tv_auto_alerts as alerts
import tv_last_hour


async def contract_diagnostic(evidence, exchange=None):
    """Read-only contract status for a rejected/cached signal. Never sends orders."""
    pair = bingx_pair(evidence.symbol)
    if not pair:
        return f'symbol={evidence.symbol or "unknown"} pair=NONE contract=UNSUPPORTED_NON_USDT'
    owned = exchange is None
    if owned:
        from signalbot.bingx import BingX
        exchange = BingX('', '')
    try:
        info = await exchange.market_info(pair, 'futures')
        if info is None:
            return f'symbol={evidence.symbol} pair={pair} contract=NOT_LISTED'
        return f'symbol={evidence.symbol} pair={pair} contract=LISTED price={info.price}'
    except Exception as exc:
        return f'symbol={evidence.symbol} pair={pair} contract=UNVERIFIED ({type(exc).__name__})'
    finally:
        if owned:
            await exchange.close()


def author_author_suspended(db, author):
    return bool(author_settings.suspension_info(db, author))


async def main(hours, max_ideas, notify, data_dir):
    cfg = load_config('config.toml')
    # Always process uncached ideas once, then reconsider prior AI output without spending Gemini.
    await run(hours=hours, max_ideas=max_ideas, notify=notify, data_dir=data_dir)
    if cfg.trading_mode != 'live' or os.getenv('TV_LIVE_EXECUTION') != 'YES':
        print('Cached LIVE checks skipped: TRADING_MODE=live and TV_LIVE_EXECUTION=YES required')
        return
    data = Path(data_dir)
    with sqlite3.connect(str(data / 'signalbot.db'), timeout=20) as db:
        authors = author_settings.enabled_authors(db)
    with sqlite3.connect(str(data / 'tv_updates_monitor.sqlite'), timeout=20) as db:
        existing = {r[0] for r in db.execute('SELECT idea_id FROM tv_live_orders')} if db.execute("SELECT name FROM sqlite_master WHERE name='tv_live_orders'").fetchone() else set()
        for author in authors:
            if author_author_suspended(db, author):
                continue
            try:
                entries = alerts.fetch_latest(author_name=author)
            except Exception as err:
                print(f'@{author} FETCH_FAILED: {err}')
                continue
            for entry in entries:
                record = alerts.unpack(entry, author_name=author)
                published = tv_last_hour.published_at(entry)
                if not record or not published:
                    continue
                iid, _, _ = record
                age = (datetime.now(timezone.utc) - published).total_seconds()
                if not 0 <= age <= hours * 3600 or iid in existing:
                    continue
                cached = db.execute('SELECT result_json FROM tv_full_chart_reviews WHERE idea_id=?', (iid,)).fetchone()
                if not cached:
                    continue
                try:
                    evidence = Evidence.model_validate(json.loads(cached[0]))
                    if not evidence.timeframe:
                        evidence, tf_source = await recover(db, iid, entry, record[1], evidence, cfg)
                        print(f'CACHED @{author} {iid}: TF_RECOVERY={evidence.timeframe or "unknown"} source={tf_source}')
                    status, reason = await execute(db, iid, author, evidence, published, cfg)
                    market = await contract_diagnostic(evidence) if status in ('SKIP', 'CHECK') else f'symbol={evidence.symbol} pair={bingx_pair(evidence.symbol) or "NONE"}'
                    print(f'CACHED @{author} {iid}: age_minutes={age/60:.1f} {status}: {reason} | {market}')
                except Exception as err:
                    print(f'CACHED @{author} {iid}: ERROR {type(err).__name__}: {err}')
                existing.add(iid)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', type=float, default=12)
    parser.add_argument('--max-ideas', type=int, default=100000, help='Upper bound of uncached ideas to review, not a trading quota')
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.hours, args.max_ideas, args.notify, os.getenv('SIGNALBOT_DATA_DIR', '/data')))
