"""Stage 1 scoring: tag matching, realistic dates for distance, and urgency."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.booking.mock.experiences import EXPERIENCES
from app.booking.mock.experiences.common import (dated, experience, once_a_year, prepaid, shows, slots, stay,
                                                 stay_deposit)
from app.recommendations.sample_profiles import SAMPLE_PROFILES
from app.recommendations.scoring import ANY_DAY, ONE_OFF, available_dates, is_last_chance, pattern, recommend

TODAY = date(2026, 11, 26)  # a Thursday, in week 46 of the 52-week cycle
TAGS = ["jazz", "art", "quiet"]


def item(id: str, tags: list[str] = TAGS, category: str = "concerts", travel: int = 20, scale: int = 1,
         booking: dict | None = None) -> dict:
    """A small catalogue item for tests: by default, a Friday concert every week."""
    return experience(category, id, id, scale=scale, place="Soho", region="London", travel=travel, price=50,
                      tags=tags, text="Test item.", booking=booking or dated(shows("fri", "19:30"), seats=10),
                      max_party=4, demand=0.5, policy=prepaid(None))


def ids(suggestions) -> list[str]:
    """The item ids of a list of suggestions, in order."""
    return [s.item["id"] for s in suggestions]


def offered(it: dict) -> date | None:
    """The date the scorer would offer this item to a member who likes jazz, or None if it's left out."""
    found = recommend(["jazz"], [], [it], TODAY)
    return found[0].next_date if found else None


# Matching

def test_more_shared_likes_rank_higher():
    """Two shared tags beat one; an item sharing none is not suggested."""
    items = [item("one", ["jazz", "golf", "art"]), item("two", ["jazz", "wine", "art"]),
             item("none", ["golf", "art", "drama"])]
    assert ids(recommend(["jazz", "wine"], [], items, TODAY)) == ["two", "one"]


def test_a_quality_counts_half_and_alone_is_not_enough():
    """One shared quality is left out; two count as much as one interest; an interest plus a quality beats both."""
    items = [item("groups-only", ["drama", "art", "groups"]), item("two-qualities", ["drama", "intimate", "romantic"]),
             item("interest", ["jazz", "art", "drama"]), item("interest-and-quality", ["jazz", "art", "intimate"])]
    found = recommend(["jazz", "groups", "intimate", "romantic"], [], items, TODAY)
    assert {s.item["id"]: s.score for s in found} == {"interest-and-quality": 1.5, "interest": 1.0, "two-qualities": 1.0}
    assert found[0].item["id"] == "interest-and-quality"


def test_any_disliked_tag_leaves_an_item_out():
    """A member who dislikes lively places never sees one, however well it matches."""
    items = [item("lively-jazz", ["jazz", "wine", "lively"]), item("quiet-jazz", ["jazz", "quiet", "art"])]
    assert ids(recommend(["jazz", "wine"], ["lively"], items, TODAY)) == ["quiet-jazz"]


def test_variety_first_then_held_back_items_fill_a_short_list():
    """The fourth concert gives way to a weaker play, then still fills the list, which is short."""
    concerts = [item(f"concert-{n}", ["jazz", "wine", "art"]) for n in range(4)]
    play = item("play", ["jazz", "drama", "quiet"], category="theatre")
    assert ids(recommend(["jazz", "wine"], [], concerts + [play], TODAY)) == [
        "concert-0", "concert-1", "concert-2", "play", "concert-3"]
    assert ids(recommend(["jazz", "wine"], [], concerts + [play], TODAY, limit=4)) == [
        "concert-0", "concert-1", "concert-2", "play"]


def test_reason_names_interests_first_then_qualities():
    """Interests in the member's order, in plain words; qualities follow, or stand alone."""
    [both] = recommend(["wine", "intimate", "fine-dining"], [], [item("x", ["fine-dining", "wine", "intimate"])], TODAY)
    assert both.reason == "Because you like wine and fine dining; it's also intimate"
    [qualities] = recommend(["intimate", "groups"], [], [item("y", ["drama", "intimate", "groups"])], TODAY)
    assert qualities.reason == "It's intimate and good for groups, as you like"


# Realistic dates

def test_lead_time_is_the_physical_minimum_for_the_distance():
    """A weekly Friday concert: tomorrow in London; in Kyoto at scale 3, the first Friday at least 4 days away."""
    assert offered(item("london")) == date(2026, 11, 27)
    assert offered(item("kyoto", travel=840, scale=3)) == date(2026, 12, 4)


def test_a_far_treat_bookable_any_week_waits_two_months():
    """A scale-1 item 14 hours away, available every week, is offered as a trip in about two months."""
    far = offered(item("far-treat", travel=840, scale=1))
    assert far is not None and far >= TODAY + timedelta(days=60)


def test_a_far_one_off_soon_needs_to_be_exceptional():
    """A one-off in New York in 15 days: offered at scale 3, or scale 2 since it's a one-off; never at scale 1."""
    soon_one_off = once_a_year("fri", "18:00", week=48, seats=6)  # Friday 11 December 2026
    assert offered(item("star", travel=480, scale=3, booking=soon_one_off)) == date(2026, 12, 11)
    assert offered(item("special", travel=480, scale=2, booking=soon_one_off)) == date(2026, 12, 11)
    assert offered(item("dinner", travel=480, scale=1, booking=soon_one_off)) is None


def test_near_europe_soon_needs_scale_two_or_rarity():
    """Geneva next week: a scale-2 weekly tour is offered then; a scale-1 one waits beyond three weeks."""
    weekly = slots(shows("mon-fri", "10:00"), places=6, duration_minutes=120)
    assert offered(item("special", travel=240, scale=2, booking=weekly)) < TODAY + timedelta(weeks=3)
    assert offered(item("treat", travel=240, scale=1, booking=weekly)) >= TODAY + timedelta(weeks=3)


def test_a_stay_is_offered_on_a_check_in_day_in_an_open_month():
    """A summer villa with Saturday check-in is next offered on the first Saturday of May."""
    villa = experience("hotels and villas", "villa", "Villa", scale=1, place="Tuscany", region="Europe", travel=240,
                       price=3000, per="night", tags=["italian", "wine", "countryside"], text="Test villa.",
                       booking=stay(1, "villa", 10, min_nights=7, check_in_days="sat", months=[5, 6, 7, 8, 9]),
                       max_party=10, demand=0.5, policy=stay_deposit(50, 60))
    [suggestion] = recommend(["italian"], [], [villa], TODAY)
    assert suggestion.next_date == date(2027, 5, 1)
    assert suggestion.pattern == "Check-in on Saturdays, May to September, from Saturday 1 May"


# Urgency and order

def test_equal_scores_go_local_first_then_most_urgent_then_soonest():
    """Among equal matches: London before Geneva; a one-off before a weekly show."""
    weekly = item("weekly")
    one_off = item("one-off", booking=once_a_year("sat", "19:30", week=50, seats=10))
    geneva = item("geneva", travel=240, scale=2)
    assert ids(recommend(["jazz"], [], [geneva, weekly, one_off], TODAY)) == ["one-off", "weekly", "geneva"]


def test_last_few_dates_before_a_long_gap_are_a_last_chance():
    """A December-only show offered in its last weeks is a last chance; one that runs on into January is not."""
    december = [date(2026, 12, d) for d in (4, 11, 18, 25)] + [date(2027, 12, 3)]
    assert is_last_chance(december, date(2026, 12, 18))
    winter = [date(2026, 12, d) for d in (4, 11, 18, 25)] + [date(2027, 1, d) for d in (1, 8, 15)]
    assert not is_last_chance(winter, date(2026, 12, 4))


def test_urgency_labels_follow_the_dates():
    """A weekly show all year is 'any day'; a single date a year is a one-off."""
    [weekly] = recommend(["jazz"], [], [item("weekly")], TODAY)
    [single] = recommend(["jazz"], [], [item("single", booking=once_a_year("sat", "19:30", week=50, seats=10))],
                         TODAY)
    assert weekly.urgency == ANY_DAY and single.urgency == ONE_OFF
    assert single.pattern == "One date only: Saturday 26 December"


def test_patterns_describe_the_dates_in_words():
    """Weekly runs, cycles and seasons read as a member would say them."""
    winter = item("winter", booking=dated(shows("thu", "19:30"), seats=10, months=[1, 2, 3]))
    every_four = item("cycle", booking=dated(shows("fri-sat", "19:30"), seats=10, every_weeks=4, week=1))
    first = available_dates(winter, TODAY, TODAY + timedelta(days=365))[0]
    assert pattern(winter, ANY_DAY, first) == "Thursdays, January to March, next on Thursday 7 January"
    assert pattern(every_four, ANY_DAY, date(2026, 12, 4)).startswith("Every 4 weeks on Fridays and Saturdays")


# The real catalogue

@pytest.mark.parametrize("profile", SAMPLE_PROFILES, ids=lambda p: p["id"])
def test_every_sample_profile_gets_suggestions_it_would_accept(profile):
    """No disliked tag, a liked tag, items over 3 per category only at the end, and far and soon only when special."""
    suggestions = recommend(profile["likes"], profile["dislikes"], EXPERIENCES, TODAY)
    assert suggestions
    for s in suggestions:
        assert not set(s.item["tags"]) & set(profile["dislikes"])
        assert s.shared and set(s.shared) <= set(profile["likes"])
        if s.item["travel_minutes"] > 300:
            assert s.next_date >= TODAY + timedelta(days=4)
            if s.next_date < TODAY + timedelta(weeks=3):
                assert s.item["scale"] >= 2
    categories = [s.item["category"] for s in suggestions]
    seen: dict[str, int] = {}
    held_back_started = False
    for category in categories:
        seen[category] = seen.get(category, 0) + 1
        if seen[category] > 3:
            held_back_started = True
        else:
            assert not held_back_started, "a held-back item came before the variety-first part was used up"


def test_runs_of_days_and_yearly_runs_read_naturally():
    """Three days or more in a row become a range; a 52-week cycle is once a year."""
    residency = item("residency", booking=dated(shows("tue-sat", "19:00"), seats=10, every_weeks=8, week=3))
    kabuki = item("kabuki", booking=dated(shows("tue-sun", "18:30"), seats=10, every_weeks=52, week=40))
    assert pattern(residency, ANY_DAY, date(2026, 12, 29)).startswith("Every 8 weeks on Tuesdays to Saturdays")
    assert pattern(kabuki, ANY_DAY, date(2027, 10, 12)).startswith("Once a year, Tuesdays to Sundays")


def test_components_are_worked_out_for_every_suggestion():
    """A local scale-2 one-off three weeks away: full nearness, urgency and soonness, half scale."""
    one_off = item("one-off", scale=2, booking=once_a_year("sat", "19:30", week=49, seats=10))  # 19 December
    [suggestion] = recommend(["jazz", "art"], [], [one_off], TODAY)
    assert suggestion.components == {"match": 2.0, "nearness": 1.0, "urgency": 1.0, "scale": 0.5, "soonness": 1.0}


def test_soonness_falls_to_nothing_six_months_out():
    """A once-a-year show ten months away has no soonness left."""
    [suggestion] = recommend(["jazz"], [], [item("next-autumn", booking=once_a_year("sat", "19:30", week=40, seats=10))],
                             TODAY)
    assert suggestion.days_ahead > 182 and suggestion.components["soonness"] == 0.0


def test_the_sports_fan_is_not_padded_with_group_outings():
    """Items sharing only `groups` with the sports fan are no longer suggested."""
    fan = next(p for p in SAMPLE_PROFILES if p["id"] == "profile-sports-fan")
    for s in recommend(fan["likes"], fan["dislikes"], EXPERIENCES, TODAY):
        assert s.shared != ["groups"]


def test_the_sports_fan_gets_motor_racing_once_the_list_is_short():
    """Motor racing meetings were held back by the limit on meeting stars; a short list now includes them."""
    fan = next(p for p in SAMPLE_PROFILES if p["id"] == "profile-sports-fan")
    found = recommend(fan["likes"], fan["dislikes"], EXPERIENCES, TODAY)
    assert any("motorsport" in s.shared for s in found)


def test_interests_read_as_phrases():
    """Tags become words a member would use."""
    [suggestion] = recommend(["japanese", "walking-and-hiking"], [], [item("x", ["japanese", "walking-and-hiking", "art"])],
                             TODAY)
    assert suggestion.reason == "Because you like all things Japanese and walks"
