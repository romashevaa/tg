"""Read-only by default: inspect recent public ideas from enabled authors.

--notify enqueues recent ideas to owner's existing alert outbox, deduplicated by idea ID.
No AI, no exchange calls, no trades.
"""
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
import os
import tv_author_settings as settings
import tv_auto_alerts as alerts
from tv_signal_review import review, review_html


def published_at(entry):
    data = entry.get('data') if isinstance(entry.get('data'), dict) else entry
    for key in ('created_at', 'published_at', 'published', 'date', 'timestamp'):
        v = data.get(key)
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
        if isinstance(v, str):
            if v.isdigit():
                n = int(v)
                return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)
            return alerts.parse_time(v)
    return None


def run(data_dir='/data', hours=1, notify=False, fetch=None, do_review=False):
    data = Path(data_dir)
    with sqlite3.connect(str(data/'signalbot.db'), timeout=15) as db:
        authors = settings.enabled_authors(db)
    if not authors:
        print('No enabled authors. Add with /tvadd username')
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    matches = []
    eventdb = None
    reviewed = []
    if notify:
        eventdb = sqlite3.connect(str(data/'tv_updates_monitor.sqlite'), timeout=15)
        alerts.initialize(eventdb)
    try:
        for author in authors:
            try:
                entries = fetch(author) if fetch is not None else alerts.fetch_latest(author_name=author)
                recent = []
                for entry in entries:
                    record = alerts.unpack(entry, author_name=author)
                    dt = published_at(entry)
                    if record and dt and cutoff <= dt <= datetime.now(timezone.utc) + timedelta(minutes=5):
                        recent.append((dt, author, *record))
                matches.extend(recent)
                if do_review:
                    for dt, a, iid, url, title in recent:
                        source = next((x for x in entries if (r := alerts.unpack(x, author_name=a)) and r[0] == iid), None)
                        if source is not None:
                            reviewed.append((a, iid, title, url, review(source)))
                print(f'@{author}: checked={len(entries)}, published_last_{hours}h={len(recent)}')
            except Exception as exc:
                print(f'@{author}: FAILED ({type(exc).__name__}: {exc})')
        matches.sort(reverse=True)
        for dt, author, iid, url, title in matches:
            print(f'{dt.isoformat()} @{author} {iid}: {title[:140]}\n  {url}')
            if notify:
                text = ('🆕 <b>Нова ідея ' + escape(author) + '</b> (за останню годину)\n'
                        + escape(title[:180]) + '\n<a href="' + escape(url, quote=True) + '">TradingView</a>')
                alerts.enqueue(eventdb, 'idea:' + iid, text)
        if do_review:
            for author, iid, title, url, result in reviewed:
                print(f"REVIEW @{author} {iid}: {result['status']} — {result['reason']}")
                if notify:
                    text = ('🔎 <b>Перевірка ' + escape(author) + '</b>\n' + escape(title[:180]) +
                            review_html(result) + '\n<a href="' + escape(url, quote=True) + '">TradingView</a>')
                    alerts.enqueue(eventdb, 'review:' + iid, text)
        if notify:
            alerts.deliver(eventdb)
            print('Recent posts queued for delivery; duplicates ignored')
        print(f'TOTAL recent ideas: {len(matches)}. This is publication discovery, not an entry/SL/TP validation.')
        return matches
    finally:
        if eventdb:
            eventdb.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=float, default=1)
    ap.add_argument('--notify', action='store_true')
    ap.add_argument('--review', action='store_true', help='Review explicit ENTRY/SL/TP, no AI or trades')
    args = ap.parse_args()
    run(os.environ.get('SIGNALBOT_DATA_DIR', '/data'), args.hours, args.notify, do_review=args.review)
