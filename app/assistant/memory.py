"""What the assistant knows about a member, rebuilt from storage for every turn.

Three sources (DECISIONS.md, "Memory"):
1. Notes the assistant saved from what the member said, with ids so a
   corrected note can be retired.
2. Booking history from the booking system, plus patterns calculated from it
   each time (usual time, usual party size), never stored, so never stale.
3. The end of the member's last few conversations, so "same as last time" and
   "the place you suggested" resolve.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from ..booking.interface import BookingProvider
from ..db import Database
from .tools import LONDON

PREVIOUS_CONVERSATIONS = 3
MESSAGES_PER_CONVERSATION = 8
MAX_MESSAGE_CHARS = 300


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def booking_patterns(bookings: list[dict]) -> list[str]:
    """Habits shown by the member's confirmed bookings, worded for the model.

    Two or more dinners within an hour of each other make a usual time to assume
    (and state); a single one is only a hint to suggest. Same idea for lunch and
    for party size.
    """
    confirmed = [b for b in bookings if b["status"] == "confirmed"]
    lines = []
    for meal, is_meal in (("dinner", lambda m: m >= 17 * 60), ("lunch", lambda m: m < 17 * 60)):
        times = [_minutes(b["time"]) for b in confirmed if is_meal(_minutes(b["time"]))]
        if len(times) >= 2 and max(times) - min(times) <= 60:
            usual = _hhmm(round(sum(times) / len(times) / 15) * 15)
            lines.append(f"Usual {meal} time: about {usual} ({len(times)} bookings). Assume it and say so.")
        elif len(times) == 1:
            lines.append(f"One previous {meal}, at {_hhmm(times[0])}: a hint to suggest, not to assume.")
        elif len(times) >= 2:
            lines.append(f"{meal.title()} times vary ({', '.join(sorted({_hhmm(t) for t in times}))}): ask.")
    sizes = Counter(b["party_size"] for b in confirmed)
    if sizes:
        size, n = sizes.most_common(1)[0]
        if n >= 2:
            lines.append(f"Usual party size: {size} ({n} of {len(confirmed)} bookings).")
    return lines


async def member_context(db: Database, provider: BookingProvider, member_id: str, conversation_id: str) -> str:
    """Everything known about the member, as text for the model's instructions."""
    member = db.one("SELECT name FROM members WHERE id = ?", (member_id,)) or {"name": "the member"}
    lines = [f"Member: {member['name']}."]

    notes = db.active_notes(member_id)
    if notes:
        lines.append("\nWhat they have told you (note id in brackets):")
        lines += [f"- [{n['id']}] {n['category']}: {n['note']} (since {n['created_at'][:10]})" for n in notes]
    else:
        lines.append("\nNothing saved about them yet: treat them as a new member and learn as you go.")

    today = datetime.now(LONDON).date().isoformat()
    bookings = await provider.bookings_for_member(member_id)

    def line(b: dict) -> str:
        extra = f", requests: {b['special_requests']}" if b.get("special_requests") else ""
        return (f"- {b['reference']}: {b['venue_name']} ({b['area']}), {b['weekday'].title()} {b['date']} "
                f"{b['time']}, party of {b['party_size']}{extra}")

    upcoming = [b for b in bookings if b["status"] == "confirmed" and b["date"] >= today]
    past = [b for b in bookings if b["status"] == "confirmed" and b["date"] < today][-5:]
    cancelled = [b for b in bookings if b["status"] == "cancelled"][-3:]
    lines.append("\nUpcoming bookings:" if upcoming else "\nNo upcoming bookings.")
    lines += [line(b) for b in upcoming]
    if past:
        lines.append("Past bookings, most recent last:")
        lines += [line(b) for b in past]
    if cancelled:
        lines.append("Recently cancelled:")
        lines += [line(b) for b in cancelled]
    patterns = booking_patterns(bookings)
    if patterns:
        lines.append("Patterns from their bookings:")
        lines += [f"- {p}" for p in patterns]

    previous = db.recent_conversations(member_id, conversation_id, PREVIOUS_CONVERSATIONS)
    if previous:
        lines.append("\nHow their last conversations ended, oldest first:")
        for conv in reversed(previous):
            lines.append(f"Conversation of {conv['created_at'][:10]}:")
            for m in db.last_messages(conv["id"], MESSAGES_PER_CONVERSATION):
                text = " ".join(m["text"].split())
                cut = text[:MAX_MESSAGE_CHARS] + ("…" if len(text) > MAX_MESSAGE_CHARS else "")
                lines.append(f"  {m['role']}: {cut}")
    return "\n".join(lines)
