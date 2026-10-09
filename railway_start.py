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
    if os.getenv('TRADING_MODE', 'shadow').lower() != 'shadow':
        raise SystemExit('Railway test build allows TRADING_MODE=shadow only.')
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
                with sqlite3.connect(f'file:{DATA / "signalbot.db"}?mode=ro', uri=True, timeout=15) as source:
                    historical = source.execute('SELECT idea_id,url FROM tv_ideas').fetchall()
                historical_ids = {idea_id for idea_id, _ in historical}
                discovered = alerts.all_discovered(db)
                idea_author = {iid: author for iid, author in db.execute(
                    'SELECT idea_id,author FROM tv_discovered_author').fetchall()}
                tasks = {idea_id: alerts.normalized_url(url) for idea_id, url in historical}
                tasks.update({idea_id: alerts.normalized_url(url) for idea_id, url in discovered})
                closed = policy.closed_ids(db)
                log.info('TV updates starting, historical=%s discovered=%s skipped_closed=%s scanning=%s',
                         len(historical), len(discovered), len(closed & tasks.keys()), len(tasks.keys() - closed))
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


def main():
    guard()
    from signalbot.cli import main as bot_main
    threading.Thread(target=watch_discovery, daemon=True, name='tv-discovery').start()
    threading.Thread(target=watch_updates, daemon=True, name='tv-updates').start()
    sys.argv = ['signalbot', '--config', 'config.toml', 'run']
    bot_main()


if __name__ == '__main__':
    main()
