---
type: design
title: "The Note Dreamer, Consolidated — Revisitor into Curator, and the Evidence Grounding that Moves With It"
description: "The note tier runs three adaptors but does the work of one and a half. The revisitor's only production trigger is a passed anchor date — the same selection the curator makes — and its distinctive machinery (revisit_at, strikes, early close) has no writer anywhere, so it is dead in production. The reaching-a-person half that justified the split is deferred to the proactive adapter. This node merges the revisitor into the curator, argues each of spec §9.0's split reasons in turn, and specifies what moves across: the evidence grounds, the retirement states, and the two loop-breaker modes."
tags: [nora, note-tier, dreamer, curator, revisitor, consolidation, retirement, evidence]
timestamp: '2026-09-03'
edges:
  - {dst: 33-note-dreamer-schedule, rel: extends, provenance: asserted}
  - {dst: 27-note-dreamer, rel: corrects, provenance: asserted}
  - {dst: 08-state, rel: extends, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-16 — §5.4's dateless aging describes a lane the tier is now organised AROUND, not an exception to a dated one.** §5.4 reads «no anchor, so expiry does nothing» as a gap in a date-based architecture; the operator's 2026-09-16 reframe makes time an optional PROPERTY of a note rather than the tier's axis, so a dateless note is an ordinary note and the strike ceiling is its lane's own retirement route rather than a fallback. §5.3's `revisit_at` policy and §5.5's metrics are unaffected and are exactly what the dateless lane runs on. The evidence that decided it is in [41-note-tier-time-is-optional.md](41-note-tier-time-is-optional.md) §0–§3.2, which also records that the retirement ACTION at the ceiling still does not exist — `nora.language.note_curator` logs and counts the crossing and does nothing else. See issue #118.

> **PARTLY DEPRECATED 2026-09-08 — the curator's MERGE half is deleted.** §2.1's `merge_groups`, §4.1's first of three reads, §5.1's cap and the M7 note at the end all describe a verb that could not run once the knowledge tier stopped minting entities (`note_entity` never populated, every group refused by construction), and it was removed rather than left wired-and-inert. The consolidation this node argues — the revisitor's selection, evidence grounding and wake/strike bookkeeping folded into the curator — is unaffected and is the whole of what the curator does now.

# The Note Dreamer, Consolidated — Revisitor into Curator

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 1. The claim

**The note tier runs three adaptors but does the work of one and a half.** The
create-facing `note-candidates` adaptor ([33](33-note-dreamer-schedule.md) §4)
is genuinely separate — it faces the transcript, proposes, and is the tier's
recognition. The other two — `note-curation` and `note-revisit` — do
**substantially the same work**: select notes whose moment has come, read
their bodies and evidence, ask a model whether each is still open, and retire
the closed or expired ones. This node merges the revisitor into the curator,
records why that is now correct, and specifies exactly what moves across.

## 2. What each adaptor actually does today (verified against the code)

### 2.1 The curator (`nora/language/note_curator.py` (deleted 2026-10-03, DEC-277))

- **Selection** (`inputs`): SQL-only, zero tokens —
  - `merge_groups` — `GROUP BY` on the anchor key `(anchor_date, resolved
    entity set)`, via `nora.domain.notes.group_on_anchor_key` (both stores
    share the pure function). Eligibility is decided in code; the model never
    decides whether two notes merge.
  - `anchored_before(day=today)` — notes whose anchor date is behind the
    room's calendar day (HKT), capped by `max_expired_per_tick`.
  - A note in a merge group is **not** also rendered as expired — the group
    wins, because merging keeps both bodies and retiring one keeps neither.
- **Judgement** (model): `closed` (the question was answered) / `open` (the
  ordinary answer) / `moot` (the occasion passed and nothing will settle it).
- **Actions** (code): merge, retire. Nothing deleted — retirement is a status
  change.

### 2.2 The revisitor (`nora/language/note_revisitor.py`)

- **Selection** (`_is_due`, line 1075): TWO triggers, a note needs only one —
  - `revisit_at` — a per-note due time. **HAS NO WRITER.** The code's own
    comment: *"`revisit_at` HAS NO WRITER — this branch is unreachable in
    production."* Same for `strikes` and `last_wake_revision_id` — the
    module's own docstring records "nothing anywhere sets any of them" (a
    completeness audit found all three columns exist, `SqlNoteStore` reads
    them into `Note`, and **nothing writes them** — not the tag-path tools,
    not the curator, not this pass).
  - `_anchor_bounds_ms(anchor_date)` — the anchor day has ended (computed in
    UTC, so it reads ~08:00 HKT the next day; recorded, fails conservative).
- **Judgement** (model): `closed` (closing condition met — must cite a
  message AND that message must be in the evidence it was shown) / `expired`
  (moment passed, nothing answered — requires **real evidence** that the room
  was still talking past the anchor: `_covers`) / otherwise no call, note
  stands.
- **Actions** (code): retire (`retired-closed`, `retired-expired`). Never
  merge — it sees one note at a time by construction.

### 2.3 The overlap, stated honestly

For a note whose anchor date has passed, **both adaptors do the same thing**:
select it, read its body, ask a model whether it is still open, retire it if
not. The curator calls it `closed`/`moot`, the revisitor `closed`/`expired` —
three words for two outcomes (answered / passed-and-unanswered), with `moot`
adding "and nothing will settle it".

**What is NOT overlap:**

1. **Early close.** A note anchored to 9 September answered by 「booked
   Yardbird for the 9th」 on 3 August. The revisit design exists to catch this
   via `revisit_at`; the curator only selects past anchors, so it would wait
   until the 9th passed. **But `revisit_at` has no writer — early close is
   unreachable in production.**
2. **Dateless notes.** "What are we playing next" — no anchor, so expiry does
   not apply. The strikes breaker is the designed mechanism for aging these
   out. **`strikes` has no writer — dateless aging is also unreachable in
   production.**
3. **The evidence requirement.** `retired-expired` requires the evidence
   window to COVER the moment — either untruncated or its oldest row
   predating the note (`_covers`, line 989). Silence is not an answer: a room
   that said nothing since the note was written cannot establish that a
   question went unanswered. This is a verdict-grounding rule, not a
   per-adaptor boundary — it moves with the logic.

**So in production today, the revisitor is: early close (dead) + dateless
aging (dead) + expired-with-evidence (the curator's retired half, plus the
`_covers` grounding).** The distinctive half of its machinery does not run,
and the half that does run is the curator's.

## 3. Why the split was made, and why it no longer holds

Spec [§9.0](2026-08-21-note-tier-design.md) records the original decision to
split "one curator" into two, on the grounds that it was *"two different
things wearing one name"*. Each ground, re-examined after the 2026-09-03
decisions:

| spec §9.0 ground | then | now |
|---|---|---|
| **different trigger axes** | sweep (watermark + clock) vs per-note due time (`revisit_at`) | `revisit_at` has no writer — the per-note axis does not exist in production. The only working trigger is the passed anchor, shared |
| **different read costs** | curation cheap projection (SQL-only) vs revisitor model call per note | merge detection stays SQL-only; the retirement half the revisitor performs IS a model call per note. Merging does not force model calls on every sweep — selection stays SQL, judgement stays per-note (see §5) |
| **different loop-breakers** | cursor (watermark advances, work is consumed) vs strikes (work never consumed) | both breakers still differ, but strikes is dead code with no writer. The merged adaptor carries both modes — a breaker switch, not a second adaptor (§5.4) |
| **the reaching-a-person half** | the revisitor would schedule reminders when a note's moment came (D21's enqueue-not-deliver resolution) | **deferred to the proactive adapter** ([33](33-note-dreamer-schedule.md) §8). The half that justified the adaptor's existence is no longer this tier's job. What remains — retirement — is the curator's work |

**The honest summary:** the split existed to serve a reaching-a-person
revisitor and a per-note clock. Both are gone (deferred, or unwired). What
remains is one job — notebook housekeeping — done twice.

## 4. What the merged adaptor looks like

**One adaptor, `note-curation`, with the revisitor's logic folded in.**

```
note-candidates   — create (unchanged, [33] §4)
note-curation     — merge + retire, now with due-selection + evidence grounding
```

### 4.1 Selection — three SQL-only reads, zero tokens until a model is needed

| read | selects | source |
|---|---|---|
| `merge_groups` | colliding anchors | curator's existing |
| `anchored_before(day=today)` | expired anchors | curator's existing |
| `due` (new) | `revisit_at <= now` OR anchor passed **but not yet retired** | revisitor's `_is_due`, now that `revisit_at` has a writer (§5.3) |

Union, de-duplicated (a note due by both anchor and `revisit_at` renders
once, exactly as a merge-group member is excluded from expired today).

### 4.2 Judgement — one model call per selected note

The merged prompt shows each note's body + evidence window and asks for the
unified verdict vocabulary:

- **`closed`** — the closing condition is met (citation required, must be in
  the evidence shown). Covers the revisitor's `retired-closed` and the
  curator's `closed`.
- **`expired` / `moot`** — the moment passed and nothing answered. Requires
  the `_covers` evidence ground: either the window is untruncated or its
  oldest row predates the note. `moot` remains the stronger form (nothing
  will ever settle it); both retire.
- **`open`** — ordinary, writes nothing.

### 4.3 What moves across, line by line

| revisitor mechanism | disposition in the merge |
|---|---|
| `_is_due` second trigger (`revisit_at`) | kept — as the new `due` read (§5.3) |
| `_covers` evidence grounding (line 989) | **moves with the logic** — the single most important thing not to lose. Silence is not an answer; a retired-expired note must have evidence that the room was still talking past the moment |
| `retired-expired` state | kept, with its evidence requirement |
| `retired-closed` citation test | kept — the model must name a message it was shown |
| ordinal handles (opaque, screen-safe) | kept — both adaptors already use them; dedupe the mapping |
| `strikes` / `last_wake_revision_id` / `record_wake` | kept as the dateless-aging mode (§5.4) — now with the writers it never had |
| `_anchor_bounds_ms` UTC-vs-HKT recording | kept, recorded not fixed (fails conservative) |
| per-adaptor metrics | consolidated — see §5.5 |

### 4.4 Tag-path boundaries are unchanged

The merged adaptor is still a Dreamer adaptor: it proposes, code disposes.
The tag path's read-before-write latch and the R-55 guard are untouched. The
merged adaptor still cannot speak — delivery verbs remain out of scope
([33](33-note-dreamer-schedule.md) §4.1).

## 5. The details that decide whether this is safe

### 5.1 Early close must not become a full-scan model-call sweep

The reason the revisitor existed as a clock rather than a sweep is cost: a
daily model call per note is fine when a note is DUE; it is not fine if every
sweep asks the model about every note. The merged selection preserves this:
`due` is a SQL READ (`revisit_at <= now`, `strikes < K`), and the model is
only asked about notes the SQL selected. An early-close note — answered
3 August, anchored 9 September — is caught because the closing condition
fires the model when `revisit_at` (set by the writer that records the
revision) comes due, not because the sweep scans all anchors. **The cheap
selection is what keeps the merge honest; without it, merging is how a daily
sweep turns into a full-notebook model call.**

### 5.2 The evidence requirement is the hard part, and it must not degrade

The `_covers` rule exists because a truncated window converts «answered
outside the window» into a retirement (27 §11.3.3, reproduced against real
code before the fix). In the merge this is doubly load-bearing: the curator
already has `max_expired_per_tick` as a cap, and if the evidence rule is lost
in the merge, a busy room's window truncation silently retires notes whose
answers just fell outside view. **The merged `expired` acceptance test is:
«the note has evidence the room was still talking past the anchor» — not
«the date passed».**

### 5.3 `revisit_at` gets its writer — and the policy it never had

The merge makes the per-note clock real, which it has never been:
`record_wake` (27 §11.3.5, built 2026-08-28) exists on both stores but has no
caller. The merged adaptor calls it — a note whose head moved on the tag
path resets strikes and gets the next `revisit_at`; a note whose head did not
move takes a strike and no new time; past K strikes it stops waking and
becomes eligible for dateless retirement. **This converts the revisitor's
dead branches into the curator's live modes — which is the point of the
merge: two sets of dead machinery become one working clock.**

**Kimi review finding M2 — the writer needs a POLICY, and the create path
changes the progress test.**

1. **Who calls `record_wake`, on which notes, with what `next_revisit_at`?**
   34 as written names the mechanism but not the schedule. The policy that
   makes §5.1's cheap-selection invariant hold: the merged curator's `due`
   SQL read selects notes where `revisit_at <= now` (plus anchor-passed),
   and `record_wake` is called ONLY on selected, judged notes — with
   `next_revisit_at` chosen by the adaptor as *anchor day minus a lead
   window* for anchored notes (so a note answered early is caught at the
   next wake before its anchor), and a bounded horizon (e.g. +7d, capped by
   the strike ceiling) for dateless ones. Without this policy, "wake every
   standing note periodically" becomes the full-scan model call §5.1
   explicitly refuses.
2. **Gate-created notes never show tag-path progress.** `record_wake`'s
   progress test (`tag_path_progressed_since_wake`) counts only revisions
   authored on the tag path. A note created by 33's deterministic gate is
   background-authored (`established_by` is a run id), so on its first wake
   the chain holds no tag-path revision — `progressed = False`, a strike is
   charged, and `revisit_at` is never set. **The wake clock and the create
   path do not compose as written** for the very notes the tier is gaining.
   Three resolutions. (a) **REJECTED — Kimi review finding N3: it is a
   no-op.** Seeding `last_wake_revision_id = head` at creation changes
   nothing under current semantics: with `NULL` the whole chain is walked
   (`domain/notes.py:640`) and a gate-created chain contains no `tag:`
   revision either way, so the first wake is a strike with `revisit_at`
   unset in both cases (`notes.py:584-590`). Conversely, the worry that
   seeding would break strike semantics for a tag-path-touched note is
   unfounded — the span walk still finds the intervening `tag:` revision.
   (b) **a predicate change** treating the establishing revision as
   first-wake progress — viable but touches the domain predicate that
   §9.6's trap makes load-bearing. (c) **RECOMMENDED — creation writes an
   explicit `revisit_at`.** The gate, when it creates a note, also writes
   `revisit_at` derived from the note's anchor (or a bounded default
   horizon for dateless notes), so a gate-created note's first wake is a
   normal scheduled wake, not a strike bootstrap. This is the smallest
   correct change and it composes with `record_wake`'s existing
   re-arm-on-progress semantics. Must be decided before implementation,
   but (c) is now the recommendation; (a) is recorded as rejected.

### 5.4 Two loop-breaker modes, one switch

The merged adaptor carries both:
- **selection mode** (the curator's existing): SQL-only reads each tick
  (`merge_groups`, `anchored_before`) — a cap, not a watermark. The curator
  is `uses_watermark = False` (the module notes "retirement is TIME-driven,
  not evidence-driven"), so nothing is consumed by being handled; declined
  work re-fires next tick (Kimi review finding M8 — 34's earlier wording
  mischaracterised this as a cursor).
- **strike mode** (the revisitor's, now wired): a note that never changes is
  due forever; strikes bound the waking; eligibility for dateless retirement
  falls out.

These are not two schedulers — they are the breaker behaviour selected by
whether the note has an anchor. The adaptor's `inputs` picks the mode per
note; the runtime sees one adaptor.

### 5.5 Metrics consolidate, with the same honesty rule

| current series | merge disposition |
|---|---|
| `nora_note_curator_total{action=...}` (actual vocabulary: `merge`, `closed`, `moot`, `merge_refused`, `merge_failed`, `retire_failed`, `retire_stale` — `note_curator.py:79-90`; there is NO literal `action="retire"`, retirement counts as `closed`/`moot`) | kept — the merged adaptor keeps this name and vocabulary |
| `nora_note_curator_groups_refused_total` | kept |
| `nora_note_revisit_*` (standing, candidates, verdicts, refused, deferred) | renamed/merged under `nora_note_curator_*` — the series is the adaptor, and the adaptor is now one. A stale `nora_note_revisit_*` series left publishing would double-count the retirement readings the merge is meant to unify |
| `nora_note_retirements_total{because,result}` | **kept under the same name** — this is the actual retirement series, emitted by the revisitor (`note_revisitor.py:932/950`); the consolidation must carry it into the curator unchanged, not fold it into a renamed series. Kimi review finding: 34's earlier table missed it entirely |

Exact series names, verified (`nora/obs/metric_specs.py:1591-1659`; the
`nora_note_revisit_standing` gauge lives at `:1998`, outside the count range):
the revisitor emits `nora_note_revisit_candidates_total`, `…_verdicts_total`,
`…_refused_total`, `…_deferred_total`, `nora_note_revisit_standing`, and —
the retirement reading the merge cares about most —
`nora_note_retirements_total`. The `strikes_total`/`revisit_total` names
from the note-tier spec §6 remain unbuilt — metric_specs.py mentions them
in a comment as measuring adaptors that do not exist (cosmetic: not declared
specs).

The merge is a metric-name change plus a docstring update; the vocabularies
already enforce the label sets (`_VOCABULARIES`, `note_curator.py:300`).

### 5.6 What is deliberately NOT merged

- **`note-candidates` stays separate.** It faces the transcript, proposes
  creates, and is tool-free (33 §4). Folding create-proposals into the
  curator would make the curator both face the world and face the notebook —
  the fact tier's *(deprecated 2026-09-07, code deleted — issue #115)* Phase A/B cost split, proven in `fact_adaptor.py`'s
  module docstring: *"a research loop per message is unbounded cost on a
  background clock nobody is watching."*
- **The proactive adapter stays separate** (33 §8). Merging the revisitor
  into the curator does NOT move the reaching-a-person half here. The merged
  adaptor still never initiates contact; it consolidates and retires.

## 6. Why this matches the operator's framing

The operator's three-way framing, answered by the merge:

1. **Note Candidate** — outside data (transcript, standing, facts) examined
   broadly to see what belongs in the notebook. Unchanged, still separate.
2. **Note Curation** — the already-created notes, looked at across the
   notebook to merge / suppress / sunset / grow. **This is where the merge
   lands: selection, judgement and retirement are one job.**
3. **Revisit** — *"個 Revisit 其實有咩用？"* — its uses were (a) early close,
   (b) dateless aging, (c) evidence-grounding. All three survive inside the
   merged curator; none needed a second adaptor to exist, and two of the
   three were dead in production anyway.

## Status / Checkpoint

2026-09-03 — written after the operator's review: *"Curator 係可以同 Revisit
merge 嘅…大部分都重疊"*. This node records the merge as a design decision,
with the evidence grounding (§5.2) and the cheap-selection invariant (§5.1)
as the two non-negotiables. Implementation touches: `note_curator.py` gains
the `due` read and the `_covers`/strike logic; `note_revisitor.py` logic
moves across; metrics merge; objective `note-curation` gains the revised
vocabulary; `note-revisit` is retired as an adaptor (its objective file and
composition entry removed). The 33 schedule's per-adaptor cadence applies to
the two remaining adaptors: `note-candidates` hourly, `note-curation` daily.

**2026-09-03 (Kimi external audit, independent model). Verdict: REQUEST
CHANGES — no BLOCKING.** The reviewer re-verified the load-bearing code
claims (the "no writer" claim is triple-documented; `_covers` and its
war story match the code; the cheap-selection invariant is achievable —
empty `inputs()` suppresses the model call). Five MAJOR findings, all
addressed in place:

- **M1 (33 §4.1)** — the read-only awareness grant is constrained by
  `tool_subset_faults`: unit-keyed adaptors can only hold
  `UNIT_SAFE_TOOLS`; `list_reminders`/`list_events` are room-scoped and
  need a supplied-key path before they are grantable. Resolved: recorded as
  an implementation decision under the constraint.
- **M2 (§5.3, the load-bearing one)** — `record_wake` needs a POLICY (who
  calls it, on which notes, with what `next_revisit_at`), and its progress
  test counts only tag-path revisions, so gate-created notes never show
  progress and would be striked on first wake. Resolved: §5.3 now specifies
  the policy and three candidate resolutions for the gate-created case.
- **M3 (33 §1)** — watermark-free adaptors have NO fire condition (run every
  tick, `runtime.py:470`); 33's "inherits the shared 30-minute quiet" was a
  factual error. Fixed in 33.
- **M4 (§5.5)** — the revisitor's metric series are `nora_note_revisit_*`
  plus `nora_note_retirements_total` (the table missed the retirement series
  entirely, and `strikes`/`wakes` series do not exist). Fixed.
- **M5 (33 §4/C3)** — `note_candidate` migration: `probe`/`first_seen_at`/
  `last_seen_at` are `nullable=False` with no defaults; the create-key
  (`unit, title`) and the miss-key (`unit, probe`) are two domains
  with separate upsert behaviour. Fixed in 33.
- **M6 (33 §6)** — hourly-vs-daily is NOT a phase offset: periods coincide
  every 24h and `dream_loop` dispatches concurrently. The rule is an
  explicit selection guard. Fixed in 33.
- **M8 (§5.4)** — "cursor mode" mischaracterised the curator (it is
  `uses_watermark = False`; declined work re-fires). Fixed.

Also recorded: **M7/INFO** — merge is INERT in production (`note_entity` has
no writer, so `merge_groups` refuses every group); 34 §2.1 presented merge as
live machinery. This does not change the merge argument — it strengthens it —
but the docs should say so; and the architecture test pinning `record_wake`
as caller-less (`tests/architecture/test_store_methods_have_production_callers.py:221`)
must be updated when the merge wires it (M12/INFO).

**2026-09-03 (Kimi audit #2, after folding the first round). Verdict:
REQUEST CHANGES — the first-round fixes are all verified FIXED and correct
on current master; one rebase-era MAJOR plus minors.** Findings folded in:

- **N1 (MAJOR, 33 §5)** — `AdaptorSchedule` is watermark-bound: the
  schedule check sits inside the watermark branch of `_dream_adaptor_once`
  (`runtime.py:498-503`) and watermark-free adaptors return at
  `runtime.py:470` first. Only `note-candidates` can take a schedule.
  **Resolved 2026-09-03 by the operator's time-vs-data framing: the
  curator is a TRUE cron job** (anchor dates pass whether or not the room
  speaks; `note_curator.py` itself says "retirement is TIME-driven, not
  evidence-driven"), so the data-gated `AdaptorSchedule` is the wrong
  shape for it — the recommended fix is (a) a non-watermark schedule path
  (`now - last_fired >= interval`, checked before the watermark early
  return), with (b) every-tick + empty-`inputs()` suppression as the
  fallback while (a) is unbuilt. Both recorded in 33 §5.
- **N2 (MINOR, 33 §5)** — there is no `[dreamer.schedules.*]` TOML surface;
  `DreamerConfig` has `extra="forbid"` and no schedules key. The mechanism
  is the hardcoded dict at `composition.py:2415-2421`. TOML block removed.
- **N3 (MINOR, §5.3)** — recommendation (a) for gate-created notes was a
  no-op: seeding `last_wake_revision_id = head` changes nothing, because
  the walk (`domain/notes.py:640`) finds no `tag:` revision either way.
  Rejected and replaced with (c) — creation writes an explicit
  `revisit_at`; (b) predicate change recorded as viable.
- **N4 (INFO, 33+34)** — stale line refs post-rebase (`runtime.py:530`→544,
  `schema.py:1821`→1839, `composition.py:2457/2489`→2487/2522,
  `metric_specs` range, `nora_note_curator_total{action}` vocabulary has
  no literal `retire` — retirement counts as `closed`/`moot`). Fixed where
  they mattered.
