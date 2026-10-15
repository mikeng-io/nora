# Build step 3, vectors: the dense channel in the memory index

Status: **plan**, 2026-10-16, approved by the operator the same day ("Yes
proceed", after the bake-off closed at #24). Grounded in master at
`13f299e3`; the migration head is `0105_memory_index`. It finishes
[00](00-intent.md) «Sequence» step 3 ("vectors join when step 2 picks the
embedder") on top of [60](60-build-step-3.md) S1–S5. V1 merged as #30; V2
was built 2026-10-16, and §4 records what it decided.

## 0. What the bake-off fixed

From [20](20-embedding-bakeoff.md) and PRs #22 and #24:

- **Embedder: Qwen3-VL-Embedding-2B**, the Q4_K_M GGUF with its f16
  projector, served by llama-server with `--embedding --pooling last`.
  Measured at Q4: text → image 1.000, Cantonese 0.885, names 0.896 recall@5;
  peak RSS about 3.06 GB; about 0.4 s per text query and 3.4 s per image on
  norty's CPU.
- **Input format is the model's, and is fixed.** A query carries the system
  instruction "Retrieve images or text relevant to the user's query."; a
  document or image carries "Represent the user's input.". The exact
  rendering is `evals/retrieval_bakeoff/qwen_llama.py`'s
  `QwenLlamaServerEmbedder`, which the bake-off verified token by token.
  Production imports one formatter, and the bake-off adapter uses the same
  one, so the two cannot drift. It is `nora/domain/embedder_input.py`, in the
  domain because `evals/` may import no other runtime layer (V1, 2026-10-16).
- **Store 2048, index 256.** `halfvec(2048)` on the node; an HNSW expression
  index on `subvector(embedding, 1, 256)::halfvec(256)` with
  `halfvec_cosine_ops`; the search takes the index's top 50 and re-scores
  them on the full 2048. Measured in #24: no query lost against an exact
  2048 search, about 15 ms p50 in Postgres. pgvector's HNSW limit is 4000
  dimensions for `halfvec` (live: 0.8.6 on PG18).

## 1. Where the plan corrects the design

1. **No separate embedding queue.** [25](25-memory-graph.md) §7 named "the
   embedding worker"; the queue is the node itself. `project()`'s upsert
   clears `embedding` when the input the vector was made from changes, and
   the worker embeds nodes whose vector is NULL, through a partial index. The
   node records that input (`embed_input`: its `text`, or the `bytes` its
   `sha256` names), so an image embedded from its reading because its bytes
   were not held is cleared by a new reading, and one embedded from its
   bytes is not (review of #30). A rebuild therefore equals live, and a
   model change is one statement (clear every vector of another
   fingerprint, which the worker runs once per process before its first
   batch) followed by the backfill.
5. **No node escapes the worker's bookkeeping, and none is retried
   forever** (review of #30). Any failure of one node, an unexpected
   exception included, is recorded on it and backed off exponentially; after
   8 attempts it is not picked again until its input changes or the operator
   clears vectors, and it is counted `given_up`. Every image is decoded by
   the worker (Pillow), bounded (refused above 64 Mpx, downscaled to 1920
   image tokens' worth) and sent as a PNG, so a crafted file fails as that
   node's rejection and never inside the shared server.
6. **The served model is checked by file name, not by hash.** The
   fingerprint is the configured `<gguf file>@<sha256 prefix>` plus the
   formatter's version; the worker embeds only while the server's /props
   `model_path` names that file. llama-server exposes no file hash, so the
   prefix is the operator's record of what was hashed, not a check.
7. **The server runs the bake-off's flags exactly** (`-c 2048 -b 2048 -ub
   2048 -t 4 -ngl 0 --parallel 1`), with `LLAMA_MEDIA_MARKER` pinned. A
   pooled embedding is one micro-batch, so a text is counted with the
   server's /tokenize and cut to fit 2000 tokens; a 5xx from a server whose
   /health answers 200 is the input's fault (rejected, per node), from one
   that does not, the server's (unavailable, the tick stops).
2. **The old `embedding` table (0044) is dead.** Nothing outside its schema
   reads or writes it (grep, 2026-10-16). The migration drops it, with its
   `EMBEDDING_DIMENSIONS` constant (the name now holds 2048), so there is one
   place vectors live.
3. **An attachment that is an image is embedded from its bytes**, not from
   its reading; text → image is what the bake-off measured. Video, audio and
   documents are embedded from their reading's text. A sticker or a GIF is
   an image.
4. **Hubs are not embedded** (they never seed, [60](60-build-step-3.md) S5),
   and neither is a kind 25 §7 marks lexical only (stays, trips, outings,
   episodes, disputes, feedback).

## 2. Slices

| slice | builds | proves it |
|---|---|---|
| **V1** | migration `0106_memory_vectors`: `memory_node.embedding halfvec(2048)`, `embedding_model text` (the fingerprint: model file hash plus format version), `embedded_at`, `embed_attempts`, `embed_error`, `embed_input` (§1.1); the HNSW expression index; a partial index on the unembedded embeddable nodes, in the pick's order; drop `embedding`. An `embedder` compose service (llama.cpp server with the bake-off's flags, §1.7, the model files bind-mounted read-only, threads capped so turns keep the CPU). `nora/infra/embedder.py`: the one client, over the one formatter `nora/domain/embedder_input.py`. The embedding worker in the runtime's drain loop: a batch per tick, a failure counted and backed off, given up after 8 (§1.5), never a hot loop. `nora memory rebuild --embeddings-only` and a media throttle sized from the measured media count | the upsert clears a vector when its recorded input changes and keeps it otherwise; a poisoned image, an unexpected exception and an oversized text are each one node's recorded failure; a restarted server's new marker still embeds; the worker fills NULLs and records the model; an embedder error leaves the node NULL and counted; a node from another model reads as unembedded; the HNSW index exists and the planner uses it; the bake-off adapter and production format a query, a document and an image identically; schema-parity, row-history and single-head tests |
| **V2** | S5 seeds: **dense** (the two-stage search, scope inside the scan, `hnsw.iterative_scan` so a scoped search still returns its 50) and **exact** (a literal handle, URL, id or listed person's name contained in the node: pinned ahead of every dense hit, operator 2026-10-16). Trigram similarity remains only as the fallback when the embedder is down, and the answer is then `partial`. Coverage: while embeddable nodes are unembedded, the answer is `partial` | the scope suite again with dense seeds; an exact name match ranks above a closer dense neighbour; embedder down gives `partial` with lexical results, never `nothing_found`; an out-of-scope node never returned however near its vector |
| **V3** | the acceptance run: deploy (asked first), backfill, `[memory_index] enabled = true` in a sandbox, `conversational_recall` no worse than 7/8 | the eval read-back |

The reranker test (Qwen3-VL-Reranker-2B against bge-reranker-v2-m3, within
300 ms on a tag turn and 2 s in Cognition) is after V3, and only if V3's
misses are ranking misses.

## 3. Not in this step

HNSW at the real index size is measured after the backfill, not assumed from
the bake-off's 700 items.

**No chunking now.** Measured 2026-10-16 on production lengths: notes at most
929 characters, lessons 131, messages at most 1798, media readings p99 1295
and at most 2435, all inside one embedding or close to it. Chunking (a node
per section, `part_of` its parent, the title prefixed) is designed when
artifacts or long transcripts join the index. Its trigger is
`nora_memory_embed_truncated_total{kind}`: the worker counts every text the
embedder cut to fit its micro-batch (V2), on top of `ok`. The iGPU (`norty` is not in `render`) is an
operator action that changes the throttle, not the design. The `ask_memory`
text alignment is its own `nora-tool` PR.

## 4. V2 as built (2026-10-16)

What V2 decided where the plan above left room (DEC-315; the code is
`nora.infra.sql.memory_read` and `nora.agent.memory_search`):

1. **Three seeds, in order.** Exact hits are seeded first and **pinned**:
   every one ranks ahead of every other hit in the answer (operator,
   2026-10-16). Then dense, the question embedded once with the query
   instruction. The trigram seed runs only with no query vector. This
   replaces 25 §4 step 2's «fused by reciprocal rank»: there is nothing to
   fuse while lexical is the fallback, and V3 measures whether that loses a
   case.
2. **Exact is narrow.** It covers a URL (with a scheme or `www.`), a handle
   (`@` plus two or more handle characters, never inside an e-mail
   address), an id-like token (five or more letters and digits holding
   both, or six or more digits), and the name or alias of a person the
   reading room lists. Each must be literal in the question. An ASCII term
   matches only on its own, so `Kit` is not in `kitchen`. Any other script
   matches as a substring, because Cantonese has no spaces. A reference
   inside a longer one is dropped (`Kit` inside `Kit Chan`), as is a name
   under two characters. Ordinary words are never exact. At most 10 exact
   seeds (`EXACT_LIMIT`), the nearest to the question first. Names reach the
   search from the tool, from the room's resolved roster filtered to the
   persons the policy lists, so `nora.infra` reads no policy.
3. **Dense in one statement.** Fifty candidates come from the HNSW index on
   the leading 256 dimensions, with the scope filter and the model
   fingerprint in the same scan. They are re-scored on the full 2048 cosine,
   and the best 20 are kept. `hnsw.iterative_scan = relaxed_order`, so a
   room whose unit holds few of the nearest vectors still gets candidates.
   `hnsw.ef_search = 100`: the scan needs at least 50 or it returns fewer
   than 50, and the bake-off's 50 was measured on 460 items, so this is
   twice that, with no measurement at the real size yet (V3). A test
   asserts the plan scans `idx_memory_node_embedding_256` with the scope as
   its filter.
4. **Score.** A dense seed's relevance is its cosine. An exact seed's is 1
   (literal containment, which is what the lexical seed also scores 1). The
   walk's floor stays at 0.1. **Consequence:** a dense search returns its
   nearest even when nothing is relevant, so `nothing_found` comes only
   from an empty scope or vectors orthogonal to the question. A higher
   floor needs the cosine distribution of real hits and misses, which V3
   reads off the backfilled index. A floor set above a true hit's cosine
   would turn «found» into «you never said that».
5. **Statuses.** The answer is `partial` in three cases:
   - the seeds were lexical. That happens when `[memory_index] embed` is
     off (so `enabled` on with `embed` off answers `partial` every time),
     when the embedder failed, timed out (10 s per call, in a turn) or is
     serving another GGUF file than the configured one (checked per query
     against /props, the worker's rule), or when no node holds a current
     vector yet;
   - live embeddable nodes still wait for a vector of the current model.
     This includes nodes holding another model's vector and failing nodes
     in backoff;
   - the index is incomplete, as before.

   A node the worker **gave up on** (8 attempts) does not count, or one
   poisoned file would hold every answer `partial` for good.
   `rebuild --embeddings-only --dry-run` and the embed counter report those
   nodes. A failing node can hold answers `partial` for about a day, until
   it is given up. Coverage is one count per search over the node table.
6. **Episodes are lexical only** (§1.4), so with dense seeds a stay, trip or
   outing is reached only through an edge or as a named person's `about`
   seed, never by its own text. The scope suite runs both ways.
