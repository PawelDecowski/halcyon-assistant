"""Halcyon's HTTP contract."""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app.booking.mock.provider import MockBookingProvider
from app.db import Database
from app.main import LONDON, create_app

TOKEN = "test-token"
H = {"Authorization": f"Bearer {TOKEN}"}
MEMBER = {"id": str(uuid.uuid4()), "name": "Jane Doe"}


def client(tmp_path):
    return TestClient(create_app(api_token=TOKEN, db_path=str(tmp_path / "t.db")))


def post(c, cid, text, message_id=None):
    return c.post(f"/conversations/{cid}/messages", headers=H,
                  json={"message_id": message_id or str(uuid.uuid4()), "member": MEMBER, "text": text})


def messages(c, cid, n_assistant=1, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = c.get(f"/conversations/{cid}/messages", headers=H).json()["messages"]
        if sum(m["role"] == "assistant" for m in msgs) >= n_assistant:
            return msgs
        time.sleep(0.05)
    raise AssertionError("no reply")


def test_auth(tmp_path):
    with client(tmp_path) as c:
        cid = str(uuid.uuid4())
        assert c.get(f"/conversations/{cid}/messages").status_code == 401
        assert c.get(f"/conversations/{cid}/messages", headers={"Authorization": "Bearer x"}).status_code == 401
        assert c.get(f"/members/{uuid.uuid4()}/bookings").status_code == 401


def test_post_202_and_message_shape(tmp_path):
    with client(tmp_path) as c:
        cid, mid = str(uuid.uuid4()), str(uuid.uuid4())
        assert post(c, cid, "need dinner for 2 thurs", mid).status_code == 202
        msgs = messages(c, cid)
        assert [m["role"] for m in msgs] == ["member", "assistant"] and msgs[0]["id"] == mid
        for m in msgs:
            assert set(m) == {"id", "role", "text", "created_at"}
            uuid.UUID(m["id"])
            datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%SZ")


def test_repeated_delivery_stored_and_answered_once(tmp_path):
    with client(tmp_path) as c:
        cid, mid = str(uuid.uuid4()), str(uuid.uuid4())
        post(c, cid, "hello", mid)
        post(c, cid, "hello", mid)
        messages(c, cid)
        assert post(c, cid, "hello", mid).json()["duplicate"] is True
        time.sleep(0.2)
        msgs = c.get(f"/conversations/{cid}/messages", headers=H).json()["messages"]
        assert [m["role"] for m in msgs] == ["member", "assistant"]


def test_validation(tmp_path):
    with client(tmp_path) as c:
        assert post(c, str(uuid.uuid4()), "hi", message_id="nope").status_code == 422
        assert post(c, "not-a-uuid", "hi").status_code == 422
        assert c.get(f"/conversations/{uuid.uuid4()}/messages", headers=H).json() == {"messages": []}


def test_restart_answers_what_was_left_unanswered(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    cid = str(uuid.uuid4())
    db.upsert_member(MEMBER["id"], MEMBER["name"])
    db.ensure_conversation(cid, MEMBER["id"])
    db.insert_member_message(str(uuid.uuid4()), cid, "sent just before a crash")
    with client(tmp_path) as c:
        assert messages(c, cid)[-1]["role"] == "assistant"


def test_bookings_endpoint_shows_provider_state(tmp_path):
    db_path = str(tmp_path / "t.db")
    p = MockBookingProvider(Database(db_path), tz=LONDON)
    from tests.test_mock_provider import book_args
    p._book(**book_args(member_id=MEMBER["id"]))
    with TestClient(create_app(api_token=TOKEN, db_path=db_path, provider=p)) as c:
        bookings = c.get(f"/members/{MEMBER['id']}/bookings", headers=H).json()["bookings"]
        assert len(bookings) == 1 and bookings[0]["status"] == "confirmed"
