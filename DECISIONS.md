# Decisions

Part 1 is what the system does now and why, by topic. Part 2 is what practice runs found and how each finding changed it. Where practice changed a decision, Part 1 says so in a clause.

## Part 1: decisions

### Hosting and architecture

- **Railway, one container, deploy on push.** No CI to maintain; always on, about $5 a month.
- **One process, SQLite on a volume.** POST stores the message and returns 202; a background task in the same process writes the reply. The work happens after the response and state must last weeks, so platforms that scale to zero, throttle CPU after a response or wipe the disk were ruled out.
- **At larger scale:** Postgres, a job queue instead of in-process tasks, several instances.

### The contract and reliability

- **Repeated deliveries:** `message_id` is unique in storage, so a redelivery gets 202 but is stored and answered once.
- **Crash-safe turns:** the reply, the "handled" mark on the messages it answers and the assistant's transcript commit in one transaction. On startup, anything left unanswered is answered; booking keys are derived from the conversation and turn, so a redone turn can't book twice.
- **One turn at a time per conversation.** A turn starts once the member has been quiet for 2 seconds, so a burst of messages is one request (practice: the first version counted from the first message and answered a slow burst twice). Messages arriving mid-turn are answered in the next turn.
- **A reply within 120 seconds, always.** If the model is slow, down or fails, the member gets a truthful fallback built from what the turn actually changed ("I booked X, ref Y; nothing else"), never silence.
- **One complete reply per turn, no "on it".** The contract has no typing indicator, so a holding message counts as a reply and would be answered before the real one; Halcyon's member waits up to 180 s, so silence while working costs nothing. Only the final message of a turn is sent (practice: sending all text from a turn recovered a dropped answer but leaked a working note to a member, so the instructions now say plainly that only the final message is seen).
- **Claude Sonnet for every turn:** judgement is most of what members notice. The assistant keeps the full record of the current conversation, searches included, so "the second one" refers to something already found.

### When to book and when to ask

- **Ask only what changes the booking, all in one message**, and include dietary needs when asking anyway. Never ask what the occasion makes obvious or what was already answered.
- **Fill gaps from history and say so.** Two or more bookings at a similar time make a usual time to assume ("8pm, your usual"); one is a hint to suggest. A time accepted as a compromise is saved as the time they wanted, which outranks booking history (practice: it anchored on 7.15pm when the member had wanted 7.30pm).
- **Specific request: book it. Choice left to us:** two or three options with a recommendation for a member whose taste is unknown; book our pick and offer to swap for one we know or who said "just book it".
- **Conditional requests wait.** "Check X before you confirm" means no booking until the condition is met or the member says go ahead.
- **When the request can't be met:** one sentence on why, the closest real option with its trade-off, at most one alternative. Alternatives keep what made it the member's choice (an omakase request wants intimate and chef-led, not just a free table); if the time at a named venue is the problem, also offer a similar venue at their time. "Somewhere new" excludes venues booked before; the latest version of a requirement counts.
- **Every assumption is stated**, so the member can correct it in one message.

### Money

- **Nothing that costs money without a clear yes:** deposits, card holds and late-cancellation fees need an explicit "member accepted" flag on the call, set only after the member agreed.
- **The terms are the real ones for that date.** Search and availability give `terms_if_booked` for the date and time, so a deposit already inside its window is never described as refundable (practice: a member agreed to £360 on the general policy's terms, for a dinner that was already non-refundable). In a list of options money is mentioned briefly; full terms come when asking for a yes.
- **Cancellation terms are worked out by the provider** and repeated, never calculated by the model.

### Memory

Rebuilt for every turn from three sources:

- **Notes** of what the member said: allergies, dietary needs, likes and dislikes, people they dine with, places (home, office), occasions, how they like to be served. Only what they said; one Italian booking does not become "loves Italian". Corrections retire a note rather than delete it.
- **Booking history**, with patterns (usual time, party size) calculated each time, never stored, so never stale. Bookings are labelled upcoming or past, with how far away they are, and those made in the current conversation are marked as such (practice: without the mark, the assistant told four members they "already had" a booking it had just made).
- **The end of their last three conversations**, so "same as last time" resolves.

Everything goes in the prompt: a member's file is small, so no search step can miss anything. Notes are written by the assistant during the conversation. A background extraction pass is the next step if notes are missed; practice found one miss in nine scenarios.

### Honesty

- A booking exists only if the provider confirmed it.
- A note to a restaurant is one-way: the assistant never claims a restaurant was asked, has replied or will confirm (practice: it once said a restaurant "has been asked to confirm").
- It never claims a place is "the only option" unless the search shows no other, never says the member has been somewhere unless it is a past booking, and describes allergen handling exactly as stated.

### Rules enforced in code, not only in the instructions

- The member's own allergies go on every booking. A guest's allergy is saved with their name and passed on only when that guest is coming (practice: a client's allergy would otherwise have followed the member to every dinner).
- Spending money needs the "member accepted" flag.
- An identical booking is never made twice, whichever turn asks; a second booking the same day needs an explicit flag.
- Members can only change or cancel their own bookings.
- Search results mark venues the member has booked before.

### The mock booking provider

- **Inside the app, behind an interface.** The assistant only sees `BookingProvider`: async, JSON-ready results, failures as `BookingError` with a stable code and a `retryable` flag. A real API would be a second implementation.
- **48 invented London restaurants**, three or four in most areas, so nothing reads as a claim about a real restaurant and "nothing there" is as rare as on a real network (practice: with 25 venues, one seafood restaurant in all of London decided an outcome). The mix is realistic, not fitted to any scenario: Islington has one coeliac-safe kitchen, so Halcyon's member can't get a new one there.
- **Deterministic availability**, scarce at peak, each venue with its own demand; our bookings use up tables.
- **Rules that need judgement:** deposits and card holds, party limits with private dining for bigger groups, closed days, a booking window, and allergen handling in four levels from "shared kitchen" to "fully gluten-free" (practice: with only "gluten-free options", a coeliac could not be served honestly). A diet is met by a stricter one (a pescatarian can eat vegetarian). Search lists venues with tables first, so ones that can't take the booking never hide ones that can.
- **Failures like a real API**, set by env and off in tests: latency, a table taken between checking and booking, and timeouts, half of them after the booking was written. Idempotency keys make a retry return the original booking. Each failure defaults to 1% of calls: often enough to happen, rare enough that the mock isn't what decides a member's outcome.
- **Bookings confirm instantly.** "Pending, the venue will confirm later" would need follow-ups after the conversation has moved on; listed under next steps.

### Cost

Each turn makes several model calls, and each resends the instructions, tools, member file and conversation. The instructions, tools and the conversation so far are marked for caching, so later calls in a turn pay a tenth for what was already sent. Every call logs its tokens and estimated cost. Practice cost fell from about $0.63 to $0.10 for the same three conversations.

### Testing and practice

- **Offline tests** with a scripted model cover the contract, reliability, the mock and the rules in code.
- **Halcyon's public member** for end-to-end runs; **Halcyon's custom scenarios** for behaviours it never tests.
- **Our own harness:** `scripts/simulate.py` plays the nine members in `scenarios/` over the real contract and timing rules, then checks outcomes (what was really booked and noted, messages needed to reach the goal), not wording.
- **Reading transcripts beats scores.** Several bugs below had no failing status. Wording problems are fixed only once they repeat, to keep the instructions short. Data is never changed to suit one persona.

## Part 2: what practice changed

**Halcyon's public member** (Oliver: a client lunch, then dinners with his coeliac husband): 1 of 3 succeeded, then 2, 2, 2, 2, and 3 of 3.

- No venue could serve a coeliac honestly: allergen-handling levels in the data.
- It said a restaurant "has been asked to confirm": booking notes are one-way.
- A cancellation deadline was miscalculated, and free venues claimed a window that cost nothing to miss: the provider works out the terms.
- Replies too long; questions re-asked; dietary needs asked too late: length matched to the member, never re-ask, dietary needs in the first question.
- "Same again" lost the area; an upcoming dinner was called "last Friday": places in notes, bookings labelled with how far away they are.
- A relaxed requirement was ignored: the latest requirement counts; coeliac-accredited kitchens added as a safe level.
- "Somewhere new" offered last week's venue: venues booked before are marked in search results.
- Full card-hold terms for an option not chosen; anchored on a compromise time: brief terms in lists; the wanted time is saved.

**Halcyon's custom scenarios** (a birthday growing from 12 to 16, a deposit, a guest's allergy, "just book it", change then cancel, an impossible request): all succeeded, but reading them found:

- "The only place that can seat 16", when another could: venues that couldn't take the booking were crowding search results.
- "Nothing suitable for a pescatarian" in an area with a vegetarian restaurant: the dietary filter matched labels literally.
- A guest's allergy would have followed the member to every booking.
- Alternatives to an omakase counter ignored why it was chosen.
- "I haven't passed anything to the restaurant", twice: confirmations mention notes only when there are some.

**Our harness** (4, then 6, then 7 of 9; 4 of 4 on re-running the affected scenarios):

- Members told they "already had" a booking made moments before: bookings made in the conversation are marked.
- A latent double booking in a later turn: identical bookings are never made twice.
- An answer dropped when written alongside an action, then a working note leaked when all text was sent: final message only, with instructions that say so.
- A deposit described as refundable for a dinner already inside its window: terms for the date before consent.
- Booked despite "check before you confirm": conditional requests wait.
- One seafood restaurant in London: denser venue data.
- The harness's own mistakes, also fixed: counting a member's closing "thanks", limits that ignored our own rules, and expectations a simulated member made impossible.
