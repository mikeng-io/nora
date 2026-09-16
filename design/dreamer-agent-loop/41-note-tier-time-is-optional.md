---
type: design
title: "Time Is a Property of a Note, Not Its Axis — the Note Tier's Dated and Dateless Lanes"
description: "The note tier was built around a date: the create path demands an anchor_date, the curator selects on passed anchors, the index renders a countdown. The operator's reframe is that the architecture had it backwards — a note is a thing the room must not drop, and whether it has a moment attached is a PROPERTY of that thing, not the axis the tier is organised on. The living thread («what game next») has no date and must still be a note. This node records the reframe and maps the cascade the operator asked for: schema, the Dreamer's per-note timer, index render, curator selection, proactive, and the three issues it absorbs. Time becomes optional; it does not become ignorable — silence is still the one signal a conversation cannot carry."
tags: [nora, note-tier, dreamer, curator, candidates, dateless, anchor, revisit, strikes, timer, schema, proactive]
timestamp: '2026-09-16'
edges:
  - {dst: 37-note-candidates-v2, rel: extends, provenance: asserted}
  - {dst: 34-note-dreamer-consolidation, rel: revises, provenance: asserted}
  - {dst: 33-note-dreamer-schedule, rel: extends, provenance: asserted}
  - {dst: 27-note-dreamer, rel: revises, provenance: asserted}
  - {dst: 40-proactive-origin-delivery, rel: relates_to, provenance: asserted}
version: 1
---

# Time Is a Property of a Note, Not Its Axis

*DEC-190 — assigned at PR opening 2026-09-16, below its decision-index row.*

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
  (`notes.py:132`) — the curator **logs and counts**
  (`nora_note_curator_total{action="dateless_stale"}`,
  `note_curator.py:1282-1293`) and does nothing else. Its own comment says
  why: *"ELIGIBLE, not retired … this merge counts the crossing rather than
  building the discharge decision"*.
- **DEC-189 / #235 (merged 2026-09-16, PR #264) put the title in the staging
  key.** `IDX_NOTE_CANDIDATE_CREATE_ANCHOR_KEY` is now `(workspace,
  anchor_date, anchor_entity_ids, lower(btrim(title)))`
  (`schema.py:2026-2032`, migration `0054`), because in a fixed-membership
  room the entity list is a constant and the key had degenerated to
  `(workspace, anchor_date)`. That is the precondition this node needed — but
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
| identity at staging | `(workspace, anchor_date, entity ids, title)` | `(workspace, title)` + a topic anchor — §3.1 |
| create-vs-update | title resolution (#228), unchanged | title resolution, and a **judgement** where the title does not resolve (37 §101) |
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
  the retire-only posture, no merge verb.

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
| judgement gate for a dateless candidate | **MUST BUILD** — 37 §101 |
| `revisit_at` seeded at dateless creation | **MUST BUILD** — §3.2, highest consequence |
| retirement ACTION at the strike ceiling | **MUST BUILD** — §3.2 / 37 §4 |
| close verb in `note-candidates` | **MUST BUILD** — §3.5 |
| dateless index line carries a time | **MUST BUILD** — §3.3 |

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
- **#236 — partly absorbed.** The duplicate bound it tracks is, for the
  dateless lane, exactly two things this node specifies: the judgement gate
  (37 §101) at the front, and the strikes retirement at the back. What it is
  **not** is a merge verb — merge was deleted 2026-09-08 and nothing here
  revives it. The dated lane's duplicate bound is unchanged and stays #236's.
  The one thing this node *adds* to #236's surface is §3.1's NULL-key defect,
  which would create dateless duplicates faster than any gate retires them.

## Status / Checkpoint

**2026-09-16 — design only. No code, no schema, no test, no policy.** Every
line-number citation above was read out of the tree on this date; the two
readings this node did NOT measure and states as reasoning are named in §3.1
(Postgres NULLS-DISTINCT semantics on the staging index, derived from the
index declaration and the documented default rather than from a probe) and
§3.3 (that a dateless index line reads worse without a time, argued from
AGENTS.md's measured relevance-gate case, not measured here). Both are cheap
to settle and both should be settled in the build step rather than assumed.

Related: #118 (the build), #159 (follow-on), #236 (duplicate bound), #101
(discharge, shipped, off), #103 (proactive adapter). The design lineage is
27 → 33 → 34 → 37 → this node; 36 is the review draft that first recorded
«date was never meant to be identity» (C3).
