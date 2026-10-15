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
candidate replaces bge-m3 only if the recall@5 of its best configuration
(dense or fused) on the Cantonese slice is within 0.05 (absolute) of the
baseline's (operator ruling 2026-10-15: the hold is judged on the candidate's
best setup, dense or fused, as `decide.py` does; this sentence said "hybrid"), and on another journey slice its
miss rate is at most half the baseline's: (1 − r) ≤ 0.5 × (1 − r_base).
Anything short of that is a tie, and a tie keeps the incumbent. Why: bge-m3
over the stored readings measured recall@5 0.928 on text → image (stage 1,
`evals/retrieval_bakeoff/results/`), so +0.10 was unreachable there; halving
what is still missed is reachable at any floor and still a large gain. The
rule is `evals/retrieval_bakeoff/decide.py`. A model that wins images but loses Cantonese text can only be a
second, media-only embedder, which is a decision for the operator because it
doubles the index.

**Several passing** (operator ruling 2026-10-16): when more than one
candidate arm passes, the winner is the one with the fewest **total misses**
summed over the three journey slices (text → image, Cantonese, names). A miss
is a query whose first relevant item is not in the top 5, counted from each
result's per-query ranks. A remaining tie goes to the lower dimension, and
a tie on both goes to the arm name in ascending order, so the verdict never
depends on file order. The per-query ranks are needed only here: when
several arms pass, one whose result carries none makes the verdict ERROR,
because its misses are unknown and are never guessed. A single passing arm
needs no count. A candidate arm measured twice on one slice (two runs loaded
together) is also ERROR, because the verdict would depend on which file was
read last; the incumbents and the lexical floors, which every run
re-measures, may recur. This replaced "the
best text → image recall@5, then the lower dimension", which on 2026-10-15
picked WeMM-2B over Qwen3-VL-Embedding-2B for one more image found, despite
0.15 less Cantonese recall@5 (see the measured note below).

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

## WeMM-2B measured (2026-10-15, Q4_K_M)

Stage 2 for WeMM-Embedding-2B ran on all three journey slices
(`evals/retrieval_bakeoff/results/20261015T125057Z-*`). It used the community
**Q4_K_M** GGUF with the BF16 projector, served by llama-server on CPU,
because the `norty` user is not in the `render` group. The input was the
model's own template: no instruction, and `<embedding>` appended once by the
server. Recall@5 for dense WeMM at 2048 / 256 against the incumbent baseline:
text → image 1.000 / 1.000 (baseline 0.928), Cantonese 0.738 / 0.689 (baseline
0.770, the hold bar is 0.720), names 0.896 / 0.896 (baseline 0.896).

`decide()` returns **REPLACE**, with `dense:wemm@2048` as the winner. It holds
Cantonese and wins text → image. At 256, and in every fused WeMM arm,
Cantonese is not held, so those arms are media-only. The Cantonese hold clears
its bar by about one query of 61, and the Q4 vectors agree with the bf16
checkpoint at a mean cosine of 0.976 (min 0.951, 21 items). The verdict is
therefore a Q4 verdict that a full-precision run could reverse. Cost: 3.9 s
p50 and 5.1 s p95 per image, 0.20–0.29 s per text, and a peak RSS of
3.52 GiB (3,691,280 KiB). The peak is the server's lifetime high-water mark
(`VmHWM`), not a per-request figure, and this server had also served the
24-item agreement probe. The first run, superseded because its results
carried no per-query ranks, peaked at 3.16 GiB (3,316,516 KiB).

Full precision, 2026-10-15 (`20261015T123946Z-*`): the HF checkpoint at fp32
(norty's CPU has no bf16 instructions; text-only load, peak RSS 11.10 GiB,
11,370 MiB `VmHWM` in a load-stage probe; bitwise equal to the whole fp32
checkpoint and cosine ≥ 0.99991 to bf16, `20261015T123946Z-wemm-fp32-agreement.json`)
measured the same recall@5 on Cantonese at 2048 (0.738, two offsetting flips)
and on names, so `decide()` over fp32 text plus Q4 text → image still returns
REPLACE for `dense:wemm@2048`. text → image was not re-run at fp32: about 2 h
of CPU for 224 images.

Not acted on (operator, 2026-10-15): the verdict is recorded, but adopting
WeMM waits for the four-way comparison, after Qwen3-VL-Embedding-2B and
jina-v5-omni-small have been measured on the same set.

## Qwen3-VL-Embedding-2B and jina-embeddings-v5-omni-small measured (2026-10-15, fp32)

Two more stage-2 candidates ran on all three journey slices, one model at a
time, through sentence-transformers on norty's CPU in float32 (both
checkpoints are stored bf16, so the cast is exact; the CPU has no bf16
instructions), with `set_dir_clean`, nice 19 and the watchdog on. Scored
results: `evals/retrieval_bakeoff/results/20261015T165508Z-stage2-qwen3vl-*`
(from 4dfe55ae) and `20261015T145806Z-stage2-jina5omni-*` (from fb582e90).

- **Qwen3-VL-Embedding-2B** (`9f2f7e71`, Apache-2.0). The instruction is a
  system turn, then the user turn, the generation prompt and the
  `<|endoftext|>` its tokenizer appends, which is the pooled token. Last
  token, then L2; Matryoshka 64 to 2048. A **query** gets "Retrieve images
  or text relevant to the user's query.", and a document or image gets the
  default "Represent the user's input.". The Hugging Face card names only
  the default. The query instruction is the vendor's own retrieval usage:
  the README of QwenLM/Qwen3-VL-Embedding (@393e2978, its Transformers
  example) and its MMEB text-to-image eval. It is the documented path, not
  something tuned on this set.
- **Recorded variant, not scored:** the first Qwen run
  (`20261015T135707Z-stage2-qwen3vl-*`, from fb582e90) gave queries the
  default instruction too (`QWEN3VL_DEFAULT_INSTRUCTION_VARIANT`). Its arms
  are written as `qwen3vl` in those files. `decide` recognises the variant's
  recorded format and loads them as `qwen3vl-defaultinst`, so they can sit
  beside the scored run without replacing it.
- **jina-embeddings-v5-omni-small** (`950200ab`, **CC-BY-NC-4.0**:
  non-commercial, so this is a measurement only and could not ship under this
  licence). The `retrieval` LoRA adapter. Text is `Query: ` or `Document: `
  plus the text, with no template. A pool image is a document inside the chat
  template, as its sentence-transformers module builds it. Last token, then
  L2; Matryoshka 32 to 1024. Under transformers 5.4.0 every load path
  measured (sentence-transformers, `AutoModel`, and its remote base class's
  own `from_pretrained`, each given `dtype` or `torch_dtype` float32) returned
  the base, 902M parameters, in bf16 beside fp32 LoRA weights, for a reason
  not established; the adapter casts and asserts float32 on every parameter.

The adapter (`adapters.OfficialEmbedder`) decodes every sequence the model
receives and refuses any that is not the format above: 224 images, 860
documents and 184 queries (178 set queries, 6 warm-ups) per run passed. The
M2 guard passed (largest cosine between two different probe images 0.474 for
Qwen, 0.852 for jina, and no identical vectors among the 224). On the
noodles check, img-001 ranks first for "two bowls of noodles", 兩碗麵 and
有兩碗麵嗰張相 at both dimensions in both models, and for Qwen in both its
scored format and the variant.

Recall@5 against the incumbent baseline (every incumbent arm re-measured
identical to stage 2's, in all three runs):

| slice | Qwen @2048 / @256 | Qwen variant @2048 / @256 | jina @1024 / @256 | baseline | bar |
|---|---|---|---|---|---|
| text → image | **0.9855** / 0.9710 | 0.9493 / 0.9348 | 0.9565 / 0.9203 | 0.9275 | win ≥ 0.9638 |
| Cantonese | **0.8852** / 0.8361 | 0.8525 / 0.8525 | 0.8525 / 0.8361 | 0.7705 | hold ≥ 0.7205 |
| names | 0.8958 / 0.8958 | 0.8958 / 0.8750 | 0.8958 / 0.8958 | 0.8958 | win ≥ 0.9479 |

(dense arms; the best fused arm is never higher on text → image or
Cantonese, and on names reaches 0.9167 for the variant and jina, 0.8958 for
the scored Qwen.)

`decide()`:

- incumbent + Qwen: **REPLACE, `dense:qwen3vl@2048`**. It holds Cantonese
  (0.8852, the highest measured) and wins text → image. `dense:qwen3vl@256`
  passes too. No Qwen arm is media-only.
- incumbent + the Qwen variant: KEEP_BGE_M3, which is why the variant is not
  the scored arm. The instruction alone moved text → image from 0.9493 to
  0.9855.
- incumbent + jina: KEEP_BGE_M3, no media-only flag. It holds Cantonese, and
  on text → image stops at 0.9565, about half a query's credit short.
- **Five-way** (incumbent, EmbeddingGemma 2 `105051Z`, WeMM-2B, Qwen, jina),
  the comparison the WeMM note above waits for, re-derived from the
  committed files under the 2026-10-16 ruling (fewest total misses): **REPLACE,
  `dense:qwen3vl@2048`**, with WeMM read either way (Q4 on every slice, or fp32
  text plus Q4 text → image through `rename`). Three arms pass the rule:

  | passing arm | text → image | Cantonese | names | total misses (ti + ca + nm) |
  |---|---|---|---|---|
  | `dense:qwen3vl@2048` | 0.9855 | 0.8852 | 0.8958 | **12** (0 + 7 + 5) |
  | `dense:qwen3vl@256` | 0.9710 | 0.8361 | 0.8958 | 17 (2 + 10 + 5) |
  | `dense:wemm@2048` | 1.000 | 0.7377 | 0.8958 | 21 (0 + 16 + 5) |

  Under the earlier pick (the best text → image recall@5) the same files gave
  `dense:wemm@2048`. On the reading-gap subset the pick is the same, Qwen at
  2048 (8 misses, against 13 and 14).

- **Cantonese by language** at the native dimension (bge-m3 / Qwen / jina):
  en (25) 0.88 / 0.92 / 0.92, mixed (17) 0.82 / 1.00 / 0.94, roman (5)
  0.20 / 0.20 / 0.20, yue (14) 0.71 / 0.93 / 0.86. The gain is mixed script
  and written Cantonese. Romanised Cantonese is 0.20 for every model measured.
- **Time-only subset**: in each of these queries the relevant message and
  one hard negative say the same thing and differ only in when they were
  sent, which no arm sees. Their wording still differs (no two texts are
  identical), so an arm orders the pair by wording, never by the time the
  query asks about, and both can sit in the top 5. Recall@5 there is
  therefore no evidence of reading time. Cantonese (n=5) 0.80 / 1.00 / 1.00;
  names (n=1) 1.00 for all three. Outside it, Cantonese is 0.768 / 0.875 /
  0.839.

**Agreement with each vendor's other path** (`agreement_official.py`, 29
items: `results/20261015T173509Z-qwen3vl-agreement.json` for the scored Qwen
format, `20261015T135628Z-qwen3vl-agreement.json` for the variant, and
`20261015T145737Z-jina5omni-agreement.json`). Qwen's adapter vectors equal
the script its model repository ships (qwen3_vl_embedding.py, given the query
instruction the way the vendor's README passes it) at cosine 1.0000 on every
item, images included, in both formats. jina's text equals its raw-transformers
quickstart at 1.0000, but its images do not: 0.82 to 0.90 at 1024 (mean
0.873). The two paths differ in two ways, and which one causes the gap is
not shown. The quickstart sends `Document: ` and the image run without the
chat template that the sentence-transformers module wraps around an image.
Its `AutoProcessor` also reads `processor_config.json`'s pixel budget
(65,536 to 16,777,216), where the sentence-transformers module sets
262,144 to 1,310,720 (`custom_st.py`). On this set's images (sides 223 to
512 px) that is 112 to 256 vision tokens against 256 to 288, different for
215 of the 224. The card offers both paths. The scored run keeps the
sentence-transformers one (`encode_document`). Both paths rank img-001 first
on all three probes. A text → image score through the quickstart path was
not measured.

**Cost on norty**, with Nora running, one model resident:

| | per image p50 / p95 | per text p50 (batch 16) | peak RSS |
|---|---|---|---|
| Qwen3-VL-Embedding-2B fp32 | 2.48 / 3.27 s | 0.21–0.31 s | 12,771 MiB |
| jina-v5-omni-small fp32 (vision only, no audio tower) | 2.37 / 2.83 s | 0.05–0.08 s | 6,760 MiB |

The scored Qwen run's lowest MemAvailable was 5.67 GiB. One earlier attempt
(started at 12.0 GiB available) was aborted by the watchdog when swap grew
1.31 GB; aborted results are not kept. The variant run went down to
3.51 GiB. Loading the stored bf16 and casting gave bitwise-identical vectors
to loading at fp32 directly, at about the same peak RSS (12.5 against
12.9 GB), so that is not a way to make it fit. Its 2.13B parameters at fp32
are most of the cost. A production Qwen path would
need bf16 hardware or a quantised build, measured again.

## Two-stage 256 > 2048 measured (2026-10-16, Qwen3-VL-Embedding-2B Q4_K_M)

The question was whether the approved index design keeps full-2048 quality
while searching only 256 dimensions. The design stores the full vector as
`halfvec(2048)`, puts HNSW (`halfvec_cosine_ops`) only on
`subvector(embedding,1,256)::halfvec(256)`, takes the top k from that index,
and re-ranks them by full-2048 cosine. On this set it does: no two-stage arm
lost an item that dense@2048 had in its top 5, on any slice, at any k.
Results are in `evals/retrieval_bakeoff/results/20261015T171213Z-stage2-qwen3vl-q4-*`.
The run was at commit 5c650bf6, all three slices, on the Q4_K_M GGUF and
F16 projector (mradermacher at ac21c878). Every prompt's server token ids
equalled the official tokenizer's.

Recall@5 as queries, then MRR@10 in thousandths. Every Cantonese and names
query has exactly one relevant item, and text → image recall@5 is 1.000 in
every row, so each count is recall@5 times the number of queries:

| arm | text → image | Cantonese | names |
|---|---|---|---|
| dense@2048 | 69 of 69, MRR 938 | 54 of 61, MRR 767 | 43 of 48, MRR 821 |
| dense@256 | 69 of 69, MRR 954 | 53 of 61, MRR 717 | 43 of 48, MRR 810 |
| rerank k=20 | 69 of 69, MRR 938 | 54 of 61, MRR 767 | 43 of 48, MRR 823 |
| rerank k=50 | 69 of 69, MRR 938 | 54 of 61, MRR 767 | 43 of 48, MRR 821 |
| rerank k=100 | 69 of 69, MRR 938 | 54 of 61, MRR 767 | 43 of 48, MRR 821 |
| hnsw-rerank k=50 | 69 of 69, MRR 938 | 54 of 61, MRR 767 | 43 of 48, MRR 821 |

- **Re-ranking gives back what 256 alone loses.** dense@256 drops one
  Cantonese query from the top 5 (53/61 against 54/61). Every two-stage arm
  finds the same 54. The per-language rows, the reading-gap rows and the
  time-only rows all equal dense@2048 too. The one difference is names at
  k=20, where MRR@10 is 0.823 against 0.821.
- **The HNSW approximation cost nothing measurable.** The HNSW arm used
  pgvector 0.8.6 on Postgres 18.6 with default `m`/`ef_construction`,
  `hnsw.ef_search` = 50, and `enable_seqscan` off. `EXPLAIN` showed the
  index scanned on every query, and no candidate set came back short. Its
  per-query ranks equal the exact arm's.
- **Latency.** In SQL the two stages take 15 ms p50 and 17 ms p95 per query,
  and the index search alone takes 7.5–7.6 ms p50. These are client wall
  times over loopback, for pools of 224–460 items. Embedding the query
  dominates at 0.36–0.41 s p50 per text on CPU. The server's peak RSS was
  3.0 GiB (3,129,164 KiB).
- **`decide()` returns REPLACE, and a two-stage arm wins.** This is
  re-derived from these files under the fewest-total-misses tie-break
  (operator, 2026-10-16). A miss is a query whose first relevant item is not
  in the top 5. Eight Qwen Q4 arms pass: they hold Cantonese and win
  text → image. Each of the four two-stage arms misses 12 (text → image 0,
  Cantonese 7, names 5). dense@2048 also misses 12, dense@256 misses 13, and
  the fused trigram arms miss 22 and 23. dense@2048 ties the two-stage arms
  on misses, and the next key is the dimension an arm searches. For a
  two-stage arm that is its indexed 256, not the 2048 it re-ranks with
  (`decide._dim`, tested). So a two-stage arm beats dense@2048, and dense@256
  loses on misses. The four two-stage arms then tie on misses and on 256, so
  the arm name decides: `hnsw-rerank:qwen3vl-q4@256>2048/k50` sorts first and
  wins. Nothing in its results separates it from the three exact arms. The
  fused bm25+Qwen arms are flagged media-only. The verdict is the same with
  #22's fp32 `qwen3vl` files loaded alongside: fp32 dense@2048 also misses
  12, and the arm names do not collide.

This is a Q4 verdict on 69 / 61 / 48 queries. It shows that the two-stage
design loses nothing here. It does not show that it would lose nothing at
Nora's real index size, where an HNSW search over many more vectors is a
harder approximation.
