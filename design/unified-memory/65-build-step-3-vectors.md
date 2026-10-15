# Build step 3, vectors: the dense channel in the memory index

Status: **plan**, 2026-10-16, approved by the operator the same day ("Yes
proceed", after the bake-off closed at #24). Grounded in master at
`13f299e3`; the migration head is `0105_memory_index`. It finishes
[00](00-intent.md) «Sequence» step 3 ("vectors join when step 2 picks the
embedder") on top of [60](60-build-step-3.md) S1–S5.

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
   clears `embedding` when the node's embedded input changes (its text, or
   its `sha256` for an image), and the worker embeds nodes whose vector is
   NULL, through a partial index. A rebuild therefore equals live, and a
   model change is one statement (clear every vector) followed by the
   backfill.
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
| **V1** | migration `0106_memory_vectors`: `memory_node.embedding halfvec(2048)`, `embedding_model text` (the fingerprint: model file hash plus format version), `embedded_at`, `embed_attempts`, `embed_error`; the HNSW expression index; a partial index on the unembedded embeddable nodes; drop `embedding`. An `embedder` compose service (llama.cpp server, the model files bind-mounted read-only, threads capped so turns keep the CPU). `nora/infra/embedder.py`: the one client, over the one formatter `nora/domain/embedder_input.py`. The embedding worker in the runtime's drain loop: a batch per tick, a failure counted and backed off, never a hot loop. `nora memory rebuild --embeddings-only` and a media throttle sized from the measured media count | the upsert clears a vector when the text or image changes and keeps it otherwise; the worker fills NULLs and records the model; an embedder error leaves the node NULL and counted; a node from another model reads as unembedded; the HNSW index exists and the planner uses it; the bake-off adapter and production format a query, a document and an image identically; schema-parity, row-history and single-head tests |
| **V2** | S5 seeds: **dense** (the two-stage search, scope inside the scan, `hnsw.iterative_scan` so a scoped search still returns its 50) and **exact** (a literal handle, URL, id or listed person's name contained in the node: pinned ahead of every dense hit, operator 2026-10-16). Trigram similarity remains only as the fallback when the embedder is down, and the answer is then `partial`. Coverage: while embeddable nodes are unembedded, the answer is `partial` | the scope suite again with dense seeds; an exact name match ranks above a closer dense neighbour; embedder down gives `partial` with lexical results, never `nothing_found`; an out-of-scope node never returned however near its vector |
| **V3** | the acceptance run: deploy (asked first), backfill, `[memory_index] enabled = true` in a sandbox, `conversational_recall` no worse than 7/8 | the eval read-back |

The reranker test (Qwen3-VL-Reranker-2B against bge-reranker-v2-m3, within
300 ms on a tag turn and 2 s in Cognition) is after V3, and only if V3's
misses are ranking misses.

## 3. Not in this step

HNSW at the real index size is measured after the backfill, not assumed from
the bake-off's 700 items. The iGPU (`norty` is not in `render`) is an
operator action that changes the throttle, not the design. The `ask_memory`
text alignment is its own `nora-tool` PR.
