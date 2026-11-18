"""The assistant's turn handling, tools and memory, with a scripted model (no API calls)."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi.testclient import TestClient

from app.assistant.llm import FakeLLM
from app.assistant.memory import booking_patterns
from app.booking.mock.provider import MockBookingProvider
from app.db import Database
from app.main import LONDON, create_app
from tests.test_mock_provider import day

TOKEN = "test-token"
H = {"Authorization": f"Bearer {TOKEN}"}


def app_with(tmp_path, llm, burst_wait_s=0.0):
    db_path = str(tmp_path / "t.db")
    provider = MockBookingProvider(Database(db_path), tz=LONDON)  # no latency, no injected failures
    return TestClient(create_app(api_token=TOKEN, db_path=db_path, provider=provider, llm=llm,
                                 burst_wait_s=burst_wait_s)), db_path


def post(c, cid, member_id, text, name="Jane Doe"):
    r = c.post(f"/conversations/{cid}/messages", headers=H,
               json={"message_id": str(uuid.uuid4()), "member": {"id": member_id, "name": name}, "text": text})
    assert r.status_code == 202


def replies(c, cid, n=1, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = c.get(f"/conversations/{cid}/messages", headers=H).json()["messages"]
        out = [m["text"] for m in msgs if m["role"] == "assistant"]
        if len(out) >= n:
            return out
        time.sleep(0.05)
    raise AssertionError("no reply in time")


def last_tool_result(messages):
    block = messages[-1]["content"][-1]
    return json.loads(block["content"]) if block["type"] == "tool_result" else None


def tool_then_report(name, args):
    """A model that calls one tool, then replies with what it got back."""
    def script(system, messages, tools):
        result = last_tool_result(messages)
        if result is None:
            return [{"type": "tool_use", "id": "t1", "name": name, "input": args}]
        return [{"type": "text", "text": json.dumps(result)}]
    return script


BOOK_CASA = {"venue_id": "v-casa-lucia", "date": day(0), "time": "19:30", "party_size": 2}


def test_books_through_the_whole_service(tmp_path):
    c, _ = app_with(tmp_path, FakeLLM(tool_then_report("book_table", BOOK_CASA)))
    with c:
        member, cid = str(uuid.uuid4()), str(uuid.uuid4())
        post(c, cid, member, "casa lucia monday 7.30 for 2 please")
        result = json.loads(replies(c, cid)[0])
        assert result["status"] == "confirmed" and result["guest_name"] == "Jane Doe"
        assert c.get(f"/members/{member}/bookings", headers=H).json()["bookings"][0]["reference"] == result["reference"]


def test_redone_turn_after_crash_does_not_double_book(tmp_path):
    c, db_path = app_with(tmp_path, FakeLLM(tool_then_report("book_table", BOOK_CASA)))
    member, cid = str(uuid.uuid4()), str(uuid.uuid4())
    with c:
        post(c, cid, member, "book it")
        replies(c, cid)
    db = Database(db_path)  # simulate a crash after booking, before the reply was stored
    db.execute("DELETE FROM messages WHERE role = 'assistant'")
    db.execute("UPDATE messages SET handled = 0")
    db.execute("DELETE FROM transcripts")
    c, _ = app_with(tmp_path, FakeLLM(tool_then_report("book_table", BOOK_CASA)))
    with c:  # the restart redoes the turn
        assert "already_booked" in json.loads(replies(c, cid)[0])
        assert len(c.get(f"/members/{member}/bookings", headers=H).json()["bookings"]) == 1


def test_model_outage_gets_an_honest_reply(tmp_path):
    def down(system, messages, tools):
        raise RuntimeError("API unavailable")

    c, _ = app_with(tmp_path, FakeLLM(down))
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, str(uuid.uuid4()), "dinner tomorrow?")
        assert "Nothing has been booked or changed" in replies(c, cid)[0]


def test_failure_after_booking_says_what_was_booked(tmp_path):
    inner = tool_then_report("book_table", BOOK_CASA)

    def dies_after_booking(system, messages, tools):
        if last_tool_result(messages):
            raise RuntimeError("model died")
        return inner(system, messages, tools)

    c, _ = app_with(tmp_path, FakeLLM(dies_after_booking))
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, str(uuid.uuid4()), "book it")
        text = replies(c, cid)[0]
        assert "Booked Casa Lucia" in text and "ref HC-" in text


def test_turn_time_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("TURN_LIMIT_S", "0.3")

    async def slow(system, messages, tools):
        await asyncio.sleep(5)

    c, _ = app_with(tmp_path, FakeLLM(slow))
    with c:
        cid = str(uuid.uuid4())
        start = time.time()
        post(c, cid, str(uuid.uuid4()), "hello?")
        assert "technical problem" in replies(c, cid)[0] and time.time() - start < 3


def test_burst_is_answered_as_one_request(tmp_path):
    llm = FakeLLM(lambda s, m, t: [{"type": "text", "text": m[-1]["content"][0]["text"]}])
    c, _ = app_with(tmp_path, llm, burst_wait_s=0.5)
    with c:
        cid, member = str(uuid.uuid4()), str(uuid.uuid4())
        for text in ("dinner for 4", "actually 5", "fri"):  # 0.3 s apart: longer in total than one wait
            post(c, cid, member, text)
            time.sleep(0.3)
        assert replies(c, cid) == ["dinner for 4\nactually 5\nfri"]
        time.sleep(1)
        assert len(llm.calls) == 1


def test_allergies_always_reach_the_restaurant(tmp_path):
    c, db_path = app_with(tmp_path, FakeLLM(tool_then_report("book_table", {**BOOK_CASA,
                                                                             "special_requests": "Anniversary"})))
    member = str(uuid.uuid4())
    Database(db_path).add_note(member, "allergies", "Severe peanut allergy", None)
    Database(db_path).add_note(member, "people", "Client Maria: shellfish allergy", None)  # a guest: not every booking
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, member, "book it")
        assert json.loads(replies(c, cid)[0])["special_requests"] == "Anniversary; Allergies: Severe peanut allergy"


def test_second_booking_same_day_needs_confirmation(tmp_path):
    other_place = {**BOOK_CASA, "venue_id": "v-anatolia", "time": "20:00"}

    def books_twice(system, messages, tools):
        """First call books Casa Lucia, second books Anatolia the same day, third reports."""
        tool_rounds = sum(1 for m in messages if m["role"] == "user" and m["content"][-1]["type"] == "tool_result")
        if tool_rounds < 2:
            args = [BOOK_CASA, other_place][tool_rounds]
            return [{"type": "tool_use", "id": f"t{tool_rounds}", "name": "book_table", "input": args}]
        return [{"type": "text", "text": json.dumps(last_tool_result(messages))}]

    c, _ = app_with(tmp_path, FakeLLM(books_twice))
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, str(uuid.uuid4()), "book both")
        assert json.loads(replies(c, cid)[0])["error"] == "already_booked_that_day"


def test_memory_carries_into_the_next_conversation(tmp_path):
    def script(system, messages, tools):
        last = messages[-1]["content"][-1]
        if last["type"] == "text" and "vegan" in last["text"]:
            return [{"type": "tool_use", "id": "n1", "name": "save_member_note",
                     "input": {"category": "people", "note": "Partner Sam is vegan"}}]
        return [{"type": "text", "text": "Noted."}]

    llm = FakeLLM(script)
    c, _ = app_with(tmp_path, llm)
    with c:
        member, first, second = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        post(c, first, member, "btw my partner Sam is vegan")
        replies(c, first)
        post(c, second, member, "lunch saturday?")
        replies(c, second)
        shown = " ".join(b["text"] for b in llm.calls[-1]["system"])
        assert "people: Partner Sam is vegan" in shown  # the note
        assert "btw my partner Sam is vegan" in shown  # the end of the earlier conversation
        assert llm.calls[-1]["messages"][0]["content"][0]["text"] == "lunch saturday?"  # a fresh transcript


def test_correcting_a_note_retires_it(tmp_path):
    c, db_path = app_with(tmp_path, FakeLLM())
    db = Database(db_path)
    member = str(uuid.uuid4())
    note_id = db.add_note(member, "dietary", "Vegetarian", None)
    c, _ = app_with(tmp_path, FakeLLM(tool_then_report("retire_member_note", {"note_id": note_id})))
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, member, "I eat fish now")
        assert json.loads(replies(c, cid)[0]) == {"retired": True}
    assert db.active_notes(member) == []
    assert db.one("SELECT active FROM member_notes WHERE id = ?", (note_id,))["active"] == 0  # kept, not deleted


def test_cannot_touch_another_members_booking(tmp_path):
    c, db_path = app_with(tmp_path, FakeLLM())
    p = MockBookingProvider(Database(db_path), tz=LONDON)
    ref = p._book(**{**BOOK_CASA, "member_id": "someone-else", "guest_name": "X", "idempotency_key": "k"})["reference"]
    c, _ = app_with(tmp_path, FakeLLM(tool_then_report("cancel_booking", {"reference": ref})))
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, str(uuid.uuid4()), "cancel " + ref)
        assert json.loads(replies(c, cid)[0])["error"] == "unknown_booking"
    assert p._get(ref)["status"] == "confirmed"


def test_booking_patterns():
    def b(t, size=2, status="confirmed"):
        return {"time": t, "party_size": size, "status": status}

    assert booking_patterns([]) == []
    assert "Usual dinner time: about 20:00" in booking_patterns([b("19:45"), b("20:15")])[0]
    assert "hint to suggest" in booking_patterns([b("20:00")])[0]
    assert "vary" in booking_patterns([b("18:00"), b("21:30")])[0]
    assert any("Usual party size: 2" in line for line in booking_patterns([b("19:45"), b("20:15")]))
    assert booking_patterns([b("20:00", status="cancelled")]) == []


def test_upcoming_bookings_show_how_far_away(tmp_path):
    llm = FakeLLM()
    c, db_path = app_with(tmp_path, llm)
    member = str(uuid.uuid4())
    p = MockBookingProvider(Database(db_path), tz=LONDON)
    p._book(**{**BOOK_CASA, "member_id": member, "guest_name": "Jane Doe", "idempotency_key": "k"})
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, member, "same again next week?")
        replies(c, cid)
    shown = " ".join(b["text"] for b in llm.calls[-1]["system"])
    assert "(in " in shown and "days) 19:30" in shown


def test_search_marks_venues_booked_before(tmp_path):
    c, db_path = app_with(tmp_path, FakeLLM(tool_then_report(
        "search_restaurants", {"date": day(0), "party_size": 2, "area": "Notting Hill"})))
    member = str(uuid.uuid4())
    p = MockBookingProvider(Database(db_path), tz=LONDON)
    p._book(**{**BOOK_CASA, "member_id": member, "guest_name": "Jane Doe", "idempotency_key": "k"})
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, member, "somewhere new in notting hill?")
        results = {r["id"]: r for r in json.loads(replies(c, cid)[0])["results"]}
    assert results["v-casa-lucia"]["previously_booked"].startswith("upcoming booking on Mon ")
    assert "previously_booked" not in results["v-taqueria-azul"]


# -- harness run 1: bookings made here, identical bookings, all reply text -------------------

def booking_turns(script_per_turn):
    """A model whose behaviour depends on which member turn it is answering (1-based)."""
    def script(system, messages, tools):
        turn = sum(1 for m in messages if m["role"] == "user" and m["content"][0]["type"] == "text")
        return script_per_turn(turn, system, messages)
    return script


def test_booking_made_here_is_marked_in_the_next_turn(tmp_path):
    def script(turn, system, messages):
        if turn == 1 and last_tool_result(messages) is None:
            return [{"type": "tool_use", "id": "b", "name": "book_table", "input": BOOK_CASA}]
        return [{"type": "text", "text": "ok"}]

    llm = FakeLLM(booking_turns(script))
    c, _ = app_with(tmp_path, llm)
    with c:
        cid, member = str(uuid.uuid4()), str(uuid.uuid4())
        post(c, cid, member, "book casa lucia")
        replies(c, cid)
        post(c, cid, member, "thanks")
        replies(c, cid, n=2)
    shown = " ".join(b["text"] for b in llm.calls[-1]["system"])
    assert "[booked by you in this conversation]" in shown


def test_same_booking_asked_for_again_later_is_not_made_twice(tmp_path):
    def script(turn, system, messages):
        result = last_tool_result(messages)
        if result is None:  # every turn tries to book the same table
            return [{"type": "tool_use", "id": f"b{turn}", "name": "book_table", "input": BOOK_CASA}]
        return [{"type": "text", "text": json.dumps(result)}]

    c, _ = app_with(tmp_path, FakeLLM(booking_turns(script)))
    with c:
        cid, member = str(uuid.uuid4()), str(uuid.uuid4())
        post(c, cid, member, "book casa lucia")
        first = json.loads(replies(c, cid)[0])
        post(c, cid, member, "keep it")
        second = json.loads(replies(c, cid, n=2)[1])
        assert second["reference"] == first["reference"]
        assert second["already_booked"].endswith("You made it earlier in this conversation.")
        assert len(c.get(f"/members/{member}/bookings", headers=H).json()["bookings"]) == 1


def test_retry_after_timeout_is_presented_as_this_turns_booking(tmp_path):
    calls = {"n": 0}

    def script(system, messages, tools):
        result = last_tool_result(messages)
        if result is None or (result.get("error") == "upstream_timeout" and calls["n"] < 2):
            calls["n"] += 1
            return [{"type": "tool_use", "id": f"b{calls['n']}", "name": "book_table", "input": BOOK_CASA}]
        return [{"type": "text", "text": json.dumps(result)}]

    c, db_path = app_with(tmp_path, FakeLLM(script))
    provider = c.app.state.worker.assistant.provider

    def timeout_once():
        provider._maybe_fail_after = lambda: None
        from app.booking.interface import BookingError
        raise BookingError("upstream_timeout", "timed out", retryable=True)

    provider._maybe_fail_after = timeout_once  # the first call writes the booking, then "times out"
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, str(uuid.uuid4()), "book it")
        result = json.loads(replies(c, cid)[0])
    assert "replayed" not in result and result["note"].startswith("This is the booking your earlier call")
    assert result["booked_in_this_conversation"] is True


def test_text_written_alongside_an_action_reaches_the_member(tmp_path):
    def script(system, messages, tools):
        if last_tool_result(messages) is None:
            return [{"type": "text", "text": "I can't set calendar reminders, sorry."},
                    {"type": "tool_use", "id": "n", "name": "save_member_note",
                     "input": {"category": "people", "note": "Client Maria: severe peanut allergy"}}]
        return [{"type": "text", "text": "Maria's allergy is saved."}]

    c, _ = app_with(tmp_path, FakeLLM(script))
    with c:
        cid = str(uuid.uuid4())
        post(c, cid, str(uuid.uuid4()), "remind me at 5pm, and note Maria's allergy")
        assert replies(c, cid)[0] == "I can't set calendar reminders, sorry.\n\nMaria's allergy is saved."
