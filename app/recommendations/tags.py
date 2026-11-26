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
