"""Mock venue directory: 48 invented London restaurants, three or four in most areas.

The names are fictional on purpose, so nothing here implies real availability
at a real restaurant. The mix is designed to exercise the assistant: a few
near-impossible tables, deposit and cancellation-fee venues, venues closed on
some days, counter-only seating, small maximum party sizes, dietary gaps,
step-free access, and private dining for large groups.

Each venue's fields:
  seatings    day -> list of [first seating, last seating] windows, e.g.
              {"tue": [["12:00", "14:30"], ["18:00", "22:00"]]}. Tables can be
              booked every 15 minutes inside a window. A missing day = closed.
  tables      tables available per seating time.
  demand      0..1, how popular it is: drives how scarce tables are at peak.
  max_party   largest party bookable online; private_dining_max, if present,
              is offered for bigger groups.
  policy      booking terms, built with one of the helpers below.
  allergens   how the kitchen handles allergies, one of ALLERGEN_HANDLING,
              plus an optional `allergen_note` with venue-specific detail.
"""
from __future__ import annotations

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def seatings(days: str, *windows: str) -> dict[str, list[list[str]]]:
    """Seating windows for a day or a range of days.

    seatings("tue-sat", "12:00-14:30", "18:00-22:00") gives Tuesday to Saturday a
    lunch and a dinner window. Each window is "first seating-last seating".
    seatings("sun", "12:00-16:00") covers a single day.
    """
    first, _, last = days.partition("-")
    start = DAYS.index(first)
    stop = DAYS.index(last) if last else start
    return {day: [window.split("-") for window in windows] for day in DAYS[start : stop + 1]}


def schedule(*parts: dict[str, list[list[str]]]) -> dict[str, list[list[str]]]:
    """Combine several seatings() into one weekly schedule.

    schedule(seatings("wed-sun", "18:00-22:00"), seatings("sat-sun", "12:00-15:00"))
    is dinner Wednesday to Sunday plus weekend lunch; Monday and Tuesday are closed.
    """
    week: dict[str, list[list[str]]] = {}
    for part in parts:
        for day, windows in part.items():
            week.setdefault(day, []).extend(windows)
    return week


# How a kitchen handles allergies. "Gluten-free options" on a menu is not the same
# as being safe for a coeliac, so the levels are kept distinct.
ALLERGEN_HANDLING = {
    "cannot_accommodate": "Set menu; cannot adapt dishes for allergies or dietary needs.",
    "shared_kitchen": "Can adapt dishes, but everything is cooked in a shared kitchen: cross-contamination is possible.",
    "separate_preparation": ("Allergy-trained staff, separate preparation area and utensils for allergy orders. "
                             "Suitable for most allergies, but no absolute guarantee."),
    "coeliac_accredited": ("Separate gluten-free preparation area, staff trained and audited under Coeliac UK's "
                           "accreditation: safe for coeliacs. Other allergies handled with separate preparation."),
    "dedicated_gluten_free": "The whole kitchen is gluten-free: safe for coeliacs. Other allergies handled with separate preparation.",
}

# Booking policies. All amounts are per person, in pounds.

FREE_CANCELLATION = {"mode": "instant", "deposit_pp_gbp": 0, "cancellation_hours": 0, "cancellation_fee_pp_gbp": 0}
"""No deposit, no card hold: free to cancel any time before the booking."""


def deposit_required(per_person: int, refundable_until_hours: int = 48) -> dict:
    """A deposit is taken when booking; it is lost if cancelled within the window or on a no-show."""
    return {"mode": "deposit", "deposit_pp_gbp": per_person, "cancellation_hours": refundable_until_hours,
            "cancellation_fee_pp_gbp": per_person}


def card_hold(fee_per_person: int, free_until_hours: int = 24) -> dict:
    """Nothing is taken when booking, but a card is held and charged for a no-show or a late cancellation."""
    return {"mode": "card_hold", "deposit_pp_gbp": 0, "cancellation_hours": free_until_hours,
            "cancellation_fee_pp_gbp": fee_per_person}


VENUES: list[dict] = [
    {
        "id": "v-ember-and-oak", "name": "Ember & Oak", "area": "Mayfair", "cuisine": "British grill",
        "price": "££££", "avg_spend_pp_gbp": 140,
        "description": "Wood-fired steaks and game in a dark, clubby dining room. Strong wine list.",
        "tags": ["special occasion", "business", "wine list", "dimly lit"],
        "dietary": ["gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sat", "12:00-14:30", "18:00-22:30")),
        "max_party": 8, "tables": 6, "demand": 0.7, "table_time_minutes": 150,
        "policy": card_hold(50), "private_dining_max": 16,
    },
    {
        "id": "v-salt-house", "name": "The Salt House", "area": "Marylebone", "cuisine": "Seafood",
        "price": "£££", "avg_spend_pp_gbp": 85,
        "description": "Bright, airy fish restaurant with a raw bar and a short, daily-changing menu.",
        "tags": ["relaxed", "lunch", "romantic", "terrace"],
        "dietary": ["gluten-free options", "pescatarian"], "step_free": True,
        "allergens": "coeliac_accredited",
        "seatings": schedule(seatings("tue-sun", "12:00-15:00", "17:30-22:00")),
        "max_party": 6, "tables": 8, "demand": 0.5, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-kiku-counter", "name": "Kiku Counter", "area": "Soho", "cuisine": "Japanese omakase",
        "price": "££££", "avg_spend_pp_gbp": 190,
        "description": "Twelve-seat counter; one 18-course omakase sitting at 18:00 and one at 20:30.",
        "tags": ["tasting menu", "counter seating only", "special occasion", "intimate"],
        "dietary": [], "step_free": False,
        "allergens": "cannot_accommodate",
        "notes": "Counter only. Cannot accommodate vegetarian, vegan or shellfish allergies.",
        "seatings": schedule(seatings("tue-sat", "18:00-18:00", "20:30-20:30")),
        "max_party": 4, "tables": 3, "demand": 0.95, "table_time_minutes": 135,
        "policy": deposit_required(95, 72),
    },
    {
        "id": "v-verdure", "name": "Verdure", "area": "Shoreditch", "cuisine": "Modern vegetarian",
        "price": "£££", "avg_spend_pp_gbp": 70,
        "description": "Inventive plant-led small plates, natural wine, warehouse space with an open kitchen.",
        "tags": ["lively", "natural wine", "small plates", "groups"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": True,
        "allergens": "coeliac_accredited",
        "seatings": schedule(seatings("wed-sun", "18:00-22:00"), seatings("sat-sun", "12:00-15:00")),
        "max_party": 10, "tables": 10, "demand": 0.55, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-casa-lucia", "name": "Casa Lucia", "area": "Notting Hill", "cuisine": "Italian",
        "price": "££", "avg_spend_pp_gbp": 55,
        "description": "Family-run trattoria: handmade pasta, candlelit, warm and a bit noisy.",
        "tags": ["romantic", "casual", "family friendly", "neighbourhood"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "12:00-14:30", "18:00-22:30")),
        "max_party": 12, "tables": 12, "demand": 0.45, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-maison-bastide", "name": "Maison Bastide", "area": "Chelsea", "cuisine": "French",
        "price": "££££", "avg_spend_pp_gbp": 160,
        "description": "Classic French fine dining, white tablecloths, quiet room, impeccable service.",
        "tags": ["special occasion", "quiet", "tasting menu", "business", "dress code"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": True,
        "allergens": "separate_preparation",
        "notes": "Smart dress; no sportswear.",
        "seatings": schedule(seatings("tue-sat", "12:00-13:45", "18:30-21:30")),
        "max_party": 8, "tables": 7, "demand": 0.75, "table_time_minutes": 165,
        "policy": deposit_required(60, 48), "private_dining_max": 20,
    },
    {
        "id": "v-dilli-house", "name": "Dilli House", "area": "Covent Garden", "cuisine": "Indian",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Old Delhi street food and tandoor in a colourful, buzzy room. Great before the theatre.",
        "tags": ["pre-theatre", "lively", "groups", "casual"],
        "dietary": ["vegetarian", "vegan", "halal", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "12:00-15:00", "17:00-23:00")),
        "max_party": 14, "tables": 14, "demand": 0.5, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-the-larder", "name": "The Larder", "area": "Islington", "cuisine": "Modern British",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Neighbourhood bistro with seasonal plates and a famous Sunday roast. Dog friendly.",
        "tags": ["casual", "neighbourhood", "sunday roast", "dog friendly", "brunch"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": False,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("wed-sat", "18:00-22:00"), seatings("sat-sun", "10:00-15:30")),
        "max_party": 8, "tables": 9, "demand": 0.5, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-jade-pavilion", "name": "Jade Pavilion", "area": "Mayfair", "cuisine": "Cantonese",
        "price": "££££", "avg_spend_pp_gbp": 120,
        "description": "Glamorous Cantonese with dim sum at lunch and Peking duck at dinner.",
        "tags": ["special occasion", "groups", "business", "glamorous", "late night"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "12:00-15:00", "18:00-23:00")),
        "max_party": 12, "tables": 12, "demand": 0.65, "table_time_minutes": 120,
        "policy": card_hold(40), "private_dining_max": 24,
    },
    {
        "id": "v-bodega-sur", "name": "Bodega Sur", "area": "Borough", "cuisine": "Spanish tapas",
        "price": "££", "avg_spend_pp_gbp": 45,
        "description": "Sherry bar and tapas by the market; walk-ins at the bar, tables bookable.",
        "tags": ["lively", "casual", "groups", "small plates", "late night"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sat", "12:00-22:30")),
        "max_party": 8, "tables": 10, "demand": 0.5, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-anatolia", "name": "Anatolia", "area": "King's Cross", "cuisine": "Turkish",
        "price": "££", "avg_spend_pp_gbp": 45,
        "description": "Charcoal grill, mezze and fresh pide in a canal-side dining room.",
        "tags": ["groups", "casual", "terrace", "family friendly"],
        "dietary": ["vegetarian", "vegan", "halal"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "12:00-22:30")),
        "max_party": 16, "tables": 14, "demand": 0.4, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-nordlys", "name": "Nordlys", "area": "Fitzrovia", "cuisine": "Scandinavian tasting menu",
        "price": "££££", "avg_spend_pp_gbp": 175,
        "description": "Nine-course Nordic tasting menu, calm pale-wood room, one seating per night.",
        "tags": ["tasting menu", "special occasion", "quiet", "intimate"],
        "dietary": ["vegetarian", "pescatarian"], "step_free": True,
        "allergens": "separate_preparation",
        "notes": "Vegetarian menu needs 48 hours' notice.",
        "seatings": schedule(seatings("wed-sat", "19:00-19:30")),
        "max_party": 6, "tables": 5, "demand": 0.85, "table_time_minutes": 180,
        "policy": deposit_required(75, 72),
    },
    {
        "id": "v-lantern-room", "name": "The Lantern Room", "area": "South Bank", "cuisine": "Modern European",
        "price": "£££", "avg_spend_pp_gbp": 80,
        "description": "Rooftop dining with river and skyline views; best at sunset.",
        "tags": ["views", "romantic", "special occasion", "cocktails", "terrace"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "notes": "Window tables cannot be guaranteed; request them in special_requests.",
        "seatings": schedule(seatings("mon-sun", "12:00-15:00", "17:30-22:30")),
        "max_party": 10, "tables": 12, "demand": 0.7, "table_time_minutes": 120, "policy": card_hold(30),
    },
    {
        "id": "v-pho-saigon", "name": "Little Saigon Kitchen", "area": "Shoreditch", "cuisine": "Vietnamese",
        "price": "£", "avg_spend_pp_gbp": 28,
        "description": "Quick, cheerful pho and banh xeo. Good value.",
        "tags": ["casual", "quick", "good value", "groups"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "11:30-22:00")),
        "max_party": 10, "tables": 12, "demand": 0.35, "table_time_minutes": 75, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-osteria-fumo", "name": "Osteria Fumo", "area": "Soho", "cuisine": "Italian",
        "price": "£££", "avg_spend_pp_gbp": 75,
        "description": "Smoky open-fire Italian with a long bar, very much a scene. Loud on weekends.",
        "tags": ["lively", "date night", "cocktails", "late night", "bar seating"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": False,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sat", "17:30-23:00")),
        "max_party": 6, "tables": 9, "demand": 0.8, "table_time_minutes": 105, "policy": card_hold(25),
    },
    {
        "id": "v-heath-tavern", "name": "The Heath Tavern", "area": "Hampstead", "cuisine": "Gastropub",
        "price": "££", "avg_spend_pp_gbp": 45,
        "description": "Proper pub by the Heath with a garden, open fires and a big Sunday lunch.",
        "tags": ["casual", "sunday roast", "garden", "dog friendly", "family friendly", "groups"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "12:00-21:30")),
        "max_party": 14, "tables": 14, "demand": 0.45, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-sumac", "name": "Sumac & Smoke", "area": "Clerkenwell", "cuisine": "Middle Eastern",
        "price": "£££", "avg_spend_pp_gbp": 65,
        "description": "Levantine sharing plates from the grill; warm, busy, great for groups of friends.",
        "tags": ["groups", "lively", "small plates", "date night"],
        "dietary": ["vegetarian", "vegan", "halal", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("tue-sun", "12:00-14:30", "18:00-22:30")),
        "max_party": 12, "tables": 11, "demand": 0.6, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-the-pantry-kensington", "name": "Kensington Pantry", "area": "Kensington", "cuisine": "All-day cafe & brunch",
        "price": "££", "avg_spend_pp_gbp": 35,
        "description": "Bright all-day spot for breakfast, brunch and light lunches, with an entirely gluten-free kitchen and bakery.",
        "tags": ["brunch", "breakfast", "casual", "family friendly", "quiet"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": True,
        "allergens": "dedicated_gluten_free",
        "seatings": schedule(seatings("mon-sun", "08:00-16:00")),
        "max_party": 8, "tables": 12, "demand": 0.4, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-ishq", "name": "Ishq", "area": "Mayfair", "cuisine": "Modern Indian",
        "price": "££££", "avg_spend_pp_gbp": 110,
        "description": "Refined regional Indian tasting and à la carte; elegant, quiet room.",
        "tags": ["special occasion", "business", "tasting menu", "quiet"],
        "dietary": ["vegetarian", "vegan", "halal", "gluten-free options"], "step_free": True,
        "allergens": "coeliac_accredited",
        "seatings": schedule(seatings("mon-sat", "12:00-14:00", "17:30-22:00")),
        "max_party": 10, "tables": 9, "demand": 0.65, "table_time_minutes": 135, "policy": card_hold(40),
    },
    {
        "id": "v-green-door", "name": "The Green Door", "area": "Chelsea", "cuisine": "Modern British",
        "price": "£££", "avg_spend_pp_gbp": 75,
        "description": "Townhouse restaurant with a hidden courtyard garden. Calm and grown-up.",
        "tags": ["quiet", "romantic", "garden", "business", "lunch"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": False,
        "allergens": "separate_preparation",
        "seatings": schedule(seatings("mon-sat", "12:00-14:30", "18:00-22:00"), seatings("sun", "12:00-16:00")),
        "max_party": 8, "tables": 10, "demand": 0.5, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-taqueria-azul", "name": "Taquería Azul", "area": "Notting Hill", "cuisine": "Mexican",
        "price": "££", "avg_spend_pp_gbp": 40,
        "description": "Tacos, mezcal and loud music. Fun for birthdays and big groups.",
        "tags": ["lively", "groups", "cocktails", "casual", "birthday"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("tue-sun", "12:00-23:00")),
        "max_party": 16, "tables": 12, "demand": 0.5, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-han-seoul", "name": "Han", "area": "Soho", "cuisine": "Korean barbecue",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Tabletop grills, banchan and soju. Smoky, fun and sociable.",
        "tags": ["groups", "lively", "casual", "late night"],
        "dietary": ["vegetarian options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("mon-sun", "12:00-15:00", "17:00-23:00")),
        "max_party": 10, "tables": 10, "demand": 0.55, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-corvo", "name": "Corvo", "area": "Marylebone", "cuisine": "Wine bar & small plates",
        "price": "£££", "avg_spend_pp_gbp": 60,
        "description": "Candlelit wine bar with a serious list and small seasonal plates. Perfect for two.",
        "tags": ["date night", "romantic", "wine list", "small plates", "intimate", "bar seating"],
        "dietary": ["vegetarian", "gluten-free options"], "step_free": True,
        "allergens": "shared_kitchen",
        "seatings": schedule(seatings("tue-sat", "17:00-22:30")),
        "max_party": 4, "tables": 7, "demand": 0.6, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-hearth-and-field", "name": "Hearth & Field", "area": "Islington", "cuisine": "Modern British",
        "price": "£££", "avg_spend_pp_gbp": 60,
        "description": "Calm, candlelit dining room on Upper Street; seasonal British cooking from an entirely gluten-free kitchen.",
        "tags": ["quiet", "romantic", "neighbourhood", "date night"],
        "dietary": ["gluten-free", "coeliac-safe", "vegetarian", "dairy-free options"],
        "allergens": "dedicated_gluten_free",
        "allergen_note": "Coeliac UK accredited. Mild, unspiced cooking; chilli only on request.",
        "step_free": True,
        "seatings": schedule(seatings("tue-sat", "18:00-21:30"), seatings("sun", "12:00-15:00")),
        "max_party": 6, "tables": 8, "demand": 0.55, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-the-chancery", "name": "The Chancery Rooms", "area": "City of London", "cuisine": "Modern European",
        "price": "£££", "avg_spend_pp_gbp": 90,
        "description": "Polished, quiet restaurant built for business lunches and client dinners. Closed weekends.",
        "tags": ["business", "quiet", "private dining", "lunch"],
        "dietary": ["vegetarian", "vegan", "gluten-free options", "halal"], "step_free": True,
        "allergens": "separate_preparation",
        "seatings": schedule(seatings("mon-fri", "12:00-14:30", "18:00-21:30")),
        "max_party": 12, "tables": 12, "demand": 0.45, "table_time_minutes": 120,
        "policy": FREE_CANCELLATION, "private_dining_max": 30,
    },
    # -- Added after practice: three or four per area and more cuisines, so "nothing there" is as
    # rare as it would be on a real booking network. A realistic mix, not fitted to any scenario.
    {
        "id": "v-trattoria-sette", "name": "Trattoria Sette", "area": "Islington", "cuisine": "Italian",
        "price": "££", "avg_spend_pp_gbp": 45,
        "description": "Cosy, calm neighbourhood Italian off Upper Street; fresh pasta and a short Sicilian wine list.",
        "tags": ["neighbourhood", "romantic", "quiet", "casual"],
        "dietary": ["vegetarian", "vegan options", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("tue-sun", "18:00-22:00"), seatings("sat-sun", "12:00-14:30")),
        "max_party": 8, "tables": 9, "demand": 0.5, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-ostra-bar", "name": "Ostra", "area": "Islington", "cuisine": "Seafood",
        "price": "£££", "avg_spend_pp_gbp": 70,
        "description": "Oyster bar and grilled fish on Essex Road; marble counter and a few tables, busy at weekends.",
        "tags": ["date night", "lively", "bar seating", "wine list"],
        "dietary": ["pescatarian", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": False, "seatings": schedule(seatings("wed-sun", "17:30-22:00")),
        "max_party": 6, "tables": 7, "demand": 0.6, "table_time_minutes": 105, "policy": card_hold(25),
    },
    {
        "id": "v-izakaya-neko", "name": "Izakaya Neko", "area": "Soho", "cuisine": "Japanese izakaya",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Basement izakaya: yakitori, small plates, sake and highballs. Loud and fun.",
        "tags": ["lively", "late night", "small plates", "groups", "cocktails"],
        "dietary": ["vegetarian options", "pescatarian"], "allergens": "shared_kitchen",
        "step_free": False, "seatings": schedule(seatings("mon-sat", "17:30-23:00")),
        "max_party": 8, "tables": 10, "demand": 0.65, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-pescatore", "name": "Pescatore", "area": "Soho", "cuisine": "Seafood",
        "price": "£££", "avg_spend_pp_gbp": 75,
        "description": "Italian-style fish restaurant: crudo, whole grilled fish, seafood pasta. Smart but relaxed.",
        "tags": ["date night", "romantic", "special occasion"],
        "dietary": ["pescatarian", "vegetarian", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("mon-sat", "12:00-14:30", "17:30-22:30")),
        "max_party": 8, "tables": 9, "demand": 0.6, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-brick-lane-social", "name": "The Social", "area": "Shoreditch", "cuisine": "Modern British small plates",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Small plates from the wood oven, meat and veg alike, and a long natural wine list. Buzzy.",
        "tags": ["lively", "small plates", "natural wine", "groups", "date night"],
        "dietary": ["vegetarian", "vegan options", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("tue-sat", "17:30-22:30"), seatings("sat-sun", "12:00-15:00")),
        "max_party": 10, "tables": 11, "demand": 0.6, "table_time_minutes": 105, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-mono-ramen", "name": "Mono", "area": "Shoreditch", "cuisine": "Japanese ramen",
        "price": "£", "avg_spend_pp_gbp": 25,
        "description": "Quick, cheerful ramen bar with counter and communal tables.",
        "tags": ["casual", "quick", "good value", "counter seating"],
        "dietary": ["vegetarian", "vegan options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("mon-sun", "12:00-22:00")),
        "max_party": 6, "tables": 10, "demand": 0.4, "table_time_minutes": 60, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-lune", "name": "Lune", "area": "Marylebone", "cuisine": "French bistro",
        "price": "£££", "avg_spend_pp_gbp": 70,
        "description": "Quiet, candlelit French bistro: steak frites, sole meunière, a good cheese trolley.",
        "tags": ["romantic", "quiet", "date night", "wine list"],
        "dietary": ["vegetarian", "pescatarian", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("tue-sat", "12:00-14:00", "18:00-22:00")),
        "max_party": 6, "tables": 8, "demand": 0.6, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-sora", "name": "Sora", "area": "Mayfair", "cuisine": "Japanese kaiseki",
        "price": "££££", "avg_spend_pp_gbp": 180,
        "description": "Seasonal Japanese tasting menu in a hushed room; a chef's table seats up to six.",
        "tags": ["tasting menu", "special occasion", "quiet", "intimate", "chef's table"],
        "dietary": ["pescatarian"], "allergens": "separate_preparation",
        "notes": "Vegetarian kaiseki needs 72 hours' notice.",
        "step_free": True, "seatings": schedule(seatings("tue-sat", "18:00-20:30")),
        "max_party": 6, "tables": 4, "demand": 0.85, "table_time_minutes": 150, "policy": deposit_required(80, 72),
    },
    {
        "id": "v-the-admiral", "name": "The Admiral", "area": "Chelsea", "cuisine": "Gastropub",
        "price": "££", "avg_spend_pp_gbp": 45,
        "description": "Smart pub with a fire, a proper Sunday roast and a dining room upstairs.",
        "tags": ["casual", "sunday roast", "dog friendly", "groups", "family friendly"],
        "dietary": ["vegetarian", "vegan options", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": False, "seatings": schedule(seatings("mon-sun", "12:00-21:30")),
        "max_party": 12, "tables": 12, "demand": 0.45, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-marlowes", "name": "Marlowe's", "area": "Notting Hill", "cuisine": "Seafood and grill",
        "price": "£££", "avg_spend_pp_gbp": 65,
        "description": "Relaxed fish and grill restaurant with a raw bar and a small covered terrace.",
        "tags": ["relaxed", "terrace", "date night", "neighbourhood"],
        "dietary": ["pescatarian", "vegetarian", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("tue-sun", "12:00-15:00", "18:00-22:00")),
        "max_party": 8, "tables": 10, "demand": 0.55, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-theatre-kitchen", "name": "The Prompt Corner", "area": "Covent Garden", "cuisine": "Modern British",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Pre- and post-theatre set menus in a bright room two minutes from the theatres.",
        "tags": ["pre-theatre", "quick", "casual", "groups"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("mon-sat", "12:00-14:30", "17:00-22:30")),
        "max_party": 10, "tables": 12, "demand": 0.55, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-bistro-lumiere", "name": "Brasserie Lumière", "area": "Covent Garden", "cuisine": "French brasserie",
        "price": "£££", "avg_spend_pp_gbp": 65,
        "description": "Grand, glittering brasserie open late: oysters, steak tartare, champagne.",
        "tags": ["glamorous", "late night", "lively", "special occasion", "groups"],
        "dietary": ["vegetarian", "pescatarian", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("mon-sun", "12:00-15:00", "17:00-23:30")),
        "max_party": 12, "tables": 16, "demand": 0.6, "table_time_minutes": 120, "policy": card_hold(25),
        "private_dining_max": 20,
    },
    {
        "id": "v-wharf-fish-house", "name": "Wharf Fish House", "area": "Borough", "cuisine": "Seafood",
        "price": "££", "avg_spend_pp_gbp": 40,
        "description": "Market fish cooked simply: fish and chips, crab, grilled mackerel. Casual and quick.",
        "tags": ["casual", "good value", "lunch", "groups"],
        "dietary": ["pescatarian", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("tue-sun", "12:00-21:30")),
        "max_party": 10, "tables": 12, "demand": 0.45, "table_time_minutes": 75, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-granary-kitchen", "name": "Granary Kitchen", "area": "King's Cross", "cuisine": "Modern European",
        "price": "£££", "avg_spend_pp_gbp": 70,
        "description": "Calm, high-ceilinged dining room in a converted warehouse; good for business and quiet dinners.",
        "tags": ["quiet", "business", "lunch", "private dining"],
        "dietary": ["vegetarian", "vegan", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("mon-sat", "12:00-14:30", "18:00-22:00")),
        "max_party": 12, "tables": 12, "demand": 0.5, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
        "private_dining_max": 18,
    },
    {
        "id": "v-kin-thai", "name": "Kin", "area": "Fitzrovia", "cuisine": "Thai",
        "price": "££", "avg_spend_pp_gbp": 45,
        "description": "Fiery northern Thai grill and curries; loud, fast and very popular.",
        "tags": ["lively", "spicy", "groups", "casual"],
        "dietary": ["vegetarian options", "gluten-free options"], "allergens": "shared_kitchen",
        "notes": "Most dishes are spicy and many contain peanuts or fish sauce.",
        "step_free": True, "seatings": schedule(seatings("mon-sat", "12:00-15:00", "17:30-22:30")),
        "max_party": 8, "tables": 12, "demand": 0.7, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-hollis", "name": "Hollis", "area": "Fitzrovia", "cuisine": "Modern British",
        "price": "£££", "avg_spend_pp_gbp": 80,
        "description": "Small, quiet dining room with a seasonal tasting menu and à la carte at lunch.",
        "tags": ["quiet", "tasting menu", "special occasion", "intimate", "lunch"],
        "dietary": ["vegetarian", "vegan", "gluten-free"], "allergens": "coeliac_accredited",
        "step_free": False, "seatings": schedule(seatings("wed-sat", "12:00-13:45", "18:30-21:00")),
        "max_party": 6, "tables": 7, "demand": 0.7, "table_time_minutes": 150, "policy": card_hold(40),
    },
    {
        "id": "v-saint-bride", "name": "Saint Bride", "area": "Clerkenwell", "cuisine": "British",
        "price": "£££", "avg_spend_pp_gbp": 65,
        "description": "Whitewashed room, nose-to-tail British cooking, big sharing roasts for groups.",
        "tags": ["groups", "business", "wine list", "special occasion"],
        "dietary": ["vegetarian options", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("mon-sat", "12:00-14:30", "18:00-22:00")),
        "max_party": 12, "tables": 10, "demand": 0.6, "table_time_minutes": 135, "policy": card_hold(30),
        "private_dining_max": 20,
    },
    {
        "id": "v-villa-rosa", "name": "Villa Rosa", "area": "Kensington", "cuisine": "Italian",
        "price": "£££", "avg_spend_pp_gbp": 65,
        "description": "Elegant, quiet Italian with a garden room; classic dishes done carefully.",
        "tags": ["quiet", "romantic", "family friendly", "garden"],
        "dietary": ["vegetarian", "vegan options", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("mon-sun", "12:00-14:30", "18:00-22:00")),
        "max_party": 10, "tables": 11, "demand": 0.5, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-bank-street-grill", "name": "Bank Street Grill", "area": "City of London", "cuisine": "Steakhouse",
        "price": "££££", "avg_spend_pp_gbp": 110,
        "description": "Dry-aged steaks and a serious wine list in booths built for business. Closed weekends.",
        "tags": ["business", "quiet", "wine list", "private dining"],
        "dietary": ["gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("mon-fri", "12:00-14:30", "17:30-22:00")),
        "max_party": 10, "tables": 10, "demand": 0.55, "table_time_minutes": 120, "policy": card_hold(40),
        "private_dining_max": 16,
    },
    {
        "id": "v-mercado", "name": "Mercado", "area": "South Bank", "cuisine": "Spanish tapas",
        "price": "££", "avg_spend_pp_gbp": 40,
        "description": "Busy tapas bar under the arches by the river; good for a quick bite before a show.",
        "tags": ["lively", "small plates", "pre-theatre", "groups", "casual"],
        "dietary": ["vegetarian", "vegan options", "gluten-free options"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("mon-sun", "12:00-22:30")),
        "max_party": 10, "tables": 12, "demand": 0.5, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-petit-jardin", "name": "Le Petit Jardin", "area": "Hampstead", "cuisine": "French bistro",
        "price": "££", "avg_spend_pp_gbp": 50,
        "description": "Small, quiet bistro with a garden; long lunches and early dinners.",
        "tags": ["quiet", "romantic", "garden", "lunch", "neighbourhood"],
        "dietary": ["vegetarian", "gluten-free options"], "allergens": "separate_preparation",
        "step_free": True, "seatings": schedule(seatings("wed-sun", "12:00-14:30", "18:00-21:30")),
        "max_party": 6, "tables": 8, "demand": 0.45, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-canal-pizza", "name": "Lock Pizza", "area": "Hackney", "cuisine": "Pizza",
        "price": "£", "avg_spend_pp_gbp": 25,
        "description": "Sourdough pizza by the canal, long tables, natural wine on tap. Very relaxed.",
        "tags": ["casual", "groups", "good value", "family friendly", "natural wine"],
        "dietary": ["vegetarian", "vegan"], "allergens": "shared_kitchen",
        "step_free": True, "seatings": schedule(seatings("tue-sun", "12:00-22:00")),
        "max_party": 16, "tables": 14, "demand": 0.4, "table_time_minutes": 90, "policy": FREE_CANCELLATION,
    },
    {
        "id": "v-laurel", "name": "Laurel", "area": "Hackney", "cuisine": "Modern European",
        "price": "£££", "avg_spend_pp_gbp": 60,
        "description": "Natural wine bistro with a short daily menu; candlelit and intimate.",
        "tags": ["date night", "natural wine", "small plates", "intimate"],
        "dietary": ["vegetarian", "vegan options", "pescatarian"], "allergens": "separate_preparation",
        "step_free": False, "seatings": schedule(seatings("wed-sat", "18:00-22:00")),
        "max_party": 6, "tables": 7, "demand": 0.6, "table_time_minutes": 120, "policy": FREE_CANCELLATION,
    },
]

VENUES_BY_ID = {v["id"]: v for v in VENUES}
