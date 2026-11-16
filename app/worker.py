"""Turns member messages into replies, in the background (DECISIONS.md, "Conversation and reliability").

* One turn at a time per conversation. Messages arriving during a turn are
  answered together in the next one; a short wait before each turn lets a
  burst ("dinner for 4" / "actually 5" / "fri") be read as one request.
* Every turn ends in a reply within the time limit. If the model is slow or
  down, the reply is a truthful fallback built from what the turn changed.
* The reply, the "handled" mark on the messages it answers, and the
  assistant's transcript are committed in one transaction. A crash before
  that commit means the turn is redone on restart; booking keys are derived
  from the turn, so a redo can't book twice.
"""
from __future__ import annotations

import asyncio
import logging

from .assistant.agent import Assistant, fallback_reply
from .assistant.tools import TurnContext
from .db import Database

log = logging.getLogger("halcyon.worker")


class Worker:
    def __init__(self, db: Database, assistant: Assistant, turn_limit_s: float = 120, burst_wait_s: float = 2):
        self.db, self.assistant = db, assistant
        self.turn_limit_s, self.burst_wait_s = turn_limit_s, burst_wait_s
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule(self, conversation_id: str) -> None:
        """Start answering a conversation unless it is already being answered.

        The running task re-reads unanswered messages before it stops, with no
        await in between, so a message arriving mid-turn is never missed.
        """
        task = self._tasks.get(conversation_id)
        if not task or task.done():
            self._tasks[conversation_id] = asyncio.create_task(self._answer(conversation_id))

    def recover(self) -> int:
        """At startup: answer anything a crash or deploy left unanswered. Returns how many conversations."""
        pending = self.db.conversations_with_unhandled()
        for conversation_id in pending:
            self.schedule(conversation_id)
        return len(pending)

    async def wait_idle(self) -> None:
        """Wait for all running turns to finish (shutdown and tests)."""
        while running := [t for t in self._tasks.values() if not t.done()]:
            await asyncio.gather(*running, return_exceptions=True)

    async def _answer(self, conversation_id: str) -> None:
        while True:
            await asyncio.sleep(self.burst_wait_s)
            pending = self.db.unhandled_member_messages(conversation_id)
            if not pending:
                return
            try:
                await self._turn(conversation_id, pending)
            except Exception:  # e.g. the database; retry rather than leave the member unanswered
                log.exception("turn failed in conversation %s; retrying", conversation_id)
                await asyncio.sleep(5)

    async def _turn(self, conversation_id: str, pending: list[dict]) -> None:
        conversation = self.db.conversation(conversation_id)
        member = self.db.one("SELECT id, name FROM members WHERE id = ?", (conversation["member_id"],))
        ctx = TurnContext(conversation_id=conversation_id, member_id=member["id"], member_name=member["name"],
                          turn_key=str(pending[-1]["seq"]))
        texts = [m["text"] for m in pending]
        transcript = self.db.load_transcript(conversation_id)
        try:
            reply, transcript = await asyncio.wait_for(self.assistant.respond(ctx, texts, transcript),
                                                       timeout=self.turn_limit_s)
        except Exception as e:  # model error, timeout, bug: the member still gets a truthful answer
            log.exception("assistant failed in conversation %s: %r", conversation_id, e)
            reply = fallback_reply(ctx)
            transcript = transcript + [
                {"role": "user", "content": [{"type": "text", "text": "\n".join(texts)}]},
                {"role": "assistant", "content": [{"type": "text", "text": reply}]},
            ]
        with self.db.transaction():
            self.db.insert_assistant_message(conversation_id, reply)
            self.db.mark_handled([m["seq"] for m in pending])
            self.db.save_transcript(conversation_id, transcript)
