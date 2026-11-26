"""The suggestions review page: plain HTML, no login.

GET /suggestions                         -> sample members and members from conversations, with their tags
GET /suggestions/{member_id}             -> one member's ranked suggestions
GET /suggestions/{member_id}?today=...   -> the same, as if today were that date (YYYY-MM-DD)

Every member is simulated (the hand-written samples, and Halcyon's and our
harness's test members), so the page is open; it shows names and likes and
dislikes notes that the token-protected review endpoints otherwise guard.
Nothing shown is recorded. Each card has a collapsed "why this order" note
saying, in words, where its points come from and how ties were broken, for
judging the ranking, not for members.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.booking.mock.experiences import EXPERIENCES
from app.db import Database
from app.recommendations.sample_profiles import SAMPLE_PROFILES
from app.recommendations.scoring import (ANY_DAY, DAY_TRIP, FAR, LAST_CHANCE, LOCAL, NEAR, OCCASIONAL, ONE_OFF,
                                         RARE, Suggestion, band, recommend)
from app.recommendations.tags import INTEREST_PHRASES, QUALITY_PHRASES, VOCABULARY_HASH, tag_weight

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
details p { margin: 4px 0; }
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


BAND_WORDS = {LOCAL: "in London", DAY_TRIP: "a day trip, up to 3 hours away",
              NEAR: "up to 5 hours away", FAR: "more than 5 hours away"}
URGENCY_WORDS = {ONE_OFF: "a one-off", LAST_CHANCE: "in its last few dates",
                 RARE: "rare, with fewer than 10 dates a year", OCCASIONAL: "on occasionally, 10 to 49 dates a year",
                 ANY_DAY: "available most weeks"}


def _why_this_order(suggestion: Suggestion) -> list[str]:
    """Why the card is where it is, in words: where its points come from, then how ties are broken.

    ["2.5 points from your likes: jazz 1.0, wine 1.0, intimate 0.5 (interests count 1, qualities 0.5).",
     "Among items with equal points: nearer first (this is in London), then rarer dates (this is on
      occasionally, 10 to 49 dates a year), then sooner (next on Sunday 29 November)."]
    Scale and soonness are left out: they don't affect the order until a weighted score is used.
    """
    points = ", ".join(f"{INTEREST_PHRASES.get(tag) or QUALITY_PHRASES[tag]} {tag_weight(tag):.1f}"
                       for tag in suggestion.shared)
    day = suggestion.next_date
    return [f"{suggestion.score:.1f} points from your likes: {points} (interests count 1, qualities 0.5).",
            f"Among items with equal points: nearer first (this is {BAND_WORDS[band(suggestion.item)]}), "
            f"then rarer dates (this is {URGENCY_WORDS[suggestion.urgency]}), "
            f"then sooner (next on {day.strftime('%A')} {day.day} {day.strftime('%B')})."]


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
<details><summary>Why this order</summary>{"".join(f"<p>{escape(line)}</p>" for line in _why_this_order(suggestion))}</details>
</div>"""


def _today(value: str | None) -> date:
    """The date to recommend for: `value` as YYYY-MM-DD, or today in London."""
    if value is None:
        return datetime.now(LONDON).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="today must be a date, YYYY-MM-DD")


def _profile_card(profile: dict, extra: str = "") -> str:
    """One member on the index: name linking to their page, likes, dislikes, and an optional note."""
    likes = f"Likes {_tag_words(profile['likes'])}" if profile["likes"] else "No likes tagged yet"
    dislikes = f"<div class=\"meta\">Not {escape(_tag_words(profile['dislikes']))}</div>" if profile["dislikes"] else ""
    return f"""<div class="card"><h2><a href="/suggestions/{escape(profile["id"])}">{escape(profile["name"])}</a></h2>
<div class="meta">{escape(likes)}</div>{dislikes}{f'<div class="meta">{escape(extra)}</div>' if extra else ""}</div>"""


def _member_profile(db: Database, member: dict) -> dict:
    """A member from conversations in the same shape as a sample profile, from their tagged notes."""
    tags = db.member_tags(member["id"])
    notes = [n["note"] for n in db.active_notes(member["id"]) if n["category"] in ("likes", "dislikes")]
    untagged = len(db.notes_needing_tags(VOCABULARY_HASH, member["id"]))
    return {"id": member["id"], "name": member["name"], "notes": notes, "untagged": untagged, **tags}


def add_recommendation_pages(app: FastAPI, db: Database) -> None:
    """Register the two review pages on the app."""

    @app.get("/suggestions", response_class=HTMLResponse)
    async def recommendation_profiles():
        """Sample members and members from conversations, each linking to their suggestions."""
        samples = "".join(_profile_card(p) for p in SAMPLE_PROFILES)
        members = []
        for member in db.members():
            profile = _member_profile(db, member)
            waiting = f"{profile['untagged']} note(s) waiting to be tagged" if profile["untagged"] else ""
            extra = waiting or ("" if profile["likes"] else "Nothing to match yet")
            members.append(_profile_card(profile, extra))
        from_conversations = "".join(members) or '<p class="muted">No members have written in yet.</p>'
        return _page("Suggestions", f"""<h1>Suggestions</h1>
<p class="muted">Suggestions come from the mock catalogue of {len(EXPERIENCES)} experiences. Every member here is
simulated: the hand-written samples, and members who wrote in through the conversations endpoint.</p>
<h2>Sample members</h2>{samples}
<h2>Members from conversations</h2>
<p class="muted">Tags come from their likes and dislikes notes, tagged after each turn.</p>{from_conversations}""")

    @app.get("/suggestions/{profile_id}", response_class=HTMLResponse)
    async def recommendation_list(profile_id: str, today: str | None = None):
        """One member's ranked suggestions, as of today or the given date."""
        profile = PROFILES.get(profile_id)
        if profile is None:
            member = db.one("SELECT id, name FROM members WHERE id = ?", (profile_id,))
            if member is None:
                raise HTTPException(status_code=404, detail="No such member")
            profile = _member_profile(db, member)
        on = _today(today)
        suggestions = recommend(profile["likes"], profile["dislikes"], EXPERIENCES, on)
        notes = "".join(f"<li>{escape(note)}</li>" for note in profile["notes"]) or "<li>No likes or dislikes noted yet</li>"
        waiting = (f'<p class="muted">{profile["untagged"]} note(s) still waiting to be tagged.</p>'
                   if profile.get("untagged") else "")
        cards = "".join(_card(rank, s) for rank, s in enumerate(suggestions, start=1)) or \
            '<p class="muted">Nothing in the catalogue matches yet.</p>'
        return _page(profile["name"], f"""<p><a href="/suggestions">All members</a></p>
<h1>{escape(profile["name"])}</h1>
<ul class="notes muted">{notes}</ul>{waiting}
<form method="get"><label>As of <input type="date" name="today" value="{on.isoformat()}"></label>
<button type="submit">Show</button></form>
<p class="muted">{len(suggestions)} suggestions for {on.strftime("%A")} {on.day} {on.strftime("%B %Y")}.</p>{cards}""")
