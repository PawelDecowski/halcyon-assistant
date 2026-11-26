"""Shared shape and helpers for the mock experience catalogue.

Every experience is a dict with the same fields, built by `experience()`:

  id, name, category   e.g. "con-halden-winter-strings", "Winter Strings", "concerts"
  place, region        where it happens; region is one of REGIONS
  travel_minutes       typical door-to-door time from central London
  description          one or two sentences in plain words
  scale                how much it is worth travelling or rearranging plans for, one of SCALES
  tags                 3 to 6 tags from app/recommendations/tags.py
  price_gbp, price_per what it costs: per "person", per "night" (stays) or per "booking"
  booking              how it is booked, from dated(), slots() or stay() below
  max_party            the largest party bookable in one booking
  demand               0..1, how popular it is: drives how scarce places are
  policy               booking terms, from the helpers below or venues.py
  step_free            True if there is step-free access throughout
  min_age              youngest age allowed, if there is a limit
  star                 meeting stars only: {"name": ..., "known_for": ...}
  notes                anything a member should know before booking

Three ways to book:
  dated   fixed dates and times with limited seats (concerts, theatre, stars).
          Dates repeat in a cycle counted from EPOCH, so a date never moves:
          it arrives, passes, and the next one in the cycle appears.
  slots   the same times every week with limited places (nature, watchmaking).
  stay    nights from a check-in date (hotels, villas), in rooms or a whole house.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.booking.mock.venues import DAYS

EPOCH = date(2026, 1, 5)
"""A fixed Monday from which every dated cycle is counted."""

CATEGORIES = ["concerts", "nature", "meeting stars", "theatre", "watchmaking", "hotels and villas"]
REGIONS = ["London", "UK", "Europe", "Further afield"]
SCALES = {
    1: "A treat: good, but there is always another (a dinner, a weekly tour, a hotel).",
    2: "Special: limited or notable (a premiere, a festival, a masterclass, a residency).",
    3: "Once in a lifetime: rare access or a rare moment (meeting a star, the migration from a balloon).",
}
"""Scale is written by hand for each item: price, demand and category measure other things."""


def shows(days: str, *times: str) -> dict[str, list[str]]:
    """Start times on a day or range of days.

    shows("tue-sat", "19:30") is an evening show Tuesday to Saturday;
    shows("sat", "14:30", "19:30") is a matinee and an evening show on Saturday.
    """
    first, _, last = days.partition("-")
    start = DAYS.index(first)
    stop = DAYS.index(last) if last else start
    if stop < start:
        raise ValueError(f"day range {days!r} wraps past Sunday; combine two shows() instead")
    return {day: list(times) for day in DAYS[start : stop + 1]}


def _merge(parts: tuple[dict[str, list[str]], ...]) -> dict[str, list[str]]:
    """Combine several shows() into one week, times sorted."""
    week: dict[str, list[str]] = {}
    for part in parts:
        for day, times in part.items():
            week[day] = sorted(set(week.get(day, []) + times))
    return week


ALL_YEAR = list(range(1, 13))


def dated(*parts: dict[str, list[str]], seats: int, every_weeks: int = 1, week: int = 0,
          months: list[int] | None = None) -> dict:
    """Fixed dates: the given week of every cycle of `every_weeks` weeks, counted from EPOCH.

    dated(shows("fri-sat", "19:30"), seats=40, every_weeks=6, week=2) is a Friday and
    Saturday concert in the third week of every six. every_weeks=1 is a long run:
    dated(shows("tue-sat", "19:30"), seats=30) plays every week. `months` limits it
    to part of the year: months=[12] is December only.
    """
    if not 0 <= week < every_weeks:
        raise ValueError("week must be inside the cycle")
    return {"kind": "dated", "times": _merge(parts), "every_weeks": every_weeks, "week": week, "seats": seats,
            "months": months or ALL_YEAR}


def once_a_year(day: str, time: str, *, week: int, seats: int) -> dict:
    """A single date a year: one day and time in the given week of a 52-week cycle from EPOCH.

    Any 12 months hold exactly one date, so it counts as a one-off whenever it is
    looked at, and the catalogue never runs out of one-offs.
    once_a_year("sat", "19:30", week=48, seats=20) is one Saturday in early December.
    """
    return dated(shows(day, time), seats=seats, every_weeks=52, week=week)


def slots(*parts: dict[str, list[str]], places: int, duration_minutes: int, months: list[int] | None = None) -> dict:
    """The same start times every week, in the given months (all year if none).

    slots(shows("tue-sat", "10:00", "14:00"), places=8, duration_minutes=180) is a
    three-hour tour twice a day, Tuesday to Saturday, for up to 8 people each time.
    """
    return {"kind": "slots", "times": _merge(parts), "places": places, "duration_minutes": duration_minutes,
            "months": months or ALL_YEAR}


def stay(units: int, unit: str, guests_per_unit: int, min_nights: int = 1,
         check_in_days: str | None = None, months: list[int] | None = None) -> dict:
    """Nights from a check-in date.

    stay(6, "suite", 2, min_nights=2) is six suites for two, two nights or more.
    stay(1, "villa", 10, min_nights=7, check_in_days="sat", months=[5, 6, 7, 8, 9])
    is a whole villa for ten, a week from Saturday, May to September.
    """
    days = list(shows(check_in_days)) if check_in_days else list(DAYS)
    return {"kind": "stay", "units": units, "unit": unit, "guests_per_unit": guests_per_unit,
            "min_nights": min_nights, "check_in_days": days, "months": months or ALL_YEAR}


def occurrences(booking: dict, start: date, end: date) -> list[tuple[date, str]]:
    """Every (date, start time) of a dated or slots booking from `start` to `end` inclusive.

    Stays have no start times; they return an empty list.
    """
    if booking["kind"] == "stay":
        return []
    found = []
    day = start
    while day <= end:
        times = booking["times"].get(DAYS[day.weekday()], [])
        if day.month not in booking["months"]:
            times = []
        elif booking["kind"] == "dated":
            week_number = (day - EPOCH).days // 7
            if week_number % booking["every_weeks"] != booking["week"]:
                times = []
        found += [(day, t) for t in times]
        day += timedelta(days=1)
    return found


def prepaid(refundable_until_hours: int | None) -> dict:
    """Paid in full when booking; refunded if cancelled more than the given hours before.

    prepaid(None) is never refundable, as with most concert and theatre tickets.
    """
    return {"mode": "prepaid", "refundable_until_hours": refundable_until_hours}


def stay_deposit(percent: int, refundable_until_days: int) -> dict:
    """A share of the stay is paid when booking and refunded if cancelled more than the given days before arrival."""
    return {"mode": "stay_deposit", "deposit_percent": percent, "refundable_until_days": refundable_until_days}


def experience(category: str, id: str, name: str, *, scale: int, place: str, region: str, travel: int, price: int,
               tags: list[str], text: str, booking: dict, max_party: int, demand: float, policy: dict,
               per: str = "person", step_free: bool = True, min_age: int | None = None,
               star: dict | None = None, notes: str | None = None) -> dict:
    """One catalogue item in the shape described at the top of this module.

    Each category module wraps this with its own category, for example
    concert = functools.partial(experience, "concerts").
    """
    if scale not in SCALES:
        raise ValueError(f"{id}: scale must be one of {sorted(SCALES)}")
    return {"id": id, "name": name, "category": category, "scale": scale, "place": place, "region": region,
            "travel_minutes": travel, "description": text, "tags": tags, "price_gbp": price,
            "price_per": per, "booking": booking, "max_party": max_party, "demand": demand,
            "policy": policy, "step_free": step_free, "min_age": min_age, "star": star, "notes": notes}
