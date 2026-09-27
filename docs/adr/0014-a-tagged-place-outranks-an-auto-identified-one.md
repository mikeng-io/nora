# 0014 — A tagged place outranks an auto-identified one

- **Status:** accepted — built 2026-09-27 (design/27 §1.4)
- **Date:** 2026-09-17
- **Reverses, in part:** DEC-115 point (4) — "Life360's saved labels are a
  naming HINT and never the authority". Reversed for **identity and name**,
  upheld for **geometry**. DEC-115's fail-closed three-state labelling is
  unchanged; it now labels a place that already exists rather than deciding
  whether one exists.
- **Index:** DEC-190. Argument: `design/proposed/27-presence-analytics.md` §3.7.
  Built 2026-09-27; the regressions below are in `tests/unit/test_place_identity.py`.

## Context

`presence_derived_places` is written by one method, `upsert_place`, which keeps
**one row per `(subject_id, kind)`** with `kind IN ('home','work','unlabeled')`
and never stores `unlabeled` at all — a downgrade deletes the row. Read as a
schema rather than a vocabulary, `design/27` §3.4's "a place is home, work, or
unlabeled" is therefore a **cardinality ceiling: at most one home and one work
per subject.**

The invariant is held by that one method and nothing else. The table has a
**non-unique** index on `(subject_id, kind)` and no constraint, and the Speed-3
caller loops over every cluster calling `upsert_place` once each without
counting.

Measured against the deployment, 2026-09-17. The ceiling does not merely limit;
it corrupts. A subject with two residences produces two clusters that both label
`home`, and Speed-3 processes them in DBSCAN discovery order:

- the **first** creates the row and runs the first-place backfill, which links
  that cluster's stays;
- every **later** `home` cluster takes the update branch and overwrites that
  row's `lat`/`lng` — no backfill, no re-link.

So the last cluster in discovery order owns the coordinates and the first owns
the links, and discovery order is a function of stay ordering, not of strength.
One subject's **68-stay, 456-hour** residence had its row moved onto a
**10-stay, 16-hour** cell whose label carried **confidence 0.053**.

Nothing repairs it: `set_place_ids` fills only NULLs by design, and the backfill
fires only on a kind's first creation. **88 stays are currently linked to a
place they never happened at** — 20 at 12.1 km, 68 at up to 3.8 km — through a
gate that only ever links within 100 m, which is why the distance is
arithmetically impossible at link time and proves the row moved afterwards.

The live table is **14 rows for 8 subjects**, against a ceiling of 16. One
subject demonstrably has three places; the third has nowhere to live.

`presence_places` — Life360's saved places — is an unbounded, human-named
registry with stable ids, already joined to the Alembic chain, already the table
`design/23 §4` resolves names against for routing.
`presence_derived_places.saved_place_id` was reserved for it when the table was
created and has never been populated.

## Decision

1. **A stay whose centroid lies within `max(tagged_radius_m, match_radius_m)` of
   a tagged place resolves to that tagged place.** Identity is Life360's stable
   id; the name is the household's own.
2. **A tagged place is a place in its own right** as soon as any stay resolves to
   it under (1) — no clustering, no `min_nights` gate.
3. **A derived cluster within `match_radius_m` of a tagged place adopts that
   identity** (`saved_place_id`, name) rather than becoming a rival row.
4. **Otherwise the cluster is its own place, and places per subject are
   unbounded.** `home`/`work` becomes an attribute, not the key. Concretely,
   `unlabeled` stops being a deletion and becomes a stored value.

**Identity and name come from the tag; geometry and coverage come from the
cluster.** A pin is where a person dropped it, and one in this corpus sits 636 m
from the cluster it names.

`match_radius_m` is a **new named constant and must not reuse `cluster_eps_m`**,
which already carries three roles (DBSCAN's `eps`, the stay-to-place link gate,
and the `radius_m` stored on every place row). Measured from the centroids of
each place's linked stays, nearest-tag distance is **7.0–49.3 m** for a match and
**636 m** for the nearest non-match — a 13× gap — so `match_radius_m` is safe in
**100–500 m** and is not safe at 50 m.

## Alternatives rejected

**Keep the ceiling and tune `label_place`'s thresholds.** This is what the
existing code does, and `label_place`'s threshold tuning exists *because* there
is one slot to win. It cannot express a subject with three places at any
threshold, and the corruption above is not a labelling error — both clusters
label correctly, with confidence 1.0.

**Take geometry from the tag as well as identity.** Rejected on measurement: a
work pin sits 636 m from its cluster. Adopting pin geometry would move a place
away from where its own stays are, which is the defect this ADR exists to
remove.

**Use tagged places alone and drop derivation.** Rejected on coverage: tagged
places contain 27.9% of stays, and one subject has 139 stays and 651.6 hours
with no tagged place at all.

## Consequences

**What gets better.** A subject may hold the places they actually have. Place
identity stops churning across re-derivations, which is what orphaned Tier 3's
data once already. Places carry the household's own names, so a consumer can say
where someone is rather than print a coordinate.

**What gets worse, and must be accepted deliberately:**

- **Evidence per place gets thinner.** `design/27` §4.1 measures that
  time-anchored occupancy already loses to a trivial baseline for subjects who
  are almost always in one place, because per-bin evidence is thin. More places
  per subject makes every bin thinner. This is a regression for exactly the
  subjects that arm currently serves best.
- **`presence_occupancy` grows with place count**, not with people — it is keyed
  `(subject, place, hour_of_week)`.
- **Per-cluster and per-place work scales with place count.** §3.3's sizing
  argument was made against a bounded place table.
- **A new user-visible state exists:** a place with a name and no label.
  §3.4's three-state vocabulary stops being the whole answer, and `where_been`'s
  docstring — which tells the model `unlabeled` means "no pattern yet", codified
  as DEC-155's load-bearing third state — becomes wrong about a named place with
  357 hours of dwell. It changes in the same commit as the schema.
- **One bad pin is not recovered automatically.** The 636 m pair is left to
  decision (2) as two places until an operator-declared alias joins them. Naming
  the residual is preferred to widening the radius until it swallows
  neighbouring tags.

**Regressions this owes, none of which exist yet:**

1. A stay is never left pointing at a place whose centroid has moved — it is
   re-linked, and no stay resolves to a place further than the link radius from
   it. *This is the defect that was measured; the migration must repair the 88
   existing rows, not carry them forward.*
2. Place ids survive the migration: `presence_stays.place_id`,
   `presence_occupancy` and `presence_place_rhythm` still resolve afterwards.
3. A matched place's stored `lat`/`lng`/`radius_m` equal the cluster's, never
   the tag's.
4. An unlabelled place is stored, not deleted.
5. One subject with three places, none mislinked.
6. Two tagged places within ~100 m merge — and the merge is not scoped
   per-circle, because the measured pair (50.3 m apart) sits in two different
   circles.
7. A subject with no tagged place is still auto-identified.
8. The Speed-2 recheck no longer writes a place at `all_stays[0]`'s coordinates
   — the subject's chronologically first stay, unrelated to the cluster being
   rechecked. It has not fired in this corpus but is a second, independent way
   to plant a place kilometres from its stays.
