"""Multi-author TradingView public idea discovery and owner-only Telegram notifications.

No orders, no Gemini, no Telegram user-session access.  The existing monitor DB
is the source of truth for discovery, deduplication and alert delivery.
"""
import json
import logging
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape

log = logging.getLogger('tv-alerts')
API = 'https://www.tradingview.com/api/v1/ideas/'
AUTHOR = 'Altsignals'
AUTHORS = ('Altsignals', 'coinpediamarkets')
IDEA_RE = re.compile(r'/chart/[^/]+/([A-Za-z0-9]{8})(?:[-/]|$)')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS tv_discovered (idea_id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT, discovered_utc TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS tv_discovered_author (idea_id TEXT PRIMARY KEY, author TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS tv_alert_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS tv_alert_outbox (
        alert_key TEXT PRIMARY KEY, text TEXT NOT NULL, created_utc TEXT NOT NULL,
        sent_utc TEXT)''')
    db.commit()


def now():
    return datetime.now(timezone.utc).isoformat()


def normalized_url(url):
    if not isinstance(url, str):
        return ''
    value = url.strip()
    m = re.fullmatch(r'\[[^]]+\]\((https?://[^)]+)\)', value)
    if m:
        value = m.group(1)
    if value.startswith('//'):
        value = 'https:' + value
    if value.startswith('/'):
        value = 'https://www.tradingview.com' + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.tradingview.com', 'tradingview.com'):
        return ''
    if not IDEA_RE.search(parsed.path):
        return ''
    return urllib.parse.urlunsplit(('https', 'www.tradingview.com', parsed.path, '', ''))


def unpack(entry, author_name=AUTHOR):
    """Accept known public API wrappers but reject non-idea entries."""
    if not isinstance(entry, dict):
        return None
    data = entry.get('data') if isinstance(entry.get('data'), dict) else entry
    idea_id = str(data.get('image_url') or data.get('idea_id') or '')
    title = str(data.get('name') or data.get('title') or entry.get('name') or '')
    author = data.get('author') or entry.get('author') or data.get('user')
    if isinstance(author, dict):
        author = author.get('username') or author.get('name') or author.get('login')
    # API is already filtered by author; if an author is present, verify it.
    if isinstance(author, str) and author.lower() != author_name.lower():
        return None
    candidates = [data.get('chart_url'), data.get('url'), data.get('short_url'), data.get('link'),
                  entry.get('url'), entry.get('short_url'), entry.get('link')]
    url = next((v for v in (normalized_url(u) for u in candidates) if v), '')
    if not url:
        return None
    parsed_id = IDEA_RE.search(urllib.parse.urlsplit(url).path).group(1)
    if not re.fullmatch(r'[A-Za-z0-9]{8}', idea_id):
        idea_id = parsed_id
    if idea_id != parsed_id:
        return None
    return idea_id, url, title


def fetch_latest(per_page=50, author_name=AUTHOR):
    params = urllib.parse.urlencode({'page': 1, 'per_page': per_page, 'by': author_name,
                                     'locale': 'en', 'q': ''})
    req = urllib.request.Request(API + '?' + params,
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = json.load(response)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = next((payload[k] for k in ('results', 'data', 'ideas', 'items')
                      if isinstance(payload.get(k), list)), None)
        if isinstance(items, dict):
            items = items.get('results')
    else:
        items = None
    if not isinstance(items, list):
        raise RuntimeError('Unexpected TradingView API response structure')
    return items


def enqueue(db, key, text):
    with db:
        db.execute('INSERT OR IGNORE INTO tv_alert_outbox(alert_key,text,created_utc) VALUES (?,?,?)',
                   (key, text, now()))


def discover(db, existing_ids, fetch=None, author_name=AUTHOR):
    """First successful discovery seeds silently. Subsequent discoveries notify."""
    initialize(db)
    entries = fetch() if fetch is not None else fetch_latest(author_name=author_name)
    ideas = {}
    for entry in entries:
        record = unpack(entry, author_name=author_name)
        if record:
            ideas[record[0]] = record[1:]
    if entries and not ideas:
        raise RuntimeError('TradingView API returned entries, but no recognizable idea URLs; inspect API schema')
    # Preserve AltSignals baseline from previous deployments; new authors get their own baseline.
    state_key = 'discovery_initialized' if author_name.lower() == AUTHOR.lower() else 'discovery_initialized:' + author_name.lower()
    ready = db.execute('SELECT value FROM tv_alert_state WHERE key=?', (state_key,)).fetchone()
    baseline = ready is None
    added = 0
    with db:
        for idea_id, (url, title) in ideas.items():
            known = idea_id in existing_ids or db.execute(
                'SELECT 1 FROM tv_discovered WHERE idea_id=?', (idea_id,)).fetchone()
            if known:
                # Record author for existing discovered ideas when migrating from the single-author monitor.
                if db.execute('SELECT 1 FROM tv_discovered WHERE idea_id=?', (idea_id,)).fetchone():
                    db.execute('INSERT OR IGNORE INTO tv_discovered_author VALUES (?,?)', (idea_id, author_name))
                continue
            db.execute('INSERT OR IGNORE INTO tv_discovered VALUES (?,?,?,?)',
                       (idea_id, url, title, now()))
            db.execute('INSERT OR IGNORE INTO tv_discovered_author VALUES (?,?)', (idea_id, author_name))
            added += 1
            # On first subscription, recover posts published during the past hour,
            # while keeping older history silent. No AI/trade execution.
            recent_first_sync = False
            if baseline:
                from tv_last_hour import published_at
                source_entry = next((e for e in entries if unpack(e, author_name=author_name) and unpack(e, author_name=author_name)[0] == idea_id), None)
                published = published_at(source_entry) if source_entry else None
                recent_first_sync = bool(published and datetime.now(timezone.utc) - timedelta(hours=1) <= published <= datetime.now(timezone.utc) + timedelta(minutes=5))
            if not baseline or recent_first_sync:
                message = ('🆕 <b>Нова ідея ' + escape(author_name) + '</b>\n' +
                           (escape(title[:180]) + '\n' if title else '') +
                           '<a href="' + escape(url, quote=True) + '">Відкрити TradingView</a>')
                db.execute('INSERT OR IGNORE INTO tv_alert_outbox(alert_key,text,created_utc) VALUES (?,?,?)',
                           ('idea:' + idea_id, message, now()))
        if baseline:
            db.execute('INSERT OR REPLACE INTO tv_alert_state VALUES (?,?)', (state_key, '1'))
    log.info('TV discovery [%s]: API entries=%s valid=%s added=%s baseline=%s',
             author_name, len(entries), len(ideas), added, baseline)
    return added


def all_discovered(db):
    return db.execute('SELECT idea_id,url FROM tv_discovered').fetchall()


def parse_time(value):
    try:
        dt = parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (AttributeError, ValueError):
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def update_alerts(db, idea_id, url, fresh_events, known_before, is_new_idea, author_name=AUTHOR):
    """Only recent, strictly later updates on already-tracked ideas become alerts."""
    if not known_before or is_new_idea:
        return 0
    last = max((dt for row in known_before if (dt := parse_time(row[0]))), default=None)
    count = 0
    for row in fresh_events:
        event_time = parse_time(row['time'])
        if not event_time or event_time < datetime.now(timezone.utc) - timedelta(hours=48):
            continue
        if last and event_time <= last:
            continue
        key_row = db.execute('SELECT event_key FROM tv_events WHERE idea=? AND time_utc=? AND status=? AND body=?',
                             (idea_id, row['time'], row['status'], row['text'])).fetchone()
        if not key_row:
            continue
        title = escape((row['status'] or 'Оновлення автора')[:120])
        message = ('🔔 <b>Оновлення ' + escape(author_name) + '</b>\n<b>' + title + '</b>\n' +
                   escape(row['text'][:800]) + '\n' +
                   '<a href="' + escape(url, quote=True) + '">Відкрити TradingView</a>')
        enqueue(db, 'event:' + idea_id + ':' + key_row[0], message)
        count += 1
    return count


def deliver(db):
    token = os.getenv('BOT_TOKEN', '').strip()
    owner = os.getenv('OWNER_ID', '').strip()
    if not token or not owner:
        log.warning('Telegram alerts disabled: BOT_TOKEN or OWNER_ID missing')
        return
    pending = db.execute('SELECT alert_key,text FROM tv_alert_outbox WHERE sent_utc IS NULL ORDER BY created_utc LIMIT 20').fetchall()
    for key, text in pending:
        payload = urllib.parse.urlencode({'chat_id': owner, 'text': text,
                                          'parse_mode': 'HTML',
                                          'disable_web_page_preview': 'true'}).encode()
        request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                         data=payload, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            if not result.get('ok'):
                raise RuntimeError('Telegram API did not confirm success')
        except Exception as exc:
            # Do not log tokens, response bodies, or complete URLs.
            log.warning('Telegram delivery failed (%s); will retry', type(exc).__name__)
            break
        with db:
            db.execute('UPDATE tv_alert_outbox SET sent_utc=? WHERE alert_key=?', (now(), key))
        log.info('Telegram alert delivered: %s', key)
