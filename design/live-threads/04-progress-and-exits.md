---
type: proposal
title: "Last Moved, And The Two Exits"
description: "She is asked whether a thing is progressing and given no measure of progress. And her only act is to schedule an interruption, though both turns already read the same note store. One renderer change and one field close both."
tags: [nora, notes, proactive, participation, rendering]
timestamp: '2026-09-11'
version: 1
---

# Last Moved, And The Two Exits

## 1. The two gaps

Both are the same class as the Relevance Gate's missing timestamps
(`~/.claude/CMS.md`): a judgement whose input cannot ground it does not fail
loudly — the model answers anyway, wrong, at a rate nothing reports.

## 2. Nothing says when a thing last moved

**Verified in the tree.** `Note` carries `created_at` and no last-moved.
`NoteRevision` carries `written_at`, and the proactive adaptor reads the head
revision in `_changed_since` — purely to decide whether to run at all. Neither
renderer puts it in front of the model:

- `nora/language/proactive.py::_note_line` — title, description,
  `anchor_date`, `revisit_at`, `strikes`.
- `nora/language/note_render.py` (the tag and participation path) — thinner
  still: `- {title} — {description} (about {date})` and nothing else. No
  revisit, no strikes, no movement.

The tag and participation turns therefore see **less** about a note than the
proactive pass does, while being the turns that could act on an opening
(`§4`).

So the pass is asked "is this progressing?" and shown when the thing *happens*,
never when it last *changed*. `01 §4.1` and `01 §4.2` are both entirely this:
a thread that stopped is indistinguishable, in the prompt, from a thread that
started yesterday.

**Proposed.** Both renderers carry, per note, **how long since it last moved,
and what moved** — where "moved" is a tag-path revision, the distinction
`is_tag_path_revision` already draws, because a background write by the curator
or the debrief is not the room doing something.

```
[1] «Ben's flat for the 25th» — whether the landlord allows the party;
    about Fri 25 Sep; nothing new for 9 d
```

Three notes on the wording, each with a reason:

- **"nothing new for 9 d", not "last revised 9 d ago."** The judgement is about
  the room's behaviour, not about the store's. A revision is the mechanism; a
  stall is the fact.
- **The phrase must be present when a note is fresh too** ("moved today"),
  because a field that appears only in the bad case teaches the model that its
  presence *is* the signal, and it will then act on presence rather than on
  duration.
- **One vocabulary.** `nora/domain/prompt.py::age` is canonical since
  2026-09-10. A third phrasing still exists at `proactive.py:_age` ("2 h" vs
  "2h ago") and should be folded when this lands, not before — it is inside a
  graded prompt.

**This is the smallest change in the bundle and the one every scenario needs.**
It is a renderer change: no migration, no writer change, inert until a note is
actually stale. It can land before DEC-163 — but see `06 §4` on judging it.

### 2.1 Why not derive it in the model

Because it cannot be. The pass does not see revisions; it sees an index line.
`note_read` returns a body, not a history. There is no tool call that answers
"when did this last change", so no instruction can obtain it.

## 3. Nothing says when a state stops being true

`03 §1` adds `holds_until`. It is rendered on the same line, and it does two
jobs: it stops her raising a healed ankle, and it gives a state-shaped note a
retirement rule that is not the strike counter.

The strike counter is wrong for a state. `MAX_DATELESS_STRIKES = 4` at a 7-day
horizon retires an untouched dateless note in about a month — reasonable for an
unanswered question, wrong for *what are we playing next*, which is a standing
interest that recurs forever and should get **better** each cycle rather than
being recreated from the transcript each time (`01 §4.2`).

## 4. Her only act is to schedule an interruption

**Verified.** The decide turn's act verb is `remind(when, text, note)` and
there is no other. So anything she notices becomes a scheduled interruption or
is lost.

`01 §4.4` is the case that breaks: *is your ankle still bad?* dropped into an
ordinary exchange is care; the same sentence arriving at 18:00 because a row
came due is unsettling. The content is identical and the exit decides whether
it lands.

**And the second exit already exists.** The tag and participation turns render
`<notes>` from the same store (`prefill._notes_block`). She is *already*
holding the note index every time the room talks. What is missing is a way for
the decide turn to say "not worth choosing a moment for — say it if it comes
up".

**Proposed: a field on the note, not a channel between tiers.**

| value | meaning | who reads it |
|---|---|---|
| `choose_a_moment` | this needs a time picked; being late loses it | decide turn → `remind` |
| `when_it_comes_up` | worth saying, not worth interrupting for | participation turn, from the index |

The test is one question — **can it wait?** — and it is a judgement, not a menu:

| | can it wait? | exit |
|---|---|---|
| book before Mid-Autumn | no; after it, the option is gone | choose a moment |
| ask after his ankle | yes, and the more natural the better | when it comes up |
| a game they might like | yes — but not past the point where they settle for anything | when it comes up, then a moment |

That last row is the interesting one: the right behaviour is to wait for an
opening *and* to stop waiting once waiting starts costing. That is expressible
as `when_it_comes_up` plus a `holds_until` — at which point, if nothing has
picked it up, the decide turn may choose a moment. No third value needed.

### 4.1 What this does not do

It does not let the proactive pass write into the participation turn. It writes
a note field; the participation turn reads notes as it already does and decides
for itself, under `silence.md`, whether the moment is right. **The note is the
channel.** Nothing new is wired between tiers, and the two turns still cannot
address each other — which is the property that keeps `silence.md` in charge of
whether anything is said at all.

### 4.2 What has to be true for it to work

The participation turn must actually be able to act on it, which means the
Relevance Gate must let the turn happen. The gate decides on the room, not on
her notes (`participation.md`, a file she never reads). **A room that never
trips the gate never provides an opening**, and a `when_it_comes_up` note there
would wait forever — which is what `holds_until` and the fallback in §4 are
for, and which needs measuring rather than assuming (`06 §4`).
