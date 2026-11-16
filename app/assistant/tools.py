"""What the assistant can do: the tool definitions the model sees, and their execution.

Rules enforced here in code, not left to the model:
* Booking idempotency keys are derived from (conversation, turn, venue, date,
  time, party). A retry after a timeout, or the whole turn redone after a
  crash, returns the original booking and never makes a second one.
* Allergies on file are always added to the restaurant's special requests.
* Spending the member's money (deposits, card holds, late-cancellation fees)
  needs an explicit "accepted by member" flag; the instructions say to set it
  only after the member said yes.
* A second booking on a day the member already has one needs an explicit flag.
* Every change to a booking is recorded on the TurnContext, so if the turn
  fails half-way the member is still told exactly what was done.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ..booking.interface import BookingError, BookingProvider
from ..db import Database

LONDON = ZoneInfo("Europe/London")
NOTE_CATEGORIES = ["allergies", "dietary", "likes", "dislikes", "people", "places", "occasions",
                   "service_style", "feedback", "other"]

TOOLS: list[dict] = [
    {
        "name": "search_restaurants",
        "description": ("Search restaurants with live availability. Use filters only for hard requirements; "
                        "put soft wishes (romantic, quiet, views, lively) in keywords. Each result lists up to 8 "
                        "available times nearest the requested time, and previously_booked if the member has "
                        "booked it before."),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "party_size": {"type": "integer", "minimum": 1},
                "time": {"type": "string", "description": "Preferred time, HH:MM 24h. Default 19:30."},
                "area": {"type": "string", "description": "London neighbourhood, e.g. Soho, Mayfair, Chelsea."},
                "cuisine": {"type": "string", "description": "e.g. Italian, seafood, tasting menu."},
                "keywords": {"type": "string", "description": "Soft wishes: romantic quiet views terrace lively..."},
                "dietary": {"type": "string", "description": ("A hard need: vegetarian, vegan, halal, gluten-free, "
                                                              "pescatarian, or coeliac (only kitchens that are coeliac-safe: accredited or fully gluten-free).")},
                "max_price": {"type": "string", "enum": ["£", "££", "£££", "££££"]},
                "step_free": {"type": "boolean"},
            },
            "required": ["date", "party_size"],
        },
    },
    {
        "name": "get_restaurant",
        "description": "Full details of one restaurant: opening hours, dietary options, policies, notes.",
        "input_schema": {"type": "object", "properties": {"venue_id": {"type": "string"}}, "required": ["venue_id"]},
    },
    {
        "name": "check_availability",
        "description": "Available times at one restaurant on a date, nearest the preferred time if given.",
        "input_schema": {
            "type": "object",
            "properties": {"venue_id": {"type": "string"}, "date": {"type": "string"},
                           "party_size": {"type": "integer", "minimum": 1}, "time": {"type": "string"}},
            "required": ["venue_id", "date", "party_size"],
        },
    },
    {
        "name": "book_table",
        "description": ("Book a table for the member. It exists only if this returns status 'confirmed'. "
                        "Safe to retry after a timeout: a retry in the same turn cannot double-book."),
        "input_schema": {
            "type": "object",
            "properties": {
                "venue_id": {"type": "string"}, "date": {"type": "string"}, "time": {"type": "string"},
                "party_size": {"type": "integer", "minimum": 1},
                "special_requests": {"type": "string",
                                     "description": "For the restaurant: occasion, seating wishes, dietary needs."},
                "guest_name": {"type": "string", "description": "Defaults to the member's name."},
                "policy_accepted_by_member": {
                    "type": "boolean",
                    "description": "True only if the member agreed to this venue's deposit or card-hold fee in this conversation."},
                "second_booking_same_day": {
                    "type": "boolean",
                    "description": "True to book even though the member already has a booking that day."},
            },
            "required": ["venue_id", "date", "time", "party_size"],
        },
    },
    {
        "name": "list_member_bookings",
        "description": "The member's bookings. Use to check what is really booked before saying so.",
        "input_schema": {"type": "object", "properties": {"include_past": {"type": "boolean"}}},
    },
    {
        "name": "change_booking",
        "description": "Change date, time, party size or requests on a booking. If the new slot isn't available, the booking stays as it was.",
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string"}, "date": {"type": "string"}, "time": {"type": "string"},
                           "party_size": {"type": "integer", "minimum": 1}, "special_requests": {"type": "string"}},
            "required": ["reference"],
        },
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a booking. If a late-cancellation charge applies, this fails with the amount unless fee_accepted_by_member is true.",
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string"}, "fee_accepted_by_member": {"type": "boolean"}},
            "required": ["reference"],
        },
    },
    {
        "name": "save_member_note",
        "description": ("Remember something lasting the member told you, for future conversations. Only what they "
                        "said, in their terms; not guesses, not details of a single booking."),
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": NOTE_CATEGORIES},
                           "note": {"type": "string", "description": ("One short line, e.g. 'Husband Sam is coeliac: "
                                                                      "needs a coeliac-safe kitchen' or 'Lives in Islington'.")}},
            "required": ["category", "note"],
        },
    },
    {
        "name": "retire_member_note",
        "description": "Retire a note that is no longer true (the member corrected it). Save the new fact separately.",
        "input_schema": {"type": "object", "properties": {"note_id": {"type": "string"}}, "required": ["note_id"]},
    },
]


@dataclass
class TurnContext:
    """Who and what one turn is about, and what it has changed so far."""
    conversation_id: str
    member_id: str
    member_name: str
    turn_key: str  # the same if the turn is redone, so idempotency keys repeat
    effects: list[str] = field(default_factory=list)


class ToolRunner:
    """Executes the model's tool calls. Results go back to the model as JSON text."""

    def __init__(self, db: Database, provider: BookingProvider):
        self.db, self.provider = db, provider

    async def run(self, ctx: TurnContext, name: str, args: dict) -> str:
        """Run one tool call. Expected failures come back as {"error": ...} for the model to handle."""
        fn = getattr(self, f"_{name}", None)
        if fn is None or name not in {t["name"] for t in TOOLS}:
            return json.dumps({"error": "unknown_tool", "message": f"No tool called {name}."})
        try:
            result = await fn(ctx, **args)
        except BookingError as e:
            result = e.as_dict()
        except TypeError as e:  # the model passed arguments the tool doesn't take
            result = {"error": "bad_arguments", "message": str(e)}
        return json.dumps(result, ensure_ascii=False)

    # -- restaurants ----------------------------------------------------------------------
    async def _search_restaurants(self, ctx, **filters):
        """Search, marking any venue the member has booked before, so "somewhere new" can't be missed."""
        result = await self.provider.search(**filters)
        today = datetime.now(LONDON).date().isoformat()
        booked = {}
        for b in await self.provider.bookings_for_member(ctx.member_id):
            if b["status"] == "confirmed":
                when = "upcoming" if b["date"] >= today else "past"
                booked[b["venue_id"]] = f"{when} booking on {b['weekday'].title()} {b['date']}"
        for venue in result.get("results", []):
            if venue["id"] in booked:
                venue["previously_booked"] = booked[venue["id"]]
        return result

    async def _get_restaurant(self, ctx, venue_id):
        return await self.provider.venue(venue_id)

    async def _check_availability(self, ctx, venue_id, date, party_size, time=None):
        return await self.provider.availability(venue_id, date, party_size, time=time)

    # -- bookings -----------------------------------------------------------------------
    async def _book_table(self, ctx, venue_id, date, time, party_size, special_requests=None, guest_name=None,
                          policy_accepted_by_member=False, second_booking_same_day=False):
        key = hashlib.sha256(f"{ctx.conversation_id}|{ctx.turn_key}|{venue_id}|{date}|{time}|{party_size}"
                             .encode()).hexdigest()
        if not second_booking_same_day:
            same_day = [b for b in await self.provider.bookings_for_member(ctx.member_id)
                        if b["status"] == "confirmed" and b["date"] == date]
            # A retry of a booking this turn already made is not a second booking.
            same_day = [b for b in same_day if not (b["venue_id"] == venue_id and b["time"] == time)]
            if same_day:
                return {"error": "already_booked_that_day",
                        "message": "The member already has a booking that day. Check with them, or set "
                                   "second_booking_same_day if intended (e.g. replacing it: book, then cancel the old one).",
                        "existing": same_day}
        result = await self.provider.book(
            venue_id=venue_id, date=date, time=time, party_size=party_size, member_id=ctx.member_id,
            guest_name=guest_name or ctx.member_name, idempotency_key=key,
            special_requests=self._with_allergies(ctx.member_id, special_requests),
            policy_acknowledged=bool(policy_accepted_by_member))
        if not result.get("replayed"):
            ctx.effects.append(f"Booked {result['venue_name']} for {result['weekday'].title()} {result['date']} at "
                               f"{result['time']}, party of {result['party_size']} (ref {result['reference']}).")
        return result

    def _with_allergies(self, member_id: str, requests: str | None) -> str | None:
        """Add the member's allergy notes to the restaurant's special requests, whatever the model wrote."""
        allergies = [n["note"] for n in self.db.active_notes(member_id) if n["category"] == "allergies"]
        missing = [a for a in allergies if a.lower() not in (requests or "").lower()]
        if not missing:
            return requests
        return "; ".join(filter(None, [requests, "Allergies: " + "; ".join(missing)]))

    async def _list_member_bookings(self, ctx, include_past=False):
        today = datetime.now(LONDON).date().isoformat()
        bookings = await self.provider.bookings_for_member(ctx.member_id)
        return {"bookings": bookings if include_past else [b for b in bookings if b["date"] >= today]}

    async def _owned(self, ctx, reference: str) -> None:
        """Members can only touch their own bookings."""
        if reference not in {b["reference"] for b in await self.provider.bookings_for_member(ctx.member_id)}:
            raise BookingError("unknown_booking", f"The member has no booking with reference {reference}.")

    async def _change_booking(self, ctx, reference, date=None, time=None, party_size=None, special_requests=None):
        await self._owned(ctx, reference)
        result = await self.provider.modify(reference, date=date, time=time, party_size=party_size,
                                            special_requests=special_requests)
        ctx.effects.append(f"Changed booking {reference}: now {result['venue_name']}, {result['weekday'].title()} "
                           f"{result['date']} at {result['time']}, party of {result['party_size']}.")
        return result

    async def _cancel_booking(self, ctx, reference, fee_accepted_by_member=False):
        await self._owned(ctx, reference)
        result = await self.provider.cancel(reference, accept_fee=bool(fee_accepted_by_member))
        if not result.get("already_cancelled"):
            ctx.effects.append(f"Cancelled booking {reference} at {result['venue_name']}.")
        return result

    # -- memory ----------------------------------------------------------------------
    async def _save_member_note(self, ctx, category, note):
        note = note.strip()
        if category not in NOTE_CATEGORIES:
            category = "other"
        if any(n["note"].lower() == note.lower() for n in self.db.active_notes(ctx.member_id)):
            return {"saved": False, "reason": "already on file"}
        return {"saved": True, "note_id": self.db.add_note(ctx.member_id, category, note, ctx.conversation_id)}

    async def _retire_member_note(self, ctx, note_id):
        return {"retired": self.db.retire_note(ctx.member_id, note_id.strip())}
