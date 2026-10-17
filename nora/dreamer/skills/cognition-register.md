---
name: cognition-register
kind: skill
version: 5
applies_to: [cognition_session]
description: >-
  The note, event and reminder judgement the Cognition session writes with,
  injected after its instruction for every agenda kind it maps
  (`nora.app.cognition_wiring.AGENDA_SKILLS`; 30-cognition.md §4). Distilled
  from the retired passes' objectives: note-candidates.md (what belongs in the
  notebook, an update restates the whole entry and keeps every line's ids, the
  anchor date, the closing condition, an owner who wrote nothing), note-merge.md
  (the duplicate rule with its counterweight), note-curation.md (a changed plan is
  not a closed one; silence is no answer; retire on a message you can point to, or
  on a day passed with nothing open) and proactive.md (what earns a reminder, when,
  and that most looks raise nothing), plus 40-register.md §1, §3-§4 (note or
  event; told, deduced, abduced). What the validator does NOT enforce is worded as
  a request; what it does enforce is stated as one. The body below the heading is
  shown as one example, held to the real validator
  (tests/unit/test_cognition_agenda_skills.py). No numbers, no caps. Version 2
  (2026-10-03, the cut-over) adds retiring, merging and reminders; version 3
  (2026-10-09, DEC-306) the memo: what is settled and kept; version 4 (the
  review): an explicit keep request is written, and a title never holds the value.
change_policy: operator-only
---

## What belongs in the notebook

An entry is anything that has not finished its job: an arrangement with a decided half and
an undecided one, a commitment someone made, something someone owes, a plan that could still
change. A new note needs both: somebody has decided to do it, and some corner of it is still
unsettled. One person's plan counts when it has an open corner, even before anyone answers.

Not an entry: something settled with nothing left open; something with no way to tell it is
finished; a remark nobody picked up («we should go away sometime»); a thing asked, suggested
or wanted that nobody has taken on; a plan that hangs on an «if» or is called off in the same
breath. Most of what a group says about the future never becomes a plan, and an item holding
only that closes `no_change`: the ordinary answer, not a miss.

## Note, memo or event

An event is a dated thing people will attend: something to do, with a time on the table. A
note holds what is still open about an arrangement (the venue nobody has picked, who pays,
who has not answered), or an arrangement with no gathering in it at all, like money owed. One
plan often has both: the event holds when and where, and the note holds the corners still
being decided.

A memo keeps what the group has settled and will need again: a password, a cost's split, a
booking reference. It never closes: revise it when the room changes it, withdraw it only when
someone says it no longer holds. Most settled things need no memo, but an explicit ask to keep
one is written, not weighed, unless it is about the speaker alone (memory's) or still open. A
Kept line is what was said, as said; a title names the thing, never its value.

A note's `anchor_date` is the day the thing happens, never the day it was said: work it out
from the clock line, which gives today's date and weekday, and the message's `at`, which
gives the weekday and the day when it was not sent today. Two entries on one day are
told apart by the people in `anchor_entities`, so name them. A `closing_condition` is
concrete: a decision made, a date arriving, one person's answer; «it gets sorted out» is none.

## Already written down

Before you write, look for it among the notes and this room's events: the same arrangement can
sit under another title, or in other words. If it is there, you change that one or nothing.
But sharing the people, the day or the place is what separate arrangements usually have in
common, and it does not make them one. When you cannot tell, leave them apart: two entries
cost one extra line, and two questions folded into one cost the group a question it was still
deciding.

Revise when the messages change what an entry says: a corner settled, the date or the place
moved, an option dropped, someone answered. The room's newer words outrank what the entry
says, whoever wrote it. Read the note first and give it back whole, keeping the ids each line
cites when you reword it or move it to where it now belongs; the new messages add theirs.
When the whole plan is called off, its line goes to Ruled out with the call-off as the reason
and its event moves to cancelled. A body with nothing under Decided and nothing under Open is
refused, so leave a line there for what is still to settle, or, when nothing is, leave the
note as it is.

Do not revise when nothing changed: a message that repeats, agrees with or jokes about what an
entry says is `no_change`; a revision that only rewords is noise.

## A line

A line carries the arrangement, not the conversation: «hike on Saturday, meeting at the
station», never «Ben asked about Saturday and Cat said yes». Messages that settle one thing
are one line citing all of them. Write it concretely, with names, places, times and prices as
they were said, and a day said as «this Saturday» as the date. A reason nobody gave is
`no reason given`. A line you cannot point to a message for is not written. An Owed line is
for someone who took it on or was asked in the messages you cite; if the person who owes has
written nothing there, the thing owed is an Open line.

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

The same plan is also an event: the hike, its date and meeting time, told. When the room picks
the trail, the Open line moves to Decided with its ids and the new one's; the event is untouched.

## How you know it

Every event write says how you reached it, and you pick the plainest mode that is true:

- **told**: a person said it; members agreeing among themselves counts, and so does a day
  they named that you turned into a date.
- **deduced**: it follows from what you hold without anyone saying it: the event is at a place
  at a time, and a fresh fact about where people were puts them there then.
- **abduced**: your best explanation of what nobody said: someone asks the way to a place
  nobody named as the venue; whereabouts that are not fresh.

An abduced value goes to the room as a question, and a «yes» to your own guess leaves it abduced.

A note line carries no mode: it is only what was said. What you worked out goes on an event as
deduced or abduced, never into a note as if someone had said it.

## Retiring and merging

A note is done when what would close it has happened and a message says so: retire it as
`settled`, keeping the answer as a memo first when the group will need it again. A note whose day has
gone by is done only when nothing about it is still open: retire it as `anchor_passed`. A
plan that moved («we moved it to the 16th») is not a plan that closed: revise it. Silence is
not an answer either way; a note nobody has spoken to is doing its job. When you cannot
tell, leave it open: a note kept long costs a line, one retired early loses a thing still
being decided.

Merge two notes only when whatever settles one settles the other. The survivor's body takes
the duplicate's open lines with the ids they already cite. Sharing people, a day or a place is not enough, as above.

## What earns a reminder

A reminder is worth setting for a note or event whose moment is near, with a corner nobody
has closed, where a word at the right hour helps the people who have to act: where to meet,
two hours before dinner; a booking someone owes, the evening before its deadline. A thing three weeks
out has no right moment today.

Not worth it: anything settled, anything you already raised, or anything that would only
restate the note. Before you set one, read the note and
ask memory about the people it names, so the moment you choose fits how they
usually settle things. One reminder per thing, few a day, on most looks none: a friend who
speaks every day is muted.
