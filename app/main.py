"""Halcyon assistant service: the HTTP contract.

POST /conversations/{conversation_id}/messages  -> 202, stored once per message_id
GET  /conversations/{conversation_id}/messages  -> every message, oldest first

Step 1 (deployment check): replies with a fixed placeholder so the whole
pipeline (Railway, auth, persistence, Halcyon's polling) can be tested before
any assistant logic exists.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("halcyon")

PLACEHOLDER = "Thanks, I've got your message. (Test deployment: the assistant isn't connected yet.)"


def _uuid(v: str) -> str:
    try:
        return str(uuid.UUID(v))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("must be a UUID")


class Member(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=200)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _uuid(v)


class InboundMessage(BaseModel):
    message_id: str
    member: Member
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("message_id")
    @classmethod
    def _mid(cls, v: str) -> str:
        return _uuid(v)


def create_app(api_token: str | None = None, db_path: str | None = None) -> FastAPI:
    api_token = api_token or os.environ.get("API_TOKEN")
    if not api_token:
        raise RuntimeError("API_TOKEN is not set")
    db = Database(db_path or os.environ.get("DB_PATH", "data/halcyon.db"))
    tasks: dict[str, asyncio.Task] = {}

    async def answer(conversation_id: str) -> None:
        # Placeholder worker; replaced by the assistant in the next step.
        while pending := db.unhandled_member_messages(conversation_id):
            with db.transaction():
                db.insert_assistant_message(conversation_id, PLACEHOLDER)
                db.mark_handled([m["seq"] for m in pending])

    def schedule(conversation_id: str) -> None:
        task = tasks.get(conversation_id)
        if not task or task.done():
            tasks[conversation_id] = asyncio.create_task(answer(conversation_id))

    @asynccontextmanager
    async def lifespan(app):
        for cid in db.conversations_with_unhandled():  # answer anything a restart left behind
            schedule(cid)
        yield

    app = FastAPI(title="Halcyon assistant", lifespan=lifespan)

    def auth(request: Request) -> None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), api_token):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def conv_id(conversation_id: str) -> str:
        try:
            return _uuid(conversation_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="conversation_id must be a UUID")

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.post("/conversations/{conversation_id}/messages", status_code=202, dependencies=[Depends(auth)])
    async def post_message(conversation_id: str, body: InboundMessage):
        cid = conv_id(conversation_id)
        db.upsert_member(body.member.id, body.member.name)
        db.ensure_conversation(cid, body.member.id)
        created = db.insert_member_message(body.message_id, cid, body.text)
        if created:
            schedule(cid)
        return {"status": "accepted", "duplicate": not created}

    @app.get("/conversations/{conversation_id}/messages", dependencies=[Depends(auth)])
    async def get_messages(conversation_id: str):
        return {"messages": db.messages(conv_id(conversation_id))}

    return app
