"""Read-only conditional observation of market structure. No order submission."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from .opportunity import watch_snapshot, _fmt
from .scenario import plan, Scenario, evaluate

log = logging.getLogger("signalbot.watch")
INTERVAL_SECONDS = 60
MAX_WATCH_HOURS = 24

class WatchManager:
    def __init__(self, bot, pipeline):
        self.bot = bot
        self.pipeline = pipeline
        self.db = pipeline.storage.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS manual_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, symbol TEXT NOT NULL,
            market TEXT NOT NULL, side TEXT NOT NULL, expires_at TEXT NOT NULL,
            last_candle INTEGER NOT NULL DEFAULT 0, alerted INTEGER NOT NULL DEFAULT 0)""")
        columns={r[1] for r in self.db.execute("PRAGMA table_info(manual_watches)")}
        if "scenario_json" not in columns:
            self.db.execute("ALTER TABLE manual_watches ADD COLUMN scenario_json TEXT")
        self.db.commit()
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.loop(), name="manual-watch-loop")

    async def stop(self):
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass

    def add(self, source, symbol, market, side, sig=None):
        if side not in ('long','short') or not symbol:
            raise ValueError("Спостереження доступне лише за визначеним LONG/SHORT та парою")
        if not source.startswith('https://'):
            raise ValueError("Потрібне https-посилання на повідомлення або TradingView")
        expiry = (datetime.now(timezone.utc)+timedelta(hours=MAX_WATCH_HOURS)).isoformat()
        scenario_data = plan(sig).dumps() if sig is not None else None
        # One active observation per source; re-adding renews it.
        previous = self.db.execute('SELECT id FROM manual_watches WHERE source=?', (source,)).fetchone()
        if previous:
            self.db.execute('UPDATE manual_watches SET symbol=?,market=?,side=?,expires_at=?,last_candle=0,alerted=0,scenario_json=? WHERE id=?',
                            (symbol,market,side,expiry,scenario_data,previous['id']))
            wid = previous['id']
        else:
            c = self.db.execute('INSERT INTO manual_watches (source,symbol,market,side,expires_at,scenario_json) VALUES (?,?,?,?,?,?)',
                                (source,symbol,market,side,expiry,scenario_data))
            wid=c.lastrowid
        self.db.commit()
        return wid

    def list(self):
        self.prune()
        return self.db.execute('SELECT * FROM manual_watches ORDER BY id DESC').fetchall()

    def prune(self):
        self.db.execute('DELETE FROM manual_watches WHERE expires_at <= ?', (datetime.now(timezone.utc).isoformat(),))
        self.db.commit()

    def remove(self, wid):
        c=self.db.execute('DELETE FROM manual_watches WHERE id=?',(wid,))
        self.db.commit()
        return c.rowcount > 0

    async def loop(self):
        # Only send alerts on a NEW closed 15m candle; never on startup or repeat.
        while True:
            try:
                await self.check_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Watch loop error')
            await asyncio.sleep(INTERVAL_SECONDS)

    async def check_all(self):
        for row in self.list():
            try:
                await self.check_one(row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Watch %s fetch failed: %s', row['id'], exc)

    async def check_one(self,row):
        frames=await watch_snapshot(self.pipeline.exchange,row['symbol'],row['market'])
        f=frames['15m']
        candle=f['last_closed_start']
        if not row['last_candle']:
            self.db.execute('UPDATE manual_watches SET last_candle=? WHERE id=?',(candle,row['id']))
            self.db.commit()
            return
        if candle <= row['last_candle']:
            return
        self.db.execute('UPDATE manual_watches SET last_candle=? WHERE id=?',(candle,row['id']))
        self.db.commit()
        if row['scenario_json']:
            scenario = Scenario.loads(row['scenario_json'])
            result, reason = evaluate(scenario, f['close'], f)
            if result == 'INVALIDATED':
                self.db.execute('DELETE FROM manual_watches WHERE id=?', (row['id'],))
                self.db.commit()
                await self.bot.send(f"⛔ WATCH #{row['id']} · сценарій інвалідований\n{reason}\n{row['source']}\nБез ордерів.")
            elif result == 'REVIEW' and not row['alerted']:
                self.db.execute('UPDATE manual_watches SET alerted=1 WHERE id=?', (row['id'],))
                self.db.commit()
                await self.bot.send(f"🔔 WATCH #{row['id']} · перевірка сценарію\n{row['symbol']} {row['side'].upper()}\n{reason}\n{row['source']}\nЦе НЕ підтверджений вхід. Перевірте /analyze; без ордерів.")
            return
        direction='down' if row['side']=='short' else 'up'
        aligned = frames['1h']['trend']==direction and frames['4h']['trend']==direction
        # A CLOSED candle returns to MA20 in the intended trend's direction.
        crossed=(f['previous_close'] >= f['ma20'] and f['close'] < f['ma20']) if direction=='down' else (f['previous_close'] <= f['ma20'] and f['close'] > f['ma20'])
        if aligned and crossed and not row['alerted']:
            self.db.execute('UPDATE manual_watches SET alerted=1 WHERE id=?',(row['id'],))
            self.db.commit()
            await self.bot.send(
                f"🔔 WATCH #{row['id']} · {row['symbol']} {row['side'].upper()}\n"
                f"Закрита 15m свічка перейшла через MA20 у напрямку сценарію. "
                f"1H і 4H узгоджені.\n"
                f"Close {_fmt(f['close'])} · MA20 {_fmt(f['ma20'])}\n"
                f"Джерело: {row['source']}\n\n"
                "Це тільки технічний тригер спостереження, НЕ торговий сигнал. "
                "Немає підтверджених Entry, SL, TP або R:R. Жодних ордерів. "
                "Перевірте /analyze повторно.")
