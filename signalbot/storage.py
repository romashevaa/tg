from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import Channel, ChannelProfile, ParsedSignal, TgMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    username TEXT,
    profile TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    edit_date TEXT,
    text TEXT NOT NULL,
    fwd_date TEXT,
    fwd_from TEXT,
    reply_to INTEGER,
    has_photo INTEGER NOT NULL,
    links TEXT NOT NULL,
    PRIMARY KEY (channel_id, message_id)
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    entry REAL,
    parsed TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasons TEXT NOT NULL,
    plan TEXT,
    status TEXT NOT NULL,
    exchange_response TEXT,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_signals_msg ON signals (channel_id, message_id);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals (symbol, created_at);
"""

# Statuses that mean an order was sent (or would have been, in shadow mode).
ACTIVE = ("executed", "shadow")


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def now() -> datetime:
    return datetime.now(timezone.utc)


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # Databases created by earlier versions lack newer columns.
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(channels)")}
        if "evaluation" not in columns:
            self.db.execute("ALTER TABLE channels ADD COLUMN evaluation TEXT")
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    # channels

    def upsert_channel(self, ch: Channel) -> None:
        self.db.execute(
            """INSERT INTO channels (channel_id, title, username, profile, enabled, added_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 title=excluded.title, username=excluded.username,
                 profile=COALESCE(excluded.profile, channels.profile), enabled=excluded.enabled""",
            (
                ch.channel_id,
                ch.title,
                ch.username,
                ch.profile.model_dump_json() if ch.profile else None,
                int(ch.enabled),
                _iso(now()),
            ),
        )
        self.db.commit()

    def set_evaluation(self, channel_id: int, evaluation: dict) -> None:
        self.db.execute("UPDATE channels SET evaluation=? WHERE channel_id=?",
                        (json.dumps(evaluation), channel_id))
        self.db.commit()

    def set_enabled(self, channel_id: int, enabled: bool) -> None:
        self.db.execute("UPDATE channels SET enabled=? WHERE channel_id=?", (int(enabled), channel_id))
        self.db.commit()

    def channels(self, only_enabled: bool = False) -> list[Channel]:
        sql = "SELECT * FROM channels" + (" WHERE enabled=1" if only_enabled else "")
        return [self._channel(r) for r in self.db.execute(sql)]

    def channel(self, channel_id: int) -> Channel | None:
        row = self.db.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        return self._channel(row) if row else None

    @staticmethod
    def _channel(row: sqlite3.Row) -> Channel:
        profile = ChannelProfile.model_validate_json(row["profile"]) if row["profile"] else None
        evaluation = json.loads(row["evaluation"]) if row["evaluation"] else None
        return Channel(row["channel_id"], row["title"], row["username"], profile, bool(row["enabled"]), evaluation)

    # messages

    def save_message(self, m: TgMessage) -> bool:
        """Store a message. Returns False when the same text is already stored (no-op edit)."""
        row = self.db.execute(
            "SELECT text FROM messages WHERE channel_id=? AND message_id=?",
            (m.channel_id, m.message_id),
        ).fetchone()
        if row and row["text"] == m.text:
            return False
        self.db.execute(
            """INSERT OR REPLACE INTO messages
               (channel_id, message_id, date, edit_date, text, fwd_date, fwd_from, reply_to, has_photo, links)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                m.channel_id,
                m.message_id,
                _iso(m.date),
                _iso(m.edit_date),
                m.text,
                _iso(m.fwd_date),
                m.fwd_from,
                m.reply_to,
                int(m.has_photo),
                json.dumps(m.links),
            ),
        )
        self.db.commit()
        return True

    def recent_messages(self, channel_id: int, before_id: int | None, limit: int) -> list[TgMessage]:
        sql = "SELECT * FROM messages WHERE channel_id=?"
        args: list = [channel_id]
        if before_id is not None:
            sql += " AND message_id<?"
            args.append(before_id)
        sql += " ORDER BY message_id DESC LIMIT ?"
        args.append(limit)
        rows = self.db.execute(sql, args).fetchall()
        return [self._message(r) for r in reversed(rows)]

    @staticmethod
    def _message(r: sqlite3.Row) -> TgMessage:
        return TgMessage(
            channel_id=r["channel_id"],
            message_id=r["message_id"],
            date=_dt(r["date"]),
            text=r["text"],
            edit_date=_dt(r["edit_date"]),
            fwd_date=_dt(r["fwd_date"]),
            fwd_from=r["fwd_from"],
            reply_to=r["reply_to"],
            has_photo=bool(r["has_photo"]),
            links=json.loads(r["links"]),
        )

    # signals

    def add_signal(
        self,
        msg: TgMessage,
        parsed: ParsedSignal,
        symbol: str | None,
        entry: float | None,
        decision: str,
        reasons: list[str],
        plan: dict | None,
        status: str,
        exchange_response: dict | None = None,
        latency_ms: int | None = None,
    ) -> int:
        cur = self.db.execute(
            """INSERT INTO signals
               (channel_id, message_id, created_at, kind, symbol, side, entry, parsed, decision,
                reasons, plan, status, exchange_response, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.channel_id,
                msg.message_id,
                _iso(now()),
                parsed.kind,
                symbol,
                parsed.side,
                entry,
                parsed.model_dump_json(),
                decision,
                json.dumps(reasons, ensure_ascii=False),
                json.dumps(plan) if plan else None,
                status,
                json.dumps(exchange_response, default=str) if exchange_response else None,
                latency_ms,
            ),
        )
        self.db.commit()
        return cur.lastrowid

    def set_signal_status(self, signal_id: int, status: str, exchange_response: dict | None = None) -> None:
        self.db.execute(
            "UPDATE signals SET status=?, exchange_response=COALESCE(?, exchange_response) WHERE id=?",
            (status, json.dumps(exchange_response, default=str) if exchange_response else None, signal_id),
        )
        self.db.commit()

    def signal(self, signal_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()

    def message_was_traded(self, channel_id: int, message_id: int) -> bool:
        row = self.db.execute(
            f"SELECT 1 FROM signals WHERE channel_id=? AND message_id=? AND status IN {ACTIVE}",
            (channel_id, message_id),
        ).fetchone()
        return row is not None

    def find_duplicate(
        self, symbol: str, side: str, entry: float, hours: float, tolerance_pct: float
    ) -> sqlite3.Row | None:
        since = _iso(now() - timedelta(hours=hours))
        rows = self.db.execute(
            f"""SELECT * FROM signals WHERE symbol=? AND side=? AND created_at>=?
                AND status IN {ACTIVE} ORDER BY id DESC""",
            (symbol, side, since),
        ).fetchall()
        for r in rows:
            if r["entry"] and abs(r["entry"] - entry) / entry * 100 <= tolerance_pct:
                return r
        return None

    def open_signals(self, channel_id: int | None = None, days: float = 14) -> list[sqlite3.Row]:
        since = _iso(now() - timedelta(days=days))
        sql = f"SELECT * FROM signals WHERE created_at>=? AND status IN {ACTIVE}"
        args: list = [since]
        if channel_id is not None:
            sql += " AND channel_id=?"
            args.append(channel_id)
        return self.db.execute(sql + " ORDER BY id", args).fetchall()

    def recent_signals(self, limit: int = 10, channel_id: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM signals WHERE kind IN ('new_signal', 'update')"
        args: list = []
        if channel_id is not None:
            sql += " AND channel_id=?"
            args.append(channel_id)
        return self.db.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()

    def channel_stats(self, days: float = 30) -> dict[int, dict]:
        """Per channel: messages read by the model, and what happened to the calls found."""
        since = _iso(now() - timedelta(days=days))
        rows = self.db.execute(
            """SELECT channel_id, kind, status, COUNT(*) AS n, AVG(latency_ms) AS latency
               FROM signals WHERE created_at>=? GROUP BY channel_id, kind, status""",
            (since,),
        ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            st = out.setdefault(r["channel_id"], {"parsed": 0, "signals": 0, "opened": 0, "awaiting": 0,
                                                  "rejected": 0, "errors": 0, "updates": 0, "latency": []})
            st["parsed"] += r["n"]
            if r["latency"]:
                st["latency"].append((r["latency"], r["n"]))
            if r["kind"] == "update":
                st["updates"] += r["n"]
            if r["kind"] != "new_signal":
                continue
            st["signals"] += r["n"]
            if r["status"] in ("executed", "shadow", "closed", "cancelled"):
                st["opened"] += r["n"]
            elif r["status"] in ("awaiting", "approving"):
                st["awaiting"] += r["n"]
            elif r["status"] == "error":
                st["errors"] += r["n"]
            else:
                st["rejected"] += r["n"]
        for st in out.values():
            total = sum(n for _, n in st["latency"])
            st["latency"] = sum(v * n for v, n in st["latency"]) / total if total else 0
        return out
