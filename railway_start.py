"""Railway shadow runner with separate public idea discovery and update schedules."""
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import tv_auto_alerts as alerts
import tv_update_monitor as monitor
import tv_watch_policy as policy
import tv_author_settings as author_settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('railway')
DATA = Path(os.environ.get('SIGNALBOT_DATA_DIR', '/data'))


def guard():
    if not DATA.is_dir():
        raise SystemExit(f'Persistent volume {DATA} missing. Mount a Railway Volume.')
    mode = os.getenv('TRADING_MODE', 'shadow').strip().lower()
    if mode not in ('shadow', 'live'):
        raise SystemExit('Only shadow/live are supported by this Railway runner.')
    if mode == 'live':
        if os.getenv('BINGX_LIVE_ACK', '') != 'I_UNDERSTAND_REAL_ORDERS':
            raise SystemExit('Live mode requires BINGX_LIVE_ACK=I_UNDERSTAND_REAL_ORDERS')
        if os.getenv('TV_LIVE_EXECUTION') == 'YES' and os.getenv('TV_LIVE_ACK') != 'I_ACCEPT_TWO_REAL_ORDERS':
            raise SystemExit('TV LIVE requires TV_LIVE_ACK=I_ACCEPT_TWO_REAL_ORDERS')
        log.warning('LIVE ENABLED: real BingX orders are possible; TradingView requires separate TV_LIVE_EXECUTION=YES.')
    if not (DATA / 'signalbot.session').exists():
        raise SystemExit('Telegram reader session missing: /data/signalbot.session')
    if not os.getenv('OWNER_ID'):
        raise SystemExit('OWNER_ID required on Railway.')
    os.environ['TG_SESSION'] = str(DATA / 'signalbot')
    os.environ['SIGNALBOT_DB_PATH'] = str(DATA / 'signalbot.db')


def watch_discovery():
    interval = max(60, int(os.getenv('TV_DISCOVERY_SECONDS', '60')))
    db = monitor.init_db(str(DATA / 'tv_updates_monitor.sqlite'))
    alerts.initialize(db)
    log.info('TV discovery watcher started, interval=%ss', interval)
    try:
        while True:
            started = time.monotonic()
            try:
                with sqlite3.connect(f'file:{DATA / "signalbot.db"}?mode=ro', uri=True, timeout=15) as source:
                    historical_ids = {r[0] for r in source.execute('SELECT idea_id FROM tv_ideas')}
                with sqlite3.connect(str(DATA / 'signalbot.db'), timeout=15) as author_db:
                    authors = author_settings.enabled_authors(author_db)
                for author_name in authors:
                    try:
                        if not author_settings.check_author(db, author_name):
                            continue
                        alerts.discover(db, historical_ids, author_name=author_name)
                    except Exception as exc:
                        log.warning('TV discovery [%s] failed (%s): %s', author_name, type(exc).__name__, exc)
                alerts.deliver(db)
            except Exception as exc:
                log.warning('TV discovery iteration failed (%s): %s', type(exc).__name__, exc)
            time.sleep(max(1, interval - (time.monotonic() - started)))
    finally:
        db.close()


def watch_updates():
    interval = max(60, int(os.getenv('TV_UPDATES_SECONDS', '300')))
    db = monitor.init_db(str(DATA / 'tv_updates_monitor.sqlite'))
    alerts.initialize(db)
    policy.initialize(db)
    seeded = policy.initialize_existing(db)
    log.info('TV update watcher started, interval=%ss, historical closed seeded=%s', interval, seeded)
    try:
        while True:
            started = time.monotonic()
            failures = 0
            try:
                with sqlite3.connect(str(DATA / 'signalbot.db'), timeout=15) as source:
                    enabled = {name.lower() for name in author_settings.enabled_authors(source)}
                    historical = source.execute('SELECT idea_id,url,handle FROM tv_ideas').fetchall()
                # Never poll historical ideas from disabled/suspended authors.
                suspended = {name.lower() for name in enabled if author_settings.suspension_info(db, name)}
                eligible = enabled - suspended
                historical_ids = {idea_id for idea_id, _, _ in historical}
                discovered = alerts.all_discovered(db)
                idea_author = {iid: author for iid, author in db.execute(
                    'SELECT idea_id,author FROM tv_discovered_author').fetchall()}
                tasks = {iid: alerts.normalized_url(url) for iid, url, author in historical
                         if author.lower() in eligible}
                tasks.update({iid: alerts.normalized_url(url) for iid, url in discovered
                              if idea_author.get(iid, '').lower() in eligible})
                closed = policy.closed_ids(db)
                log.info('TV updates starting, historical=%s discovered=%s skipped_closed=%s scanning=%s suspended=%s',
                         len(historical), len(discovered), len(closed & tasks.keys()), len(tasks.keys() - closed), sorted(suspended))
                for idea_id, url in tasks.items():
                    if idea_id in closed:
                        continue
                    try:
                        if not url:
                            raise ValueError('Invalid TradingView idea URL')
                        known_before = db.execute(
                            'SELECT time_utc FROM tv_events WHERE idea=?', (idea_id,)).fetchall()
                        rows, new = monitor.scan(db, idea_id, monitor.get_page(url))
                        if new:
                            log.info('TV %s: %s new events', idea_id, len(new))
                            alerts.update_alerts(db, idea_id, url, new, known_before,
                                                 idea_id not in historical_ids and not known_before,
                                                 author_name=idea_author.get(idea_id, alerts.AUTHOR))
                        reason = policy.record_closed(db, idea_id, rows)
                        if reason:
                            log.info('TV idea %s closed by author: %s', idea_id, reason)
                    except Exception as exc:
                        failures += 1
                        log.error('TV idea %s failed: %s', idea_id, exc)
                    time.sleep(1)
                # Only discovery thread delivers outbox messages, avoiding concurrent send duplicates.
                log.info('TV updates finished, failures=%s', failures)
            except Exception as exc:
                log.exception('TV update iteration failed: %s', exc)
            time.sleep(max(1, interval - (time.monotonic() - started)))
    finally:
        db.close()


def watch_full_reviews():
    """Optional cached Gemini image+text reviews; never places orders."""
    import asyncio
    from tv_full_review import run
    interval = max(60, int(os.getenv('TV_FULL_REVIEW_SECONDS', '60')))
    max_ideas = max(1, int(os.getenv('TV_FULL_REVIEW_PER_CYCLE', '1000')))
    log.info('TV full chart review started, interval=%ss, max_per_cycle=%s', interval, max_ideas)
    while True:
        started = time.monotonic()
        try:
            # Notification delivery is handled by the existing discovery watcher.
            asyncio.run(run(hours=float(os.getenv('TV_FULL_REVIEW_LOOKBACK_HOURS', '1')), max_ideas=max_ideas, notify=True,
                            data_dir=str(DATA), send_now=False))
        except Exception as exc:
            log.warning('TV full chart review failed (%s): %s', type(exc).__name__, str(exc)[:180])
        time.sleep(max(1, interval - (time.monotonic() - started)))



def watch_smart_entry():
    """Smart Entry scenario monitor; LIVE attempts require explicit separate opt-in."""
    import asyncio
    from tv_smart_scan import scan
    interval = max(60, int(os.getenv('TV_SMART_ENTRY_SECONDS', '300')))
    active = os.getenv('TV_SMART_AUTO_EXECUTION') == 'YES'
    log.info('TV Smart Entry watcher started, interval=%ss, auto_live_requested=%s', interval, active)
    while True:
        started = time.monotonic()
        try:
            log.info('TV Smart Entry cycle started, auto_live_requested=%s', os.getenv('TV_SMART_AUTO_EXECUTION') == 'YES')
            asyncio.run(scan(hours=float(os.getenv('TV_SMART_ENTRY_LOOKBACK_HOURS', '16')), data_dir=str(DATA),
                             attempt_live=(os.getenv('TV_SMART_AUTO_EXECUTION') == 'YES')))
            log.info('TV Smart Entry cycle finished')
        except Exception as exc:
            log.warning('TV Smart Entry failed (%s): %s', type(exc).__name__, str(exc)[:180])
        time.sleep(max(1, interval - (time.monotonic() - started)))


def main():
    guard()
    from signalbot.cli import main as bot_main
    threading.Thread(target=watch_discovery, daemon=True, name='tv-discovery').start()
    threading.Thread(target=watch_updates, daemon=True, name='tv-updates').start()
    if os.getenv('TV_FULL_REVIEW_ENABLED', '0') == '1':
        threading.Thread(target=watch_full_reviews, daemon=True, name='tv-full-review').start()
    if os.getenv('TV_SMART_ENTRY_ENABLED', '0') == '1':
        threading.Thread(target=watch_smart_entry, daemon=True, name='tv-smart-entry').start()
    sys.argv = ['signalbot', '--config', 'config.toml', 'run']
    bot_main()


if __name__ == '__main__':
    main()
