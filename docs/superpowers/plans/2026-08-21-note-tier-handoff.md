---
kind: plan
title: "Note tier — handoff: what is live, what is left, and what will bite"
description: "The note tier's tag-path half shipped and is enabled in production on 2026-08-21. Two pieces remain and they have DIFFERENT dependencies: the Dreamer curator (merge duplicates, retire expired, promote to events) is unblocked now that the Dreamer runtime landed at 27b72af, and the debrief (notice an open question AFTER the reply) never depended on the runtime at all — it is a tag-path stage with its own plan already written. This is the context a fresh session needs that the code and git log do not carry."
tags: [nora, notes, handoff, dreamer, debrief]
timestamp: '2026-08-21'
status: active
derives_from:
  - docs/superpowers/specs/2026-08-21-note-tier-design.md
  - docs/superpowers/plans/2026-08-21-standing-debrief-plan.md
  - docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md
---

# Note tier — handoff

## 1. What is live right now

**Enabled in production**, `[notes] enabled = true`, verified in the running
container on 2026-08-21: all four tools in her prompt, migration `0026` applied,
`note` / `note_revision` / `note_entity` / `note_candidate` present in the live
database, container healthy.

| piece | where |
|---|---|
| domain types | `nora/domain/notes.py` — `Note`, `NoteRevision`, `StaleNoteHeadError` |
| tables | migration `0026_note_tier`, `nora/infra/sql/schema.py` |
| store | `nora/infra/sql/notes.py`, `nora/testing/note_store.py`, `tests/contract/test_note_store_contract.py` |
| prompt block | `nora/language/note_render.py`, `_notes_block` in `nora/agent/prefill.py` |
| tools | `note_read` / `note_search` / `note_create` / `note_update` in `nora/agent/tools.py` |
| the latch | `ReplyGuard` in `nora/agent/guards.py`, R-55 |
| wiring | `NotesConfig` in `nora/config.py`, `nora/app/composition.py`, `nora/engagement/context.py` |

**How a note happens today:** the `<notes>` index (title + one-line description
per standing note, workspace-scoped) is injected on every tag. She decides
**mid-turn** whether to call a note tool. `send_reply` is `terminal=True`, so
**a note must be written BEFORE the reply** — there is no after-the-reply path
yet. That is §2 below.

## 2. What is left — two pieces, different dependencies

### 2.1 The Dreamer curator + revisitor — UNBLOCKED

Spec: note-tier design §9 (all of it), §7.1, §7.4, §10, §15.

The Dreamer runtime landed at **`27b72af`**: `nora/agent/dreamer.py`,
`nora/agent/dream_guard.py`, and the `DreamAdaptor` port in
`nora/domain/ports.py`. That port is the note tier's shape exactly — its own
docstring says it is `RoomConsolidator` generalised for adaptors that
**invalidate as well as append** and that are keyed on a **workspace** rather
than a scope.

What to build: `NoteCurator` (merge duplicates, retire expired, promote a
settled note to an `Event`, compact history) and `NoteRevisitor` (research one
note, self-scheduled).

**Read §9 before writing any of it.** It specifies the trigger conditions, what
each pass may read, what decides each action, and the loop breakers, in the live
loop's own terms. Four things in it are load-bearing and non-obvious:

1. **Merge is keyed on the ANCHOR, never on topical similarity** (§7.1). Two
   weddings are two notes because their anchors differ. A similarity-based
   consolidator merges them and the venue-to-groom mapping is gone. Candidate
   detection is a `GROUP BY … HAVING count(*) > 1` — zero tokens.
2. **The empty-pass watermark refresh** (§9.4). Copy `_dream_once`'s `else:`
   branch; do not re-derive it. Without it the age condition is a latch with no
   reset and the pass reruns every tick forever. That bug has already shipped
   once in this repo (`runtime.py`, labelled "PLAN DEFECT found and fixed").
3. **The strike reset must be "head moved BY THE TAG PATH"** (§9.6), not "head
   moved". If the revisitor's own writes reset its strike, the breaker resets
   itself forever and is decorative. `NoteRevision.established_by` exists for
   exactly this.
4. **`note_create` / `note_update` are NOT in the revisitor's tool subset**
   (§10, corrected). The model proposes, code disposes: writes leave as a
   schema-validated `Proposal` through `DreamSink`. The runtime enforces this —
   `NativeTool.read_only` derives the background ceiling and an adaptor naming a
   write verb is refused at build time.

Also in scope when this lands: `tests/contract/test_dream_sink_contract.py` —
the note store is the **second** `DreamSink` implementation, so joining it is
this tier's job. It is an `IMPLEMENTATIONS` entry plus per-implementation
`make_proposal` and `read_row` callables. Read the file rather than the spec's
description of it; it changed shape after a third implementation joined and
measured 4 passed / 9 failed against the old "joining is one entry" claim.

### 2.2 The debrief — "notice after the reply" — NEVER blocked by the runtime

Spec: `docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md`
Plan: `docs/superpowers/plans/2026-08-21-standing-debrief-plan.md` (already written)

**This does not depend on the Dreamer runtime and never did.** It is a
**tag-path** stage that runs in-slot after delivery. It has been buildable the
whole time; it is simply bigger: `TagTrace` extraction at the `nora/agent/`
boundary, a retention store and migration, the `Standing` artifact, the debrief
stage itself — and the note proposal is one output hooked onto the end.

It delivers more than notes (`Standing`, `TurnVerdict`, and the fact tier's
propose side), so it deserves its own session rather than being folded into the
curator's.

**Its Phase 0 instrument check is already DONE and PASSED** — `agent.messages`
retains `reasoningContent`; the stripping happens at the transport, not the
store. Do not redo it; the plan records the evidence.

## 3. Open questions the specs leave to whoever builds next

- **`Event` promotion's wiring** (spec §15.5 item 2). `DreamSink` writes into
  the tier owning `key`; an `Event` is conversation-scoped and a note is
  workspace-scoped. Promotion is probably not a `propose` and the curator
  probably holds a second collaborator — decide it explicitly rather than
  discover it.
- **`closing_condition` has no length cap**, which is why it is not rendered in
  the index (§16.3). Cap it on the domain type and rendering becomes a two-line
  change.
- **A shared rule three tiers have now independently needed**: *a store that
  cannot record something refuses loudly; it never accepts and forgets*
  (`StaleNoteHeadError`, the fact candidate store's `ValueError`,
  `NullableAxis.REFUSED`). It is behavioural, so it wants a
  `design/09-decision-index.md` entry and a named test in `RULE_TESTS`. Not
  landed; three sessions agreeing is an argument, not authority.

## 4. What will bite you — earned the hard way on 2026-08-21

- **`docker build` reads the WORKING TREE, not HEAD.** Building from
  `~/workspace/nora` while a peer was mid-merge shipped their conflict markers
  and crash-looped production for ten minutes. Always
  `git worktree add --detach /tmp/wt <sha>` and build from there. Then check
  `config.toml` — it is bind-mounted from the main checkout and is **not**
  pinned by the image, so image and config must come from the same commit.
- **A contract suite must scope its database per MODULE.** The shared `pg_url`
  fixture is function-scoped: CREATE DATABASE + the full migration chain + DROP,
  every test. At 31 tests that degraded the container enough to fail an
  unrelated wall-clock test in `tests/integration/` — the cost landed nowhere
  near its cause and took a morning and five wrong hypotheses to find. See
  `tests/contract/test_note_store_contract.py::_notes_db` for the shape.
- **`make test-affected` is currently degraded** for any branch whose cumulative
  diff touches `nora/doctrine/**` or `nora/dreamer/**` — Rule 0 in
  `scripts/affected_tests.py` forces ALL on the whole-branch diff, not the
  change. Found by another session; unfixed at time of writing.
- **Migration parents collide silently.** Two branches cut from one head both
  write the same `down_revision`, git merges clean, tests stay green, the
  container fails at boot. `uv run alembic heads` must print exactly one line
  after every merge.
- **DEC/R numbers are claimed in the index, not in chat.** Two sessions
  coordinated carefully and a third took DEC-131 anyway. Re-read the file at
  merge time.

## 5. Where the reasoning lives

`docs/superpowers/specs/2026-08-21-note-tier-design.md` is the design and it is
`accepted`. §12 records the operator corrections that shaped it; §16 records the
four things implementation falsified. Read §16 before trusting any earlier
section — three of its four items overturn something stated confidently above
them.
