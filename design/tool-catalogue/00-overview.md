# Tool Catalogue — split a tool's text by the decision it serves

## The measurement that started this

One `chat` call on the Discord tag lane, Langfuse trace
`836eda48d8217234a73587bac33628aa` (2026-09-21 15:21 UTC), reported input
**35,473 tokens**:

| part | tokens | share |
|---|---|---|
| 54 tool schemas | 22,130 | 62% |
| doctrine (identity, voice, silence, conduct, constraints) | 8,288 | 23% |
| per-turn situation (self_card, note index, transcript) | ~5,400 | 15% |
| the tag message itself (`@nora play some music`) | 122 | 0.3% |

Counts are `cl100k_base` (tiktoken), not DeepSeek's tokenizer. The trace
cross-checks them independently: `cache_read_input_tokens` is **30,080** on
every warm call, and tools + doctrine = 30,418 — the cached prefix *is* those
two, 1.1% off. Remainder 35,473 − 30,080 = 5,393 ≈ the per-turn block.
Re-derive with `scripts/` — see `05-measurement.md` §1.

The prompt is not doctrine-heavy. It is **tool-schema-heavy**, and the schemas
grew there by decision: DEC-150 (`design/capability-drop/`) deleted the
generated `kind: capability` block precisely because the schemas already
carried 85% of its when-clauses. DEC-29 recorded the schemas at **1,222
tokens, 25% of a ~4,900-token call**. Today: **22,130 tokens, 62% of 35,473**.
Eighteen-fold growth into the surface that had just been made their single
home, with nothing bounding it.

## What a tool's text is actually doing

A description today carries three different things fused into one artefact,
and every turn pays all three for all 54 tools:

1. **Selection** — *does this capability exist, and is it the one I want?*
   The first line, "call this when…", the boundary with a sibling tool.
2. **Invocation** — *what arguments, which arm, what format?*
   The `inputSchema`, the `what="day"` arm list.
3. **Reporting** — *what does `unavailable` mean, may I read the coordinate
   aloud, must I carry the age?*

Measured across all 54 tools: descriptions **16,045** tokens, `inputSchema`
**4,630**, JSON wrapper/names ~1,455. **54% of description text — 8,602
tokens — is category 3**, read only *after* a call returns. Per tool:
`send_reply` 100%, `where_is` 99%, `online_now` 93%, `where_been` 84%,
`parking_status` 79%. (Paragraph-level classification on status vocabulary;
crude at the margins, sound at this magnitude.)

Each category is needed at a different moment. Fusing them means paying
invocation detail and reporting rules for 52 tools whose only pending
decision is "not this one."

## The split

| category | lives in | paid | status |
|---|---|---|---|
| selection | a generated **catalogue** in the prompt | always — all 54, 991 tokens | **in scope** |
| invocation | the **tool schema** | on `load_tools`, ~407 × ~1 per turn | **in scope** |
| reporting | the **tool result** | on return, only the tool that fired | **PARKED** |

`01-the-catalogue.md`, `02-load-tools.md`, `03-result-handling.md` take one
category each. `04-code-surface.md` is the authoritative per-file change list.
`05-measurement.md` is how to prove it and what would falsify it.

**Category 3 is parked** by operator decision, 2026-09-22 — "not really a big
deal" against what categories 1–2 already save. `03-result-handling.md` is
kept for its measurement, not as a plan; see its header. The split is stated
as three because that is what a tool's text *is*, and naming the third is what
makes the first two legible. Implementing it is a separate decision.

## Why this is viable: a turn uses 2 tools out of 54

574 turns, 2,000 tool calls, 2026-08-12 → 2026-09-21 (Langfuse, grouped by
`traceId`):

| | median | mean | p90 | max |
|---|---|---|---|---|
| distinct tools per turn | 2.0 | 2.02 | 4 | 11 |
| cold tools (outside the hot set) | 1.0 | 1.09 | 2 | 7 |

27.2% of turns need **no** cold tool; 50.2% need exactly one. Together 77.4%.

The duplication is real and small: a catalogue line (~17 tokens) is paid on
top of the schema for a tool that *is* loaded — **4% overhead per loaded
tool**. The saving is on the ~52 not loaded.

**Break-even: 46 cold tools in a single turn.** Observed maximum is 7; p90 is
2. There is no measured turn anywhere near the losing side.

## Projected budget

| | today | with the split |
|---|---|---|
| tool schemas resident | 22,130 | catalogue 991 + hot set 2,606 + `load_tools` ~80 = **~3,680** |
| fetched per turn (mean) | — | 1.09 × 407 ≈ **444** |
| doctrine | 8,288 | 8,288 (untouched by this bundle) |
| situation | ~5,400 | ~5,400 |
| **mean total, per call** | **~35,500** | **~17,700 (−50%)** |
| **per TURN** (the honest unit) | ~106,700 | **~72,000 (−30%)** |

The catalogue figure is **measured**, not estimated: rendering `name — first
sentence` for all 54 tools from today's docstrings is 991 cl100k tokens, mean
18.4 per line. `05-measurement.md` §2a keeps it under 1,400 as a regression
guard.

With category 3 parked, the 8,602 tokens of result-handling prose stay in the
schemas — so a **fetched** schema still costs its full ~407 tokens. That is
already what the 444/turn figure assumes; parking changes the ceiling this can
reach later, not the number it reaches now.

**The per-call row is the flattering one and it is not the unit that matters.**
`load_tools` plausibly adds a fourth model call on the 72.8% of turns needing a
cold tool, and a fetched schema is re-paid on every later call in the turn.
Per turn the saving is ~30%, not ~50%. It is still large, and it does not move
the break-even near the observed maximum of 7 — but `05` §3 gates on per-turn
tokens, calls-per-turn and end-to-end latency, and treats every per-call number
as a diagnostic. Adversarial review found this framing and it was right to.

## What this is not

**Not a trim.** Nothing is deleted, and with category 3 parked, no existing
docstring prose moves at all — the only docstring edits in scope are the
`where_is` discriminator repair and two over-budget first sentences. DEC-24's minimize-parameters was already
over-applied once to external-service tools, cutting intent-bearing
parameters (the transit BUS/SUBWAY/TRAIN modes); the operator reversed that
on 2026-08-06. Every word survives — it moves to where its decision is made.

**Not the capability block returning as it was.** DEC-150 killed that block
for duplicating the schemas. Under this split the schemas are not resident,
so the duplication that justified killing it does not exist. What returns is
an **index**, not a second copy. `ToolRegistry.tool_names()` was deliberately
kept, so the generator's input is still in place.

**Not lane scoping, and not a replacement for it.** Platform scoping already
exists for the music verbs (`tests/unit/test_tool_registry.py`
`::test_non_discord_catalog_omits_music_tools`, pinned as a token-saving cut).
It composes: a scoped-out tool leaves the catalogue too. Under this split its
value shrinks, because an unloaded tool costs a catalogue line rather than a
schema.

## Convention this follows

Anthropic's published threshold: use deferred tool loading when you have **10+
tools, or tool definitions exceeding 10k tokens**; tool-selection accuracy
"degrades once you exceed 30–50 available tools"
(<https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool>).
Nora is at **54 tools / 22,130 tokens** — 5× and 2.2× past both.

LongFuncEval (<https://arxiv.org/abs/2505.10570>) measures the degradation:
7–85% accuracy loss as the tool catalogue grows, 7–91% as tool responses grow,
13–40% over long multi-turn.

The second figure is why `03-result-handling.md` is parked rather than
sequenced: moving prose into results makes results longer, and that is the
only part of this work whose risk lands on quality rather than tokens.

The third figure — 13–40% loss over long multi-turn — is the one that still
applies to what IS in scope, because `load_tools` adds a turn on cold paths.
`05-measurement.md` §2c and #325.

Anthropic's own tool-authoring guidance also puts error and response handling
in **tool responses, not descriptions**
(<https://www.anthropic.com/engineering/writing-tools-for-agents>), with a
measured 206 → 72 token example.

**Strands has no native tool search.** `strands-agents==1.50.2` ships exactly
one integration, `AgentCoreToolSearchPlugin`, which requires Amazon Bedrock
AgentCore Gateway with semantic search and tools registered on the gateway.
Nora's tools are native Python closures behind OmniRoute/DeepSeek. The
plugin's *shape* is the reference — a hook before model invocation that
adjusts the loaded tool set — and Nora already registers that hook
(`nora/agent/guards.py:1201`, `ReplyGuard` on `BeforeModelCallEvent`).

## Two architectures, and why this bundle picks the second

**A — pre-model hook (AgentCore's shape).** Derive intent from recent
messages, load the matching tools, call the model. No extra round trip; costs
one classifier call. **Fails silently**: a mis-derived intent means the tool is
simply absent, the model cannot ask for it, and nothing reports it. That is
DEC-28's failure mode — a model that looks like it will not reach for a tool
when the tool was never offered — and constraints.md's "a broken Nora and a
quiet Nora look the same" is the rule it violates.

**B — model-driven `load_tools` (this bundle).** The catalogue is resident, so
Nora always knows the shape of what she can do; she names what she needs, and
`load_tools` registers them through Strands' own `register_dynamic_tool` so the
next model call carries them natively (`02`, verified).
**Fails closed and visibly**: R-7 default-deny already refuses an invented
tool name, so a miss is an error, not a silence. Self-correcting — she can ask
again. Costs at most one extra round trip on a cold turn, and measurement says
a tag turn already spends ~3 model calls, so it is largely absorbed.

The catalogue **must be resident**, not itself behind `load_tools`. A
catalogue you have to ask for requires already suspecting the capability
exists, which reintroduces exactly the failure A was rejected for.

## Sequencing

1. **Phase 1 — catalogue + `load_tools`** (`01`, `02`). The whole in-scope
   saving lives here. Gated on `05` §2 — 2b and 2c can each stop it.
2. **Phase 2 — lane scoping** (out of bundle; existing platform filter
   extended). De-risks phase 1 by shrinking the catalogue per room.
3. ~~Phase 3 — result-handling relocation~~ — **PARKED** 2026-09-22. It was
   sequenced last because it carries LongFuncEval's response-length risk;
   parking it removes the bundle's only quality risk beyond selection
   accuracy, at the cost of ~6,970 tokens not taken.

Doctrine compression (identity 973 / conduct 3,893 tokens) is **a separate
workstream** and is deliberately not in this bundle; the same three-way
principle may apply to it, which `05-measurement.md` §5 records as an open
question rather than a plan.

## Status

No DEC number is claimed here. Per `AGENTS.md`, a decision number is taken
from `design/09-decision-index.md` at **merge** time, not at claim time
(2026-08-21: two sessions coordinated numbers carefully and a third took
DEC-131 anyway). Highest allocated at the time of writing: DEC-192.
