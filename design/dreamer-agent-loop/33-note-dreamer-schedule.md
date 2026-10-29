---
type: design
title: "The Note Dreamer — Notebook Positioning, a Create Path, and Per-Adaptor Schedule"
description: "The note tier's only create path is a tag-path tool that has never fired. The tier's own spec defines a note as tracked uncertainty, which makes a notebook hold only open questions and leaves the settled half of an arrangement with no home. This node reframes the note as a notebook — the home for anything that must survive across turns — gives the tier a Dreamer-side create path (note-candidates, the fact tier's Phase A shape), gives each note adaptor its own schedule with a phase-offset rule instead of a dependency graph, and trims the tier to lifecycle-only: note dreamers hold read-only cross-tier awareness, never delivery verbs — a separate proactive adapter (deferred) owns initiative."
tags: [nora, note-tier, dreamer, notebook, candidates, schedule, discharge, phase-offset]
timestamp: '2026-09-03'
edges:
  - {dst: 27-note-dreamer, rel: extends, provenance: asserted}
  - {dst: 32-fact-dreamer-schedule, rel: follows, provenance: asserted}
  - {dst: 19-between-tiers, rel: extends, provenance: asserted}
  - {dst: 20-knowledge-tier, rel: relates_to, provenance: asserted}
  - {dst: 08-state, rel: extends, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.

> **FURTHER DEPRECATED IN PART 2026-09-08 — the note tier's entity-resolution pass and the curator's merge half are deleted.** §5's schedule has two note adaptors, not three (`note-entity-resolution` is gone), and the `fact-adjudication → entity-resolution` dependency in §6 names two passes that no longer exist. The create path (§4) still stands; its gate compared the anchor DATE alone from that day until 2026-09-13.

> **SUPERSEDED IN PART 2026-09-13 — §4's create gate no longer decides identity in code (issue #228).** The date-only key fired twice in production and merged two genuinely different occasions both times, which is the reversal condition `nora.language.note_candidates` had written for it. §4's sentence *"does a standing note with this anchor already exist"* is replaced by: **the candidate carries the TITLE of the standing note it updates, exactly as the index rendered it, and code resolves that title** — one match revises, none creates, more than one creates rather than guessing. §4's argument against trusting the model («relying on the model to decline is self-attestation on a destructive operation») is what changed, not its principle: since the same issue's first half a revision APPENDS rather than replaces, so the operation is no longer destructive and the bar no longer applies. The anchor date remains what §4 always described it as for every other purpose — the note's `revisit_at` seed, the staging key's first half, and what the curator reads to ask whether a moment has passed.


# The Note Dreamer — Notebook Positioning, a Create Path, and Per-Adaptor Schedule

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 1. The problem, in the operator's words

Three complaints, all confirmed against the design corpus and the live system:

1. **The note's positioning is awkward.** The spec defines a note as *tracked
   uncertainty* — `(anchor, uncertainty, closing condition, body)` — and the
   operator's framing is sharper: *"note 嘅定位係咩咧？因為我唔覺得佢唔應該
   淨係擺啲 uncertainty 嘅 information 入去，而係點樣用一個 note 嘅定位、
   一個筆記本嘅定位。"* A notebook is where you write **anything** — not only
   open questions. The dinner plan (three candidates, nobody picked) is not
   an uncertainty; it is an **arrangement** with an unsettled corner. The
   settled half — who is coming, who proposed which place, who does not eat
   beef — has no home under the current definition, and the tier's own
   discriminator ("a thing with no closing condition is not a note") makes
   the notebook hold only questions.

2. **The tier has no create path that can fire.** The cold start (§5.4 of
   [27](27-note-dreamer.md)) measured **one** note-tool call in the tier's
   life — a `note_search` miss. Zero `note_create`, zero `note_revise`, ever.
   The tag path has the tools but no recognition: with an empty index the
   `<notes>` block is absent from every prompt, and doctrine carries no note
   recognition at all. The curator is housekeeping-only ("You are NOT writing
   anything"). **Nothing in the system can create a note.** The operator's
   diagnosis: *"呢個好依賴 Dreamer 醒唔醒目，因為冇可能靠每一 turn、每一個
   Nora reply 嘅 message 去 create 一個咁 complex 嘅 note 或者瘋狂 update."*

3. **The Dreamer runs as a flat list, not a schedule.** `dream_loop`
   (`nora/app/runtime.py:530`) iterates `for adaptor in adaptors` with no
   ordering concept. Adaptors WITHOUT a watermark (`uses_watermark = False`)
   have **no fire condition at all** — the runtime calls them every tick and
   relies on `inputs()` returning `""` to suppress the model call
   (`runtime.py:470`); adaptors WITH a watermark and no per-adaptor schedule
   inherit the shared `[language]` fire condition (30 minutes of quiet). The operator
   wants the note tier to follow the fact tier's cadence change — fewer runs,
   deeper runs: *"由一開始好快好頻密，轉變到一日先行一次、更加 in-depth 咁
   做，我覺得都要 follow 嚟做呢件事。可能冇話一日做一次，可能每一個鐘，
   或者都係行 watermark 咁樣。未密到要 5 分鐘行一次，我哋 5 分鐘都冇嘢
   create、冇 candidate。"* And explicitly **not** a dependency graph: *"我唔想
   搞咁多 dependencies，好似而家變相好似好複雜喎。"*

## 2. What is already true (verified against the code)

| Claim | Where | Status |
|---|---|---|
| Per-adaptor schedule is designed AND wired | [32](32-fact-dreamer-schedule.md) §3.1 — `AdaptorSchedule(min_interval_seconds, batch_limit, max_items)`, name-keyed mapping, no Protocol change. Merged into master (`3ca43b83`); composition wires `dream_schedules` (`composition.py:2415`) for `fact-candidates` ONLY — see §5 N1 for why the note curator cannot use it as-is | ✅ designed + wired (fact only) |
| The note tier has two adaptors, neither creates | `NoteRevisitor` (`nora/language/note_revisitor.py:485`, `name="note-revisit"`, unit-keyed, watermark-free) and `NoteCuratorAdaptor` (`nora/language/note_curator.py:227`, `name="note-curation"`, unit-keyed, watermark-free) | ✅ both built, both read-only |
| The tag path has create/update tools | `note_create`/`note_revise` (`nora/agent/tools.py`), gated by R-55 read-before-write latch | ✅ exists, never fired |
| `note_candidate` is a MISS CACHE, not a create queue | `nora/infra/sql/schema.py:1821` — `(unit, probe)` unique key, `occurrence_count`, `observed_in`; written by `record_miss` (`nora/agent/tools.py:2000`) | ✅ the only candidate table, wrong shape for create |
| The fact tier's Phase A/B split is the pattern to copy | `FactCandidateAdaptor` (`nora/language/fact_adaptor.py:201`, scope-keyed, watermarked, tool-free) → `fact_candidate` table → `FactAdaptor` (Phase B adjudication) | ✅ proven |
| The Dreamer has no write tools | `UNIT_SAFE_TOOLS` + `read_only_tools()` ceiling | ✅ structural |
| The note tier's adaptors have no schedule of their own | `composition.py:2457/2489` — both built under `[notes]` gates, no `AdaptorSchedule` wired | ❌ to add |

## 3. The reframe — a note is a notebook entry, not an uncertainty

**The note is the home for anything that must survive across turns.** The
spec's `(anchor, uncertainty, closing condition, body)` is not wrong — it is
incomplete. Uncertainty is one *kind* of notebook entry (an open question);
an arrangement, a commitment, a follow-up, a plan with a settled half and an
unsettled corner are others. What they share is not uncertainty — it is
**forward-lookingness**: the entry has not finished its job.

The tier's own discriminator survives, widened:

> **A thing with no closing condition is not a note.** It is either a fact
> (already settled) or ignorance (nothing would resolve it).

"Closing condition" now means *what would finish this entry's job* — the
restaurant is booked, the commitment is fulfilled, the follow-up is done —
not only *what would resolve this uncertainty*. The discriminator still stops
the tier swallowing the other three; it just no longer limits the notebook to
questions.

**The settled half has a home: discharge.** The dinner plan is not an
uncertainty, but it is also not a fact — it is a bundle. When the note closes:

| settled content | goes to | warrant |
|---|---|---|
| "dinner on the 9th, 3 people" | **Event machinery** (spec §2.2 — the awareness/commitment split) | commitment |
| "Yardbird is a yakitori place" | **knowledge tier** | `source`/`stated` |
| "Miles likes Yardbird for the jazz" | **Honcho** | person-claim |
| the note row itself | **retired-closed** — stays in the store, drops out of the always-on index, `note_search` finds it | archive |

**Close is a discharge, not a delete/suppress/move choice.** The note row is
suppressed (retired, never deleted — revision history is the evidence chain);
the settled content is routed by kind to its permanent home; the unsettled
remainder keeps the note alive. The note is a **staging area**; closing is the
**pump** that routes settled content out and retires the container.

**What this changes in the field convention (§5.1 of [27](27-note-dreamer.md)):**

| field | was | now |
|---|---|---|
| **title** | the anchor, in words | unchanged |
| **description** | the uncertainty and its closing condition | **what this entry is + why it is not finished + what would finish it** |
| **body** | the arrangement | unchanged — the thing that has to survive |

The index's purpose is unchanged: Nora decides *whether to read the body this
turn*. "Three restaurants proposed, nobody has picked one" still tells her
whether it bears on what is being said — it just no longer pretends the
restaurant question is the whole note.

**Recognition follows the reframe.** The cold-start fix (§5.4) added
「未有結論嘅嘢」to `conduct.md`. Under the notebook framing, recognition is
broader: *this conversation has something that must survive across turns* —
an undecided plan, a commitment, a follow-up, an arrangement. The doctrine
line changes from "open questions" to "things that are not finished."

## 4. The create path — a note-candidates adaptor

**The tier needs the fact tier's Phase A shape: a Dreamer adaptor that reads
a message batch and proposes notes, with code deciding create.**

| | fact-candidates (proven) | note-candidates (new) |
|---|---|---|
| key | scope (per-room watermark) | scope (per-room watermark) |
| reads | message batch | message batch + the unit's note index (title/description) |
| writes | `fact_candidate` rows | `note_candidate` rows, `kind='create'` |
| tools | none — one cheap call | **read-only awareness only** — see §4.1 |
| output | `(subject, predicate, object, message_ids)` | a full note proposal: `(title, description, body, anchor, closing_condition, message_ids)` |
| code decides | adjudication eligibility | create/update eligibility |

**Why scope-keyed:** the transcript is per-room, and a note's arrangement
spans a week of conversation — the operator's own case: *"呢啲 conversation、
呢啲 transcript 可能 cross 一個禮拜去做。"* A watermark over each room's
messages is the only way the adaptor sees the whole exchange, not a
5-minute slice.

**Why it needs the note index:** unlike fact candidates (new claims, no
dedupe against existing facts — that is adjudication's job), a note proposal
must not duplicate a note that already exists. The adaptor sees the
unit's standing index (title + description, the same projection the tag
path gets) and is told: *if this arrangement is already written down, do not
propose it again — propose an update instead, or nothing.*

**The candidate table needs a shape change.** `note_candidate` today is a miss
cache — `(unit, probe)`, `occurrence_count`, `observed_in`. It is the
wrong shape for a create queue. The design:

- Add `kind` (`miss` | `create`) — existing rows are all `miss`, no backfill.
- For `kind='create'`: `title`, `description`, `body`, `anchor_date`,
  `closing_condition`, `message_ids` (attribution), `status`
  (`pending` | `created` | `refused`), `attempts`.
- The miss cache keeps its `(unit, probe)` unique key. Create candidates
  dedupe on the **anchor key** (`unit, anchor_date, entity_set` — the
  same `group_on_anchor_key` the curator uses), not on title: a proposal for
  an anchor that already has a standing note is an update proposal, not a
  duplicate (Kimi review finding M10).
- **Migration constraint (Kimi review finding):** `probe`, `first_seen_at` and
  `last_seen_at` are `nullable=False` with no defaults (`schema.py:1829-1831`).
  A `kind='create'` row cannot be inserted without either relaxing these or
  writing meaningful values — the migration must make `probe` nullable for
  `kind='create'` rows (or give it a sentinel default) and treat
  `first_seen_at`/`last_seen_at` as the proposal's sighting timestamps.
  The `(unit, title)` create-key and the existing `(unit, probe)`
  miss-key are two different uniqueness domains, not one: the upsert for a
  re-sighting of a create candidate needs its own `ON CONFLICT` behaviour.

**The create decision is code, not model.** The adaptor proposes; a
deterministic gate decides: does a standing note with this anchor already
exist (**anchor-keyed via `group_on_anchor_key`, not title-keyed** — two
spellings of one occasion are one note; two notes sharing a title but with
different anchors are two notes; Kimi review finding). If yes → update
proposal (only if the proposal adds something the existing note lacks). If
no → create proposal. The model never writes a note directly — the same
`model proposes, code disposes` split the fact tier already enforces, and
the same reason: a model deciding truth while the room waits is the shape
this whole bundle refuses.

**The tag path stays as it is** — it may only touch a note it read this turn
(R-55). It never creates. The cold start proved it will not; the adaptor is
the recognition it lacks.

### 4.1 The tool surface — read-only awareness, never delivery

**Note dreamers hold read-only verbs only.** The operator's trim, confirmed
2026-09-03: a proactive adapter (deferred — see §8) owns initiative and
delivery; the note tier owns only the note's lifecycle. So the note adaptors
may be granted the cross-tier READ surface so they know what is happening
while they consolidate — and none of the delivery or write verbs (`remind`,
`cancel_reminder`, `create_event`, `update_event`, `cancel_event`).

**Kimi review finding M1 — the grant is constrained by tool_subset_faults.**
`tool_subset_faults` (`dreamer.py:325`) refuses, at startup AND per-run, any
tool outside the read-only ceiling, and any ROOM-scoped tool on a
UNIT-keyed adaptor (a unit is not a conversation id; room-scoped
tools would answer EMPTY — `unavailable` reported as `nothing_found`, the
one substitution R-27 forbids). So the unit-keyed curator can only
hold tools in `UNIT_SAFE_TOOLS` (`dreamer.py:259`) — which today is:
`search_knowledge`/`get_knowledge`/`knowledge_premises`/`knowledge_asserters`/
`knowledge_entity`, `note_read`/`note_search`, `search_memory_workspace`,
`web_search`/`web_fetch`, `now`, `calculate`, `weather`, `version`,
`social_fetch`, `media_search`. **`list_reminders`, `list_events`,
`create_event` etc. are NOT in that set** — they are room-scoped
(conversation-bound), so they cannot be granted to a unit-keyed adaptor
without either (a) making them unit-safe with the same supplied-key path
32 §3.4 used for the knowledge tools (their tables are unit-keyed
already), or (b) accepting that the curator's awareness excludes reminders and
events until the proactive adapter reads them. This node records the
constraint; the exact grant list is an implementation decision under it.
`note-candidates` is scope-keyed, so it can hold room-scoped tools — but
its design keeps it tool-free anyway (the fact tier's cost split).

The revisitor's schedule_reminder block ([27](27-note-dreamer.md) §3) is
**not widened here.** The reaching-a-person half — "a note whose moment has
come, remind the person whose plan it is" — is exactly the proactive
adapter's job, built ON TOP of this tier's settled state, and it belongs to
that deferred design rather than being smuggled into the note tier through
a widened `tool_subset_faults`. Note dreamers propose and consolidate; they
never initiate contact.

## 5. Per-adaptor schedule

Following [32](32-fact-dreamer-schedule.md) §3.1's name-keyed mapping — no
Protocol change, existing adaptors untouched by omission:

```python
# composition.py wiring (NOT a TOML surface — see N2 below)
dream_schedules = {
    "note-candidates": AdaptorSchedule(
        min_interval_seconds=3600,   # hourly — the operator's "可能每一個鐘"
        batch_limit=200,             # a week of one room's planning
    ),
}
```

**Kimi review finding N1 (MAJOR, post-rebase) — `AdaptorSchedule` is
watermark-bound, so only `note-candidates` can use it.** The schedule check
lives inside the watermark branch of `_dream_adaptor_once`
(`runtime.py:498-503`): an adaptor with `uses_watermark = False` returns at
`runtime.py:470` BEFORE the schedule is consulted. `note-candidates` is
scope-keyed and watermarked, so its hourly cadence works. The curator
(`uses_watermark = False`, `note_curator.py:267`) can NOT be given a daily
`AdaptorSchedule` — it would run every tick exactly as it does today,
because `min_interval_seconds` is never read.

So the cadence for the curator must be decided differently — and the
operator's framing (2026-09-03) settles it: **the note tier's disposal is a
TRUE cron job, not a data-driven pass.** "Fact tier: data accumulates, so
there will always be data. Note curation: NOT about accumulating data —
purely a cron job." The curator's trigger is the calendar (anchor dates pass
whether or not the room speaks), so it must fire even with ZERO new data —

- **(a) RECOMMENDED — add a non-watermark schedule path.** In
  `_dream_adaptor_once`, check the schedule BEFORE the `uses_watermark` early
  return (`runtime.py:470`): a watermark-free scheduled adaptor fires when
  `now - last_fired >= min_interval_seconds`, with NO `lag > 0` requirement.
  The runtime's `failures`/`last_failure_at` dicts already track per-unit
  last-run state; a per-(adaptor,key) `last_fired_at` dict is the same shape.
  This makes `AdaptorSchedule` live up to its own docstring ("one adaptor's
  own clock") for every adaptor. **This is required, not optional**: the
  existing data-gated `AdaptorSchedule` (`fires = lag > 0 and age >=
  interval`, `runtime.py:497`) is WRONG for the curator — a silent room would
  never fire, and time-driven retirement would never happen.
- **(b) REJECTED as the primary answer — keep the curator every-tick with
  empty-`inputs()` suppression** — what it does today. `inputs()` returns
  `""` on the overwhelming majority of ticks (nothing expired, no merge
  groups), and the runtime skips the model call (`dreamer.py:702-711`). It
  is functionally correct (the calendar check runs every tick) but pays a
  cheap SQL round per unit per 300s tick forever. It is the fallback
  while (a) is unbuilt, not the design.

**N2 (MINOR) — there is no `[dreamer.schedules.*]` TOML surface.**
`DreamerConfig` (`nora/config.py:2091-2245`) has `budgets` but no
`schedules`, with `extra="forbid"` (`:2138`) — 33's earlier TOML block would
fail at boot. The shipped mechanism is the hardcoded name-keyed dict at
`composition.py:2415-2421` (fact-candidates only), wired through
`composition.py:2616` and `__main__.py:198-219`. The schedule wiring for
note adaptors belongs in composition beside it.

**Why these numbers:** the operator's own words — *"未密到要 5 分鐘行一次，
我哋 5 分鐘都冇嘢 create、冇 candidate。"* A note arrangement spans days, not
minutes; hourly gives the adaptor a week's exchange to read without paying
for a model call every five minutes. The curator is the tier's decay —
daily matches the fact tier's cadence change (fewer, deeper), subject to
N1 option (a) or (b) above. **The budget follows the fact tier's pattern
too** — the note-candidates adaptor gets the enlarged budget shape ([32]
§3.2): more turns, more total tokens, longer timeout, because it reads a
week of transcript and proposes a full note, not a triple.

## 6. Phase offset, not a DAG

**The note tier has no execution dependency between its adaptors.** The
operator asked for ordering — *"至少要有一個 DAG，即係有個 ordering 去 run，
而唔係一窩蜂去 run"* — and the answer is that the note tier does not need
one:

| adaptor | reads | writes |
|---|---|---|
| note-candidates | transcript + note index | `note_candidate` (kind=create) |
| note-curation | notes | notes (merge/retire) |
| note-revisit | notes + evidence | notes (retire) |

The curator and revisitor read **notes**, never candidates. The candidates
feed a deterministic gate that writes notes; the next curator/revisitor tick
sees the new notes. **Ordering is emergent at the data level** — a pass with
no input does not fire (`lag = 0`), and a pass whose input just landed sees
it on its next tick. No dependency graph, no topological sort, no engine.

**The one rule worth writing down is a phase offset, not a graph:** within a
tier, adaptors that share a table should not run on the same tick. The
candidates pass writes; the gate creates; the curator retires. If they ran
simultaneously, the curator could retire a note the gate just created, or
miss one it is about to create.

**Kimi review finding M6 — the schedule does NOT itself provide the phase
offset.** `min_interval_seconds` floors (hourly vs daily) are not phase
offsets: a daily curator tick and an hourly candidates tick coincide every
24h, and `dream_loop` dispatches adaptors as CONCURRENT tasks with no
ordering (`runtime.py:765-796`). The separation of periods is necessary but
not sufficient. What the rule must actually be: **a note the gate created in
this tick is not eligible for retirement until a later tick** — enforced
either in the curator's selection (explicit same-tick exclusion) or by a
deliberate offset (e.g. candidates at :00, curator at :30 UTC) where the
adaptor clock supports it. The practical hazard is low — the curator only
retires past anchors and new notes carry future anchors — but the schedule
alone is not the guarantee.

**Why no delivery verbs appear here:** the reminder lifecycle the operator
described — "未 decide → remind, 後嚟 decide 咗 → cancel" — is cross-tier
synthesis, not note maintenance. A note reaching its anchor with its
question open is an INPUT the proactive adapter reads (a `<notes>` index
entry, settled state); deciding to remind, and later cancelling when the
note closes, is initiative. Putting it inside the note tier would blur the
boundary the operator explicitly wants kept — note dreamers consolidate,
the proactive adapter acts.

**The fact tier is different and out of scope here.** `fact-candidates` →
`fact-adjudication` → `entity-resolution` have real data dependencies
(candidates feed adjudication, facts feed resolution). That ordering is
[32](32-fact-dreamer-schedule.md)'s business and another session's; this node
records only that the note tier does not need the machinery.

## 7. Discharge — what closing does

The operator's question: *"close 咗個 note 係冇問題，但係點收埋先？純粹
delete 咗佢？suppress 咗佢？定係 move 入去個 facts 度？"*

**All three, at different layers — a discharge, not a choice:**

1. **The note row: suppress.** `retired-closed`, stays in the store, drops
   out of the always-on index, `note_search` finds it. Never deleted —
   revision history is the evidence chain (who proposed which place, who
   liked which), and a lossy step with no retained input cannot be
   re-derived when it goes wrong (spec §7.4's own argument).
2. **The settled content: route by kind.** The note is a bundle, not a
   claim. "Dinner on the 9th" is a commitment → Event machinery. "Yardbird
   is a yakitori place" is a world-claim → knowledge tier. "Miles likes
   Yardbird for the jazz" is a person-claim → Honcho. Each goes to the tier
   whose warrant it carries ([19](19-between-tiers.md) §1's table).
3. **The unsettled remainder: keeps the note alive.** If the closing
   condition is not met, the note does not close.

**What exists today and what does not:** the revisitor's `retired-closed`
half is built ([27](27-note-dreamer.md) §11). Event promotion exists in the
spec (§7.3) but is not built. **A general discharge pipeline does not exist**
— the revisitor retires a note and stops; it does not route settled content
to knowledge or Honcho. That is the gap this node records, and it is the
curator's job (the only adaptor that may act across notes), sequenced after
the create path exists — a note must exist before it can be discharged.

## 8. The proactive adapter — deferred, and why it is not this tier's

> **REVISED 2026-09-09 by [39-proactive-ooda-loop.md](39-proactive-ooda-loop.md).** The adaptor is decided there: a Dreamer adaptor, unit-keyed through a home room, whose one act verb is her own `remind`, stamped by Dreamer provenance to write a proactive reminder row, and whose decision IS the tool call — so «model-proposes / code-disposes» below does not describe it (39 D4). The boundary rule below stands. The «knowledge and language tiers» named as inputs were deleted 2026-09-07 (issue #115) and are historical here.

Confirmed with the operator 2026-09-03, recorded here so the boundary stays
visible: **Nora should move from passive to active** — initiative derived
from the memory, notes, knowledge and events she has collected, with
synthesis across tiers. That is a real capability and it is **not the note
tier's job**.

- **Not NoteRevisitor + tools.** The reaching-a-person half ("a note whose
  moment has come, remind the person whose plan it is", then cancel when
  the note closes) is initiative, not note maintenance. Widening
  `tool_subset_faults` (27 §3's blocked route) would blur the tier's
  boundary — the exact thing this session decided against.
- **Not a Heartbeat.** Heartbeat is deterministic delivery machinery (pick
  due rows, fire). Proactive needs judgement — what is worth doing, given
  everything Nora holds.
- **A new adaptor, built on top of the streamers.** Its input is the settled
  cross-tier summary — `<notes>` index, standing, facts block, event and
  reminder state — not raw transcripts; its output is proposed actions
  (remind, cancel, update, discharge, reach out), model-proposes /
  code-disposes like every other adaptor; its cadence is daily, because
  synthesis is expensive.

**Why it is deferred:** it is built ON TOP of this tier and the knowledge and
language tiers — it reads their settled state. There is nothing to be active
about until the note tier has a create path, a schedule, and a discharge
pipeline. The operator's direction: *"都係返返去 focus note 嗰度先。"* A
future node (33+ or a separate design) owns the proactive adapter.

**The boundary rule, one line:** *the note tier consolidates and closes; it
never initiates contact. Initiative is a separate adaptor's job, reading
this tier's settled state.*

## Status / Checkpoint

2026-09-03 — written after the operator's review session on the note tier's
positioning. The reframe (§3), the create path (§4), the schedule (§5), the
phase-offset rule (§6) and the discharge model (§7) are design decisions
recorded here; none is implemented. The `note_candidate` shape change and
the `note-candidates` adaptor are the implementation work, and the discharge
pipeline is sequenced after them. The fact tier's DAG question is explicitly
out of scope — [32](32-fact-dreamer-schedule.md) and its implementation
branch own it.

**2026-09-03 (later, same session) — three boundary decisions.**

**(0) The revisitor is merged into the curator** — [34](34-note-dreamer-consolidation.md).
The merge is sound (Kimi external audit: REQUEST CHANGES, no BLOCKING), the
revisitor's distinctive machinery having no writer in production.

**(1) A proactive adapter is confirmed as a separate, deferred design** —
cross-tier initiative ("Nora 由 passive 變 active") built on top of the note,
knowledge and language streamers' settled state. It is NOT the note tier's
job, and this node records its existence so the note tier does not grow
into it. (2) **The note tier is trimmed to lifecycle-only**: note dreamers
get read-only cross-tier awareness (§4.1) and never delivery or write verbs;
the revisitor's reaching-a-person half belongs to the proactive adapter,
not to a widened `tool_subset_faults` here.

**(3) Kimi external audit fixes folded in** (independent model, 2026-09-03):
M1 (tool grant constrained by `tool_subset_faults` / unit-safe set,
§4.1 now records it), M3 (§1 factual fix — watermark-free adaptors have NO
fire condition, they run every tick), M5 (`note_candidate` migration
nullable constraints + two uniqueness domains, §4 now records it), M6
(§6 phase-offset claim corrected — the schedule alone is not the guarantee,
the rule is an explicit selection guard), M10 (§4 gate dedupes on anchor key,
not title). See [34](34-note-dreamer-consolidation.md) Status for the full
Kimi record and the M2 (wake policy) resolution.
