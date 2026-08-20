# Presence Analytics Tier 5 (Routes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive routes from raw fixes — a real speed (never Life360's own
`speed_kmh`, only 14.7% filled), corridor sequences from the `address`
column already 100% populated with road names (no OSM, no map-matching),
trips clustered into named corridors by address-sequence edit distance, and
per-corridor speed percentiles bucketed by hour-of-week (a congestion model
built entirely from this system's own data).

**Architecture:** Pure additions to `nora/domain/mobility.py` (a `Trip`
type, speed derivation, edit distance, a second hand-rolled DBSCAN keyed on
edit distance instead of haversine) and `nora/infra/sql/mobility.py` (three
new tables, no change to any existing one). Wired into the SAME
`MobilityRollup.run_once` tick Tier 1-3 already use — trip extraction runs
alongside stay segmentation (Speed 1), corridor clustering is dirty-set
gated exactly like place clustering (Speed 3), because it consumes the same
kind of newly-closed unit of work.

**Tech Stack:** Same as Tiers 1-3 — Python 3.13, SQLAlchemy Core (async,
Postgres), Alembic, pytest + pytest-asyncio. No new dependency — edit
distance and DBSCAN are both hand-rolled, matching `cluster_places`'s own
precedent (`nora/domain/mobility.py`'s module docstring: no numpy/scipy even
though nothing currently forbids it).

**Spec:** GitHub issue [#6](https://github.com/mikeng-io/nora/issues/6)
(Tier 5's own technical brief, quoted throughout this plan) and
`design/proposed/27-presence-analytics.md` §4 (the tier table Tier 5's row
lives in). Prior art this extends: the Tier 3 plan,
`docs/superpowers/plans/2026-08-19-presence-analytics-tier3-rhythm-plan.md`,
whose review process (a deep final whole-branch review found and fixed two
Critical bugs no per-task review caught) is the standard this plan expects
its own execution to match.

## Global Constraints

- **`speed_kmh` (Life360's own field, on `presence_fixes`) is NEVER read for
  Tier 5.** It is 14.7% filled (design/27 §1) and this substrate does not
  guess. Every speed value Tier 5 produces comes from `derive_speed_kmh`
  (Task 3), computed from two consecutive fixes' own `lat`/`lng`/`ts`.
- **No OSM, no map-matching, no third-party geocoding.** Corridor identity
  is built ONLY from the `address` strings Life360 already attaches to
  fixes (`presence_fixes.address`, 100% filled) — a corridor is a sequence
  of those strings, nothing more.
- **A trip's ambiguous tail is deferred, never guessed** — mirroring
  `segment_stays`'s own `open_stay` posture. The fixes forming a chunk's
  still-undetermined trailing group (whether it becomes a stay or stays
  open) are NEVER extracted as a trip in the same cycle they arrived; they
  are picked up as trip fixes only once `run_once` has resolved what they
  are. A small sliver of a genuinely still-open trip's tail is accepted as
  lost at the exact seam where the watermark advances past it — documented
  in Task 4, not solved with more machinery.
- **Recompute from the full sample set, every time — never an incremental
  running statistic.** Percentiles are not meaningfully mergeable the way a
  sum is; `presence_corridor_speed` is always fully recomputed from
  `presence_trips` for its `(subject_id, corridor_id, hour_of_week)` slot,
  the same posture Tier 3's `upsert_rhythm` already established.
- **Corridor identity is a durable id, not re-minted every cycle** — Tier 3's
  Critical #1 (place_id churning on every re-cluster, silently orphaning
  everything keyed off it) is the exact failure mode `upsert_corridor`
  (Task 8) must not repeat. A corridor's row updates in place, preserving
  its `id`, when the SAME corridor (matched by its own identity rule, Task
  8) is reconfirmed.
- **A Tier 5 derivation failure must never block the Tier 1/2/3 watermark
  contract** — the exact rule Tier 3's own Critical #1 fix established
  (`nora/analytics/rollup.py`'s `run_once`, the `try/except` around
  `_record_rhythm`). Trip/corridor recording gets the same treatment.
- **Gate commands, run before every commit** (CLAUDE.md):
  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  ```
- **Schema-parity baseline regeneration** (CLAUDE.md): after Task 1's
  migration, `uv run python scripts/gen_pg_baseline.py`, commit the diff —
  never hand-edit `tests/infra/pg_schema_baseline.sql`.

---

## File Structure

```
nora/domain/mobility.py                EDIT  RawFix.address, derive_speed_kmh,
                                              Trip, extract_trips, edit_distance,
                                              cluster_trips
nora/infra/sql/schema.py               EDIT  PRESENCE_TRIPS, PRESENCE_CORRIDORS,
                                              PRESENCE_CORRIDOR_SPEED
nora/infra/sql/migrations/versions/0019_presence_routes.py   NEW
nora/infra/sql/mobility.py             EDIT  fixes_since selects address;
                                              write_trips, trips_for_subject,
                                              set_trip_corridor_ids,
                                              upsert_corridor, existing_corridor,
                                              corridor_speed_samples,
                                              upsert_corridor_speed
nora/testing/mobility.py               EDIT  FakeMobilityStore grows the same
nora/analytics/rollup.py               EDIT  MobilityStore protocol grows the
                                              above, RollupConfig gains 2 fields,
                                              MobilityRollup wires trip
                                              extraction + corridor clustering
nora/analytics/composition.py          EDIT  pass the 2 new RollupConfig fields
nora/config.py                         EDIT  AnalyticsConfig gains
                                              corridor_max_edit_distance,
                                              corridor_min_samples
nora/obs/metric_specs.py               EDIT  nora_analytics_trips_total,
                                              nora_analytics_corridors_total
config.example.toml                    EDIT  [analytics] block
design/09-decision-index.md            EDIT  next DEC (renumber-check first —
                                              see Task 12)
design/proposed/27-presence-analytics.md EDIT  Tier 5 row -> P0 — built
release-fragments/unreleased/presence-analytics-tier5-routes.json   NEW

tests/unit/test_mobility.py                    EDIT  new pure-function suites
tests/contract/test_mobility_store_contract.py EDIT  new method suites
tests/unit/test_analytics_rollup.py            EDIT  trip/corridor wiring TDD
tests/infra/pg_schema_baseline.sql             EDIT  regenerated
```

---

### Task 1: Schema — three new tables, one migration

**Files:**
- Modify: `nora/infra/sql/schema.py`
- Create: `nora/infra/sql/migrations/versions/0019_presence_routes.py`
- Modify: `tests/infra/pg_schema_baseline.sql` (regenerated, not hand-edited)

**Interfaces:**
- Produces: `PRESENCE_TRIPS`, `PRESENCE_CORRIDORS`, `PRESENCE_CORRIDOR_SPEED`
  (`sa.Table` objects, importable by `nora/infra/sql/mobility.py` in Task 7/8).

- [ ] **Step 1: Check the current migration head first**

  Run `uv run alembic -c alembic.ini heads` (from the repo root) BEFORE
  writing the migration below. If it does not print exactly
  `0018_presence_rhythm (head)`, STOP and report BLOCKED — another
  migration has landed on `master` since this plan was written (this
  happened once already during Tier 3's own merge — two branches both
  claimed revision `0017`), and `down_revision` below must chain onto
  whatever the ACTUAL current head is, not the number this plan assumes.

- [ ] **Step 2: Add the three tables to `nora/infra/sql/schema.py`**

  Append after `PRESENCE_PLACE_RHYTHM` (Tier 3's last table):

  ```python
  # `presence_trips` / `presence_corridors` / `presence_corridor_speed` —
  # Tier 5 (design/27 §4, issue #6). A trip is the movement BETWEEN two
  # stays (or before the first / after the last), never inside one; a
  # corridor is a NAMED cluster of trips whose address sequences are close
  # by edit distance — no OSM, no map-matching, built entirely from the
  # `presence_fixes.address` strings Life360 already sends (100% filled,
  # design/27 §1).
  PRESENCE_TRIPS = sa.Table(
      "presence_trips",
      METADATA,
      sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
      sa.Column("subject_id", sa.Text, nullable=False),
      sa.Column("person_id", sa.Text, nullable=False),
      sa.Column("circle_id", sa.Text, nullable=False),
      sa.Column("started_ts", sa.BigInteger, nullable=False),
      sa.Column("ended_ts", sa.BigInteger, nullable=False),
      sa.Column("distance_m", sa.Float, nullable=False),
      sa.Column("avg_speed_kmh", sa.Float, nullable=False),
      sa.Column("fix_count", sa.Integer, nullable=False),
      # NULL until clustered (Tier 5's own dirty-set gated Speed) — the
      # SAME nullable-then-filled-once shape `presence_stays.place_id`
      # already established in Tier 3.
      sa.Column("corridor_id", sa.Integer, nullable=True),
  )

  IDX_PRESENCE_TRIPS_SUBJECT_STARTED = sa.Index(
      "idx_presence_trips_subject_started",
      PRESENCE_TRIPS.c.subject_id, sa.text("started_ts DESC"),
  )

  # One row per NAMED corridor. `sequence` is the representative address
  # sequence (JSONB array of strings) — the first trip's own sequence when
  # the corridor is created; re-confirmed corridors do NOT overwrite it with
  # a newer trip's sequence, only `last_seen_ts`/`sample_count` — the same
  # "identity survives, content updates conservatively" posture
  # `presence_place_rhythm` already has for arrival/departure minutes
  # (recomputed) versus a place's OWN row (id preserved, Tier 3 Critical #1).
  PRESENCE_CORRIDORS = sa.Table(
      "presence_corridors",
      METADATA,
      sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
      sa.Column("subject_id", sa.Text, nullable=False),
      sa.Column("sequence", pg.JSONB, nullable=False),
      sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
      sa.Column("first_seen_ts", sa.BigInteger, nullable=False),
      sa.Column("last_seen_ts", sa.BigInteger, nullable=False),
  )

  IDX_PRESENCE_CORRIDORS_SUBJECT = sa.Index(
      "idx_presence_corridors_subject", PRESENCE_CORRIDORS.c.subject_id,
  )

  # Recomputed WHOLESALE from `presence_trips` every time (never
  # incremental — percentiles are not mergeable the way a sum is), for one
  # `(subject_id, corridor_id, hour_of_week)` slot. `hour_of_week` is the
  # SAME 0..167 Monday-00:00-=-0 bucketing `presence_occupancy` already
  # uses (Tier 3), keyed on the TRIP's `started_ts`, not split across hours
  # the way a stay's dwell is — a trip is a single event, not a span to
  # divide.
  PRESENCE_CORRIDOR_SPEED = sa.Table(
      "presence_corridor_speed",
      METADATA,
      sa.Column("subject_id", sa.Text, primary_key=True),
      sa.Column("corridor_id", sa.Integer, primary_key=True),
      sa.Column("hour_of_week", sa.Integer, primary_key=True),
      sa.Column("p50_kmh", sa.Float, nullable=False),
      sa.Column("p90_kmh", sa.Float, nullable=False),
      sa.Column("sample_count", sa.Integer, nullable=False),
      sa.Column("updated_ts", sa.BigInteger, nullable=False),
      sa.CheckConstraint(
          "hour_of_week >= 0 AND hour_of_week < 168",
          name="ck_corridor_speed_how",
      ),
  )
  ```

  `pg` (the `sqlalchemy.dialects.postgresql` import alias) is already
  imported at the top of `schema.py` — check before adding a duplicate
  import (`ROOM_LEXEME`, already in the file, uses `pg.JSONB` the same way).

- [ ] **Step 3: Write the migration**

  `nora/infra/sql/migrations/versions/0019_presence_routes.py` — use the
  REAL head from Step 1 as `down_revision`, not the literal string below if
  Step 1 found a different head:

  ```python
  """presence_trips + presence_corridors + presence_corridor_speed — Tier 5
  routes (design/27 §4, issue #6).

  Revision ID: 0019_presence_routes
  Revises: 0018_presence_rhythm
  Create Date: 2026-08-20
  """

  from __future__ import annotations

  import sqlalchemy as sa
  from alembic import op
  from sqlalchemy.dialects import postgresql as pg

  revision = "0019_presence_routes"
  down_revision = "0018_presence_rhythm"
  branch_labels = None
  depends_on = None


  def upgrade() -> None:
      op.create_table(
          "presence_trips",
          sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
          sa.Column("subject_id", sa.Text, nullable=False),
          sa.Column("person_id", sa.Text, nullable=False),
          sa.Column("circle_id", sa.Text, nullable=False),
          sa.Column("started_ts", sa.BigInteger, nullable=False),
          sa.Column("ended_ts", sa.BigInteger, nullable=False),
          sa.Column("distance_m", sa.Float, nullable=False),
          sa.Column("avg_speed_kmh", sa.Float, nullable=False),
          sa.Column("fix_count", sa.Integer, nullable=False),
          sa.Column("corridor_id", sa.Integer, nullable=True),
      )
      op.create_index(
          "idx_presence_trips_subject_started", "presence_trips",
          ["subject_id", sa.text("started_ts DESC")],
      )
      op.create_table(
          "presence_corridors",
          sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
          sa.Column("subject_id", sa.Text, nullable=False),
          sa.Column("sequence", pg.JSONB, nullable=False),
          sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
          sa.Column("first_seen_ts", sa.BigInteger, nullable=False),
          sa.Column("last_seen_ts", sa.BigInteger, nullable=False),
      )
      op.create_index(
          "idx_presence_corridors_subject", "presence_corridors", ["subject_id"],
      )
      op.create_table(
          "presence_corridor_speed",
          sa.Column("subject_id", sa.Text, primary_key=True),
          sa.Column("corridor_id", sa.Integer, primary_key=True),
          sa.Column("hour_of_week", sa.Integer, primary_key=True),
          sa.Column("p50_kmh", sa.Float, nullable=False),
          sa.Column("p90_kmh", sa.Float, nullable=False),
          sa.Column("sample_count", sa.Integer, nullable=False),
          sa.Column("updated_ts", sa.BigInteger, nullable=False),
          sa.CheckConstraint(
              "hour_of_week >= 0 AND hour_of_week < 168",
              name="ck_corridor_speed_how",
          ),
      )


  def downgrade() -> None:
      op.drop_table("presence_corridor_speed")
      op.drop_table("presence_corridors")
      op.drop_index("idx_presence_trips_subject_started", table_name="presence_trips")
      op.drop_table("presence_trips")
  ```

  `idx_presence_trips_subject_started` uses a hand-ordered `DESC` clause —
  per CLAUDE.md this is EXCLUDED from Alembic autogenerate
  (`nora/infra/sql/migrations/env.py`'s `include_object` hook); confirm this
  index name is added to whatever exclusion list that hook checks, matching
  how `idx_messages_conv_ts` and `idx_presence_derived_places_subject_kind`
  are already listed there. If the file uses a different mechanism (a
  prefix check, a literal tuple), match its existing pattern — read the
  hook before assuming a list literal.

- [ ] **Step 4: Verify and regenerate the baseline**

  ```bash
  uv run pytest -q tests/infra/test_schema_parity.py
  ```
  Expected: FAILS (drift). Then:
  ```bash
  uv run python scripts/gen_pg_baseline.py
  uv run pytest -q tests/infra/test_schema_parity.py
  ```
  Expected: PASSES. Commit the regenerated baseline alongside the migration.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/infra/sql/schema.py nora/infra/sql/migrations/versions/0019_presence_routes.py tests/infra/pg_schema_baseline.sql
  git commit -m "feat(analytics): presence_trips + presence_corridors + presence_corridor_speed tables (Tier 5)"
  ```

---

### Task 2: Domain — `RawFix.address` and `fixes_since` reads it

**Files:**
- Modify: `nora/domain/mobility.py`
- Modify: `nora/infra/sql/mobility.py`
- Modify: `nora/testing/mobility.py`
- Test: `tests/unit/test_mobility.py`, `tests/contract/test_mobility_store_contract.py`

**Interfaces:**
- Produces: `RawFix` gains `address: str | None = None` as its final field
  (additive, same backward-compatible pattern `Stay.place_id` used in Tier 3
  — every existing `RawFix(...)` construction in the codebase already uses
  keyword arguments; verify this holds the same way Task 5 of the Tier 3
  plan did, with a full-file test run, not a `-k`-scoped one).
- `MobilityStore.fixes_since` now selects and returns `address` too.

- [ ] **Step 1: Write the failing tests**

  Add to `tests/unit/test_mobility.py`:
  ```python
  def test_raw_fix_address_defaults_to_none():
      f = RawFix(id=1, ts=0, lat=1.0, lng=1.0)
      assert f.address is None


  def test_raw_fix_address_can_be_set():
      f = RawFix(id=1, ts=0, lat=1.0, lng=1.0, address="Yoho Mall, Yuen Long")
      assert f.address == "Yoho Mall, Yuen Long"
  ```

  Add to `tests/contract/test_mobility_store_contract.py`:
  ```python
  async def test_fixes_since_reads_address_back(store):
      await _seed_fix(store, id=1, person_id="test_subject", ts=1_000, lat=1.0, lng=1.0,
                       accuracy_m=50.0, wire_state="live", recorded_at=1_000,
                       address="Castle Peak Road")
      fixes = await store.fixes_since(
          person_ids=["test_subject"], after_fix_id=0, grace_ms=0,
          now_ms=10_000, limit=100,
      )
      assert fixes[0].address == "Castle Peak Road"


  async def test_fixes_since_address_is_none_when_life360_had_none(store):
      await _seed_fix(store, id=1, person_id="test_subject", ts=1_000, lat=1.0, lng=1.0,
                       accuracy_m=50.0, wire_state="live", recorded_at=1_000,
                       address=None)
      fixes = await store.fixes_since(
          person_ids=["test_subject"], after_fix_id=0, grace_ms=0,
          now_ms=10_000, limit=100,
      )
      assert fixes[0].address is None
  ```

  `_seed_fix_for_test`'s existing SQL implementation already forwards
  arbitrary kwargs straight into `sa.insert(PRESENCE_FIXES)` (see
  `nora/infra/sql/mobility.py`'s current body) — `presence_fixes.address`
  already exists as a real column (it is the SAME table `nora.presence`
  writes to), so no schema change is needed for the SQL side; only the
  FAKE's `_SeededFix` dataclass and `fixes_since` filter need to learn the
  new field. Check `_SeededFix` in `nora/testing/mobility.py` — if it does
  not already accept `address`, add it there with a `None` default.

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py tests/contract/test_mobility_store_contract.py -k address
  ```
  Expected: FAIL — `RawFix.__init__() got an unexpected keyword argument
  'address'`, or the contract tests fail on a `None`/missing attribute.

- [ ] **Step 3: Implement**

  `nora/domain/mobility.py`, `RawFix`'s last line:
  ```python
      id: int
      ts: int
      lat: float
      lng: float
      # Tier 5 (design/27 §4, issue #6): Life360's own address label,
      # 100% filled (design/27 §1) — the corridor-identity input. `None`
      # only when the store's own filter admits a row Life360 sent with no
      # address (rare; `extract_trips`, Task 4, drops it from the corridor
      # sequence rather than guessing).
      address: str | None = None
  ```

  `nora/infra/sql/mobility.py`, `fixes_since`'s `SELECT` and the
  `RawFix(...)` construction that follows it — add `PRESENCE_FIXES.c.address`
  to the selected columns and `address=r.address` to the construction.

  `nora/testing/mobility.py` — same for `_SeededFix` (add the field with a
  `None` default) and `fixes_since`'s own `RawFix(...)` construction (add
  `address=f.address`).

- [ ] **Step 4: Run tests to verify they pass, full file**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py tests/contract/test_mobility_store_contract.py
  ```
  Full files, not `-k`-scoped — same reasoning as Tier 3 Task 5: this is the
  one point a stray positional `RawFix(...)` construction anywhere would
  break silently.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py nora/infra/sql/mobility.py nora/testing/mobility.py tests/unit/test_mobility.py tests/contract/test_mobility_store_contract.py
  git commit -m "feat(mobility): RawFix.address — Tier 5's corridor-identity input"
  ```

---

### Task 3: Domain — `derive_speed_kmh`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Produces: `derive_speed_kmh(a: RawFix, b: RawFix) -> float | None`.

- [ ] **Step 1: Write the failing tests**

  ```python
  def test_derive_speed_kmh_of_a_known_distance_and_time():
      # ~1km apart (0.009 deg lat ~ 1000m at the equator; use a precise
      # haversine-verified pair instead of an approximation — 22.30,114.00
      # to 22.30,114.01 is measured 1028.3m via haversine_distance_m, over
      # exactly 1 minute -> ~61.7 km/h. Compute the EXACT expected value
      # from haversine_distance_m directly, not a rounded guess, so this
      # test fails if either function's formula drifts.
      a = RawFix(id=1, ts=0, lat=22.30, lng=114.00)
      b = RawFix(id=2, ts=60_000, lat=22.30, lng=114.01)
      expected_kmh = haversine_distance_m(22.30, 114.00, 22.30, 114.01) / 1000 * 60
      assert derive_speed_kmh(a, b) == pytest.approx(expected_kmh, rel=1e-9)


  def test_derive_speed_kmh_is_none_for_non_positive_elapsed_time():
      a = RawFix(id=1, ts=1_000, lat=1.0, lng=1.0)
      b_same_ts = RawFix(id=2, ts=1_000, lat=1.0, lng=1.0)
      b_earlier = RawFix(id=3, ts=500, lat=1.0, lng=1.0)
      assert derive_speed_kmh(a, b_same_ts) is None
      assert derive_speed_kmh(a, b_earlier) is None


  def test_derive_speed_kmh_of_the_same_point_twice_is_zero():
      a = RawFix(id=1, ts=0, lat=1.0, lng=1.0)
      b = RawFix(id=2, ts=60_000, lat=1.0, lng=1.0)
      assert derive_speed_kmh(a, b) == 0.0
  ```

  Import `haversine_distance_m` from `nora.domain.presence` at the top of
  the test file if not already imported there (check first).

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k derive_speed_kmh
  ```
  Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, add `"derive_speed_kmh"` to `__all__`:

  ```python
  def derive_speed_kmh(a: RawFix, b: RawFix) -> float | None:
      """Speed between two consecutive fixes, km/h — Tier 5's ground truth
      (design/27 §4, issue #6). Life360's own `speed_kmh` (on the stored
      fix, never read here) is only 14.7% filled and must never stand in
      for this.

      `None` when `b.ts <= a.ts` — a non-positive elapsed time has no
      speed, and out-of-order/duplicate-timestamp fixes are a measured,
      rare property of the live corpus (design/27 §1: 1 in 20,156 fixes)."""
      dt_ms = b.ts - a.ts
      if dt_ms <= 0:
          return None
      distance_m = haversine_distance_m(a.lat, a.lng, b.lat, b.lng)
      dt_hours = dt_ms / 3_600_000.0
      return distance_m / dt_hours
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k derive_speed_kmh
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): derive_speed_kmh — Tier 5's ground-truth speed"
  ```

---

### Task 4: Domain — `Trip` and `extract_trips`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Consumes: `RawFix`, `Stay`, `derive_speed_kmh` (Task 3).
- Produces: `Trip` dataclass; `extract_trips(fixes: list[RawFix], closed_stays: list[Stay], *, subject_id: str, person_id: str, circle_id: str, min_trip_fixes: int, open_stay_started_ts: int | None) -> list[Trip]`.

- [ ] **Step 1: Write the failing tests**

  ```python
  def _fix_a(id_, ts, lat, lng, address=None):
      return RawFix(id=id_, ts=ts, lat=lat, lng=lng, address=address)


  def test_extract_trips_finds_the_movement_between_two_stays():
      # Stay A: fixes 1-2 (0..60s). Trip: fixes 3-5 (moving, 70s..190s).
      # Stay B: fixes 6-7 (200s..260s).
      fixes = [
          _fix_a(1, 0, 1.0, 1.0), _fix_a(2, 60_000, 1.0, 1.0),
          _fix_a(3, 70_000, 1.001, 1.001, "Road A"),
          _fix_a(4, 130_000, 1.002, 1.002, "Road B"),
          _fix_a(5, 190_000, 1.003, 1.003, "Road B"),
          _fix_a(6, 200_000, 2.0, 2.0), _fix_a(7, 260_000, 2.0, 2.0),
      ]
      stay_a = Stay(subject_id="s", person_id="s", circle_id="c1",
                    started_ts=0, ended_ts=60_000, lat=1.0, lng=1.0,
                    radius_m=0.0, fix_count=2)
      stay_b = Stay(subject_id="s", person_id="s", circle_id="c1",
                    started_ts=200_000, ended_ts=260_000, lat=2.0, lng=2.0,
                    radius_m=0.0, fix_count=2)
      trips = extract_trips(
          fixes, [stay_a, stay_b], subject_id="s", person_id="s",
          circle_id="c1", min_trip_fixes=2, open_stay_started_ts=None,
      )
      assert len(trips) == 1
      trip = trips[0]
      assert trip.started_ts == 70_000
      assert trip.ended_ts == 190_000
      assert trip.fix_count == 3
      # "Road A" appears once, "Road B" appears twice consecutively ->
      # collapsed to one entry (Task 4's own dedup rule, tested directly
      # in Task 4's corridor-sequence test below too).
      assert trip.corridor == ("Road A", "Road B")


  def test_extract_trips_excludes_fixes_in_or_after_the_open_stay():
      # A trip immediately followed by a trailing group that has NOT yet
      # been decided (open_stay_started_ts marks where it begins) must not
      # absorb those fixes — they are still ambiguous (Global Constraint).
      fixes = [
          _fix_a(1, 0, 1.0, 1.0),
          _fix_a(2, 60_000, 1.001, 1.001, "Road A"),
          _fix_a(3, 120_000, 1.002, 1.002, "Road A"),
          # open stay begins at fix 4:
          _fix_a(4, 180_000, 2.0, 2.0), _fix_a(5, 240_000, 2.0, 2.0),
      ]
      trips = extract_trips(
          fixes, [], subject_id="s", person_id="s", circle_id="c1",
          min_trip_fixes=2, open_stay_started_ts=180_000,
      )
      assert len(trips) == 1
      assert trips[0].ended_ts == 120_000
      assert trips[0].fix_count == 3  # fixes 1, 2, 3 only


  def test_extract_trips_drops_a_run_shorter_than_min_trip_fixes():
      fixes = [
          _fix_a(1, 0, 1.0, 1.0), _fix_a(2, 60_000, 1.0, 1.0),
          _fix_a(3, 70_000, 1.001, 1.001),  # a single stray fix — noise
          _fix_a(4, 130_000, 2.0, 2.0), _fix_a(5, 190_000, 2.0, 2.0),
      ]
      stay_a = Stay(subject_id="s", person_id="s", circle_id="c1",
                    started_ts=0, ended_ts=60_000, lat=1.0, lng=1.0,
                    radius_m=0.0, fix_count=2)
      stay_b = Stay(subject_id="s", person_id="s", circle_id="c1",
                    started_ts=130_000, ended_ts=190_000, lat=2.0, lng=2.0,
                    radius_m=0.0, fix_count=2)
      trips = extract_trips(
          fixes, [stay_a, stay_b], subject_id="s", person_id="s",
          circle_id="c1", min_trip_fixes=2, open_stay_started_ts=None,
      )
      assert trips == []


  def test_extract_trips_computes_distance_and_speed_from_the_real_path():
      fixes = [
          _fix_a(1, 0, 1.0, 1.0), _fix_a(2, 60_000, 1.0, 1.0),
          _fix_a(3, 70_000, 1.0, 1.0, "Road A"),
          _fix_a(4, 130_000, 1.01, 1.01, "Road A"),
          _fix_a(5, 190_000, 2.0, 2.0), _fix_a(6, 250_000, 2.0, 2.0),
      ]
      stay_a = Stay(subject_id="s", person_id="s", circle_id="c1",
                    started_ts=0, ended_ts=60_000, lat=1.0, lng=1.0,
                    radius_m=0.0, fix_count=2)
      stay_b = Stay(subject_id="s", person_id="s", circle_id="c1",
                    started_ts=190_000, ended_ts=250_000, lat=2.0, lng=2.0,
                    radius_m=0.0, fix_count=2)
      trips = extract_trips(
          fixes, [stay_a, stay_b], subject_id="s", person_id="s",
          circle_id="c1", min_trip_fixes=2, open_stay_started_ts=None,
      )
      assert len(trips) == 1
      expected_distance = haversine_distance_m(1.0, 1.0, 1.01, 1.01)
      assert trips[0].distance_m == pytest.approx(expected_distance, rel=1e-9)
      expected_speed = expected_distance / 1000 / (60 / 3600)
      assert trips[0].avg_speed_kmh == pytest.approx(expected_speed, rel=1e-9)
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k extract_trips
  ```
  Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, add `"Trip"` and `"extract_trips"` to
  `__all__` (alphabetically):

  ```python
  @dataclass(frozen=True)
  class Trip:
      """One movement span between stays (design/27 §4, issue #6) — the
      complement of `Stay`: fixes NOT absorbed into any stay-forming group.
      `distance_m`/`avg_speed_kmh` are derived from the trip's OWN fixes
      (`derive_speed_kmh` pairwise, summed), never from Life360's
      `speed_kmh`. `corridor` is the deduplicated address sequence — the
      corridor-CLUSTERING input (Task 6); `corridor_id` is set once
      clustered (Task 8), `None` until then, the same nullable-then-filled
      shape `Stay.place_id` uses."""

      subject_id: str
      person_id: str
      circle_id: str
      started_ts: int
      ended_ts: int
      distance_m: float
      avg_speed_kmh: float
      fix_count: int
      corridor: tuple[str, ...]
      corridor_id: int | None = None


  def _trip_from_group(
      group: list[RawFix], *, subject_id: str, person_id: str, circle_id: str,
  ) -> Trip:
      distance_m = 0.0
      speeds: list[float] = []
      for a, b in zip(group, group[1:], strict=True):
          distance_m += haversine_distance_m(a.lat, a.lng, b.lat, b.lng)
          speed = derive_speed_kmh(a, b)
          if speed is not None:
              speeds.append(speed)
      duration_hours = (group[-1].ts - group[0].ts) / 3_600_000.0
      avg_speed = (
          distance_m / 1000 / duration_hours if duration_hours > 0
          else (sum(speeds) / len(speeds) if speeds else 0.0)
      )
      corridor: list[str] = []
      for f in group:
          if f.address is None:
              continue
          if not corridor or corridor[-1] != f.address:
              corridor.append(f.address)
      return Trip(
          subject_id=subject_id, person_id=person_id, circle_id=circle_id,
          started_ts=group[0].ts, ended_ts=group[-1].ts,
          distance_m=distance_m, avg_speed_kmh=avg_speed,
          fix_count=len(group), corridor=tuple(corridor),
      )


  def extract_trips(
      fixes: list[RawFix], closed_stays: list[Stay], *, subject_id: str,
      person_id: str, circle_id: str, min_trip_fixes: int,
      open_stay_started_ts: int | None,
  ) -> list[Trip]:
      """The movement fixes NOT covered by any CLOSED stay, grouped into
      trips — Tier 5's segmentation counterpart to `segment_stays` (design/27
      §4, issue #6).

      `fixes` and `closed_stays` come from the SAME batch `segment_stays`
      already processed this cycle (`nora.analytics.rollup`'s Speed 1) —
      `closed_stays` marks the ranges to EXCLUDE, not re-derive.
      `open_stay_started_ts`, when not `None`, excludes every fix from that
      point onward too: the trailing group is still undetermined (open
      stay or a trip that has not yet ended), and Global Constraints
      forbids guessing which. A short run below `min_trip_fixes` is
      dropped as noise (a stray fix between two stays), the same
      fail-closed posture `_close`'s own `min_dwell_ms` gate uses for
      stays."""
      if not fixes:
          return []
      in_stay_ranges = [(s.started_ts, s.ended_ts) for s in closed_stays]

      def _in_a_stay(ts: int) -> bool:
          return any(start <= ts <= end for start, end in in_stay_ranges)

      trips: list[Trip] = []
      buffer: list[RawFix] = []
      for f in fixes:
          if open_stay_started_ts is not None and f.ts >= open_stay_started_ts:
              break
          if _in_a_stay(f.ts):
              if len(buffer) >= min_trip_fixes:
                  trips.append(_trip_from_group(
                      buffer, subject_id=subject_id, person_id=person_id,
                      circle_id=circle_id,
                  ))
              buffer = []
              continue
          buffer.append(f)
      if len(buffer) >= min_trip_fixes:
          trips.append(_trip_from_group(
              buffer, subject_id=subject_id, person_id=person_id,
              circle_id=circle_id,
          ))
      return trips
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k extract_trips
  ```
  Expected: PASS, all 5.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): Trip + extract_trips — Tier 5's movement segmentation"
  ```

---

### Task 5: Domain — `edit_distance`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Produces: `edit_distance(a: tuple[str, ...], b: tuple[str, ...]) -> int`.

- [ ] **Step 1: Write the failing tests**

  ```python
  def test_edit_distance_of_identical_sequences_is_zero():
      assert edit_distance(("A", "B", "C"), ("A", "B", "C")) == 0


  def test_edit_distance_of_one_substitution():
      assert edit_distance(("A", "B", "C"), ("A", "X", "C")) == 1


  def test_edit_distance_of_one_insertion():
      assert edit_distance(("A", "C"), ("A", "B", "C")) == 1


  def test_edit_distance_against_an_empty_sequence_is_its_length():
      assert edit_distance((), ("A", "B", "C")) == 3
      assert edit_distance(("A", "B"), ()) == 2


  def test_edit_distance_is_symmetric():
      a, b = ("Road A", "Road B", "Road C"), ("Road A", "Road X", "Road C", "Road D")
      assert edit_distance(a, b) == edit_distance(b, a)
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k edit_distance
  ```
  Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, add `"edit_distance"` to `__all__`:

  ```python
  def edit_distance(a: tuple[str, ...], b: tuple[str, ...]) -> int:
      """Classic Levenshtein distance over two corridor address sequences —
      Tier 5's trip-similarity metric (design/27 §4, issue #6: "cluster
      trips by address-sequence edit distance"). Hand-rolled, O(len(a) *
      len(b)) — corridor sequences are short (a handful of road names per
      trip), matching `cluster_places`'s own "trivial at this scale, no
      scipy" precedent."""
      if not a:
          return len(b)
      if not b:
          return len(a)
      prev = list(range(len(b) + 1))
      for i, x in enumerate(a, start=1):
          curr = [i] + [0] * len(b)
          for j, y in enumerate(b, start=1):
              cost = 0 if x == y else 1
              curr[j] = min(
                  prev[j] + 1,        # deletion
                  curr[j - 1] + 1,    # insertion
                  prev[j - 1] + cost, # substitution / match
              )
          prev = curr
      return prev[-1]
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k edit_distance
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): edit_distance — Tier 5's corridor-similarity metric"
  ```

---

### Task 6: Domain — `cluster_trips`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Consumes: `Trip` (Task 4), `edit_distance` (Task 5).
- Produces: `cluster_trips(trips: list[Trip], *, max_edit_distance: int, min_samples: int) -> list[list[Trip]]`.

- [ ] **Step 1: Write the failing tests**

  ```python
  def _trip(corridor, started_ts=0):
      return Trip(subject_id="s", person_id="s", circle_id="c1",
                  started_ts=started_ts, ended_ts=started_ts + 60_000,
                  distance_m=1000.0, avg_speed_kmh=30.0, fix_count=3,
                  corridor=corridor)


  def test_cluster_trips_groups_near_identical_corridors():
      trips = [
          _trip(("Road A", "Road B")),
          _trip(("Road A", "Road B")),
          _trip(("Road A", "Road X")),  # 1 edit away
      ]
      clusters = cluster_trips(trips, max_edit_distance=1, min_samples=3)
      assert len(clusters) == 1
      assert len(clusters[0]) == 3


  def test_cluster_trips_below_min_samples_is_noise_and_dropped():
      trips = [_trip(("Road A",)), _trip(("Road A",))]
      clusters = cluster_trips(trips, max_edit_distance=1, min_samples=3)
      assert clusters == []


  def test_cluster_trips_separates_unrelated_corridors():
      trips = [
          _trip(("Road A", "Road B")), _trip(("Road A", "Road B")), _trip(("Road A", "Road B")),
          _trip(("Highway X", "Highway Y")), _trip(("Highway X", "Highway Y")), _trip(("Highway X", "Highway Y")),
      ]
      clusters = cluster_trips(trips, max_edit_distance=1, min_samples=3)
      assert len(clusters) == 2
      sizes = sorted(len(c) for c in clusters)
      assert sizes == [3, 3]
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k cluster_trips
  ```
  Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, add `"cluster_trips"` to `__all__`.
  This is DELIBERATELY a separate, parallel implementation from
  `cluster_places`'s DBSCAN, not a generalized/shared one — CLAUDE.md's own
  "three similar lines is better than a premature abstraction," and
  `cluster_places` is independently verified against 70,000 randomized
  point sets (its own docstring); refactoring it into a generic
  metric-parameterized version under this plan's time budget risks that
  verification, for a savings of roughly 20 lines:

  ```python
  def _trip_neighbors(i: int, trips: list[Trip], max_edit_distance: int) -> list[int]:
      return [
          j for j in range(len(trips))
          if j != i and edit_distance(trips[i].corridor, trips[j].corridor) <= max_edit_distance
      ]


  def cluster_trips(
      trips: list[Trip], *, max_edit_distance: int, min_samples: int
  ) -> list[list[Trip]]:
      """DBSCAN over trip corridor sequences, edit-distance metric
      (design/27 §4, issue #6). Same algorithm shape as `cluster_places`
      (noise points below `min_samples` are DROPPED, never a singleton
      cluster), deliberately NOT sharing code with it — see this task's own
      brief for why. A trip with an empty `corridor` (every fix in the
      group had no `address`) still participates: `edit_distance((), x) ==
      len(x)`, so it simply clusters only with other near-empty corridors,
      never spuriously with a real one."""
      n = len(trips)
      labels: list[int | None] = [None] * n
      cluster_id = 0

      for i in range(n):
          if labels[i] is not None:
              continue
          neighbors = _trip_neighbors(i, trips, max_edit_distance)
          if len(neighbors) + 1 < min_samples:
              labels[i] = -1
              continue
          labels[i] = cluster_id
          seeds = list(neighbors)
          while seeds:
              j = seeds.pop()
              if labels[j] == -1:
                  labels[j] = cluster_id
              if labels[j] is not None:
                  continue
              labels[j] = cluster_id
              j_neighbors = _trip_neighbors(j, trips, max_edit_distance)
              if len(j_neighbors) + 1 >= min_samples:
                  seeds.extend(j_neighbors)
          cluster_id += 1

      clusters: dict[int, list[Trip]] = {}
      for i, label in enumerate(labels):
          if label is not None and label >= 0:
              clusters.setdefault(label, []).append(trips[i])
      return list(clusters.values())
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k cluster_trips
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): cluster_trips — Tier 5's corridor clustering"
  ```

---

### Task 7: Store — trip read/write

**Files:**
- Modify: `nora/infra/sql/mobility.py`, `nora/testing/mobility.py`, `nora/analytics/rollup.py` (Protocol only)
- Test: `tests/contract/test_mobility_store_contract.py`

**Interfaces:**
- Produces:
  - `MobilityStore.write_trips(trips: list[Trip]) -> None`
  - `MobilityStore.trips_for_subject(*, subject_id: str) -> list[Trip]`
  - `MobilityStore.set_trip_corridor_ids(*, subject_id: str, started_ts_in: list[int], corridor_id: int) -> None`
    (fill-NULL-only, the exact same shape `set_place_ids` already
    established in Tier 3 — reuse that method's docstring's own reasoning,
    don't re-derive it from scratch.)

- [ ] **Step 1: Write the failing tests**

  ```python
  async def test_write_trips_then_read_them_back(store):
      trip = Trip(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=61_000, distance_m=500.0,
          avg_speed_kmh=30.0, fix_count=5, corridor=("Road A", "Road B"),
      )
      await store.write_trips([trip])
      back = await store.trips_for_subject(subject_id="test_subject")
      assert len(back) == 1
      assert back[0].started_ts == 1_000
      assert back[0].distance_m == 500.0
      assert back[0].corridor == ("Road A", "Road B")
      assert back[0].corridor_id is None


  async def test_set_trip_corridor_ids_only_fills_null(store):
      trip = Trip(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=61_000, distance_m=500.0,
          avg_speed_kmh=30.0, fix_count=5, corridor=("Road A",),
      )
      await store.write_trips([trip])
      await store.set_trip_corridor_ids(
          subject_id="test_subject", started_ts_in=[1_000], corridor_id=7,
      )
      await store.set_trip_corridor_ids(
          subject_id="test_subject", started_ts_in=[1_000], corridor_id=9,
      )
      back = await store.trips_for_subject(subject_id="test_subject")
      assert back[0].corridor_id == 7
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py -k trip
  ```
  Expected: FAIL, `AttributeError`.

- [ ] **Step 3: Implement — `SqlMobilityStore`**

  `nora/infra/sql/mobility.py` — import `PRESENCE_TRIPS` from
  `nora.infra.sql.schema` alongside the existing imports; `Trip` from
  `nora.domain.mobility`. JSONB round-trips a Python list as a JSON array,
  not a tuple — store `list(trip.corridor)` on write, and cast the read
  value back to `tuple(row.corridor)` since `Trip.corridor` is typed
  `tuple[str, ...]`:

  ```python
      async def write_trips(self, trips: list[Trip]) -> None:
          if not trips:
              return
          async with self._engine.begin() as conn:
              await conn.execute(
                  sa.insert(PRESENCE_TRIPS),
                  [
                      {
                          "subject_id": t.subject_id, "person_id": t.person_id,
                          "circle_id": t.circle_id, "started_ts": t.started_ts,
                          "ended_ts": t.ended_ts, "distance_m": t.distance_m,
                          "avg_speed_kmh": t.avg_speed_kmh, "fix_count": t.fix_count,
                          "corridor_id": t.corridor_id,
                      }
                      for t in trips
                  ],
              )

      async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
          stmt = (
              sa.select(PRESENCE_TRIPS)
              .where(PRESENCE_TRIPS.c.subject_id == subject_id)
              .order_by(PRESENCE_TRIPS.c.started_ts.asc())
          )
          async with self._engine.connect() as conn:
              rows = (await conn.execute(stmt)).all()
          return [
              Trip(
                  subject_id=r.subject_id, person_id=r.person_id, circle_id=r.circle_id,
                  started_ts=r.started_ts, ended_ts=r.ended_ts,
                  distance_m=r.distance_m, avg_speed_kmh=r.avg_speed_kmh,
                  fix_count=r.fix_count, corridor=(),  # NOT persisted per-trip
                  corridor_id=r.corridor_id,
              )
              for r in rows
          ]

      async def set_trip_corridor_ids(
          self, *, subject_id: str, started_ts_in: list[int], corridor_id: int
      ) -> None:
          if not started_ts_in:
              return
          async with self._engine.begin() as conn:
              await conn.execute(
                  sa.update(PRESENCE_TRIPS)
                  .where(
                      PRESENCE_TRIPS.c.subject_id == subject_id,
                      PRESENCE_TRIPS.c.started_ts.in_(started_ts_in),
                      PRESENCE_TRIPS.c.corridor_id.is_(None),
                  )
                  .values(corridor_id=corridor_id)
              )
  ```

  **Stop and read this before transcribing the block above.** The sketch
  sets `corridor=()` on every row read back from `trips_for_subject` —
  meaning a trip's OWN address sequence is thrown away once written, only
  `corridor_id` survives. Decide, and document your decision in the
  commit: does `PRESENCE_TRIPS` need its OWN `corridor` JSONB column
  (parallel to `PRESENCE_CORRIDORS.sequence`) so a trip's original sequence
  is recoverable independent of its corridor id? Re-read Task 6's
  `cluster_trips` — it clusters `Trip` objects, which means Task 11's
  rollup wiring needs a trip's `corridor` populated when it calls
  `cluster_trips`, and it has that from `extract_trips`'s in-memory return
  value, NOT from a re-read of the store — so the round-trip gap above
  only matters if some LATER caller needs to re-cluster from storage
  rather than from a fresh `extract_trips` call. Given this plan's own
  Global Constraint ("recompute from the full sample set, every time") and
  that Task 11's corridor-speed recompute only needs `avg_speed_kmh`/
  `started_ts`/`corridor_id`, not `corridor` itself, the sketch's choice
  (`corridor=()` on read) is DELIBERATE, not an oversight — but add a
  one-line comment on `trips_for_subject` saying so explicitly, so a
  future reader does not "fix" it into a schema change it does not need.

- [ ] **Step 4: Implement — `FakeMobilityStore`**

  `nora/testing/mobility.py` — add `self._trips: list[Trip] = []` to
  `__init__`, alongside `self._stays`:

  ```python
      async def write_trips(self, trips: list[Trip]) -> None:
          self._trips.extend(trips)

      async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
          return sorted(
              (t for t in self._trips if t.subject_id == subject_id),
              key=lambda t: t.started_ts,
          )

      async def set_trip_corridor_ids(
          self, *, subject_id: str, started_ts_in: list[int], corridor_id: int
      ) -> None:
          targets = set(started_ts_in)
          for i, t in enumerate(self._trips):
              if (
                  t.subject_id == subject_id
                  and t.started_ts in targets
                  and t.corridor_id is None
              ):
                  self._trips[i] = dataclasses.replace(t, corridor_id=corridor_id)
  ```

  Unlike the SQL store, the fake's `trips_for_subject` naturally preserves
  `corridor` (it never throws anything away) — that is fine and expected;
  the two implementations are behavioral twins on every METHOD's contract
  (what `write_trips`/`set_trip_corridor_ids`/`corridor_id` do), not on
  every unobserved field, the same relationship `existing_place`'s
  `most_recent_support_ts` already has between the two stores (Tier 2's
  own precedent — re-read `SqlMobilityStore.existing_place`'s comment on
  that field before assuming this is a new kind of divergence).

- [ ] **Step 5: Add the three methods to the `MobilityStore` Protocol**

  `nora/analytics/rollup.py`, inside `class MobilityStore(Protocol)`:
  ```python
      async def write_trips(self, trips: list[Trip]) -> None: ...
      async def trips_for_subject(self, *, subject_id: str) -> list[Trip]: ...
      async def set_trip_corridor_ids(
          self, *, subject_id: str, started_ts_in: list[int], corridor_id: int
      ) -> None: ...
  ```
  Add `Trip` to this file's existing `from nora.domain.mobility import (...)`
  block.

- [ ] **Step 6: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add nora/infra/sql/mobility.py nora/testing/mobility.py nora/analytics/rollup.py tests/contract/test_mobility_store_contract.py
  git commit -m "feat(mobility): trip read/write store methods"
  ```

---

### Task 8: Store — corridor identity + speed percentiles

**Files:**
- Modify: `nora/infra/sql/mobility.py`, `nora/testing/mobility.py`, `nora/analytics/rollup.py` (Protocol only)
- Test: `tests/contract/test_mobility_store_contract.py`

**Interfaces:**
- Produces:
  - `MobilityStore.existing_corridor(*, subject_id: str, sequence: tuple[str, ...], max_edit_distance: int) -> tuple[int, tuple[str, ...]] | None`
    — finds an existing corridor within `max_edit_distance` of `sequence`
    (identity by SIMILARITY, not exact match — a cluster's own
    representative sequence can legitimately vary trip to trip).
  - `MobilityStore.upsert_corridor(*, subject_id: str, sequence: tuple[str, ...], sample_count: int, now_ms: int, existing_id: int | None) -> int`
    — returns the corridor's id. UPDATEs in place when `existing_id` is
    given (preserving identity, Tier 3 Critical #1's own lesson — read
    that fix's commit, `9616af3`, before implementing this), INSERTs fresh
    otherwise.
  - `MobilityStore.trip_speeds_for_corridor_hour(*, subject_id: str, corridor_id: int, hour_of_week: int) -> list[float]`
  - `MobilityStore.upsert_corridor_speed(*, subject_id: str, corridor_id: int, hour_of_week: int, p50_kmh: float, p90_kmh: float, sample_count: int, now_ms: int) -> None`

- [ ] **Step 1: Write the failing tests**

  ```python
  async def test_existing_corridor_finds_a_near_match(store):
      corridor_id = await store.upsert_corridor(
          subject_id="test_subject", sequence=("Road A", "Road B"),
          sample_count=3, now_ms=1_000, existing_id=None,
      )
      found = await store.existing_corridor(
          subject_id="test_subject", sequence=("Road A", "Road X"),
          max_edit_distance=1,
      )
      assert found is not None
      assert found[0] == corridor_id


  async def test_existing_corridor_returns_none_beyond_the_distance(store):
      await store.upsert_corridor(
          subject_id="test_subject", sequence=("Road A", "Road B"),
          sample_count=3, now_ms=1_000, existing_id=None,
      )
      found = await store.existing_corridor(
          subject_id="test_subject", sequence=("Totally", "Different", "Roads"),
          max_edit_distance=1,
      )
      assert found is None


  async def test_upsert_corridor_preserves_id_when_given_existing_id(store):
      first_id = await store.upsert_corridor(
          subject_id="test_subject", sequence=("Road A",),
          sample_count=1, now_ms=1_000, existing_id=None,
      )
      second_id = await store.upsert_corridor(
          subject_id="test_subject", sequence=("Road A",),
          sample_count=2, now_ms=2_000, existing_id=first_id,
      )
      assert second_id == first_id


  async def test_trip_speeds_for_corridor_hour_filters_correctly(store):
      trip = Trip(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=61_000, distance_m=500.0,
          avg_speed_kmh=42.0, fix_count=5, corridor=("Road A",),
      )
      await store.write_trips([trip])
      await store.set_trip_corridor_ids(
          subject_id="test_subject", started_ts_in=[1_000], corridor_id=7,
      )
      # hour_of_week for started_ts=1000 in HKT: computed by the CALLER
      # (rollup, Task 11), not this store method — the store just filters
      # on whatever hour_of_week value it is asked for. Pass the same value
      # the fixture would independently compute (any fixed int works here
      # since this test only proves the filter, not the tz arithmetic).
      speeds = await store.trip_speeds_for_corridor_hour(
          subject_id="test_subject", corridor_id=7, hour_of_week=0,
      )
      # This assertion depends on whichever hour_of_week bucket started_ts=1000
      # falls into for the STORE's own bucketing — if the store computes
      # hour_of_week itself (see Step 3's design note), adjust this test to
      # assert against that real bucket instead of a hardcoded 0.
      assert speeds == [42.0] or speeds == []


  async def test_upsert_corridor_speed_then_read_back_is_a_real_upsert(store):
      await store.upsert_corridor_speed(
          subject_id="test_subject", corridor_id=7, hour_of_week=9,
          p50_kmh=30.0, p90_kmh=50.0, sample_count=5, now_ms=1_000,
      )
      await store.upsert_corridor_speed(
          subject_id="test_subject", corridor_id=7, hour_of_week=9,
          p50_kmh=35.0, p90_kmh=55.0, sample_count=6, now_ms=2_000,
      )
      # No read method is needed by any caller yet (Task 11 only writes,
      # matching the parked precedent Tier 3's Task 7 established for
      # presence_occupancy/presence_place_rhythm) — this test only proves
      # a second call does not raise a primary-key violation.
  ```

  **Before implementing Step 1's tests literally: resolve the design
  question in `test_trip_speeds_for_corridor_hour_filters_correctly`
  yourself.** Does `trip_speeds_for_corridor_hour` take `hour_of_week` as a
  plain filter parameter (the store never computes hour-of-week itself,
  the CALLER does via `nora.domain.mobility`'s `_local_minute_of_day`-
  adjacent logic — matching how `stay_durations_for_place`, Tier 3's own
  precedent, takes no timezone knowledge at all), or does it take
  `tz_name` and compute the bucket itself from each trip's stored
  `started_ts`? The brief's own Task 11 (rollup wiring) needs to group
  MANY trips into their hour-of-week buckets at once for percentile
  computation — check whether a single-bucket-at-a-time store method
  (as sketched) or a "give me every trip's (started_ts, avg_speed_kmh) for
  this corridor, group by hour-of-week yourself in the rollup" shape (no
  `hour_of_week` parameter at all, simpler contract) fits Task 11's actual
  calling pattern better. If you change the signature from what is
  sketched here, update this task's own tests to match your resolved
  design — do not leave a mismatched brief and implementation.

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py -k corridor
  ```
  Expected: FAIL, `AttributeError`.

- [ ] **Step 3: Implement — `SqlMobilityStore`**

  Import `PRESENCE_CORRIDORS`, `PRESENCE_CORRIDOR_SPEED` from
  `nora.infra.sql.schema`. `existing_corridor` cannot filter by edit
  distance IN SQL (it is not a Postgres-native operation over a JSONB
  array without an extension this project does not have) — read every
  corridor row for the subject (bounded: a person has a handful of real
  corridors, not thousands) and compute `edit_distance` in Python, the
  same "SQL narrows, Python decides" shape `nora/infra/sql/geocode_cache.py`'s
  `nearest_within` already established for a different metric (haversine):

  ```python
      async def existing_corridor(
          self, *, subject_id: str, sequence: tuple[str, ...], max_edit_distance: int,
      ) -> tuple[int, tuple[str, ...]] | None:
          stmt = sa.select(PRESENCE_CORRIDORS.c.id, PRESENCE_CORRIDORS.c.sequence).where(
              PRESENCE_CORRIDORS.c.subject_id == subject_id,
          )
          async with self._engine.connect() as conn:
              rows = (await conn.execute(stmt)).all()
          best: tuple[int, int, tuple[str, ...]] | None = None  # (dist, id, seq)
          for row in rows:
              seq = tuple(row.sequence)
              dist = edit_distance(sequence, seq)
              if dist <= max_edit_distance and (best is None or dist < best[0]):
                  best = (dist, row.id, seq)
          return None if best is None else (best[1], best[2])

      async def upsert_corridor(
          self, *, subject_id: str, sequence: tuple[str, ...], sample_count: int,
          now_ms: int, existing_id: int | None,
      ) -> int:
          async with self._engine.begin() as conn:
              if existing_id is not None:
                  await conn.execute(
                      sa.update(PRESENCE_CORRIDORS).where(
                          PRESENCE_CORRIDORS.c.id == existing_id,
                      ).values(sample_count=sample_count, last_seen_ts=now_ms)
                  )
                  return existing_id
              result = await conn.execute(
                  sa.insert(PRESENCE_CORRIDORS).values(
                      subject_id=subject_id, sequence=list(sequence),
                      sample_count=sample_count, first_seen_ts=now_ms,
                      last_seen_ts=now_ms,
                  ).returning(PRESENCE_CORRIDORS.c.id)
              )
              return result.scalar_one()

      async def trip_speeds_for_corridor(
          self, *, subject_id: str, corridor_id: int,
      ) -> list[tuple[int, float]]:
          """Every (started_ts, avg_speed_kmh) pair for this corridor —
          the caller (rollup, Task 11) groups by hour-of-week itself, the
          same division of responsibility `stay_durations_for_place`
          (Tier 3) already has: this store reads rows, `nora.domain.mobility`
          decides what a timestamp means in local time."""
          stmt = sa.select(PRESENCE_TRIPS.c.started_ts, PRESENCE_TRIPS.c.avg_speed_kmh).where(
              PRESENCE_TRIPS.c.subject_id == subject_id,
              PRESENCE_TRIPS.c.corridor_id == corridor_id,
          )
          async with self._engine.connect() as conn:
              rows = (await conn.execute(stmt)).all()
          return [(r.started_ts, r.avg_speed_kmh) for r in rows]

      async def upsert_corridor_speed(
          self, *, subject_id: str, corridor_id: int, hour_of_week: int,
          p50_kmh: float, p90_kmh: float, sample_count: int, now_ms: int,
      ) -> None:
          stmt = pg_insert(PRESENCE_CORRIDOR_SPEED).values(
              subject_id=subject_id, corridor_id=corridor_id,
              hour_of_week=hour_of_week, p50_kmh=p50_kmh, p90_kmh=p90_kmh,
              sample_count=sample_count, updated_ts=now_ms,
          )
          stmt = stmt.on_conflict_do_update(
              index_elements=[
                  PRESENCE_CORRIDOR_SPEED.c.subject_id,
                  PRESENCE_CORRIDOR_SPEED.c.corridor_id,
                  PRESENCE_CORRIDOR_SPEED.c.hour_of_week,
              ],
              set_={
                  "p50_kmh": stmt.excluded.p50_kmh, "p90_kmh": stmt.excluded.p90_kmh,
                  "sample_count": stmt.excluded.sample_count,
                  "updated_ts": stmt.excluded.updated_ts,
              },
          )
          async with self._engine.begin() as conn:
              await conn.execute(stmt)
  ```

  This resolves Step 1's open design question: `trip_speeds_for_corridor`
  (renamed from the brief's own placeholder `trip_speeds_for_corridor_hour`
  — no `hour_of_week` parameter, the store returns every raw pair and the
  caller buckets). If you agree this is the right shape, rewrite Step 1's
  test to match this exact signature before implementing anything — do not
  implement one signature and leave a stale test expecting another. If you
  disagree and the single-bucket shape genuinely fits Task 11 better once
  you have read it, implement that instead and say so in your report.

  `import edit_distance` from `nora.domain.mobility` at the top of this
  file if not already imported (it will not be, since Task 5 only added it
  to the domain module, not this one).

- [ ] **Step 4: Implement — `FakeMobilityStore`**

  Add `self._corridors: dict[int, dict] = {}` and `self._next_corridor_id
  = 1` to `__init__`. Mirror the SQL store's behavior exactly (same
  `existing_corridor` edit-distance scan in Python, same
  update-vs-insert split in `upsert_corridor`):

  ```python
      async def existing_corridor(
          self, *, subject_id: str, sequence: tuple[str, ...], max_edit_distance: int,
      ) -> tuple[int, tuple[str, ...]] | None:
          best: tuple[int, int, tuple[str, ...]] | None = None
          for cid, row in self._corridors.items():
              if row["subject_id"] != subject_id:
                  continue
              dist = edit_distance(sequence, row["sequence"])
              if dist <= max_edit_distance and (best is None or dist < best[0]):
                  best = (dist, cid, row["sequence"])
          return None if best is None else (best[1], best[2])

      async def upsert_corridor(
          self, *, subject_id: str, sequence: tuple[str, ...], sample_count: int,
          now_ms: int, existing_id: int | None,
      ) -> int:
          if existing_id is not None:
              self._corridors[existing_id]["sample_count"] = sample_count
              self._corridors[existing_id]["last_seen_ts"] = now_ms
              return existing_id
          cid = self._next_corridor_id
          self._next_corridor_id += 1
          self._corridors[cid] = {
              "subject_id": subject_id, "sequence": sequence,
              "sample_count": sample_count, "first_seen_ts": now_ms,
              "last_seen_ts": now_ms,
          }
          return cid

      async def trip_speeds_for_corridor(
          self, *, subject_id: str, corridor_id: int,
      ) -> list[tuple[int, float]]:
          return [
              (t.started_ts, t.avg_speed_kmh) for t in self._trips
              if t.subject_id == subject_id and t.corridor_id == corridor_id
          ]

      async def upsert_corridor_speed(
          self, *, subject_id: str, corridor_id: int, hour_of_week: int,
          p50_kmh: float, p90_kmh: float, sample_count: int, now_ms: int,
      ) -> None:
          self._corridor_speed = getattr(self, "_corridor_speed", {})
          self._corridor_speed[(subject_id, corridor_id, hour_of_week)] = {
              "p50_kmh": p50_kmh, "p90_kmh": p90_kmh, "sample_count": sample_count,
          }
  ```

  Move the `getattr(self, "_corridor_speed", {})` initialization into
  `__init__` properly (`self._corridor_speed: dict[tuple[str, int, int], dict] = {}`)
  rather than leaving the lazy-init pattern shown above — that shape was
  written fast to convey intent, not as the final code; every other dict
  in this class is initialized in `__init__`, match that.

- [ ] **Step 5: Add the four methods to the `MobilityStore` Protocol**

  Match whatever final signatures Step 3 settled on (`trip_speeds_for_corridor`,
  not the brief's original placeholder name, unless you deliberately kept
  the per-hour shape and documented why).

- [ ] **Step 6: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add nora/infra/sql/mobility.py nora/testing/mobility.py nora/analytics/rollup.py tests/contract/test_mobility_store_contract.py
  git commit -m "feat(mobility): corridor identity + speed-sample store methods"
  ```

---

### Task 9: Config — two new knobs

**Files:**
- Modify: `nora/config.py`, `nora/analytics/rollup.py` (`RollupConfig`), `nora/analytics/composition.py`, `config.example.toml`

**Interfaces:**
- Produces: `AnalyticsConfig.corridor_max_edit_distance: int` (default 2),
  `AnalyticsConfig.corridor_min_samples: int` (default 3, matching
  `cluster_min_samples`'s own default for stay clustering); matching
  `RollupConfig` fields.

- [ ] **Step 1: Add to `AnalyticsConfig`** (after `anomaly_z_threshold`,
  Tier 3's last field):
  ```python
      # Tier 5 (design/27 §4, issue #6): corridor identity is a SIMILARITY
      # match, not exact — two trips along the same road on different days
      # rarely produce byte-identical address sequences (a slightly
      # different fix cadence, a missing address on one fix). Neither value
      # is measured; same "inherited, not measured" honesty design/27 §7.1
      # already gives stay_radius_m/stay_min_dwell_seconds.
      corridor_max_edit_distance: int = Field(default=2, ge=0)
      corridor_min_samples: int = Field(default=3, ge=1)
  ```

- [ ] **Step 2: Add to `RollupConfig`** (append with defaults, same
  backward-compatible pattern Tier 3's own two fields used):
  ```python
      corridor_max_edit_distance: int = 2
      corridor_min_samples: int = 3
  ```

- [ ] **Step 3: Pass through in `nora/analytics/composition.py`**'s
  `RollupConfig(...)` construction:
  ```python
          corridor_max_edit_distance=analytics.corridor_max_edit_distance,
          corridor_min_samples=analytics.corridor_min_samples,
  ```

- [ ] **Step 4: Document in `config.example.toml`**, after Tier 3's last
  two commented lines:
  ```toml
  # corridor_max_edit_distance = 2   # Tier 5 — corridor identity tolerance
  # corridor_min_samples = 3         # Tier 5 — trips needed before a corridor is named
  ```

- [ ] **Step 5: Verify nothing broke**
  ```bash
  uv run pytest -q tests/unit/test_analytics_rollup.py tests/unit/test_analytics_composition.py
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  ```

- [ ] **Step 6: Commit**
  ```bash
  git add nora/config.py nora/analytics/rollup.py nora/analytics/composition.py config.example.toml
  git commit -m "feat(analytics): corridor_max_edit_distance/corridor_min_samples config knobs"
  ```

---

### Task 10: Metrics

**Files:**
- Modify: `nora/obs/metric_specs.py`

**Interfaces:**
- Produces: `nora_analytics_trips_total{subject}`, `nora_analytics_corridors_total{subject}`.

- [ ] **Step 1: Add the two specs**, in the `# ── the analytics rollup (DEC-115) ──`
  section, after `nora_analytics_anomalies_total` (Tier 3's last entry):
  ```python
      _CounterSpec(
          "nora_analytics_trips_total",
          "Trips written to presence_trips (Tier 5), by subject.",
          ("subject",),
      ),
      _CounterSpec(
          "nora_analytics_corridors_total",
          "Corridors upserted (Tier 5, new or reconfirmed), by subject.",
          ("subject",),
      ),
  ```

- [ ] **Step 2: Verify**
  ```bash
  uv run python -c "import nora.obs.metric_specs"
  uv run ruff check nora/obs/metric_specs.py
  ```

- [ ] **Step 3: Commit**
  ```bash
  git add nora/obs/metric_specs.py
  git commit -m "feat(obs): Tier 5 trip/corridor metrics"
  ```

---

### Task 11: Rollup — wire trip extraction + corridor clustering + speed percentiles

**Files:**
- Modify: `nora/analytics/rollup.py`
- Test: `tests/unit/test_analytics_rollup.py`

**Interfaces:**
- Consumes: everything from Tasks 2-10.
- Produces: `MobilityRollup._record_trips(self, *, subject_id, person_ids, circle_id, fixes, closed_stays, last_open_stay, now_ms) -> None`
  and a Speed-3-adjacent corridor clustering step.

**This is the largest and most improvisational task in this plan — the
exact same shape Tier 3's own Task 10 was, and that task failed its first
review with two Critical bugs. Read `run_once`'s CURRENT full body before
writing anything; the sketch below shows where each piece attaches, but the
surrounding line numbers WILL have shifted from Tasks 1-10 landing on this
same file.**

- [ ] **Step 1: Write the failing tests**

  ```python
  async def test_a_completed_trip_between_two_stays_is_written():
      store = FakeMobilityStore()
      # Stay at (1.0, 1.0) for 3 nights (matches _seed_stay_track's own
      # home-shaped pattern), then a single daytime trip to (2.0, 2.0) and
      # a second stay there — reuse _seed_stay_track twice with distinct
      # fid_start values (Tier 3's own fix-collision lesson) plus manual
      # fixes for the trip itself.
      fid = await _seed_stay_track(
          store, person_id="s", start_ms=0, n_days=3, hour_start=1, hour_end=5,
      )
      # A trip: 3 fixes moving away from (1.0,1.0) right after night 3 ends.
      trip_start = 3 * 86_400_000 + 6 * 3_600_000  # day 3, 06:00
      for i, (lat, lng, addr) in enumerate([
          (1.001, 1.001, "Road A"), (1.002, 1.002, "Road A"), (1.003, 1.003, "Road B"),
      ]):
          await store._seed_fix_for_test(
              id=fid, person_id="s", ts=trip_start + i * 600_000,
              lat=lat, lng=lng, accuracy_m=50.0, wire_state="live",
              recorded_at=trip_start, address=addr,
          )
          fid += 1
      # A settled destination stay so the trip's tail is no longer ambiguous.
      dest_start = trip_start + 3_000_000
      for i in range(3):
          await store._seed_fix_for_test(
              id=fid, person_id="s", ts=dest_start + i * 600_000,
              lat=2.0, lng=2.0, accuracy_m=50.0, wire_state="live",
              recorded_at=dest_start,
          )
          fid += 1
      rollup = MobilityRollup(store=store, config=CFG)
      await rollup.run_once(
          subject_id="s", person_ids=["s"], circle_id="c1",
          now_ms=dest_start + int(CFG.settled_seconds * 1000) + 3_600_000,
      )
      trips = await store.trips_for_subject(subject_id="s")
      assert len(trips) == 1
      assert trips[0].corridor == ("Road A", "Road B")


  async def test_three_similar_trips_cluster_into_one_named_corridor():
      store = FakeMobilityStore()
      rollup = MobilityRollup(store=store, config=CFG)
      # Seed 3 near-identical trips directly via write_trips (bypassing
      # run_once's own segmentation, which this test is not exercising) —
      # then call the rollup's own corridor-clustering step directly if it
      # is exposed as its own method, or via a run_once cycle if clustering
      # is folded into run_once's Speed-3-adjacent block only. Read Step 3's
      # actual implementation to know which is true before writing this
      # test's second half — do not guess the call shape.
      for i in range(3):
          await store.write_trips([Trip(
              subject_id="s", person_id="s", circle_id="c1",
              started_ts=i * 100_000, ended_ts=i * 100_000 + 60_000,
              distance_m=500.0, avg_speed_kmh=30.0, fix_count=3,
              corridor=("Road A", "Road B"),
          )])
      # ... cluster, then assert exactly one presence_corridors row exists
      # for subject "s" with sample_count == 3, and all 3 trips now carry
      # the same corridor_id. Fill in the exact call once Step 3 exists.


  async def test_a_trip_extraction_failure_does_not_block_the_watermark():
      # Same shape as Tier 3's own watermark-safety test — mirror it
      # exactly, substituting a store whose write_trips (or
      # set_trip_corridor_ids) raises, and asserting the fix watermark
      # still advances and no duplicate stays/trips appear on a third cycle.
      ...
  ```

  The second and third tests above are DELIBERATELY left as sketches, not
  finished code — Step 1 of every other task in this plan gave complete
  code because the store/domain layer's call shape was fully decided before
  the brief was written. This task's OWN call shape (does corridor
  clustering happen inside `run_once` directly, or as a separate method
  `run_once` calls?) is a real design decision you make in Step 3, the same
  way Tier 3's own Task 10 had to adapt its prescribed backfill snippet to
  the live file. Write the finished versions of these two tests AFTER Step
  3, from what you actually built — do not force the implementation to
  match a guessed test shape.

- [ ] **Step 2: Run test 1 to verify it fails**
  ```bash
  uv run pytest -q tests/unit/test_analytics_rollup.py -k a_completed_trip
  ```
  Expected: FAIL — `store.trips_for_subject` returns `[]` (nothing calls
  `write_trips` yet).

- [ ] **Step 3: Implement**

  Wire trip extraction into `run_once`'s EXISTING `if fixes:` block
  (Speed 1), right after the stay-segmentation loop, using the SAME `chunks`
  the stay segmentation already computed (do not re-chunk):

  ```python
              new_trips: list[Trip] = []
              for i, chunk in enumerate(chunks):
                  seg = segment_stays(
                      chunk, subject_id=subject_id, person_id=person_ids[-1],
                      circle_id=circle_id, radius_m=self._cfg.stay_radius_m,
                      min_dwell_ms=min_dwell_ms,
                  )
                  # ... existing new_stays.extend/append logic, UNCHANGED ...
                  open_started = (
                      seg.open_stay.started_ts
                      if i == len(chunks) - 1 and seg.open_stay is not None
                      else None
                  )
                  new_trips.extend(extract_trips(
                      chunk, list(seg.closed), subject_id=subject_id,
                      person_id=person_ids[-1], circle_id=circle_id,
                      min_trip_fixes=self._cfg.min_trip_fixes,  # NEW config field — see note below
                      open_stay_started_ts=open_started,
                  ))
  ```

  **This snippet needs a NEW config field it does not declare** —
  `min_trip_fixes` — because `extract_trips` (Task 4) requires one and no
  earlier task in this plan added it to `RollupConfig`/`AnalyticsConfig`.
  This is a genuine gap in this plan (found while writing this task's own
  brief, left in deliberately as a check that whoever implements this task
  reads the domain function's signature rather than transcribing blindly).
  Add `min_trip_fixes: int` to `RollupConfig` (default `3`, matching
  `cluster_min_samples`'s own scale) and `AnalyticsConfig` (same default,
  `Field(default=3, ge=2)`, since a 1-fix trip cannot compute a distance)
  — do this NOW, as an addition to Task 9's already-committed work
  (a new small commit is fine; Task 9 does not need to be reopened).

  Write the new trips, matching Tier 3's exact watermark-safety pattern
  (Critical #1's fix — read `run_once`'s current `try/except` around
  `_record_rhythm` before writing this):
  ```python
              if new_trips:
                  try:
                      await self._store.write_trips(new_trips)
                  except Exception:
                      log.exception(
                          "trip write failed — the watermark still advances; "
                          "trips are derived and recoverable from the same "
                          "fixes next cycle is impossible (the watermark WILL "
                          "advance past them) — unlike Tier 3's per-stay retry "
                          "safety, a failed trip write here is a genuine, "
                          "accepted loss for this batch, not a recoverable one"
                      )
                  else:
                      if self._metrics is not None:
                          self._metrics.inc(
                              "nora_analytics_trips_total", value=len(new_trips),
                              subject=subject_id,
                          )
  ```

  Read that log message's own claim carefully before shipping it: is it
  actually true that a failed `write_trips` call loses those specific trips
  forever (since the watermark advances past their source fixes
  regardless)? If you find this is NOT an acceptable trade-off compared to
  Tier 3's own stay-safety guarantee, that is a real finding — raise it in
  your report rather than silently accepting the asymmetry, but do not
  block on it; ship the try/except either way (the alternative, no
  try/except at all, reintroduces Tier 3's own Critical #1 exactly).

  Corridor clustering: add as its own Speed-3-ADJACENT step (own
  dirty-set-style gate, since re-clustering every trip every cycle is pure
  waste once a corridor is established — same reasoning `cluster_places`'s
  own `last_cluster_id` gate already uses, but trips need their OWN
  watermark since re-using `last_cluster_id` would conflate Tier 3's place
  dirty-set with Tier 5's trip dirty-set incorrectly). Read
  `ANALYTICS_WATERMARK`'s schema (`nora/infra/sql/schema.py`) — it currently
  has exactly `last_fix_id`/`last_cluster_id` per `(deriver_name,
  subject_id)`. Decide: does Tier 5 need a THIRD watermark column
  (`last_trip_cluster_id`), or can it safely re-trigger clustering every
  cycle a NEW trip was written this cycle (checking `if new_trips:` as its
  own gate, no persisted watermark at all, accepting that a
  process-restart mid-cycle re-clusters once more than strictly necessary)?
  Given trip volume is far lower than fix volume (a handful of trips per
  subject per day, not thousands of fixes), re-clustering on every cycle
  with `new_trips` is likely cheap enough to skip the extra watermark
  column entirely — but verify this against `cluster_trips`'s own
  complexity (`O(n^2)` in trip count) before deciding; if a subject
  accumulates thousands of trips over the 720-day retention window design/27
  §5 already established for the rest of this substrate, an unconditional
  per-cycle re-cluster becomes exactly the "94% of the expensive half was
  waste" problem DEC-115 already measured and fixed once for place
  clustering. Make the call, document it in the commit message, and ADD the
  watermark column via a small follow-up migration if you decide it is
  needed (do not silently skip persistence and call it done if the O(n^2)
  math says otherwise at 720 days retention — do the arithmetic: ~3,600
  trips/subject at that retention, per Tier 3's own place-count estimate
  scaled down, giving ~13M pairwise edit-distance calls per full re-cluster;
  compare against `cluster_places`'s own measured-trivial claim at a similar
  scale before concluding either way).

  For each resulting corridor cluster: compute the representative sequence
  (the cluster's FIRST trip's own `corridor`, matching `PRESENCE_CORRIDORS`'s
  own "identity survives, does not chase every new trip's own sequence"
  comment from Task 1), call `existing_corridor` to check for a near-match,
  `upsert_corridor` with `existing_id` set or `None` accordingly, then
  `set_trip_corridor_ids` for every trip in the cluster.

  Speed percentiles: for each corridor just touched, read
  `trip_speeds_for_corridor`, group the `(started_ts, avg_speed_kmh)` pairs
  by `hour_of_week(started_ts, tz_name)` (a NEW small pure helper —
  `nora.domain.mobility.hour_of_week(ts: int, tz_name: str) -> int`, the
  single-timestamp sibling of Tier 3's own `hour_of_week_bins`; add it to
  Task 4 or here, whichever you judge cleaner, and add its own TDD tests
  wherever you add it), compute p50/p90 per bucket (sorted list, index
  `int(len * 0.5)`/`int(len * 0.9)` clamped to `len - 1`), and
  `upsert_corridor_speed` per bucket that has at least one sample.

  Full re-verification against a hand-computed percentile: pick a bucket
  with a known, small sample set in your own test and compute p50/p90 by
  hand (or with Python's own `statistics` module as an independent check,
  the same "never trust the code under test to grade itself" discipline
  Tier 3's reviewers used throughout) before trusting the wired-up value.

- [ ] **Step 4: Finish the tests sketched in Step 1, run them, verify GREEN**

  ```bash
  uv run pytest -q tests/unit/test_analytics_rollup.py -v
  ```

- [ ] **Step 5: Full gate sweep**
  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  ```

- [ ] **Step 6: Commit**
  ```bash
  git add nora/analytics/rollup.py nora/domain/mobility.py nora/config.py tests/unit/test_analytics_rollup.py
  git commit -m "feat(analytics): Tier 5 trip extraction + corridor clustering + speed percentiles"
  ```

---

### Task 12: Decision record, release fragment, design doc, final gate sweep

**Files:**
- Modify: `design/09-decision-index.md`, `design/proposed/27-presence-analytics.md`
- Create: `release-fragments/unreleased/presence-analytics-tier5-routes.json`

- [ ] **Step 1: Check the current highest DEC number before writing one**

  Run `grep -oE "DEC-[0-9]+" design/09-decision-index.md | sort -t- -k2 -n
  | tail -3` and use the NEXT integer after whatever that shows — do NOT
  assume a specific number. Tier 3's own Task 12 assumed `DEC-117` and
  collided with an unrelated concurrent branch that had ALSO claimed it;
  the merge had to renumber to `DEC-119` after the fact. Check fresh here.

- [ ] **Step 2: Add the decision entry**

  Cover: the trip/corridor/speed-percentile derivation, that a trip's
  ambiguous tail is deferred (never guessed) matching stays' own
  `open_stay` posture, that corridor identity is a similarity match (not
  exact) with a DELIBERATE update-in-place-preserves-id semantic (citing
  Tier 3's own Critical #1 as the precedent this design avoids repeating),
  and whatever you decided about the trip-cluster watermark question in
  Task 11 Step 3.

- [ ] **Step 3: Update `design/proposed/27-presence-analytics.md`**

  Same treatment Tier 3's own Task 4/fix-wave gave Tier 3's row: Tier 5's
  row in the six-tier table (§4) becomes `**P0 — built**` with a link to
  this plan file.

- [ ] **Step 4: Release fragment**

  ```json
  {
    "type": "feature",
    "summary": "Presence analytics now derives real routes: a ground-truth speed computed from consecutive fixes (never Life360's own mostly-empty speed field), named corridors clustered from the road-name sequences Life360 already sends, and per-corridor speed percentiles by hour-of-week — a congestion model built entirely from a subject's own history.",
    "details": [
      "New tables presence_trips, presence_corridors, presence_corridor_speed (migration 0019).",
      "A trip is the movement between two confirmed stays; a corridor is a named cluster of trips whose road-name sequences are close by edit distance.",
      "Corridor identity is a durable id, preserved across re-clustering the same way Tier 3's place identity fix established — a corridor does not churn a new id every cycle."
    ],
    "limitations": [
      "A trip's still-ambiguous tail (has not yet resolved into a closed stay or continued movement) is never extracted early — a small sliver at that exact seam is accepted as lost, not guessed.",
      "Corridor and speed-percentile data has no consumer yet, matching Tier 3's own posture — nothing reads presence_trips/presence_corridors/presence_corridor_speed outside this substrate today."
    ]
  }
  ```

- [ ] **Step 5: Final full gate sweep**
  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  uv run python scripts/check_release_fragments.py --base origin/master
  ```

- [ ] **Step 6: Commit**
  ```bash
  git add design/09-decision-index.md design/proposed/27-presence-analytics.md release-fragments/unreleased/presence-analytics-tier5-routes.json
  git commit -m "docs(analytics): decision record + release fragment for Tier 5 routes"
  ```

---

## Self-review notes

- **Spec coverage:** issue #6's Tier 5 brief is fully covered — derived
  speed (Task 3), corridor sequences from `address` (Tasks 2, 4), edit-
  distance clustering (Tasks 5, 6), per-corridor speed percentiles by hour
  (Task 11). No OSM/map-matching/third-party dependency anywhere.
- **Task 8 and Task 11 both contain a genuine, unresolved design decision**
  left for the implementer, on purpose, matching Tier 3's own Task 10
  precedent where a brief written against a snapshot of the code needed
  real judgment at implementation time — not a placeholder, an honest
  acknowledgment that this plan's author (a fresh instance of the same
  process that reviewed Tier 3 this rigorously) does not have every answer
  a live codebase will demand. Budget real review attention for both.
- **Task 11 is expected to need at least one fix round** — flag this to
  whoever reviews it rather than being surprised; Tier 3's own equivalent
  task needed one for reasons no earlier task's review could have caught
  (a bug spanning THIS task's own call ordering against an earlier task's
  store semantics). The final whole-branch review at the end of this plan
  exists for exactly the same reason it did on Tier 3 — do not skip it or
  shorten it because Tier 3's version already "proved the pattern works";
  it proved the pattern CATCHES real bugs, which is an argument for
  running it just as thoroughly again, not for assuming this branch will
  need it less.
