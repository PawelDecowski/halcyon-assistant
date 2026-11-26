"""Turning members' likes and dislikes notes into tags from the vocabulary.

A small model (Claude Haiku 5.5, low effort) reads one note and picks the tags
it supports, as likes or dislikes; the code keeps only tags in the vocabulary,
so nothing invented gets through. The note text stays as it was: the
assistant still reads it in conversation, recommendations read the tags.

When it runs: after each turn, for that member, in the background, so the
reply never waits; and at startup, for every note untagged or tagged under an
older vocabulary (see VOCABULARY_HASH). A failed call leaves the note
untagged, and the next pass tries again.

    tagger = NoteTagger(db, ClaudeLLM(model="claude-haiku-5-5", effort="low"))
    tagger.schedule(member_id)      # after a turn
    tagger.schedule()               # at startup: every member
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from app.assistant.llm import LLM
from app.db import Database
from app.recommendations.tags import QUALITY_GROUPS, TAG_GROUPS, TAGS, VOCABULARY_HASH

log = logging.getLogger("halcyon.tagging")

TAGGING_MODEL = "claude-haiku-5-5"


def _vocabulary_text() -> str:
    """The tag list as the model sees it: one "tag: definition" line per tag, under its group."""
    lines = []
    for group, tags in TAG_GROUPS.items():
        kind = "quality" if group in QUALITY_GROUPS else "interest"
        lines.append(f"\n{group} ({kind}):")
        lines += [f"- {tag}: {definition}" for tag, definition in tags.items()]
    return "\n".join(lines)


SYSTEM = f"""You turn one note about a member of a lifestyle membership into tags.

The note records something the member said about what they like or don't like.
Choose only tags from the list below that the note clearly supports. Put a tag
under "likes" if the member wants it and under "dislikes" if they want to avoid
it; a note can give both ("loves jazz, hates loud clubs" gives likes jazz,
dislikes lively). Do not guess beyond what the note says: one Italian dinner
does not mean they like Italian food. If no tag fits, return empty lists.

Reply with only a JSON object, for example {{"likes": ["jazz"], "dislikes": ["lively"]}}.

Tags:{_vocabulary_text()}"""


def parse_tags(text: str) -> tuple[list[str], list[str]] | None:
    """The likes and dislikes in the model's reply, keeping only tags in the vocabulary.

    Returns None if the reply has no readable JSON object, so the note is tried again later.
    parse_tags('{"likes": ["jazz", "loud"], "dislikes": []}') -> (["jazz"], [])
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    def known(key: str) -> list[str]:
        values = data.get(key) or []
        return list(dict.fromkeys(v for v in values if isinstance(v, str) and v in TAGS)) if isinstance(values, list) else []

    likes, dislikes = known("likes"), known("dislikes")
    return likes, [tag for tag in dislikes if tag not in likes]


class NoteTagger:
    """Tags members' likes and dislikes notes in the background, one note per model call."""

    def __init__(self, db: Database, llm: LLM):
        self.db, self.llm = db, llm
        self._tasks: set[asyncio.Task] = set()

    def schedule(self, member_id: str | None = None) -> None:
        """Tag a member's notes that need it, or everyone's if no member is given, without waiting."""
        task = asyncio.create_task(self.tag_pending(member_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        """Wait for running tagging passes to finish (shutdown and tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def tag_pending(self, member_id: str | None = None) -> int:
        """Tag every note that needs it; returns how many were tagged. A failure is logged and left for later."""
        tagged = 0
        for note in self.db.notes_needing_tags(VOCABULARY_HASH, member_id):
            try:
                if await self.tag_note(note):
                    tagged += 1
            except Exception:
                log.exception("tagging note %s failed; it will be tried again", note["id"])
        return tagged

    async def tag_note(self, note: dict) -> bool:
        """Ask the model for one note's tags and store them. Returns False if the reply was unreadable."""
        blocks = await self.llm.complete(
            system=[{"type": "text", "text": SYSTEM}],
            messages=[{"role": "user", "content": f"Category: {note['category']}\nNote: {note['note']}"}],
            tools=[], label=f"tagging {note['member_id']}")
        reply = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        parsed = parse_tags(reply)
        if parsed is None:
            log.warning("tagging note %s: unreadable reply %r", note["id"], reply[:200])
            return False
        self.db.set_note_tags(note["id"], parsed[0], parsed[1], VOCABULARY_HASH)
        return True
