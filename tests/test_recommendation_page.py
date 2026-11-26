"""The recommendations review page: open, sample profiles only."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.assistant.llm import FakeLLM
from app.main import create_app
from app.recommendations.sample_profiles import SAMPLE_PROFILES


def client(tmp_path):
    """A test app; the page needs no token."""
    return TestClient(create_app(api_token="test-token", db_path=str(tmp_path / "t.db"), llm=FakeLLM(), burst_wait_s=0))


def test_profile_list_shows_every_sample_member_without_a_token(tmp_path):
    """The index is open and links to each profile."""
    with client(tmp_path) as c:
        page = c.get("/suggestions")
    assert page.status_code == 200
    for profile in SAMPLE_PROFILES:
        assert f'/suggestions/{profile["id"]}' in page.text


def test_a_profile_page_shows_ranked_cards_for_the_given_date(tmp_path):
    """The jazz couple's page lists suggestions with reasons and the date asked for."""
    with client(tmp_path) as c:
        page = c.get("/suggestions/profile-jazz-and-wine?today=2026-11-26")
    assert page.status_code == 200
    assert "Because you like" in page.text and "Thursday 26 November 2026" in page.text
    assert page.text.count('class="card"') >= 5


def test_real_member_ids_and_unknown_ids_are_not_found(tmp_path):
    """Only sample profiles are served, so a real member's preferences can never appear here."""
    with client(tmp_path) as c:
        assert c.get(f"/suggestions/{uuid.uuid4()}").status_code == 404
        assert c.get("/suggestions/profile-nobody").status_code == 404


def test_a_bad_date_is_refused(tmp_path):
    """The date override must be YYYY-MM-DD."""
    with client(tmp_path) as c:
        assert c.get("/suggestions/profile-jazz-and-wine?today=tomorrow").status_code == 422


def test_why_this_order_explains_the_points_and_the_tie_breaks_in_words():
    """The points are broken down by tag; the tie-breaks say what they mean for this item."""
    from datetime import date

    from app.booking.mock.experiences import EXPERIENCES
    from app.recommendations.page import _why_this_order
    from app.recommendations.scoring import recommend

    paddock = next(i for i in EXPERIENCES if i["id"] == "star-rafe-okonjo-paddock")
    [suggestion] = recommend(["motorsport", "special-occasion"], [], [paddock], date(2026, 11, 26))
    assert _why_this_order(suggestion) == [
        "1.5 points from your likes: motor racing 1.0, one for a special occasion 0.5 "
        "(interests count 1, qualities 0.5).",
        "Among items with equal points: nearer first (this is a day trip, up to 3 hours away), "
        "then rarer dates (this is a one-off), then sooner (next on Saturday 17 July)."]
