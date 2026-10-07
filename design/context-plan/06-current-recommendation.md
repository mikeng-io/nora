---
type: design
title: "Current recommendation — optional exposure after full assembly"
description: "Source-grounded answers to the ten context-selection questions, corrected by current acceleration measurements and the peer-history tool coupling."
tags: [nora, context-plan, architecture, routing, experiment]
timestamp: '2026-10-07'
edges:
  - {dst: 01-the-seam.md, rel: informs, provenance: asserted}
  - {dst: 02-what-is-selectable.md, rel: informs, provenance: asserted}
  - {dst: 07-current-measurements.md, rel: constrained_by, provenance: asserted}
  - {dst: 08-experiment-and-handoff.md, rel: sequences, provenance: asserted}
---

# Current recommendation

This node is authoritative for the current experiment. Nodes 00–05 preserve the
October 5 proposal; their CPU-only verdict and wider selectable-block set are
historical. New measurements establish functioning iGPU offload and a plausible
cloud selector. They do not establish benefit to Nora's cognition.

The operator initially requested source inspection and no implementation. The
later authorization is to fix the existing sender-name eval defect and add an
**experiment-only context filter**, then compare replay arms. There is no
production router authorization. The latest instruction is to save this bundle,
commit the worktree and provide a context-transfer prompt.

## Verified source flow

`TranscriptInjection.assemble` in `nora/engagement/context.py` gathers transcript,
cards, Standing, notes, presence, Culture, character, computer, documents and
subagents with `asyncio.gather`. The tag pipeline assembles this full `TagContext`,
constructs `Room` and the admitted tool surface, then calls
`nora.agent.prefill.build_prefill`. The latter declares the named blocks through
its existing `_BLOCKS` table and `nora.domain.blocks.assemble`.

Culture means what this room is like and how it became so. Standing means Nora's
current position **in this conversation**, not social reputation. Retrieved
context and context exposed to the main model are separate facts.

## The ten questions

1. **Where the contract lives.** A future runtime value belongs beside
   `ContextBlock` in `nora/domain/blocks.py`. It remains separate from
   `EngageDecision`: whether an occasion runs is different from what cognition
   sees. Evaluate it after full context assembly and before constructing the
   Room/tool surfaces and prefill. The experiment's value lives only in
   `nora/subject/context_filter.py`; no production parameter or model call is added.
2. **Phase 1 after assembly.** Yes. All existing initial retrieval remains intact.
   Filter the existing declared blocks at their assembly seam. Do not null
   `TagContext` fields or reparse the incoming message. Phase 2 is a later experiment.
3. **Selector substrate.** Current normalized message; tagger name/card; clock;
   Culture and Standing with existing timestamps and type framing; a recent
   transcript with names, ages and explicit order; available candidate descriptions
   and bounded previews. The experiment uses eight recent rows and 600-character
   candidate previews. This substrate is a hypothesis, not proven sufficient for
   ambiguous long or mixed-domain turns. Uncertainty means include context.
4. **Mandatory versus selectable.** For this experiment, keep the static authored
   head, current and quoted message, admitted tool catalogue/schemas, room membership,
   presence, self/peer cards, people in the window, notes/decisions, Culture/character,
   Standing/working set and full transcript. Select only `peer_history`, `documents`,
   `computer`, `subagents`. Omitted context does not remove a tool's admission.
5. **Culture/Standing reach Nora.** Both reach the selector and Nora. Letting only
   the selector see them would delegate conversational interpretation to a second
   reasoning stage. Withholding either requires a separate downstream measurement.
6. **Transcript depth.** Fixed at the full assembled window for now, including
   quote expansion beyond the configured default. The source binds numbered rows
   to quotes, `send_reply` and `who_said` references. A prompt-only tail cannot
   safely change those references. Any later extent choice needs one coherent
   projection across prompt and tool surfaces, preserving resolved quote parents.
7. **Selector failure.** Full context on timeout, API failure or invalid output.
   Unknown names, extra keys, duplicate names and changed depth are rejected. Record
   an error, never a successful plan. A successful Nora reply under fallback does
   not pass the router experiment.
8. **Cache and latency.** The main static head and tool schemas stay unchanged.
   Volatile changes can still change caching. Measure cached/fresh usage rather than
   pricing character savings. Phase 1 adds a serial request after concurrent reads.
   Phase 2's base reads → decision API → selected reads can lengthen the critical
   path despite fewer reads; require measured benefit before designing it.
9. **Minimum evidence.** Bind selected, available and actually withheld blocks to
   each replay draw; selector failures/timing/usage/provider/cache identity; rendered
   exposure sizes; existing Nora tool grades and cached/fresh tokens; complete replay
   settlement time. Distinguish settlement time from time to the first sent reply.
   One draw is a smoke check, not an accuracy estimate. Tool routes do not prove
   reply quality; the open judge-timeout defect limits that measurement.
10. **Which model.** Conservative deterministic rules are the zero-call baseline.
    The operator authorized DeepSeek V4.1 Flash through OmniRoute/Ollama Cloud.
    Its small synthetic result warrants testing, not deployment. Tev1 4B is also an
    accepted candidate and is measured on iGPU and T4. JEV, specialized Laya,
    Strands plus pre-ranker and another small LLM need comparable measurements.

## Contract and ownership

```python
@dataclass(frozen=True)
class ContextPlan:
    blocks: frozenset[str]
    transcript_window: int
```

In the experiment, `blocks` is **optional names only**; application code retains
all mandatory blocks. `transcript_window` must equal `len(ctx.transcript)`.
Do not silently interpret it as the configured window: quote expansion can differ.

The Flash model proposes a plan. Application code validates and applies it. Nora
alone decides what to say and which tools to call. The exact system instruction is
saved in [evidence](evidence/replay-system-prompt.txt) and lives with the declared
input block table in `nora/subject/context_filter.py`. It prohibits answering the
message, proposing a reply, deciding engagement or calling tools.

## Peer-history coupling

`Room.tagger_card_injected` specifically means **peer_history was exposed**.
The `about` tool returns `already_present` when it is true instead of fetching
history again. Hiding the block while leaving that flag true would point Nora at
context she never received and prevent the recovery read.

The experiment temporarily records the actual Room, sets this flag from the
selected rendered block set before tool execution, and clears its temporary
reference. Full fallback retains the flag when the history block exists. A future
runtime planner should pass exposure directly into Room's constructor. It must
not confuse fetched context with exposed context.

This recommendation holds until anchored downstream evidence demonstrates useful
selection with no required-context losses and acceptable net latency/cached cost.
