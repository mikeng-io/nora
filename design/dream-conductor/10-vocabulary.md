---
type: design
title: "Vocabulary — memo, note, idea, event"
description: "Which word is reserved for what. A memo is new: a settled thing the group wants kept. A note stays what the code already says it is. An idea is not a type. An event, to a person, is a gathering. The routing rule a model applies between them."
tags: [nora, vocabulary, memo, note, gathering, remember, glossary]
timestamp: '2026-09-27'
edges:
  - {dst: 40-specialists, rel: motivates, provenance: asserted}
---

# 10 · Vocabulary

## 1. What the words mean today

- **note** — `nora/domain/notes.py` opens: "A note is TRACKED UNCERTAINTY —
  never a settled claim". Every note carries a `closing_condition` and an
  anchor. Designs 33 and 41 widened it to "a thing the room must not drop",
  which covers a commitment, a debt, a plan; it is still forward-looking and
  still closes. Tools: `note_create`, `note_update`, `note_read`.
- **remember** — writes a Honcho conclusion about **the person speaking**, and
  only that person (`nora/agent/tools.py`, the `remember` tool).
- **fact** — the knowledge tier's settled claim about the world. Deleted
  2026-09-07 (#115); the tables remain, nothing writes them.
- **Event** — in code, the value a fire turn is about (DEC-139). The calendar
  thing people call an event is a **Gathering** (`nora/domain/gatherings.py`),
  though its tag-path tools are still `create_event` and friends.

So a settled thing about the group, a third party or the world has **no
home**: "the wifi password is …", "we agreed the deposit is split four ways",
"Ken's flat is 12B". `remember` would file it as a fact about whoever spoke;
`note_create` refuses it (nothing would close it); the curator's own
objective says such a thing has "nowhere to go".

## 2. The decision

| word | reserved for | status |
|---|---|---|
| **memo** | a settled thing the group wants kept: no closing condition, cited, revised when the room restates it | **new** |
| **note** | a thing the room must not drop until it closes: an open question, a plan, a commitment, a debt | **stays** as built |
| **idea** | nothing. A proposal is a `## Proposed` line inside a note's body (DEC-213) | **not a type** |
| **gathering** | a dated thing people attend, with RSVPs | stays; the model-facing word for it is "event", the code word stays `Gathering` |

**Why note stays rather than being renamed.** The rename was considered: it
would free "note" for the settled thing, which is what the word means in
English. It is rejected on cost and on the fact that it buys nothing a new
word does not. "note" is the name of four tables, six migrations, three tag
tools, the `<notes>` block, R-55, DEC-189/194/202/213, ADR 0018, the metrics
and the dreamer eval suites. A rename is a migration with no behaviour change; a new
word is additive. And once **memo** exists, the two words divide cleanly on
the one property that matters — **does it close?** A memo does not; a note
does. The discriminator design 19 §1 already uses ("a thing with no closing
condition is not a note") now points somewhere instead of nowhere.

**Why idea is not a type.** "Idea" names a proposal. The note body already
has a section for proposals, and a proposal with nothing to decide is
chatter. A third tier keyed on "someone floated something" would swallow
both of the others.

## 3. The routing rule

The one sentence a model applies, on the tag path and in every specialist:

| the thing | goes to |
|---|---|
| settled, about the person speaking | `remember` (Honcho) |
| settled, about the group, someone else or the world, and worth keeping | **memo** |
| not settled, and the room must not drop it | **note** |
| a dated thing people attend | **gathering** |
| something that should happen at a time | reminder (`remind`) |

A note that closes may leave a memo behind: "venue open" closes on "Yardbird,
booked for 8", and the booking is a memo if anyone will ask again. That is the
curator's `closed` verdict gaining a third discharge kind beside `commitment`
and `person_claim` ([40](40-specialists.md) §3).

## 4. What a memo is

- **title** (≤ 60), **text** (the settled thing, restated whole on revision,
  as DEC-213 does for a note body), **established_in** (its origin room),
  **unit**, and **cited message ids**. Every memo cites a person's message;
  Nora's own rows are excluded, as for notes.
- **No anchor, no closing condition, no revisit.** It is revised when the room
  restates it and retired only when the room says it no longer holds
  (`retired-superseded`, `retired-withdrawn`).
- Written on the tag path by `memo_write` when someone says to keep it
  («記低», "note that", "remember that" about anything but themselves), and by
  the memo specialist when the room settles something it will need again.
  Read as a `<memos>` index plus `memo_read`, unit-scoped like notes.

**The open position is secrets.** "The wifi password is …" is exactly the
case that motivates the memo and exactly the case the egress screen
(R-18/R-28) exists for. Three answers, not decided here: refuse credentials
outright; accept them with a `sensitive` flag that keeps the text out of every
rendered index and returns it only from `memo_read` on the tag path; or accept
them only when a person wrote them with an explicit «記低». The memo tier does
not ship until the operator picks one.

## 5. Model-facing text

The trigger word «記低» today routes to `remember`. With memos it splits on
the subject: about the speaker → `remember`; about anything else → `memo_write`.
That is a tool-text change under ADR 0015, measured with `tool_selection`
before and after (`nora-tool`), and it lands with the memo tier, not before.
