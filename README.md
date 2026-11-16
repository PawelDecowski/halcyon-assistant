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

**Honesty.** A booking exists only if the provider confirmed it. A note to a restaurant is one-way, so the assistant never claims a restaurant was asked or will confirm. Cancellation terms are worked out by the provider and repeated, not calculated by the model. If a turn fails, the member is told exactly what was already done.

**Rules in code, not just in the prompt.** Allergies on file are always added to the restaurant's requests; spending money needs an explicit "member accepted" flag; a second booking the same day needs confirmation; members can only touch their own bookings; venues booked before are marked so "somewhere new" can't be missed.

**Reliability.** Repeated deliveries are stored and answered once. A turn starts when the member goes quiet for 2 seconds, so a burst is one request. Every turn replies within 120 seconds, falling back to a truthful message if the model is slow or down. Reply, "handled" marks and transcript commit together; a crash means the turn is redone on restart, and booking idempotency keys derived from the turn stop a redo from booking twice. One reply per turn, no "on it" messages: the contract has no typing indicator, so a holding message would be answered before the real one.

**The mock provider.** 25 invented restaurants, so nothing claims availability at a real one. Availability is deterministic per slot and scarce at peak. It enforces rules that need judgement (deposits, card holds, closed days, party limits, allergen handling from "shared kitchen" to "coeliac accredited" to "fully gluten-free") and fails the way real APIs do (latency, a table taken between checking and booking, timeouts, half of them *after* the booking was written). Failure rates are set by env and are off in tests.

**Assumptions.** Members are in London and mean London time. Every member has a card on file for deposits. Restaurants confirm instantly (no "pending" bookings). Halcyon's simulated members have no calendars, so there's no calendar integration.

## Practice

Three runs against Halcyon's public member, fixing what each showed:

| Run | Succeeded | Member messages | Cost (USD) | What it led to |
|---|---|---|---|---|
| 1 | 1 of 3 | 11 | 0.63 | Allergen levels in the data; no "the restaurant will confirm"; provider-calculated cancellation terms; shorter replies; places in memory |
| 2 | 2 of 3 | 7 | 0.18 | Coeliac-accredited kitchens; the latest requirement counts; upcoming vs past bookings labelled |
| 3 | 2 of 3, all outcomes good | 6 | 0.10 | "Previously booked" marked in search results |

Conversation caching and fewer turns account for the cost drop. To avoid tuning for one persona, `scenarios/` holds nine members of my own, each probing a behaviour: memory used next time, an impossible request, a deposit, changes over three conversations, a correction, a burst for a group of 14, a guest's allergy, a member who wants no options. `scripts/simulate.py` plays them over the real contract and timing rules and checks outcomes (the booking that exists, notes saved or retired, messages needed), not wording.

## What I'd do next

- **A status channel in the contract** (typing, searching, booking), so members see progress without it counting as a message.
- **Pending confirmations and replies from restaurants**, with follow-up messages when they arrive.
- **A background memory pass** after each conversation, if the scenarios show the assistant missing notes.
- **Cheaper long conversations:** summarise old search results in the transcript; leaner search results.
- **Calendar integration**, so the assistant checks the member is free, not just their usual time.
- **Scenarios in CI** against a staging deploy, as a regression check on every prompt change.

## Time spent

[Pawel: fill in.] Built with Claude as a coding agent: I made the product and engineering calls (logged in `DECISIONS.md`), it wrote most of the code to them, and we iterated on practice transcripts together.

## Worth looking at

- `app/assistant/prompt.py`: the assistant's behaviour in plain English.
- `app/booking/mock/`: the mock provider and its venues.
- `app/assistant/tools.py`: the rules enforced in code.
- `tests/`: 42 tests, offline, with a scripted model.
- Review endpoints, not part of the contract, behind the same token: `GET /members/{id}/bookings` and `GET /members/{id}/notes`.

## Running it

```
pip install -r requirements-dev.txt
pytest                                          # offline, no API key needed
API_TOKEN=dev ANTHROPIC_API_KEY=... uvicorn --factory app.main:create_app
python -m scripts.simulate scenarios/*.yaml --url http://localhost:8000 --token dev
```

Settings (env): `API_TOKEN`, `ANTHROPIC_API_KEY`, `DB_PATH`, `LLM_MODEL`, `TURN_LIMIT_S`, `BURST_WAIT_S`, `MOCK_TRANSIENT_ERROR_RATE`, `MOCK_SLOT_RACE_RATE`, `MOCK_LATENCY_S`.
