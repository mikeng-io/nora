---
type: design
title: "Time Is a Property of a Note, Not Its Axis — the Note Tier's Dated and Dateless Lanes"
description: "The note tier was built around a date: the create path demands an anchor_date, the curator selects on passed anchors, the index renders a countdown. The operator's reframe is that the architecture had it backwards — a note is a thing the room must not drop, and whether it has a moment attached is a PROPERTY of that thing, not the axis the tier is organised on. The living thread («what game next») has no date and must still be a note. This node records the reframe and maps the cascade the operator asked for: schema, the Dreamer's per-note timer, index render, curator selection, proactive, and the three issues it absorbs. Time becomes optional; it does not become ignorable — silence is still the one signal a conversation cannot carry."
tags: [nora, note-tier, dreamer, curator, candidates, dateless, anchor, revisit, strikes, timer, schema, proactive, duplicates, note-merge]
timestamp: '2026-09-16'
updated: '2026-09-25'
edges:
  - {dst: 37-note-candidates-v2, rel: extends, provenance: asserted}
  - {dst: 34-note-dreamer-consolidation, rel: revises, provenance: asserted}
  - {dst: 33-note-dreamer-schedule, rel: extends, provenance: asserted}
  - {dst: 27-note-dreamer, rel: revises, provenance: asserted}
  - {dst: 40-proactive-origin-delivery, rel: relates_to, provenance: asserted}
  - {dst: 26-standing, rel: revises, provenance: asserted}
version: 2
---

# Time Is a Property of a Note, Not Its Axis

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


*§7 (2026-09-24, DEC-202) adds the duplicate bound this node declined in its first version: the Standing debrief stops creating notes, creation refuses exact duplicates, and a new `note-merge` adaptor folds what gets through. It reverses §3.4's «no merge verb», §6's «nothing here revives it», R-75's merge clauses and 27 §2's «never on topical similarity»; each is marked where it stands.*

*DEC-194 — renumbered 2026-09-22. Drafted as DEC-190 at PR opening 2026-09-16; DEC-190 was taken by the presence place-identity decision (#336, merged 2026-09-17) while this PR sat open, and master now runs to DEC-193. Below its decision-index row.*

## 0. What is true on 2026-09-16 (read back, not recalled)

Six facts, each read out of the tree on the day this was written. They are the
floor the rest of the node stands on, and every one of them is a line you can
open.

- **A dateless note cannot be STAGED.** `note_candidate_create_fields_check`
  (`nora/infra/sql/schema.py:1960-1964`, migration `0049`) requires
  `anchor_date is not null` for every `kind='create'` row. The parser refuses
  before that: `nora/language/note_candidates.py:522-528` rejects a candidate
  whose `anchor_date` is absent, non-`int`, or below `_MIN_ANCHOR_DATE`. The
  create lane is date-gated at two layers, and the constraint is the harder of
  the two.
- **A dateless note CAN exist.** `note.anchor_date` is already nullable
  (`schema.py:1782`) and `note_has_an_anchor_check` (`schema.py:1816-1819`)
  asks only for `anchor_date is not null or cardinality(anchor_entity_ids) >
  0` — a topic anchor satisfies it. The note TABLE has been ready for this
  since `0026`; only the staging lane in front of it was not. Today the only
  door into that state is the tag path, which 33's cold start measured at one
  call in the tier's life.
- **The per-note timer is dateless-ready in its arithmetic and half-built in
  its policy.** `default_revisit_at` (`nora/domain/notes.py:182-217`) takes
  `anchor_date: int | None` and already branches: an anchor gives day-start
  minus `REVISIT_LEAD_DAYS`; `None` (or an unreadable anchor) gives `now_ms +
  DATELESS_REVISIT_HORIZON_DAYS`, 7 days (`notes.py:118`). `record_wake` has
  had a production caller since the curator merge
  (`nora/language/note_curator.py:1253-1294`): every still-standing note the
  tick selected gets a wake, and the store
  (`nora/infra/sql/notes.py:541-573`) decides progress (a tag-path revision
  since the last wake → reset strikes, advance `revisit_at`) or strike
  (`revisit_at` untouched, so the note stays due). At the ceiling —
  `anchor_date is None and outcome.strikes > MAX_DATELESS_STRIKES`, 4
  (`notes.py:134`) — the curator **logs and counts**
  (`nora_note_curator_total{action="dateless_stale"}`,
  `note_curator.py:1282-1293`) and does nothing else. Its own comment says
  why: *"ELIGIBLE, not retired … this merge counts the crossing rather than
  building the discharge decision"*.
- **DEC-189 / #235 (merged 2026-09-16, PR #264) put the title in the staging
  key.** `IDX_NOTE_CANDIDATE_CREATE_ANCHOR_KEY` is now `(unit,
  anchor_date, anchor_entity_ids, lower(btrim(title)))`
  (`schema.py:2026-2032`, migration `0054`), because in a fixed-membership
  room the entity list is a constant and the key had degenerated to
  `(unit, anchor_date)`. That is the precondition this node needed — but
  it is **not sufficient on its own**, and §3.1 says why.
- **The create gate does not key on the date at all** (post-#228). The
  candidate carries the TITLE of the standing note it updates, exactly as the
  index rendered it; `_resolve_updates` resolves that title — one match
  revises, none creates, more than one creates rather than guessing
  (`note_candidates.py:173-196`). The date is *when the thing happens*, the
  `revisit_at` seed and half the staging key, and nothing else.
- **Proactive already routes by provenance, not by clock.** 40 sends a
  note-linked proactive row into the note's `established_in` room when that
  room is proactive-trusted, and refuses fail-closed otherwise. Nothing in
  that path reads `anchor_date`.

## 1. The operator's reframe (2026-09-16, verbatim)

> 「而家問題係成個 note tier 嘅 architecture 嘅一個更改。成個 architecture 關於
> notes 就係：佢會 based on time-sensitive，但係我哋要做到嘅嘢，就係啱先嗰個
> game 嘅例子，就係佢唔可以係 time-sensitive，而 time-sensitive 係要 depends
> on 㗎。」

And the example it rests on, which has been the same example since 34 §5.4:

> 「我同啲 friend 想玩 game，但一直話唔知玩咩 game 好，陸陸續續話玩 Game A，
> 之後玩咗 Game B，然後又唔知玩咩 game。呢個完全冇日期定喺入面，但佢又要
> note。」

Read against §0 the complaint is exact, and it is architectural rather than a
missing feature. The tier does not merely *lack* support for undated notes —
it is **organised on the date**. The create path asks for one, the staging key
leads with one, the curator's primary selection is «anchors that have passed»,
the index line is a countdown to one. Every one of those is a correct design
for an arrangement and a category error for a thread. «What game next» is not
an arrangement with a missing field; it is a note whose job never had a
moment attached, and the tier has no shape for it.

**The reframe: a note is a thing the room must not drop. Time is one PROPERTY
such a thing may carry — the day it happens — and the tier reads that property
when it is there.** It is not the axis the tier is built on. 33 C1 already
moved the tier once in this direction (a note is a notebook entry, not an
uncertainty); this is the same move applied to the other assumption 27 baked
in on day one, where `(anchor, uncertainty, closing condition, body)` made the
anchor structural.

## 2. The design: two lanes, one notebook

There is one note type and one table. What differs is which signals can act on
a note, and that follows from whether it carries a date — which is why this is
a property and not a subtype.

| | **dated lane** (anchor present) | **dateless lane** (topic anchor only) |
|---|---|---|
| identity at staging | `(unit, anchor_date, entity ids, title)` | `(unit, title)` + a topic anchor — §3.1 |
| create-vs-update | title resolution (#228), unchanged | title resolution, and a **judgement** where the title does not resolve (37 §3) |
| first wake | anchor day start − 3 days | `now + 7 days` |
| clock-driven retirement | `anchored_before` → `expired`/`moot`, `_covers`-grounded | **strikes ceiling** → a retirement ACTION that does not exist yet (§4) |
| conversation-driven close | 37's close verb (not built) | 37's close verb — the *primary* close for this lane |
| index line | `— description (about Fri 25 Sep, in 14 days)` | `— description`, and nothing about how long it has been open (§3.3) |

**What each lane owns.** The dated lane owns the countdown: a moment arrives,
passes, and the curator asks whether it settled. The dateless lane owns
*attention*: it has no moment to pass, so the only clock signal available to
it is how many times it woke with nothing new said — the strike ceiling — and
its natural close is the conversation saying so.

**Time becomes optional; it does not become ignorable.** 37 §2 held this and
this node keeps it verbatim. A thread nobody mentions again is a thread
`note-candidates` never reads again — it fires on new messages after a
watermark — so the *absence* of conversation is a signal only the clock
carries. Removing the date from the create path does not remove the clock from
the tier; it moves the dateless lane's clock from «when is it» to «how long
has nobody said anything», which is exactly what `record_wake` already
computes and nothing yet consumes.

## 3. The cascade map

The operator's second question, and the one this section exists for:

> 「我想知會唔會 cascade 到其他情況出現？我哋除咗 database schema 之外，最重要
> 就係個 note tier 仲有個 note timer（dreamer）。我想知 note tier 同 note
> timer 係咪有銜接到佢啲變化？」

Six surfaces. Two of them are already connected, three need work, and one is a
defect this node found while checking.

### 3.1 Database schema

- **`note` needs nothing.** `anchor_date` is nullable (`schema.py:1782`) and
  `note_has_an_anchor_check` (`schema.py:1816-1819`) already accepts a
  note anchored by topic alone. **But the check is a floor, not a
  formality**: a dateless candidate that also carries an empty
  `anchor_entity_ids` cannot become a note. So the dateless lane's create path
  must supply a topic anchor (37 §3's position: the existing free-text
  `anchor_entity_ids` column is the topic string), or this CHECK becomes the
  second thing to relax. **This node takes the first**: a note with neither a
  date nor a topic is not a note, it is a sentence.
- **`note_candidate` needs one relaxation.**
  `note_candidate_create_fields_check` (`schema.py:1960-1964`) drops
  `anchor_date is not null` from its conjunction; a migration after `0054`
  carries it, plus the mirrored change in `nora/testing/note_store.py` so the
  contract suite covers both implementations. The parser guard
  (`note_candidates.py:522-528`) relaxes with it — absent becomes legal,
  malformed stays refused, and those two must not collapse into each other.
- **The staging index is the defect, and it is the opposite of what was
  assumed.** The pre-condition claim behind this node — «title is in the key
  now, so `anchor_date` can go NULL without collapsing staging» — is only half
  true. `IDX_NOTE_CANDIDATE_CREATE_ANCHOR_KEY` (`schema.py:2026-2032`) is a
  plain `unique=True` index, and Postgres' default for a unique index is
  **NULLS DISTINCT**: two rows with `anchor_date IS NULL` and byte-identical
  titles do **not** collide, so the staging dedupe silently stops deduping for
  exactly the lane this node is adding. The key does not collapse — it
  *disappears*, which is worse, because a collapse is visible as a wrong merge
  and this is visible as nothing. Two fixes, both available on the deployed
  `pgvector/pgvector:pg18`: declare the index `NULLS NOT DISTINCT` (PG15+), or
  index `coalesce(anchor_date, 0)`. This node does not pick between them —
  that is an implementation reading — but it does state that **the schema step
  is not "relax the CHECK"; it is "relax the CHECK and make the key survive a
  NULL"**, and a build that does only the first ships a silent duplicate
  factory. `alembic check` is blind to both the partial predicate and the
  expression column, so `tests/infra/test_schema_parity.py` is the only gate
  that will see it.

### 3.2 The note timer (the Dreamer's per-note clock) — the 銜接 the operator asked about

**It connects. The arithmetic is already there; the policy at the end of it is
not.** Concretely:

*Already works, and needs no change:*

- `default_revisit_at(anchor_date=None, …)` returns `now_ms + 7 days`
  (`notes.py:213-217`). A dateless note gets a real first wake from the same
  function the dated lane uses — the docstring's own point, *"so a
  gate-created note's first wake is a normal scheduled wake rather than a
  strike bootstrap"*.
- `record_wake`'s caller loops over `shown.due_or_anchored`
  (`note_curator.py:1253-1294`) with no anchor requirement; it passes
  `shown.anchor_date.get(note_id)`, which is `None` for a dateless note and
  flows straight into the `None` branch above.
- The strike loop is closed: no tag-path progress → `revisit_at` untouched →
  `due_for_revisit` re-selects it next tick → another strike. A dateless note
  therefore wakes every 7 days until something moves, which is the cadence
  `DATELESS_REVISIT_HORIZON_DAYS` was written for.
- The crossing is counted:
  `nora_note_curator_total{action="dateless_stale"}`
  (`note_curator.py:1293`).

*Does not work, and is the whole of what must be built:*

- **The action at the ceiling.** `WakeOutcome`'s docstring
  (`notes.py:694-711`) is explicit that the store reports and the caller
  decides, and the caller currently decides nothing. So today a dateless note
  is **immortal**: it has no anchor for `anchored_before` to select, its only
  other selection is `due_for_revisit`, and the one outcome that could retire
  it is a log line. 37 §4 specified the missing consumer — a note past the
  ceiling goes to the model with its evidence window under the existing `moot`
  rule, retiring only if the window shows the room moved on — and that spec is
  what this node schedules for build.
- **The word «eligibility» has to resolve.** Three places
  (`notes.py:126-131`, `WakeOutcome`'s docstring, the curator's log line) say
  "eligible for dateless retirement" and point at a decision nobody has made.
  Until a consumer exists, "eligible" is a synonym for "ignored", and the
  metric that counts the crossing is measuring an inaction.
- **The docstring at `notes.py:129` names a metric label that does not
  exist** — `nora_note_curator_total{action="stale"}`. The real labels are
  `retire_stale` (`note_curator.py:187`) and `dateless_stale`
  (`note_curator.py:194`). Trivial, and worth fixing in the build step,
  because it is the one place a reader would go to learn what the ceiling
  currently does.

**So the connection dateless-create needs is one edge, not a subsystem**: the
create path must write `revisit_at` at creation for a dateless note exactly as
it does for an anchored one. `due_for_revisit`'s `revisit_at IS NOT NULL` is
load-bearing and its docstring says so (`nora/infra/sql/notes.py:719-725`) — a
note nothing ever woke stays out of that read **forever**. For a dated note
that is harmless, because `anchored_before` catches it anyway. For a dateless
note it is total: a create path that forgets the seed produces a note no pass
will ever look at again. That is the single highest-consequence line in the
build.

### 3.3 Index render and proactive decide

`_render_entry` (`nora/language/note_render.py:175-202`) already handles
`anchor_date is None` — it returns `- {title} — {description}` and stops. So
the dateless lane renders today, and renders *incompletely*: the dated line
carries `(about Fri 25 Sep, in 14 days)` and the dateless line carries no
temporal information at all. The function's own docstring says the date is on
the line because it is *"the only answer to that hedge"* — the block's warning
that a listed note may already have settled. **The dateless line inherits the
hedge and loses the answer.**

The position: a dateless line should carry *how long this has been open and
when it last moved* — the note's age and its last revision — which is the
dateless lane's analogue of a countdown and is exactly the shape AGENTS.md's
own rule demands (*"if the judgement turns on when, the block carries a
time"*). The relevance gate paid for this lesson in September; the note index
is the same omission one tier over. Reading a render change is cheap and its
effect is measurable against the scenario suite, so it belongs in the build
step, not here.

**#159 (owed_at) belongs to this same render layer and is NOT in this node.**
Its shape — a commitment owed *before* the anchor, Ben owes an answer today
about a party on the 25th, so triage sees the wrong date — is a second time on
a note, not the absence of the first. It becomes *possible* here (once time is
a property, a note may carry more than one kind of time without the tier
warping), and it is a **follow-on**: shipping it inside a node whose point is
"a note may have no time" would make the first build carry two unrelated risks.

Proactive decide reads the index; a lane that renders without a time is a lane
the decide pass judges without a clock. That is the same defect as the render
gap and is fixed by fixing it.

### 3.4 Curator selection

- `anchored_before(day)` (`nora/infra/sql/notes.py:660`) selects notes whose
  anchor is behind the room's calendar day. **A dateless note is invisible to
  it by construction**, permanently. Unchanged by this node.
- `due_for_revisit` (`nora/infra/sql/notes.py:700-746`) selects standing notes
  with `revisit_at IS NOT NULL AND revisit_at <= now_ms`. Its docstring
  already names the dateless note as its own contribution: *"a dateless note
  (no anchor to pass) and an anchored note whose anchor has not yet arrived"*.
  **For the dateless lane this is the ONLY selection path**, which makes the
  §3.2 seed rule non-negotiable.
- `_covers` (`note_curator.py:1757`) refuses retirement without evidence the
  room was still talking past the moment. A dateless note has no moment, so
  `_covers` in its anchor form cannot ground a dateless retirement at all —
  which is the deeper reason the strike ceiling is the dateless lane's only
  retirement route, and the reason 37 §4 routes it through `moot` with an
  evidence window rather than retiring on the counter alone. **Silence still
  retires nothing; silence plus a model reading the room's later traffic may.**
- What stays: the union-and-deduplicate of the two reads, the per-tick caps,
  the retire-only posture. ~~No merge verb.~~ **Superseded 2026-09-24 (§7):
  the curator stays retire-only, and merging moves to a separate adaptor,
  `note-merge`, because the curator judges each note alone and never puts two
  side by side.**

### 3.5 Proactive and the close verb

Two ways a note stops, and the dateless lane inverts their weights:

- **Conversation-driven close** (37 §2, direction 1, not built). For a dated
  note this is an optimisation — the curator would get there at the anchor
  anyway, one pass later. For a dateless note it is the *primary* close: when
  the room says 「就玩 Game B 啦」 the thread is over, and there is no anchor
  behind it to catch the case where the close is missed. **The close verb
  stops being a latency improvement and becomes a correctness requirement for
  the lane this node adds.**
- **Clock-driven close**: §3.4's strike route, slow and grounded.

Remind/reminder (38/39) and proactive origin (40) are unaffected as
mechanisms. The relationship worth stating: **a note with no date has no
default moment to be raised at**, so nothing schedules a dateless note into a
conversation the way an anchor does. Proactive can still act on it — on
conversation signal, and routed by 40's `established_in` rule, which reads
provenance and never the clock. A dateless note is raised because the room
came back to the subject, not because a day arrived. That is the correct
behaviour for «what game next» and it needs no new mechanism, only the
absence of an assumption.

### 3.6 The issues that fold in

See §6.

## 4. What exists, and what must be built

| | state |
|---|---|
| `note` table accepts a dateless note | **exists** (`schema.py:1782`, `1816-1819`) |
| dateless timer arithmetic | **exists** (`notes.py:182-217`) |
| wake/strike loop with a production caller | **exists** (`note_curator.py:1253-1294`, `infra/sql/notes.py:541-573`) |
| dateless ceiling counted | **exists** (`note_curator.py:1293`) |
| title in the staging key (the precondition) | **exists** (#235/DEC-189, `schema.py:2026-2032`) |
| dateless line renders at all | **exists** (`note_render.py:198-199`) |
| staging key survives a NULL anchor | **MUST BUILD** — §3.1, and it is a silent failure if skipped |
| `note_candidate` CHECK + parser accept a dateless create | **MUST BUILD** — §3.1 |
| judgement gate for a dateless candidate | **MUST BUILD** — 37 §3 |
| `revisit_at` seeded at dateless creation | **MUST BUILD** — §3.2, highest consequence |
| retirement ACTION at the strike ceiling | **MUST BUILD** — §3.2 / 37 §4 |
| close verb in `note-candidates` | **MUST BUILD** — §3.5 |
| dateless index line carries a time | **MUST BUILD** — §3.3 |
| the debrief creates no notes | **DONE** 2026-09-24 (#451) — §7.1 |
| a revision cannot land on a retired note; a merged note forwards | **MUST BUILD** — §7.5/§7.6, step 1 |
| `ambiguous` stops creating; `note_create` refuses an open title | **MUST BUILD** — §7.2, step 2 |
| `merge_into`, the curator's head-checked retire, `note-merge` in shadow | **MUST BUILD** — §7.3–7.7, step 3 |
| duplicate eval set, gating `on` | **MUST BUILD** — §7.8, step 4 |
| unique open-title index | **MUST BUILD** — §7.2 (3), step 5 |

Build order, measurement and the scenario cases are a follow-on issue's, not
this node's. The one ordering claim this node does make: **the seed and the
key fix ship in the same change as the CHECK relaxation**, because each of the
three alone produces a note the tier cannot see or a duplicate nothing
reports.

## 5. Open positions, and what would decide them

| position | what would decide it |
|---|---|
| `owed_at` (#159) is a follow-on, not part of this node | a measured case where a dated note's triage is wrong *because* the tier now has two lanes — i.e. the lane split makes the single-time assumption worse rather than merely leaving it in place |
| the dateless retirement ACTION belongs here, not to #101's discharge pipeline | if the action turns out to need a discharge sink (an event, a conclusion) rather than a plain `supersede`, it is #101's after all. 37 §4 assumes `supersede` with a reason; the first dateless note to cross the ceiling in production settles it |
| the staging key fix is `NULLS NOT DISTINCT`, not `coalesce` | whichever `tests/infra/test_schema_parity.py` can hold against the migration chain without a hand-written exception |
| a note needs a date OR a topic anchor; neither is not a note | a real operator case of a thread with no date and no nameable topic. None has appeared in the corpus; the game example has a topic |
| 37's Stage B enrichment (step 4) stays behind steps 2–3 | 37 §7 already set this: build it only if the created bodies read as useless without the group's history, measured by reading them. This node does not move it, and the dateless lane is precisely where it would matter most — a living thread's value IS its history — so the temptation to pull it forward should be named and resisted until step 3 has produced a body to read |

Everything above inherits 37 §8's standing reversal condition on the lane
split itself: **if measurement shows threads almost never fall silent
unclosed, the curator's clock-driven half collapses into the conversation's
close and §2's table loses a column.**

## 6. Relationship to the open issues

- **#118 — partly absorbed; this node is its architecture half.** Its steps 2
  (close verb), 3 (dateless create) and 4 (Honcho enrichment) are the build
  behind §4, and step 1 (the positive control) is done and recorded in 37's
  checkpoint. What this node adds that #118 did not have: the schema cascade
  (§3.1, including the NULL-key defect), the timer connection (§3.2), and the
  render gap (§3.3). #118 stays the tracking issue; its scope is now read
  through this node.
- **#159 — NOT absorbed; a follow-on this node enables.** §3.3 states the
  reasoning: a second time on a note is a different change from the absence of
  the first, and it becomes clean to express only once time is a property.
  Filed as a follow-on, sequenced after the dateless lane ships.
- **#236 — absorbed (2026-09-24).** The duplicate bound it tracks is, for the
  dateless lane, the judgement gate (37 §3) at the front and the strikes
  retirement at the back. ~~What it is **not** is a merge verb — merge was
  deleted 2026-09-08 and nothing here revives it.~~ **Superseded by §7: both
  lanes' duplicates are folded by `note-merge`, a model-judged merge that
  needs no entity key.**
  The one thing this node *adds* to #236's surface is §3.1's NULL-key defect,
  which would create dateless duplicates faster than any gate retires them.

## 7. The duplicate bound: the debrief stops creating notes, creation refuses what it can, and `note-merge` folds what gets through (2026-09-24, DEC-202)

*Revised the same day after two independent reviews (a fact-check against the
tree and the live DB, and an adversarial design audit). Every claim below was
re-read at `origin/master` `6f83d504` and on norty's live DB.*

### 7.0 What was true on 2026-09-24 (read back)

- **The Standing debrief was the main note creator.** 52 of the 64 live notes in
  `friends`/`ridgeline`/`wedding` carried its stamp (a bare tag id as the first
  revision's `established_by`), 12 came from `note-candidates` and 0 from the tag
  path. A read of all 52: **5** genuine group questions, **25** about Nora's own
  turns (tool failures, her own unanswered replies), **21** duplicates, **1** a
  question nobody raised. It never saw the open notes and could only create: 47
  of the 52 have exactly one revision, and one 09-18 supper thread got 12 notes.
- **Its notes suppressed `note-candidates`.** The candidates objective tells the
  model not to re-propose, as if new, something already in the notebook it was
  shown (`note-candidates.md:42`; it may note that an entry needs an update). In
  3 of the genuine cases it judged the triggering message a committed plan with
  open details and held back only because a debrief note existed (Langfuse
  `918f8b96` msg 6543, `f3375235` msg 6778, `d326a1ec` msg 6784); in a fourth
  (msg 6542, the 龍哥 headcount) it judged the message not committed at all. The
  control is clean: in `d326a1ec` it skipped Saturday dinner («already in
  notebook») and created Friday lunch (msg 6791), which had no debrief note.
- **Nothing merges.** The curator's merge half went on 2026-09-08 (R-75: it keyed
  on `note_entity`, which nothing populates once the knowledge tier was
  deleted), and `note-candidates`' date-matching fallback went on 2026-09-13
  (#228). The curator's docstring: *"nothing merges a duplicate anywhere in this
  tier any more"* (`note_curator.py:27-28`). What stood in for merging was
  «duplicates are not created», and the debrief broke it.
- **The curator never compares notes with each other.** `NoteCuratorAdaptor.inputs`
  (`note_curator.py:822`) reads `anchored_before` and `due_for_revisit` and
  judges each selected note against its own room's evidence. A gate-created note
  is seeded with `revisit_at` = its anchor day minus the lead days
  (`domain/notes.py:215-218`), so the curator does see it before the day; a
  tag-created or debrief-created note has `revisit_at` NULL and is first
  selected after the day. Either way, nothing in its input puts two notes side
  by side.
- **A note's title is NOT unique, whatever the comments say.** `schema.py:2082`
  and DEC-189's text state that `note`'s uniqueness is the title per unit. No
  such index exists in `schema.py`, in any migration (0026 creates only the two
  partial btrees), or live. Two open notes titled 「今晚宵夜地點時間未定」
  (`c0151d45`, `9496d5e7`) co-existed from 09-18 16:34 to 09-24 11:07. An
  exact-title duplicate is an amplifier on both write paths: a candidate naming
  that title matches two notes and the gate CREATES a third (`ambiguous`,
  `note_candidates.py:1194`, `:1207-1210`), and the tag path's `note_read` /
  `note_revise` refuse it as ambiguous (`tools.py:8621-8643`).
- **A revision can land on a retired note.** `_append_on_conn`'s compare-and-swap
  (`nora/infra/sql/notes.py:420-428`) checks `id`, `unit` and `head_revision_id`
  only; `supersede` sets `retired_at` without touching the head
  (`notes.py:1035-1043`); `propose` funnels through the same append (`:933`);
  the in-memory double has the same hole (`nora/testing/note_store.py:239-252`).
  And `read` returns retired notes (`notes.py:340`), so `note_candidates._update`'s
  `current is None` guard (`note_candidates.py:1312-1321`) cannot catch a
  retirement, though its comment says it does.
- **A lost update is not retried.** `note_candidates._update` catches
  `StaleNoteHeadError` and leaves the staging row pending
  (`note_candidates.py:1366-1376`); nothing re-selects pending rows, and the
  adaptor is watermarked (`:894`), so its next pass reads only newer messages.
- **The curator's retirement is check-then-act.** It re-reads the head
  (`note_curator.py:1186`), then `supersede` runs in a separate transaction with
  no head check.
- **A pending proactive reminder dies with its note.** The fire-time screen
  cancels a row whose note is retired (`nora/app/proactive_screen.py:147`,
  `cancelled_note_retired`), and the once-a-day wake limit is per note
  (`proactive.py:1429`).
- **Adaptors run concurrently, and `note-candidates` runs per room.** The lock is
  `f"{adaptor.name}:{key}"` (`nora/app/runtime.py:1087`), and `note-candidates`'
  key is a room (`key_kind="scope"`), so two rooms of one unit can run it at
  once alongside the curator and any unit-keyed adaptor.

### 7.1 The debrief creates no notes (done, #451)

Design 26 §1 put the note tier's proposals on the debrief only because it was
the one after-the-reply path; 26 §7 is marked reversed. The debrief writes the
room's position and nothing else. The 49 notes it left open were triaged the
same day: 42 retired (21 `retired-closed` with `replaced_by` the note they
duplicate, 21 `retired-expired`, every anchor 0914–0923), 5 genuine kept, 2
held (`2358d164`, `ec18f8d1`) for `retired-withdrawn` (§7.6). Backup:
`~/backups/note-open-before-retire-20260924.csv` on norty. The 21 duplicates
are relabelled `retired-merged` once that value exists, so «how many closed»
stops counting them.

### 7.2 Prevent first: three cheap refusals at creation

The design audit's point, and the order it implies: most remaining duplicates
can be refused where they are made, and a merge pass is the back-stop for what
cannot. Built first, and measured before `note-merge` is enabled (§7.8):

1. **`ambiguous` stops creating.** A candidate whose `updates` title matches
   more than one open note is left pending and counted `ambiguous`, not written
   as a new note. Creating a third note is the amplifier in §7.0; a pending row
   loses no text.
2. **`note_create` refuses an open title.** The tag path's create refuses a
   normalised title (`note_handle`) that an open note in the unit already has,
   and tells the model to update that note instead.
3. **The unique partial index follows.** `(unit, lower(btrim(title))) WHERE
   retired_at IS NULL`, once (1) and (2) are live and the open notebook holds no
   exact-title duplicate. `note-candidates`' create path must then treat the
   violation as `ambiguous` (pending), never let it abort `propose`'s batch.
   This makes `schema.py:2082` and DEC-189's text true instead of rewriting them.

### 7.3 `note-merge`: one job, and why it is not the curator

A new Dreamer adaptor, `note-merge`, keyed on the unit. **Its one job: two open
notes asking the same open question become one.**

| | `note-curation` | `note-merge` |
|---|---|---|
| judges | each note, alone, against its room | notes against each other |
| selection | past its day, or due for revisit | notes created or revised since the last pass, against every other open note |
| when | the per-note clock | when the notebook changed (§7.4) |
| evidence | the room's messages since the note | the notes themselves |
| action | retire (`closed`/`expired`) | fold one note into another, then retire it (`merged`) |

Folding merge into the curator would put a second, unrelated question into its
prompt and widen its input to every open note. 34 folded the revisitor into the
curator because they did «substantially the same work» on the same selection
(34:25); merge does neither.

**Tools-less.** What it judges is known before the call — «knowable before the
call is an input» (`08-state.md:4`) — so the notes are rendered in and the model
gets no tools. **One sample**: the runtime UNIONS samples
(`nora/agent/dreamer.py:1555-1560`), so with `samples > 1` a group any one
sample proposed would be applied, which fails toward merging. `note-merge` pins
`samples = 1`.

### 7.4 When it runs, and what it is shown

- **State**: a `DreamScheduleClock` row `note-merge:pass` per unit, which survives
  restarts. It is stamped with the **render instant** (the time `inputs` read the
  notebook), as the last write in `commit`, and never on an `unavailable` pass.
  Stamping with the commit time instead — as the proactive pass does
  (`proactive.py:1305`, the runtime takes it after the model call,
  `dreamer.py:1615`) — would never see a note created during the model call.
- **Change predicate**: an open note created after the stamp, or any revision
  written after it whose `established_by` is not `dream:note-merge:*`. It reads
  revisions, not only each note's head, because a later revision would otherwise
  hide an earlier one — the gap in proactive's `_changed_since`, which also
  counts only tag-path revisions (`proactive.py:904-909`) and so would miss
  `note-candidates`' writes.
- **Input**: the changed notes, each compared against every other open note in
  the unit. Every note is shown by a per-pass ordinal (never an id, R-75's
  envelope rule) with its title, description, uncertainty, closing condition,
  anchor and the room it was born in; bodies are excerpts under a stated cap.
  Prompt tokens are recorded per pass. Nothing changed renders nothing, so the
  runtime makes no call.

### 7.5 Division of work across the tier

The three adaptors never call each other; the notes store is the only shared
state.

| writer | may create | may append a revision | may retire |
|---|---|---|---|
| tag path (`note_create`/`note_revise`) | yes, not over an open title (§7.2) | yes (read-before-write, R-55) | no |
| `note-candidates` | yes, not on `ambiguous` (§7.2) | yes (the title the model names) | no |
| `note-merge` | **no** | yes, to the survivor only, inside a merge | yes, `retired-merged`, with `replaced_by` |
| `note-curation` | no | no | yes, `retired-closed`/`retired-expired` |

`note-candidates` and the tag path are the **front gate**; `note-merge` is the
**back-stop** for what gets through. **Only `note-merge` merges.**

**A merged note forwards to its survivor.** Every lookup that resolves a note by
title or id and lands on a note retired `retired-merged` follows `replaced_by` to
the open survivor: `note-candidates`' `updates` resolution (an update aimed at
the duplicate is counted `updated` on the survivor, not `unresolved` → create),
the tag path's `_resolve` (so `note_read`/`note_revise` on a title still in the
turn's `<notes>` reach the survivor), `remind`, the fire-time proactive screen
(a pending row on the duplicate is re-pointed, not cancelled), and the per-note
wake limit. This is what makes a merge safe against a pass that rendered its
list before the merge — without it, `note-candidates` resolves the duplicate's
title to nothing and creates the note again (`note_candidates.py:1182`,
`:1211`).

### 7.6 The store: one new verb, three guards

- **Guard 1 — an append requires the note to be open.** `_append_on_conn` adds
  `retired_at IS NULL` to its compare-and-swap, the double mirrors it, and the
  contract suite covers both. A lost race raises `RetiredNoteError`, a subclass
  of `StaleNoteHeadError` with its own `reason`, so it is not counted as an
  ordinary stale head. `note_candidates._update` catches it and re-resolves the
  update through the survivor forwarding (§7.5) once, instead of leaving the row
  pending. Ships first; it closes §7.0's hole for every writer.
- **Guard 2 — `merge_into`, a store verb that is not on `DreamSink`.** `DreamSink`
  is two verbs by design (`ports.py:2127`), and its `propose`/`supersede` each
  own a transaction, so a merge cannot go through it. The precedent for a store
  verb outside the sink is `record_wake` (`notes.py:541`). In ONE transaction:
  append the merge revision to the survivor (CAS on its head and `retired_at IS
  NULL`) and retire the duplicate `retired-merged` with `replaced_by` (CAS on
  its head and `retired_at IS NULL`). Either failing rolls back both; counted
  `stale`, retried on the next change. Implemented in `SqlNoteStore` and the
  double, with a shared contract case.
- **Guard 3 — the curator's retirement takes the head it read.** `supersede`
  gains an optional expected head; the curator passes the head it re-read, so a
  merge landing between that read and the retire makes the retire a no-op
  instead of retiring a survivor that just absorbed a duplicate.
- **Vocabulary**: `retired-merged` and `retired-withdrawn` join
  `NoteSupersedeReason.ALL`. `retire_reason` is unconstrained text in the schema,
  so no migration is needed. `retired-withdrawn` («never an open question of the
  group») is for operator triage only; no pass writes it.

### 7.7 What a merge writes

- **Pairwise, one duplicate at a time.** A group the model names is folded one
  duplicate at a time into the survivor, each in its own `merge_into`
  transaction, stopping at the first failure. Overlapping groups in one pass
  ({1,2} and {2,3}) are all refused (`overlap`); the transitive closure is never
  taken.
- **Same room only, in v1.** A group whose notes were born in different rooms
  (`established_in`) is refused (`cross_room`): the survivor's room decides
  where proactive rows go (design 40) and which room the curator reads for
  evidence (`note_curator.py:753`), and merging across rooms would silently
  change both. Revisited once the `cross_room` rate is read.
- **Survivor**: the OLDEST note in the group (the title people and passes have
  already used). It keeps its id, title and `established_in`, so everything
  that points at it keeps working; what pointed at the duplicate forwards
  (§7.5).
- **Anchor**: the survivor takes the LATEST anchor in the group, and its
  `revisit_at` is recomputed with `default_revisit_at`. Otherwise a merged
  0923 booking-deadline note carrying the 0927 dinner question would be
  selected by `anchored_before` on 0924 and could be expired with the later
  question inside it.
- **The appended revision**: the survivor's body, then — under a marked line —
  the duplicate's title, uncertainty and closing condition, and only the part of
  its body the survivor does not already contain. A result over the revision
  ceiling is refused (`too_long`), never truncated.
- **Strikes**: a merged-in duplicate newer than the survivor is evidence the
  room is still talking, so the survivor's strikes reset; an older one leaves
  them.
- **What "reversible" means here**: the duplicate row and its whole revision
  chain are kept, so un-retiring it (clearing `retired_at`/`replaced_by`)
  restores it exactly. The survivor keeps the appended revision; undoing that is
  a new revision, not a rollback, and there is no automatic undo verb. This is
  the answer to 27 §2's objection that a wrong merge «has already lost which
  revision belonged to which occasion»: here nothing is lost, only duplicated
  onto the survivor.

### 7.8 The judgement, which way it fails, and the instrument

The model is asked which of the shown notes are **the same open question** —
the same thing the group has not settled, not merely the same people, day or
place — and answers groups of ordinals with a reason. **No deterministic key**:
#228 deleted date-matching because it merged two unrelated occasions both times
it fired, and the converse rule (refuse when anchors differ) would have kept 4
of the 5 Neway-booking duplicates (anchors 0923) apart from their survivor
(0927). Code refuses what it can decide: a note not open, a group of one, an
unknown ordinal, `overlap`, `cross_room`.

**It fails toward keeping notes apart**, by code and not only by prompt: one
sample (§7.3), overlap refused, cross-room refused, no transitive closure. A
missed merge costs a duplicate line, which is visible; a wrong merge folds two
questions into one note.

**Modes**: `[notes] merge_mode = "off" | "shadow" | "on"`, default `"off"`.
`shadow` runs the pass and records the groups it would fold (a decision-log row
each: survivor title, duplicate title, reason) without writing, for the
operator to label. `on` writes.

**The eval, before `shadow` becomes `on`**:
- cases are **full-unit snapshots**, not pairs, because production renders the
  whole unit;
- positives from this deployment's own duplicates (the triaged pairs, the open
  「Bath gathering」 pair) and from `shadow` rows the operator confirms — not
  only from the debrief, a producer that no longer exists;
- **at least 20 near-miss negatives**: the same title on different days, a
  dated and a dateless note on one topic, sub-questions of one evening (the
  27th's car vs its venue vs its headcount), the same people in different
  rooms, Friday lunch vs Saturday dinner;
- the grading rule for «same evening, related question» written down before
  labelling (the Neway booking folded into 「坐低食定掃街」 is a positive; the
  龍哥 headcount against the venue is a negative, and the rule must say why);
- labels signed off by the operator, not by the author;
- **k ≥ 5 draws per case**; the gate is **0 false merges across all negatives ×
  k** and a recall floor stated before the run. One run decides nothing.

**Production instrument**: `nora_note_merge_total{outcome=merged|stale|too_long|overlap|cross_room|unknown_ordinal|not_open}`,
`nora_note_merge_pass_total{reason=changed|unchanged|nothing_open}`, prompt
tokens per pass, the decision-log rows above, and a DB query over
`retire_reason = 'retired-merged'` as the durable count (a rare counter does not
survive Prometheus restarts). Old rows carrying the pre-2026-09-08 `merged`
value are included in that query.

**Named tests** (each with a mutation that turns it red):
`test_an_append_to_a_retired_note_raises_retired_note_error`,
`test_merge_into_rolls_back_when_either_head_moved`,
`test_the_curators_retire_is_a_noop_when_the_head_moved`,
`test_overlapping_groups_are_refused_whole`,
`test_a_cross_room_group_is_refused`,
`test_the_survivor_takes_the_latest_anchor`,
`test_an_update_aimed_at_a_merged_note_lands_on_its_survivor`,
`test_a_pending_proactive_row_on_a_merged_note_follows_its_survivor`,
`test_the_pass_clock_is_stamped_with_the_render_instant`,
`test_its_own_merge_revision_does_not_trigger_the_next_pass`,
`test_an_ambiguous_candidate_stays_pending`,
`test_note_create_refuses_an_open_title`.

### 7.9 What this does not change

- The curator stays retire-only, on its own selection and evidence. Its
  dropped retirements (`anchor_not_covered`: the evidence is the newest
  `_evidence_limit` messages, `note_curator.py:753`, and `_covers` needs the
  window to predate the note) are a separate defect — fixed 2026-09-25
  (DEC-203): past `ANCHOR_LAPSE_MS` the reach-back half no longer binds, so a
  busy room's past-anchor notes expire. If the curator retires one
  note of a would-be group before `note-merge` folds it, that note's body is not
  folded in — 27 §12.3's content-loss shape, accepted here because the retired
  note keeps its chain.
- `note-candidates`' hourly batches splitting a live exchange (a reply whose
  target is in an earlier batch renders as a timestamp,
  `note_candidates.py:770`) is a separate recall defect.
- 33 §6's M6 (no ordering between adaptors on one schedule) now has a
  cross-adaptor dependency to account for; it is carried by §7.5's forwarding
  and §7.6's guards, not by the schedule.

### 7.10 Build order

1. Guard 1 (the append) and the survivor forwarding in the three resolvers —
   they close holes that exist today, with or without `note-merge`.
2. §7.2's refusals (1) and (2), then count the duplicate rate after #451 from
   `nora_note_candidate_gate_total{action="ambiguous"|"unresolved"}` and the DB.
3. `merge_into`, guard 3, the vocabulary, and `note-merge` in `shadow`.
4. The eval (§7.8); `on` only when it passes.
5. §7.2 (3), the unique index, once the open notebook holds no exact-title
   duplicate.

Docs that change with the build: AGENTS.md (the 2026-09-08 deprecation
paragraph), GLOSSARY.md (the live adaptors and the note entry), ARCHITECTURE.md
(the note tier's writers), `config.example.toml` (the `[notes]` block, and the
`candidate_samples` comment that says duplicates «collapse in the staging key»,
false since DEC-189), the `note_curator.py` docstring, `note_candidates.py`
(«Nothing MERGES it afterwards»), `domain/notes.py`'s `NoteSupersedeReason`
comments, the `schema.py:2080-2083` comment, and R-75's and DEC-189's text.

### 7.11 Open positions

| position | what would decide it |
|---|---|
| `note-merge` is built at all, after §7.2 | the duplicate rate counted in build step 2. If the refusals leave almost nothing, `shadow` runs longer before `on`, or the adaptor waits |
| a separate adaptor, not a curator verb | a measured conflict between a merge and a retirement that §7.6's guards do not settle |
| no deterministic key; the model judges | the eval's and `shadow`'s false-merge count. A near-miss shape that merges gets a code refusal for that shape, never a general date rule (#228) |
| same room only | the `cross_room` refusal rate in `shadow`; a real cross-room duplicate the operator wants folded |
| survivor = the oldest | a merge whose survivor's title is clearly worse; then the model may nominate the survivor, and forwarding still covers every pointer |

## Status / Checkpoint

**2026-09-16 — design only. No code, no schema, no test, no policy.** Every
line-number citation above was read out of the tree on this date; the two
readings this node did NOT measure and states as reasoning are named in §3.1
(Postgres NULLS-DISTINCT semantics on the staging index, derived from the
index declaration and the documented default rather than from a probe) and
§3.3 (that a dateless index line reads worse without a time, argued from
AGENTS.md's measured relevance-gate case, not measured here). Both are cheap
to settle and both should be settled in the build step rather than assumed.

**2026-09-24 — §7 added, design only for everything after #451.** The debrief's
note creation is removed (#451) and the leftover notes triaged (§7.1). §7 was
revised the same day after a fact-check (3 claims wrong, 8 partly) and an
adversarial design audit (2 blockers: R-75/27 §2 not named as reversed, and a
merge undone by a pass that rendered before it). No code, schema or test for
§7.2–7.8 exists yet; §7.0 was read at `6f83d504` and on norty's live DB.


**2026-09-24 — build steps 1 and 2 shipped; step 3 HELD until a re-count on
2026-10-01.** Step 1 (#467): appends refuse a retired note, and a
`retired-merged` note forwards to its survivor; the 21 triaged duplicates were
relabelled `retired-merged` and forward live. Step 2 (#472): an ambiguous
`updates` stays pending, and `note_create` refuses an open title. The
step-2 measurement over the first ~8 hours after #451 (10:47–18:30 HKT): 2
notes created, both by `note-candidates`, 0 duplicates — too short a window to
size anything. Per §7.11, `note-merge` waits for the count over a week of
ordinary traffic: notes created since 2026-09-24 10:47 HKT by producer, open
pairs asking the same question, exact-title pairs, and staging rows left
pending as `ambiguous`. Duplicates getting through → step 3 in `shadow`;
almost none → the adaptor keeps waiting.

**2026-09-25 — build step 3 built, in `shadow`; the 2026-10-01 re-count
skipped by operator decision.** The operator chose on 2026-09-25 to build
`note-merge` now rather than wait for the week's count above; the count
still decides when `shadow` becomes `on`, together with the eval (step 4).
What landed: `merge_into` on both stores (one transaction, compare-and-swap
on both heads and `retired_at IS NULL`), guard 3 (`supersede` takes an
optional expected head and the curator passes the head it judged), the
`note-merge` adaptor (`nora/language/note_merge.py` (deleted 2026-10-03, DEC-277), objective
`nora/dreamer/objectives/note-merge.md` (deleted 2026-10-03, DEC-277)), `[notes] merge_mode = "off" |
"shadow" | "on"` defaulting to `"off"`, `nora_note_merge_total{outcome}`,
`nora_note_merge_pass_total{reason}`, `nora_note_merge_prompt_chars`, and a
fire-time screen that follows a `retired-merged` note's survivor. Where it
differs from §7.3-7.8, and why:

- **The «decision-log row» is a `note_merge_decision` table** (migration
  `0063`, renumbered from `0062` when #489 took that number), not the shared
  `decision_log`. That table's rows render into the
  participation and proactive prompts, its `reason` is an 80-character code
  label, and it refuses note titles and model text by its own rule; adding a
  writer to it would also have changed the `<loop_decisions>` gloss every
  ambient prompt carries. The new table holds both ids, both titles and the
  model's reason, and no prompt reads it.
- **Prompt TOKENS per pass are on the Langfuse trace**, not a metric: the
  adaptor never sees the call's usage, so it records the rendered prompt's
  size in characters.
- **More refusal outcomes than §7.8 lists**: `would_merge` (shadow),
  `not_changed` (a group none of whose notes changed since the last pass —
  re-asking a pair every pass would turn one sample into a union over
  passes), and the counted-only `singleton` and `malformed`.
- **Strikes reset when the duplicate was added to more recently than the
  survivor** (head revision times). With the survivor fixed as the OLDEST
  note, «a duplicate newer than the survivor» read by creation time would
  always hold.
- **The pass clock is kept per mode** (`note-merge:pass:shadow`,
  `note-merge:pass:on`), a review finding on #495. §7.4 names one row, and
  with one row the `not_changed` refusal made the flip to `on` inert for
  every pair `shadow` had already judged: none of their notes had changed
  since the shadow stamp, so each was refused for ever. A fresh clock on the
  flip makes the first `on` pass reconsider every pair once.
- **The fire-time screen follows the survivor rather than re-pointing the
  row**: a row on a merged note fires unless the survivor was revised on the
  tag path after the row was written. `remind` and the per-note wake limit do
  not forward yet; neither can meet a merged note until `on`.

**2026-09-25 — a prerequisite for `on` built and proven (PR #520): `merge_into`
resolves both notes' open proactive rows, not just one.** PR #517
(`0064_proactive_open_note`, merged `e8ded3c4`) added the ONE-open-row index
and the fire-time survivor rule, but keyed the index on the RAW note id a
row's `payload` carries — so two notes that EACH held an open row, folded
together, produced two rows that both forward to the survivor through
`successor()` and both fire, unreachable by the index because neither row's
own note id ever collided. Only reachable under `merge_mode = "on"`, never
`shadow`, which this repo still runs in production. Fixed in
`SqlNoteStore.merge_into` (`nora/infra/sql/notes.py`), inside the fold's own
transaction: the duplicate's open row is re-pointed to the survivor
(`note_id` AND `revision_id` — see below); if the survivor already held one,
exactly one of the two is kept (earliest `due_at`, a `firing` row never
given up for a `pending` one — the opposite of 0064's own boot-time
tie-break, and why, is in `_repoint_proactive_rows`'s docstring) and the
other cancelled `cancelled_duplicate_note`, 0064's own outcome for the
identical condition. Cancel runs before re-point when both are needed: the
index is not deferrable, so the two rows would collide mid-transaction in
the other order.

**A first review round found a BLOCKER, fixed the same day.** Re-pointing
only `note_id` left `revision_id` naming a revision of the retired
duplicate — not in the survivor's chain — so `ProactiveScreen.refusal`'s
`tag_path_progressed_since_wake` walk never found it, fell to the note's
root revision, and returned `True` (moved) the moment it met ANY tag-path
write, which is nearly every note's own creation: a forwarded reminder was
being CANCELLED, not delivered. Fixed by moving `revision_id` onto the
fold's own new revision too, which in turn required replicating
`ProactiveScreen._follow_survivor`'s "moved" check EAGERLY (against the
survivor's pre-fold chain, at the row's own `created_at`) — re-pointing
straight onto the fold's revision would otherwise hide a genuine tag-path
write that happened between the row being linked and the fold. Four more
findings from the same round: `InMemoryNoteStore.merge_into` now models the
repoint too (an optional `reminders: FakeReminderStore | None`, preferred
over leaving the gap SQL-only — and this is what caught, in seconds rather
than on norty, a stale-`survivor_head` bug in a test's own setup); a
concurrent `remind` insert landing between the transaction's `SELECT ...
FOR UPDATE` and its re-point `UPDATE` now translates the resulting
`IntegrityError` (23505 only) into `StaleNoteHeadError`, which
`note_merge.py`'s `_fold` already catches as `stale`; every write is
rowcount-checked, raising loudly rather than silently no-op'ing; and
`nora_note_merge_proactive_total{outcome}` (new counter, `nora/obs/
metric_specs.py`) plus a log line record what the step did, via an
`on_reminder_outcome` callback `SqlNoteStore` takes rather than a `Metrics`
dependency — the store still takes no config, clock or metrics itself. A
SECOND finding, from norty's real Postgres rather than a second review
(the first fix for "both rows firing" still re-pointed the firing keeper
onto the survivor while the un-cancelled firing loser held that same id
open, which collided with the unique index it was trying to respect): both
rows are now left alone, keeper included, when both collide firing.

**Superseded the same day by rounds 2–3; the round-1 proof below it is
history.** A second review found the eager check was a hand-copy that
diverged from the fire-time screen twice: it read only the survivor's
chain (a tag edit on the DUPLICATE after its row was linked let a stale row
fire), and it chose a keeper before classifying the duplicate's row (a live
survivor row could be cancelled for a stale one). Round 3 found the mirror
case: an unclassified STALE survivor row due first won the tie-break over a
live duplicate row, so nothing fired. Now one shared domain function,
`proactive_row_forwarded_is_moved` (`nora/domain/notes.py`), is what both
`ProactiveScreen._follow_survivor` and `merge_into` call, and `merge_into`
classifies both rows (the duplicate's by that function, the survivor's by
the ordinary walk) before any keeper is chosen. A `firing` row is never
classified or cancelled. The invariant is pinned by
`tests/unit/test_note_merge_reminder_parity.py`: for six scenarios,
`merge_into`'s outcome equals what the fire-time screen decides for the
same row left un-re-pointed.

Round-1 proof (history): 16/16 passed, 8/8 mutations RED, `make gate`
GATE_RC 0 with 10627 passed, on norty.

Related: #118 (the build), #159 (follow-on), #236 (duplicate bound), #101
(discharge, shipped, off), #103 (proactive adapter). The design lineage is
27 → 33 → 34 → 37 → this node; 36 is the review draft that first recorded
«date was never meant to be identity» (C3).
