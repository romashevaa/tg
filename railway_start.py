"""Railway 24/7 runner. Requires persistent /data volume and pre-existing Telegram user session."""
import os, sqlite3, sys, threading, time, logging
from pathlib import Path
from urllib.parse import urlparse
import tv_update_monitor as monitor

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log=logging.getLogger('railway')
DATA=Path(os.environ.get('SIGNALBOT_DATA_DIR','/data'))

def guard():
    if not DATA.is_dir():
        raise SystemExit(f'Persistent volume {DATA} missing. Mount Railway Volume at {DATA} before starting.')
    if os.getenv('TRADING_MODE','shadow').lower()!='shadow':
        raise SystemExit('Railway test build allows TRADING_MODE=shadow only.')
    session=DATA/'signalbot.session'
    if not session.exists():
        raise SystemExit(f'Telegram reader session missing: {session}. Migrate existing .session file to the Railway volume; do not start interactive login in container.')
    if not os.getenv('OWNER_ID'):
        raise SystemExit('OWNER_ID required on Railway.')
    os.environ['TG_SESSION']=str(DATA/'signalbot')
    os.environ['SIGNALBOT_DB_PATH']=str(DATA/'signalbot.db')

def watch():
    interval=max(300,int(os.getenv('TV_POLL_SECONDS','600')))
    dbfile=DATA/'signalbot.db'
    eventdb=monitor.init_db(str(DATA/'tv_updates_monitor.sqlite'))
    log.info('TradingView monitor started, interval=%ss',interval)
    while True:
        try:
            with sqlite3.connect(f'file:{dbfile}?mode=ro',uri=True,timeout=15) as db:
                ideas=db.execute('SELECT idea_id,url FROM tv_ideas').fetchall()
            log.info('TV scan starting, saved ideas=%s',len(ideas))
            failures=0
            for iid,url in ideas:
                try:
                    parsed=urlparse(url)
                    if parsed.hostname not in ('tradingview.com','www.tradingview.com') or not parsed.path.startswith('/chart/'):
                        continue
                    _,new=monitor.scan(eventdb,iid,monitor.get_page(url))
                    if new:log.info('TV %s: %s new timeline events',iid,len(new))
                except Exception as exc:
                    failures+=1; log.error('TV idea %s failed: %s',iid,exc)
                time.sleep(1)
            log.info('TV scan finished, failures=%s',failures)
        except Exception as exc:log.exception('TV monitor iteration failed: %s',exc)
        time.sleep(interval)

def main():
    guard()
    from signalbot.cli import main as bot_main
    threading.Thread(target=watch,daemon=True,name='tv-monitor').start()
    sys.argv=['signalbot','--config','config.toml','run']
    bot_main()

if __name__=='__main__':main()
