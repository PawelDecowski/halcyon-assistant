"""The tag vocabulary shared by members, restaurants and experiences.

Tags describe taste, not logistics: price, place, travel time, party limits,
access, dress code, opening times, diets and allergens are fields on an item,
used to filter and rank, never tags. Many tags apply across categories on
purpose (`craftsmanship` fits a watch factory and an omakase counter), so a
member's preferences can reach experiences they have not booked before.

Cuisine tags mean the food or the culture: `japanese` can be on a dinner or on
a stay in a Japanese-style inn. Some tags exist mainly as the target of a
dislike: "nothing stuffy" becomes a negative `formal`.

Every tag has a one-line definition, which the note-tagging step and whoever
writes catalogue items both follow.
"""
from __future__ import annotations

TAG_GROUPS: dict[str, dict[str, str]] = {
    "food and drink": {
        "japanese": "Japanese food or culture.",
        "italian": "Italian food or culture.",
        "french": "French food or culture.",
        "british": "British food or culture, including pubs and Sunday roasts.",
        "spanish": "Spanish food or culture, including tapas.",
        "middle-eastern": "Food or culture of the Middle East, Turkey and North Africa.",
        "indian": "Food or culture of India and South Asia.",
        "chinese": "Chinese food or culture.",
        "southeast-asian": "Food or culture of Thailand, Vietnam, Malaysia, Indonesia and nearby.",
        "korean": "Korean food or culture.",
        "mexican": "Mexican or Latin American food or culture.",
        "seafood": "Fish and shellfish at the centre of the menu.",
        "steak-and-grill": "Steak, grilled meat and cooking over fire.",
        "vegetarian-led": "Vegetables at the centre: vegetarian or vegan cooking, not just one option.",
        "fine-dining": "Ambitious cooking with polished service.",
        "wine": "Wine as a main attraction: a strong list, tastings, vineyards.",
        "natural-wine": "Low-intervention, natural or biodynamic wine.",
        "cocktails": "Cocktails as a main attraction.",
        "spirits": "Whisky, gin, sake and other spirits: distilleries, tastings, specialist bars.",
        "spicy": "Food with real heat.",
    },
    "music": {
        "classical": "Classical music: orchestras, chamber music, recitals.",
        "opera": "Opera.",
        "jazz": "Jazz, blues and soul.",
        "rock-and-pop": "Rock, pop and indie.",
        "electronic": "Electronic and dance music, DJs.",
        "folk-and-acoustic": "Folk, acoustic and singer-songwriter music.",
    },
    "stage and screen": {
        "drama": "Plays and serious theatre.",
        "musicals": "Musical theatre.",
        "comedy": "Stand-up and comic theatre.",
        "dance": "Ballet and contemporary dance.",
        "film-and-tv": "Film and television: screenings, sets, the people who make them.",
    },
    "culture and ideas": {
        "art": "Painting, sculpture, galleries and artists.",
        "design-and-architecture": "Buildings, interiors and design.",
        "history-and-heritage": "Historic places, old houses, the stories behind them.",
        "books-and-ideas": "Authors, talks and conversations.",
        "fashion": "Fashion, designers and tailoring.",
    },
    "craft": {
        "watches": "Watches and watchmaking.",
        "craftsmanship": "Things made by hand with great skill: watches, omakase, tailoring, instruments.",
    },
    "sport": {
        "sport": "Sport in general, watching or meeting the people who play it.",
        "motorsport": "Formula 1 and other motor racing.",
        "football": "Football.",
        "tennis": "Tennis.",
        "golf": "Golf.",
    },
    "nature and outdoors": {
        "wildlife": "Seeing animals and birds in the wild.",
        "walking-and-hiking": "Walks, hikes and time on foot in the landscape.",
        "on-the-water": "Sailing, boats, swimming and time on rivers, lakes or the sea.",
        "gardens": "Gardens and parks.",
        "stargazing": "Night skies and astronomy.",
        "outdoors": "Mostly outside, including terraces and open-air events.",
    },
    "places to stay": {
        "beach": "By the sea, with a beach.",
        "mountains": "In the mountains.",
        "countryside": "In the countryside, away from towns.",
        "city-break": "In a city, with the city as the attraction.",
        "spa-and-wellness": "Spa, treatments, yoga and rest.",
    },
    "atmosphere": {
        "lively": "Busy, loud and buzzing.",
        "quiet": "Calm and quiet enough to talk easily.",
        "romantic": "Suited to a couple: a date, an anniversary.",
        "intimate": "Small and close: few people, low light.",
        "glamorous": "Dressed-up, see-and-be-seen.",
        "relaxed": "Casual and unfussy, nothing stuffy.",
        "formal": "Formal: white tablecloths, dress codes, ceremony.",
        "adventurous": "Physically demanding or a little daring.",
        "views": "A memorable view.",
        "family-friendly": "Welcoming to children.",
        "late-night": "Goes on late, or starts late.",
    },
    "format": {
        "tasting-menu": "A set sequence of many courses.",
        "small-plates": "Small dishes to share.",
        "counter-and-chefs-table": "Seated at a counter or chef's table, watching the cooking.",
        "live-music": "Live music as part of the evening.",
        "hands-on": "You make or do something yourself: a workshop, a class.",
        "guided-tour": "Led by a guide or expert.",
        "behind-the-scenes": "Access the public does not get: a factory floor, backstage, a private collection.",
        "exclusive-use": "Yours alone: private hire, a whole villa.",
    },
    "occasion": {
        "special-occasion": "Worth saving for a birthday, anniversary or celebration.",
        "business": "Suited to entertaining clients or colleagues.",
        "groups": "Works well for a larger group.",
    },
}
"""Every tag, by group, with the definition that decides when it applies."""

TAGS: dict[str, str] = {tag: definition for group in TAG_GROUPS.values() for tag, definition in group.items()}
"""Every tag and its definition, without the groups."""


def unknown_tags(tags: list[str]) -> list[str]:
    """The tags in `tags` that are not in the vocabulary, in their original order.

    unknown_tags(["jazz", "loud"]) gives ["loud"]; an empty result means all are valid.
    """
    return [tag for tag in tags if tag not in TAGS]


QUALITY_GROUPS = ["atmosphere", "format", "occasion"]
"""Groups whose tags say what an experience is like, not what it is. The rest are interests."""

QUALITIES: set[str] = {tag for group in QUALITY_GROUPS for tag in TAG_GROUPS[group]}

INTEREST_WEIGHT, QUALITY_WEIGHT = 1.0, 0.5
"""A shared interest counts in full; a shared quality half, since qualities are spread across the
catalogue (`quiet` is on 40 items) and say little alone. Two qualities count as much as one interest."""

QUALITY_PHRASES = {
    "lively": "lively", "quiet": "quiet", "romantic": "romantic", "intimate": "intimate",
    "glamorous": "glamorous", "relaxed": "relaxed", "formal": "formal", "adventurous": "adventurous",
    "views": "with a view", "family-friendly": "family-friendly", "late-night": "late",
    "tasting-menu": "a tasting menu", "small-plates": "small plates", "counter-and-chefs-table": "at the counter",
    "live-music": "with live music", "hands-on": "hands-on", "guided-tour": "guided",
    "behind-the-scenes": "behind the scenes", "exclusive-use": "yours alone",
    "special-occasion": "one for a special occasion", "business": "good for business", "groups": "good for groups",
}
"""How each quality reads after "it's": "it's also intimate and good for groups"."""


def tag_weight(tag: str) -> float:
    """What one shared tag adds to a match: tag_weight("jazz") is 1.0, tag_weight("quiet") is 0.5."""
    return QUALITY_WEIGHT if tag in QUALITIES else INTEREST_WEIGHT


INTEREST_PHRASES = {
    "japanese": "all things Japanese", "italian": "all things Italian", "french": "all things French",
    "british": "British classics", "spanish": "all things Spanish", "middle-eastern": "Middle Eastern culture",
    "indian": "all things Indian", "chinese": "all things Chinese", "southeast-asian": "Southeast Asia",
    "korean": "all things Korean", "mexican": "Latin America", "seafood": "seafood",
    "steak-and-grill": "a good steak", "vegetarian-led": "vegetarian cooking", "fine-dining": "fine dining",
    "wine": "wine", "natural-wine": "natural wine", "cocktails": "cocktails", "spirits": "fine spirits",
    "spicy": "spicy food",
    "classical": "classical music", "opera": "opera", "jazz": "jazz", "rock-and-pop": "pop music",
    "electronic": "electronic music", "folk-and-acoustic": "folk music",
    "drama": "theatre", "musicals": "musicals", "comedy": "comedy", "dance": "dance", "film-and-tv": "film",
    "art": "art", "design-and-architecture": "architecture", "history-and-heritage": "history", "books-and-ideas": "books",
    "fashion": "fashion",
    "watches": "watches", "craftsmanship": "fine craftsmanship",
    "sport": "sport", "motorsport": "motor racing", "football": "football", "tennis": "tennis", "golf": "golf",
    "wildlife": "wildlife", "walking-and-hiking": "walks", "on-the-water": "time on the water", "gardens": "gardens",
    "stargazing": "stargazing", "outdoors": "being outdoors",
    "beach": "beaches", "mountains": "the mountains", "countryside": "the countryside", "city-break": "city breaks",
    "spa-and-wellness": "spas",
}
"""How each interest reads after "Because you like": "Because you like all things Japanese and walks".
Phrases avoid "and" inside them, so a list of them still reads clearly."""
