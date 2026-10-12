---
type: design
title: "What a tag turn carries — measured block by block, 2026-10-12"
description: "Reopening on a wider question: compose the whole prompt per turn, doctrine included. The first call of 200 live tag turns split into doctrine sections, tool surfaces, memory blocks and transcript, with the whole-turn multiplier."
tags: [nora, context-plan, prompt-composition, doctrine, measurement, langfuse]
timestamp: '2026-10-12'
edges:
  - {dst: 08-experiment-and-handoff.md, rel: supersedes, provenance: asserted}
  - {dst: 07-current-measurements.md, rel: extends, provenance: asserted}
  - {dst: 10-doctrine-audit.md, rel: informs, provenance: asserted}
---

# What a tag turn carries

## Why the bundle reopened, and on what question

Operator, 2026-10-12: the parked experiment asked the wrong question. It let a
selector withhold four optional memory blocks from a prompt that is mostly
something else. The question now is what the WHOLE prompt of a turn should be
composed of — doctrine units, tool surface, memory blocks, transcript depth —
and by what. "Context router" may not be the right name for that; this node
calls it **composition** until the design names it.

The operator's case: asked "what was the weather in Hong Kong today?", Nora
needs her identity, her voice, the hard constraints and the weather tool. She
does not need the full conduct doctrine, every memory block and the whole tool
catalogue. Nora is no longer an assistant inside a group; she runs a cognition
loop, operates a computer and generates images and video, and the doctrine has
not caught up with that (node [10](10-doctrine-audit.md)).

The 2026-10-07 finding still stands for what it measured: a per-turn MODEL
selector over four optional blocks costs more than it saves. It does not answer
this question, because the blocks it could withhold were a small part of the call.

## Method

200 most recent `engagement` traces in Langfuse, 2026-09-25 to 2026-10-11, read
on norty. For each, the first GENERATION's input (the main model's first call)
was split into the doctrine's `<section name=…>` units, the system prompt's other
tags, and each top-level tag of the prefill messages. No room text left norty;
the evidence holds only per-block character counts.

No tokenizer for the deployed `nora` alias is installed, so characters were
converted with a least-squares fit across the 200 turns:
`input_tokens = a·ascii_chars + b·cjk_chars + c`. Result: a = 0.259 tokens per
non-CJK character, b = 0.906 per CJK character, c = 2,073 (tool schemas, which the
trace does not record, plus message framing). Median absolute residual 137 tokens
on a ~20.8k call (0.7%); maximum 1,459. Per-block figures are estimates from that
fit, not tokenizer counts.

Scripts and numeric output: [composition-2026-10-12/](evidence/composition-2026-10-12/)
(`seg.py`, `fit.py`, `turn_totals.py`, `rows.json`, `*-output.txt`).

## The first call

`input_tokens` median **20,848**, p10 19,337, p90 23,912.

| Part | Median tokens | Share | Present |
|---|---:|---:|---|
| **Doctrine** (7 sections) | **~9,130** | **44%** | every turn |
| ↳ `conduct` | 3,906 | | 200/200 |
| ↳ `voice` | 2,180 | | 200/200 |
| ↳ `constraints` | 948 | | 200/200 |
| ↳ `belonging` | 740 | | 197/200 |
| ↳ `identity` | 557 | | 200/200 |
| ↳ `pictures` | 505 | | 180/200 |
| ↳ `silence` | 292 | | 200/200 |
| **Tool surface** | **~5,220** | **25%** | every turn |
| ↳ `tool_catalogue` block | 2,669 | | 200/200 |
| ↳ tool schemas + framing (fit intercept) | ~2,073 | | |
| ↳ `available_skills` | 476 | | 200/200 |
| **Memory and room blocks** | ~4,000–5,000 | ~22% | varies |
| ↳ `character` | 871 | | 82/200 |
| ↳ `culture` | 827 | | 200/200 |
| ↳ `notes` | 750 | | 176/200 |
| ↳ `self` | 637 | | 36/200 |
| ↳ `standing` + `standing_working_set` | 603 + 350 | | 200 / 76 |
| ↳ `computer` | 528 | | 75/200 |
| ↳ `peer_history`, `peer_card`, `self_card` | 363, 262, 272 | | 197, 164, 140 |
| ↳ `unit_culture`, `documents`, `room_members`, `presence`, `lessons` | 258, 243, 231, 226, 197 | | 25, 53, 138, 200, 13 |
| **Transcript** (`group_transcript`) | 2,068 (max 4,952) | 10% | every turn |
| **The message** (`tag_message`) + header | 145 + 182 | <2% | every turn |

The message a turn exists to answer is under 1% of what the model reads.

## The whole turn

| Per turn | Median | p90 |
|---|---:|---:|
| Generations | 4 | 9 |
| Input tokens, all generations | 86,388 | 250,345 |
| Of which not served from cache | 36,120 | 74,875 |

Every loop iteration resends the full prompt, so the first-call size is paid
once per generation. The count includes the post-turn debrief generation
(~7k input in the sampled trace); it is not the main agent alone.

## What this does and does not establish

- **Established:** doctrine and the tool surface are ~69% of every first call
  and are present regardless of what was asked. The memory blocks the parked
  experiment could withhold are ~22%, which is why withholding them could not pay.
- **Cost is not tokens.** 123 of 200 first calls reported `cache_read_input_tokens`
  (median 11,822), and the cached part is the stable head, mostly doctrine. Cutting
  doctrine saves fewer dollars than tokens; a turn-variable head would also break
  the cache for everything behind it, as the September defer-tools measurement showed: input fell 36% and cost rose.
  The design must keep a byte-stable core first and put composed units after it.
- **Not established:** that a smaller composed prompt answers as well. That needs
  the eval suite on a built composition, not this measurement.
- **Not measured:** per-turn reply latency against prompt size, and which turns
  needed which units. Node [10](10-doctrine-audit.md) supplies the second as a
  source audit, not as a measurement.
