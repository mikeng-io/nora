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
| `set.yaml` | per slice: where its items live (`items`), its labels file (`labels`), the pool (item ids) and the queries |
| `media.yaml` | per media item: source, licence, source URL or generator and prompt, sha256, the frozen reading |
| `texts.yaml` | per text item (the `cantonese` and `names` slices): when it was sent, its language and its text |
| `labels.yaml` | the `text_image` slice, per query: the relevant items, the hard negatives, both labellers' raw sets, and every disagreement with how it was settled |
| `labels_text.yaml` | the same for the `cantonese` and `names` slices, which share their labellers |
| `labels.lock` | sha256 of `set.yaml`, `media.yaml`, `texts.yaml`, `labels.yaml` and `labels_text.yaml` |
| `run.py` | the harness: loads the locked set, runs each arm fail-closed, scores it, writes one aggregates-only result |
| `adapters.py` | one embedder per serving path (Ollama, llama-server, sentence-transformers), with each model's prefix and dtype rule |
| `decide.py` | the decision rule over fused results (design/unified-memory/20 «Slices and margins») |
| `results/` | one JSON per run: aggregates and provenance, no vector and no text |

`tests/evals/test_bakeoff_set.py` enforces the shape: the three journey slices
(`text_image`, `cantonese`, `names`) exist, at least 40 queries per slice,
every label resolves to an id in its own slice's pool, every media and text
item is in a pool, every query has at least one relevant item and at least one
hard negative (or a stated reason the pool has none), every
`hard_negative_fails` key is one of the query's hard negatives, each labels
file covers exactly the queries of the slices that name it and no withdrawn
query is back in the set, no two queries share a text, a text slice's `as_of`
equals its labels file's, each labeller's `raw_sha256` and the reported
agreement recompute from that file's committed raw sets, every
`time_only_queries` entry is a live query and one of its hard negatives, and
the lock matches the five files. A mismatch fails; it never skips.

## Blind by construction

The readings are the input of the arm the bake-off measures against (the floor:
lexical or bge-m3 over a reading). A query written, or a label judged, with the
readings in view leans toward words the reading already contains, and the floor
then ties every candidate (measured on this set's first version, 2026-10-15: a
cross-encoder over readings alone reached recall@5 0.954, so no arm could win
by the 0.10 margin). So:

- **Queries are written blind.** The query author is a fresh session that
  sees only the image files and their pool ids: never a reading, a Commons
  title or description, `media.yaml`, or an earlier version of the set.
- **Labels are judged blind.** Both labellers see only the images, the pool
  ids and the queries. Neither sees a reading.
- **No selection by a floor.** Queries are not chosen, kept or dropped by how
  a reading-based arm scores them, and the author is not told to aim at what
  readings miss; either would bias the set against the incumbent. A floor run
  over the readings is reported with the set as information only.
- **No hand-judged gap flag.** Which queries a reading cannot answer is
  derived by the harness from a reading-only lexical run, not judged by a
  person who has read the readings.

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
`/home/<deploy-user>/nora-bakeoff/media/<sha256>.jpg` on the deploy host, with a copy
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

Written blind (above) after the pool and its readings are frozen, and before
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
- A query is frozen once the second labeller has seen it. A disagreement
  settles labels only; it never edits a query.

## Relevance

**Binary.** A pool item is relevant to a query if a member who asked it would
accept that photo as the one they meant, or as one of them: every constraint
the query states holds visibly in the image. A constraint the query does not
state is not required.

- **The image decides.** Labellers judge the pixels; they are not shown the
  readings at all.
- **Counts, colours, positions, time of day and setting** are constraints when
  the query states them. "Two cups" excludes a photo with three.
- **Ambiguous by sight** (the labeller cannot tell whether a constraint holds)
  is not relevant, and the item is named in the labeller's notes.
- Every query has at least one relevant item. A query whose final set is empty
  is a set defect, fixed by a new pool item or by withdrawing the query (logged
  in `labels.yaml` under `withdrawn`, with its raw labels and the reason), never
  by loosening the rule.

## Hard negatives

A hard negative is a pool item that shares the query's subject but fails one
stated constraint: the same dish in another setting, the same street at night
instead of by day, a near-identical second shot that differs in the stated
detail, the same animal in another colour. Each must be a real near miss: a
photo a person could plausibly confuse with the target, not merely another
item of the same broad kind. The query author proposes them with the failed
detail named; labeller A checks each against the image; they are labelled not
relevant and recorded per query as `hard_negatives` so the harness can report
how often an arm ranks one first. Every query has at least one where the pool
allows; a query without one records why under `no_hard_negative`.

A hard negative that the reconciled labels mark relevant loses the tag; it is
never both.

## Labelling

1. **Labeller A.** The set's maintainer marks the relevant items per query
   across the whole pool, from the images, and freezes the result before B's
   is seen.
2. **Labeller B.** A fresh agent session, separate from the query author and
   from A, with no retrieval tools and no access to anyone's notes or labels.
   It receives only this protocol's Relevance section, the images with their
   pool ids, and the queries, and marks every relevant item per query across
   the whole pool, not a top-k list.
3. **Agreement** is reported before reconciliation: per query, whether the two
   sets are identical, and their Jaccard overlap; over all query-item pairs,
   Cohen's kappa.
4. **Reconciliation.** Each item on which A and B differ is settled by looking
   at the image against the Relevance rules. Each settlement is logged in
   `labels.yaml` with the item, the two raw labels, the outcome and the reason.
5. **Raw sets and their digest.** Both labellers' raw sets are committed in
   `labels.yaml` (per query under `raw`, and under `withdrawn` for a withdrawn
   query). Each labeller's `raw_sha256` is the sha256 of the UTF-8 bytes of
   `json.dumps({query_id: sorted(ids)}, sort_keys=True, ensure_ascii=False,
   separators=(",", ":"))` over every query it labelled, withdrawn ones
   included; the loader test recomputes it.
6. **The lock.** `labels.lock` is written in the same commit as the
   reconciled labels. Labels change only in a new commit with a new lock, and
   every result cites the lock it was scored against.

## The text slices: `cantonese` and `names`

The sections above were written for `text_image`. Where the text slices differ,
this section governs them; where it is silent, the rules above hold with
"message" for "image".

**What they test** (design 20, «Slices»): `cantonese` is a message found by a
later question about it, across written Cantonese, English, mixed script and
romanised Cantonese, with slang and near-duplicates; `names` is a message found
by an invented game title, an invented handle or a Hong Kong place name inside
it. The `names` pool is the `cantonese` pool plus about 60 messages about named
things, so a names query competes against the whole chat.

**The pool is synthetic.** Every message is written for this set by a fresh
agent session (the pool author) that is told the setting, the languages and
the kinds of confusable it must contain (near-duplicates differing in one
detail, a plan made last month and again this week, two titles one word
apart, two handles one character apart), and never sees a query or a label.
Nothing comes from a room, a member or a production store. People appear only
as invented handles or as relations ("my boss"), never as a personal name. No
phone, email, URL, street number, booking code or ID shape, and no run of four
or more digits. Each item records `sent` (Hong Kong time), `lang` and `text`
in `texts.yaml`; ids are shuffled against time so an id says nothing about
which messages relate. Exact duplicate texts are not allowed: a text-only arm
could not tell them apart.

**Time is part of the input.** A query may say "this week" or "last month",
so each slice states the moment its queries are asked (`as_of`), and the
labellers judge a time constraint by the message's `sent` against `as_of`.
In stages 1–3 (lexical, dense, fused; design 20) every arm indexes the message
text alone and is never shown `sent`. A query whose relevant message and one
of its hard negatives say the same thing and differ only in when they were
sent is therefore a tie for every arm: no arm can rank the right one first
except by chance. Those queries are listed in `labels_text.yaml` under
`time_only_queries` (ca-q16, ca-q35, ca-q36, ca-q78, ca-q79, nm-q22, each
with the hard negative it ties with), and the harness reports them separately from the
rest of the slice.

**Blind for text.** The incumbent's floor (lexical or bge-m3) indexes the
message text itself, so a query author who copies a message's words hands the
floor the answer (the lesson of this set's first version). So:

- **Three separate fresh sessions.** The pool author, the query author and
  labeller B are different agent sessions; none sees another's notes. The
  query author sees only the pool (ids, `sent`, `lang`, text) and this
  section's query rules. Labeller B sees only the pool, the queries with
  `as_of`, and the relevance rules below.
- **Queries are what a person later asks to recover a message**: a
  paraphrase, another register or language, or a vague reference ("that
  typhoon night when the minibus stopped"), never a copy of the message's
  distinctive words. Generic words with no other way to say them are allowed.
  In `names`, the name itself is the point and may appear, the way a person
  recalling it would write it; the rest of the message's wording is not
  copied.
- **Languages.** `yue`, `en` and `mixed` as above, each at least 10 per slice,
  plus `roman` (romanised Cantonese) in the text slices.
- **No selection by a floor**, exactly as above: no query is kept, dropped or
  written by how a text arm scores it.
- The query author records, separately from the queries, the message it meant
  (`author_targets`) and proposed hard negatives with the one detail each
  fails. Labeller A labels from the pool and the query texts without opening
  that file, and checks the proposed hard negatives only after A's labels are
  frozen.

**Relevance for text.** A message is relevant to a query if a member who asked
it would accept that message as the one they meant, or as one of them: every
constraint the query states holds in the message as written, read with its
`sent` time against `as_of`. A constraint the query does not state is not
required.

- **The message decides**, not what a reader guesses happened around it. A
  reply that only makes sense next to another message ("ok 7pm then") is
  relevant only if the query's stated constraints hold in it alone.
- **Counts, days, times, places, names and who did it** are constraints when
  the query states them. "Six of us" excludes a message about five. "This
  week" excludes a message sent before the Monday of `as_of`'s week.
- **A name** must match the one the query names: a title one word off, a
  sequel number, or a handle one character off is a different name, unless the
  query itself is vague enough to cover both.
- **Ambiguous** (the labeller cannot tell whether a constraint holds) is not
  relevant, and the item is named in the labeller's notes.

**Hard negatives for text** are near misses of the same kind as above: the
near-duplicate that differs in the stated detail, last month's plan when the
query asks for this week's, the similar title, the handle one character off,
the place easily confused with the named one.

**Labelling and its digest.** As above: A (the maintainer) labels every query
across its whole slice pool and freezes before opening B's set; B is the third
fresh session, with no retrieval or search tools, reading the whole pool;
agreement is reported per slice before reconciliation; every disagreement is
settled against the message and logged. `labels_text.yaml` holds both slices'
queries, and each labeller's `raw_sha256` there is the digest defined above
over every query in that file, withdrawn ones included.

## Identifiers and PII

Nothing in this directory may carry a real member's name, a room's content, a
phone number, an email address, or an ID-shaped string. Commons credits are
not copied: the licence field and the file's page URL are the provenance. Every
commit runs `scripts/pii_scan.py`; a finding is fixed by changing the value,
never acknowledged.
