"""The tag vocabulary is well formed."""
from __future__ import annotations

import re

from app.recommendations.tags import TAG_GROUPS, TAGS, unknown_tags


def test_every_tag_is_in_exactly_one_group():
    """A tag listed in two groups would silently keep only one definition."""
    total = sum(len(group) for group in TAG_GROUPS.values())
    assert total == len(TAGS)


def test_tags_are_lowercase_slugs_with_definitions():
    """Tags are compared as exact strings, so they follow one spelling rule."""
    for tag, definition in TAGS.items():
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", tag), tag
        assert definition.strip(), tag


def test_unknown_tags_lists_only_tags_outside_the_vocabulary():
    """Valid tags pass; anything else comes back in order."""
    assert unknown_tags(["jazz", "craftsmanship"]) == []
    assert unknown_tags(["loud", "jazz", "date night"]) == ["loud", "date night"]


def test_sample_profiles_use_only_known_tags():
    """A misspelt tag in a profile would silently match nothing."""
    from app.recommendations.sample_profiles import SAMPLE_PROFILES

    assert len({p["id"] for p in SAMPLE_PROFILES}) == len(SAMPLE_PROFILES)
    for profile in SAMPLE_PROFILES:
        assert unknown_tags(profile["likes"] + profile["dislikes"]) == [], profile["id"]
        assert not set(profile["likes"]) & set(profile["dislikes"]), profile["id"]
        assert profile["notes"], profile["id"]
