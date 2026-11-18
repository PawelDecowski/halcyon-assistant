# Halcyon assistant

A restaurant-booking assistant for Halcyon members: it holds conversations over the two-endpoint contract, books through a mocked booking provider, and remembers each member across conversations.

- **Service:** https://halcyon-assistant-dev.up.railway.app
- **Every decision, and what each practice run changed:** [`DECISIONS.md`](DECISIONS.md)

`POST` stores the message (once per `message_id`) and returns 202; a background worker answers it, with Claude Sonnet running a short tool loop (search, check, book, change, cancel, save a note) against a `BookingProvider` interface. One container on Railway with SQLite on a volume: the work happens after the response and state must last weeks, so scale-to-zero platforms were out. At larger scale: Postgres, a job queue, several instances.

## Decisions and assumptions

- **Book or ask.** Ask only what changes the booking, all in one message. Fill gaps from the member's history and say so ("8pm, your usual"). Book straight away when the request is specific or the member says "just book it"; offer two or three options when their taste is unknown.
- **Money needs a yes.** Deposits, card holds and late-cancellation fees are stated with the terms that will really apply for that date, and nothing is booked or cancelled until the member agrees.
- **Memory.** Rebuilt every turn from notes of what the member said (corrections retire a note, never delete it), booking history (patterns are calculated, never stored, so they don't go stale) and the end of their last three conversations. A member's file fits in the prompt, so no retrieval step can miss anything.
- **Honesty.** A booking exists only if the provider confirmed it. A note to a restaurant is one-way, so the assistant never claims a restaurant was asked or will confirm. If a turn fails, the member is told exactly what was already done.
- **Rules in code, not only in the prompt.** The member's own allergies go on every booking (a guest's only when that guest comes); spending money needs an explicit "member accepted" flag; an identical booking is never made twice; members can only touch their own bookings.
- **Reliability.** Repeated deliveries are answered once; a burst of messages is one request; every turn replies within 120 s, with a truthful fallback if the model is down; a crash redoes the turn without booking twice. No "on it" messages: the contract has no typing indicator, so one would be answered before the real reply.
- **The mock.** 48 invented restaurants (nothing claims availability at a real one), deterministic availability, scarce at peak. It enforces rules that need judgement (deposits, closed days, party limits, allergen handling from "shared kitchen" to "fully gluten-free") and fails like real APIs: latency, a table taken between checking and booking, timeouts, half of them *after* the booking was written. Each failure happens on 1% of calls by default (`MOCK_TRANSIENT_ERROR_RATE`, `MOCK_SLOT_RACE_RATE`).
- **Assumptions.** Members are in London. Every member has a card on file. Restaurants confirm instantly. Halcyon's members have no calendars, so there is no calendar integration.

## What I'd do next

- A status channel in the contract (typing, searching), so members see progress without it counting as a message.
- Pending confirmations and replies from restaurants, with follow-up messages when they arrive.
- A background memory pass after each conversation: the practice scenarios caught one missed note in nine.
- Cheaper long conversations: summarise old search results in the transcript.
- Calendar integration, so the assistant checks the member is free, not just their usual time.
- The practice scenarios in CI against a staging deploy, as a regression check on every prompt change.

## Time spent

About 5 hours of hands-on time over four evenings, not counting time spent waiting for practice runs. Built with Claude as a coding agent: I made the product and engineering calls, it wrote most of the code to them, and we reviewed practice transcripts together.

## Worth looking at beyond the code

- **`app/assistant/prompt.py`:** the assistant's behaviour in plain English.
- **`app/booking/mock/`:** the mock provider and its venues.
- **Practice.** Halcyon's public member went from 1 of 3 conversations succeeding to 3 of 3. To avoid tuning for one persona, `scenarios/` holds nine members of my own, each probing a behaviour, and `scripts/simulate.py` plays them over the real contract and timing rules, then checks what was really booked and noted. Reading transcripts mattered more than the scores: it found bugs no status showed, such as the assistant telling members they "already had" a booking it had just made, and a search that let venues unable to seat a group hide one that could.
- **Review endpoints** (not part of the contract, same token): `GET /members/{id}/bookings` and `GET /members/{id}/notes`.

## Running it

Python 3.11 or newer.

```
uv venv && source .venv/bin/activate && uv pip install -r requirements-dev.txt
pytest                                    # offline, scripted model, no API key needed
API_TOKEN=dev ANTHROPIC_API_KEY=... uvicorn --factory app.main:create_app
python -m scripts.simulate scenarios/*.yaml --url http://localhost:8000 --token dev
```
