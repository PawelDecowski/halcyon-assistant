"""Stage 1 recommendations: one list, matched on tags, made realistic by distance and timing.

Matching: an item scores 1 for every interest it shares with the member's likes
and 0.5 for every quality (atmosphere, format, occasion), and needs at least
MIN_MATCH to be suggested: one quality alone says too little. A member we
know little about gets a short list rather than a padded one. Any disliked tag leaves it out; an item sharing no liked tag is not
suggested (related interests are a later stage).

Realistic dates: nothing is suggested before it could sensibly be reached
(LEAD_DAYS by distance). A far-away item soon has to be worth it: the bar
depends on its hand-written scale and its rarity (how many dates it has in
the next year). An item that doesn't pass soon is offered at its first later
date that does, so a hotel in Kyoto appears as a trip in a couple of months,
not tonight. Each item is one suggestion, however many dates it has.

Order: the tag match, then the shorter journey, then the more urgent
(one-off, last chance, rare, occasional, any day), then the sooner date. Every
suggestion also carries its components (match, nearness, urgency, scale,
soonness), and ordering lives in rank_key() alone, so a weighted score can
replace it later without changing anything else. At most `per_category`
items from one category make the list while others can fill it; if the list
is still short, the best items held back by that limit fill it.

    recommend(["jazz", "wine"], ["lively"], EXPERIENCES, date(2026, 11, 26))
    -> [Suggestion(item=<Jazz Supper Club ...>, score=2, shared=["jazz", "wine"], ...), ...]
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.booking.mock.experiences.common import occurrences
from app.booking.mock.venues import DAYS
from app.recommendations.tags import INTEREST_PHRASES, QUALITIES, QUALITY_PHRASES, tag_weight

MIN_MATCH = 1.0
"""The smallest match suggested: one shared interest, or two shared qualities."""

YEAR = timedelta(days=365)

# Distance bands, by travel time from central London in minutes.
LOCAL, DAY_TRIP, NEAR, FAR = "local", "day trip", "near", "far"
LEAD_DAYS = {LOCAL: 0, DAY_TRIP: 1, NEAR: 2, FAR: 4}
"""The earliest an item is suggested, in days from today, by distance band.

These are physical minimums (time to arrange travel), not a judgement of
whether a trip is worth it: that is the bar in worth_it(). Meeting a star in
New York next week can be suggested; a dinner there cannot."""

SOON = timedelta(weeks=3)
FAIRLY_SOON = timedelta(days=60)
"""A date within SOON, or within FAIRLY_SOON, of today raises the bar for distant items."""

# Rarity, from the number of dates in the next 12 months.
ONE_OFF, RARE, OCCASIONAL, ANY_DAY = "one-off", "rare", "occasional", "any day"
LAST_CHANCE = "last chance"
LAST_CHANCE_GAP = timedelta(weeks=8)
"""A run with at most 3 dates left before a gap this long is in its last few dates."""

# Components of a suggestion, each from 0 to 1 except match. Today they only break
# ties (see rank_key); a weighted score would add them up. Values are a first version.
NEARNESS = {LOCAL: 1.0, DAY_TRIP: 0.7, NEAR: 0.4, FAR: 0.2}
URGENCY_VALUE = {ONE_OFF: 1.0, LAST_CHANCE: 0.8, RARE: 0.6, OCCASIONAL: 0.3, ANY_DAY: 0.0}
SCALE_VALUE = {1: 0.0, 2: 0.5, 3: 1.0}
SOON_DAYS, DISTANT_DAYS = 28, 182
"""Soonness is 1 for a date within SOON_DAYS, falling evenly to 0 at DISTANT_DAYS."""

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
DAY_NAMES = {"mon": "Mondays", "tue": "Tuesdays", "wed": "Wednesdays", "thu": "Thursdays",
             "fri": "Fridays", "sat": "Saturdays", "sun": "Sundays"}


@dataclass
class Suggestion:
    """One recommended item: why it was chosen, the date offered, and how urgent it is.

    `next_date` is the date suggested to the member: the first realistic date
    that passes the bar for its distance, not necessarily the item's next date.
    """

    item: dict
    score: float
    shared: list[str]
    next_date: date
    urgency: str
    pattern: str
    days_ahead: int

    @property
    def components(self) -> dict[str, float]:
        """What the suggestion is made of, each worked out the same whichever ranking is used.

        {"match": 2, "nearness": 1.0, "urgency": 0.3, "scale": 0.5, "soonness": 1.0}. Match is the
        number of shared likes; the others run from 0 to 1. Urgency counts from the event date; once
        we can estimate when an item sells out, it should count from that book-by date instead.
        """
        soonness = min(1.0, max(0.0, (DISTANT_DAYS - self.days_ahead) / (DISTANT_DAYS - SOON_DAYS)))
        return {"match": float(self.score), "nearness": NEARNESS[band(self.item)],
                "urgency": URGENCY_VALUE[self.urgency], "scale": SCALE_VALUE[self.item["scale"]],
                "soonness": round(soonness, 3)}

    @property
    def reason(self) -> str:
        """The line shown to the member: interests first, then qualities.

        "Because you like jazz and wine", "Because you like jazz; it's also intimate",
        or for qualities alone "It's intimate and romantic, as you like".
        """
        interests = [INTEREST_PHRASES[tag] for tag in self.shared if tag not in QUALITIES]
        qualities = [QUALITY_PHRASES[tag] for tag in self.shared if tag in QUALITIES]
        if not interests:
            return f"It's {_join(qualities)}, as you like"
        line = "Because you like " + _join(interests)
        return line + (f"; it's also {_join(qualities)}" if qualities else "")


def _join(words: list[str]) -> str:
    """Join words in plain English: ["a"] -> "a", ["a", "b", "c"] -> "a, b and c"."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def band(item: dict) -> str:
    """The distance band of an item: local (up to 1 hour), day trip (3 hours), near (5 hours) or far."""
    minutes = item["travel_minutes"]
    return LOCAL if minutes <= 60 else DAY_TRIP if minutes <= 180 else NEAR if minutes <= 300 else FAR


def available_dates(item: dict, start: date, end: date) -> list[date]:
    """Every day from `start` to `end` inclusive on which the item can be booked, in order.

    Dated and slot items give their days with a start time; stays give the
    check-in days in the months they are open.
    """
    booking = item["booking"]
    if booking["kind"] != "stay":
        return sorted({day for day, _ in occurrences(booking, start, end)})
    days, day = [], start
    while day <= end:
        if day.month in booking["months"] and DAYS[day.weekday()] in booking["check_in_days"]:
            days.append(day)
        day += timedelta(days=1)
    return days


def rarity(dates_in_a_year: list[date]) -> str:
    """One-off (1 date in the next 12 months), rare (2 to 9), occasional (10 to 49) or any day (50 or more)."""
    count = len(dates_in_a_year)
    return ONE_OFF if count == 1 else RARE if count <= 9 else OCCASIONAL if count <= 49 else ANY_DAY


def is_last_chance(dates: list[date], offered: date) -> bool:
    """True if `offered` is among the last 3 dates of a run before a long gap.

    A winter run with two Thursdays left before it stops until next year is in
    its last few dates; the same run in its first week is not.
    """
    later = [d for d in dates if d >= offered]
    for index, day in enumerate(later[:3]):
        following = later[index + 1] if index + 1 < len(later) else None
        if following is None or following - day >= LAST_CHANCE_GAP:
            return True
    return False


def worth_it(item: dict, offered: date, today: date, how_rare: str) -> bool:
    """Whether a distant item is worth suggesting on this date.

    Local items and day trips always are. Near (UK far, near Europe) within 3
    weeks needs scale 2 or more, or a rare or one-off date. Far within 3 weeks
    needs scale 3, or scale 2 and a rare or one-off date; within 2 months it
    needs scale 2 or more, or a rare or one-off date. Later, anything is.
    """
    where, ahead, scale = band(item), offered - today, item["scale"]
    scarce = how_rare in (ONE_OFF, RARE)
    if where in (LOCAL, DAY_TRIP) or ahead >= FAIRLY_SOON:
        return True
    if where == NEAR:
        return ahead >= SOON or scale >= 2 or scarce
    if ahead < SOON:
        return scale == 3 or (scale == 2 and scarce)
    return scale >= 2 or scarce


def _month_ranges(months: list[int]) -> str:
    """Months in words, joining neighbours: [12, 1, 2, 3] -> "December to March"; all twelve -> ""."""
    if len(months) == 12:
        return ""
    chosen = set(months)
    starts = [m for m in sorted(chosen) if (m - 2) % 12 + 1 not in chosen]
    ranges = []
    for first in sorted(starts, key=lambda m: (m - starts[0]) % 12):
        last = first
        while last % 12 + 1 in chosen:
            last = last % 12 + 1
        ranges.append(MONTHS[first - 1] if first == last else f"{MONTHS[first - 1]} to {MONTHS[last - 1]}")
    return ", ".join(ranges)


def _day_list(days: list[str]) -> str:
    """Days in words, joining three or more in a row: ["tue", "wed", "thu", "sat"] -> "Tuesdays to Thursdays and Saturdays"."""
    if len(days) == 7:
        return "Every day"
    runs: list[list[str]] = []
    for day in days:
        if runs and DAYS.index(day) == DAYS.index(runs[-1][-1]) + 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    words = []
    for run in runs:
        if len(run) >= 3:
            words.append(f"{DAY_NAMES[run[0]]} to {DAY_NAMES[run[-1]]}")
        else:
            words += [DAY_NAMES[d] for d in run]
    return _join(words)


def pattern(item: dict, how_rare: str, offered: date) -> str:
    """When the item happens, in words, ending with the date offered.

    "One date only: Saturday 5 December", "Thursdays, January to March, next on Thursday 7 January",
    "Every 4 weeks on Fridays and Saturdays, next on ...", "Check-in on Saturdays, May to September, from ...".
    """
    offered_text = f"{offered.strftime('%A')} {offered.day} {MONTHS[offered.month - 1]}"
    if how_rare == ONE_OFF:
        return f"One date only: {offered_text}"
    booking = item["booking"]
    months = _month_ranges(booking["months"])
    if booking["kind"] == "stay":
        days = booking["check_in_days"]
        when = "Check-in any day" if len(days) == 7 else "Check-in on " + _day_list(days)
        return ", ".join(part for part in [when, months] if part) + f", from {offered_text}"
    days = [d for d in DAYS if d in booking["times"]]
    when = _day_list(days)
    if booking["kind"] == "dated" and booking["every_weeks"] == 52:
        when = f"Once a year, {when}"
    elif booking["kind"] == "dated" and booking["every_weeks"] > 1:
        when = f"Every {booking['every_weeks']} weeks on {when}"
    return ", ".join(part for part in [when, months] if part) + f", next on {offered_text}"


def suggest(item: dict, shared: list[str], today: date) -> Suggestion | None:
    """The suggestion for one matching item, or None if no date in the next year is realistic and worth it."""
    dates = available_dates(item, today, today + YEAR)
    if not dates:
        return None
    how_rare = rarity(dates)
    earliest = today + timedelta(days=LEAD_DAYS[band(item)])
    offered = next((d for d in dates if d >= earliest and worth_it(item, d, today, how_rare)), None)
    if offered is None:
        return None
    urgency = how_rare
    if how_rare in (OCCASIONAL, ANY_DAY) and is_last_chance(dates, offered):
        urgency = LAST_CHANCE
    return Suggestion(item=item, score=sum(tag_weight(tag) for tag in shared), shared=shared, next_date=offered, urgency=urgency,
                      pattern=pattern(item, how_rare, offered), days_ahead=(offered - today).days)


def rank_key(suggestion: Suggestion) -> tuple:
    """How suggestions are ordered: the one place to change to switch to a weighted score.

    Today: tag match first, then the nearer band, the more urgent, the sooner date.
    A weighted score would instead return (-sum(weight * value for each component), id).
    """
    c = suggestion.components
    return (-c["match"], -c["nearness"], -c["urgency"], suggestion.next_date,
            suggestion.item["travel_minutes"], suggestion.item["id"])


def recommend(likes: list[str], dislikes: list[str], items: list[dict], today: date,
              limit: int = 12, per_category: int = 3) -> list[Suggestion]:
    """The best `limit` items for a member with these liked and disliked tags, as of `today`.

    The shared tags in each suggestion keep the order of `likes`, so the reason
    reads in the member's own order of interests.
    """
    disliked = set(dislikes)
    candidates = []
    for item in items:
        tags = set(item["tags"])
        shared = [tag for tag in likes if tag in tags]
        if tags & disliked or sum(tag_weight(tag) for tag in shared) < MIN_MATCH:
            continue
        suggestion = suggest(item, shared, today)
        if suggestion:
            candidates.append(suggestion)
    candidates.sort(key=rank_key)

    chosen: list[Suggestion] = []
    held_back: list[Suggestion] = []
    per_category_count: dict[str, int] = {}
    for suggestion in candidates:
        category = suggestion.item["category"]
        if per_category_count.get(category, 0) >= per_category:
            held_back.append(suggestion)
            continue
        chosen.append(suggestion)
        per_category_count[category] = per_category_count.get(category, 0) + 1
        if len(chosen) == limit:
            return chosen
    # The limit is for variety, not to starve a short list: fill it with the best of what was held back.
    return chosen + held_back[: limit - len(chosen)]
