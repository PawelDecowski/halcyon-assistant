"""Mock restaurant-booking provider: an in-app stand-in for a third-party API.

It implements `BookingProvider`, so the assistant can't tell it from a real
one, and it behaves like one, including the ways real ones fail:

* Availability is deterministic per (venue, date, time), so a member sees a
  stable picture within a conversation. Prime time Thursday to Saturday is
  scarce, early and late is easy; each venue has its own `demand`. Bookings
  we make use up tables.
* Rules that need judgement: deposits and card holds that must be explicitly
  accepted, maximum party sizes, closed days, booking windows, and a
  late-cancellation charge reported before it is applied.
* Failures, set by env and off in tests:
    - simulated network latency on every call;
    - timeouts, half of them *after* the booking was written: the caller
      doesn't know whether it went through. Every booking carries an
      idempotency key, so the retry returns the original booking instead
      of making a second one;
    - a table that looked free being taken by the time we book.

State lives in the `mock_bookings` table, owned by this module. In real life
it would live at the third party.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import secrets
from datetime import date as Date, datetime, time as Time, timedelta

from ...db import Database, new_id, now_iso
from ..interface import BookingError, BookingProvider
from .venues import ALLERGEN_HANDLING, DAYS, VENUES, VENUES_BY_ID

COELIAC_SAFE = {"coeliac_accredited", "dedicated_gluten_free"}
# A diet is met by a venue offering it or a stricter one: a pescatarian can eat vegetarian, a
# vegetarian can eat vegan. Matching the label literally hid most venues from a pescatarian.
DIET_MET_BY = {
    "pescatarian": ("pescatarian", "vegetarian", "vegan"),
    "vegetarian": ("vegetarian", "vegan"),
}
PRICE_LEVELS = {"£": 1, "££": 2, "£££": 3, "££££": 4}
MAX_DAYS_AHEAD = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS mock_bookings (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    venue_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    guest_name TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    party_size INTEGER NOT NULL,
    special_requests TEXT,
    status TEXT NOT NULL,
    deposit_gbp INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bk_slot ON mock_bookings(venue_id, date, time, status);
CREATE INDEX IF NOT EXISTS ix_bk_member ON mock_bookings(member_id, date);
"""


def _parse_date(s: str) -> Date:
    try:
        return Date.fromisoformat(s)
    except (TypeError, ValueError):
        raise BookingError("invalid_date", f"Date must be YYYY-MM-DD, got {s!r}")


def _parse_time(s: str) -> Time:
    try:
        t = Time.fromisoformat(s)
    except (TypeError, ValueError):
        raise BookingError("invalid_time", f"Time must be HH:MM (24h), got {s!r}")
    return t.replace(second=0, microsecond=0)


def _fmt(t: Time) -> str:
    return t.strftime("%H:%M")


def _human(dt: datetime) -> str:
    """'Thursday 19 November, 7.30pm': the form the assistant uses with members."""
    hour = dt.hour % 12 or 12
    minutes = f".{dt.minute:02d}" if dt.minute else ""
    return f"{dt.strftime('%A')} {dt.day} {dt.strftime('%B')}, {hour}{minutes}{'am' if dt.hour < 12 else 'pm'}"


def _minutes(t: Time) -> int:
    return t.hour * 60 + t.minute


class MockBookingProvider(BookingProvider):
    def __init__(self, db: Database, *, tz, seed: str = "halcyon", transient_error_rate: float = 0.0,
                 slot_race_rate: float = 0.0, latency_s: float = 0.0, rng: random.Random | None = None):
        self.db, self.tz, self.seed = db, tz, seed
        self.transient_error_rate = transient_error_rate
        self.slot_race_rate = slot_race_rate
        self.rng = rng or random.Random()
        self.latency_s = latency_s
        db.executescript(SCHEMA)

    def _now(self) -> datetime:
        """The current time in London; tests replace it to check time-dependent terms."""
        return datetime.now(self.tz)

    async def _network(self) -> None:
        """Simulated round trip to the third party."""
        if self.latency_s:
            await asyncio.sleep(self.latency_s * (0.5 + self.rng.random()))

    # -- catalogue ----------------------------------------------------------------
    def _venue(self, venue_id: str) -> dict:
        v = VENUES_BY_ID.get(venue_id)
        if not v:
            raise BookingError("unknown_venue", f"No venue with id {venue_id!r}")
        return v

    def _meets_diet(self, v: dict, need: str) -> bool:
        """Whether a venue caters for a dietary need, counting stricter diets it offers."""
        need = need.lower().strip()
        accepted = DIET_MET_BY.get(need, (need,))
        return any(a in offered.lower() for a in accepted for offered in v["dietary"])

    def _allergens(self, v: dict) -> dict:
        """How the kitchen handles allergies: a level and what it means for the guest."""
        detail = ALLERGEN_HANDLING[v["allergens"]]
        if v.get("allergen_note"):
            detail += " " + v["allergen_note"]
        return {"handling": v["allergens"], "detail": detail}

    def _public_venue(self, v: dict) -> dict:
        """Venue details as a guest would see them: no internal fields like demand or tables."""
        p = v["policy"]
        policy = {"booking": p["mode"], "table_time_minutes": v["table_time_minutes"]}
        if p["cancellation_hours"]:
            policy["cancellation_window_hours"] = p["cancellation_hours"]
        if p["deposit_pp_gbp"]:
            policy["deposit_per_person_gbp"] = p["deposit_pp_gbp"]
        if p["cancellation_fee_pp_gbp"]:
            policy["late_cancellation_or_no_show_fee_per_person_gbp"] = p["cancellation_fee_pp_gbp"]
        out = {k: v[k] for k in ("id", "name", "area", "cuisine", "price", "avg_spend_pp_gbp",
                                 "description", "tags", "dietary", "step_free", "max_party")}
        out["opening"] = {d: [f"{a}-{b}" for a, b in v["seatings"].get(d, [])] or "closed" for d in DAYS}
        out["policy"] = policy
        out["allergens"] = self._allergens(v)
        if v.get("notes"):
            out["notes"] = v["notes"]
        if v.get("private_dining_max"):
            out["private_dining_max"] = v["private_dining_max"]
        return out

    # -- availability -------------------------------------------------------------
    def _slots(self, v: dict, d: Date) -> list[Time]:
        """Seating times every 15 minutes from first to last seating."""
        out = set()
        for start, end in v["seatings"].get(DAYS[d.weekday()], []):
            t, last = _minutes(_parse_time(start)), _minutes(_parse_time(end))
            while t <= last:
                out.add(Time(t // 60, t % 60))
                t += 15
        return sorted(out)

    def _baseline_taken(self, v: dict, d: Date, t: Time) -> int:
        """Tables taken by the outside world. Deterministic, peaks at prime time."""
        h = int(hashlib.sha256(f"{self.seed}|{v['id']}|{d}|{_fmt(t)}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        m = _minutes(t)
        weekend = d.weekday() in (3, 4, 5)  # Thu-Sat nights
        if 19 * 60 <= m <= 20 * 60 + 45:
            peak = 1.0 if weekend else 0.8
        elif 18 * 60 <= m <= 21 * 60 + 30:
            peak = 0.75 if weekend else 0.55
        elif 12 * 60 + 30 <= m <= 13 * 60 + 30:
            peak = 0.6 if d.weekday() >= 5 else 0.5
        else:
            peak = 0.35
        load = v["demand"] * peak * 1.45 + (h - 0.5) * 0.6
        return max(0, min(v["tables"], round(v["tables"] * load)))

    def _our_bookings_at(self, venue_id: str, d: Date, t: Time, exclude_ref: str | None = None) -> int:
        """Tables taken by bookings made through us at this exact time (optionally ignoring one booking)."""
        row = self.db.one(
            "SELECT COUNT(*) AS n FROM mock_bookings WHERE venue_id = ? AND date = ? AND time = ? "
            "AND status = 'confirmed' AND reference != ?",
            (venue_id, d.isoformat(), _fmt(t), exclude_ref or ""),
        )
        return row["n"]

    def _tables_needed(self, party_size: int) -> int:
        """Parties of up to 6 take one table; larger ones take two pushed together."""
        return 1 if party_size <= 6 else 2

    def _free(self, v: dict, d: Date, t: Time, party_size: int, exclude_ref: str | None = None) -> bool:
        """Whether enough tables are left at this time for the party, after outside and our own bookings."""
        free = v["tables"] - self._baseline_taken(v, d, t) - self._our_bookings_at(v["id"], d, t, exclude_ref)
        return free >= self._tables_needed(party_size)

    def _check_slot(self, v: dict, d: Date, t: Time) -> None:
        """Raise invalid_slot if the venue is closed that day or does not seat at that time."""
        slots = self._slots(v, d)
        if not slots:
            raise BookingError("invalid_slot", f"{v['name']} is closed on {DAYS[d.weekday()].title()}s.")
        if t not in slots:
            raise BookingError("invalid_slot", f"{v['name']} does not seat at {_fmt(t)} on that day "
                               f"(seatings {_fmt(slots[0])} to {_fmt(slots[-1])}, every 15 minutes).")

    def _check_bookable(self, v: dict, d: Date, party_size: int) -> None:
        """Raise if the date is outside the booking window or the party is too large to book online."""
        today = datetime.now(self.tz).date()
        if d < today:
            raise BookingError("date_in_past", f"{d} is in the past.")
        if d > today + timedelta(days=MAX_DAYS_AHEAD):
            raise BookingError("too_far_ahead", f"{v['name']} takes bookings up to {MAX_DAYS_AHEAD} days ahead.")
        if party_size < 1:
            raise BookingError("invalid_party_size", "Party size must be at least 1.")
        if party_size > v["max_party"]:
            extra = {"private_dining_max": v["private_dining_max"]} if v.get("private_dining_max") else {}
            hint = (f" Private dining for up to {v['private_dining_max']} is arranged directly with the venue."
                    if extra else "")
            raise BookingError("party_too_large",
                                f"{v['name']} seats parties of up to {v['max_party']} online.{hint}", **extra)

    def _availability(self, venue_id: str, date: str, party_size: int, around: str | None = None,
                     window_minutes: int = 120, exclude_ref: str | None = None) -> dict:
        v = self._venue(venue_id)
        d = _parse_date(date)
        self._check_bookable(v, d, party_size)
        slots = self._slots(v, d)
        if not slots:
            return {"venue_id": v["id"], "date": d.isoformat(), "open": False, "available_times": [],
                    "message": f"{v['name']} is closed on {DAYS[d.weekday()].title()}s."}
        now = datetime.now(self.tz)
        if around:
            centre = _minutes(_parse_time(around))
            slots = [s for s in slots if abs(_minutes(s) - centre) <= window_minutes]
            slots.sort(key=lambda s: abs(_minutes(s) - centre))
        free = []
        for s in slots:
            if datetime.combine(d, s, self.tz) < now + timedelta(minutes=60):
                continue  # online booking closes an hour before
            if self._free(v, d, s, party_size, exclude_ref):
                free.append(_fmt(s))
        if around:
            free = sorted(free[:8])
        return {"venue_id": v["id"], "venue_name": v["name"], "date": d.isoformat(), "party_size": party_size,
                "open": True, "available_times": free}

    def _search(self, *, date: str, party_size: int, time: str | None = None, area: str | None = None,
               cuisine: str | None = None, keywords: str | None = None, dietary: str | None = None,
               max_price: str | None = None, step_free: bool | None = None, limit: int = 6) -> dict:
        d = _parse_date(date)
        max_level = PRICE_LEVELS.get(max_price or "", 4)
        terms = [w for w in (keywords or "").lower().replace(",", " ").split() if len(w) > 2]
        results = []
        for v in VENUES:
            if area and area.lower() not in v["area"].lower():
                continue
            if cuisine and cuisine.lower() not in (v["cuisine"] + " " + " ".join(v["tags"])).lower():
                continue
            if dietary and any(w in dietary.lower() for w in ("coeliac", "celiac")):
                if v["allergens"] not in COELIAC_SAFE:  # "gluten-free options" is not coeliac-safe
                    continue
            elif dietary and not self._meets_diet(v, dietary):
                continue
            if PRICE_LEVELS[v["price"]] > max_level:
                continue
            if step_free and not v["step_free"]:
                continue
            hay = " ".join([v["name"], v["cuisine"], v["description"], " ".join(v["tags"]), v["area"]]).lower()
            score = sum(1 for w in terms if w in hay)
            try:
                av = self._availability(v["id"], date, party_size, around=time or "19:30", window_minutes=90)
                times, reason = av["available_times"], (av.get("message") if not av["open"] else None)
            except BookingError as e:
                times, reason = [], e.message
            results.append((score, bool(times), v, times, reason))
        # Venues that can take the booking come first, best keyword match first. A few that can't
        # follow, with the reason, only if there is room: they must never crowd out real options.
        bookable = sorted((r for r in results if r[1]), key=lambda r: -r[0])
        unbookable = sorted((r for r in results if not r[1]), key=lambda r: -r[0])
        shown = bookable[:limit]
        shown += unbookable[:min(2, limit - len(shown))]
        out = []
        for score, _, v, times, reason in shown:
            item = {k: v[k] for k in ("id", "name", "area", "cuisine", "price", "avg_spend_pp_gbp", "tags")}
            item["summary"] = v["description"]
            item["dietary"] = v["dietary"]
            item["allergens"] = v["allergens"]
            item["available_times"] = times
            if reason:
                item["unavailable_reason"] = reason
            if v["policy"]["mode"] != "instant":
                item["policy_note"] = self._policy_line(v)
            out.append(item)
        resp = {"date": d.isoformat(), "weekday": DAYS[d.weekday()], "party_size": party_size,
                "matched": len(results), "available": len(bookable), "results": out}
        if len(bookable) > limit:
            resp["more_available"] = (f"{len(bookable) - limit} more venues have tables; "
                                      "narrow the search or raise the limit to see them.")
        if not results:
            resp["known_areas"] = sorted({v["area"] for v in VENUES})
            resp["known_cuisines"] = sorted({v["cuisine"] for v in VENUES})
        return resp

    def _policy_line(self, v: dict) -> str:
        """One-sentence summary of a deposit or card-hold policy, for the guest."""
        p = v["policy"]
        if p["mode"] == "deposit":
            return (f"£{p['deposit_pp_gbp']}pp deposit taken at booking; non-refundable within "
                    f"{p['cancellation_hours']}h.")
        return (f"Card held; £{p['cancellation_fee_pp_gbp']}pp charged for no-show or cancelling within "
                f"{p['cancellation_hours']}h.")

    # -- bookings -----------------------------------------------------------------
    def _maybe_fail_before(self) -> None:
        """Injected timeout before anything is written: nothing was booked."""
        if self.rng.random() < self.transient_error_rate / 2:
            raise BookingError("upstream_timeout", "The booking system timed out. Safe to retry with the same idempotency key.",
                                retryable=True)

    def _maybe_fail_after(self) -> None:
        """Injected timeout after the booking was written: it exists, but the caller never hears back."""
        if self.rng.random() < self.transient_error_rate / 2:
            raise BookingError("upstream_timeout", "The booking system timed out. Safe to retry with the same idempotency key.",
                                retryable=True)

    def _book(self, *, venue_id: str, date: str, time: str, party_size: int, member_id: str, guest_name: str,
             idempotency_key: str, special_requests: str | None = None, policy_acknowledged: bool = False) -> dict:
        existing = self.db.one("SELECT * FROM mock_bookings WHERE idempotency_key = ?", (idempotency_key,))
        if existing:
            return {**self._booking_view(existing), "replayed": True}
        v = self._venue(venue_id)
        d, t = _parse_date(date), _parse_time(time)
        self._check_bookable(v, d, party_size)
        if v["policy"]["mode"] != "instant" and not policy_acknowledged:
            raise BookingError("policy_acknowledgement_required",
                                f"{v['name']}: {self._policy_line(v)} The guest must accept this first.",
                                policy=self._policy_line(v))
        self._maybe_fail_before()
        self._check_slot(v, d, t)
        if datetime.combine(d, t, self.tz) < datetime.now(self.tz) + timedelta(minutes=60):
            raise BookingError("too_late", "Online booking closes an hour before the reservation time.")
        raced = self.rng.random() < self.slot_race_rate
        if raced or not self._free(v, d, t, party_size):
            alt = self._availability(venue_id, date, party_size, around=time, window_minutes=90)["available_times"]
            raise BookingError("slot_unavailable", f"{_fmt(t)} is no longer available at {v['name']}.",
                                alternatives=alt)
        deposit = v["policy"]["deposit_pp_gbp"] * party_size
        ts = now_iso()
        row = {"id": new_id(), "reference": "HC-" + secrets.token_hex(3).upper(), "idempotency_key": idempotency_key,
               "venue_id": v["id"], "member_id": member_id, "guest_name": guest_name, "date": d.isoformat(),
               "time": _fmt(t), "party_size": party_size, "special_requests": special_requests or None,
               "status": "confirmed", "deposit_gbp": deposit, "created_at": ts, "updated_at": ts}
        try:
            self.db.execute(
                "INSERT INTO mock_bookings (id, reference, idempotency_key, venue_id, member_id, guest_name, date, time, "
                "party_size, special_requests, status, deposit_gbp, created_at, updated_at) VALUES (:id, :reference, "
                ":idempotency_key, :venue_id, :member_id, :guest_name, :date, :time, :party_size, :special_requests, "
                ":status, :deposit_gbp, :created_at, :updated_at)", row)
        except Exception:  # concurrent replay of the same key
            existing = self.db.one("SELECT * FROM mock_bookings WHERE idempotency_key = ?", (idempotency_key,))
            if existing:
                return {**self._booking_view(existing), "replayed": True}
            raise
        self._maybe_fail_after()  # booking exists, but the caller doesn't hear back
        return self._booking_view(row)

    def _get(self, reference: str) -> dict:
        row = self.db.one("SELECT * FROM mock_bookings WHERE reference = ?", (reference,))
        if not row:
            raise BookingError("unknown_booking", f"No booking with reference {reference!r}")
        return row

    def _bookings_for_member(self, member_id: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM mock_bookings WHERE member_id = ? ORDER BY date, time", (member_id,))
        return [self._booking_view(r) for r in rows]

    def _modify(self, reference: str, *, date: str | None = None, time: str | None = None,
               party_size: int | None = None, special_requests: str | None = None) -> dict:
        row = self._get(reference)
        if row["status"] != "confirmed":
            raise BookingError("not_modifiable", f"Booking {reference} is {row['status']}.")
        v = self._venue(row["venue_id"])
        nd, nt = date or row["date"], time or row["time"]
        np = party_size or row["party_size"]
        d, t = _parse_date(nd), _parse_time(nt)
        self._check_bookable(v, d, np)
        slot_changed = (nd, _fmt(t), np) != (row["date"], row["time"], row["party_size"])
        if slot_changed:
            self._check_slot(v, d, t)
            if not self._free(v, d, t, np, exclude_ref=reference):
                alt = self._availability(v["id"], nd, np, around=nt, window_minutes=90, exclude_ref=reference)
                raise BookingError("slot_unavailable", f"{_fmt(t)} on {nd} is not available for {np} at {v['name']}. "
                                    "The original booking is unchanged.", alternatives=alt["available_times"])
        deposit = v["policy"]["deposit_pp_gbp"] * np
        self.db.execute(
            "UPDATE mock_bookings SET date = ?, time = ?, party_size = ?, special_requests = ?, deposit_gbp = ?, "
            "updated_at = ? WHERE reference = ?",
            (d.isoformat(), _fmt(t), np, special_requests if special_requests is not None else row["special_requests"],
             deposit, now_iso(), reference))
        return self._booking_view(self._get(reference))

    def _cancellation_quote(self, reference: str) -> dict:
        """What cancelling now would cost: inside the window the deposit or card-hold fee is charged."""
        row = self._get(reference)
        v = self._venue(row["venue_id"])
        start = datetime.combine(_parse_date(row["date"]), _parse_time(row["time"]), self.tz)
        hours = (start - datetime.now(self.tz)).total_seconds() / 3600
        p = v["policy"]
        inside = hours < p["cancellation_hours"]
        fee = 0
        if inside:
            fee = row["deposit_gbp"] if p["mode"] == "deposit" else p["cancellation_fee_pp_gbp"] * row["party_size"]
        return {"reference": reference, "hours_until": round(hours, 1), "inside_window": inside,
                "fee_gbp": fee, "refund_gbp": 0 if inside else row["deposit_gbp"]}

    def _cancel(self, reference: str, accept_fee: bool = False) -> dict:
        row = self._get(reference)
        if row["status"] == "cancelled":
            return {**self._booking_view(row), "already_cancelled": True}
        quote = self._cancellation_quote(reference)
        if quote["fee_gbp"] and not accept_fee:
            raise BookingError("fee_acknowledgement_required",
                                f"Cancelling now incurs a £{quote['fee_gbp']} charge. The guest must accept this first.",
                                quote=quote)
        self.db.execute("UPDATE mock_bookings SET status = 'cancelled', updated_at = ? WHERE reference = ?",
                        (now_iso(), reference))
        return {**self._booking_view(self._get(reference)), "cancellation": quote}

    def _booking_view(self, row: dict) -> dict:
        """A booking as returned to callers: venue name, weekday and terms added, internal ids left out."""
        v = VENUES_BY_ID[row["venue_id"]]
        return {"reference": row["reference"], "status": row["status"], "venue_id": v["id"], "venue_name": v["name"],
                "area": v["area"], "date": row["date"],
                "weekday": DAYS[_parse_date(row["date"]).weekday()], "time": row["time"],
                "party_size": row["party_size"], "guest_name": row["guest_name"],
                "special_requests": row["special_requests"], "deposit_gbp": row["deposit_gbp"],
                **self._cancellation_terms(v, row)}

    def _cancellation_terms(self, v: dict, row: dict) -> dict:
        """The cancellation terms of a booking as of now, worked out here so nobody else has to.

        Returns `cancellation` (a sentence to pass on to the guest) and
        `free_cancellation_until` (local time, or None if there is no deadline or it has passed).
        """
        p = v["policy"]
        start = datetime.combine(_parse_date(row["date"]), _parse_time(row["time"]), self.tz)
        if row["status"] != "confirmed":
            return {"cancellation": None, "free_cancellation_until": None}
        if not p["cancellation_hours"]:
            return {"cancellation": "Free to cancel any time before the booking.", "free_cancellation_until": None}
        deadline = start - timedelta(hours=p["cancellation_hours"])
        if p["mode"] == "deposit":
            cost = f"the £{row['deposit_gbp']} deposit is kept"
        else:
            cost = f"£{p['cancellation_fee_pp_gbp'] * row['party_size']} is charged (£{p['cancellation_fee_pp_gbp']} per person)"
        if self._now() < deadline:
            return {"cancellation": f"Free to cancel until {_human(deadline)}. After that, or for a no-show, {cost}.",
                    "free_cancellation_until": deadline.strftime("%Y-%m-%d %H:%M")}
        return {"cancellation": f"Already inside the {p['cancellation_hours']}-hour cancellation window: "
                                f"if cancelled or a no-show, {cost}.",
                "free_cancellation_until": None}

    # -- the BookingProvider interface --------------------------------------------
    async def search(self, *, date: str, party_size: int, time: str | None = None, area: str | None = None,
                     cuisine: str | None = None, keywords: str | None = None, dietary: str | None = None,
                     max_price: str | None = None, step_free: bool | None = None, limit: int = 6) -> dict:
        await self._network()
        return self._search(date=date, party_size=party_size, time=time, area=area, cuisine=cuisine,
                            keywords=keywords, dietary=dietary, max_price=max_price, step_free=step_free, limit=limit)

    async def venue(self, venue_id: str) -> dict:
        await self._network()
        return self._public_venue(self._venue(venue_id))

    async def availability(self, venue_id: str, date: str, party_size: int, time: str | None = None) -> dict:
        await self._network()
        return self._availability(venue_id, date, party_size, around=time)

    async def book(self, *, venue_id: str, date: str, time: str, party_size: int, member_id: str,
                   guest_name: str, idempotency_key: str, special_requests: str | None = None,
                   policy_acknowledged: bool = False) -> dict:
        await self._network()
        return self._book(venue_id=venue_id, date=date, time=time, party_size=party_size, member_id=member_id,
                          guest_name=guest_name, idempotency_key=idempotency_key,
                          special_requests=special_requests, policy_acknowledged=policy_acknowledged)

    async def bookings_for_member(self, member_id: str) -> list[dict]:
        await self._network()
        return self._bookings_for_member(member_id)

    async def modify(self, reference: str, *, date: str | None = None, time: str | None = None,
                     party_size: int | None = None, special_requests: str | None = None) -> dict:
        await self._network()
        return self._modify(reference, date=date, time=time, party_size=party_size,
                            special_requests=special_requests)

    async def cancel(self, reference: str, accept_fee: bool = False) -> dict:
        await self._network()
        return self._cancel(reference, accept_fee)
