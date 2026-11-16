# Decisions

A running log of the calls made while building this, and why. The README is written from it.

## 1. Hosting: Railway, one container, SQLite on a volume

- **Railway** builds the Dockerfile on every push to `main`, with no CI to maintain. Hobby plan (about $5/month), always on.
- **One instance, one process.** POST stores the message and returns 202; the reply is produced by a background task in the same process. SQLite on a Railway volume (`/data`) holds conversations and member state across restarts and deploys.
- **Why not scale-to-zero or serverless (Cloud Run, Render free):** the work happens after the response, and state must persist for weeks. Platforms that throttle CPU after a response or wipe the disk break one or the other.
- **At larger scale:** Postgres instead of SQLite, a job queue instead of in-process tasks, then several instances.

## 2. The contract and reliability

- `message_id` is unique in storage, so a repeated delivery is accepted (202) but stored and answered once.
- Every member message is marked handled in the same transaction that stores its reply. On startup, anything left unanswered by a crash or deploy is answered.
- `/healthz` lets Railway restart a hung process.

## 3. When the assistant books, and when it checks first

Adaptive, based on how much we know and what is at stake:

- **Missing essentials** (date, time, party size): ask, with every question bundled into one message, unless the member's history makes the answer obvious.
- **Time from history:** if past bookings show a clear pattern (e.g. dinners around 8pm), assume it and say so ("8pm, your usual"). One booking or mixed times is a hint to suggest, not assume. A new member is asked.
- **Specific request** (named venue, date, time, size): book it.
- **Choice left to us** ("somewhere nice"):
  - new member or unknown taste: two or three options, one line each, a recommendation, booked as soon as they pick;
  - known member, or one who has said "just book it": book our pick and offer to swap.
- **Money at stake** (deposits, card holds, late-cancellation fees): always a clear yes first.
- **Every assumption is stated** in the reply, so the member can correct it in one message.

Out of scope: calendar integration. Simulated members have no calendar, so it would only be a mock. Listed under next steps.

## 4. Memory

What the assistant knows about a member, rebuilt for every reply from three sources:

- **Booking history** from the booking system: always accurate, never stale. Patterns such as their usual time are calculated from it when needed, not stored.
- **Notes**: dietary needs and allergies, likes and dislikes, people they dine with, occasions, how they like to be served. Only what the member actually said; one Italian booking does not become "loves Italian".
- **Recent conversations**: excerpts of the last few, so "same as last time" resolves.

How it works:

- **The assistant writes notes during the conversation** with a save action. If practice runs show it missing things, add a background extraction pass after each reply.
- **Corrections retire, never delete.** "I eat fish now" retires the vegetarian note and saves a new one; history is kept, the newer note wins.
- **Everything goes in the prompt.** A member's file is small; no search or embeddings, so nothing is missed because a search didn't find it.
- **Allergies are safety-critical**: always passed to the restaurant and repeated in the confirmation.

## 5. The mock booking system

- **Inside the app, behind an interface.** The assistant only sees `BookingProvider` (`app/booking/interface.py`): async, JSON-ready results, failures as `BookingError` with a stable code and a `retryable` flag. The mock (`app/booking/mock/`) is one implementation; a real API would be another.
- **24 invented London restaurants**, varied by area, cuisine, price and atmosphere. Invented so nothing reads as a claim about a real restaurant's availability.
- **Availability is deterministic** per venue, date and time, so a conversation sees a stable picture. Prime time Thursday to Saturday is scarce, early and late is easy, and each venue has its own demand. Our bookings use up tables.
- **Rules that test judgement:** deposits and card holds that must be accepted first, maximum party sizes (with private dining for bigger groups), closed days, an omakase counter that can't do dietary needs, a booking window, and late-cancellation charges reported before they apply.
- **Failures that test reliability**, set by env (`MOCK_TRANSIENT_ERROR_RATE`, `MOCK_SLOT_RACE_RATE`, `MOCK_LATENCY_S`) and off in tests: simulated latency, timeouts (half of them after the booking was written), and a table taken between checking and booking. Every booking carries an idempotency key, so a retry returns the original booking and never makes a second one.
- **Bookings confirm instantly.** "Pending, the venue will confirm later" would mean follow-ups after the conversation has moved on, which clashes with the 15-second reply rule. Listed under next steps.
- **`GET /members/{id}/bookings`**, behind the same token, is a review and testing aid, not part of Halcyon's contract: it shows what the booking system actually holds, so practice scenarios and reviewers can check it against what the assistant said.

## 6. Conversation and reliability

- **One complete reply per turn.** The contract has no typing indicator: any assistant message counts as a reply, and the member answers 15 seconds after the last one. A holding "on it" would be answered before the real reply arrives, and would add to the message count. Halcyon's member waits up to 180 s for a first reply, so silence while working costs nothing. (Next step for a real product: a separate status channel in the contract.)
- **Bursts are one request.** Each turn starts after a 2-second wait (`BURST_WAIT_S`), and every unanswered message is answered together. Messages arriving mid-turn are answered in the next turn, never dropped.
- **A reply within 120 seconds, always** (`TURN_LIMIT_S`). If the model is slow, down or fails, the member gets a truthful fallback built from what the turn actually changed ("I booked X, ref Y, nothing else"), never silence.
- **Crash-safe turns.** Reply, "handled" marks and the assistant's transcript commit together; an interrupted turn is redone on restart, and booking keys derived from the turn stop a redo from booking twice.
- **Claude Sonnet** for every turn (`LLM_MODEL`): judgement is most of what is graded. If turns are slow, the first fix is fewer steps per turn, not a smaller model.
- **Full record within a conversation**, including searches and their results, so "the second one" refers to something already found.
- **Rules enforced in code, not only in the instructions:** allergies on file are always added to the restaurant's requests; deposits, card holds and late-cancellation fees need an explicit "member accepted" flag; a second booking the same day needs an explicit flag; members can only change their own bookings.
