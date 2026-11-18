# Halcyon assistant

A restaurant-booking assistant for Halcyon members. It holds conversations over Halcyon's two-endpoint contract, books through a mocked booking provider, and remembers each member across conversations.

- **Service:** https://halcyon-assistant-dev.up.railway.app
- **Decision log:** [`DECISIONS.md`](DECISIONS.md) has every call made, why, and what changed after each practice run. This README is the short version.

## How it works

`POST` stores the message (once per `message_id`) and returns 202. A background worker answers it: Claude Sonnet runs a short tool loop (search, check, book, change, cancel, save a note) against a `BookingProvider` interface, then writes one reply. `GET` returns the conversation.

```
app/main.py            the contract, auth, review endpoints
app/worker.py          one turn at a time per conversation, time limit, crash recovery
app/assistant/         instructions, tools, member memory, Claude adapter
app/booking/           BookingProvider interface; mock/ is the only implementation
scenarios/, scripts/   practice members and the harness that plays them
```

One container on Railway, SQLite on a volume. The work happens after the response and state must last weeks, so scale-to-zero platforms were out. At larger scale: Postgres, a job queue, several instances.

## Decisions and assumptions

**When to book, when to ask.** Ask only what changes the booking, in one message. Fill gaps from the member's history and say so ("8pm, your usual"). Book straight away when the request is specific or the member has said "just book it"; offer two or three options to a new member whose taste is unknown. Never spend money (deposits, card holds, late fees) without a clear yes.

**Memory.** Rebuilt for every turn from three sources: notes of what the member said (corrections retire a note, never delete it), booking history (patterns such as their usual time are calculated, not stored, so they never go stale) and the end of their last three conversations. A member's file fits in the prompt, so no retrieval step can miss anything.

**Honesty.** A booking exists only if the provider confirmed it. A note to a restaurant is one-way, so the assistant never claims a restaurant was asked or will confirm. Cancellation terms are worked out by the provider and repeated, not calculated by the model, including before booking, so a member agrees to the terms that will really apply. If a turn fails, the member is told exactly what was already done.

**Rules in code, not just in the prompt.** The member's own allergies are always added to the restaurant's requests (a guest's only when that guest is coming); spending money needs an explicit "member accepted" flag; a second booking the same day needs confirmation; members can only touch their own bookings; venues booked before are marked so "somewhere new" can't be missed.

**Reliability.** Repeated deliveries are answered once. A turn starts when the member goes quiet for 2 seconds, so a burst is one request. Every turn replies within 120 seconds, with a truthful fallback if the model is slow or down. A crash means the turn is redone on restart; booking keys derived from the turn stop a redo booking twice. No "on it" messages: the contract has no typing indicator, so one would be answered before the real reply.

**The mock provider.** 48 invented restaurants, three or four in most areas, so nothing claims availability at a real one; deterministic availability, scarce at peak. It enforces rules that need judgement (deposits, card holds, closed days, party limits, allergen handling from "shared kitchen" to "fully gluten-free") and fails like real APIs (latency, a table taken between checking and booking, timeouts, half of them *after* the booking was written).

**Assumptions.** Members are in London and mean London time. Every member has a card on file for deposits. Restaurants confirm instantly (no "pending" bookings). Halcyon's simulated members have no calendars, so there's no calendar integration.

## Practice

**Halcyon's public member** (Oliver: a client lunch, then two dinners with his coeliac husband), five runs:

| Run | Succeeded | Member messages | Cost (USD) | What it led to |
|---|---|---|---|---|
| 1 | 1 of 3 | 11 | 0.63 | Allergen levels in the data; no "the restaurant will confirm"; cancellation terms worked out by the provider; shorter replies |
| 2 | 2 of 3 | 7 | 0.18 | Coeliac-accredited kitchens; the latest requirement counts; upcoming vs past bookings labelled |
| 3 | 2 of 3 | 6 | 0.10 | Venues booked before marked in search results |
| 4, 5 | 2 of 3; run 5 had no issues at all | 6, 7 | | |

The third conversation stays "abandoned": Oliver wants somewhere new in Islington that is safe for a coeliac, and the data has none left. Adding one would tune the data to one persona. Caching and fewer turns account for the cost drop.

**Halcyon's custom scenarios** (a birthday growing from 12 to 16, a deposit, a guest's allergy, "just book it", change then cancel, an impossible request) all succeeded. Reading them still found bugs no status showed: venues that couldn't seat 16 crowded out one that could, so the assistant said "the only place"; a literal dietary filter hid vegetarian venues from a pescatarian; a guest's allergy would have followed the member to every future booking.

**My own harness.** `scripts/simulate.py` plays the nine members in `scenarios/` over the real contract and timing rules, then checks outcomes (the booking that really exists, notes saved or retired, messages needed to reach the goal), not wording. Runs went from 4 of 9 to 6 of 9 to 7 of 9, where both failures were the scenarios' own mistakes (a simulated member inventing a constraint). It found what nothing else did: the assistant telling members they "already had" a booking it had just made, a latent double booking, answers dropped when written alongside an action, and a deposit described as refundable for a dinner already inside its window. It also found its own mistakes, such as counting a member's closing "thanks", and showed twice that thin data decided an outcome (one seafood restaurant in all of London), which led to doubling the venues.

## What I'd do next

- **A status channel in the contract** (typing, searching, booking), so members see progress without it counting as a message.
- **Pending confirmations and replies from restaurants**, with follow-up messages when they arrive.
- **A background memory pass** after each conversation: the harness caught one missed note (an anniversary) in nine scenarios.
- **Cheaper long conversations:** summarise old search results in the transcript; leaner search results.
- **Calendar integration**, so the assistant checks the member is free, not just their usual time.
- **Scenarios in CI** against a staging deploy, as a regression check on every prompt change.

## Time spent

[Pawel: fill in.] Built with Claude as a coding agent: I made the product and engineering calls (logged in `DECISIONS.md`), it wrote most of the code to them, and we iterated on practice transcripts together.

## Worth looking at

- `app/assistant/prompt.py`: the assistant's behaviour in plain English.
- `app/booking/mock/`: the mock provider and its venues.
- `app/assistant/tools.py`: the rules enforced in code.
- `tests/`: offline tests with a scripted model; no API key needed.
- Review endpoints, not part of the contract, behind the same token: `GET /members/{id}/bookings` and `GET /members/{id}/notes`.

## Running it

Python 3.11 or newer (`.python-version` says 3.12; `uv venv` picks it up).

```
uv venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt
pytest                                          # offline, no API key needed
API_TOKEN=dev ANTHROPIC_API_KEY=... uvicorn --factory app.main:create_app
python -m scripts.simulate scenarios/*.yaml --url http://localhost:8000 --token dev
```

Settings are env variables; see `app/main.py`.
