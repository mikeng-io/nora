# Standing / `debrief` — implementation plan

**Spec:** `docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md`
**Date:** 2026-08-21
**Status:** plan. No code yet.
**Priority:** ahead of the Dreamer runtime (Phases 2–3 of the substrate plan) — operator's call, 2026-08-21.

## Why this is first

The bug that started this work: a conclusion outlives its evidence, so it is
cheap to overturn. Everything shipped on 2026-08-20 — R-53, the SOURCE_SCOPE
eval axis, the provenance columns, the instruction substrate — is either the
measurement layer for that or the substrate it runs on. **None of it closes the
gap.** This does.

## Phase 0 — the instrument check — **DONE, PASS**

Recorded here because the result inverts the spec's own §1.5 and the plan below
depends on it.

`agent.messages` **retains** `reasoningContent`. `strands/models/openai.py:386`
`_format_regular_messages` builds a new list and `continue`s past the block when
assembling the request; it never mutates `messages`. Live confirmation, not
source-reading alone: the warning at `:401` has fired **844 times** in the
deployed log, and it cannot fire unless the block is present.

**So the reasoning is stripped from what is SENT, not from what is STORED.** The
844 warnings are the positive evidence. Had this come back the other way, §6.1's
input schema would have been unimplementable and the design would need a
different source for reasoning — which is exactly why it is checked before
anything is built.

## Sequencing constraint that drives everything

**§14.6: raw `TagTrace` retention and the re-derivation path must exist BEFORE
the first debrief ships, or early folds are unrecoverable.** `Standing` is lossy
by design (§6.9) — that is the mechanism, not a defect — so without retention a
single bad fold destroys history with no way to notice or rebuild.

Retention is therefore Phase 1, before the stage that produces anything.

## Phase 1 — `TagTrace` extraction and retention

Nothing folds yet. This phase only captures and stores.

| # | task | notes |
|---|---|---|
| 1.1 | `TagTrace` model | §6.1. `messages` whole, never semantically filtered. `prior` is NOT a field. |
| 1.2 | extraction at the `nora/agent/` boundary | `tests/test_boundaries.py::test_strands_import_boundary` allows `{"agent","compute"}`. Returns no SDK types. |
| 1.3 | slice offset | record `len(agent.messages)` at construction, **not** `len(pre_fill)` — `agent.py:1281-1282` mutates the caller's dicts in place and `BeforeInvocationEvent` can replace `messages`. |
| 1.4 | sanitisation | three synthetic artefacts must not fold as Nora's words: the guard veto string (`guards.py:946-958`), `"Tool execution cancelled"` (`event_loop.py:754-772`), `_recover_message_on_max_tokens_reached` placeholders. |
| 1.5 | caps + counters | §6.5. Truncate assistant narration, **pin human turns** — intent cannot be reconstructed from the work that followed. Every truncation counted, never silent. |
| 1.6 | retention store + migration | the thing §14.6 requires. Next migration after `0025`. |
| 1.7 | re-derivation path | rebuild the position from retained turns, never fold-onto-fold. |

**Defensive throughout** (`agent_runner.py:117-142`'s reason): a renamed SDK
field degrades to an empty trace, never raises into a turn that otherwise
succeeded.

**Gate:** a turn's reasoning is demonstrably captured and stored, with a
red-on-revert test that deletes the reasoning blocks and fails.

## Phase 2 — the `debrief` stage and `Standing`

| # | task | notes |
|---|---|---|
| 2.1 | `debrief.run` skeleton + own `asyncio.wait_for` | §7.2. `tag_timeout + debrief_timeout` must fit the max acceptable slot hold; `stages/invoke.py:121-129` is the model. |
| 2.2 | `completed = True` **before** the await | §7.3. Otherwise an escape after successful delivery yields `"pipeline_error"` → false `nora_partial_replies_total` on a turn that sent a complete reply. |
| 2.3 | catch `BaseException`, never re-raise | `CancelledError` is not an `Exception`; `except Exception` leaves the cancel path open and trips `nora_silent_failures_total{reason="handler_error"}`. |
| 2.4 | runs on **every** completed turn | §7.4 — do NOT inherit `feedback.run`'s `if sent and ctx.tagger is not None` gate. R-5 silence is deliberate and produces just as much reasoning. `outcome` carries which kind. |
| 2.5 | `Standing` model + store, update-in-place | §6.3. One row per conversation. |
| 2.6 | the sliding window | §6.2 — `ctx.transcript`, already assembled and rehydrated. NOT a watermark slice: `RecordStore.since` excludes `is_nora` rows in SQL and `tag_id` has no ordering relation to `messages`. |
| 2.7 | `<standing>` injection | §6.7. `escape()`d, `untrusted="true"`, **volatile tail only** — never `_context_header`'s cached prefix. |
| 2.8 | latency separation | §7.5. Observed in the `finally` **after** the debrief; must be excluded from `nora_tag_latency_seconds` or §14.3's SLO gate measures against a series this change broke. |
| 2.9 | overflow as a model-resolvable error | §6.6. Refuse the write, return the inventory, "consolidate now". Silent eviction is the failure mode. |

**Gate:** §14.3 — debrief latency on `nora_debrief_seconds`, with the
**second-queued-tag** case explicit, against p95 ≤ 5s
(`design/05-evaluation.md:79`). The user-visible cost lands on the second person
queued behind a busy conversation, not the first.

## Phase 3 — `TurnVerdict`

One row per turn, never rewritten (§6.3). Split from `Standing` because the
first draft put per-turn fields on the per-conversation row, so §16.4's
re-derivation trigger would have read a flag already overwritten by the next
turn.

Also resolve **§8.3**: the narrowed Honcho gate — withholding Nora's *own*
ungrounded assertions from her `self_card` — justified on its own merits or
dropped. It is **not** the motivating bug; §1.1 is not a Honcho-write bug. If
kept, `fire.py::_feed` (`:922-950`) is a second caller the gate must reach.

## Phase 4 — proposing facts (depends on `fact-tier` merging)

The debrief's append-only half. **Blocked on `fact-tier`** (26 commits, in final
review as of 2026-08-21).

Three corrections to §6.3 that a plan reading the spec would otherwise get
wrong — the spec is now annotated, but restated here because this is where it
bites:

- **A fact is a triple, not a sentence.** `subject_id` / `predicate` /
  XOR(`object_entity_id`, `object_literal`) against a canonical `fact_entity`.
  There is no `statement: str`. What forecloses a graph later is prose-vs-triple.
- **Facts key on `workspace`**, not `conversation_id` (DEC-105). §16.2 is closed
  in favour of the wider scope.
- **The sink is the substrate's `DreamSink`** — `propose` / `supersede`, and
  **no delete verb on the interface at all**, so §5.2's "invalidate, never
  delete" is enforced by the port's shape rather than by either side remembering.

**Newly load-bearing, and it must be stated in this phase rather than
inherited:** §9's argument that the debrief must never adjudicate contradictions
— because it runs in-slot, seconds after the exchange, under the same social
pressure that produced both retractions — is now the **only** thing standing
between a bad in-slot fold and the fact tier. The store has no delete verb, but
`propose` still appends anything. A debrief that adjudicates would write the
capitulation in as durable and make the error permanent.

## Decisions needed from the operator

Not blocking Phase 1. Each changes what gets built, so none should be guessed.

| # | question | spec | my recommendation |
|---|---|---|---|
| D1 | Do the four non-tag slot holders (`heartbeat`, `announce`, `compute_poller`, lost-jobs) debrief? | §7.6, §14.9 | **Yes, except `announce`** — it defers to the next boot (`:161-170`), so a longer hold is not a free retry there. These turns reason and assert; excluding them silently loses that. |
| D2 | Re-derivation trigger — fold count, elapsed time, or forced when `grounded` is false? | §16.4 | **Forced on `grounded=false`, plus a fold-count ceiling.** Time alone re-derives healthy conversations for nothing. |
| D3 | Debrief window size — same 40 rows as `context_window`, or its own? | §16.7 | **Its own, smaller.** The debrief revises a position; it does not need the full reply context. |
| D4 | `Standing` key — `conversation_id`, or `(platform, conversation_id)` like every lock/queue/slot? | §16.9 | **`(platform, conversation_id)`** — `slots.py:36` and the `messages` platform column both already key that way. |
| D5 | Does `<standing>` render on the fire path (reminder/announce/relay)? | §16.10 | **Yes** — `build_prefill` is shared and parameterised only by `header=`; suppressing it there is the special case needing justification. |
| D6 | Corroborate `grounded` with `handle.tool_call_log`, which knows deterministically which tools ran? | §16.6 | **Yes** — a deterministic corroborator beside a model verdict is exactly the repo's fail-closed shape. |

## Carried risk, stated not solved

**§16.5 — injection persistence.** Every other untrusted block is rebuilt from
the store each turn, so an injection lasts one turn. `Standing` is
update-in-place, so a successful injection **persists** until a later fold drops
it — and §6.9 markets that persistence as the feature. Escaping is necessary and
not obviously sufficient. This does not block Phase 1 or 2, but it must not be
forgotten at Phase 2.7.

**§17's blocking context** — `tool_choice=required` saturation is a separate
unfixed reliability failure. Everything here assumes replies keep arriving via
DEC-93's recovery path.

## Not in this plan

`FactSynthesizer` as a Dreamer adaptor (§5.3, §8.2) belongs to the **Dreamer
runtime**, handed off at `2026-08-21-dreamer-runtime-handoff.md`. The debrief is
a pipeline stage on the tag path and is **not** an adaptor (§8.2).

## Numbering

Next free is **DEC-130**. `R-54` belongs to the fact tier. Candidate rule for
`tests/test_acceptance_rules.py::RULE_TESTS`: *a `Standing` is never asserted as
fact; it is situation-tier and contradictable.*
