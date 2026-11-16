"""The Claude adapter: caching markers, checked without calling the API."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from anthropic.types import TextBlock

from app.assistant.llm import ClaudeLLM, cache_conversation

CONVERSATION = [
    {"role": "user", "content": [{"type": "text", "text": "dinner thursday?"}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "search_restaurants", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
]


def test_cache_marker_goes_on_the_last_block_only():
    marked = cache_conversation(CONVERSATION)
    assert marked[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for m in marked[:-1] for b in m["content"])


def test_callers_transcript_is_not_changed():
    before = json.dumps(CONVERSATION)
    cache_conversation(CONVERSATION)
    assert json.dumps(CONVERSATION) == before  # the stored transcript must stay clean


def test_claude_call_sends_cache_markers(caplog):
    caplog.set_level("INFO", logger="halcyon.llm")
    llm = ClaudeLLM(api_key="test", model="test-model")
    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)
        usage = SimpleNamespace(input_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=4000,
                                output_tokens=50)
        return SimpleNamespace(content=[TextBlock(type="text", text="Hello")], usage=usage)

    llm.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    tools = [{"name": "a", "description": "", "input_schema": {}}, {"name": "b", "description": "", "input_schema": {}}]
    blocks = asyncio.run(llm.complete([{"type": "text", "text": "instructions"}], CONVERSATION, tools))
    assert blocks == [{"type": "text", "text": "Hello"}]
    assert sent["tools"][-1]["cache_control"] and "cache_control" not in sent["tools"][0]
    assert sent["messages"][-1]["content"][-1]["cache_control"]
    assert "cache_control" not in tools[-1]  # the shared tool list isn't changed either
    asyncio.run(llm.complete([], CONVERSATION, tools, label="conv-1"))
    assert "usage conv-1 input=100 cache_write=0 cache_read=4000 output=50 cost_usd=0.0015" in caplog.text
