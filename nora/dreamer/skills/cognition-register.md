---
name: cognition-register
kind: skill
version: 1
applies_to: [cognition_session]
description: >-
  The note and event judgement the Cognition session writes the Register with,
  injected after its instruction for the agenda kinds `message`, `rollup` and
  `self` (`nora.app.cognition_wiring.AGENDA_SKILLS`; design/cognition-loop/
  30-cognition.md §4: the replaced adaptors' objectives become Cognition's
  skills). Distilled from note-candidates.md (what belongs in the notebook, an
  update restates the whole entry and keeps every line's ids), note-merge.md
  (one arrangement, one entry) and 40-register.md §1, §3-§4 (note or event;
  told, deduced, abduced; an abduced value goes out as a question). Written
  from the input side: the agenda block already teaches the line grammar, the
  note index and events blocks say they exist so nothing is written twice, the
  verbs say what they take, and the objective says how an item closes; none of
  that is repeated. The body below the heading is shown as one example rather
  than described, and it is held to the real validator
  (tests/unit/test_cognition_agenda_skills.py). No numbers, no caps, no tool
  that does not exist. Not yet measured on the live catalogue:
  `tool_selection_cognition` before and after says only that routing did not
  regress, never whether the notes are good.
change_policy: operator-only
---

## What belongs in the notebook

An entry is anything that has not finished its job: an arrangement with a decided half and
an undecided one, a commitment someone made, something someone owes, a plan that could still
change. A new note needs both: somebody has decided to do it, and some corner of it is still
unsettled. One person's plan counts when it has an open corner (a teammate not found, a table
not booked), even before anyone answers.

Not an entry: something settled with nothing left open; something with no way to tell it is
finished; a remark nobody picked up («we should go away sometime»); a thing asked, suggested
or wanted that nobody has taken on. Most of what a group says about the future never becomes a
plan, and an item holding only that closes `no_change`. That is the ordinary answer, not a
miss.

## Note or event

An event is a dated thing people will attend: something to do, with a time on the table. A
note holds what is still open about an arrangement (the venue nobody has picked, who pays,
who has not answered), or an arrangement with no gathering in it at all, like money owed. One
plan often has both: the event holds when and where, and the note holds the corners still
being decided.

## Already written down

Before you write, look for it among what is already written down: the same arrangement can sit
under another title, in other words, or in another room's event. If it is there, you change
that one or nothing. A second note or a second event for one arrangement is the worst
duplicate there is, because the group then has two answers to one question.

Revise when the messages change what an entry says: a corner settled, the date or the place
moved, an option dropped, someone answered, the plan called off. The room's newer words
outrank what the entry says, whoever wrote it. Read the note first and give it back whole:
every line it had keeps the ids it cites, reworded or moved to where it now belongs (a settled
corner from Open to Decided, a dropped option to Ruled out with the reason given), and the new
messages add theirs. A plan called off goes to Ruled out with the call-off as its reason, and
its event moves to cancelled.

Do not revise when nothing changed. A message that repeats, agrees with or jokes about what an
entry already says is `no_change`, and a revision that only rewords is noise in a history the
group reads.

## A line

A line carries the arrangement, not the conversation: «hike on Saturday, meeting at the
station», never «Ben asked about Saturday and Cat said yes». Messages that settle one thing
are one line citing all of them. Write it concretely, with names, places, times and prices as
they were said; a day said as «this Saturday» is written as the date, worked out from the
moment the message was sent. A reason nobody gave is `no reason given`. A line you cannot
point to a message for is not written.

A body in the notebook's form, for a hike being planned:

```
## Decided
- Hike on 2026-11-07 Sat, meeting 09:00 at the station exit (Ben, Cat) [5201, 5204]
## Open
- Which trail: Dragon's Back or Lion Rock [5202, 5205]
## Owed
- Cat -> book the minibus back -> 2026-11-05 Thu [5206]
## Proposed
- Dragon's Back (Ada) [5202]
- Lion Rock (Cat) [5205]
## Ruled out
- 大帽山: 太遠 (Ben) [5203]
```

The same plan is also an event: the hike, its date and its meeting time, told. When the room
picks the trail, the Open line moves to Decided with its ids and the new message's id, the
trail left behind goes to Ruled out, and the event is not touched unless its time or place
changed.

## How you know it

Every event write says how you reached it, and you pick the plainest mode that is true:

- **told**: a person said it, to you or to each other; members agreeing among themselves
  counts, and so does a day they named that you turned into a date.
- **deduced**: it follows from what you hold without anyone saying it: the event is at a place
  at a time, and a fresh fact about where people were puts them there then.
- **abduced**: your best explanation of what nobody said: someone asks the way to a place
  nobody named as the venue; whereabouts that are not fresh.

How sure you are is a separate answer from how you know it. An abduced value goes to the room
as a question, and a «yes» to your own guess leaves it abduced. A fact about where people were
can move an event, never put words in a line: it says who was where, not who said what.

A note line carries no mode because it is only ever what was said. What you worked out goes on
an event as deduced or abduced, or nowhere: never into a note as if someone had said it.
