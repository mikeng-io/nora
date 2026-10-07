---
type: design
title: Historical October 5 — Measurement — what a decision model costs on this host, and the four doors
  that do not exist
description: 'Historical October 5 finding, superseded in relevant part by nodes 06–08 on October 7.
  The decision-model benchmark run on 2026-10-05: the native Ollama /v1/systemone door, per-model context
  refusal, tokens summed once per question, CPU-only wall clock for nimble/tev1/tev1:0.8b, the JEV door
  that is catalogued but speaks no protocol OmniRoute routes, the cloud door that is not implemented,
  and the iGPU flag that has never been true on this box.'
tags:
- nora
- benchmark
- decision-model
- ollama
- jev
- laya
- igpu
- measurement
timestamp: '2026-10-05'
edges:
- dst: 00-overview.md
  rel: realizes
  provenance: asserted
- dst: 04-sequencing.md
  rel: informs
  provenance: asserted
---

> **PARTLY DEPRECATED (2026-10-07).** Historical 2026-10-05 proposal and CPU measurements. The current recommendation, acceleration measurements, operator authorization and implementation state are in [06](06-current-recommendation.md), [07](07-current-measurements.md) and [08](08-experiment-and-handoff.md). In particular, the CPU-only/no-fast-selector conclusion is superseded; the historical measurements themselves remain recorded.


# Measurement

Run 2026-10-05 on the Nora host (GMKtec NucBox M7 Ultra, Ryzen 7 PRO 6850U, 16
threads, 27.7 GiB RAM, Radeon 680M iGPU, no CUDA). Scripts kept at
`~/workspace/nora-bench-ctx/` (`bench_systemone.py`, `bench_fair.py`) — outside
the repo, because they are a measurement and not a Nora artifact.

## 1. The right door, and how it was established

Ollama 0.35 ships a system-one endpoint: `POST /v1/systemone`, Jev's wire shape
(`state` + named typed `questions` → `answers` with probabilities). Established
three ways, not assumed:

* the installed binary carries the implementation — `strings
  /usr/local/bin/ollama` shows `decision.Question`, `decision.Choice`,
  `decision.NoulAnswer`, `decision.ScoreAnswer`, `decision.Compiled`,
  `orderedmap.Map[string,ollama/decision.Question]`;
* `ollama show nimble` reports `Capabilities: decision` (and `tools thinking
  completion`), as does `tev1:4b` and `tev1:0.8b`;
* the endpoint answers — `POST /v1/systemone {"model":"tev1:0.8b", …}` returns
  `{"answers":{"refund":{"type":"noul","noul":0.377…}},"usage":{…}}`.

`ollama ps` reports a **PROCESSOR** column, which is the only honest way to say
where a model ran. Every run below reads **100% CPU**.

## 2. The plan question set

11 questions over a substrate of the shape this design proposes — the operator's
routing substrate (current message, tagger, culture, standing, recent messages)
plus filler to reach a size:

| question | type |
|---|---|
| `peer_card`, `peer_history`, `notes`, `loop_decisions`, `culture`, `character`, `computer`, `documents`, `subagents`, `standing` | `noul` ("is this block needed?") |
| `transcript` | `score` ("how much of the window should be shown?") |

## 3. What it costs

**Cache honesty first.** A repeated identical state lets llama.cpp restore its KV
checkpoint, and the second and third calls then look ~10x faster than the first.
Measured with the state rebuilt distinct per repetition (Nora's tag turn never
repeats a state), warmed, 11 questions:

| model | params | state | wall (s), reps | input tokens | per-question prompt |
|---|---|---|---|---|---|
| `tev1:0.8b` | 752M Q8_0 | ~1.4k chars | **6.00 / 7.60 / 6.01** | 17,661 / 17,617 / 17,562 | ~1,605 tok (cap 2,050) |
| `tev1:4b` | 4.2B Q8_0 | ~1.4k chars | **33.26 / 32.79 / 30.22** | 17,661 / 17,617 / 17,562 | ~1,605 tok |
| `nimble` | 9.0B Q8_0 | ~1.4k chars | **10.25 / 9.00 / 8.33** | 16,572 ×3 | ~1,507 tok |
| `nimble` | 9.0B Q8_0 | ~4k chars | **8.66 / 7.93 / 12.25** | 28,826 ×3 | ~2,620 tok (cap 8,194) |

Load (first call, includes model load):

| model | build + load |
|---|---|
| `tev1:0.8b` | 6.40 s (already resident) / 0.41 s when hot |
| `nimble` | 77.5 s from cold |
| `tev1:4b` | 38.1 s from cold |

**Tokens are charged once per question.** The same 600-character state asked
with a growing question count (one call, `tev1:0.8b`):

| questions | 1 | 2 | 5 | 10 | 20 |
|---|---|---|---|---|---|
| `usage.input_tokens` | 185 | 450 | 1,725 | 5,450 | 19,110 |
| output tokens | 1 | 3 | 6 | 11 | 21 |

A 20-question call on the same shape bills 18,610 input tokens for 20 output
tokens in 4.38 s — the same linearity at a larger scale, and the reason the
`output_tokens` field is a red herring: **the cost of a decision model is
entirely on the input side.**

So `input_tokens` is a SUM, and the per-question prompt (state + that question's
instructions) is what the model's `num_ctx` caps. This is why 11 plan questions
over a 1.4k-char substrate bill **~17.6k input tokens for one plan** — more than
the entire rendered tag prompt's *context-block* share (§5). The vendor's own
model page states the mechanism ("Nimble reads the prompt once per question and
scores the answer tokens directly").

The daemon's own timing lines put a number on the CPU rate behind these walls:
`prompt eval time … 7.0 ms per token, ~140 tokens per second` (`tev1:0.8b`, warm
slot, journalctl `print_timing`).

**Per-model context is a hard refusal, not a hint.** `tev1:0.8b`'s Modelfile
pins `num_ctx 2050`, and the endpoint rejects an over-long per-question prompt
with `HTTP 400: prompt 0 has 2314 tokens; expected 1–2050 (input is never
truncated)`. A selector's substrate is therefore capped by the *smallest* model
in the fleet, and the cap is per question — a 4k-char substrate is not usable at
all on the 0.8B model. `nimble` carries `num_ctx 8194`.

## 4. The three doors that are not available

**4.1 The JEV/Laya class the operator named — catalogued, unreachable.**

`GET /v1/models` on OmniRoute (852 models) lists exactly two:
`opencode-zen/jev-1.13` and `opencode-zen/jev-1.13-free`, both declared with
`context_length: 200000` and `capabilities {tool_calling: true, reasoning:
true}`. They are reached **only** by Jev's own protocol, and every OpenAI-shaped
route refuses:

| route | answer |
|---|---|
| `POST /v1/chat/completions` | `400 ModelProtocolUnsupported: Model does not support this protocol.` |
| `POST /v1/responses` | same |
| `POST /v1/messages` | same |
| `POST /v1/systemone` | `404 unknown_route` — OmniRoute has no System One route |
| `POST /v1/decisions` | `404 unknown_route` |

No key is configured for the hosted APIs either: `~/workspace/nora/.env` carries
no `JEV_API_KEY`, `LAYA_API_KEY` or `DECISIONS_API_KEY`. **Wiring JEV or Laya is
therefore not a config change — it is a new provider route (or a direct HTTPS
call from the container), and a new egress destination, which is an operator
decision and not an implementation detail.** (Their published latency is ~70–500
ms; that is a vendor claim from their own pages, not something measurable from
here without a key.)

**4.2 Ollama cloud — decision models do not exist there.**

`POST https://ollama.com/v1/systemone` → `{"error":"not implemented"}`. The cloud
catalogue (17 models: `kimi-k2.6/k3`, `glm-5.2/5.3(-flash)`, `minimax-m2.7/m3`,
`deepseek-v4.1-flash/v4-pro`, `nemotron-3-nano/super/ultra`, `gpt-oss:20b/120b`,
`gemma4:31b`, `mistral-large-3:675b`) contains **no** `nimble` and no `tev1` —
`POST /api/chat {"model":"nimble"}` → `{"error": "model 'nimble' not found"}`. So
the operator's "ollama cloud" arm of the reference benchmark has nothing to run
in the decision class; the only cloud shapes available are chat models, and the
chat route honours `format`/JSON-schema but is a different (slower, prose-capable)
instrument.

For reference, the cloud *chat* route in the same shape (11 questions worth of
context, JSON answer, 2026-10-05): `gemma4:31b` 0.68 / 1.03 s (no thinking,
75 out tokens, in 502 tok); `nemotron-3-nano:30b` 4.23 / 6.16 s **with 4.7–5.2k
characters of thinking**; `gpt-oss:20b` 5.01 / 7.94 s with 2.2–3.3k chars of
thinking. Those are chat calls, they re-emit reasoning, and they are not the
typed-decision contract — recorded here only so the cloud arm is not left
unmeasured.

**4.3 The iGPU — the flag has never been on.**

The operator asked specifically for "ollama cloud with iGPU". The iGPU path is
not merely slow here; **it has never been enabled**, and the reason is a
malformed systemd override:

```
/etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_VULKAN=1                     ← opening quote, never closed
Environment="OLLAMA_IGPU_ENABLE=1"
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_CONTEXT_LENGTH=32768"
```

`systemctl show ollama -p Environment` therefore reports only
`OLLAMA_IGPU_ENABLE=1`, `OLLAMA_HOST`, `OLLAMA_CONTEXT_LENGTH` — `OLLAMA_VULKAN`
is swallowed into the unterminated value. The running daemon's own startup config
line confirms the effective state: `OLLAMA_IGPU_ENABLE:  OLLAMA_VULKAN:true`
(the empty value is the tell), followed by

> `dropping integrated GPU; to enable, set OLLAMA_IGPU_ENABLE=1
> id=0 library=Vulkan compute=0.0 name=Vulkan0 description="AMD Radeon Graphics
> (RADV REMBRANDT)" pci_id=0000:e5:00.0`

and, separately, ROCm is dropped because Rembrandt is `gfx1035`, outside the
installed `rocblas` targets. Net: **every local run on this box, before and
during this benchmark, is CPU-only**, and `ollama ps` says so (`100% CPU`,
`size_vram: 0`).

I did not fix it: the override is root-owned and `sudo` asked for a password, and
changing host service config is an operator action. Two ways forward, both
Miles's call — (a) correct the override's quoting (the missing closing quote after
`OLLAMA_VULKAN=1`), `systemctl daemon-reload && systemctl restart ollama`; or
(b) for a user-launched server, note that user `norty` is **not** in
`render`/`video`, so even with the flag set it cannot open
`/dev/dri/renderD128` (mode `0660 root:render`, `other::---`). A second daemon
started under `norty` with `OLLAMA_IGPU_ENABLE=1` reported the flag set and still
loaded `size_vram: 0` — consistent with (b), and the reason I could not produce
an iGPU-enabled number at all.

**Consequence for the design:** the iGPU arm of the operator's benchmark is
**unmeasured, blocked on a host fix**, and the CPU numbers in §3 are the number
the tag path would actually pay today.

## 5. What the tag path pays now, for comparison

From Langfuse ClickHouse (read-only), the newest five tag traces
(`AGENT invoke_agent nora`):

| trace | generations | summed wall | slowest call | avg input tok |
|---|---|---|---|---|
| `0796f8d9…` | 7 | 18.5 s | 9.1 s | 28,547 |
| `0edfe5d9…` | 3 | 15.2 s | 9.6 s | 16,957 |
| `4ebd6f03…` | 5 | 13.0 s | 4.8 s | 22,067 |
| `4efba226…` | 5 | 13.8 s | 4.3 s | 22,370 |
| `573071d8…` | 3 | 13.6 s | 8.5 s | 17,351 |

Tag turns per day over the last ten days: 1–41 (median 15), mean wall 5–32 s.
Read latencies in the same store (RETRIEVER spans, 10 days): every context read
is p50 2–186 ms / p95 9–291 ms **except** Honcho's `representation` at p50 162 ms
/ **p95 1,860 ms** (`store.memory.representation`, n=66). The prompt's own split
is the previously measured one: six doctrine sections ≈ **72%** of the request;
the context blocks and transcript ≈ 24%, and the blocks are a minority of that,
already prefix-cached at 95–99%.

## 6. What the numbers say, stated as three claims

1. **A local decision model cannot be on the tag path.** 6 s (0.8B) to 33 s (4B)
   for one plan, against an SLO of p95 ≤ 5 s for the whole turn — and before the
   main call even starts. (Known — measured, §3.)
2. **The plan is not a token saving; it is a token cost.** ~17.6k input tokens
   per plan versus a context-block share of a 22–28k prompt that is already
   cached. The one read a plan could plausibly skip (Honcho's `representation`,
   p95 1.86 s) is smaller than the decision that would skip it. (Known — §3, §5.)
3. **Even the *cheapest* honest shape is a latency tax, not a win.** Asking ONE
   `noul` question ("does this turn need the expensive read?") on the 0.8B model
   is ~185 input tokens and sub-second warm — that is the only shape with a
   chance of paying for itself, and it buys a single boolean, not a plan.
   (Believe — the call is measured; whether the boolean is *right* is not, and
   `04` makes that the gate.)
