# Storage

## The graph is the truth

The tables are the graph. Nothing is projected from another store, and no
store outside the graph holds cognition state. A search index is derived and
rebuildable; it is never the truth.

## Tables

| holds | tables | lifecycle code enforces |
|---|---|---|
| items | one table per source shape (messages, media, presence, fetched pages, watch results, acts, tool outcomes) | append-only |
| aggregates | one table per aggregate family | recomputable: dropping and rebuilding gives the same rows |
| entities | one table | admitted, merged, retired |
| claims | one table | revisions append; a stale base is refused |
| intentions | one table | revisions append; ended only with a closed-by edge and its outcome |
| registry | one row per graph row: its id, kind and unit | written with the row |
| edges | **one table for every edge between any two rows**, citations included | revisions append |

Every table carries the planes under the same names and meanings: identity,
basis, scope, provenance, time, revision, lifecycle. A column specific to a
kind exists only for that kind's lifecycle, such as an intention's conditions
or its verdict. Basis and scope are columns, never implied by which table a row
is in.

**Integrity.** Every kind table references the registry. Each edge references
both endpoints by id and unit through the registry, so the database itself
refuses a dangling edge and an edge across units. Edge types and legal endpoint
kinds are checked by admission; concurrent revision is refused by base.

## Retention and forgetting

- Each item source has a retention horizon, set in configuration. Past it the
  item is deleted, and so are the edges that point at it; a claim that cited
  it keeps its history with a deleted-source citation.
- A forget request from a person hides at once what it names from rendering and
  retrieval; physical deletion follows the source's horizon or the request's
  own terms.
- Aggregates rebuild from what remains, at their watermark.
- Working state ends by its own conditions: an intention's drop-when, deadline
  or review time, never by silent expiry.

## Access

- Retrieval walks the edge table from scoped anchors and loads current rows
  from their tables ([loop](loop.md)).
- The unit is applied before any ranking or walk.
- Only code writes. Model proposals reach a table only through admission.

## Why this shape

- **Per-kind tables, not one generic node table**: each kind's lifecycle stays
  a database constraint (append-only items, a verdict from a fixed set, an
  intention that must have a condition) instead of a JSON shape code has to
  check, and the hot message table keeps its own indexes and write path.
- **One edge table**: a walk crosses kinds without joining per pair of kinds,
  and every edge carries its basis and provenance in one place.
- **Not a projection over existing stores**: a projection keeps two truths;
  the graph exists to remove them.

**The reading that would reopen this**: a kind needs a lifecycle its table
cannot express without per-row JSON; that points at the kinds, not the storage.
