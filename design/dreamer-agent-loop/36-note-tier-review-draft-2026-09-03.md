> **PARTLY DEPRECATED 2026-09-07 — every reference below to the knowledge tier (the "fact tier") or to the language tier describes code that has since been deleted.** Those parts are retained as a record of the reasoning, not as a description of the system; the rest of this document still stands.
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.
>
> **ALSO DEPRECATED IN PART — the note curator's MERGE half (2026-09-08) and the create gate's anchor-DATE match (2026-09-13, #228).** Every clause below that gives the curator a merge verb — C8's *"one `note-curation` adaptor carries merge + retire"* and the §1 table's *"merge + retire only"* among them — describes code that no longer exists. Nothing minted an entity once the knowledge tier went, so `note_entity` could never be populated and merge was refused by construction; the verb, the registry store and the resolution pass were deleted. Duplicates then moved to the create gate keyed on the anchor date, and THAT arm went too: it had fired twice since the tier shipped and conflated two unrelated occasions both times. The gate now asks the model which standing note a proposal updates. **Nothing merges a duplicate anywhere in this tier** — a known, recorded limitation, bounded by issue #236. *(Superseded 2026-09-25: a separate `note-merge` pass now folds duplicates, DEC-202, design 41 §7; the curator stays retire-only.)* Retirement, the evidence grounds and the two non-negotiables are unaffected. R-75 in `design/09-decision-index.md` carries the current reading.

# Note Tier — Current State, Problems, Changes, and Target Effect

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


**Review draft — 2026-09-03.** For Miles's review before implementation scope is locked.
Design detail lives in `design/dreamer-agent-loop/33-note-dreamer-schedule.md` (committed on `feat/note-dreamer-schedule`).

---

## 1. Where the note tier is today (verified)

| Layer | State |
|---|---|
| **Spec** | `2026-08-21-note-tier-design.md` (accepted) — a note is `(anchor, uncertainty, closing condition, body)`, unit-scoped, index injected on every tag |
| **Tools** | `note_create` / `note_revise` / `note_read` / `note_search` exist on the tag path, gated by R-55 (read-before-write latch) |
| **Cold start** | Measured 2026-08-27: **one** note-tool call in the tier's life (a `note_search` miss). Zero creates, zero updates, ever. The create path is not broken — it has never been called |
| **Curator** | Built 2026-08-28 — merge + retire only. "You are NOT writing anything." |
| **Revisitor** | Built 2026-08-28 — retirement half only. Reaches a person only via `schedule_reminder` |
| **Candidate table** | `note_candidate` is a **miss cache** (`(unit, probe)`, occurrence count) — the wrong shape for a create queue |
| **Scheduling** | `dream_loop` runs a flat list; note adaptors inherit the shared `[language]` fire condition (30 min of quiet) |
| **Discharge** | Event promotion exists in the spec (§7.3) but is not built. No general discharge pipeline exists |

## 2. The problems

**P1 — Positioning: the note is defined as an uncertainty container, but a notebook holds anything.**
The dinner plan (three candidates, nobody picked) is not an uncertainty — it is an **arrangement** with an unsettled corner. The settled half (who is coming, who proposed which place, who does not eat beef) has no home. The spec's discriminator ("a thing with no closing condition is not a note") limits the notebook to questions.

**P2 — No create path that can fire.**
The tag path has the tools but no recognition (empty index → no `<notes>` block → no reason to look). The curator and revisitor are read-only by design. **Nothing in the system can create a note.** A complex, cross-week arrangement cannot be assembled from per-turn tag-path updates — it needs a background pass with the whole exchange in view.

**P3 — The Dreamer runs as a flat list, not a schedule.**
No ordering, no cadence, no per-adaptor rhythm. Note work does not need 5-minute ticks — nothing is created or proposed at that frequency.

## 3. The changes

### C1 — Reframe: a note is a notebook entry, not an uncertainty
- Closing condition widens from "what resolves this uncertainty" to **"what finishes this entry's job"** (restaurant booked, commitment fulfilled, follow-up done).
- Description becomes **what this entry is + why it is not finished + what would finish it** (was: uncertainty + closing condition).
- Recognition (doctrine) broadens from 「未有結論嘅嘢」 to **"something that must survive across turns"** — undecided plans, commitments, follow-ups, arrangements.
- The discriminator survives, widened — it still stops the tier swallowing facts/memory/standing.

### C2 — A create path: the `note-candidates` adaptor (fact tier's Phase A shape)
- Scope-keyed, watermarked, tool-free — reads a room's message batch **plus the unit's note index**.
- Proposes a **full note** (title, description, body, anchor, closing condition, message_ids).
- Read-only awareness only (see C2.1) — never delivery verbs
- **Code decides** create vs update vs nothing (dedupe against the standing index; update only if the proposal adds something).
- The tag path stays as-is (R-55 latch, never creates). The cold start proved it will not.

### C2.1 — Tool surface: read-only awareness, never delivery

- Note dreamers may hold **read-only cross-tier verbs** so they know what is happening while consolidating: `list_reminders`, `list_events`, `search_knowledge` / `get_knowledge`, `search_memory_workspace`, `note_read` / `note_search`.
- **Never** delivery or write verbs: `remind`, `cancel_reminder`, `create_event`, `update_event`, `cancel_event`.
- The revisitor's `schedule_reminder` block is **not widened** — reaching a person is a separate proactive adapter's job (see C7).

### C3 — `note_candidate` table gains a create shape
- Add `kind` (`miss` | `create`); existing rows are all `miss`, no backfill.
- Create candidates carry the full proposal + attribution + status (`pending`/`created`/`refused`) + attempts.
- Miss cache keeps its `(unit, probe)` key; create candidates key on `(unit, title)`.

### C4 — Per-adaptor schedule (following the fact tier's cadence change)
| Adaptor | Cadence | Mechanism |
|---|---|---|
| `note-candidates` | hourly (3600s) | `AdaptorSchedule` — works, it is watermark-bearing |
| `note-curation` | **true daily cron** | needs a non-watermark schedule path (`now - last_fired >= interval`, checked before the watermark early return) — the curator is time-driven, not data-driven, so the data-gated `AdaptorSchedule` is the wrong shape; every-tick suppression is the fallback while unbuilt |
| (revisit — merged into curator per C8) | — | — |

**Kimi audit #2 (N1/N2):** only `note-candidates` can take an
`AdaptorSchedule` as wired today (schedule check inside watermark branch,
`runtime.py:498`); the curator is watermark-free so a daily schedule would be
inert. No `[dreamer.schedules.*]` TOML surface exists (`extra="forbid"`) —
wiring is the hardcoded dict at `composition.py:2415`. Budgets follow 32's
enlarged pattern (more turns, more tokens, longer timeout).

### C5 — Phase offset, not a DAG
- Verified: the note adaptors share **no execution dependency** (curator/revisitor read notes, never candidates). Ordering is emergent at the data level — a pass with no input does not fire.
- The one rule: adaptors sharing a table do not run on the same tick — expressed as schedule, not as a graph.
- The fact tier's DAG question is explicitly out of scope (owned by 32 / another session).

### C6 — Discharge (sequenced after C2)
- **Close = suppress + route, not delete/move.** The note row retires (stays in store, drops out of the always-on index, `note_search` finds it).
- Settled content routes by kind: commitment → Event machinery; world-claim → knowledge tier; person-claim → Honcho.
- Unsettled remainder keeps the note alive.
- Gap recorded: no general discharge pipeline exists; it is the curator's job, sequenced after the create path.

### C7 — Proactive adapter (deferred, cross-tier)

- Confirmed 2026-09-03: Nora should move from **passive to active** — initiative derived from collected memory/notes/knowledge/events, with cross-tier synthesis.
- This is **not** the note tier's job: note dreamers consolidate and close; they **never initiate contact**.
- A separate proactive adapter (built on the settled state of note/knowledge/language tiers) owns initiative: input = cross-tier summaries (`<notes>` index, standing, facts block, event/reminder state), output = proposed actions (`remind`, `cancel_reminder`, `update`, `discharge`, reach out), model-proposes / code-disposes, daily cadence.
- Deferred because it is built **on top** of this tier — nothing to be active about until the note tier has a create path, schedule and discharge.

### C8 — Revisitor merged into curator (`note-curation`)

- Verified: in production the revisitor's distinctive machinery is **dead** — `revisit_at` / `strikes` / `last_wake_revision_id` have no writer anywhere, so early close and dateless aging are unreachable. Its only working trigger (passed anchor) is the curator's selection. The reaching-a-person half that justified the split is deferred to the proactive adapter.
- **Merge**: one `note-curation` adaptor carries merge + retire, with three SQL-only selections (`merge_groups`, `anchored_before`, `due`) → one model judgement per selected note (`closed` / `expired` / `moot` / `open`) → code disposes.
- **Two non-negotiables** (full detail in 34): cheap selection must stay SQL-only or a daily sweep becomes a full-notebook model call (5.1); the `_covers` evidence ground must not degrade — silence is not an answer (5.2).
- `revisit_at` gets its writer: `record_wake` converts the dead branches into live dateless-aging modes (5.3) — **with the policy it never had** (who calls it, which notes, what next_revisit_at; gate-created notes get an explicit `revisit_at` at creation — resolution (c), after (a) was rejected as a no-op in audit #2). Metrics consolidate under `nora_note_curator_*` (5.5). `note-candidates` and the proactive adapter stay separate (5.6).
- **Kimi external audit (2026-09-03): REQUEST CHANGES, no BLOCKING.** Merge direction confirmed sound; evidence-grounding and cheap-selection invariants hold. MAJOR fixes folded in: tool grant constrained by `tool_subset_faults` (C2.1), wake policy + gate-created notes (C8), metric names `nora_note_revisit_*` + `nora_note_set_statusments_total` (not `nora_note_revisitor_*`), phase-offset corrected, `note_candidate` migration nullable constraints. Full record in 34 Status.

## 4. What gets built (implementation order)

1. **Migration** — `note_candidate` shape change (kind=create columns).
2. **`note-candidates` adaptor** — `nora/language/note_candidates.py` (deleted 2026-10-03, DEC-277), objective `note-candidates.md`, deterministic create/update gate.
3. **Revisitor → curator merge** (C8) — `note_curator.py` gains `due` read + `_covers`/strike logic; `note_revisitor.py` logic moves across; `note-revisit` adaptor retired.
4. **Schedule wiring** — `AdaptorSchedule` entries in composition (depends on 32's impl landing or being carried here).
5. **Doctrine recognition** — `conduct.md` line broadens.
6. **Discharge pipeline** — curator-side, after create path is live.

## 5. Target effect

- **Nora starts writing notes.** The tier's first create path that can actually fire — recognition + a background pass with the whole exchange in view.
- **The notebook holds arrangements, not just questions.** A dinner plan lives whole; its settled half has a home.
- **Cross-week conversations get digested.** Hourly watermark over each room's transcript assembles the arrangement the per-turn tag path cannot.
- **Closed notes discharge cleanly.** Settled content reaches the right tier (Event / knowledge / Honcho); the container retires as archive.
- **The Dreamer has a clear rhythm — and fewer of them.** Candidate + curator, instead of candidate + curator + a revisitor whose distinctive machinery was dead in production (C8).
- **The Dreamer has a clear rhythm.** Per-adaptor cadence, phase-offset ordering, no flat-list stampede — and no dependency-graph machinery the tier does not need.
- **Boundaries stay sharp.** Note dreamers read across tiers for awareness, never deliver or initiate; the proactive adapter (deferred) owns initiative separately.

---

*Open question for Miles: whether to wait for `feat/fact-dreamer-schedule-impl` to land before implementing C4 (both touch `AdaptorSchedule`), or carry the schedule wiring in this branch.*

*Second decision 2026-09-03: proactive adapter deferred (C7) — note tier trimmed to lifecycle-only; read-only cross-tier awareness for consolidation (C2.1).*
