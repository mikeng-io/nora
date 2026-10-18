# Build step 3: the index, `project()` and `ask_memory`, lexical first

Status: **plan**, 2026-10-15, approved by the operator the same day. Grounded
in master at `5892177e`; the migration head then was `0104_external_files`.
Vectors, Persona, note citations, response/act episodes, `mentions` edges and
removing Honcho are not in this step ([00](00-intent.md) «Sequence»).

## 0. Where the code corrected the design

1. **Messages carry no unit.** The unit is `policy.toml`'s, and `nora.infra`
   may not import `nora.policy`. A node stores its `origin_room`
   (`nora.domain.rollup.room_key`) or its `unit` when it belongs to a unit, and
   the read filter is `unit = ANY($units) OR origin_room = ANY($rooms)`, both
   lists from the live policy at read time. `project()` stays pure, and a room
   changing units needs no rebuild. This refines [25](25-memory-graph.md) §3.
2. **No place hierarchy exists.** `presence_places` is flat and circle-scoped,
   and a saved place's existence is itself secret (R-35); geocoder addresses are
   free text; only event venues carry a place id. Step 3 projects venue place
   nodes and no `located_in` edges, so 25 §6a's "near Mong Kok" does not work
   yet. It is a later step of its own (00 «Sequence» step 3a), **built
   2026-10-17**: the areas come from the geocoder's structured address
   components, stored beside each place (`events.venue_district`/
   `venue_city`, `rollup_outing.district`/`city`, `geocode_cache`).
3. **Stays and trips carry no unit or room**, only a subject and a build. Their
   scope is the person: readable only in rooms that list the subject, and only
   where their presence lineage passes (R-31, DEC-222), the check
   `rollup_read.visible_rows` already makes.
4. **`SqlRollupStore.apply` also writes builds still `building`.** Only the
   `current` build has nodes, keyed by the Rollup's natural key
   (`nora.domain.rollup.Citation`), not by row id.
5. **Delete paths 25 §7 missed:** `SqlRecordStore.prune_all` (retention), the
   delete half of the Rollup diff, `replace_places`, and a snapshot restore,
   which leaves the index empty and reports `partial` until a rebuild.
6. **`mentions` needs identity resolution** from `identity.toml`, which a pure
   projector cannot do; deferred to Persona's step. `authored_by` uses the
   already-resolved `messages.person_id`.
7. **`ask_memory` already has a `depth`** (a reasoning-level Literal) and a
   required `person`. Hop depth maps onto it (low 2, medium 3, high and max 4).
   A person-free single verb changes the tool's text: a later `nora-tool` PR
   with its `tool_selection` measurement.
8. **Two Character key schemes.** Only unit-keyed heads are indexed; the old
   room-keyed ones would widen disclosure (`character_key`).
9. **A second note writer**, `nora/subject/knowledge.py` (sandbox seeding),
   projects too, or the eval subject has no index.
10. **AGENTS.md: no piecewise PRs with nothing measured between them.** S1–S4
    are dark (written, read by nothing), behind no flag a user can see;
    behaviour is measured once, at S5.

## 1. Schema: migration `0105_memory_index`

Hand-written DDL (a CHECK and a trigram GIN index). Re-read origin/master's
versions directory before merging; one alembic head.

- **`memory_node`**: `id`, `kind`, `source_id`, `unit`, `origin_room`,
  `persons text[]` (readable only where a room lists all of them),
  `exclude_persons text[]`, `lineage jsonb`, `sha256`, `text`, `at_ms`,
  `state` (`live`, `retired`, `superseded`), `projected_at`.
  `UNIQUE(kind, source_id)`; `kind` CHECKed; a non-hub node must carry a
  scope. GIN `gin_trgm_ops` on `text`; btree on `unit` and `origin_room`.
  No vector column yet: the bake-off's migration adds it and its HNSW index to
  this row, so the scope filter runs inside the scan with no join.
- **`memory_edge`**: `(src_kind, src_id, rel, dst_kind, dst_id)` primary key,
  `provenance`, `created_at`; `rel` CHECKed against 25 §2; no foreign keys,
  every hop joins `memory_node`, so a dangling edge drops itself.
- **`memory_projection_queue`**: `(kind, source_id)`, `first_failed_at`,
  `attempts`, `last_error`.
- **`memory_index_state`**: one row, `status` (`never`, `building`,
  `complete`), `started_at`, `finished_at`, `unprojected`.

All four are `NOT_RESTORED` (`nora/infra/sql/history.py`): derived, rebuilt by
`nora memory rebuild`.

## 2. The projector: `nora/infra/sql/memory_index.py`

`project(conn, kind, source_id)` re-reads the source row on the caller's
connection and upserts its node and outgoing column edges, or drops them when
the row is hidden or gone. `reproject_media(conn, sha256)` and
`drop(conn, kind, ids)` cover media and deletes. A delete removes the node and
the edges its own projection writes; an edge another entry owns into it (a
memo's `cites` of a hidden message) stays dangling, exactly as a rebuild
writes it, and drops itself at the read's join (S4, so live equals rebuild). `INDEXED_WRITES` maps each
table to its kind, for the architecture test. Every call runs in a savepoint
(`begin_nested`): a failure queues the row and counts
`nora_memory_projection_total{kind,outcome="queued"}`, and never undoes the
store's own write.

## 3. Slices

| slice | builds | proves it |
|---|---|---|
| **S1** | the migration; `project()` in `audit.py` (`append`, `append_edit`, `hide_message`, `set_person_id`, `set_delivery_outcome`, `prune_all`); attachment nodes `(message, sha256)` from `media_json`, with `attached_to`, `shared_by`, the reading copied in; `media_objects.comprehend` and `media_descriptions.remember` re-project | `tests/infra/test_memory_projection_record.py` (ingest, edit, hide, prune, reading before and after ingest, a forced failure that queues while the message stays); `tests/architecture/test_every_indexed_kind_is_projected.py` (every write to an indexed table calls the projector, or is listed with a reason; only `memory_index.py` writes the index); row-history, schema-parity and single-head tests |
| **S2** | notes, memos, lessons, events, culture/Character (unit-keyed) and sandbox seeding; `cites`, `about`, `supersedes`, `tracks`; person, room, unit, Nora and venue place nodes | one test per store |
| **S3** | the analytics process: `apply` (current build only), `switch_build`, `commit_fold`, `relink_*`; `during` edges; the queue drained each tick | `test_rollup_builds` extended: a switch replaces episode nodes, a building build has none |
| **S4** | `nora memory rebuild` (also the backfill): `building`, batched projection, a final pass over hidden and deleted sources, drain, `complete`; a drain loop in the runtime | a rebuild equals live projection, and is idempotent; a restored snapshot reads `never` |
| **S5** | the retrieval service (scope from policy, lexical seed, hop-by-hop expansion with scope on every node, the hub rule, the listed-room rule, two hops by default and four at most, an evidence packet with paths, screened for identifiers; `ok`, `nothing_found`, `unavailable`, `partial`); `ask_memory` behind `[memory_index] enabled = false` | an integration scope suite: another unit two hops away unreachable; a same-unit path crossing another unit cut; the in-unit control reached with its path; no fan-out through a person node; talk about Kit in a room without Kit not followed to Kit; a hidden message never a hit; a location-off room gets no stay. Flag off calls Honcho, flag on does not. A DEC row for the scope rule. Then `conversational_recall` with the flag on, no worse than 7/8 |
