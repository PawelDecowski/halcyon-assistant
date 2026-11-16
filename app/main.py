"""Halcyon assistant service: the HTTP contract.

POST /conversations/{conversation_id}/messages  -> 202, stored once per message_id
GET  /conversations/{conversation_id}/messages  -> every message, oldest first

Not part of Halcyon's contract, a review and testing aid behind the same token:
GET  /members/{member_id}/bookings  -> what the booking system actually holds

POST only stores the message; the reply is written in the background by
app/worker.py, which runs the assistant in app/assistant/.
"""
from __future__ import annotations

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .assistant.agent import Assistant
from .assistant.llm import LLM, ClaudeLLM
from .booking.interface import BookingProvider
from .booking.mock.provider import MockBookingProvider
from .db import Database
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("halcyon")

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
               provider: BookingProvider | None = None, llm: LLM | None = None,
               burst_wait_s: float | None = None) -> FastAPI:
    """Build the app. Settings come from env; tests pass them in, plus a provider or model if they need one."""
    api_token = api_token or os.environ.get("API_TOKEN")
    if not api_token:
        raise RuntimeError("API_TOKEN is not set")
    db = Database(db_path or os.environ.get("DB_PATH", "data/halcyon.db"))
    provider = provider or make_mock_provider(db)
    if llm is None and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")  # fail the deploy, not every conversation
    worker = Worker(db, Assistant(db, provider, llm or ClaudeLLM()),
                    turn_limit_s=_env_float("TURN_LIMIT_S", 120),
                    burst_wait_s=_env_float("BURST_WAIT_S", 2) if burst_wait_s is None else burst_wait_s)

    @asynccontextmanager
    async def lifespan(app):
        """On startup, answer anything a crash or deploy left unanswered; on shutdown, finish running turns."""
        recovered = worker.recover()
        if recovered:
            log.info("answering %d conversation(s) left unanswered", recovered)
        yield
        await worker.wait_idle()

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
            worker.schedule(cid)
        return {"status": "accepted", "duplicate": not created}

    @app.get("/conversations/{conversation_id}/messages", dependencies=[Depends(auth)])
    async def get_messages(conversation_id: str):
        """Every message in the conversation, oldest first. Unknown conversation: empty list."""
        return {"messages": db.messages(conv_id(conversation_id))}

    @app.get("/members/{member_id}/bookings", dependencies=[Depends(auth)])
    async def member_bookings(member_id: str):
        """Review aid, not part of the contract: what the booking system holds for a member."""
        return {"bookings": await provider.bookings_for_member(member_id)}

    app.state.worker = worker
    return app
