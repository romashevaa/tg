"""Conservative, deterministic public TradingView terminal-event classification.

This does not represent exchange positions. Manual entry, paper and real positions
must not be inferred from TradingView alone.
"""
import re

TERMINAL_STATUS = re.compile(r"^trade\s+closed\b", re.I)
STOP_FINISHED = re.compile(r"\b(?:stop[ -]?loss|\bsl)\s+(?:was\s+)?(?:hit|reached|triggered)\b", re.I)
TARGET_FINISHED = re.compile(r"\b(?:reached|hit|smashed)\s+all\s+(?:the\s+)?targets\b|\ball\s+targets\s+(?:reached|hit|smashed)\b", re.I)
EXPLICIT_CLOSE = re.compile(r"\bclosed\s+(?:at\s+)?(?:b/?e|breakeven|break-even)\b|\b(?:trade|position)\s+(?:is\s+)?closed\b", re.I)


def terminal_reason(status: str, body: str) -> str | None:
    status = (status or "").strip()
    body = (body or "").strip()
    if TERMINAL_STATUS.search(status):
        return status[:120]
    # Text heuristics only apply to actual update statuses; not original idea descriptions.
    if not status.lower().startswith("trade active"):
        return None
    if STOP_FINISHED.search(body):
        return "stop-loss reported"
    if TARGET_FINISHED.search(body):
        return "all targets reported"
    if EXPLICIT_CLOSE.search(body):
        return "closed by author"
    return None


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS tv_closed_ideas (idea_id TEXT PRIMARY KEY, reason TEXT NOT NULL, closed_utc TEXT NOT NULL)")
    db.commit()


def record_closed(db, idea_id, events):
    """Mark an author-closed idea after its final event has been persisted and alerted."""
    for row in sorted(events, key=lambda x: x.get("time") or "", reverse=True):
        reason = terminal_reason(row.get("status", ""), row.get("text", ""))
        if reason:
            with db:
                db.execute('INSERT OR IGNORE INTO tv_closed_ideas VALUES (?, ?, ?)',
                           (idea_id, reason, row.get("time") or ""))
            return reason
    return None


def initialize_existing(db):
    """Seed from events already stored; does not send historical alerts."""
    from datetime import datetime, timezone
    rows = db.execute('SELECT idea, time_utc, status, body FROM tv_events WHERE status != ""').fetchall()
    inserted = 0
    # A terminal update is final; do not treat a later independent initial idea as a continuation.
    for idea, date, status, body in rows:
        reason = terminal_reason(status, body)
        if reason:
            with db:
                cur = db.execute('INSERT OR IGNORE INTO tv_closed_ideas VALUES (?,?,?)',
                                 (idea, reason, date or datetime.now(timezone.utc).isoformat()))
            inserted += int(cur.rowcount > 0)
    return inserted


def closed_ids(db):
    return {row[0] for row in db.execute('SELECT idea_id FROM tv_closed_ideas')}
