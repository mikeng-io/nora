---
type: design
title: "Current measurements — Radeon 680M, T4 and DeepSeek Flash"
description: "Measured synthetic context-selection probes, explicit cache and warmup exclusions, model limits and current hardware evidence."
tags: [nora, context-plan, benchmark, tev1, laya, strands, deepseek, igpu]
timestamp: '2026-10-07'
edges:
  - {dst: 03-measurement-decision-models.md, rel: informs, provenance: asserted}
  - {dst: 06-current-recommendation.md, rel: informs, provenance: asserted}
  - {dst: 08-experiment-and-handoff.md, rel: informs, provenance: asserted}
---

# Current measurements

## Host and backend

Nora's eventual host is norty: GMKtec NucBox M7 Ultra, Ryzen 7 PRO 6850U,
8 cores/16 threads, Radeon 680M, approximately 27.7 GiB usable RAM. Development is
on the MacBook; host worktrees are for eval, not development.

Ollama 0.35.0's live environment has `OLLAMA_VULKAN=1` and
`HSA_OVERRIDE_GFX_VERSION=10.3.0`. The measured backend is **ROCm**, not Vulkan.
Journal evidence binds ROCm0 to the Radeon and records 25/25 layers offloaded for
Tev1 0.8B and 33/33 for 4B. Resident model sizes were about 0.894 and 4.669 GB.
The isolated CPU service reported zero VRAM. See [backend evidence](evidence/gpu-evidence.txt).

The October 5 node records an unterminated systemd Vulkan environment quote and
CPU-only runs. That result was true of that configuration and workload; it is not
current evidence that this host cannot offload. No live Ollama config or driver
change was made during the October 7 probes.

## Shared synthetic method

Eight generated, pre-labelled situations: English/Cantonese document requests,
English/Cantonese personal recall, ongoing computer work, delegated work, greeting,
and an old-thread case. Four optional-block choices plus transcript depth 8/20/40
make 40 choices. State carries clock, synthetic identity, Culture, Standing,
transcript direction and explicit candidate/row cues.

This is a classifier smoke probe, not Nora replay. Exact-match counts weight an
unneeded inclusion and a required-context omission equally; omissions are more
consequential. There is no independent holdout, calibrated confidence threshold,
real input-distribution accuracy or ASR/TTS contention measurement.

| Arm | Warm request median | Correct choices | Optional choices | Required-block misses |
|---|---:|---:|---:|---|
| Tev1 0.8B Q8 / ROCm 680M | 0.995 s | 30/40 | 24/32 | Cantonese recall |
| Tev1 0.8B / CPU | 2.842 s | 31/40 | 25/32 | Cantonese recall |
| Tev1 4B Q8 / ROCm 680M | 4.103 s | 38/40 | 32/32 | None |
| Laya English ModernBERT / CPU | 4.019 s | 19/40 | 13/32 | Both document requests, Cantonese recall |
| Strands Decider 2B v19 / CPU | about 9 s | 26/40 | 19/32 | None |

Both Tev1 sizes chose too shallow a window for the old-thread case. Tev1 4B also
chose too many rows for a greeting. Optional-block success does not establish
transcript sufficiency. First-load requests are excluded: Tev1 4B's one-question
warmup took 13.06 s.

Strands ran on CPU: installed PyTorch reported neither CUDA availability nor HIP.
Its last two samples overlapped another probe and are not isolated timings.
The **pre-ranker was not tested**, nor Strands on the iGPU. Laya was the English
root checkpoint; these results say nothing about multilingual/specialized Laya.
JEV was not called because the required credential was absent.

A single bundled-choice Tev1 4B shortcut returned 6/8 correct at 1.347 s warm
median, but missed English recall. It was not promoted. Its timing overlapped the
Strands tail. See [local report](evidence/report.txt), [results](evidence/summary.json)
and archived synthetic scripts/results in this evidence directory. Reruns require
the original staging and dependencies described in [probe setup](evidence/README.txt).

## T4: actual CUDA probe, not an A2000/A3000 estimate

The authorized Modal probe used the same Tev1 4B Q8 weight blob, Ollama 0.35.0,
`num_ctx=2050`, synthetic state and five-question requests. Tesla T4 CUDA offloaded
33/33 layers. Eight cases ran three times.

| Timing | Seconds |
|---|---:|
| First warm pass, median | 1.220 |
| Two repeated passes, median | 0.842 |
| All 24 requests, median / nearest-rank p95 | 0.852 / 1.240 |
| First startup request / model download | 85.701 / 41.507 |

The fairer first-pass comparison is 4.103 → 1.220 s, about 3.36× faster. Repeated
identical inputs may benefit from retained context. Measurements are inside the
container and exclude WAN, provisioning and download. Each pass reproduced 38/40
choices, including the same two depth errors. Repeats are not independent cases.
All Modal apps were read back stopped with zero tasks.

A2000 and the requested A3000 LP were **not physically benchmarked**. An exact
A3000 board/vendor link is needed before a precise comparison. CUDA acceleration
makes near-one-second inference plausible, not guaranteed. Shared ASR/TTS and gate
workloads, residency, VRAM, quantization and request length must be measured.
The operator is not currently planning an A3000 LP purchase.
See [T4 report](evidence/modal-t4/report.txt) and [raw result](evidence/modal-t4/result.json).

## DeepSeek V4.1 Flash through the requested route

Actual route: norty → OmniRoute `127.0.0.1:20128/v1` →
`ollamacloud/deepseek-v4.1-flash`. All 24 final responses identified
`deepseek-v4.1-flash`, provider `ollamacloud`, cache `MISS`; completion ID/created
pairs were distinct. Temperature 0, reasoning effort none, output cap 256 tokens,
strict local JSON validation, no repair/retry cascade or tools.

Initial identical repeats returned roughly 20 ms with reused IDs/timestamps. Those
are retained in [cached-repeat evidence](evidence/deepseek-flash/cached-repeat-result.json)
and excluded. Final inputs used an explicitly irrelevant UUID to avoid response
reuse. Prefix caching may still apply. Timing includes the network/provider round
trip and JSON decoding, unlike the inside-container T4 timings.

| Measurement | Result |
|---|---:|
| Fresh-request p50 / nearest-rank p95 | 1.305 / 1.359 s |
| Maximum | 5.831 s |
| Valid plans | 24/24 |
| Optional decisions / depth decisions | 96/96 / 24/24 |
| Required optional omissions / observed reasoning content | 0 / 0 |

There are still only eight distinct situations. This is **not 100% Nora accuracy**.
The explicit short cues make this easier than ambiguous real context. Output used
13–16 reported tokens; no incremental subscription cost was inferred from usage.
See [Flash report](evidence/deepseek-flash/report.txt), [raw responses](evidence/deepseek-flash/result.json)
and its saved probe prompt/script. The replay prompt is deliberately different:
its transcript depth is fixed and it receives actual rendered blocks.

## Other candidates and limits

Tev1 4B is acceptable to the operator. Clef Flash and Nemotron 3 Nano 30B Cloud were
discussed but not benchmarked here. Check the exact Clef quantization, context
memory and Ollama-version requirement before attempting a load; no fit or latency
claim is established for it. Cloud general LLMs are a reasonable experiment route;
Flash is the one actually measured. JEV/Laya credentials/protocols, a small general
local LLM and the Strands pre-ranker remain open alternatives.

The local CPU probe service was terminated, Ollama models unloaded, all standalone
probes completed and Modal apps stopped. No persistent router or live Nora config
change was introduced. Cloud selection adds a request only to accepted occasions
that run the experiment's tag prefill, not automatically to every inbound message.
