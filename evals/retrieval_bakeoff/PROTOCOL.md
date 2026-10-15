# Retrieval bake-off: labelling protocol

The rules the labelled set is built and judged by. This file is committed
before any label exists, so a label can be checked against a rule that was
fixed before it was written. Design source:
[unified-memory/20](../../design/unified-memory/20-embedding-bakeoff.md)
(what is compared, the slices, the pass margins) and
[conversational-recall/30](../../design/conversational-recall/30-measurement.md)
(synthetic or public media only, independent labels).

## What is in this directory

| file | holds |
|---|---|
| `PROTOCOL.md` | these rules |
| `set.yaml` | per slice: the pool (item ids) and the queries |
| `media.yaml` | per media item: source, licence, source URL or generator and prompt, sha256, the frozen reading |
| `labels.yaml` | per query: the relevant items, the hard negatives, both labellers' raw sets, and every disagreement with how it was settled |
| `labels.lock` | sha256 of `set.yaml`, `media.yaml` and `labels.yaml` |

The set's loader test (test_bakeoff_set.py, under tests/evals, added with the
labels) enforces the shape: at least 40 queries per
slice, every label resolves to a pool id, every query has at least one relevant
item, and the lock matches the three files. A mismatch fails; it never skips.

## The pool

**Sources.** Two, recorded per item in `media.yaml`:

1. **Real photographs** from Wikimedia Commons whose licence field, read from
   the file's own metadata, is CC0 or public domain. Real photos are the
   default, because a set of generated images would be easier than what a
   group actually shares.
2. **Generated images**, only where a hard-negative pair could not be sourced.
   The generator and the full prompt are recorded. No photoreal faces.

No production media, no room content, no consented private photos in this
slice.

**What is excluded.** A candidate is dropped, before any reading or query is
written, if:

- a person in it is identifiable (a face large and sharp enough to recognise,
  or a name tag); crowds too small or blurred to recognise anyone are kept;
- legible text in it carries a phone number, an email address, a licence
  plate read clearly enough to identify a vehicle's owner, or anything shaped
  like an ID number;
- it is a screenshot, a diagram, an artwork reproduction, or a scan rather
  than a photograph.

**Processing.** Every image is re-encoded once: auto-oriented, all metadata
stripped (EXIF, GPS, XMP, ICC comments), resized to a maximum side of 512 px,
JPEG quality 85. The sha256 of the processed bytes is the item's content hash.
Images are never committed: they live at
`/home/norty/nora-bakeoff/media/<sha256>.jpg` on the deploy host, with a copy
outside every checkout on the development machine. A file that is missing or
whose bytes do not hash to its `sha256` is an ERROR for that item, never a
miss.

## The reading

Each image's reading is what a text-only arm sees for it: Nora's ingest reader
(`MediaComprehender` with the image question and the JSON shape, through the
live `[media.describe.image]` alias at the live effort and token ceiling),
called once per image through the deploy host's model gateway. The three
fields it returns (`title`, `description`, `content`) are frozen in
`media.yaml` with the alias, the model that answered, the prompt's sha256 and
the time.

- A reading is produced once and never re-rolled to get a better one.
- An image whose reading came back unavailable is dropped from the pool
  before labelling; it is not re-read until it succeeds.
- A later reader change does not touch these readings. A new reading is a new
  set version with a new lock.

## The queries

Written by the author after the pool and its readings are frozen, and before
any label exists.

- **What the photo shows.** A query describes the photo's visible content the
  way a member would ask for it in a group chat: "the one with the egg tarts
  on a metal tray", not a title.
- **Never the caption's words.** A query does not reuse the distinctive words
  of the Commons title or description, or the reading's `title`. Generic
  words ("cat", "tram") are allowed, because there is no other way to name the
  thing.
- **Languages.** Cantonese (written colloquial, `係`, `嘅`, `咗`), English, and
  mixed (Cantonese with English nouns, as members write). Each query records
  its language as `yue`, `en` or `mixed`. Each language has at least 10
  queries in a slice.
- **Reading gaps.** Some queries name a visible detail the reading does not
  mention (a colour, a count, a position, the light). They carry
  `reading_gap: true`, set by comparing the query to the frozen reading. These
  are the queries a visual vector could win and a reading-based arm cannot.
- A query is frozen once the second labeller has seen it. A disagreement
  settles labels only; it never edits a query.

## Relevance

**Binary.** A pool item is relevant to a query if a member who asked it would
accept that photo as the one they meant, or as one of them: every constraint
the query states holds visibly in the image. A constraint the query does not
state is not required.

- **The image decides, not the reading.** The reading is an aid for finding
  candidates; where it disagrees with the pixels, the pixels win.
- **Counts, colours, positions, time of day and setting** are constraints when
  the query states them. "Two cups" excludes a photo with three.
- **Ambiguous by sight** (the labeller cannot tell whether a constraint holds)
  is not relevant, and the item is named in the labeller's notes.
- Every query has at least one relevant item. A query whose final set is empty
  is a set defect, fixed by a new pool item or a new set version, never by
  loosening the rule.

## Hard negatives

A hard negative is a pool item that shares the query's subject but fails one
stated constraint: the same dish in another setting, the same street at night
instead of by day, a near-identical second shot that differs in the stated
detail, the same animal in another colour. They are written by the author when
the query is written, labelled not relevant, and recorded per query as
`hard_negatives` so the harness can report how often an arm ranks one first.

A hard negative that the reconciled labels mark relevant loses the tag; it is
never both.

## Labelling

1. **Author (labeller A).** Writes the queries, then marks the relevant items
   and the hard negatives per query, across the whole pool.
2. **Independent labeller (labeller B).** A separate agent session with no
   retrieval tools and no access to the author's notes or labels. It receives
   only this protocol's Relevance section, the pool (ids, image files,
   readings) and the queries, and marks every relevant item per query across
   the whole pool, not a top-k list.
3. **Agreement** is reported before reconciliation: per query, whether the two
   sets are identical, and their Jaccard overlap; over all query-item pairs,
   Cohen's kappa.
4. **Reconciliation.** Each item on which A and B differ is settled by looking
   at the image against the Relevance rules. Each settlement is logged in
   `labels.yaml` with the item, the two raw labels, the outcome and the reason.
5. **The lock.** `labels.lock` is written in the same commit as the
   reconciled labels. Labels change only in a new commit with a new lock, and
   every result cites the lock it was scored against.

## Identifiers and PII

Nothing in this directory may carry a real member's name, a room's content, a
phone number, an email address, or an ID-shaped string. Commons credits are
not copied: the licence field and the file's page URL are the provenance. Every
commit runs `scripts/pii_scan.py`; a finding is fixed by changing the value,
never acknowledged.
