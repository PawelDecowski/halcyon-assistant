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

### Recommendations (in design)

A `/suggestions` page that suggests new experiences to a member, from what we know of them, where they are and what is on.

- **Experiences, not only restaurants.** A new mock catalogue of invented events (concerts, exhibitions, theatre, tastings, supper clubs), each with dates, location, price, tags and capacity, alongside the 48 restaurants. Events are bookable through `BookingProvider`, because a booking made from a suggestion is the signal the system learns from; a suggestion-only catalogue would learn from restaurants alone.
- **Preferences as tags.** Experiences carry tags, and a member's preference notes are turned into structured tags, so a member and an experience can be matched, and "members who like X booked Y" can be counted. Free-text notes alone give nothing to count: "likes natural wine" and "loves low-intervention wines" are just different strings.
- **A separate tagging step, text kept.** When a `likes` or `dislikes` note is saved, a small model call (Claude Haiku 5.5 at low effort, about $0.0002 a note: some 1,200 tokens of tag list and note in, a few dozen out) maps it to tags from the vocabulary, checked in code, and stores them beside the note; retiring the note retires its tags. The same code tags existing notes and re-tags them all when the vocabulary changes. The conversation model is not given a second job mid-booking, and the note text stays for the conversation, so "nothing stuffy" is not reduced to `relaxed` there. Allergies and diets stay hard filters, not tags.
- **Tagging runs after each turn and at startup** (`app/recommendations/tagging.py`). After a turn's reply is committed, the member's untagged likes and dislikes notes are tagged in the background, so the reply never waits; at startup, every note untagged or tagged under another vocabulary (a hash of the tag list stored on each note) is tagged, which covers existing notes and any change to the list. A failed or unreadable call leaves the note for the next pass. When two notes disagree about a tag, the newer wins. Tags live in three columns on the note, so retiring a note retires its tags, and the columns are added to an existing database on open. Tagging on page view was rejected: the page is open, so anyone loading it could cause model calls.
- **Checked against the sample profiles.** Haiku tagged the 24 sample notes for about $0.006 and matched the hand-written tags exactly for four of seven profiles, dislikes included. Most differences were the hand tags reading more into a note than it said; Haiku's one clear miss was "Skis, sails, climbs" without `mountains`. The instructions were left alone (one miss is not a repeated problem). The profiles were rewritten so every tag is backed by a note, making them a check set for the tagger; "Skis, sails, climbs" was kept as a fair hard case. "Likes football, so likes sport" waits for the related-tags graph, where an inferred like can count for less than a stated one.
- **One catalogue, three ways to book.** About 50 invented items in each of six categories (concerts, nature, meeting stars, theatre, watchmaking, hotels and villas), alongside the restaurants. One-off dates (concerts, theatre, meeting stars) have a fixed time and limited seats; regular slots (nature, watchmaking) run on set days; stays (hotels, villas) have check-in, check-out and a price per night. The mock enforces each the way it does tables: real availability, terms before consent, idempotency keys. One search and one booking path, rather than three sets of tools for the model to choose between.
- **Invented, like the venues.** Stars are invented people, since real names would read as claims about real people. Items can be outside London (watchmaking in Switzerland, villas abroad), each with a travel time from London. Dates are set relative to today by fixed rules, so the catalogue never runs out of upcoming items. Each item has its own description, not template text.
- **A vocabulary of 76 tags** (`app/recommendations/tags.py`), each with a one-line definition, in groups: food and drink, music, stage and screen, culture and ideas, craft, sport, nature and outdoors, places to stay, atmosphere, format, occasion. Tags describe taste, not logistics: price, place, travel time, party limits, access, dress code and opening times are fields. Many apply across categories on purpose (`craftsmanship` fits a watch factory and an omakase counter), and some exist as the target of a dislike (`formal` for "nothing stuffy"). A first version, to refine once recommendations are seen working.
- **Matching in two stages.** First, a member's tags are compared with an item's tags as they are: an item scores on the tags it shares with the member's likes, and loses on the ones they dislike. Later, a separate stage adds a graph of related tags (`jazz` near `live-music`, `watches` near `craftsmanship`), so a member can be offered something they have no tag for yet. Exact matching first, because it is simple to test and explain, and gives a baseline that the graph must beat.
- **Stage 1 scoring: count shared likes.** An item scores the number of tags it shares with the member's likes; any disliked tag leaves it out; ties go to the soonest date, then the shortest travel; at most 3 items per category in the top 12. The reason shown is the shared tags ("Because you like jazz and wine"). Weighted scores were left out so that exact matching can be judged on its own first. Later, with real note tagging: each member tag weighted by how recent its note or booking is, so the count becomes a sum of weights; nothing else changes.
- **Hand-written profiles first.** No member has tags yet: note tagging is on hold, restaurant tags are not in the vocabulary, and experiences can't be booked. Seven sample profiles (`app/recommendations/sample_profiles.py`), each a different kind of member written from what they would say, not from the catalogue, let matching and the page be built and judged now. Each keeps the notes it came from, so the tagging step can be checked against it later.
- **Scale and rarity, for far and soon.** One list, with no limit on distance: members can fly anywhere, but suggestions must be realistic. Local things are welcome any time; far-away things soon only when they are worth it. Two properties of an item decide that, apart from how well it matches. *Scale* (1 a treat, 2 special, 3 once in a lifetime) is written by hand for each item, because price, demand and category measure something else: a Tuscan villa is expensive and popular but always there. *Rarity* is counted from the item's dates in the next 12 months (one-off: 1; rare: 2 to 9; occasional: 10 to 49; any day: 50 or more), because something bookable any other day is not urgent. A run in its last few dates of the season is treated as more urgent too ("last chance"), since a yearly count can't tell the first week of a run from the last. A one-off counts what is scheduled, as real data would: in the mock, an item on a yearly cycle has exactly one date in any 12 months, so the catalogue never runs out of one-offs. Scale is set for all 300 items (165 treats, 113 special, 22 once in a lifetime; no stay is a 3, since a hotel is always there), and 17 items are one-offs: a stadium band's small show, a press night, a pop star's soundcheck, a premiere. Each item is one suggestion however many dates it has, shown with its first realistic date and its pattern ("Thursdays, January to March").
- **Timing and distance rules** (`app/recommendations/scoring.py`, a first version to adjust once the lists are seen). Four bands by travel from London: local (up to 1 hour), day trip (3 hours), near (5 hours: the far UK and near Europe), far. Lead times are physical minimums only (today, 1, 2 and 4 days), so a star in New York next week can be suggested; whether it is worth it is a separate bar. Near within 3 weeks needs scale 2+ or a rare or one-off date; far within 3 weeks needs scale 3, or scale 2 and rare; far within 2 months needs scale 2+ or rare; later, anything. An item that fails soon is offered at its first later date that passes, so a weekly treat in Kyoto appears as a trip two months out. Order: score, then the nearer band, then urgency (one-off, last chance, rare, occasional, any day), then the sooner date. (A first draft set the far lead time at 21 days, which made the "far within 3 weeks" rule unreachable; a test caught it.)
- **Interests count in full, qualities half.** Tags are interests (what an experience is: `jazz`, `watches`, `beach`) or qualities (what it is like: the atmosphere, format and occasion groups). Qualities are spread across the catalogue (`quiet` is on 40 items) and say little alone, so a shared quality scores 0.5 and an interest 1, and an item needs 1 to be suggested: one quality alone is not enough, two are. Before this, the sports fan's list was three real matches and nine items that only shared `groups`. Requiring an interest was simpler but would drop reasonable suggestions such as a candlelit recital for a couple who like intimate, romantic evenings; a minimum of 2 whatever the tags would give a member we know little about nothing. A member with few matches gets a short list, not a padded one. The reason line leads with interests, each with a phrase a member would use: "Because you like all things Japanese; it's also intimate". The limit of 3 per category gives way when the list would otherwise be short: the sports fan's four suggestions had left out three motor racing meetings because meeting stars already had three.
- **Ready for a weighted score.** Order stays tag match first, with distance and urgency as tie-breaks, but every suggestion carries its components (match; nearness, urgency, scale and soonness from 0 to 1), and ordering lives in one function, `rank_key()`. Switching to a weighted sum changes only that function. Weights would start by hand, judged by reading lists, then be learned from what members book; that needs each shown suggestion recorded with its components, which is left until real members have tags (sample profiles never book, so their records would only mislead). Hard rules (dislikes, lead times, the far-and-soon bar) stay rules, not weights. Planned, not built: urgency counted from a book-by date (when an item is likely to sell out) rather than the event date, so a show 12 months away that sells out in 3 gets a bump; in the mock from demand and seats, from real providers from on-sale dates and remaining inventory.
- **The page: open, sample profiles only.** `/suggestions` lists the sample profiles and `/suggestions/<id>` shows one list (with `?today=` to see another date). No login, because everything on it is invented; it serves only sample profile ids, so it cannot start showing a real member's preferences when tagging lands. Real members will need their own access (a page password, not the API token in a URL), and nothing shown is recorded yet.
- **Restaurants left out for now.** Their tags predate the vocabulary, and the assistant's restaurant search matches on them, so changing them would change booking. The page is about new experiences, which members can't already get by asking. They come in with learning from bookings, which needs restaurant bookings in the vocabulary anyway: a separate field of vocabulary tags and a small adapter, so the search and the scorer are untouched.
- **The catalogue** (`app/booking/mock/experiences/`): 300 items, about 50 per category, one module each, shared shape and helpers in `common.py`. Dates repeat in cycles counted from a fixed Monday rather than "N days from today", which would move every day so a concert never arrives; seasonal items also carry the months they run. Real brands and institutions are kept out of the text, as with the venues. Built before the booking path and the note-tagging step, which come next.

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
