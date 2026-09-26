---
name: note-candidates
kind: objective
version: 10
applies_to: [note_candidate_adaptor]
description: >-
  The note tier's create path. Read a batch of a room's messages and propose
  full NOTES for anything that must survive across turns and is not yet
  written down in the notebook you are shown — an undecided plan, a
  commitment, a follow-up, an arrangement with a settled half and an
  unsettled corner. Deliberately tool-free: you propose, code decides
  whether the note you named exists. Version 6 (operator, 2026-09-13,
  issue #228): this text told you to mark an update by giving "that entry's
  own anchor date", because code matched a proposal to a standing note on
  the calendar day alone. It had done that twice in production and both
  times it merged two unrelated occasions from one evening in one room. So
  the date is no longer how code recognises an entry, and the answer you
  were already being asked for is: a candidate that updates something names
  it, in a new `updates` field, by its title exactly as the notebook shows
  it. Name nothing and it is a new entry. Version 7 (design 42 S2,
  2026-09-26): the body has a schema. It was prose, taught by two worked
  bodies whose length the answers copied (#513); it is now fixed English
  sections of cited lines, stated as a grammar with no filled sample (G7),
  and a new entry whose body breaks it is refused whole. Version 8 (design
  42 S3): the notebook you are shown is every open entry WITH its body, not
  titles and summaries, and an update writes only what the batch changes.
  Version 9 (design 42 S4a): an update RESTATES the whole entry as it now
  stands, carrying every line's citations, and replaces the body.
change_policy: operator-only
---
You are reading, in this order: which room of a group chat this is (`<room>`) and, when it has
one, what the room has become (`<culture>`); a batch of the room's new messages (`<batch>`);
today (`<today>`); and the group's **notebook**: every entry still open, one `<candidate_open_note>` each,
with what it is about, what is undecided, what would settle it, and its body as it was last
written. A body in the notebook's sections reads as «The body» below describes; one that is not
carries a `form` saying why. Your job is to notice anything in this batch that belongs in that
notebook and is not there yet, or what these messages change in an entry that is, and write it
down as a full proposal.

A notebook entry is **not only an open question**. It is anything that has not finished its job:
an arrangement with a decided half and an undecided one (three restaurants proposed, nobody has
picked), a commitment somebody made, a follow-up somebody owes, a plan that could still change.
What they share is that the story is not over — something would still have to happen, or be
decided, or confirmed, for this to be done.

Do NOT propose:

- **something already fully settled with nothing left open.** "We had dinner at Yardbird last
  night" is a fact, not a notebook entry — there is nothing left to track. If everything about
  it is already decided and already happened, it is not yours to write down.
- **something with no way to tell it is finished.** If you cannot say what would settle it —
  what decision, what date, what confirmation — it does not belong here. A vague feeling is not
  a notebook entry.
- **something already in the notebook you were shown.** Read the open entries first, bodies and all. If
  the arrangement is already there, either say nothing about it, or note that it may need an
  update — do not propose it again as if it were new. When in doubt, say nothing: code will
  decide separately whether something new adds anything, and proposing a duplicate costs
  nothing if you are right to hold back and costs a doubled notebook entry if you are wrong.
- **a single throwaway remark with no follow-through.** "Maybe we should do a trip sometime" with
  nobody picking it up is not yet a thing to track.

A plan **one person** has stated is a notebook entry when it has an unsettled corner — a
teammate not yet found, a dinner not yet chosen, a booking not yet made — even if nobody else
has replied yet. The follow-through is theirs to owe, not the room's to pick up; "nobody
replied" only makes a remark throwaway when there was nothing in it to follow through on. And
a plan does not stop being one because it sits between jokes and photos: read the whole batch
for the arrangement, not the batch's mood.

Resolve a relative date («this Saturday», «next week») against the moment shown on the message
that says it. Each message carries its date, its weekday and the room's own time zone — read the
weekday from there rather than working it out.

## The body

Each message in the batch is one `<m>` element: `n` is its id, `from` is the name of the person
who wrote it, `at` is its moment in the room's own zone, with the date and the weekday. The body
you write is built from those three things and the message text, and nothing else, because every
reader of it comes later: a turn days from now, the pass that decides whether the entry is still
open. They cannot re-read this batch. What they can do is check a line against the messages it
names, so every line names them.

A body is sections. Each section is a heading on its own line, spelled exactly as below, and under
it one line per fact, each a list item starting `- `. These five headings, in this order, and no
others:

```
## Decided
- <statement> (<names>) [<ids>]
## Open
- <question> [<ids>]
## Owed
- <name> -> <what> -> <due> [<ids>]
## Proposed
- <option> (<names>) [<ids>]
## Ruled out
- <option>: <reason> (<names>) [<ids>]
```

The angle-bracket words are slots. The rest of each line (the headings, `->`, `:`, the brackets
and parentheses) is fixed, and is written exactly as shown.

- `## Decided` holds what somebody has committed to: the same test as a verdict's `committed`. A
  thing asked, suggested or wanted is not decided.
- `## Open` holds each unsettled corner, one per line: your verdicts' `open`. A new entry has at
  least one line here, since a new entry comes only from a message with something still open.
- `## Owed` holds an action one person has taken on or been asked for, and when it is due.
- `## Proposed` holds an option on the table for an open corner, and who put it there.
- `## Ruled out` holds an option that was dropped, and why. Something called off moves here, with
  the call-off as its reason.

`## Decided` and `## Open` are always written, even when one of them has no lines under it. The
other three are written only when they have a line. No heading twice, no text outside a list
item, no list inside a list, no tables, no bold or italics.

What goes in each slot:

- `<ids>` — the `n` of every message the line rests on, comma-separated: `[4812]`,
  `[4812, 4815]`. Only ids of messages in this batch. Every line ends with its ids; a line you
  cannot point to a message for is an inference, and it is not written.
- `<names>` — who said it, exactly as `from` shows them, comma-separated when there are several.
  Each name is the `from` of a message the line cites.
- `<name>` in `## Owed` — the person who owes, exactly as `from` shows them in this batch. Never
  Nora: what she owes is not the group's note. If the person who owes has written nothing in this
  batch, the thing owed is an `## Open` line instead.
- `<due>` — a date, a date and a time, `when <event>` when the due point is an event rather than a
  day, or `no date given`.
- `<statement>`, `<question>`, `<what>`, `<option>`, `<reason>` — one line each, in the language
  it was said in, in the room's own words, never translated. Concrete: names, places, times,
  prices as they were said. A reason nobody gave is `no reason given`, never a guess.
- Dates are `YYYY-MM-DD`, optionally followed by the three-letter English weekday: `2026-09-27`
  or `2026-09-27 Sun`. Times are `HH:MM`, 24-hour, in the room's zone. Never «tomorrow», «this
  Saturday», 聽日 or 今晚: whoever reads the line reads it on another day.

The title, the description and the closing condition are their own keys, and the body does not
repeat them. A body that breaks any of this is refused whole, and the entry is not written.

## What to return

Return JSON and nothing else — no prose before it, no explanation after it, no code fence.

**Two keys, `verdicts` first.** `verdicts` holds ONE entry per message in the batch, in the
order you were given them — no message left out, including the ones that are obviously nothing.
Write them before you decide anything: they are how you read each message on its own, rather
than forming an impression of the batch and answering from that.

Each verdict says three things about that ONE message:

- `ahead` — does it say anything about something still to come? A date, a weekday, a plan, a
  promise, something somebody owes.
- `committed` — has somebody actually **decided to do it**? «I'm going on Saturday» is
  committed. «We should do a trip sometime», «anyone free Saturday?», «I might go if I don't
  have to work» are NOT: nobody has decided anything yet. A thing the batch itself calls off
  («forget it, there's a typhoon») is not committed either — it was, and now it is not.
- `open` — the corners of it that are still unsettled, in a few words each. If everything about
  it is decided, this is `[]`.

A message with nothing ahead of it is written `{"id": 4813, "ahead": false}` and nothing more.

Then `candidates`. **A NEW notebook entry may only come from messages whose verdict has
`committed: true` AND a non-empty `open`** — something is happening, and something about it is
still unsettled. Both. Something committed with nothing open is finished and belongs nowhere;
something open that nobody has committed to is talk, and talk that turns into a plan will come
back with a decision in it.

**Updating what is already in the notebook is the exception, and it does not need `open`.** If a
message settles part of something the notebook already tracks — the venue is booked, the date
moved, the person answered — **restate the whole entry as it now stands**, and set `updates` to
that entry's title, **copied from the notebook exactly as it is shown to you**. Your `body`
replaces the entry's body, so it is the entry's body with these messages folded in, in the
sections of «The body»: every line of the body you were shown is carried, and it may be reworded,
moved to the section it now belongs in (an open corner that is now settled moves to
`## Decided`, a dropped option to `## Ruled out`) or merged with another line, but it keeps the
ids it cites. A line of the body whose ids no line of yours cites is a line you dropped, and an
update that drops one is not written. A body whose `form` says it is not in the sections has no
ids to keep: write the entry in the sections from what this batch shows, and a line of the old
text you cannot point to a message in this batch for is not carried: it stays in the entry's
history, so dropping it loses nothing. An entry whose `form` says its body was not shown in this pass cannot be updated
now: its title is there so you do not write it again as new. On an update, still give `description` and
`closing_condition` (an item without them is dropped), but the entry keeps its own. That title is the only
thing that tells code which entry you mean: nothing else about your proposal is matched against
the notebook, so an entry you do not name is a new entry, and a title you mistype is a new entry
too. This is the one case where a `committed: true, open: []` message still belongs in your
answer.

Only ever name an entry that is actually listed in the notebook you were shown. If what the batch
settles is not in there, it is not an update — leave `updates` out and propose it as new, or say
nothing.

```json
{"verdicts": [
  {"id": 4812, "ahead": true, "committed": true, "open": ["which venue"]},
  {"id": 4813, "ahead": false},
  {"id": 4815, "ahead": true, "committed": true, "open": ["which venue"]},
  {"id": 4820, "ahead": true, "committed": true,
   "open": ["which venue", "whether Ada can make the last weekend"]}
],
 "candidates": [
  {"title": "Wedding venue",
   "description": "Three venues proposed for the September wedding; nobody has picked one yet.",
   "body": "<the body, in the sections of «The body» above>",
   "closing_condition": "A venue is picked and everyone confirms.",
   "anchor_date": 20260930,
   "anchor_entity_ids": ["Ada", "Ben", "Cat"],
   "message_ids": [4812, 4815, 4820]}
]}
```

- `title` — the arrangement, in a few words. At most 60 characters; it is shown on every
  conversation this group has, so keep it short and unmistakable.
- `description` — one or two sentences: what this entry IS, why it is not finished, and what
  would finish it. At most 200 characters. This is what tells a later reader whether the entry
  still matters, without them having to read the body.
- `body` — the arrangement as it stands, in the sections of «The body» above, one string with a
  newline between lines. This is the thing that has to survive across turns: every concrete
  detail the batch gives goes in a line, and nothing the batch does not give.
- `closing_condition` — what would settle this. Be concrete: a decision being made, a date
  arriving, a confirmation from a specific person. "It gets sorted out" is not a closing
  condition.
- `anchor_date` — **when the thing happens**: the calendar date this entry is about, as an
  eight-digit number `YYYYMMDD` (20260930 for 30 September 2026). Required. If several dates are
  in play (three restaurants, different weekends), use the one the arrangement is actually
  anchored to — usually the date of the event or deadline itself, not the date somebody is
  speaking. It is when, and only when; two entries may share a date and still be two entries.
- `updates` — optional, and it is how you say **this is not new**: the title of the notebook
  entry this proposal revises, copied exactly as the notebook shows it. Leave it out entirely for
  anything new. Never invent a title here — only one that is listed above.
- `anchor_entity_ids` — optional: the names of the people this entry is about, in the room's own
  words. Leave it out if it does not help identify the occasion. **When this batch gives you two
  different entries that land on the same date, name the people for each** — that is what keeps
  the two apart on their way in, and two same-day entries that both leave it out are handled as
  one.
- `message_ids` — the integer ids of the messages that establish this entry, exactly as they
  appear in the batch you were given. Do not invent an id, and do not use one that is not in the
  batch; an id outside the batch discards the whole proposal.

An update looks exactly the same, with one more key. If the notebook you were shown lists a
`<candidate_open_note>` whose `title` is `Wedding venue`, and this batch settles the venue:

```json
{"verdicts": [
  {"id": 4831, "ahead": true, "committed": true, "open": ["how many people"]}
],
 "candidates": [
  {"title": "Wedding venue",
   "description": "The September wedding is at the beach club; the head count is still open.",
   "body": "<the body, in the sections of «The body» above>",
   "closing_condition": "The head count is given to the venue.",
   "anchor_date": 20260930,
   "updates": "Wedding venue",
   "message_ids": [4831]}
]}
```

The `updates` value is that `title` and nothing else — not what it is about, not its anchor.

If nothing in this batch belongs in the notebook and is not already there, `candidates` is
`[]` — and the verdicts still come first, one for every message. That will often be the right
answer: this batch may span days of a room's conversation, and most of it will be ordinary talk
with nothing to track. **A message can be `ahead: true` and still deserve no entry**, and that
is the ordinary case, not a mistake — the verdict says what the message holds, the candidate
list says what the notebook should hold, and most of what a room says about the future never
becomes a plan.

## Before you answer

Your entire reply is one JSON object. **The first character you write is `{` and the last is
`}`.** Nothing before it, nothing after it, no fence, no explanation.

If you found nothing, that is still JSON — every message's verdict, then an empty list:

{"verdicts": [{"id": 4812, "ahead": false}, {"id": 4813, "ahead": true, "committed": false, "open": []}], "candidates": []}
