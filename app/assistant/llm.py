"""The language model behind the assistant.

The assistant speaks one message format, Anthropic's content blocks (text,
tool_use, tool_result), and talks to an `LLM` with a single method. Claude is
the real implementation; `FakeLLM` replays scripted replies so tests run
offline and deterministically.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Awaitable, Callable, Protocol

DEFAULT_MODEL = "claude-sonnet-5-5"
log = logging.getLogger("halcyon.llm")

# US dollars per million tokens, for the cost estimate in the logs. Cache writes are 1.25 times
# the input price; cache reads are as listed for Sonnet 5.5 here and a tenth of input for Haiku 5.5.
PRICES = {"input": 2.00, "cache_write": 2.50, "cache_read": 0.20, "output": 10.00}  # Sonnet 5.5
PRICES_BY_MODEL = {
    "claude-sonnet-5-5": PRICES,
    "claude-haiku-5-5": {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50},
}


class LLM(Protocol):
    async def complete(self, system: list[dict], messages: list[dict], tools: list[dict],
                       label: str = "", text_only: bool = False) -> list[dict]:
        """One model call. Returns the assistant's content blocks: text and/or tool_use.
        `label` (e.g. the conversation id) only tags the usage log line.
        `text_only` keeps the tools defined (earlier tool calls need them) but forbids new ones."""


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

    def __init__(self, api_key: str | None = None, model: str | None = None, effort: str | None = None):
        """`effort` ("low" to "max") is sent when given; otherwise the model's default applies."""
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                                               timeout=60, max_retries=3)
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.effort = effort

    async def complete(self, system, messages, tools, label="", text_only=False):
        options = {}
        if tools:  # cache the long, unchanging prefix (instructions + tool definitions)
            options["tools"] = tools[:-1] + [{**tools[-1], "cache_control": {"type": "ephemeral"}}]
            if text_only:
                options["tool_choice"] = {"type": "none"}
        if self.effort:
            options["output_config"] = {"effort": self.effort}
        resp = await self.client.messages.create(model=self.model, max_tokens=1500, system=system,
                                                 messages=cache_conversation(messages), **options)
        log_usage(label, getattr(resp, "usage", None), PRICES_BY_MODEL.get(self.model, PRICES))
        return [b.model_dump(include={"type", "text", "id", "name", "input"}, exclude_none=True)
                for b in resp.content if b.type in ("text", "tool_use")]


def log_usage(label: str, usage, prices: dict | None = None) -> None:
    """Log the tokens a call used and its estimated cost, so cost is measured rather than guessed.

    `prices` are the model's, per million tokens; without them, Sonnet 5.5's.
    """
    prices = prices or PRICES
    if usage is None:
        return
    tokens = {"input": usage.input_tokens or 0,
              "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
              "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
              "output": usage.output_tokens or 0}
    cost = sum(tokens[k] * prices[k] for k in tokens) / 1_000_000
    log.info("usage %s input=%d cache_write=%d cache_read=%d output=%d cost_usd=%.4f", label or "-",
             tokens["input"], tokens["cache_write"], tokens["cache_read"], tokens["output"], cost)


Script = Callable[[list, list, list], "list[dict] | Awaitable[list[dict]]"]


class FakeLLM:
    """For tests. `script(system, messages, tools)` returns the blocks to reply with.

    Every call is recorded in `calls`, so tests can check what the model was shown.
    """

    def __init__(self, script: Script | None = None):
        self.script = script or (lambda system, messages, tools: [{"type": "text", "text": "Hello from the test model."}])
        self.calls: list[dict] = []

    async def complete(self, system, messages, tools, label="", text_only=False):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages))})
        out = self.script(system, messages, tools)
        return await out if hasattr(out, "__await__") else out
