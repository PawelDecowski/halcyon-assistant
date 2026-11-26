"""Stage 1 recommendations: compare a member's tags with each experience's tags as they are.

An item scores one point for every tag it shares with the member's likes.
Any tag the member dislikes leaves the item out. Ties go to the soonest
date, then the shortest travel. At most `per_category` items from one
category make the list, so one interest can't fill the page. Items sharing
no liked tag are not suggested: finding related interests is a later stage.

    recommend(["jazz", "wine"], ["lively"], EXPERIENCES, date(2026, 11, 26))
    -> [Suggestion(item=<Jazz Supper Club ...>, score=2, shared=["jazz", "wine"], ...), ...]
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.booking.mock.experiences.common import occurrences
from app.booking.mock.venues import DAYS

LOOK_AHEAD = timedelta(weeks=53)
"""How far ahead to look for an item's next date."""


@dataclass
class Suggestion:
    """One recommended item, why it was chosen, and when it next happens."""

    item: dict
    score: int
    shared: list[str]
    next_date: date

    @property
    def reason(self) -> str:
        """The line shown to the member, e.g. "Because you like jazz and wine"."""
        return "Because you like " + _join(self.shared)


def _join(words: list[str]) -> str:
    """Join words in plain English: ["a"] -> "a", ["a", "b", "c"] -> "a, b and c"."""
    words = [w.replace("-", " ") for w in words]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def next_date(item: dict, today: date) -> date | None:
    """The first day from `today` on which the item can be booked, or None within a year.

    Dated and slot items use their next start time; stays use the next day
    that is a check-in day in a month they are open.
    """
    booking = item["booking"]
    if booking["kind"] != "stay":
        found = occurrences(booking, today, today + LOOK_AHEAD)
        return found[0][0] if found else None
    day = today
    while day <= today + LOOK_AHEAD:
        if day.month in booking["months"] and DAYS[day.weekday()] in booking["check_in_days"]:
            return day
        day += timedelta(days=1)
    return None


def recommend(likes: list[str], dislikes: list[str], items: list[dict], today: date,
              limit: int = 12, per_category: int = 3) -> list[Suggestion]:
    """The best `limit` items for a member with these liked and disliked tags.

    The shared tags in each suggestion keep the order of `likes`, so the reason
    reads in the member's own order of interests.
    """
    disliked = set(dislikes)
    candidates = []
    for item in items:
        tags = set(item["tags"])
        if tags & disliked:
            continue
        shared = [tag for tag in likes if tag in tags]
        if not shared:
            continue
        when = next_date(item, today)
        if when is None:
            continue
        candidates.append(Suggestion(item=item, score=len(shared), shared=shared, next_date=when))
    candidates.sort(key=lambda s: (-s.score, s.next_date, s.item["travel_minutes"], s.item["id"]))

    chosen: list[Suggestion] = []
    per_category_count: dict[str, int] = {}
    for suggestion in candidates:
        category = suggestion.item["category"]
        if per_category_count.get(category, 0) >= per_category:
            continue
        chosen.append(suggestion)
        per_category_count[category] = per_category_count.get(category, 0) + 1
        if len(chosen) == limit:
            break
    return chosen
