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
CPU), and that image embedding was shown only on CPU (GPU image execution
was not verified, which is not proof it is unsupported) through a native
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
ships only if stage 4 lifts recall@5 or MRR@10 by at least 0.05 on a journey
slice, at a p95 added latency on norty, with Nora running, of at most 300 ms
on the tag turn's pre-fetch and 2 s in the Cognition session (the main caller,
[10](10-honcho-audit.md) §2).

## The measure

- **Retrieval:** recall@5 and MRR@10 per slice, per arm, per dimension (768
  and 256 for Gemma; 2048 and 256 for WeMM).
- **Cost on norty:** warm latency per item and per batch, peak RSS, and
  whether it runs on the iGPU, measured with Nora running, not on an idle host.
- **Fail-closed:** an embedding call that errors, returns NaN or returns the
  wrong dimension is an ERROR for that item, never a miss and never a pass.

**Slices and margins.** Each slice has at least 40 labelled queries. The
**journey slices** are the three that map to conversational-recall's journeys:
Cantonese and English text, names and titles, and text → image.

**Pass** (operator ruling 2026-10-15, replacing an absolute +0.10 recall@5
margin): the baseline is the incumbent's best configuration per slice, the
highest recall@5 of bge-m3 alone, trigram + bge-m3 and BM25 + bge-m3. A
candidate replaces bge-m3 only if its hybrid recall@5 on the Cantonese slice
is within 0.05 (absolute) of the baseline's, and on another journey slice its
miss rate is at most half the baseline's: (1 − r) ≤ 0.5 × (1 − r_base).
Anything short of that is a tie, and a tie keeps the incumbent. Why: bge-m3
over the stored readings measured recall@5 0.928 on text → image (stage 1,
`evals/retrieval_bakeoff/results/`), so +0.10 was unreachable there; halving
what is still missed is reachable at any floor and still a large gain. The
rule is `evals/retrieval_bakeoff/decide.py`. A model that wins images but loses Cantonese text can only be a
second, media-only embedder, which is a decision for the operator because it
doubles the index.

**Stage 2, 2026-10-15: EmbeddingGemma 2, Q8_0 and full precision.** Both
decide KEEP_BGE_M3, with Gemma flagged media-only. Q8_0 through Ollama
(`results/20261015T100638Z-stage2-*`): text → image recall@5 1.000, Cantonese
0.7049 against a hold bar of 0.7205. Full precision through
sentence-transformers (`results/20261015T105051Z-stage2-*`; float32, because
the host CPU has no bf16 instructions): identical recall@5 on every slice at
768 and 256, so quantisation was not the miss. Its text vectors match
Ollama's at cosine ≥ 0.9997. Its image vectors match at only 0.95–0.97
(`results/20261015T112457Z-agreement-hf-vs-ollama.json`, 5 texts, 4 images).
The cause is not established. Ollama spends 81–92 tokens per image, against
the 260–266 the HF run spends at the card's default budget of 280. But a
budget of 70 (60–63 tokens) agrees no better (0.95–0.98), so the token count
alone does not explain it. The remaining differences are Q8_0 against float32
and the two runtimes' image preprocessing. Cost on CPU with Nora running:
about 3.9 s per image (p50), 17–31 ms per text, 5.08 GiB (5203 MiB) peak
RSS. That RSS includes the audio encoder: the full 740M parameters were
loaded, and `config_kwargs={"audio_config": None}` would load 440M.

## The target is one multimodal embedder (operator, 2026-10-15)

EmbeddingGemma 2 and WeMM-2B are candidates **because** they embed images and
video (Gemma 2 also audio) in the same space as text. The aim is one
embedder for everything Nora keeps: a message, a photo with no caption, a
reel, a voice note, found by one query. Embedding only media's text
readings would waste the reason they were chosen.

So the order changes:

1. **The text → image slice is built first**, then text → video, then
   text → audio. Step 0 ran 2026-10-15 (00 «Sequence»): 7 of 8 recall cases
   passed at n=1 on existing tools, and the one failure was a time-scoping
   misread, not a record that could not be found. So nothing has yet shown a
   visual vector finding what a stored reading misses, and the multimodal
   target rests on the operator's ruling, not on a measured recall gap. This
   slice is the measurement that would show one.
2. **A multimodal candidate becomes the only embedder** if it holds the
   Cantonese slice within 0.05 of the baseline and wins the text → image slice by
   the pass margin above. Media then gets image and video vectors, embedded
   from the stored bytes (media custody), alongside its reading.
3. **If it wins media and loses Cantonese text**, two embedders mean two
   vector columns and two seeds to fuse. That stays the operator's decision,
   because it doubles the index.
4. **If no multimodal candidate qualifies**, bge-m3 indexes text and media
   readings, which is the floor and works from day one.

**Cost on norty is part of the pass.** Image embedding was measured only on
CPU, at 3–5 s per bounded image (conversational-recall/10). The backfill
embeds every stored media object once, and the live path embeds each new
one off the turn (the embedding worker, [25](25-memory-graph.md) §7). Both
are measured with Nora running.

## Not decided here

- Graph expansion after the search ([25](25-memory-graph.md) §4; hops are
  ruled in, operator 2026-10-15) is measured in its own arm, after stage 3:
  adaptive depth against a fixed two hops on the journey slices. Following
  edges from the seeds is a different retrieval step from reranking them.
- **The media backfill's cost.** Before it runs, the count of stored media
  objects is read and the embed time is extrapolated from the measured
  per-image cost; the backfill then runs under a throttle (a per-hour budget
  sized from that count) with Nora running, never as one unbounded batch.
