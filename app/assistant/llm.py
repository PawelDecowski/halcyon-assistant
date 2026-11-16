"""The language model behind the assistant.

The assistant speaks one message format, Anthropic's content blocks (text,
tool_use, tool_result), and talks to an `LLM` with a single method. Claude is
the real implementation; `FakeLLM` replays scripted replies so tests run
offline and deterministically.
"""
from __future__ import annotations

import json
import os
from typing import Awaitable, Callable, Protocol

DEFAULT_MODEL = "claude-sonnet-5-5"


class LLM(Protocol):
    async def complete(self, system: list[dict], messages: list[dict], tools: list[dict]) -> list[dict]:
        """One model call. Returns the assistant's content blocks: text and/or tool_use."""


def cache_conversation(messages: list[dict]) -> list[dict]:
    """Mark the end of the conversation for caching, without changing the caller's list.

    A turn makes several calls (search, book, reply), each resending everything
    so far. With the marker, the next call reads all of that from the cache at a
    tenth of the input price and pays in full only for what is new.
    """
    if not messages or not isinstance(messages[-1]["content"], list) or not messages[-1]["content"]:
        return messages
    last = messages[-1]
    blocks = last["content"][:-1] + [{**last["content"][-1], "cache_control": {"type": "ephemeral"}}]
    return messages[:-1] + [{**last, "content": blocks}]


class ClaudeLLM:
    """Claude through the Anthropic API. Retries rate limits and server errors itself (max_retries)."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                                               timeout=60, max_retries=3)
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)

    async def complete(self, system, messages, tools):
        if tools:  # cache the long, unchanging prefix (instructions + tool definitions)
            tools = tools[:-1] + [{**tools[-1], "cache_control": {"type": "ephemeral"}}]
        resp = await self.client.messages.create(model=self.model, max_tokens=1500, system=system,
                                                 messages=cache_conversation(messages), tools=tools)
        return [b.model_dump(include={"type", "text", "id", "name", "input"}, exclude_none=True)
                for b in resp.content if b.type in ("text", "tool_use")]


Script = Callable[[list, list, list], "list[dict] | Awaitable[list[dict]]"]


class FakeLLM:
    """For tests. `script(system, messages, tools)` returns the blocks to reply with.

    Every call is recorded in `calls`, so tests can check what the model was shown.
    """

    def __init__(self, script: Script | None = None):
        self.script = script or (lambda system, messages, tools: [{"type": "text", "text": "Hello from the test model."}])
        self.calls: list[dict] = []

    async def complete(self, system, messages, tools):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages))})
        out = self.script(system, messages, tools)
        return await out if hasattr(out, "__await__") else out
