> **PARTLY DEPRECATED 2026-09-07 — every reference below to the knowledge tier (the "fact tier") or to the language tier describes code that has since been deleted.** Those parts are retained as a record of the reasoning, not as a description of the system; the rest of this document still stands.
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.

# Standing / `debrief` — implementation plan

**Spec:** `docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md`
**Date:** 2026-08-21
**Status:** Phase 1 COMPLETE and merged. **Phase 2 COMPLETE** — see "Phase 2 status" below. Phases 3-4 are still plan-only.
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

## Phase 1 status (2026-08-22): COMPLETE

All 7 tasks done, reviewed clean, `make gate` green (4309 passed), single
migration head (`0028_tag_trace`). Branch `standing-debrief`, worktree
`/tmp/wt-standing-debrief`, head `c1bc2f4`. Not yet merged.

## Phase 2 blockers, found 2026-08-22 — read before writing any Phase 2 code

Two findings from live investigation (probes + a real Langfuse trace +
cross-session sync with nora-cc, who was independently debugging the
Dreamer's stuck `fact-candidates` adaptor at the same time) change how Phase
2 must be built. Neither is closed on trust — see the evidence cited.

**1. `tool_choice="required"` is not reliably enforced by the current
OmniRoute/Ollama Cloud route.** A live probe against the real doctrine and
`send_reply` schema (not a hand-rolled stand-in) got 2/6 (33%) compliance
under `"required"`, contradicting `nora/agent/factory.py`'s
`create_model_provider` docstring claim ("produces the call every time").
**Caveat, stated plainly**: that probe made a single completion call, not
the real multi-turn Strands `Agent` loop — it is not conclusive on its own,
and production's observed reliability (Nora replies essentially every turn)
is the stronger, longer-running signal. Before drawing any conclusion,
re-test properly using `evals.harness.run_case(case, live=True)`, which
already drives the REAL pipeline end to end (real doctrine, real tool
registry, real multi-turn loop) — not another single-shot probe. This
matters for Phase 2 because the debrief's own reliability will face the same
question and deserves the same properly-instrumented answer, not an assumed
one.

**2. The debrief must NOT be built as a tools-less Strands `Agent`
(`create_dream_agent`-style construction) — build it as a plain
`chat.completions.create` call instead.** Grounded in a real production
incident: the Dreamer's `fact-candidates` adaptor (`tools=frozenset()`) got
stuck in a `MaxTokensReachedException` loop on one scope
(`docs/superpowers/specs` cross-session trail; trace
`b782c5afba571f7ec47cf706eb6ebe18`, 2026-08-21, verified via the real
Langfuse API: input 3201 tokens / output 4096 tokens, mid-generation cut).
**Root cause — CORRECTED 2026-08-22, cross-session (nora-cc).** The first
version of this note got the mechanism backwards. `tools=[]` under
`tool_choice="auto"` does not prevent the Dreamer's stop condition
("the model declines to call anything") from being reached — it satisfies
it *trivially, on the first pass*, because there is nothing else the model
could do. There is no loop at all in this case: it is **one single
generation** that ran away to 4096 tokens *within itself*. Confirmed against
the actual trace pulled from Langfuse (`b782c5afba571f7ec47cf706eb6ebe18`):
exactly one `GENERATION` observation (`chat`, `ollamacloud/deepseek-v4-flash`,
input=3201/output=4096), not several — a multi-turn loop would show more
than one.

**What that means: the Strands `Agent`-vs-raw-completion choice buys NO
protection from this failure by itself.** A raw `chat.completions.create`
with the same prompt and the same output ceiling runs away identically —
and *worse*, because it fails silently (`finish_reason="length"`, a
truncated body) rather than raising. The actual causes are the ones already
found and are independent of the SDK wrapper: a research-framed doctrine on
a task with no research, no stated expected output size, and an unbounded
input.

**The real design rule, and it decides both the debrief's shape AND why
Standing needs no tool for `prior` (ties directly to blocker 3, below):**

> If the selection is knowable *before* the call, it is an input. If the
> selection depends on what the model concludes *mid-reasoning*, it must be
> a tool.

`Standing.state`, the current `TagTrace`, and the debrief's whole input set
are all knowable in advance — the debrief needs no tool, full stop, not
merely "probably doesn't need one." The fact tier's `FactCandidateAdaptor`/
`FactAdaptor` are the other case: *"what do we already call this thing?"*
isn't answerable until the model has read the batch and formed a
hypothesis — Python cannot pre-select it, which is exactly why two
contradictory `big walk` rows exist. **The one thing that would flip
Standing onto the tool side**: if a debrief should ever ask *"have I
concluded something like this before, in another conversation?"* — that is
discovery, not a known-row read, and would need nora-cc's read-tool seam.
Worth checking which side of that line any future addition falls on, rather
than assuming a plain completion stays sufficient forever.

The fix (still three parts — unaffected by the correction above, since the
underlying causes were always these, not the SDK wrapper), all before Phase
2 ships:
- Build the debrief as a raw `chat.completions.create` call, matching the
  OLD one-shot `_LanguageModelClient` pattern
  (`nora/app/composition.py:150-204`) — not a Strands `Agent` construction.
- Set `response_format={"type":"json_object"}` on that call — the wire-level
  lever `_LanguageModelClient` already uses (added because prompt-only JSON
  enforcement was proven insufficient once before) and that the Dreamer's
  agentic path never sets at all.
- Check `finish_reason` explicitly; map `"length"` (the raw-completion
  equivalent of `MaxTokensReachedException` — it doesn't raise, it silently
  truncates) to `unavailable`. Never let a truncated body reach the JSON
  parser, which could partially or wrongly succeed — the R-27 substitution
  this whole architecture exists to prevent, one layer down.

**Why Standing/debrief is structurally safer than the Dreamer's failure, and
how to keep it that way**: the Dreamer's bug is a *ratchet* — a persistent
watermark held on failure, so a stuck batch only grows and never recovers.
The debrief is per-turn (one `TagTrace` in, one debrief out), which has no
such accumulating cursor. **This safety only holds if Phase 2 stays
per-turn.** If any later refinement makes the debrief read "all traces since
X" and hold X on a failure, the same ratchet reappears. Keep it per-turn, or
add an explicit skip-with-a-counter so a permanently-too-large input cannot
block forward progress forever.

**3. The fold MUST read the current `Standing` row before writing a new
one, or it will reproduce the Dreamer's exact failure — confirmed live.**
Cross-session finding (nora-cc, 2026-08-22), grounded in production data:
across 963 processed messages, the Dreamer's entire output is 2 rows, both
wrong, both calling `Big Walk` (a co-op puzzle video game) a real-world
walking activity. The second run got *closer* to the truth — it knew about
gourds, knew they "play" it — but had no way to read the first row, so it
wrote a SECOND wrong gloss beside the first instead of correcting it. Both
are `live` simultaneously. **The tier accretes contradictions instead of
resolving them, specifically because nothing in the write path can see what
is already written.**

This is not a new requirement — §6.1 already scoped it: *"`prior` is not a
[`TagTrace`] field — it is the fold's second argument."* The debrief was
always meant to read the current `Standing.state` and hand it to the model
alongside the new `TagTrace` before producing a revision. **What's missing
is that `nora/domain/tag_trace_rederive.py`'s Phase-1 stub,
`rebuild_state(traces: Sequence[TagTrace]) -> str`, does not yet take
`prior` — Phase 2 must add it to the signature before implementing the fold,
not discover the omission while implementing.**

Two ways to deliver `prior`, and this plan's lean is the first:

- **Read in code, inject as prompt text** — a plain `SELECT` against
  `Standing` before the debrief's completion call, same shape as the fact
  tier's "standing facts... an input, never a tool" (§4.2): *"selection
  stays in code where it can be capped and counted."* Sufficient because
  Standing's read is always exactly one row, known in advance — never a
  search. Consistent with building the debrief as a plain
  `chat.completions.create` call (blocker 2, above) rather than reopening a
  tool loop.
- **A callable read tool**, matching nora-cc's proposed general seam — "a
  tier contributes its own read tools so its agent can consult its own
  prior output," precedented by the note tier's
  `ToolRegistry(notes=..., note_workspace=...)`. The right shape for an
  adaptor that needs to *search/discover* prior state mid-reasoning
  (`NoteRevisitor`, `FactAdaptor`) — not obviously needed for Standing's
  single, known-in-advance lookup, and adopting it would mean reintroducing
  a tool-bearing agent loop this plan otherwise avoids (blocker 2). Worth
  revisiting if Standing's needs turn out to be less trivial than "read one
  row" once Phase 2 is actually built — nora-cc is sharing the seam's shape
  once their investigation lands, in case it's needed.

Two smaller findings from the same investigation, carried forward:

- **Provenance direction.** `Fact.established_by` (and by extension
  anything Phase 4 writes) records which *Dreamer run* produced a row, not
  which *human* asserted the claim — provenance about the machine, not the
  belief. If `Standing` or the Phase 4 fact-proposal path records
  provenance, record the turn **and** the people in it, not just the run.
- **Input shape** (defensive note, not a Standing defect today): the fact
  tier's `FactCandidateAdaptor.inputs()` renders `f"{row.id}: {row.body}"` —
  no sender, no timestamp, no reply-threading — even though `TranscriptRow`
  already carries `person_name`/`ts`/`quoted_ts`. When Phase 2 builds the
  debrief's actual prompt from `ctx.transcript` (§6.2), use its already-
  rehydrated sender/threading info (`MentionRenderer`, DEC-34) rather than a
  stripped text dump — `ctx.transcript` already carries this, unlike the raw
  batch the fact tier was handed, so this is a "don't regress it away"
  check, not new work.

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
| 2.10 | `NoteProposal` — the debrief's fourth output | Not in the spec's §6.3 schema (`Standing`/`TurnVerdict`/`Fact` only) — added here 2026-08-21, operator decision. See below. |

**2.10, stated plainly.** `note_create`/`note_update` are Nora's own tools,
callable only inside her turn — `ReplyGuard._before_model` cancels the next
model call once `send_reply`'s terminal budget is spent (`nora/agent/guards.py`
~line 1006), so nothing in her own loop runs after the reply. `Standing`
already has an after-the-reply path because it was designed to be a SEPARATE
model reviewing the finished exchange (§9 — impartiality, not just cost). Notes
do not have that path yet. This task gives them one, on the same debrief call
that produces `Standing`/`TurnVerdict`/`Fact` — not a new stage.

- **Sink**: the note tier's `DreamSink`-shaped port (`propose`/`supersede`),
  the same interface `NoteCurator`/`NoteRevisitor` will use — never
  `note_create`/`note_update`, whose R-55 read-before-write latch is an
  in-turn rule with no meaning against a transcript reviewed after the fact.
  **Dependency**: this needs the note store joined to
  `tests/contract/test_dream_sink_contract.py`'s `IMPLEMENTATIONS` — not yet
  done as of 2026-08-21 (only `fake`/`sql`/`candidate` are joined). That join
  is nora-cc's Part 1b scope (`docs/superpowers/specs/2026-08-21-note-tier-design.md`
  §15.2); sync with them before wiring the sink rather than duplicating it.
- **Open sub-decision, to resolve when this task is actually built, not
  guessed here**: `Proposal.payload` for a note covers two shapes the fact
  tier's single-shape payload does not — a brand-new note (title,
  description, uncertainty, closing_condition, anchor) vs. a revision to one
  read this turn (body, parent_revision_id). Whether that is one payload type
  with an optional discriminant or two, and how `note_read`'s in-turn miss
  cache (§4.5) avoids the debrief re-proposing a note the tag path already
  considered and rejected, are both undecided.
- **Budget discipline carries over unchanged**: the debrief proposes,
  append-only; it never adjudicates (§9's argument against in-slot judgment
  under social pressure applies here exactly as it does to facts).

**Gate:** §14.3 — debrief latency on `nora_debrief_seconds`, with the
**second-queued-tag** case explicit, against p95 ≤ 5s
(`design/05-evaluation.md:79`). The user-visible cost lands on the second person
queued behind a busy conversation, not the first.

## Phase 2 status (2026-08-27): COMPLETE for `Standing`

Migration `0034_standing`, `uv run alembic heads` prints one line. DEC-140 and
R-69 in `design/09-decision-index.md`; `R-69` in
`tests/test_acceptance_rules.py::RULE_TESTS`. Ships OFF behind
`[standing] enabled = false`.

**Tasks 2.1–2.9 are done.** The stage is `nora/engagement/stages/debrief.py`,
awaited from `EngagementPipeline._run_admitted`'s `finally` — before
`release_and_dispatch`, so it holds the slot (2.1), after `completed` is
already final (2.2), catching `BaseException` at both the stage and the call
site (2.3), on every completed turn including silences, vetoes and timeouts
(2.4). `Standing` + `SqlStandingStore`/`InMemoryStandingStore` + a shared
contract suite (2.5). The window is `ctx.transcript`'s tail, `[standing]
window = 12`, its own and smaller than `[store] context_window` (2.6, S3).
`<standing>` renders in the volatile tail, escaped and `untrusted="true"`, last
of the six blocks and immediately before the transcript it explains (2.7).
`nora_debrief_seconds` is separate from `nora_tag_latency_seconds`, which is
read BEFORE the debrief runs (2.8). Overflow refuses, hands the model back its
own answer and asks it to consolidate; a second overflow writes nothing and the
prior stands (2.9).

**Also done, and it was §6.2b's stated precondition rather than a bonus:**
`nora_tagtrace_writes_total{outcome}`, whose denominator is
`nora_tag_latency_seconds`' own count — the written-versus-turns-taken ratio
Phase 1 shipped without.

**Blocker 1 (read the prior first)** is closed and MEASURED —
`nora_debrief_prior_total{prior}`. **Blocker 2** is closed as specified: a plain
`chat.completions.create` with `response_format=json_object` and an explicit
`finish_reason` check mapping `"length"` to `unavailable`, never to a parse.
**Blocker 3**'s `tool_choice` question does not reach this stage: the fold is a
single structured-output call with no terminal delivery tool. The 24 live
`tag_trace` rows read on 2026-08-27 also refute `16-open-items` §0's stronger
claim — every trace that made a tool call got its result back, so the transport
does honour tool calls; what 42% of turns failed to do is take the TERMINAL act
after a music tool succeeded, which is a doctrine/tool-description defect and a
separate one.

### Task 2.10 (note proposals) is NOT done, and it is blocked rather than skipped

Its own stated dependency: the sink is `DreamSink`-shaped, and `SqlNoteStore`
has `create`/`append_revision`/`record_miss` — not `propose`/`supersede`. It is
not joined to `tests/contract/test_dream_sink_contract.py::IMPLEMENTATIONS`
(still `fake`/`sql`/`candidate` only), which this plan already names as
another workstream's scope. §7's two sub-decisions stand as written and are
what the payload should be built to when that join lands.

`TurnVerdict` is Phase 3 and fact proposals are Phase 4; the fold's response
schema is deliberately NOT widened for either in advance, because an optional
field nothing validates is exactly the failure §7's
two-shapes-with-a-discriminant decision was made to avoid.

### One thing this phase could not do

`design/dreamer-agent-loop/15-roadmap.md` §6 still says "Phases 2–4, drafted,
zero code" and `26-standing.md`'s checkpoint still says Phase 2 "has zero
code". Both are now false. That directory was under active remediation by
another session on `worktree-knowledge-graphiti` while this branch was built,
so it was read and not edited. **Those two lines need correcting after that
branch lands.**

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
