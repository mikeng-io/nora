# Choosing the retrieval stack: a bake-off on Nora's own corpus

Lexical, dense, fused and reranked arms, measured in that order.

Status: **plan**, 2026-10-14. Nothing has been run. This node fixes what is
compared, on what, and what counts as a pass before any model is downloaded.

## Why a bake-off and not a leaderboard

The last choice ([dreamer-agent-loop/24](../dreamer-agent-loop/24-embedding-seam.md)
§7.6) is the precedent. The literature said bge-m3 was superseded. On eight
queries over Nora's own triples (Cantonese questions, mixed script, Cantonese
slang, near-duplicates), bge-m3 scored 7/8 and won, and EmbeddingGemma-300m's
strong MTEB number did not survive the corpus. The corpus decides, not a
published table.

## Candidates

| | bge-m3 (incumbent) | EmbeddingGemma 2 | WeMM-Embedding-2B |
|---|---|---|---|
| released | 2024 | 2026-10-06 | 2026-08 |
| parameters | ~0.57B | 740M (text 270M, vision 170M, audio 300M) | 2B (4B, 9B also exist) |
| inputs | text | text, image, video, **audio** | text, image, video, visual documents; **no audio** |
| dimensions | 1024 fixed | 768, Matryoshka to 512 / 256 / 128 | up to 2048, Matryoshka to 64 |
| context | 8,192 | 8,192 | not stated |
| languages | 100+, measured on Cantonese here | "100+"; Chinese not named | built on a Qwen base; Chinese not stated |
| published | MTEB multilingual (see 24) | MTEB multilingual v2 61.36; MMEB-v2 overall 59.01 | MMEB-v2 77.9 |
| license | MIT | Apache 2.0 | Apache 2.0 code; weights' license not stated on the repository page |
| on norty already | yes (Ollama) | no | community Q4 build probed ([conversational-recall/10](../conversational-recall/10-findings.md)) |

Sources: [EmbeddingGemma 2 model card](https://huggingface.co/google/embeddinggemma-2),
[WeMM-Embedding repository](https://github.com/Tencent/WeMM-Embedding).
Every published number is the vendor's own.

EmbeddingGemma 2 needs task prefixes on text (`task: search result | query: …`
for queries, `title: … | text: …` for documents) and none on media. It must
run in bfloat16 or float32, because float16 "can silently produce NaN or
degraded embeddings" (model card). Those are two ways to lose a comparison
without noticing; both go in the harness, not in a note.

## The hardware

norty: Ryzen 7 PRO 6850U, 8 cores / 16 threads, Radeon 680M integrated
graphics, ~27 GiB RAM shared with Nora and every other service (about 9 GiB
available on 2026-10-14). conversational-recall/10 measured that Ollama runs
**text** embeddings on the iGPU (4 captions in 0.41 s warm, against 2.68 s on
CPU), and that image embedding worked only on CPU through a native
`llama-server`, at 3–5 s per bounded image. WeMM-9B does not fit. EmbeddingGemma 2's text
encoder alone is 270M and can load without the vision and audio towers.

## The set

Synthetic or consented items only. No production message, image or identity
goes into a fixture (conversational-recall/30).

| slice | what it tests | from |
|---|---|---|
| Cantonese and English text | slang (`打風`, bge-m3's one miss), mixed script, romanised Cantonese, near-duplicates | 24's eight queries, re-made synthetic and widened to 40 or more |
| names and titles | a game title, a handle, a place name inside a sentence | new; the lexical channel should win here, and the test is whether vectors **add** anything |
| text → image | a caption-less photo found by a description of what it shows | conversational-recall's dinner journey |
| text → audio | a voice-note clip found by what was said | EmbeddingGemma 2 only; the others need a transcript |
| hard negatives | the same food in someone else's post; last month's plan vs this week's | conversational-recall/30 |

Each query has independently labelled relevant items, written before any model
runs.

## The arms: lexical, dense, fused, reranked

Operator, 2026-10-14: the comparison is the whole retrieval stack, not the
embedder alone. A BM25 plus embedding hybrid is measured, and its result
decides whether a reranker is needed. Four stages, each run only if the
previous one leaves a gap:

| stage | arms | question it answers |
|---|---|---|
| 1. lexical | `pg_trgm` `word_similarity` (live, measured on Cantonese in 24); BM25 | does BM25 beat trigram on this corpus? |
| 2. dense | bge-m3, EmbeddingGemma 2, WeMM-2B | which embedder, at which dimension? |
| 3. fused | best lexical + each dense arm, by reciprocal rank fusion | does hybrid beat either channel alone, per slice? |
| 4. reranked | the best fused arm, then a cross-encoder over its top 20–50 (bge-reranker-v2-m3 for text; Qwen3-VL-Reranker if media is in play) | does reranking fix an ordering failure the fused arm leaves? |

**BM25 and Cantonese.** BM25 scores tokens, and a whitespace tokenizer sees a
Cantonese sentence as one token. The BM25 arm therefore needs a CJK-aware
tokenizer (a segmenter, or character bigrams), and the arm is that
tokenizer plus BM25, measured as one. Postgres BM25 options (ParadeDB
`pg_search`, VectorChord-bm25, Timescale `pg_textsearch`) are candidates
whose availability on the deployed pgvector/pg18 image is checked before the
arm runs. If none fits, the BM25 arm runs out of the database for
measurement only.

**When a reranker earns its place.** It adds a model call per search. It
ships only if stage 4 lifts recall@5 or MRR@10 on a slice a live journey
needs, at a latency the Cognition session (the main caller, [10](10-honcho-audit.md)
§2) and the tag turn can absorb.

## The measure

- **Retrieval:** recall@5 and MRR@10 per slice, per arm, per dimension (768
  and 256 for Gemma; 2048 and 256 for WeMM).
- **Cost on norty:** warm latency per item and per batch, peak RSS, and
  whether it runs on the iGPU, measured with Nora running, not on an idle host.
- **Fail-closed:** an embedding call that errors, returns NaN or returns the
  wrong dimension is an ERROR for that item, never a miss and never a pass.

**Pass:** a candidate replaces bge-m3 only if hybrid recall@5 on the
Cantonese slice is no worse, and it wins at least one other slice that a live
journey needs. A model that wins images but loses Cantonese text can only be a
second, media-only embedder, which is a decision for the operator because it
doubles the index.

## Not decided here

- Whether media gets vectors at all. conversational-recall's smallest-choice
  rule stands: text readings of media are already searchable once the index
  exists, and visual vectors earn their place only where a journey shows the
  text path missing the evidence.
- Graph expansion after the search ([25](25-memory-graph.md), if the operator
  approves it) is measured in its own arm, after stage 3. Following edges from
  the seeds is a different retrieval step from reranking them.
