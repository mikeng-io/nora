---
type: design
title: "Clef on Ollama Cloud — the first decision model inside the latency budget, still short on recall"
description: "2026-10-17 benchmark of clef:27b over Ollama Cloud's /v1/systemone, the door node 03 found unimplemented. On node 15's 200 real tag turns and harness it answers in 1.2-2.4 s where Tev1 on the iGPU took 4.6-73 s, and ranks parts better (AUC 0.693 on 200 turns vs 0.557 on Tev1's 36), but keeps only 91% of what turns used at a 26% smaller prompt. As the relevance gate's reader it shows no measurable difference from nora-gate at n=1 (17/19 vs 16/19, failing different cases); safety is unmeasured because every forge case is parked."
tags: [nora, context-plan, decision-model, clef, systemone, ollama-cloud, relevance-gate, benchmark]
timestamp: '2026-10-17'
edges:
  - {dst: 15-what-the-composer-decides.md, rel: informs, provenance: asserted}
  - {dst: 03-measurement-decision-models.md, rel: informs, provenance: asserted}
  - {dst: evidence/composer-clef-2026-10-17/README.txt, rel: constrained_by, provenance: asserted}
---

# Clef on Ollama Cloud

Operator, 2026-10-17: Ollama Cloud now serves a decision model, Clef; benchmark
it against the earlier decision scenarios (prompt composition, the relevance
gate). The bundle stays parked; this node records a measurement, not a ruling.

## 1. What changed upstream

Node 03 §4.2 (2026-10-05) found `POST https://ollama.com/v1/systemone` →
`not implemented` and no decision model in the cloud catalogue. On 2026-10-17
the catalogue lists `clef:27b`, and `/api/show` reports capabilities
`decision, vision`, family `qwen35`, 27.4B parameters, context **65,536**
(Tev1 ships with `num_ctx` 2050, the cap node 15 had to lift by hand). The
endpoint answers with the same wire shape as local Ollama 0.35 (`state` plus
typed `questions` → `answers` with probabilities), so node 15's harness ran
against it unchanged except for the URL and a bearer key
(`evidence/composer-clef-2026-10-17/run_cloud.py`).

The catalogue also lists `beam` (501B, `completion, thinking, tools`, 1M
context). It is a chat model, not a decision model, and was not measured.

## 2. Composing the prompt (node 15's step 2, same turns, same labels)

The 200 real tag turns, ground truth and analyzer of
`evidence/composer-tev1-step2-2026-10-12/`; Clef ran every turn of both arms,
concurrency 1, one request per turn, every state first-seen.
Recall = evidenced items kept / 471; ratio = composed prompt tokens / recorded
first call.

| arm, model | n | median s | recall | ratio | AUC |
|---|---:|---:|---:|---:|---:|
| A (one Noul per part, ~48), Tev1 0.8B iGPU | 36 | 73.0 | 1.000 (x1.0) | 0.984 | 0.557 |
| A, **Clef 27B cloud**, x0.5 | 200 | **2.36** | **0.909** | 0.739 | **0.693** |
| A, Clef 27B cloud, x1.0 | 200 | 2.36 | 0.771 | 0.570 | |
| A, Clef 27B cloud, x2.0 | 200 | 2.36 | 0.505 | 0.372 | |
| B (turn kind + depth), Tev1 0.8B iGPU, x1.0 | 200 | 4.64 | 0.590 | 0.514 | |
| B, Tev1 4B iGPU, x1.0 | 76 | 27.3 | 0.644 | 0.469 | |
| B, **Clef 27B cloud**, x1.0 | 200 | **1.22** | **0.684** | 0.443 | |

0 errors and 0 missing answers in 400 requests; p90 2.77 s (A) and 1.48 s (B).
Clef's turn-kind answer is decisive (top probability median 0.685 vs Tev1
0.8B's 0.452), and on arm A it separates evidenced parts from the rest
(probability median 0.335 vs 0.169) where Tev1 said yes to ~90% of everything. The
AUCs are not on the same turns: Clef's covers all 200, Tev1's the 36 it finished.

**Reading it.** Latency is no longer the blocker: both arms fit inside a
second or two, where Tev1 needed 4.6-73 s. Recall is. The best point (A x0.5)
still drops ~1 in 11 evidenced items for a 26% smaller prompt, and greeting
turns stay at ~14.6k tokens, far from node 14's <5k target. Most missed at
A x0.5: `tools.catalogue` (9), `doctrine.pictures` (8), `block.notes` (8),
`block.peer_history` (5). Node 15's training step remains the next move; Clef
is a stronger starting point than Tev1, and its 65k context removes the
`num_ctx` problem, but the cloud adds a network hop and a second provider on
the tag path.

Ground truth is node 15's positive evidence only (tool calls, media codes,
n-gram overlap); it cannot say whether voice or identity doctrine was needed,
and 37 of 200 turns have no evidence at all.

## 3. The relevance gate's reader

Spike branch `mikeng-io/spike-clef-gate` (`848228c0`, never to merge): with
`NORA_SPIKE_GATE_SYSTEMONE_MODEL=clef:27b`, the composition root hands the gate
a client that asks one `choice` question (`go` / `no_go`), instructions =
`participation.md` plus the capability digest, state = the rendered room minus
the gate's own JSON suffix (stripped only as an exact suffix: a member can type
the contract line). The verdict is re-serialised to the chat path's JSON, so the
gate's parser and fail-closed rules are unchanged. Unset, the code path is
master's.

`python -m evals relevance_gate --reasons`, on norty, worktree
`nora-wt-eval-clef` at `848228c0`, same source for both arms, one run on the
host at a time, one draw per case:

| arm | active | pass / fail / error | not passed |
|---|---:|---|---|
| `nora-gate` (deployed combo) | 19 of 40 | 16 / 2 / 1 | `closed/where-been-no-name`, `closed/where-is-named`; `bound/already-answered` timed out at 20 s |
| `clef:27b` | 19 of 40 | 17 / 2 / 0 | `closed/where-is-named`, `world/route` |

No measurable difference: one case apart at n=1, the `sample: 5` cases drawn once,
and the arms fail different cases (Clef newly fails `world/route`; `nora-gate`
alone fails `closed/where-been-no-name`), so this does not show equal quality either. The
gate is off in production (2026-09-24), so nothing here changes live behaviour.
**Safety is unmeasured:** all six `safety: true` cases (`forge/*`) are parked,
so a forged-commitment or forged-assistant-row window was never put to either
arm. Clef reports no token usage through this client, so its spend line is absent.

## 4. What would move this

- Composition: train or prompt-tune against node 15's labels with Clef as the
  base, then re-run this table; ship nothing below ~0.95 recall.
- Gate: anchor the `forge/*` cases (the hard bar) before any gate comparison
  counts, then draw at `--sample 5`.
