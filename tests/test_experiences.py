"""The mock experience catalogue is well formed and its dates behave."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pytest

from app.booking.mock.experiences import EXPERIENCES
from app.booking.mock.experiences.common import CATEGORIES, EPOCH, REGIONS, SCALES, dated, occurrences, once_a_year, shows
from app.booking.mock.venues import DAYS
from app.recommendations.tags import unknown_tags

POLICY_MODES = {"instant", "deposit", "card_hold", "prepaid", "stay_deposit"}


def test_about_fifty_items_in_every_category():
    """The agreed size: about 50 per category, and no category left out."""
    counts = Counter(item["category"] for item in EXPERIENCES)
    assert set(counts) == set(CATEGORIES)
    for category, count in counts.items():
        assert 45 <= count <= 55, (category, count)


def test_ids_and_names_are_unique():
    """Bookings and suggestions refer to items by id, and members by name."""
    ids = [item["id"] for item in EXPERIENCES]
    names = [item["name"] for item in EXPERIENCES]
    assert len(ids) == len(set(ids))
    assert len(names) == len(set(names))


@pytest.mark.parametrize("item", EXPERIENCES, ids=lambda item: item["id"])
def test_item_fields_are_valid(item):
    """Every item has 3 to 6 known tags and sensible values in every field."""
    assert 3 <= len(item["tags"]) <= 6 and len(set(item["tags"])) == len(item["tags"])
    assert unknown_tags(item["tags"]) == []
    assert item["region"] in REGIONS
    assert item["price_gbp"] > 0 and item["price_per"] in {"person", "night", "booking"}
    assert 0 <= item["demand"] <= 1 and item["max_party"] >= 1 and item["travel_minutes"] >= 0
    assert item["policy"]["mode"] in POLICY_MODES
    assert item["description"].strip()
    assert item["scale"] in SCALES
    booking = item["booking"]
    assert all(1 <= month <= 12 for month in booking["months"])
    if booking["kind"] == "stay":
        assert item["price_per"] == "night"
        assert set(booking["check_in_days"]) <= set(DAYS)
    else:
        assert set(booking["times"]) <= set(DAYS) and booking["times"]
    if item["category"] == "meeting stars":
        assert item["star"]["name"] and item["star"]["known_for"]


@pytest.mark.parametrize("item", [i for i in EXPERIENCES if i["booking"]["kind"] != "stay"],
                         ids=lambda item: item["id"])
def test_every_dated_or_slot_item_happens_within_a_year(item):
    """An item with no date in the next 53 weeks could never be suggested or booked."""
    start = date(2026, 11, 26)
    assert occurrences(item["booking"], start, start + timedelta(weeks=53))


def test_a_cycle_keeps_its_dates_fixed():
    """A concert in week 2 of every 6 falls on the same dates whichever day we ask from."""
    booking = dated(shows("fri", "19:30"), seats=10, every_weeks=6, week=2)
    friday = EPOCH + timedelta(weeks=2, days=4)
    assert occurrences(booking, EPOCH, EPOCH + timedelta(weeks=6)) == [(friday, "19:30")]
    later = friday + timedelta(weeks=6)
    assert occurrences(booking, friday + timedelta(days=1), later) == [(later, "19:30")]


def test_months_limit_when_an_item_runs():
    """A December-only show has no dates in July and some in December."""
    booking = dated(shows("sat", "19:00"), seats=10, months=[12])
    assert occurrences(booking, date(2026, 7, 1), date(2026, 7, 31)) == []
    assert occurrences(booking, date(2026, 12, 1), date(2026, 12, 31))


def test_a_day_range_cannot_wrap_past_sunday():
    """shows("sun-mon") would silently be an empty week."""
    with pytest.raises(ValueError):
        shows("sun-mon", "19:00")


def test_scale_3_is_kept_for_the_few():
    """Once in a lifetime has to be rare to mean anything: under a tenth of the catalogue."""
    threes = [item for item in EXPERIENCES if item["scale"] == 3]
    assert 0 < len(threes) <= len(EXPERIENCES) // 10


def test_once_a_year_has_exactly_one_date_in_any_twelve_months():
    """A one-off stays a one-off whichever day the catalogue is looked at."""
    booking = once_a_year("sat", "19:30", week=48, seats=10)
    for offset in range(0, 400, 37):
        start = date(2026, 1, 1) + timedelta(days=offset)
        assert len(occurrences(booking, start, start + timedelta(days=364))) == 1


def test_the_catalogue_has_one_offs_where_they_belong():
    """Concerts, theatre and meeting stars each have single-date events; some are coming up soon."""
    start = date(2026, 11, 26)
    one_offs = [item for item in EXPERIENCES if item["booking"]["kind"] != "stay"
                and len(occurrences(item["booking"], start, start + timedelta(days=364))) == 1]
    assert {"concerts", "theatre", "meeting stars"} <= {item["category"] for item in one_offs}
    assert any(occurrences(item["booking"], start, start + timedelta(weeks=4)) for item in one_offs)
