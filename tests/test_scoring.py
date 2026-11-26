"""Stage 1 scoring: shared liked tags, dislikes excluded, a limit per category."""
from __future__ import annotations

from datetime import date

import pytest

from app.booking.mock.experiences import EXPERIENCES
from app.booking.mock.experiences.common import dated, experience, prepaid, shows, stay, stay_deposit
from app.recommendations.sample_profiles import SAMPLE_PROFILES
from app.recommendations.scoring import next_date, recommend

TODAY = date(2026, 11, 26)


def item(id: str, tags: list[str], category: str = "concerts", travel: int = 20, week: int = 0) -> dict:
    """A small catalogue item for tests: a Friday event in the given week of every 4."""
    return experience(category, id, id, scale=1, place="Soho", region="London", travel=travel, price=50, tags=tags,
                      text="Test item.", booking=dated(shows("fri", "19:30"), seats=10, every_weeks=4, week=week),
                      max_party=4, demand=0.5, policy=prepaid(None))


def ids(suggestions) -> list[str]:
    """The item ids of a list of suggestions, in order."""
    return [s.item["id"] for s in suggestions]


def test_more_shared_likes_rank_higher():
    """Two shared tags beat one; an item sharing none is not suggested."""
    items = [item("one", ["jazz", "quiet", "art"]), item("two", ["jazz", "wine", "art"]), item("none", ["golf", "art", "quiet"])]
    assert ids(recommend(["jazz", "wine"], [], items, TODAY)) == ["two", "one"]


def test_any_disliked_tag_leaves_an_item_out():
    """A member who dislikes lively places never sees one, however well it matches."""
    items = [item("lively-jazz", ["jazz", "wine", "lively"]), item("quiet-jazz", ["jazz", "quiet", "art"])]
    assert ids(recommend(["jazz", "wine"], ["lively"], items, TODAY)) == ["quiet-jazz"]


def test_ties_go_to_the_soonest_date_then_the_shortest_travel():
    """Equal scores are ordered by next date, then by travel time."""
    by_week = {week: next_date(item("probe", ["jazz", "art", "quiet"], week=week), TODAY) for week in range(4)}
    soonest, latest = min(by_week, key=by_week.get), max(by_week, key=by_week.get)
    items = [item("later", ["jazz", "art", "quiet"], week=latest),
             item("sooner-far", ["jazz", "art", "quiet"], travel=60, week=soonest),
             item("sooner-near", ["jazz", "art", "quiet"], travel=10, week=soonest)]
    assert ids(recommend(["jazz"], [], items, TODAY)) == ["sooner-near", "sooner-far", "later"]


def test_one_category_cannot_fill_the_list():
    """With a limit of 3 per category, the fourth concert gives way to a lower-scoring play."""
    concerts = [item(f"concert-{n}", ["jazz", "wine", "art"]) for n in range(4)]
    play = item("play", ["jazz", "drama", "quiet"], category="theatre")
    assert ids(recommend(["jazz", "wine"], [], concerts + [play], TODAY)) == ["concert-0", "concert-1", "concert-2", "play"]


def test_reason_names_the_shared_tags_in_the_members_order():
    """The reason reads in the order the member's interests are listed."""
    [suggestion] = recommend(["wine", "jazz"], [], [item("x", ["jazz", "wine", "art"])], TODAY)
    assert suggestion.reason == "Because you like wine and jazz"
    [suggestion] = recommend(["fine-dining"], [], [item("y", ["fine-dining", "art", "quiet"])], TODAY)
    assert suggestion.reason == "Because you like fine dining"


def test_next_date_of_a_stay_is_its_next_check_in_day_in_an_open_month():
    """A summer villa with Saturday check-in next opens on the first Saturday in May."""
    villa = experience("hotels and villas", "villa", "Villa", scale=1, place="Tuscany", region="Europe", travel=240,
                       price=3000, per="night", tags=["italian", "wine", "countryside"], text="Test villa.",
                       booking=stay(1, "villa", 10, min_nights=7, check_in_days="sat", months=[5, 6, 7, 8, 9]),
                       max_party=10, demand=0.5, policy=stay_deposit(50, 60))
    assert next_date(villa, TODAY) == date(2027, 5, 1)


@pytest.mark.parametrize("profile", SAMPLE_PROFILES, ids=lambda p: p["id"])
def test_every_sample_profile_gets_suggestions_it_would_accept(profile):
    """No suggestion carries a disliked tag, each shares a liked one, and no category exceeds 3."""
    suggestions = recommend(profile["likes"], profile["dislikes"], EXPERIENCES, TODAY)
    assert suggestions
    for s in suggestions:
        assert not set(s.item["tags"]) & set(profile["dislikes"])
        assert s.shared and set(s.shared) <= set(profile["likes"])
    categories = [s.item["category"] for s in suggestions]
    assert max(categories.count(c) for c in categories) <= 3
