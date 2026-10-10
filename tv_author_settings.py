"""Persistent author subscriptions managed through the owner Telegram bot.

Uses signalbot.db so subscriptions survive Railway deployment. No trading actions.
"""
import sqlite3
import time
import urllib.request
import urllib.error

DEFAULT_AUTHORS = ('Altsignals', 'coinpediamarkets')


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tv_monitor_authors (
        handle TEXT PRIMARY KEY COLLATE NOCASE,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
    )''')
    # Idempotent migration for the two monitored authors from older releases.
    db.executemany('INSERT OR IGNORE INTO tv_monitor_authors(handle,enabled) VALUES (?,1)',
                   [(a,) for a in DEFAULT_AUTHORS])
    # Migrate owners added before /tvadd enabled automatic monitoring.
    legacy = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tv_authors'").fetchone()
    if legacy:
        db.execute('''INSERT OR IGNORE INTO tv_monitor_authors(handle, enabled)
                      SELECT lower(handle), 1 FROM tv_authors''')
    db.execute('''CREATE TABLE IF NOT EXISTS tv_suspended_authors (
        handle TEXT PRIMARY KEY COLLATE NOCASE, checked_at REAL NOT NULL,
        next_check_at REAL NOT NULL, reason TEXT NOT NULL
    )''')
    db.commit()


def suspension_info(db, handle):
    initialize(db)
    return db.execute('SELECT next_check_at,reason FROM tv_suspended_authors WHERE handle=?', (handle,)).fetchone()


def check_author(db, handle, fetch=None, current_time=None):
    # Fail open for network/rate-limit errors; suspend only for explicit TradingView notice.
    from signalbot.tv_registry import parse_profile
    name, url = parse_profile(handle)
    current_time = current_time if current_time is not None else time.time()
    previous = suspension_info(db, name)
    if previous and previous[0] > current_time:
        return False
    try:
        if fetch is None:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            try:
                with urllib.request.urlopen(req, timeout=12) as res:
                    body = res.read(512000).decode('utf-8', errors='replace')
            except urllib.error.HTTPError as exc:
                # Suspension notices sometimes arrive with an HTTP error status.
                body = exc.read(512000).decode('utf-8', errors='replace')
        else:
            body = fetch(url)
        blocked = ('got suspended for bad behavior' in body.lower()
                   or ('suspended' in body.lower() and 'house rules' in body.lower()))
        if blocked:
            with db:
                db.execute('''INSERT INTO tv_suspended_authors VALUES (?,?,?,?)
                              ON CONFLICT(handle) DO UPDATE SET checked_at=excluded.checked_at,
                              next_check_at=excluded.next_check_at,reason=excluded.reason''',
                           (name, current_time, current_time + 12*3600, 'TradingView suspension notice'))
            return False
        if previous:
            with db:
                db.execute('DELETE FROM tv_suspended_authors WHERE handle=?', (name,))
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        # Do not infer suspension from transient connectivity failures.
        if previous:
            with db:
                db.execute('UPDATE tv_suspended_authors SET next_check_at=? WHERE handle=?',
                           (current_time+3600, name))
            return False
        return True


def set_enabled(db, handle, enabled):
    from signalbot.tv_registry import parse_profile
    name, _ = parse_profile(handle)
    initialize(db)
    with db:
        db.execute('''INSERT INTO tv_monitor_authors(handle,enabled) VALUES (?,?)
            ON CONFLICT(handle) DO UPDATE SET enabled=excluded.enabled''', (name, int(bool(enabled))))
    if not enabled:
        with db:
            db.execute('DELETE FROM tv_suspended_authors WHERE handle=?', (name,))
    return name


def list_authors(db):
    initialize(db)
    return db.execute('SELECT handle,enabled FROM tv_monitor_authors ORDER BY handle COLLATE NOCASE').fetchall()


def enabled_authors(db):
    return [name for name, enabled in list_authors(db) if enabled]
