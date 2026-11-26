"""Turning likes and dislikes notes into tags: validated, stored, retired, re-tagged, run after turns."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi.testclient import TestClient

from app.assistant.llm import FakeLLM
from app.booking.mock.provider import MockBookingProvider
from app.db import Database
from app.main import LONDON, create_app
from app.recommendations.tagging import NoteTagger, parse_tags
from app.recommendations.tags import VOCABULARY_HASH

MEMBER = "member-1"


def run(coro):
    """Run a coroutine to completion in a test."""
    return asyncio.run(coro)


def tagger_replying(db: Database, replies: dict[str, dict | str]) -> tuple[NoteTagger, FakeLLM]:
    """A tagger whose model answers by note text: replies["Loves jazz"] = {"likes": ["jazz"], "dislikes": []}."""
    def script(system, messages, tools):
        note = messages[-1]["content"].split("Note: ", 1)[1]
        reply = replies.get(note, {"likes": [], "dislikes": []})
        return [{"type": "text", "text": reply if isinstance(reply, str) else json.dumps(reply)}]

    llm = FakeLLM(script)
    return NoteTagger(db, llm), llm


def test_parse_keeps_only_known_tags_and_lets_likes_win_a_clash():
    """Invented tags are dropped; a tag in both lists stays a like; no JSON means try again later."""
    assert parse_tags('Here you go: {"likes": ["jazz", "vintage-cars"], "dislikes": ["lively", "jazz"]}') == (
        ["jazz"], ["lively"])
    assert parse_tags("I can't tell.") is None
    assert parse_tags('{"likes": "jazz"}') == ([], [])


def test_likes_and_dislikes_notes_become_tags_and_other_notes_are_left_alone(tmp_path):
    """A mixed note gives both kinds; an allergy note is never sent to the tagger."""
    db = Database(str(tmp_path / "t.db"))
    db.add_note(MEMBER, "likes", "Loves jazz, hates loud clubs", None)
    db.add_note(MEMBER, "allergies", "Peanuts", None)
    tagger, llm = tagger_replying(db, {"Loves jazz, hates loud clubs": {"likes": ["jazz"], "dislikes": ["lively"]}})
    assert run(tagger.tag_pending()) == 1
    assert len(llm.calls) == 1
    assert db.member_tags(MEMBER) == {"likes": ["jazz"], "dislikes": ["lively"]}
    assert run(tagger.tag_pending()) == 0  # nothing left to tag


def test_the_newer_note_wins_when_two_disagree(tmp_path):
    """Liking lively places, then later saying "not loud any more", makes lively a dislike."""
    db = Database(str(tmp_path / "t.db"))
    db.add_note(MEMBER, "likes", "Loves a buzzy room", None)
    time.sleep(1.1)  # created_at has one-second resolution
    db.add_note(MEMBER, "dislikes", "Not loud places any more", None)
    tagger, _ = tagger_replying(db, {"Loves a buzzy room": {"likes": ["lively"]},
                                     "Not loud places any more": {"dislikes": ["lively"]}})
    run(tagger.tag_pending())
    assert db.member_tags(MEMBER) == {"likes": [], "dislikes": ["lively"]}


def test_retiring_a_note_retires_its_tags(tmp_path):
    """Tags live on the note, so a retired note's tags no longer count."""
    db = Database(str(tmp_path / "t.db"))
    note_id = db.add_note(MEMBER, "likes", "Vegetarian food", None)
    tagger, _ = tagger_replying(db, {"Vegetarian food": {"likes": ["vegetarian-led"]}})
    run(tagger.tag_pending())
    db.retire_note(MEMBER, note_id)
    assert db.member_tags(MEMBER) == {"likes": [], "dislikes": []}


def test_a_changed_vocabulary_means_tagging_again(tmp_path):
    """A note tagged under another vocabulary hash is picked up again."""
    db = Database(str(tmp_path / "t.db"))
    note_id = db.add_note(MEMBER, "likes", "Loves jazz", None)
    db.set_note_tags(note_id, ["jazz"], [], "an-older-hash")
    tagger, llm = tagger_replying(db, {"Loves jazz": {"likes": ["jazz"]}})
    assert run(tagger.tag_pending()) == 1
    assert db.one("SELECT tags_vocabulary_hash FROM member_notes WHERE id = ?", (note_id,))["tags_vocabulary_hash"] \
        == VOCABULARY_HASH


def test_an_unreadable_reply_or_a_failure_leaves_the_note_for_next_time(tmp_path):
    """Nothing is stored, and the next pass tries again."""
    db = Database(str(tmp_path / "t.db"))
    db.add_note(MEMBER, "likes", "Loves jazz", None)
    tagger, _ = tagger_replying(db, {"Loves jazz": "Sorry, no idea."})
    assert run(tagger.tag_pending()) == 0

    def failing(system, messages, tools):
        raise RuntimeError("model down")

    assert run(NoteTagger(db, FakeLLM(failing)).tag_pending()) == 0
    assert len(db.notes_needing_tags(VOCABULARY_HASH)) == 1


def test_an_existing_database_gains_the_tag_columns(tmp_path):
    """A volume created before tagging existed keeps working: the columns are added on open."""
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE member_notes (id TEXT PRIMARY KEY, member_id TEXT NOT NULL, category TEXT NOT NULL, "
                 "note TEXT NOT NULL, conversation_id TEXT, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
                 "retired_at TEXT)")
    conn.commit()
    conn.close()
    db = Database(path)
    db.add_note(MEMBER, "likes", "Loves jazz", None)
    assert len(db.notes_needing_tags(VOCABULARY_HASH)) == 1


def test_a_note_saved_in_a_turn_is_tagged_after_the_reply(tmp_path):
    """The assistant saves a like; once the reply is committed, the tagger tags it in the background."""
    def assistant(system, messages, tools):
        last = messages[-1]["content"][-1]
        if last["type"] == "text":
            return [{"type": "tool_use", "id": "n1", "name": "save_member_note",
                     "input": {"category": "likes", "note": "Loves jazz"}}]
        return [{"type": "text", "text": "Noted."}]

    db_path = str(tmp_path / "t.db")
    provider = MockBookingProvider(Database(db_path), tz=LONDON)
    tagging = FakeLLM(lambda system, messages, tools: [{"type": "text", "text": '{"likes": ["jazz"], "dislikes": []}'}])
    app = create_app(api_token="t", db_path=db_path, provider=provider, llm=FakeLLM(assistant),
                     burst_wait_s=0, tagging_llm=tagging)
    member, cid = str(uuid.uuid4()), str(uuid.uuid4())
    with TestClient(app) as c:
        c.post(f"/conversations/{cid}/messages", headers={"Authorization": "Bearer t"},
               json={"message_id": str(uuid.uuid4()), "member": {"id": member, "name": "Jo"}, "text": "I love jazz"})
        deadline = time.time() + 5
        while time.time() < deadline and not Database(db_path).member_tags(member)["likes"]:
            time.sleep(0.05)
    assert Database(db_path).member_tags(member) == {"likes": ["jazz"], "dislikes": []}
