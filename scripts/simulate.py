"""Practice harness: a model plays a member against the running service over the real
HTTP contract, following Halcyon's rules: poll every 2 s, the member answers after 15 s
without a new assistant message, and the conversation fails at 180 s without a reply.

    pip install -r requirements-dev.txt
    export ANTHROPIC_API_KEY=...            # for the simulated member
    python -m scripts.simulate scenarios/*.yaml --url https://<service> --token <API_TOKEN>

Each scenario is one member with a persona and one or more conversations. After each
conversation, checks compare what the service actually holds (bookings, notes) with what
the scenario expects. Prints transcripts, timings and check results; exits 1 on any failure.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.assistant.llm import ClaudeLLM  # noqa: E402

LONDON = ZoneInfo("Europe/London")
DONE = "[DONE]"
MEMBER_INSTRUCTIONS = """\
You are role-playing a member of Halcyon, a private London lifestyle membership, texting your \
personal assistant. Stay in character. Write as you would on your phone: short, natural, sometimes \
terse. Never mention that you are an AI or a simulation.

Who you are: {persona}

What you want from this conversation: {goal}

Today is {today}. Reply with only your next message to the assistant. Answer its questions from \
what you know about yourself; if something isn't covered, decide as a real person would. Do not \
volunteer things the goal tells you to hold back. When your goal is achieved, or clearly can't be, \
reply with {done} alone, or a short closing line followed by {done}."""


class Service:
    """The service under test, used exactly as Halcyon uses it."""

    def __init__(self, url: str, token: str, quiet_s: float, poll_s: float = 2.0, fail_s: float = 180.0):
        self.http = httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        self.quiet_s, self.poll_s, self.fail_s = quiet_s, poll_s, fail_s

    async def send(self, conversation_id: str, member: dict, text: str, deliveries: int) -> None:
        """Post one member message, `deliveries` times with the same id (at-least-once delivery)."""
        body = {"message_id": str(uuid.uuid4()), "member": member, "text": text}
        for _ in range(deliveries):
            r = await self.http.post(f"/conversations/{conversation_id}/messages", json=body)
            assert r.status_code == 202, f"POST answered {r.status_code}: {r.text}"

    async def wait_for_turn(self, conversation_id: str, seen: int) -> tuple[list[dict], float]:
        """Poll until a reply has arrived and `quiet_s` has passed with nothing new.
        Returns the new assistant messages and the seconds until the first of them."""
        start, first_at, last_new = time.monotonic(), None, None
        new: list[dict] = []
        while True:
            await asyncio.sleep(self.poll_s)
            messages = (await self.http.get(f"/conversations/{conversation_id}/messages")).json()["messages"]
            fresh = [m for m in messages[seen:] if m["role"] == "assistant"]
            now = time.monotonic()
            if len(fresh) > len(new):
                new, last_new = fresh, now
                first_at = first_at or now
            if first_at is None and now - start > self.fail_s:
                raise TimeoutError("no assistant message within 180 s")
            if last_new and now - last_new >= self.quiet_s:
                return new, first_at - start

    async def bookings(self, member_id: str) -> list[dict]:
        return (await self.http.get(f"/members/{member_id}/bookings")).json()["bookings"]

    async def notes(self, member_id: str) -> list[dict]:
        return (await self.http.get(f"/members/{member_id}/notes")).json()["notes"]


async def run_conversation(service: Service, member_llm, member: dict, persona: str, conv: dict,
                           max_turns: int, deliveries: int) -> dict:
    instructions = [{"type": "text", "text": MEMBER_INSTRUCTIONS.format(
        persona=persona, goal=conv["goal"], done=DONE, today=datetime.now(LONDON).strftime("%A %d %B %Y"))}]
    conversation_id, transcript, waits, seen, sent = str(uuid.uuid4()), [], [], 0, 0
    # From the member model's side, its own messages are "assistant" and the service's are "user".
    history: list[dict] = [{"role": "user", "content": "(You start the conversation.)"}]
    opening = conv.get("opening")
    burst = opening if isinstance(opening, list) else [opening] if opening else None
    for _ in range(max_turns):
        if burst is None:
            blocks = await member_llm.complete(instructions, history, [])
            burst = ["".join(b.get("text", "") for b in blocks).strip()]
        if any(DONE in text for text in burst):
            closing = burst[0].replace(DONE, "").strip()
            if closing:  # shown, but never sent: the member has left
                transcript.append(("member (closing, not sent)", closing))
            break
        for i, text in enumerate(burst):  # a burst: several messages in quick succession
            if i:
                await asyncio.sleep(1)  # typing speed; the service waits for 2 s of quiet
            transcript.append(("member", text))
            await service.send(conversation_id, member, text, deliveries)
            seen += 1
            sent += 1
        replies, first_s = await service.wait_for_turn(conversation_id, seen)
        seen += len(replies)
        waits.append(round(first_s, 1))
        transcript += [("assistant", r["text"]) for r in replies]
        history += [{"role": "assistant", "content": "\n".join(burst)},
                    {"role": "user", "content": "\n\n".join(r["text"] for r in replies)}]
        burst = None
    return {"transcript": transcript, "member_messages": sent, "seconds_to_reply": waits}


def check(expect: dict, bookings: list[dict], notes: list[dict]) -> list[str]:
    """Compare what the service holds with the scenario's expectations. Returns the failures.

    Expectation keys: confirmed_bookings (count of upcoming), booking (one or a list of: party_size,
    weekday, venue_id, venue_not, time_between, requests_mention), notes_mention, notes_not_mention,
    max_member_messages (checked by the caller).
    """
    today = datetime.now(LONDON).date()
    upcoming = [b for b in bookings if b["status"] == "confirmed" and b["date"] >= today.isoformat()]
    failures = []
    if "confirmed_bookings" in expect and len(upcoming) != expect["confirmed_bookings"]:
        failures.append(f"expected {expect['confirmed_bookings']} upcoming booking(s), found {len(upcoming)}")
    wanted = expect.get("booking") or []
    for want in [wanted] if isinstance(wanted, dict) else wanted:
        def matches(b: dict) -> bool:
            return all([
                want.get("party_size") in (None, b["party_size"]),
                want.get("weekday") in (None, b["weekday"]),
                want.get("venue_id") in (None, b["venue_id"]),
                b["venue_id"] not in want.get("venue_not", []),
                "time_between" not in want or want["time_between"][0] <= b["time"] <= want["time_between"][1],
                "requests_mention" not in want
                or all(w.lower() in (b["special_requests"] or "").lower() for w in want["requests_mention"]),
            ])
        if not any(matches(b) for b in upcoming):
            failures.append(f"no upcoming booking matching {want}")
    active = " | ".join(n["note"].lower() for n in notes if n["active"])
    failures += [f"no active note mentions {w!r} (active: {active or 'none'})"
                 for w in expect.get("notes_mention", []) if w.lower() not in active]
    failures += [f"an active note still mentions {w!r}" for w in expect.get("notes_not_mention", [])
                 if w.lower() in active]
    return failures


async def run_scenario(path: str, service: Service, member_llm, args) -> bool:
    with open(path) as f:
        sc = yaml.safe_load(f)
    member = {"id": str(uuid.uuid4()), "name": sc["member"]["name"]}  # a fresh member every run
    print(f"\n=== {sc['name']}  ({os.path.basename(path)})\n{sc.get('description', '').strip()}")
    passed = True
    for i, conv in enumerate(sc["conversations"], 1):
        result = await run_conversation(service, member_llm, member, sc["member"]["persona"], conv,
                                        args.max_turns, 2 if args.duplicate else 1)
        print(f"\n--- conversation {i}: {conv['goal'].strip()}")
        for role, text in result["transcript"]:
            print(f"  {role.upper():9} " + text.replace("\n", "\n            "))
        # member messages = messages actually sent to the service; a closing line is not counted
        print(f"  [{result['member_messages']} member messages; seconds to each reply: {result['seconds_to_reply']}]")
        if conv.get("expect"):
            failures = check(conv["expect"], await service.bookings(member["id"]), await service.notes(member["id"]))
            limit = conv["expect"].get("max_member_messages")
            if limit and result["member_messages"] > limit:
                failures.append(f"took {result['member_messages']} member messages, expected at most {limit}")
            for f in failures:
                print(f"  CHECK FAILED: {f}")
            print("  checks passed" if not failures else "")
            passed &= not failures
    return passed


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="+", help="scenario YAML files")
    ap.add_argument("--url", default=os.environ.get("SERVICE_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("API_TOKEN", ""))
    ap.add_argument("--quiet", type=float, default=15.0, help="seconds of silence before the member answers")
    ap.add_argument("--max-turns", type=int, default=8)
    ap.add_argument("--duplicate", action="store_true", help="deliver every member message twice")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # print as it goes, even through `| tee`
    service = Service(args.url, args.token, args.quiet)
    member_llm = ClaudeLLM(model=os.environ.get("MEMBER_MODEL", "claude-haiku-5-5"))  # cheap is fine for the member
    results = [await run_scenario(p, service, member_llm, args) for p in args.scenarios]
    print(f"\n{sum(results)}/{len(results)} scenarios passed their checks")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
