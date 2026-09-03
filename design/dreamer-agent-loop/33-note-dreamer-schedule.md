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

# The Note Dreamer — Notebook Positioning, a Create Path, and Per-Adaptor Schedule

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
   life — a `note_search` miss. Zero `note_create`, zero `note_update`, ever.
   The tag path has the tools but no recognition: with an empty index the
   `<notes>` block is absent from every prompt, and doctrine carries no note
   recognition at all. The curator is housekeeping-only ("You are NOT writing
   anything"). **Nothing in the system can create a note.** The operator's
   diagnosis: *"呢個好依賴 Dreamer 醒唔醒目，因為冇可能靠每一 turn、每一個
   Nora reply 嘅 message 去 create 一個咁 complex 嘅 note 或者瘋狂 update."*

3. **The Dreamer runs as a flat list, not a schedule.** `dream_loop`
   (`nora/app/runtime.py:530`) iterates `for adaptor in adaptors` with no
   ordering concept, and every adaptor without a per-adaptor schedule inherits
   the shared `[language]` fire condition (30 minutes of quiet). The operator
   wants the note tier to follow the fact tier's cadence change — fewer runs,
   deeper runs: *"由一開始好快好頻密，轉變到一日先行一次、更加 in-depth 咁
   做，我覺得都要 follow 嚟做呢件事。可能冇話一日做一次，可能每一個鐘，
   或者都係行 watermark 咁樣。未密到要 5 分鐘行一次，我哋 5 分鐘都冇嘢
   create、冇 candidate。"* And explicitly **not** a dependency graph: *"我唔想
   搞咁多 dependencies，好似而家變相好似好複雜喎。"*

## 2. What is already true (verified against the code)

| Claim | Where | Status |
|---|---|---|
| Per-adaptor schedule is designed | [32](32-fact-dreamer-schedule.md) §3.1 — `AdaptorSchedule(min_interval_seconds, batch_limit, max_items)`, name-keyed mapping, no Protocol change | ✅ designed; implementation on `feat/fact-dreamer-schedule-impl` (unmerged) |
| The note tier has two adaptors, neither creates | `NoteRevisitor` (`nora/language/note_revisitor.py:485`, `name="note-revisit"`, workspace-keyed, watermark-free) and `NoteCuratorAdaptor` (`nora/language/note_curator.py:227`, `name="note-curation"`, workspace-keyed, watermark-free) | ✅ both built, both read-only |
| The tag path has create/update tools | `note_create`/`note_update` (`nora/agent/tools.py`), gated by R-55 read-before-write latch | ✅ exists, never fired |
| `note_candidate` is a MISS CACHE, not a create queue | `nora/infra/sql/schema.py:1821` — `(workspace, probe)` unique key, `occurrence_count`, `observed_in`; written by `record_miss` (`nora/agent/tools.py:2000`) | ✅ the only candidate table, wrong shape for create |
| The fact tier's Phase A/B split is the pattern to copy | `FactCandidateAdaptor` (`nora/language/fact_adaptor.py:201`, scope-keyed, watermarked, tool-free) → `fact_candidate` table → `FactAdaptor` (Phase B adjudication) | ✅ proven |
| The Dreamer has no write tools | `WORKSPACE_SAFE_TOOLS` + `read_only_tools()` ceiling | ✅ structural |
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
| reads | message batch | message batch + the workspace's note index (title/description) |
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
workspace's standing index (title + description, the same projection the tag
path gets) and is told: *if this arrangement is already written down, do not
propose it again — propose an update instead, or nothing.*

**The candidate table needs a shape change.** `note_candidate` today is a miss
cache — `(workspace, probe)`, `occurrence_count`, `observed_in`. It is the
wrong shape for a create queue. The design:

- Add `kind` (`miss` | `create`) — existing rows are all `miss`, no backfill.
- For `kind='create'`: `title`, `description`, `body`, `anchor_date`,
  `closing_condition`, `message_ids` (attribution), `status`
  (`pending` | `created` | `refused`), `attempts`.
- The miss cache keeps its `(workspace, probe)` unique key; create candidates
  key on `(workspace, title)` — a proposal for a title that already exists is
  an update proposal, not a duplicate.

**The create decision is code, not model.** The adaptor proposes; a
deterministic gate decides: does a standing note with this anchor already
exist? If yes → update proposal (only if the proposal adds something the
existing note lacks). If no → create proposal. The model never writes a note
directly — the same `model proposes, code disposes` split the fact tier
already enforces, and the same reason: a model deciding truth while the room
waits is the shape this whole bundle refuses.

**The tag path stays as it is** — it may only touch a note it read this turn
(R-55). It never creates. The cold start proved it will not; the adaptor is
the recognition it lacks.

### 4.1 The tool surface — read-only awareness, never delivery

**Note dreamers hold read-only verbs only.** The operator's trim, confirmed
2026-09-03: a proactive adapter (deferred — see §8) owns initiative and
delivery; the note tier owns only the note's lifecycle. So the note adaptors
may be granted the cross-tier READ surface so they know what is happening
while they consolidate — `list_reminders`, `list_events`,
`search_knowledge`/`get_knowledge`, `search_memory_workspace`, `note_read`/
`note_search` — and none of the delivery or write verbs (`remind`,
`cancel_reminder`, `create_event`, `update_event`, `cancel_event`).

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

```toml
[dreamer.schedules.note-candidates]
min_interval_seconds = 3600   # hourly — the operator's "可能每一個鐘"
batch_limit = 200            # a week of one room's planning, not a day of everything

[dreamer.schedules.note-curation]
min_interval_seconds = 86400 # daily — housekeeping, quality over latency
batch_limit = 20             # the existing max_expired_per_tick shape

[dreamer.schedules.note-revisit]
min_interval_seconds = 3600  # hourly — but self-scheduled by revisit_at
batch_limit = 5              # max_revisits_per_tick
```

**Why these numbers:** the operator's own words — *"未密到要 5 分鐘行一次，
我哋 5 分鐘都冇嘢 create、冇 candidate。"* A note arrangement spans days, not
minutes; hourly gives the adaptor a week's exchange to read without paying
for a model call every five minutes. The curator is the tier's decay — daily
matches the fact tier's cadence change (fewer, deeper). The revisitor is
already self-scheduled by each note's `revisit_at`; the hourly floor just
stops it re-checking the same due note every tick.

**The budget follows the fact tier's pattern too** — the note-candidates
adaptor gets the enlarged budget shape ([32] §3.2): more turns, more total
tokens, longer timeout, because it reads a week of transcript and proposes a
full note, not a triple.

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
miss one it is about to create. The schedule above already separates them —
hourly candidates, daily curator — which is the phase offset, expressed as
schedule rather than as a dependency.

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

**2026-09-03 (later, same session) — three boundary decisions.** (1) A
**proactive adapter is confirmed as a separate, deferred design** — cross-
tier initiative ("Nora 由 passive 變 active") built on top of the note,
knowledge and language streamers' settled state. It is NOT the note tier's
job, and this node records its existence so the note tier does not grow
into it. (2) **The note tier is trimmed to lifecycle-only**: note dreamers
get read-only cross-tier awareness (§4.1) and never delivery or write verbs;
the revisitor's reaching-a-person half belongs to the proactive adapter,
not to a widened `tool_subset_faults` here.
