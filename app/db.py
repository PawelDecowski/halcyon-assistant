"""SQLite storage. One file and one connection: the service runs as a single
process and every query here takes well under a millisecond, so a plain
lock-guarded connection is simpler and safer than a pool."""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conv_member ON conversations(member_id, created_at);
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('member', 'assistant')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    handled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conversation_id, seq);
"""


def now_iso() -> str:
    """ISO 8601 UTC with a Z suffix, matching the contract's example."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    # -- low level -----------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def transaction(self):
        return _Tx(self)

    # -- members & conversations ------------------------------------------------
    def upsert_member(self, member_id: str, name: str) -> None:
        ts = now_iso()
        self.execute(
            "INSERT INTO members (id, name, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
            (member_id, name, ts, ts),
        )

    def ensure_conversation(self, conversation_id: str, member_id: str) -> None:
        self.execute(
            "INSERT OR IGNORE INTO conversations (id, member_id, created_at) VALUES (?, ?, ?)",
            (conversation_id, member_id, now_iso()),
        )

    def conversation(self, conversation_id: str) -> dict | None:
        return self.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))

    # -- messages ---------------------------------------------------------------
    def insert_member_message(self, message_id: str, conversation_id: str, text: str) -> bool:
        """Returns False if this message_id was already stored (a redelivery)."""
        cur = self.execute(
            "INSERT OR IGNORE INTO messages (id, conversation_id, role, text, created_at) "
            "VALUES (?, ?, 'member', ?, ?)",
            (message_id, conversation_id, text, now_iso()),
        )
        return cur.rowcount == 1

    def insert_assistant_message(self, conversation_id: str, text: str) -> dict:
        msg = {"id": new_id(), "role": "assistant", "text": text, "created_at": now_iso()}
        self.execute(
            "INSERT INTO messages (id, conversation_id, role, text, created_at, handled) "
            "VALUES (?, ?, 'assistant', ?, ?, 1)",
            (msg["id"], conversation_id, text, msg["created_at"]),
        )
        return msg

    def messages(self, conversation_id: str) -> list[dict]:
        return self.query(
            "SELECT id, role, text, created_at FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        )

    def unhandled_member_messages(self, conversation_id: str) -> list[dict]:
        return self.query(
            "SELECT seq, id, text FROM messages WHERE conversation_id = ? AND role = 'member' "
            "AND handled = 0 ORDER BY seq",
            (conversation_id,),
        )

    def mark_handled(self, seqs: list[int]) -> None:
        if seqs:
            marks = ",".join("?" * len(seqs))
            self.execute(f"UPDATE messages SET handled = 1 WHERE seq IN ({marks})", tuple(seqs))

    def conversations_with_unhandled(self) -> list[str]:
        rows = self.query(
            "SELECT DISTINCT conversation_id FROM messages WHERE role = 'member' AND handled = 0"
        )
        return [r["conversation_id"] for r in rows]


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()
        return False
