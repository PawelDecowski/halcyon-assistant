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
- **Bursts are one request.** A turn starts once the member has been quiet for 2 seconds (`BURST_WAIT_S`, at most three such waits), and every unanswered message is answered together. Messages arriving mid-turn are answered in the next turn, never dropped.
- **A reply within 120 seconds, always** (`TURN_LIMIT_S`). If the model is slow, down or fails, the member gets a truthful fallback built from what the turn actually changed ("I booked X, ref Y, nothing else"), never silence.
- **Crash-safe turns.** Reply, "handled" marks and the assistant's transcript commit together; an interrupted turn is redone on restart, and booking keys derived from the turn stop a redo from booking twice.
- **Claude Sonnet** for every turn (`LLM_MODEL`): judgement is most of what is graded. If turns are slow, the first fix is fewer steps per turn, not a smaller model.
- **Full record within a conversation**, including searches and their results, so "the second one" refers to something already found.
- **Rules enforced in code, not only in the instructions:** allergies on file are always added to the restaurant's requests; deposits, card holds and late-cancellation fees need an explicit "member accepted" flag; a second booking the same day needs an explicit flag; members can only change their own bookings.

## 7. Changes after practice run 1

Halcyon's public member (Oliver: a client lunch, then dinners with his coeliac husband) scored 1 of 3 conversations as succeeded. Causes and fixes:

- **The data could not satisfy a coeliac.** Every venue said only "gluten-free options", so a good outcome was impossible and the honest assistant failed the outcome. Venues now carry an allergen-handling level (`cannot_accommodate`, `shared_kitchen`, `separate_preparation`, `dedicated_gluten_free`); searching for "coeliac" finds only fully gluten-free kitchens. Added Hearth & Field, Islington, which has one.
- **It implied it had asked the restaurant.** A booking note is one-way; the instructions now say so and forbid claiming the restaurant was asked or will confirm.
- **It miscalculated a cancellation deadline**, and the mock's free-cancellation venues claimed a 24-hour window that cost nothing to miss. The provider now works out the terms for every booking ("Free to cancel until Thursday 19 November, 7.30pm. After that, £80 is charged" or "Already inside the window"), and the assistant repeats them. Free venues are now simply free to cancel any time.
- **Replies too long** for a member who writes two or three sentences: match the member's length, one recommendation and at most two one-line alternatives, no unasked-for details.
- **Asked again what was answered; asked about dietary needs too late**: never re-ask; when asking anyway, include dietary needs if unknown.
- **"Same again" lost the area, and an upcoming dinner was called "last Friday"**: a `places` note category (home, office), "same again" defined in the instructions, and each booking in the member context shows how far away it is ("in 4 days").
- Not done: venues have no spice information, so "nothing spicy" can only be noted.

## 8. Changes after practice run 2

2 of 3 conversations succeeded (from 1), 7 member messages (from 11), cost about $0.18 (from about $0.63). Remaining failures, all in the third conversation:

- **A relaxed requirement was ignored.** The member accepted "not entirely gluten-free, but safe", and the assistant kept searching only fully gluten-free kitchens. Partly the data: "separate preparation" meant "no guarantee", so only a dedicated kitchen counted as safe. Added a `coeliac_accredited` level (separate preparation, audited: safe for coeliacs) for three venues, including Verdure near Islington; a coeliac search returns both safe levels. Instructions: the latest version of a requirement counts, search again with it.
- **It said they had been somewhere booked for later in the week.** The member context now labels bookings "not happened yet" and "already happened", and the instructions forbid saying they've been somewhere unless it is a past booking.
- **Long replies when explaining a failure**, including an unasked-for brunch suggestion: a failure reply is one sentence on why, the closest real option with its trade-off, at most one alternative, no search recap; never suggest a different kind of thing than asked unless the request is impossible.
- **"Somewhere new" offered last week's venue**: it now rules out every place booked before, unless nothing else is possible and the reply says so.

Caution: the data changes in runs 1 and 2 were shaped by one practice persona. The behaviour rules are what carry over to members we haven't seen.

## 9. Cost: caching the conversation

Practice run 1 cost about $0.63 for three conversations, several times my estimate. Almost all of it was input: each turn makes several model calls, and each call resends the instructions, tool definitions, member context and the whole conversation so far, including large search results.

- **Done:** the end of the conversation is marked for caching on every call, as well as the instructions and tool definitions. Later calls in a turn read everything already sent at a tenth of the input price. Cache writes cost 25% extra, so a single-call turn costs slightly more, but most turns make several calls. Behaviour is unchanged.
- **Measured:** every model call logs its tokens (fresh, cache written, cache read, output) and an estimated cost, tagged with the conversation id.
- **Not yet:** replacing old search results with summaries, leaner search results. Practice runs went from about $0.63 to $0.10 for the same three conversations, from caching plus fewer turns.

## 10. Changes after practice run 3

All three outcomes good; one conversation still counted as abandoned because, asked for "somewhere new", the assistant recommended a new place but also offered last week's, breaking a rule it had been given. Rules the model can miss are now backed by data: search results mark `previously_booked` venues, and the instructions forbid offering them for "somewhere new" unless there is no other option. Also tightened: allergen handling is described exactly as stated (it had called a gluten-free kitchen suitable for "any dietary need").

## 11. Practice scenarios

Halcyon's public member is one persona; three runs against it risked tuning for Oliver. `scenarios/` holds nine members of our own, each probing a behaviour rather than a venue: a vague first request, memory used next time, an impossible request, a deposit, changes over three conversations, a correction, a burst of messages for a big group, a guest's allergy, and a member who wants no options.

`scripts/simulate.py` plays each member with a model over the real HTTP contract and Halcyon's timing rules (2 s polls, the member answers after 15 s of quiet, 180 s limit, optional duplicate delivery), then checks the review endpoints: the booking really exists with the right day, size, time and requests; notes were saved or retired; the member needed no more than N messages. Checks look at outcomes, not wording, so they survive changes to the instructions.

Building it found a real bug: the burst wait counted from the first message, so a slow burst was answered twice. It now waits for the member to go quiet.
