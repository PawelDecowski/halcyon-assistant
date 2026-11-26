"""The suggestions review page: plain HTML, no login, sample profiles only.

GET /suggestions                         -> the sample profiles, with their likes and dislikes
GET /suggestions/{profile_id}            -> one profile's ranked suggestions
GET /suggestions/{profile_id}?today=...  -> the same, as if today were that date (YYYY-MM-DD)

It serves only the ids in SAMPLE_PROFILES, so it can never show a real
member's preferences; real members will need their own access. Nothing shown
is recorded. Each card has a collapsed "why this order" line with the
suggestion's components, for judging the ranking, not for members.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.booking.mock.experiences import EXPERIENCES
from app.recommendations.sample_profiles import SAMPLE_PROFILES
from app.recommendations.scoring import LAST_CHANCE, ONE_OFF, Suggestion, band, recommend
from app.recommendations.tags import INTEREST_PHRASES, QUALITY_PHRASES

LONDON = ZoneInfo("Europe/London")
PROFILES = {profile["id"]: profile for profile in SAMPLE_PROFILES}
PRICE_UNITS = {"person": "per person", "night": "per night", "booking": "per booking"}
URGENCY_BADGES = {ONE_OFF: "One date only", LAST_CHANCE: "Last few dates"}

STYLE = """
:root { --bg: #fbfaf7; --card: #ffffff; --text: #1f1d1a; --muted: #6b665e; --line: #e6e2da;
        --accent: #7a4b1e; --badge: #f3e6d6; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #171614; --card: #201f1c; --text: #ece8e1; --muted: #a59f95; --line: #34312c;
          --accent: #e0a96d; --badge: #3a2c1e; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
a { color: var(--accent); }
.muted { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin: 12px 0; }
.card h2 { font-size: 1.05rem; margin: 0; }
.rank { color: var(--muted); margin-right: 6px; }
.meta { color: var(--muted); font-size: 0.9rem; }
.reason { margin: 6px 0 2px; font-weight: 600; }
.badge { display: inline-block; background: var(--badge); color: var(--accent); border-radius: 999px;
         padding: 1px 10px; font-size: 0.8rem; font-weight: 600; margin-left: 6px; }
details { margin-top: 6px; font-size: 0.85rem; color: var(--muted); }
summary { cursor: pointer; }
ul.notes { padding-left: 18px; margin: 6px 0; }
form { margin: 12px 0; font-size: 0.9rem; }
"""


def _page(title: str, body: str) -> HTMLResponse:
    """A whole HTML page with the shared style."""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<style>{STYLE}</style></head><body><main>{body}</main></body></html>""")


def _tag_words(tags: list[str]) -> str:
    """Tags as the phrases members read: ["jazz", "groups"] -> "jazz, good for groups"."""
    return ", ".join(INTEREST_PHRASES.get(tag) or QUALITY_PHRASES[tag] for tag in tags) or "nothing"


def _how_far_ahead(days: int) -> str:
    """A number of days in words: 0 -> "today", 10 -> "10 days away", 45 -> "6 weeks away", 210 -> "7 months away"."""
    if days == 0:
        return "today"
    if days < 14:
        return f"{days} day{'s' if days > 1 else ''} away"
    if days < 60:
        return f"{round(days / 7)} weeks away"
    return f"{round(days / 30.4)} months away"


def _why_this_order(suggestion: Suggestion) -> str:
    """The components, split into what decides the order today and what doesn't yet, each with its source.

    "Order: match 1.0 → nearness 0.7 (day trip) → urgency 1.0 (one-off) → date ·
     Not used yet: scale 1.0 (scale 3), soonness 0.0 (7 months away)"
    """
    c = suggestion.components
    return (f"Order: match {c['match']:.1f} → nearness {c['nearness']:.1f} ({band(suggestion.item)}) → "
            f"urgency {c['urgency']:.1f} ({suggestion.urgency}) → date · "
            f"Not used yet: scale {c['scale']:.1f} (scale {suggestion.item['scale']}), "
            f"soonness {c['soonness']:.1f} ({_how_far_ahead(suggestion.days_ahead)})")


def _card(rank: int, suggestion: Suggestion) -> str:
    """One suggestion as a card: name, where and how much, why, when, and the components."""
    item = suggestion.item
    badge = URGENCY_BADGES.get(suggestion.urgency)
    return f"""<div class="card">
<h2><span class="rank">{rank}</span>{escape(item["name"])}{f'<span class="badge">{badge}</span>' if badge else ""}</h2>
<div class="meta">{escape(item["category"].capitalize())} · {escape(item["place"])} · £{item["price_gbp"]:,} {PRICE_UNITS[item["price_per"]]}</div>
<p>{escape(item["description"])}</p>
<div class="reason">{escape(suggestion.reason)}</div>
<div class="meta">{escape(suggestion.pattern)}</div>
<details><summary>Why this order</summary>{escape(_why_this_order(suggestion))}</details>
</div>"""


def _today(value: str | None) -> date:
    """The date to recommend for: `value` as YYYY-MM-DD, or today in London."""
    if value is None:
        return datetime.now(LONDON).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="today must be a date, YYYY-MM-DD")


def add_recommendation_pages(app: FastAPI) -> None:
    """Register the two review pages on the app."""

    @app.get("/suggestions", response_class=HTMLResponse)
    async def recommendation_profiles():
        """The sample profiles, each linking to its suggestions."""
        cards = "".join(f"""<div class="card"><h2><a href="/suggestions/{escape(p["id"])}">{escape(p["name"])}</a></h2>
<div class="meta">Likes {escape(_tag_words(p["likes"]))}</div>
<div class="meta">Not {escape(_tag_words(p["dislikes"]))}</div></div>""" for p in SAMPLE_PROFILES)
        return _page("Suggestions", f"""<h1>Suggestions</h1>
<p class="muted">Sample members, written by hand until real members' notes are turned into tags.
Suggestions come from the mock catalogue of {len(EXPERIENCES)} experiences.</p>{cards}""")

    @app.get("/suggestions/{profile_id}", response_class=HTMLResponse)
    async def recommendation_list(profile_id: str, today: str | None = None):
        """One sample profile's ranked suggestions, as of today or the given date."""
        profile = PROFILES.get(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Only sample profiles are shown here")
        on = _today(today)
        suggestions = recommend(profile["likes"], profile["dislikes"], EXPERIENCES, on)
        notes = "".join(f"<li>{escape(note)}</li>" for note in profile["notes"])
        cards = "".join(_card(rank, s) for rank, s in enumerate(suggestions, start=1)) or \
            '<p class="muted">Nothing in the catalogue matches yet.</p>'
        return _page(profile["name"], f"""<p><a href="/suggestions">All sample members</a></p>
<h1>{escape(profile["name"])}</h1>
<ul class="notes muted">{notes}</ul>
<form method="get"><label>As of <input type="date" name="today" value="{on.isoformat()}"></label>
<button type="submit">Show</button></form>
<p class="muted">{len(suggestions)} suggestions for {on.strftime("%A")} {on.day} {on.strftime("%B %Y")}.</p>{cards}""")
