"""Persistent author subscriptions managed through the owner Telegram bot.

Uses signalbot.db so subscriptions survive Railway deployment. No trading actions.
"""
import sqlite3

DEFAULT_AUTHORS = ('Altsignals', 'coinpediamarkets')


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tv_monitor_authors (
        handle TEXT PRIMARY KEY COLLATE NOCASE,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
    )''')
    # Idempotent migration for the two monitored authors from older releases.
    db.executemany('INSERT OR IGNORE INTO tv_monitor_authors(handle,enabled) VALUES (?,1)',
                   [(a,) for a in DEFAULT_AUTHORS])
    db.commit()


def set_enabled(db, handle, enabled):
    from signalbot.tv_registry import parse_profile
    name, _ = parse_profile(handle)
    initialize(db)
    with db:
        db.execute('''INSERT INTO tv_monitor_authors(handle,enabled) VALUES (?,?)
            ON CONFLICT(handle) DO UPDATE SET enabled=excluded.enabled''', (name, int(bool(enabled))))
    return name


def list_authors(db):
    initialize(db)
    return db.execute('SELECT handle,enabled FROM tv_monitor_authors ORDER BY handle COLLATE NOCASE').fetchall()


def enabled_authors(db):
    return [name for name, enabled in list_authors(db) if enabled]
