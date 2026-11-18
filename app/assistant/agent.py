"""One turn of the assistant: call the model, run the tools it asks for, repeat, reply."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from ..booking.interface import BookingProvider
from ..db import Database
from .llm import LLM
from .memory import member_context
from .prompt import INSTRUCTIONS, calendar
from .tools import LONDON, TOOLS, ToolRunner, TurnContext, references_made

log = logging.getLogger("halcyon.assistant")

MAX_TOOL_ROUNDS = 10
NUDGE_EMPTY = "(No reply was written. Write your reply to the member now.)"
NUDGE_FINISH = "(No more actions this turn. Tell the member exactly where things stand.)"


def _text(blocks: list[dict]) -> str:
    return "\n\n".join(b["text"].strip() for b in blocks if b["type"] == "text" and b.get("text", "").strip())


class Assistant:
    def __init__(self, db: Database, provider: BookingProvider, llm: LLM):
        self.db, self.provider, self.llm = db, provider, llm
        self.tools = ToolRunner(db, provider)

    async def instructions(self, ctx: TurnContext) -> list[dict]:
        """Fixed instructions (cached by the API) plus what changes each turn: date and member context."""
        about = await member_context(self.db, self.provider, ctx.member_id, ctx.conversation_id, ctx.booked_here)
        return [{"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": calendar(datetime.now(LONDON)) + "\n\n" + about}]

    async def respond(self, ctx: TurnContext, member_texts: list[str],
                      transcript: list[dict]) -> tuple[str, list[dict]]:
        """Answer the member's new message(s). Returns the reply and the updated transcript.

        The transcript is a copy; the caller stores it together with the reply.
        """
        ctx.booked_here |= references_made(transcript)
        transcript = transcript + [{"role": "user", "content": [{"type": "text", "text": "\n".join(member_texts)}]}]
        system = await self.instructions(ctx)
        # Only the final message is sent. Text written alongside actions is the model's working
        # (e.g. "that area isn't known, retrying"); sending it leaked notes to a member.
        for _ in range(MAX_TOOL_ROUNDS):
            blocks = await self.llm.complete(system, transcript, TOOLS, label=ctx.conversation_id)
            calls = [b for b in blocks if b["type"] == "tool_use"]
            text = _text(blocks)
            if not calls and not text:  # the API rejects empty assistant messages; nudge instead
                transcript += [{"role": "assistant", "content": [{"type": "text", "text": "…"}]},
                               {"role": "user", "content": [{"type": "text", "text": NUDGE_EMPTY}]}]
                continue
            transcript.append({"role": "assistant", "content": blocks})
            if not calls:
                return text, transcript
            results = await asyncio.gather(*(self.tools.run(ctx, c["name"], c.get("input") or {}) for c in calls))
            log.info("conversation %s tools: %s", ctx.conversation_id,
                     [(c["name"], json.dumps(c.get("input"))[:200]) for c in calls])
            transcript.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": c["id"], "content": r} for c, r in zip(calls, results)]})
        # Too many rounds: one last call without tools, so it has to answer.
        transcript.append({"role": "user", "content": [{"type": "text", "text": NUDGE_FINISH}]})
        blocks = await self.llm.complete(system, transcript, TOOLS, label=ctx.conversation_id, text_only=True)
        reply = _text(blocks)
        if not reply:
            raise RuntimeError("the model wrote no reply")
        transcript.append({"role": "assistant", "content": blocks})
        return reply, transcript


def fallback_reply(ctx: TurnContext) -> str:
    """The reply when a turn fails (model down, time limit): truthful about what was already done."""
    if ctx.effects:
        return ("Sorry, I hit a technical problem partway through. Where things stand: " + " ".join(ctx.effects)
                + " Nothing else has been changed. Reply here and I'll carry on from there.")
    return ("Sorry, I hit a technical problem and couldn't finish this. Nothing has been booked or changed. "
            "Could you send that again in a minute? I'll pick it straight up.")
