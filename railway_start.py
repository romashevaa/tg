"""Railway 24/7 shadow bot + TradingView monitor with discovery and Telegram alerts."""
import os
import sqlite3
import sys
import threading
import time
import logging
from pathlib import Path
import tv_update_monitor as monitor
import tv_auto_alerts as alerts

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


def watch():
    interval = max(60, int(os.getenv('TV_POLL_SECONDS', '60')))
    dbfile = DATA / 'signalbot.db'
    eventdb = monitor.init_db(str(DATA / 'tv_updates_monitor.sqlite'))
    alerts.initialize(eventdb)
    log.info('TradingView monitor started, interval=%ss', interval)
    while True:
        try:
            with sqlite3.connect(f'file:{dbfile}?mode=ro', uri=True, timeout=15) as db:
                historical = db.execute('SELECT idea_id,url FROM tv_ideas').fetchall()
            historical_ids = {idea_id for idea_id, _ in historical}
            try:
                alerts.discover(eventdb, historical_ids)
            except Exception as exc:
                log.warning('TV discovery failed (%s): %s', type(exc).__name__, exc)
            discovered = alerts.all_discovered(eventdb)
            tasks = {idea_id: alerts.normalized_url(url) for idea_id, url in historical}
            tasks.update({idea_id: alerts.normalized_url(url) for idea_id, url in discovered})
            log.info('TV scan starting, historical=%s discovered=%s total=%s',
                     len(historical), len(discovered), len(tasks))
            failures = 0
            for idea_id, url in tasks.items():
                try:
                    if not url:
                        raise ValueError('Invalid TradingView idea URL')
                    known_before = eventdb.execute(
                        'SELECT time_utc FROM tv_events WHERE idea=?', (idea_id,)).fetchall()
                    _, new = monitor.scan(eventdb, idea_id, monitor.get_page(url))
                    if new:
                        log.info('TV %s: %s new events', idea_id, len(new))
                        alerts.update_alerts(eventdb, idea_id, url, new, known_before,
                                             idea_id not in historical_ids and not known_before)
                except Exception as exc:
                    failures += 1
                    log.error('TV idea %s failed: %s', idea_id, exc)
                time.sleep(1)
            alerts.deliver(eventdb)
            log.info('TV scan finished, failures=%s', failures)
        except Exception as exc:
            log.exception('TV monitor iteration failed: %s', exc)
        time.sleep(interval)


def main():
    guard()
    from signalbot.cli import main as bot_main
    threading.Thread(target=watch, daemon=True, name='tv-monitor').start()
    sys.argv = ['signalbot', '--config', 'config.toml', 'run']
    bot_main()


if __name__ == '__main__':
    main()
