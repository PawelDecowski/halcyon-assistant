"""Halcyon assistant service: the HTTP contract.

POST /conversations/{conversation_id}/messages  -> 202, stored once per message_id
GET  /conversations/{conversation_id}/messages  -> every message, oldest first

Not part of Halcyon's contract, a review and testing aid behind the same token:
GET  /members/{member_id}/bookings  -> what the booking system actually holds

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
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .booking.interface import BookingProvider
from .booking.mock.provider import MockBookingProvider
from .db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("halcyon")

PLACEHOLDER = "Thanks, I've got your message. (Test deployment: the assistant isn't connected yet.)"


def _uuid(v: str) -> str:
    """Normalise a UUID string, or raise ValueError so pydantic reports a 422."""
    try:
        return str(uuid.UUID(v))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("must be a UUID")


class Member(BaseModel):
    """The member block of an inbound message."""

    id: str
    name: str = Field(min_length=1, max_length=200)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _uuid(v)


class InboundMessage(BaseModel):
    """The body Halcyon POSTs for each member message."""

    message_id: str
    member: Member
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("message_id")
    @classmethod
    def _mid(cls, v: str) -> str:
        return _uuid(v)


LONDON = ZoneInfo("Europe/London")


def _env_float(name: str, default: float) -> float:
    """Read a number from the environment, with a default."""
    return float(os.environ.get(name, default))


def make_mock_provider(db: Database) -> MockBookingProvider:
    """Failure rates and latency come from env, so tests and practice runs can tune them."""
    return MockBookingProvider(
        db, tz=LONDON, seed=os.environ.get("MOCK_SEED", "halcyon"),
        transient_error_rate=_env_float("MOCK_TRANSIENT_ERROR_RATE", 0.05),
        slot_race_rate=_env_float("MOCK_SLOT_RACE_RATE", 0.05),
        latency_s=_env_float("MOCK_LATENCY_S", 0.3),
    )


def create_app(api_token: str | None = None, db_path: str | None = None,
               provider: BookingProvider | None = None) -> FastAPI:
    """Build the app. Settings come from env; tests pass them in, plus a provider if they need one."""
    api_token = api_token or os.environ.get("API_TOKEN")
    if not api_token:
        raise RuntimeError("API_TOKEN is not set")
    db = Database(db_path or os.environ.get("DB_PATH", "data/halcyon.db"))
    provider = provider or make_mock_provider(db)
    tasks: dict[str, asyncio.Task] = {}

    async def answer(conversation_id: str) -> None:
        """Background task: answer every unanswered message in the conversation, then stop."""
        while pending := db.unhandled_member_messages(conversation_id):
            with db.transaction():
                db.insert_assistant_message(conversation_id, PLACEHOLDER)
                db.mark_handled([m["seq"] for m in pending])

    def schedule(conversation_id: str) -> None:
        """Start answering a conversation unless a task for it is already running.

        A running task re-reads unanswered messages before it stops, so a
        message arriving mid-turn is never missed.
        """
        task = tasks.get(conversation_id)
        if not task or task.done():
            tasks[conversation_id] = asyncio.create_task(answer(conversation_id))

    @asynccontextmanager
    async def lifespan(app):
        """On startup, answer anything a crash or deploy left unanswered."""
        for cid in db.conversations_with_unhandled():  # answer anything a restart left behind
            schedule(cid)
        yield

    app = FastAPI(title="Halcyon assistant", lifespan=lifespan)

    def auth(request: Request) -> None:
        """Require `Authorization: Bearer <API_TOKEN>`, compared in constant time."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), api_token):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def conv_id(conversation_id: str) -> str:
        """Validate the conversation id from the URL; 422 if it isn't a UUID."""
        try:
            return _uuid(conversation_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="conversation_id must be a UUID")

    @app.get("/healthz")
    async def health():
        """Liveness check for Railway."""
        return {"ok": True}

    @app.post("/conversations/{conversation_id}/messages", status_code=202, dependencies=[Depends(auth)])
    async def post_message(conversation_id: str, body: InboundMessage):
        """Store a member message and return 202 at once; the reply is written in the background.
        A redelivered message_id is accepted again but stored and answered only once."""
        cid = conv_id(conversation_id)
        db.upsert_member(body.member.id, body.member.name)
        db.ensure_conversation(cid, body.member.id)
        created = db.insert_member_message(body.message_id, cid, body.text)
        if created:
            schedule(cid)
        return {"status": "accepted", "duplicate": not created}

    @app.get("/conversations/{conversation_id}/messages", dependencies=[Depends(auth)])
    async def get_messages(conversation_id: str):
        """Every message in the conversation, oldest first. Unknown conversation: empty list."""
        return {"messages": db.messages(conv_id(conversation_id))}

    @app.get("/members/{member_id}/bookings", dependencies=[Depends(auth)])
    async def member_bookings(member_id: str):
        """Review aid, not part of the contract: what the booking system holds for a member."""
        return {"bookings": await provider.bookings_for_member(member_id)}

    return app
