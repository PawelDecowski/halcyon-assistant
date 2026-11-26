"""Hand-written member profiles for building and checking recommendations.

These stand in for real members until notes are turned into tags. Each one
is a different kind of member, written from what such a member would say,
not from what the catalogue happens to contain. Each profile keeps the notes
it is based on, so the note-tagging step can later be checked against the
tags written here.

  id, name      e.g. "profile-jazz-and-wine", "Jazz and wine couple"
  notes         what the member said, as the assistant would save it
  likes         tags the notes support
  dislikes      tags the member said they don't want
"""
from __future__ import annotations

SAMPLE_PROFILES: list[dict] = [
    {
        "id": "profile-jazz-and-wine",
        "name": "Jazz and wine couple",
        "notes": ["Loves jazz, especially small clubs", "Big on wine, mostly French",
                  "Date nights with her partner", "Hates loud clubs and DJs"],
        "likes": ["jazz", "wine", "french", "intimate", "romantic"],
        "dislikes": ["lively", "electronic"],
    },
    {
        "id": "profile-outdoor-family",
        "name": "Outdoor family",
        "notes": ["Two children, 7 and 10", "Weekends outside: walks, beaches, animals",
                  "Nothing formal, nothing late"],
        "likes": ["family-friendly", "outdoors", "walking-and-hiking", "wildlife", "beach"],
        "dislikes": ["formal", "late-night"],
    },
    {
        "id": "profile-watch-collector",
        "name": "Watch collector who travels for work",
        "notes": ["Collects mechanical watches", "Often entertaining clients",
                  "Likes a serious restaurant", "Prefers small groups, not big events"],
        "likes": ["watches", "craftsmanship", "fine-dining", "business", "city-break"],
        "dislikes": ["groups"],
    },
    {
        "id": "profile-adventurer",
        "name": "Adventurous young professional",
        "notes": ["Wants to be doing something, not sitting", "Skis, sails, climbs",
                  "Goes out dancing", "Finds quiet, formal places boring"],
        "likes": ["adventurous", "mountains", "on-the-water", "electronic", "lively"],
        "dislikes": ["quiet", "formal"],
    },
    {
        "id": "profile-culture-lover",
        "name": "Retired culture lover",
        "notes": ["Opera and classical concerts", "Museums, old houses, gardens",
                  "Likes things calm", "No late nights, nothing strenuous"],
        "likes": ["classical", "opera", "history-and-heritage", "art", "gardens", "quiet"],
        "dislikes": ["late-night", "adventurous", "lively"],
    },
    {
        "id": "profile-sports-fan",
        "name": "Sports fan with friends",
        "notes": ["Huge football and F1 fan", "Goes out with a big group of mates",
                  "Steak over anything fancy", "Not into anything stuffy"],
        "likes": ["football", "motorsport", "sport", "steak-and-grill", "groups"],
        "dislikes": ["formal"],
    },
    {
        "id": "profile-little-known",
        "name": "Member we know little about",
        "notes": ["Likes Japanese food"],
        "likes": ["japanese"],
        "dislikes": [],
    },
]
"""Seven kinds of member, the last one with almost nothing on file."""
