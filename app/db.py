"""SQLite storage. One file and one connection: the service runs as a single
process and every query here takes well under a millisecond, so a plain
lock-guarded connection is simpler and safer than a pool."""
from __future__ import annotations

import json
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
-- What the assistant has learned about a member. Corrections retire a note
-- (active = 0) rather than delete it, so the history is kept.
CREATE TABLE IF NOT EXISTS member_notes (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL,
    category TEXT NOT NULL,
    note TEXT NOT NULL,
    conversation_id TEXT,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    retired_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_notes_member ON member_notes(member_id, active);
-- The assistant's working record of a conversation, including its searches
-- and tool results. Never shown to the member.
CREATE TABLE IF NOT EXISTS transcripts (
    conversation_id TEXT PRIMARY KEY,
    transcript TEXT NOT NULL
);
"""


def now_iso() -> str:
    """ISO 8601 UTC with a Z suffix, matching the contract's example."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    """A random UUID4 as a string, for message ids."""
    return str(uuid.uuid4())


class Database:
    """The app's storage: members, conversations, messages, notes and transcripts.

    Every method takes the lock, so it is safe to call from request handlers
    and background tasks alike. Use transaction() to group writes atomically.
    """

    def __init__(self, path: str):
        """Open (or create) the database at `path`, or ":memory:" for tests, and apply the schema."""
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
    def executescript(self, sql: str) -> None:
        """Run several SQL statements at once; used by modules that own their own tables."""
        with self._lock:
            self._conn.executescript(sql)

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        """Run one statement; returns the cursor so callers can read rowcount."""
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        """Run a SELECT and return every row as a dict."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        """Run a SELECT and return the first row as a dict, or None."""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def transaction(self):
        """Context manager: everything inside commits together, or rolls back on an exception.

        with db.transaction():
            db.insert_assistant_message(...)
            db.mark_handled(...)
        """
        return _Tx(self)

    # -- members & conversations ------------------------------------------------
    def upsert_member(self, member_id: str, name: str) -> None:
        """Create the member on first contact; afterwards keep their name up to date."""
        ts = now_iso()
        self.execute(
            "INSERT INTO members (id, name, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
            (member_id, name, ts, ts),
        )

    def ensure_conversation(self, conversation_id: str, member_id: str) -> None:
        """Create the conversation on its first message; a no-op afterwards."""
        self.execute(
            "INSERT OR IGNORE INTO conversations (id, member_id, created_at) VALUES (?, ?, ?)",
            (conversation_id, member_id, now_iso()),
        )

    def conversation(self, conversation_id: str) -> dict | None:
        """The conversation row (id, member_id, created_at), or None if unknown."""
        return self.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))

    # -- messages ---------------------------------------------------------------
    def insert_member_message(self, message_id: str, conversation_id: str, text: str) -> bool:
        """Store a member message once. Returns False if this message_id was already stored (a redelivery)."""
        cur = self.execute(
            "INSERT OR IGNORE INTO messages (id, conversation_id, role, text, created_at) "
            "VALUES (?, ?, 'member', ?, ?)",
            (message_id, conversation_id, text, now_iso()),
        )
        return cur.rowcount == 1

    def insert_assistant_message(self, conversation_id: str, text: str) -> dict:
        """Store a reply and return it in the contract's message shape."""
        msg = {"id": new_id(), "role": "assistant", "text": text, "created_at": now_iso()}
        self.execute(
            "INSERT INTO messages (id, conversation_id, role, text, created_at, handled) "
            "VALUES (?, ?, 'assistant', ?, ?, 1)",
            (msg["id"], conversation_id, text, msg["created_at"]),
        )
        return msg

    def messages(self, conversation_id: str) -> list[dict]:
        """Every message in the conversation, oldest first, in the contract's shape."""
        return self.query(
            "SELECT id, role, text, created_at FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        )

    def unhandled_member_messages(self, conversation_id: str) -> list[dict]:
        """Member messages not yet answered, oldest first. Includes seq, needed by mark_handled()."""
        return self.query(
            "SELECT seq, id, text FROM messages WHERE conversation_id = ? AND role = 'member' "
            "AND handled = 0 ORDER BY seq",
            (conversation_id,),
        )

    def mark_handled(self, seqs: list[int]) -> None:
        """Mark member messages as answered. Do it in the same transaction that stores the reply."""
        if seqs:
            marks = ",".join("?" * len(seqs))
            self.execute(f"UPDATE messages SET handled = 1 WHERE seq IN ({marks})", tuple(seqs))

    def conversations_with_unhandled(self) -> list[str]:
        """Ids of conversations with an unanswered member message, e.g. left behind by a crash."""
        rows = self.query(
            "SELECT DISTINCT conversation_id FROM messages WHERE role = 'member' AND handled = 0"
        )
        return [r["conversation_id"] for r in rows]


    # -- the assistant's working record --------------------------------------------
    def load_transcript(self, conversation_id: str) -> list[dict]:
        """The assistant's full record of a conversation (messages plus tool calls), or [] if new."""
        row = self.one("SELECT transcript FROM transcripts WHERE conversation_id = ?", (conversation_id,))
        return json.loads(row["transcript"]) if row else []

    def save_transcript(self, conversation_id: str, transcript: list[dict]) -> None:
        """Replace the stored record. Called in the same transaction as the reply it led to."""
        self.execute(
            "INSERT INTO transcripts (conversation_id, transcript) VALUES (?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET transcript = excluded.transcript",
            (conversation_id, json.dumps(transcript)),
        )

    # -- member notes ------------------------------------------------------------------
    def active_notes(self, member_id: str) -> list[dict]:
        """Notes currently believed true about the member, by category then age."""
        return self.query("SELECT id, category, note, created_at FROM member_notes WHERE member_id = ? "
                          "AND active = 1 ORDER BY category, created_at", (member_id,))

    def add_note(self, member_id: str, category: str, note: str, conversation_id: str | None) -> str:
        """Save a note and return its id."""
        note_id = new_id()
        self.execute("INSERT INTO member_notes (id, member_id, category, note, conversation_id, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (note_id, member_id, category, note, conversation_id, now_iso()))
        return note_id

    def retire_note(self, member_id: str, note_id: str) -> bool:
        """Retire an active note of this member. Returns False if there was no such note."""
        cur = self.execute("UPDATE member_notes SET active = 0, retired_at = ? WHERE id = ? AND member_id = ? "
                           "AND active = 1", (now_iso(), note_id, member_id))
        return cur.rowcount == 1

    def recent_conversations(self, member_id: str, exclude_id: str, limit: int) -> list[dict]:
        """The member's latest other conversations, newest first."""
        return self.query("SELECT id, created_at FROM conversations WHERE member_id = ? AND id != ? "
                          "ORDER BY created_at DESC LIMIT ?", (member_id, exclude_id, limit))

    def last_messages(self, conversation_id: str, limit: int) -> list[dict]:
        """The last `limit` messages of a conversation, oldest first."""
        rows = self.query("SELECT role, text FROM messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?",
                          (conversation_id, limit))
        return rows[::-1]


class _Tx:
    """The context manager returned by Database.transaction(). Holds the lock for the whole transaction."""

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
