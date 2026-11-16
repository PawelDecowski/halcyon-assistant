"""The mock booking provider behaves like the third party it stands in for."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.booking.interface import BookingError
from app.booking.mock.provider import MockBookingProvider
from app.booking.mock.venues import VENUES
from app.db import Database

LONDON = ZoneInfo("Europe/London")


def run(coro):
    return asyncio.run(coro)


def day(weekday: int, weeks_ahead: int = 1) -> str:
    """A date of the given weekday (Mon=0) at least a week out, so tests never hit 'too late'."""
    d = datetime.now(LONDON).date() + timedelta(days=7 * weeks_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.isoformat()


@pytest.fixture
def p(tmp_path):
    return MockBookingProvider(Database(str(tmp_path / "t.db")), tz=LONDON)


def book_args(**kw):
    args = dict(venue_id="v-casa-lucia", date=day(0), time="19:30", party_size=2, member_id="m",
                guest_name="Jane Doe", idempotency_key="key-1")
    return {**args, **kw}


# -- availability ---------------------------------------------------------------------

def test_availability_is_stable(p):
    a = run(p.availability("v-ember-and-oak", day(4), 2, time="20:00"))
    b = run(p.availability("v-ember-and-oak", day(4), 2, time="20:00"))
    assert a == b


def test_prime_time_is_scarcer_than_off_peak(p):
    def share_free_at(d, t):
        """Of the venues that seat at exactly t on d, the share with a table free."""
        seating = [v for v in VENUES if any(a <= t <= b for a, b in v["seatings"].get(
            ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][datetime.fromisoformat(d).weekday()], []))]
        free = [v for v in seating if t in run(p.availability(v["id"], d, 2, time=t))["available_times"]]
        return len(free) / len(seating)
    saturday_8pm = sum(share_free_at(day(5, w), "20:00") for w in (1, 2, 3))
    tuesday_530pm = sum(share_free_at(day(1, w), "17:30") for w in (1, 2, 3))
    assert saturday_8pm < tuesday_530pm
    hard = run(p.availability("v-kiku-counter", day(4), 2, time="20:30"))["available_times"]
    easy = run(p.availability("v-anatolia", day(1), 2, time="17:30"))["available_times"]
    assert len(easy) > len(hard)


def test_closed_day(p):
    r = run(p.availability("v-the-chancery", day(6), 2))  # closed weekends
    assert r["open"] is False and "closed" in r["message"]
    with pytest.raises(BookingError) as e:
        run(p.book(**book_args(venue_id="v-the-chancery", date=day(6), time="19:00")))
    assert e.value.code == "invalid_slot"


def test_party_too_large_mentions_private_dining(p):
    with pytest.raises(BookingError) as e:
        run(p.availability("v-jade-pavilion", day(2), 20))
    assert e.value.code == "party_too_large" and e.value.extra["private_dining_max"] == 24


def test_past_and_far_future_dates(p):
    yesterday = (datetime.now(LONDON).date() - timedelta(days=1)).isoformat()
    with pytest.raises(BookingError) as e:
        run(p.availability("v-casa-lucia", yesterday, 2))
    assert e.value.code == "date_in_past"
    far = (datetime.now(LONDON).date() + timedelta(days=200)).isoformat()
    with pytest.raises(BookingError) as e:
        run(p.availability("v-casa-lucia", far, 2))
    assert e.value.code == "too_far_ahead"


def test_search_filters_and_reports_unavailability(p):
    r = run(p.search(date=day(2), party_size=2, dietary="vegan", area="Shoreditch"))
    assert r["results"] and all(x["area"] == "Shoreditch" for x in r["results"])
    r = run(p.search(date=day(2), party_size=2, area="Atlantis"))
    assert r["results"] == [] and "Soho" in r["known_areas"]
    r = run(p.search(date=day(2), party_size=2, cuisine="omakase", time="20:30"))
    kiku = r["results"][0]
    assert kiku["id"] == "v-kiku-counter" and "policy_note" in kiku


# -- booking --------------------------------------------------------------------------

def test_book_confirms_and_uses_a_table(p):
    b = run(p.book(**book_args()))
    assert b["status"] == "confirmed" and b["reference"].startswith("HC-")
    assert run(p.bookings_for_member("m"))[0]["reference"] == b["reference"]


def test_same_key_returns_the_original_booking(p):
    first = run(p.book(**book_args()))
    again = run(p.book(**book_args()))
    assert again["reference"] == first["reference"] and again["replayed"] is True
    assert len(run(p.bookings_for_member("m"))) == 1


def test_timeout_after_write_then_retry_does_not_double_book(p):
    """The booking is written but the caller only sees a timeout."""
    def timeout_once():
        p._maybe_fail_after = lambda: None
        raise BookingError("upstream_timeout", "timed out", retryable=True)

    p._maybe_fail_after = timeout_once
    with pytest.raises(BookingError) as e:
        run(p.book(**book_args()))
    assert e.value.retryable
    retry = run(p.book(**book_args()))
    assert retry["status"] == "confirmed" and retry["replayed"] is True
    assert len(run(p.bookings_for_member("m"))) == 1


def test_injected_failures_are_retryable_and_never_duplicate(tmp_path):
    p = MockBookingProvider(Database(str(tmp_path / "f.db")), tz=LONDON, transient_error_rate=0.8,
                            rng=random.Random(7))
    result = None
    for _ in range(50):
        try:
            result = run(p.book(**book_args()))
            break
        except BookingError as e:
            assert e.code == "upstream_timeout" and e.retryable
    assert result and len(run(p.bookings_for_member("m"))) == 1


def test_slot_taken_offers_alternatives(tmp_path):
    p = MockBookingProvider(Database(str(tmp_path / "r.db")), tz=LONDON, slot_race_rate=1.0)
    with pytest.raises(BookingError) as e:
        run(p.book(**book_args()))
    assert e.value.code == "slot_unavailable" and isinstance(e.value.extra["alternatives"], list)
    assert run(p.bookings_for_member("m")) == []


def test_deposit_needs_acknowledgement(p):
    free = run(p.availability("v-maison-bastide", day(2), 2, time="19:00"))["available_times"]
    args = book_args(venue_id="v-maison-bastide", date=day(2), time=free[0])
    with pytest.raises(BookingError) as e:
        run(p.book(**args))
    assert e.value.code == "policy_acknowledgement_required" and "£60pp" in e.value.message
    b = run(p.book(**args, policy_acknowledged=True))
    assert b["deposit_gbp"] == 120


def test_full_venue_cannot_be_overbooked(p):
    """Keep booking one slot until the venue is full; the next attempt must fail."""
    made = 0
    with pytest.raises(BookingError) as e:
        for i in range(30):
            run(p.book(**book_args(venue_id="v-corvo", date=day(1), time="18:00", idempotency_key=f"k{i}")))
            made += 1
    assert e.value.code == "slot_unavailable" and 0 < made <= 7


# -- changes and cancellations ----------------------------------------------------------

def test_modify_in_place_and_failed_change_keeps_original(p):
    b = run(p.book(**book_args()))
    moved = run(p.modify(b["reference"], time="20:00", party_size=4))
    assert (moved["time"], moved["party_size"]) == ("20:00", 4)
    with pytest.raises(BookingError):
        run(p.modify(b["reference"], time="03:00"))
    assert run(p.bookings_for_member("m"))[0]["time"] == "20:00"


def test_late_cancellation_fee_needs_acknowledgement(p):
    # Jade Pavilion holds a card (£40pp late-cancellation fee). Off-peak lunch, so always free.
    b = p._book(**book_args(venue_id="v-jade-pavilion", date=day(0), time="12:00", policy_acknowledged=True))
    # Pretend the booking is now inside the cancellation window.
    p._cancellation_quote = lambda ref: {"reference": ref, "inside_window": True, "fee_gbp": 100,
                                         "refund_gbp": 0, "hours_until": 10}
    with pytest.raises(BookingError) as e:
        run(p.cancel(b["reference"]))
    assert e.value.code == "fee_acknowledgement_required"
    done = run(p.cancel(b["reference"], accept_fee=True))
    assert done["status"] == "cancelled" and done["cancellation"]["fee_gbp"] == 100
    assert run(p.cancel(b["reference"]))["already_cancelled"] is True


# -- allergens and cancellation terms (practice run 1) -----------------------------------

def test_coeliac_search_finds_only_fully_gluten_free_kitchens(p):
    r = run(p.search(date=day(4), party_size=2, dietary="coeliac"))
    assert r["results"] and all(x["allergens"] == "dedicated_gluten_free" for x in r["results"])
    assert "v-hearth-and-field" in {x["id"] for x in r["results"]}
    gf = run(p.search(date=day(4), party_size=2, dietary="gluten-free", limit=30))
    assert len(gf["results"]) > len(r["results"])  # "gluten-free options" is a much wider net


def test_venue_details_explain_allergen_handling(p):
    larder = run(p.venue("v-the-larder"))["allergens"]
    assert larder["handling"] == "shared_kitchen" and "cross-contamination is possible" in larder["detail"]
    assert "Coeliac UK accredited" in run(p.venue("v-hearth-and-field"))["allergens"]["detail"]


def test_cancellation_terms_are_worked_out_by_the_provider(p):
    free = run(p.book(**book_args()))
    assert free["cancellation"] == "Free to cancel any time before the booking."
    held = run(p.book(**book_args(venue_id="v-jade-pavilion", time="12:00", party_size=3, idempotency_key="k2",
                                  policy_acknowledged=True)))
    assert held["cancellation"].startswith("Free to cancel until ")
    assert "£120 is charged (£40 per person)" in held["cancellation"] and held["free_cancellation_until"]


def test_booking_inside_the_window_says_so(p):
    b = p._book(**book_args(venue_id="v-jade-pavilion", time="12:00", policy_acknowledged=True))
    booked_for = datetime.fromisoformat(f"{b['date']}T12:00").replace(tzinfo=LONDON)
    p._now = lambda: booked_for - timedelta(hours=3)  # three hours before: inside the 24-hour window
    b = run(p.bookings_for_member("m"))[0]
    assert b["cancellation"].startswith("Already inside the 24-hour cancellation window")
    assert b["free_cancellation_until"] is None
