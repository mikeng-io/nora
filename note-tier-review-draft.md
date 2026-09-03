# Note Tier — Current State, Problems, Changes, and Target Effect

**Review draft — 2026-09-03.** For Miles's review before implementation scope is locked.
Design detail lives in `design/dreamer-agent-loop/33-note-dreamer-schedule.md` (committed on `feat/note-dreamer-schedule`).

---

## 1. Where the note tier is today (verified)

| Layer | State |
|---|---|
| **Spec** | `2026-08-21-note-tier-design.md` (accepted) — a note is `(anchor, uncertainty, closing condition, body)`, workspace-scoped, index injected on every tag |
| **Tools** | `note_create` / `note_update` / `note_read` / `note_search` exist on the tag path, gated by R-55 (read-before-write latch) |
| **Cold start** | Measured 2026-08-27: **one** note-tool call in the tier's life (a `note_search` miss). Zero creates, zero updates, ever. The create path is not broken — it has never been called |
| **Curator** | Built 2026-08-28 — merge + retire only. "You are NOT writing anything." |
| **Revisitor** | Built 2026-08-28 — retirement half only. Reaches a person only via `schedule_reminder` |
| **Candidate table** | `note_candidate` is a **miss cache** (`(workspace, probe)`, occurrence count) — the wrong shape for a create queue |
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
- Scope-keyed, watermarked, tool-free — reads a room's message batch **plus the workspace's note index**.
- Proposes a **full note** (title, description, body, anchor, closing condition, message_ids).
- **Code decides** create vs update vs nothing (dedupe against the standing index; update only if the proposal adds something).
- The tag path stays as-is (R-55 latch, never creates). The cold start proved it will not.

### C3 — `note_candidate` table gains a create shape
- Add `kind` (`miss` | `create`); existing rows are all `miss`, no backfill.
- Create candidates carry the full proposal + attribution + status (`pending`/`created`/`refused`) + attempts.
- Miss cache keeps its `(workspace, probe)` key; create candidates key on `(workspace, title)`.

### C4 — Per-adaptor schedule (following the fact tier's cadence change)
| Adaptor | Cadence | Why |
|---|---|---|
| `note-candidates` | hourly (3600s) | a week of planning, not 5-minute slices |
| `note-curation` | daily (86400s) | housekeeping, quality over latency |
| `note-revisit` | hourly floor | already self-scheduled by `revisit_at` |

Budgets follow 32's enlarged pattern (more turns, more tokens, longer timeout).

### C5 — Phase offset, not a DAG
- Verified: the note adaptors share **no execution dependency** (curator/revisitor read notes, never candidates). Ordering is emergent at the data level — a pass with no input does not fire.
- The one rule: adaptors sharing a table do not run on the same tick — expressed as schedule, not as a graph.
- The fact tier's DAG question is explicitly out of scope (owned by 32 / another session).

### C6 — Discharge (sequenced after C2)
- **Close = suppress + route, not delete/move.** The note row retires (stays in store, drops out of the always-on index, `note_search` finds it).
- Settled content routes by kind: commitment → Event machinery; world-claim → knowledge tier; person-claim → Honcho.
- Unsettled remainder keeps the note alive.
- Gap recorded: no general discharge pipeline exists; it is the curator's job, sequenced after the create path.

## 4. What gets built (implementation order)

1. **Migration** — `note_candidate` shape change (kind=create columns).
2. **`note-candidates` adaptor** — `nora/language/note_candidates.py`, objective `note-candidates.md`, deterministic create/update gate.
3. **Schedule wiring** — `AdaptorSchedule` entries in composition (depends on 32's impl landing or being carried here).
4. **Doctrine recognition** — `conduct.md` line broadens.
5. **Discharge pipeline** — curator-side, after create path is live.

## 5. Target effect

- **Nora starts writing notes.** The tier's first create path that can actually fire — recognition + a background pass with the whole exchange in view.
- **The notebook holds arrangements, not just questions.** A dinner plan lives whole; its settled half has a home.
- **Cross-week conversations get digested.** Hourly watermark over each room's transcript assembles the arrangement the per-turn tag path cannot.
- **Closed notes discharge cleanly.** Settled content reaches the right tier (Event / knowledge / Honcho); the container retires as archive.
- **The Dreamer has a clear rhythm.** Per-adaptor cadence, phase-offset ordering, no flat-list stampede — and no dependency-graph machinery the tier does not need.

---

*Open question for Miles: whether to wait for `feat/fact-dreamer-schedule-impl` to land before implementing C4 (both touch `AdaptorSchedule`), or carry the schedule wiring in this branch.*
