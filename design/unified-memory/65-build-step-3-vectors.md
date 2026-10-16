# Build step 3, vectors: the dense channel in the memory index

Status: **plan**, 2026-10-16, approved by the operator the same day ("Yes
proceed", after the bake-off closed at #24). Grounded in master at
`13f299e3`; the migration head is `0105_memory_index`. It finishes
[00](00-intent.md) «Sequence» step 3 ("vectors join when step 2 picks the
embedder") on top of [60](60-build-step-3.md) S1–S5. V1 merged as #30; V2
was built 2026-10-16, and §4 records what it decided, as amended by the
independent review of #31 the same day.

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
   the worker (Pillow), bounded (refused above 64 Mpx, downscaled to 256
   image tokens' worth, the largest the bake-off measured; 1920 tokens took
   about 60 s an image and 6.5 GiB live on 2026-10-16, and `FORMAT_VERSION` 2
   re-embeds what was sent larger) and sent as a PNG, so a crafted file fails as that
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
| **V2** | S5 seeds: **dense** (the two-stage search, scope inside the scan, `hnsw.iterative_scan` so a scoped search still returns its 50) and **exact** (a literal handle, URL, id or listed person's name contained in the node: the first five pinned ahead of every dense hit, operator 2026-10-16; §4.1). Trigram similarity remains only as the fallback when the embedder is down, and the answer is then `partial`. Coverage: while embeddable nodes are unembedded, the answer is `partial` | the scope suite again with dense seeds; an exact name match ranks above a closer dense neighbour; embedder down gives `partial` with lexical results, never `nothing_found`; an out-of-scope node never returned however near its vector |
| **V3** | the acceptance run: deploy (asked first), backfill, `[memory_index] enabled = true` in a sandbox, `conversational_recall` no worse than 7/8; `tool_selection --memory-index` re-run on the built index (on an empty index every `memory_search` answers `partial` with no items, so sibling calls after it cannot be judged, PR #32). Then the **adaptive cut** (operator, 2026-10-16): from the cosine spread of labelled hits and misses on the built index, set the dense relevance floor and a score-gap stop, and replace `RESULT_LIMIT = 10` with a token budget (about 2,000 tokens of packet text, not the embedder's 2,000-token input cut of §1; at least one item before the identifier screen, which can still withhold it); `PINNED_LIMIT`, now derived from `RESULT_LIMIT`, becomes its own value (a count or a share of the budget) so half the answer still goes by score; the walk's floor and the `about` seed's 0.6 are set in the same step. `RESULT_LIMIT` is a temporary cap until then | the eval read-back; the cosine spread recorded here |

**V3's harness (2026-10-16).** Two things stood between V3 and a reading.
The eval runs on the deploy host, where `embedder:8080` does not resolve and
an internal network publishes nothing; and a sandbox's index was empty (the
memory tables are derived, never restored). The embedder now also joins
`embedder_host`, a network of its own carrying a loopback publish
(`127.0.0.1:8092`), and a graph-surface subject queries it there. Each
graph-surface sandbox is projected by `rebuild` and adopts the eval
source's vectors where the embedded input is identical; the run refuses
before any draw on a source under 0.95 coverage or an embedder serving
another file, and a draw under 0.95 of the source's coverage is an ERROR
(`design/evaluation/11-the-sandbox-rebuild.md`, the 2026-10-16 amendment).
The subject never holds the worker. So the order is: backfill, `make
eval-source`, then the two runs.

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
embedder cut to fit its micro-batch whose vector it stored (V2), on top of
`ok`. The iGPU (`norty` is not in `render`) is an
operator action that changes the throttle, not the design. The `ask_memory`
text alignment is its own `nora-tool` PR (done as `memory_search`, DEC-316, §5).

## 4. V2 as built (2026-10-16)

What V2 decided where the plan above left room (DEC-315; the code is
`nora.infra.sql.memory_read` and `nora.agent.memory_search`):

1. **Three seeds, in order.** Exact hits are seeded first, and the first
   `PINNED_LIMIT` of them (5, half of `RESULT_LIMIT`), the most terms held
   and then the nearest, are **pinned**: each ranks ahead of every other hit
   in the answer (operator, 2026-10-16). An exact hit past those ranks by
   its cosine to the question, as a dense hit does. The review of #31 found
   that with every exact hit pinned, ten literal matches of one name filled
   all ten slots and even a dense hit at cosine 1.0 never showed; half the
   answer now always goes by score. Then dense, the question embedded once
   with the query instruction. The trigram seed runs when there is no query
   vector, and beside the dense seed over the nodes the worker gave up on
   (§4.5). This replaces 25 §4 step 2's «fused by reciprocal rank»: there is
   nothing to fuse while lexical is the fallback, and V3 measures whether
   that loses a case.
2. **Exact is narrow.** It covers a URL (with a scheme or `www.`), a handle
   (`@` plus two or more handle characters, never inside an e-mail
   address), an id-like token (six or more digits, or five or more letters
   and digits holding a letter and at least three digits, and not a number
   with a short suffix such as a time, an ordinal or a measure, alone or
   joined by `-`: `ZX4471` is one; `COVID-19`, `5pm-7pm`, `100th`,
   `iPhone15` are not), and a name of a person the reading room lists: the
   name Nora calls them, their first name, their full name and their
   aliases, never the bare last name (a family name pins the whole family,
   and a romanised one is often two letters). Each must be literal in the
   question, which is NFKC-normalised first (a full-width `Ｋｉｔ` is `Kit`).
   A URL, a handle or an id matches in any case; a name only as written, so
   a person named Will does not pin every «will», and a name that is also a
   common word written capitalised (a month, a modal, `_COMMON_NAMES`) is
   never exact. A Latin term (ASCII or accented Latin) matches only on its
   own, so `Kit` is not in `kitchen` and `Zoë` is not in `Zoëy`; any other
   script matches as a substring, because Cantonese has no spaces. A
   reference inside a longer one is dropped (`Kit` inside `Kit Chan`), as
   is a name under two characters. Ordinary words are never exact. At most
   10 exact seeds (`EXACT_LIMIT`), the nearest to the question first. Each
   term kind takes a form `idx_memory_node_text_trgm` serves (a regular
   expression, or `LIKE`/`ILIKE`), so the exact read is a bitmap OR of index
   scans, never a scan of the table. Names reach the search from the tool,
   from the room's resolved roster filtered to the persons the policy lists,
   so `nora.infra` reads no policy.
3. **Dense in one statement.** Fifty candidates come from the HNSW index on
   the leading 256 dimensions, with the scope filter and the model
   fingerprint in the same scan. They are re-scored on the full 2048 cosine,
   and the best 20 are kept. `hnsw.iterative_scan = relaxed_order`, so a
   room whose unit holds few of the nearest vectors still gets candidates.
   `hnsw.ef_search = 100`: the scan needs at least 50 or it returns fewer
   than 50, and the bake-off's 50 was measured on 460 items, so this is
   twice that, with no measurement at the real size yet (V3).
   `hnsw.max_scan_tuples = 50,000`, set explicitly: pgvector's default
   (20,000) is under the live index's roughly 46,000 embeddable nodes, so a
   unit holding few of the nearest vectors could be cut off with candidates
   unvisited. The cost falls on a scope holding fewer than 50 vectors, whose
   scan walks the whole graph every search; V3 times it.
   `nora_memory_dense_short_total` counts every search whose dense seed
   returned fewer nodes than it asked for while its scope held more. A test
   asserts the plan scans `idx_memory_node_embedding_256` with the scope as
   its filter.
4. **Score.** A dense seed's relevance is its cosine. An exact seed's is 1
   (literal containment, which is what the lexical seed also scores 1). The
   walk's floor stays at 0.1. **Consequence:** a dense search returns its
   nearest even when nothing is relevant, so `nothing_found` comes only
   from an empty scope or vectors orthogonal to the question. A higher
   floor needs the cosine distribution of real hits and misses, which V3
   reads off the backfilled index. A floor set above a true hit's cosine
   would turn «found» into «you never said that». **The `about` seed's
   fixed 0.6** (`PERSON_SEED_SCORE`, a node `about` the named person) is
   unchanged and may outrank a true dense hit, whose cosine is typically
   0.3 to 0.6. It is a V3 measurement item beside the floor: read the
   cosines of real hits off the backfilled index, then set both.
5. **Statuses.** The answer is `partial` in three cases:
   - the seeds were lexical. That happens when `[memory_index] embed` is
     off (so `enabled` on with `embed` off answers `partial` every time),
     when the embedder failed, timed out (10 s per call, in a turn) or is
     serving another GGUF file than the configured one (checked per query
     against /props, the worker's rule), or when no node holds a current
     vector yet;
   - live embeddable nodes **in the search's scope** still wait for a
     vector of the current model. This includes nodes holding another
     model's vector and failing nodes in backoff. A node waiting in another
     unit does not count (review of #31: one pending node anywhere made
     every search `partial`);
   - the index is incomplete, as before.

   A node the worker **gave up on** (8 attempts) does not count, or one
   poisoned file would hold every answer `partial` for good. No dense seed
   reaches it, so while the scope holds one, the trigram seed also runs
   over the given-up nodes: a confident `nothing_found` for words that are
   there was the review's finding. Nor does an attachment that failed as
   `empty` (no reading, and no image bytes to embed), which waits for its
   reading, not for the worker. Only a failure of the input itself
   (`rejected`, `empty`, `error`) counts toward the 8: an unavailable
   embedder or blob store backs the node off but never past 7 attempts, so
   an outage gives up on nothing. `rebuild --embeddings-only --dry-run` and
   the embed counter report given-up nodes. A failing node can hold answers
   `partial` for about a day, until it is given up. Coverage is one count
   per search over the scope's nodes.

   The query embedding has one 10 s deadline for every call it makes
   (/props, /embedding, and /health on a 5xx), not 10 s per call, which
   could reach 25 s in a turn. The /props model check is trusted for 60 s,
   so a search makes one call, not two; for at most a minute after an
   operator swaps the model, a question may be embedded by the new model
   and compared with the old one's vectors.
6. **Episodes are lexical only** (§1.4), so with dense seeds a stay, trip or
   outing is reached only through an edge or as a named person's `about`
   seed, never by its own text. The scope suite runs both ways.

## 5. One memory surface, and its verb (2026-10-16, DEC-316)

The index is read through ONE tool, `memory_search(query, person?, since?,
until?, kinds?)`, and only on the memory graph's tool surface.
`[memory_index] enabled` selects the surface wherever tools are built (a
tag, a fire turn, the Cognition session, every Dreamer adaptor, the
sub-agent worker); the two never sit side by side.

| surface | flag | memory verbs offered |
|---|---|---|
| Honcho (production, the rollback) | off | `ask_memory`, `search_memory`, `search_memory_workspace`, `who_said`, plus `about`, `remember`, `load_transcript` |
| graph | on | `memory_search`, plus `about`, `remember`, `load_transcript` |

- **No Honcho behind `memory_search`.** It is the retrieval service of §4
  and nothing else. `ask_memory` no longer reads the index: with the flag on
  it is not offered.
- **`about`, `remember` and `load_transcript` stay on both.** The first two
  are the profile read and the memory write, which Persona (steps 5 to 8)
  and step 9 replace; they are not search. `load_transcript` is the in-room
  scroll-back.
- **`who_said` is deprecated.** It is absent from the graph surface:
  `memory_search` names who said each hit, and `load_transcript` reads
  around it. It is deleted with the rest of the Honcho surface at step 9.
- **Why not both.** With `memory_search` beside Honcho's `search_memory`, a
  `tool_selection` run called `search_memory` in 6 of 8 new cases and
  `memory_search` in none (n=1): two names sharing both tokens, ADR 0004's
  near-name pattern.
- **Narrowing.** `person`, `since` and `until` narrow every read inside the
  scan (DEC-313's filter, ANDed). `kinds` narrows the seeds and the answer,
  not the hops (item 6 above: an episode has no vector, so `places` is
  reached from what was said during one). A person this room's policy does
  not list is `unavailable`, not a false `nothing_found`.
- **Reach.** The culture adaptor's `memory_search` reads its own room
  alone: a room's culture is not written from another room's speech, as its
  Honcho `search_memory` read only its own session. Every other occasion,
  the character-person session included, reads the unit under the
  listed-room rule.
- **One source.** The registry selects the surface (it holds a
  `MemorySearch` or not), builds only that surface, and withholds the other
  from the capability digest and from Cognition's read surface.
- **Measuring it.** `python -m evals tool_selection --memory-index` composes
  the subject on the graph surface; without the flag the subject is the
  deployment's, whichever surface its config selects, and the header says
  which. A case that routes on one surface only says so
  (`metadata.memory_surface`), the other arm skips it, and a subject composed
  on the other surface is refused per case.
- **Before the flag goes on.** Per-pass search capacity is lower on the
  graph surface: a tag or fire turn had `ask_memory` 4 + `search_memory` 4 +
  `who_said` 4 calls and has `memory_search` 4; the culture and
  character-person passes now spend it inside `max_memory_calls`. Measure it
  first.
