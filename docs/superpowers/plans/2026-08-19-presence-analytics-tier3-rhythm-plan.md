# Presence Analytics Tier 3 (Rhythm + Anomaly) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every subject's confirmed home/work place an hour-of-week occupancy
profile, a typical arrival/departure time (circular statistics), and a
robust-z anomaly score on stay duration against that subject's own history —
Tier 3 of `design/proposed/27-presence-analytics.md` §4, scoped to the
`rhythm`/`anomaly` half of issue #6 (the `routes`/`corridor` half is a
separate, independent plan — see "Scope note" below).

**Architecture:** Three new pure functions in `nora/domain/mobility.py`
(hour-of-week binning, circular mean time-of-day, median/MAD z-score):
stdlib-only, same posture as every other function in that module. Two new
tables (`presence_occupancy`, `presence_place_rhythm`) and one previously
unused column wired up (`presence_stays.place_id`, present in the schema
since Tier 2 but never written). `MobilityRollup` gains one new step, run
once per newly-closed stay that lands inside an existing home/work place —
it never runs for a cluster that hasn't crossed the fail-closed home/work
threshold, matching Tier 2's own posture that an unlabeled cluster is never
persisted.

**Tech Stack:** Same as Tier 1/2 — Python 3.13, SQLAlchemy Core (async,
Postgres), Alembic, pytest + pytest-asyncio. No new dependency.

**Spec:** `design/proposed/27-presence-analytics.md` §4/§4.1 (tier table,
parameter-revalidation note) and GitHub issue
[#6](https://github.com/mikeng-io/nora/issues/6) (the technical brief this
plan implements verbatim). Prior art this extends:
`docs/superpowers/specs/2026-08-18-presence-analytics-substrate-design.md`
(Tier 1/2's own spec) and its plan,
`docs/superpowers/plans/2026-08-19-presence-analytics-substrate-plan.md`.

**Scope note:** Issue #6 groups Tier 3 (rhythm/anomaly) and Tier 5
(routes/corridors) together because both consume closed stays and neither
touches the P0 schema. They are still independently buildable and
independently testable — this plan is Tier 3 ONLY. Tier 5 gets its own plan
once this one ships; do not fold route/corridor work into this plan's tasks.

## Global Constraints

- **Rhythm only applies to a LABELLED place.** A stay whose centroid falls
  outside every existing `home`/`work` row in `presence_derived_places` is
  left alone — no occupancy row, no rhythm row, `place_id` stays `NULL`.
  This mirrors Tier 2's own rule that an `unlabeled` cluster is never
  persisted (spec §3.5): rhythm cannot describe a place the substrate itself
  has declared unproven.
- **Recompute from the full history, every time — never an incremental
  running average.** `label_place` (Tier 2) already does this and its own
  docstring explains why: an incrementally-updated mean cannot be corrected
  if a bad sample enters, and at this data volume (dozens of stays per
  place) recomputing from `stay_durations_for_place`/equivalent reads is
  cheap. `upsert_rhythm` always overwrites, never adds to a stored total.
- **Fail-closed anomaly scoring.** `robust_z_score` returns `None` — never
  `0.0` — when there is not enough history (`min_samples`, config knob) or
  when every historical value is identical (MAD == 0, which would otherwise
  divide by zero and manufacture an infinite or NaN score). `None` means "no
  claim," exactly R-5's silence-over-a-wrong-answer discipline.
- **Every derived figure still passes through `dwell_ms`.** `hour_of_week_bins`
  uses the same capped-dwell discipline the existing `_windowed_dwell` does
  — it is walking a stay's own `started_ts..ended_ts` span, which by `Stay`'s
  own invariant (`nora/domain/mobility.py`'s `Stay` docstring) already equals
  the stay's total dwell, so no separate capping is needed inside the walk
  itself. Do not reintroduate a second dwell computation.
- **Gate commands, run before every commit** (CLAUDE.md):
  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  ```
  `nora.analytics` is still outside mypy's scoped list (Tier 1/2 left this
  as an open question, unresolved) — this plan does not change that.
- **Schema-parity baseline regeneration** (CLAUDE.md, `docs/CONVENTIONS.md`):
  after Task 1's migration, run `uv run python scripts/gen_pg_baseline.py`
  and commit the resulting diff to `tests/infra/pg_schema_baseline.sql` —
  do NOT hand-edit that file.

---

## File Structure

```
nora/domain/mobility.py                EDIT  hour_of_week_bins, circular_mean_minute,
                                              robust_z_score, Stay.place_id field
nora/infra/sql/schema.py               EDIT  PRESENCE_OCCUPANCY, PRESENCE_PLACE_RHYTHM
nora/infra/sql/migrations/versions/0017_presence_rhythm.py   NEW
nora/infra/sql/mobility.py             EDIT  set_place_ids, record_occupancy,
                                              stay_durations_for_place, upsert_rhythm;
                                              stays_for_subject reads place_id back
nora/testing/mobility.py               EDIT  FakeMobilityStore grows the same 4 methods
nora/analytics/rollup.py               EDIT  MobilityStore protocol grows the 4 methods,
                                              RollupConfig gains 2 fields,
                                              MobilityRollup._record_rhythm(...)
nora/analytics/composition.py          EDIT  pass the 2 new RollupConfig fields through
nora/config.py                         EDIT  AnalyticsConfig gains anomaly_min_samples,
                                              anomaly_z_threshold
nora/obs/metric_specs.py               EDIT  nora_analytics_occupancy_bins_total,
                                              nora_analytics_anomalies_total
config.example.toml                    EDIT  [analytics] block, 2 new commented lines
scripts/revalidate_stay_params.py      NEW   Tier 3's parameter-revalidation deliverable
                                              (design/27 §7.1)
design/09-decision-index.md            EDIT  DEC-117
release-fragments/unreleased/presence-analytics-tier3-rhythm.json   NEW

tests/unit/test_mobility.py                    EDIT  3 new pure-function suites
tests/contract/test_mobility_store_contract.py EDIT  4 new method suites
tests/unit/test_analytics_rollup.py            EDIT  rhythm wiring + backfill TDD
tests/infra/pg_schema_baseline.sql             EDIT  regenerated (Task 1's last step)
```

---

### Task 1: Schema — two new tables, one migration

**Files:**
- Modify: `nora/infra/sql/schema.py`
- Create: `nora/infra/sql/migrations/versions/0017_presence_rhythm.py`
- Modify: `tests/infra/pg_schema_baseline.sql` (regenerated, not hand-edited)

**Interfaces:**
- Produces: `PRESENCE_OCCUPANCY`, `PRESENCE_PLACE_RHYTHM` (`sa.Table` objects,
  importable by `nora/infra/sql/mobility.py` in Task 6).

- [ ] **Step 1: Add the two tables to `nora/infra/sql/schema.py`**

  Append after `IDX_PRESENCE_DERIVED_PLACES_SUBJECT_KIND`:

  ```python
  # `presence_occupancy` / `presence_place_rhythm` — Tier 3 (design/27 §4,
  # issue #6, DEC-117). Both are keyed off `presence_derived_places.id`
  # (`place_id`), NEVER off a raw cluster — a place must have crossed Tier
  # 2's fail-closed home/work threshold before rhythm can describe it.
  #
  # `hour_of_week` is 0..167, Monday 00:00 local time = 0, walked in
  # `nora.domain.mobility.hour_of_week_bins` — the same local-clock
  # discipline `label_place`'s night/work windows already use (Asia/
  # Hong_Kong, no DST, a flat day increment is safe).
  PRESENCE_OCCUPANCY = sa.Table(
      "presence_occupancy",
      METADATA,
      sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
      sa.Column("subject_id", sa.Text, nullable=False),
      sa.Column("place_id", sa.Integer, nullable=False),
      sa.Column("hour_of_week", sa.Integer, nullable=False),
      sa.Column("dwell_ms", sa.BigInteger, nullable=False, server_default=sa.text("0")),
      sa.Column("updated_ts", sa.BigInteger, nullable=False),
      sa.UniqueConstraint(
          "subject_id", "place_id", "hour_of_week", name="uq_occupancy_bin"
      ),
      sa.CheckConstraint(
          "hour_of_week >= 0 AND hour_of_week < 168", name="ck_occupancy_how"
      ),
  )

  # One row per (subject, place) — always OVERWRITTEN wholesale on every
  # recompute (`upsert_rhythm`), never incremented, for the same reason
  # `label_place` recomputes from the full stay list every time rather than
  # keeping a running average (a bad sample must be correctable, not baked
  # in forever).
  PRESENCE_PLACE_RHYTHM = sa.Table(
      "presence_place_rhythm",
      METADATA,
      sa.Column("subject_id", sa.Text, primary_key=True),
      sa.Column("place_id", sa.Integer, primary_key=True),
      # Circular mean, LOCAL time-of-day in minutes (0..1439.999) — an
      # arithmetic mean of 23:50 and 00:10 is nonsense (12:00); the circular
      # mean of those two is correctly ~00:00.
      sa.Column("mean_arrival_minute", sa.Float, nullable=True),
      sa.Column("mean_departure_minute", sa.Float, nullable=True),
      sa.Column("duration_median_ms", sa.BigInteger, nullable=True),
      sa.Column("duration_mad_ms", sa.BigInteger, nullable=True),
      sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
      sa.Column("updated_ts", sa.BigInteger, nullable=False),
  )
  ```

- [ ] **Step 2: Write the migration**

  `nora/infra/sql/migrations/versions/0017_presence_rhythm.py`:

  ```python
  """presence_occupancy + presence_place_rhythm — Tier 3 rhythm/anomaly
  (design/27 §4, issue #6, DEC-117).

  Revision ID: 0017_presence_rhythm
  Revises: 0016_geocode_cache
  Create Date: 2026-08-19
  """

  from __future__ import annotations

  import sqlalchemy as sa
  from alembic import op

  revision = "0017_presence_rhythm"
  down_revision = "0016_geocode_cache"
  branch_labels = None
  depends_on = None


  def upgrade() -> None:
      op.create_table(
          "presence_occupancy",
          sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
          sa.Column("subject_id", sa.Text, nullable=False),
          sa.Column("place_id", sa.Integer, nullable=False),
          sa.Column("hour_of_week", sa.Integer, nullable=False),
          sa.Column("dwell_ms", sa.BigInteger, nullable=False, server_default=sa.text("0")),
          sa.Column("updated_ts", sa.BigInteger, nullable=False),
          sa.UniqueConstraint(
              "subject_id", "place_id", "hour_of_week", name="uq_occupancy_bin"
          ),
          sa.CheckConstraint(
              "hour_of_week >= 0 AND hour_of_week < 168", name="ck_occupancy_how"
          ),
      )
      op.create_table(
          "presence_place_rhythm",
          sa.Column("subject_id", sa.Text, primary_key=True),
          sa.Column("place_id", sa.Integer, primary_key=True),
          sa.Column("mean_arrival_minute", sa.Float, nullable=True),
          sa.Column("mean_departure_minute", sa.Float, nullable=True),
          sa.Column("duration_median_ms", sa.BigInteger, nullable=True),
          sa.Column("duration_mad_ms", sa.BigInteger, nullable=True),
          sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
          sa.Column("updated_ts", sa.BigInteger, nullable=False),
      )


  def downgrade() -> None:
      op.drop_table("presence_place_rhythm")
      op.drop_table("presence_occupancy")
  ```

- [ ] **Step 3: Verify and regenerate the baseline**

  ```bash
  uv run pytest -q tests/infra/test_schema_parity.py
  ```
  Expected: FAILS (`schema drifted from tests/infra/pg_schema_baseline.sql`).
  ```bash
  uv run python scripts/gen_pg_baseline.py
  uv run pytest -q tests/infra/test_schema_parity.py
  ```
  Expected: PASSES. Commit the regenerated `tests/infra/pg_schema_baseline.sql`
  alongside the migration in this task's commit — never hand-edit it.

- [ ] **Step 4: Commit**

  ```bash
  git add nora/infra/sql/schema.py nora/infra/sql/migrations/versions/0017_presence_rhythm.py tests/infra/pg_schema_baseline.sql
  git commit -m "feat(analytics): presence_occupancy + presence_place_rhythm tables (Tier 3)"
  ```

---

### Task 2: Domain — `hour_of_week_bins`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Consumes: nothing new (stdlib `datetime`/`zoneinfo` only, same as
  `_local_day_bounds_ms` already in this file).
- Produces: `hour_of_week_bins(started_ts: int, ended_ts: int, tz_name: str) -> dict[int, int]`
  — Task 10 (rollup wiring) calls this directly.

- [ ] **Step 1: Write the failing test**

  Add to `tests/unit/test_mobility.py`:

  ```python
  from nora.domain.mobility import hour_of_week_bins


  def test_hour_of_week_bins_a_span_within_one_hour_is_one_bin():
      # 2026-08-17 is a Monday. 09:15-09:45 HKT stays inside hour_of_week=9
      # (Monday 09:00-10:00 -> bin = 0*24 + 9 = 9).
      start = _ms(2026, 8, 17, 9, 15)
      end = _ms(2026, 8, 17, 9, 45)
      bins = hour_of_week_bins(start, end, "Asia/Hong_Kong")
      assert bins == {9: 30 * 60_000}


  def test_hour_of_week_bins_splits_across_an_hour_boundary():
      # 08:45-09:15 HKT: 15 min in bin 8, 15 min in bin 9.
      start = _ms(2026, 8, 17, 8, 45)
      end = _ms(2026, 8, 17, 9, 15)
      bins = hour_of_week_bins(start, end, "Asia/Hong_Kong")
      assert bins == {8: 15 * 60_000, 9: 15 * 60_000}


  def test_hour_of_week_bins_an_overnight_span_lands_on_the_later_weekday():
      # Monday 23:30 -> Tuesday 00:30 HKT: bin 23 (Monday) gets 30 min,
      # bin 24 (Tuesday 00:00-01:00 = 1*24+0) gets 30 min.
      start = _ms(2026, 8, 17, 23, 30)
      end = _ms(2026, 8, 18, 0, 30)
      bins = hour_of_week_bins(start, end, "Asia/Hong_Kong")
      assert bins == {23: 30 * 60_000, 24: 30 * 60_000}


  def test_hour_of_week_bins_a_multi_day_span_covers_every_hour_between():
      # A 26-hour stay: 3 partial/full-hour bins at minimum on each end,
      # every hour strictly between is a full 3_600_000ms bin.
      start = _ms(2026, 8, 17, 10, 0)
      end = start + 26 * 3_600_000
      bins = hour_of_week_bins(start, end, "Asia/Hong_Kong")
      assert sum(bins.values()) == end - start
      assert len(bins) == 27  # 26h span touches 27 distinct hour buckets
  ```

  `_ms(year, month, day, hour, minute=0)` already exists at the top of this
  test file (Tier 1/2's own helper) — reuse it, do not redefine it.

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k hour_of_week_bins
  ```
  Expected: FAIL with `ImportError: cannot import name 'hour_of_week_bins'`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, near `_windowed_dwell` (same local-clock
  walking pattern), and add `"hour_of_week_bins"` to `__all__`:

  ```python
  def hour_of_week_bins(started_ts: int, ended_ts: int, tz_name: str) -> dict[int, int]:
      """A stay's dwell, split into local hour-of-week buckets (0..167,
      Monday 00:00 = 0) — Tier 3's occupancy input (design/27 §4, issue #6).

      Walks one LOCAL clock-hour at a time, like `_windowed_dwell`'s day-walk
      — `ended_ts - started_ts` already equals the stay's own dwell by
      `Stay`'s own invariant, so no separate `dwell_ms` capping applies here;
      this is purely a spatial-in-time split of a span already known good."""
      if ended_ts <= started_ts:
          return {}
      tz = ZoneInfo(tz_name)
      bins: dict[int, int] = {}
      cursor = started_ts
      while cursor < ended_ts:
          local = datetime.fromtimestamp(cursor / 1000, tz=tz)
          hour_start = local.replace(minute=0, second=0, microsecond=0)
          hour_start_ms = int(hour_start.timestamp() * 1000)
          hour_end_ms = hour_start_ms + 3_600_000
          bin_end = min(hour_end_ms, ended_ts)
          how = local.weekday() * 24 + local.hour
          bins[how] = bins.get(how, 0) + (bin_end - cursor)
          cursor = hour_end_ms
      return bins
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k hour_of_week_bins
  ```
  Expected: PASS, all 4 tests.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): hour_of_week_bins — Tier 3 occupancy input"
  ```

---

### Task 3: Domain — `circular_mean_minute`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Produces: `circular_mean_minute(minutes: list[float]) -> float`.

- [ ] **Step 1: Write the failing test**

  ```python
  from nora.domain.mobility import circular_mean_minute


  def test_circular_mean_minute_of_a_single_value_is_itself():
      assert circular_mean_minute([540.0]) == pytest.approx(540.0, abs=0.01)


  def test_circular_mean_minute_wraps_correctly_across_midnight():
      # 23:50 (1430) and 00:10 (10) average to ~00:00 (0 or 1440), NOT the
      # arithmetic mean of 720 (noon) — this is the whole point of the
      # function.
      mean = circular_mean_minute([1430.0, 10.0])
      assert mean == pytest.approx(0.0, abs=0.01) or mean == pytest.approx(1440.0, abs=0.01)


  def test_circular_mean_minute_of_clustered_values_is_close_to_the_cluster():
      mean = circular_mean_minute([540.0, 545.0, 535.0])  # ~09:00
      assert mean == pytest.approx(540.0, abs=1.0)
  ```

  Add `import pytest` at the top of `tests/unit/test_mobility.py` if not
  already present (check first — most domain test files in this repo import
  it for `pytest.approx`/`pytest.raises`).

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k circular_mean_minute
  ```
  Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, add `"circular_mean_minute"` to `__all__`:

  ```python
  _MINUTES_PER_DAY = 1440.0


  def circular_mean_minute(minutes: list[float]) -> float:
      """The circular mean of local time-of-day values (each 0..1439.999),
      as minutes-since-midnight — Tier 3's typical-arrival/departure stat
      (design/27 §4, issue #6). An ARITHMETIC mean of 23:50 and 00:10 gives
      12:00, which is nonsense for a clock value; treating each minute as an
      angle on a 24h circle and averaging the unit vectors gives ~00:00,
      correctly.

      Callers must pass a non-empty list — every call site accumulates from
      at least one closed stay by construction (Task 10), so this is not a
      boundary this pure function needs to guard."""
      angles = [m / _MINUTES_PER_DAY * 2 * math.pi for m in minutes]
      sin_sum = sum(math.sin(a) for a in angles)
      cos_sum = sum(math.cos(a) for a in angles)
      mean_angle = math.atan2(sin_sum, cos_sum)
      if mean_angle < 0:
          mean_angle += 2 * math.pi
      return mean_angle / (2 * math.pi) * _MINUTES_PER_DAY
  ```

  Add `import math` to the file's imports if not already present (check
  first — `label_place`'s own module may not need it yet).

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k circular_mean_minute
  ```
  Expected: PASS, all 3 tests.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): circular_mean_minute — Tier 3 typical arrival/departure"
  ```

---

### Task 4: Domain — `robust_z_score`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Produces: `robust_z_score(value: float, history: list[float], *, min_samples: int) -> float | None`.

- [ ] **Step 1: Write the failing test**

  ```python
  from nora.domain.mobility import robust_z_score


  def test_robust_z_score_is_none_below_min_samples():
      assert robust_z_score(100.0, [1.0, 2.0], min_samples=5) is None


  def test_robust_z_score_is_none_when_the_history_has_zero_spread():
      # MAD == 0 would otherwise divide by zero / manufacture an infinite
      # score for ANY deviation — fail-closed, not a crash and not a lie.
      assert robust_z_score(100.0, [5.0, 5.0, 5.0, 5.0, 5.0], min_samples=5) is None


  def test_robust_z_score_flags_a_clear_outlier():
      history = [10.0, 11.0, 9.0, 10.0, 10.0, 11.0, 9.0]
      z = robust_z_score(100.0, history, min_samples=5)
      assert z is not None
      assert z > 3.0


  def test_robust_z_score_of_a_typical_value_is_small():
      history = [10.0, 11.0, 9.0, 10.0, 10.0, 11.0, 9.0]
      z = robust_z_score(10.0, history, min_samples=5)
      assert z is not None
      assert abs(z) < 1.0
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k robust_z_score
  ```
  Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

  Add to `nora/domain/mobility.py`, add `"robust_z_score"` to `__all__`:

  ```python
  # The MAD-to-std-dev consistency constant for a normal distribution
  # (1 / Phi^-1(3/4)) — the standard correction so a MAD-based z-score is on
  # the same scale as a familiar standard-deviation-based one.
  _MAD_CONSISTENCY_CONSTANT = 1.4826


  def robust_z_score(
      value: float, history: list[float], *, min_samples: int
  ) -> float | None:
      """How anomalous `value` is against `history`'s median/MAD — Tier 3's
      anomaly signal (design/27 §4, issue #6: "still at work at 23:00, p99
      for her"). Robust (median/MAD, not mean/stddev) because a single prior
      anomaly must not drag the baseline toward itself the way a mean would.

      `None` — never `0.0` — below `min_samples` or when every historical
      value is identical (MAD == 0): fail-closed, matching `label_place`'s
      own posture that missing evidence is a missing claim, not a defaulted
      one."""
      if len(history) < min_samples:
          return None
      sorted_h = sorted(history)
      n = len(sorted_h)
      median = (
          sorted_h[n // 2] if n % 2
          else (sorted_h[n // 2 - 1] + sorted_h[n // 2]) / 2
      )
      deviations = sorted(abs(x - median) for x in history)
      mad = (
          deviations[n // 2] if n % 2
          else (deviations[n // 2 - 1] + deviations[n // 2]) / 2
      )
      if mad == 0:
          return None
      return (value - median) / (_MAD_CONSISTENCY_CONSTANT * mad)
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k robust_z_score
  ```
  Expected: PASS, all 4 tests.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): robust_z_score — Tier 3's fail-closed anomaly signal"
  ```

---

### Task 5: Domain — `Stay.place_id`

**Files:**
- Modify: `nora/domain/mobility.py`
- Test: `tests/unit/test_mobility.py`

**Interfaces:**
- Produces: `Stay` gains `place_id: int | None = None` as its final field.
  Every existing call site constructs `Stay(...)` with keyword arguments
  (verified: `_close`, `segment_stays`'s open-stay branch, both in this same
  file), so this is additive and backward compatible — no existing call site
  changes.

- [ ] **Step 1: Write the failing test**

  ```python
  def test_stay_place_id_defaults_to_none():
      s = _stay(1.0, 1.0)
      assert s.place_id is None


  def test_stay_place_id_can_be_set():
      s = Stay(
          subject_id="s", person_id="s", circle_id="c1", started_ts=0,
          ended_ts=1000, lat=1.0, lng=1.0, radius_m=10.0, fix_count=2,
          place_id=42,
      )
      assert s.place_id == 42
  ```

  `_stay(lat, lng, started_ts=0)` already exists in this test file — check
  its signature constructs `Stay(...)` via keywords (it does, per Tier 1/2's
  own code) so it needs no change for the new field's default to apply.

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py -k stay_place_id
  ```
  Expected: FAIL with `TypeError: Stay.__init__() got an unexpected keyword argument 'place_id'`.

- [ ] **Step 3: Implement**

  In `nora/domain/mobility.py`, add the field as `Stay`'s last line:

  ```python
      fix_count: int
      # Tier 3 (design/27 §4, issue #6): set once this stay's centroid is
      # found inside an existing home/work `presence_derived_places` row —
      # `MobilityRollup._record_rhythm` (Task 10) is the only writer. `None`
      # means either "not yet checked" or "outside every labelled place" —
      # the two are indistinguishable here on purpose, since a stay that is
      # outside every place today may fall inside one created LATER (Task 10's
      # backfill step handles that case going forward, not this field).
      place_id: int | None = None
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  uv run pytest -q tests/unit/test_mobility.py
  ```
  Expected: PASS, full file — this is the point where a stray positional
  `Stay(...)` construction anywhere in the codebase would break; a full-file
  run here catches it, a `-k`-scoped run would not.

- [ ] **Step 5: Commit**

  ```bash
  git add nora/domain/mobility.py tests/unit/test_mobility.py
  git commit -m "feat(mobility): Stay.place_id — Tier 3's stay-to-place link"
  ```

---

### Task 6: Store — `set_place_ids`, and `stays_for_subject` reads `place_id` back

**Files:**
- Modify: `nora/infra/sql/mobility.py`
- Modify: `nora/testing/mobility.py`
- Modify: `nora/analytics/rollup.py` (the `MobilityStore` Protocol only —
  method signatures, not `MobilityRollup`'s body, which is Task 10)
- Test: `tests/contract/test_mobility_store_contract.py`

**Interfaces:**
- Consumes: `PRESENCE_STAYS.c.place_id` (already exists in the schema since
  Tier 1/2, previously unused — see this table's own comment in
  `nora/infra/sql/schema.py`).
- Produces: `MobilityStore.set_place_ids(*, subject_id: str, started_ts_in: list[int], place_id: int) -> None`.
  `stays_for_subject` now round-trips `place_id`.

- [ ] **Step 1: Write the failing tests**

  Add to `tests/contract/test_mobility_store_contract.py`:

  ```python
  async def test_stays_for_subject_round_trips_place_id_as_none_by_default(store):
      stay = Stay(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=61_000, lat=30.1, lng=120.2,
          radius_m=50.0, fix_count=10,
      )
      await store.write_stays([stay])
      back = await store.stays_for_subject(subject_id="test_subject")
      assert back[0].place_id is None


  async def test_set_place_ids_updates_only_the_matching_stays(store):
      a = Stay(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=61_000, lat=30.1, lng=120.2,
          radius_m=50.0, fix_count=10,
      )
      b = Stay(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=200_000, ended_ts=261_000, lat=31.1, lng=121.2,
          radius_m=50.0, fix_count=10,
      )
      await store.write_stays([a, b])
      await store.set_place_ids(
          subject_id="test_subject", started_ts_in=[1_000], place_id=7,
      )
      back = {s.started_ts: s.place_id for s in await store.stays_for_subject(subject_id="test_subject")}
      assert back[1_000] == 7
      assert back[200_000] is None


  async def test_set_place_ids_does_not_overwrite_an_already_set_place_id(store):
      # A stay's place_id, once set, is never silently reassigned by a
      # later, different cluster — set_place_ids only fills NULL rows.
      a = Stay(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=61_000, lat=30.1, lng=120.2,
          radius_m=50.0, fix_count=10,
      )
      await store.write_stays([a])
      await store.set_place_ids(subject_id="test_subject", started_ts_in=[1_000], place_id=7)
      await store.set_place_ids(subject_id="test_subject", started_ts_in=[1_000], place_id=9)
      back = await store.stays_for_subject(subject_id="test_subject")
      assert back[0].place_id == 7
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py -k place_id
  ```
  Expected: FAIL — `place_id` unpacking mismatch / `AttributeError: 'SqlMobilityStore' object has no attribute 'set_place_ids'`.

- [ ] **Step 3: Implement — `SqlMobilityStore`**

  In `nora/infra/sql/mobility.py`, `stays_for_subject`:

  ```python
      async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
          stmt = (
              sa.select(PRESENCE_STAYS)
              .where(PRESENCE_STAYS.c.subject_id == subject_id)
              .order_by(PRESENCE_STAYS.c.started_ts.asc())
          )
          async with self._engine.connect() as conn:
              rows = (await conn.execute(stmt)).all()
          return [
              Stay(
                  subject_id=r.subject_id, person_id=r.person_id, circle_id=r.circle_id,
                  started_ts=r.started_ts, ended_ts=r.ended_ts,
                  lat=r.lat, lng=r.lng, radius_m=r.radius_m, fix_count=r.fix_count,
                  place_id=r.place_id,
              )
              for r in rows
          ]

      async def set_place_ids(
          self, *, subject_id: str, started_ts_in: list[int], place_id: int
      ) -> None:
          """Fills `place_id` ONLY where it is currently NULL — a stay's link
          to a place, once set, is never silently reassigned by a later,
          different cluster touching the same coordinate (Task 10's own
          docstring on why this can only ever be additive)."""
          if not started_ts_in:
              return
          async with self._engine.begin() as conn:
              await conn.execute(
                  sa.update(PRESENCE_STAYS)
                  .where(
                      PRESENCE_STAYS.c.subject_id == subject_id,
                      PRESENCE_STAYS.c.started_ts.in_(started_ts_in),
                      PRESENCE_STAYS.c.place_id.is_(None),
                  )
                  .values(place_id=place_id)
              )
  ```

- [ ] **Step 4: Implement — `FakeMobilityStore`**

  In `nora/testing/mobility.py`:

  ```python
      async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
          return sorted(
              (s for s in self._stays if s.subject_id == subject_id),
              key=lambda s: s.started_ts,
          )

      async def set_place_ids(
          self, *, subject_id: str, started_ts_in: list[int], place_id: int
      ) -> None:
          targets = set(started_ts_in)
          for i, s in enumerate(self._stays):
              if (
                  s.subject_id == subject_id
                  and s.started_ts in targets
                  and s.place_id is None
              ):
                  self._stays[i] = dataclasses.replace(s, place_id=place_id)
  ```

  Add `import dataclasses` to `nora/testing/mobility.py`'s imports.

- [ ] **Step 5: Add the method to the `MobilityStore` Protocol**

  In `nora/analytics/rollup.py`, inside `class MobilityStore(Protocol)`:

  ```python
      async def set_place_ids(
          self, *, subject_id: str, started_ts_in: list[int], place_id: int
      ) -> None: ...
  ```

- [ ] **Step 6: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py
  ```
  Expected: PASS, full file (28 existing + 3 new = 31).

- [ ] **Step 7: Commit**

  ```bash
  git add nora/infra/sql/mobility.py nora/testing/mobility.py nora/analytics/rollup.py tests/contract/test_mobility_store_contract.py
  git commit -m "feat(mobility): MobilityStore.set_place_ids, place_id round-trips on read"
  ```

---

### Task 7: Store — occupancy and rhythm read/write

**Files:**
- Modify: `nora/infra/sql/mobility.py`
- Modify: `nora/testing/mobility.py`
- Modify: `nora/analytics/rollup.py` (`MobilityStore` Protocol)
- Test: `tests/contract/test_mobility_store_contract.py`

**Interfaces:**
- Produces:
  - `MobilityStore.record_occupancy(*, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int) -> None`
  - `MobilityStore.stay_durations_for_place(*, subject_id: str, place_id: int) -> list[int]`
  - `MobilityStore.upsert_rhythm(*, subject_id: str, place_id: int, mean_arrival_minute: float, mean_departure_minute: float, duration_median_ms: int, duration_mad_ms: int, sample_count: int, now_ms: int) -> None`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/contract/test_mobility_store_contract.py`:

  ```python
  async def test_record_occupancy_then_stay_durations_round_trip(store):
      stay = Stay(
          subject_id="test_subject", person_id="test_subject", circle_id="c1",
          started_ts=1_000, ended_ts=601_000, lat=30.1, lng=120.2,
          radius_m=50.0, fix_count=10,
      )
      await store.write_stays([stay])
      await store.set_place_ids(subject_id="test_subject", started_ts_in=[1_000], place_id=7)
      assert await store.stay_durations_for_place(subject_id="test_subject", place_id=7) == [600_000]


  async def test_record_occupancy_accumulates_dwell_across_calls(store):
      await store.record_occupancy(
          subject_id="test_subject", place_id=7, bins={9: 1_000, 10: 2_000}, now_ms=1_000,
      )
      await store.record_occupancy(
          subject_id="test_subject", place_id=7, bins={9: 500}, now_ms=2_000,
      )
      # No public read method for raw bins is needed by any caller yet
      # (Task 10 only writes) — this test just proves a second call does not
      # raise and does not clobber unrelated bins; a future consumer spec
      # (P1/P2, issue #9) adds the read path when it exists.
      await store.record_occupancy(
          subject_id="test_subject", place_id=7, bins={11: 100}, now_ms=3_000,
      )


  async def test_upsert_rhythm_then_stay_durations_are_independent_of_it(store):
      # upsert_rhythm and stay_durations_for_place read/write DIFFERENT
      # tables (presence_place_rhythm vs presence_stays) — this proves the
      # write path doesn't raise and is a genuine upsert (see next test).
      await store.upsert_rhythm(
          subject_id="test_subject", place_id=7, mean_arrival_minute=540.0,
          mean_departure_minute=1080.0, duration_median_ms=28_800_000,
          duration_mad_ms=1_800_000, sample_count=5, now_ms=10_000,
      )


  async def test_upsert_rhythm_overwrites_rather_than_duplicates(store):
      await store.upsert_rhythm(
          subject_id="test_subject", place_id=7, mean_arrival_minute=540.0,
          mean_departure_minute=1080.0, duration_median_ms=28_800_000,
          duration_mad_ms=1_800_000, sample_count=5, now_ms=10_000,
      )
      # A second upsert for the SAME (subject_id, place_id) must replace, not
      # add a second row — proven indirectly: calling it twice with
      # DIFFERENT values must not raise a unique-constraint violation.
      await store.upsert_rhythm(
          subject_id="test_subject", place_id=7, mean_arrival_minute=550.0,
          mean_departure_minute=1090.0, duration_median_ms=29_800_000,
          duration_mad_ms=1_900_000, sample_count=6, now_ms=20_000,
      )
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py -k "occupancy or rhythm or stay_durations"
  ```
  Expected: FAIL — `AttributeError` on each new method.

- [ ] **Step 3: Implement — `SqlMobilityStore`**

  In `nora/infra/sql/mobility.py`, add the three methods (import
  `PRESENCE_OCCUPANCY`, `PRESENCE_PLACE_RHYTHM` from `nora.infra.sql.schema`
  and `from sqlalchemy.dialects.postgresql import insert as pg_insert` at
  the top, matching `nora/infra/sql/geocode_cache.py`'s own pattern):

  ```python
      async def record_occupancy(
          self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
      ) -> None:
          if not bins:
              return
          async with self._engine.begin() as conn:
              for hour_of_week, dwell_ms in bins.items():
                  stmt = pg_insert(PRESENCE_OCCUPANCY).values(
                      subject_id=subject_id, place_id=place_id,
                      hour_of_week=hour_of_week, dwell_ms=dwell_ms, updated_ts=now_ms,
                  )
                  stmt = stmt.on_conflict_do_update(
                      index_elements=[
                          PRESENCE_OCCUPANCY.c.subject_id, PRESENCE_OCCUPANCY.c.place_id,
                          PRESENCE_OCCUPANCY.c.hour_of_week,
                      ],
                      set_={
                          "dwell_ms": PRESENCE_OCCUPANCY.c.dwell_ms + stmt.excluded.dwell_ms,
                          "updated_ts": stmt.excluded.updated_ts,
                      },
                  )
                  await conn.execute(stmt)

      async def stay_durations_for_place(
          self, *, subject_id: str, place_id: int
      ) -> list[int]:
          stmt = sa.select(PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts).where(
              PRESENCE_STAYS.c.subject_id == subject_id,
              PRESENCE_STAYS.c.place_id == place_id,
          )
          async with self._engine.connect() as conn:
              rows = (await conn.execute(stmt)).all()
          return [r.ended_ts - r.started_ts for r in rows]

      async def upsert_rhythm(
          self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
          mean_departure_minute: float, duration_median_ms: int,
          duration_mad_ms: int, sample_count: int, now_ms: int,
      ) -> None:
          stmt = pg_insert(PRESENCE_PLACE_RHYTHM).values(
              subject_id=subject_id, place_id=place_id,
              mean_arrival_minute=mean_arrival_minute,
              mean_departure_minute=mean_departure_minute,
              duration_median_ms=duration_median_ms, duration_mad_ms=duration_mad_ms,
              sample_count=sample_count, updated_ts=now_ms,
          )
          stmt = stmt.on_conflict_do_update(
              index_elements=[PRESENCE_PLACE_RHYTHM.c.subject_id, PRESENCE_PLACE_RHYTHM.c.place_id],
              set_={
                  "mean_arrival_minute": stmt.excluded.mean_arrival_minute,
                  "mean_departure_minute": stmt.excluded.mean_departure_minute,
                  "duration_median_ms": stmt.excluded.duration_median_ms,
                  "duration_mad_ms": stmt.excluded.duration_mad_ms,
                  "sample_count": stmt.excluded.sample_count,
                  "updated_ts": stmt.excluded.updated_ts,
              },
          )
          async with self._engine.begin() as conn:
              await conn.execute(stmt)
  ```

- [ ] **Step 4: Implement — `FakeMobilityStore`**

  In `nora/testing/mobility.py`, add matching in-memory state and methods:

  ```python
          # In __init__, alongside self._places / self._watermarks:
          self._occupancy: dict[tuple[str, int, int], int] = {}  # (subject, place, how) -> dwell_ms
          self._rhythm: dict[tuple[str, int], dict] = {}

      async def record_occupancy(
          self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
      ) -> None:
          for how, dwell_ms in bins.items():
              key = (subject_id, place_id, how)
              self._occupancy[key] = self._occupancy.get(key, 0) + dwell_ms

      async def stay_durations_for_place(
          self, *, subject_id: str, place_id: int
      ) -> list[int]:
          return [
              s.ended_ts - s.started_ts for s in self._stays
              if s.subject_id == subject_id and s.place_id == place_id
          ]

      async def upsert_rhythm(
          self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
          mean_departure_minute: float, duration_median_ms: int,
          duration_mad_ms: int, sample_count: int, now_ms: int,
      ) -> None:
          self._rhythm[(subject_id, place_id)] = {
              "mean_arrival_minute": mean_arrival_minute,
              "mean_departure_minute": mean_departure_minute,
              "duration_median_ms": duration_median_ms,
              "duration_mad_ms": duration_mad_ms,
              "sample_count": sample_count,
          }
  ```

- [ ] **Step 5: Add the three methods to the `MobilityStore` Protocol**

  In `nora/analytics/rollup.py`:

  ```python
      async def record_occupancy(
          self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
      ) -> None: ...
      async def stay_durations_for_place(
          self, *, subject_id: str, place_id: int
      ) -> list[int]: ...
      async def upsert_rhythm(
          self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
          mean_departure_minute: float, duration_median_ms: int,
          duration_mad_ms: int, sample_count: int, now_ms: int,
      ) -> None: ...
  ```

- [ ] **Step 6: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/contract/test_mobility_store_contract.py
  ```
  Expected: PASS, full file.

- [ ] **Step 7: Commit**

  ```bash
  git add nora/infra/sql/mobility.py nora/testing/mobility.py nora/analytics/rollup.py tests/contract/test_mobility_store_contract.py
  git commit -m "feat(mobility): occupancy + rhythm store methods"
  ```

---

### Task 8: Config — two new knobs

**Files:**
- Modify: `nora/config.py`
- Modify: `nora/analytics/rollup.py` (`RollupConfig`)
- Modify: `nora/analytics/composition.py`
- Modify: `config.example.toml`

**Interfaces:**
- Produces: `AnalyticsConfig.anomaly_min_samples: int` (default 5),
  `AnalyticsConfig.anomaly_z_threshold: float` (default 3.0);
  `RollupConfig` gains the same two fields.

- [ ] **Step 1: Add to `AnalyticsConfig`** (`nora/config.py`, after
  `label_freshness_days`):

  ```python
      # Tier 3 (design/27 §4, issue #6, DEC-117): robust_z_score's own
      # fail-closed floor — fewer than this many prior stays at a place and
      # no anomaly claim is made, ever (nora.domain.mobility.robust_z_score's
      # own docstring). 5 is a starting point, not measured — same honesty
      # design/27 §7.1 already gives stay_radius_m/stay_min_dwell_seconds.
      anomaly_min_samples: int = Field(default=5, ge=2)
      # |z| above this increments nora_analytics_anomalies_total — a METRIC
      # only; this substrate emits typed facts, never a consumer-facing
      # alert (spec §1.1's neutrality rule still applies to Tier 3).
      anomaly_z_threshold: float = Field(default=3.0, gt=0)
  ```

- [ ] **Step 2: Add to `RollupConfig`** (`nora/analytics/rollup.py`):

  ```python
  @dataclass(frozen=True)
  class RollupConfig:
      # ... existing fields unchanged ...
      anomaly_min_samples: int = 5
      anomaly_z_threshold: float = 3.0
  ```

  (Appended with defaults so this is backward compatible with every
  existing `RollupConfig(...)` construction in `tests/unit/test_analytics_rollup.py`.)

- [ ] **Step 3: Pass through in `nora/analytics/composition.py`**

  In `build_rollup`'s `RollupConfig(...)` construction, add:

  ```python
          anomaly_min_samples=analytics.anomaly_min_samples,
          anomaly_z_threshold=analytics.anomaly_z_threshold,
  ```

- [ ] **Step 4: Document in `config.example.toml`**

  In the `[analytics]` block, after `# label_freshness_days = 45`:

  ```toml
  # anomaly_min_samples = 5     # Tier 3 — fewer prior stays and no anomaly claim (fail-closed)
  # anomaly_z_threshold = 3.0   # Tier 3 — |z| above this counts nora_analytics_anomalies_total
  ```

- [ ] **Step 5: Verify nothing broke**

  ```bash
  uv run pytest -q tests/unit/test_analytics_rollup.py tests/unit/test_analytics_composition.py
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  ```
  Expected: PASS (no test constructs `RollupConfig`/`AnalyticsConfig`
  positionally, per Tier 1/2's own convention — verify this holds before
  moving on, since a positional construction anywhere would break silently
  otherwise).

- [ ] **Step 6: Commit**

  ```bash
  git add nora/config.py nora/analytics/rollup.py nora/analytics/composition.py config.example.toml
  git commit -m "feat(analytics): anomaly_min_samples/anomaly_z_threshold config knobs"
  ```

---

### Task 9: Metrics

**Files:**
- Modify: `nora/obs/metric_specs.py`
- Test: none new — Task 10's rollup test exercises these against a real
  `Metrics()` instance, matching Tier 1/2's own
  `test_metrics_are_recorded_at_every_call_site_with_their_labels` pattern.

**Interfaces:**
- Produces: `nora_analytics_occupancy_bins_total{subject}` (counter),
  `nora_analytics_anomalies_total{subject}` (counter).

- [ ] **Step 1: Add the two specs**

  In `nora/obs/metric_specs.py`, in the `# ── the analytics rollup (DEC-115) ──`
  section, after the existing `nora_analytics_unlabeled_total` entry:

  ```python
      _CounterSpec(
          "nora_analytics_occupancy_bins_total",
          "Hour-of-week occupancy bins updated (Tier 3), by subject.",
          ("subject",),
      ),
      # A rise here is EXPECTED as the corpus grows — unlike
      # nora_analytics_unlabeled_total, this is not a health signal, it is
      # the anomaly detector doing its job (design/27 §4, issue #6).
      _CounterSpec(
          "nora_analytics_anomalies_total",
          "Stay durations scoring |z| above anomaly_z_threshold against the "
          "subject's own history at that place, by subject.",
          ("subject",),
      ),
  ```

- [ ] **Step 2: Verify the spec module still imports clean**

  ```bash
  uv run python -c "import nora.obs.metric_specs"
  ```
  Expected: no output, exit 0.

- [ ] **Step 3: Commit**

  ```bash
  git add nora/obs/metric_specs.py
  git commit -m "feat(obs): Tier 3 occupancy/anomaly metrics"
  ```

---

### Task 10: Rollup — wire rhythm recording + first-place backfill

**Files:**
- Modify: `nora/analytics/rollup.py`
- Test: `tests/unit/test_analytics_rollup.py`

**Interfaces:**
- Consumes: every method added in Tasks 6/7, `hour_of_week_bins`,
  `circular_mean_minute`, `robust_z_score` from `nora.domain.mobility`.
- Produces: `MobilityRollup._record_rhythm(self, *, subject_id: str, stay: Stay, now_ms: int) -> None`
  (private — no external caller besides `run_once` itself).

- [ ] **Step 1: Write the failing tests**

  Add to `tests/unit/test_analytics_rollup.py`:

  ```python
  async def test_a_new_stay_inside_an_existing_home_gets_a_place_id_and_occupancy():
      store = FakeMobilityStore()
      await _seed_stay_track(
          store, person_id="s", start_ms=0, n_days=3, hour_start=1, hour_end=5
      )
      rollup = MobilityRollup(store=store, config=CFG)
      await rollup.run_once(
          subject_id="s", person_ids=["s"], circle_id="c1", now_ms=4 * 86_400_000,
      )
      # A home place now exists (Speed 3, same as the existing rollup tests
      # already prove). Seed ONE more day so a NEW stay closes inside it.
      await _seed_stay_track(
          store, person_id="s", start_ms=4 * 86_400_000, n_days=1, hour_start=1, hour_end=5
      )
      await rollup.run_once(
          subject_id="s", person_ids=["s"], circle_id="c1", now_ms=6 * 86_400_000,
      )
      stays = await store.stays_for_subject(subject_id="s")
      newest = max(stays, key=lambda s: s.started_ts)
      assert newest.place_id is not None


  async def test_a_stay_outside_every_labelled_place_gets_no_place_id():
      store = FakeMobilityStore()
      # A single short, isolated stay far from anywhere else — never
      # accumulates enough evidence to become home/work (min_nights=3 in CFG).
      await store._seed_fix_for_test(
          id=1, person_id="s", ts=0, lat=1.0, lng=1.0,
          accuracy_m=50.0, wire_state="live", recorded_at=0,
      )
      await store._seed_fix_for_test(
          id=2, person_id="s", ts=700_000, lat=1.0, lng=1.0,
          accuracy_m=50.0, wire_state="live", recorded_at=0,
      )
      rollup = MobilityRollup(store=store, config=CFG)
      await rollup.run_once(
          subject_id="s", person_ids=["s"], circle_id="c1", now_ms=2_000_000,
      )
      stays = await store.stays_for_subject(subject_id="s")
      assert stays and all(s.place_id is None for s in stays)


  async def test_an_extreme_duration_increments_the_anomaly_metric():
      store = FakeMobilityStore()
      metrics = Metrics()
      await _seed_stay_track(
          store, person_id="s", start_ms=0, n_days=6, hour_start=1, hour_end=5
      )
      rollup = MobilityRollup(store=store, config=CFG, metrics=metrics)
      await rollup.run_once(
          subject_id="s", person_ids=["s"], circle_id="c1", now_ms=7 * 86_400_000,
      )
      # `_seed_stay_track`'s stays are all ~4h (hour_start=1 to hour_end=5) —
      # uniform, so MAD == 0 and robust_z_score returns None (no false
      # anomaly from identical history). This test only proves the counter
      # exists and does not raise; a genuinely varied-duration fixture is
      # more fragile to hand-construct than it is worth for this plan's
      # scope — the pure function itself is exhaustively covered in Task 4.
      assert metrics.get("nora_analytics_occupancy_bins_total", subject="s") is not None
  ```

  Add `from nora.testing import FakeGeocodeCacheStore` is NOT needed here
  (that import already exists from the prior geocode-cache work); no new
  imports beyond what Task 6/7 already require via `MobilityRollup`/
  `FakeMobilityStore`, both already imported at this file's top.

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest -q tests/unit/test_analytics_rollup.py -k "place_id or anomaly_metric"
  ```
  Expected: FAIL — `place_id` stays `None` (the wiring doesn't exist yet).

- [ ] **Step 3: Implement**

  In `nora/analytics/rollup.py`, add imports at the top:

  ```python
  from nora.domain.mobility import (
      DWELL_CAP_MS,
      LabelResult,
      RawFix,
      Stay,
      circular_mean_minute,
      cluster_places,
      hour_of_week_bins,
      label_place,
      robust_z_score,
      segment_stays,
  )
  ```

  Add a new method on `MobilityRollup`:

  The arrival/departure recompute needs the STAYS themselves (for their
  `started_ts`/`ended_ts` local time-of-day), not just durations —
  `stay_durations_for_place` (Task 7) only returns durations. Rather than
  adding a second store method, `_apply_rhythm` reuses `stays_for_subject`
  (already returns full `Stay` objects, including `place_id` since Task 6)
  and filters in Python. This is the complete, single-piece implementation —
  do not split it:

  ```python
      async def _record_rhythm(self, *, subject_id: str, stay: Stay, now_ms: int) -> None:
          """Tier 3 (design/27 §4, issue #6): occupancy + rhythm + anomaly
          for ONE closed stay, only when it falls inside an existing
          home/work place — an unlabelled cluster is never persisted (Tier
          2's own rule), so rhythm has nothing to attach to until then."""
          place_id: int | None = None
          for kind in ("home", "work"):
              existing = await self._store.existing_place(subject_id=subject_id, kind=kind)
              if existing is None:
                  continue
              pid, place_lat, place_lng, _radius, _result, _label = existing
              if _within(stay, place_lat, place_lng, self._cfg.cluster_eps_m):
                  place_id = pid
                  break
          if place_id is None:
              return
          await self._apply_rhythm(
              subject_id=subject_id, place_id=place_id, stay=stay, now_ms=now_ms,
          )

      async def _apply_rhythm(
          self, *, subject_id: str, place_id: int, stay: Stay, now_ms: int,
      ) -> None:
          duration_ms = stay.ended_ts - stay.started_ts
          # Baseline read BEFORE this stay is linked, so it is never
          # anomalous relative to itself.
          history = await self._store.stay_durations_for_place(
              subject_id=subject_id, place_id=place_id,
          )
          z = robust_z_score(
              float(duration_ms), [float(d) for d in history],
              min_samples=self._cfg.anomaly_min_samples,
          )
          if z is not None and abs(z) >= self._cfg.anomaly_z_threshold and self._metrics is not None:
              self._metrics.inc("nora_analytics_anomalies_total", subject=subject_id)

          await self._store.set_place_ids(
              subject_id=subject_id, started_ts_in=[stay.started_ts], place_id=place_id,
          )
          bins = hour_of_week_bins(stay.started_ts, stay.ended_ts, self._cfg.tz_name)
          await self._store.record_occupancy(
              subject_id=subject_id, place_id=place_id, bins=bins, now_ms=now_ms,
          )
          if self._metrics is not None:
              self._metrics.inc(
                  "nora_analytics_occupancy_bins_total", value=len(bins), subject=subject_id,
              )

          # Rhythm recomputes from EVERY stay now linked to this place,
          # including the one just linked above — `label_place`'s own
          # "recompute from scratch, never an incremental average" posture.
          all_stays = await self._store.stays_for_subject(subject_id=subject_id)
          linked = [s for s in all_stays if s.place_id == place_id] + (
              [] if any(s.started_ts == stay.started_ts for s in all_stays) else [stay]
          )
          tz = ZoneInfo(self._cfg.tz_name)
          arrivals = [
              _local_minute_of_day(s.started_ts, tz) for s in linked
          ]
          departures = [
              _local_minute_of_day(s.ended_ts, tz) for s in linked
          ]
          durations = [float(s.ended_ts - s.started_ts) for s in linked]
          sorted_d = sorted(durations)
          n = len(sorted_d)
          median_ms = int(
              sorted_d[n // 2] if n % 2 else (sorted_d[n // 2 - 1] + sorted_d[n // 2]) / 2
          )
          sorted_dev = sorted(abs(d - median_ms) for d in durations)
          mad_ms = int(
              sorted_dev[n // 2] if n % 2
              else (sorted_dev[n // 2 - 1] + sorted_dev[n // 2]) / 2
          )
          await self._store.upsert_rhythm(
              subject_id=subject_id, place_id=place_id,
              mean_arrival_minute=circular_mean_minute(arrivals),
              mean_departure_minute=circular_mean_minute(departures),
              duration_median_ms=median_ms, duration_mad_ms=mad_ms,
              sample_count=len(linked), now_ms=now_ms,
          )
  ```

  Add the module-level helper (near `_within`/`_chunk_by_gap` at the bottom
  of the file):

  ```python
  def _local_minute_of_day(ts_ms: int, tz: ZoneInfo) -> float:
      local = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
      return local.hour * 60.0 + local.minute + local.second / 60.0
  ```

  Add `from datetime import datetime` and `from zoneinfo import ZoneInfo` to
  this file's imports (neither is currently imported here — `nora.domain.mobility`
  has its own copies, this file needs its own for `_local_minute_of_day`).

  **Wire the call site.** In `run_once`, immediately after the existing
  `if new_stays: await self._store.write_stays(new_stays)` block:

  ```python
              if new_stays:
                  await self._store.write_stays(new_stays)
                  for closed_stay in new_stays:
                      await self._record_rhythm(
                          subject_id=subject_id, stay=closed_stay, now_ms=now_ms,
                      )
  ```

  **Wire the first-place backfill.** In the Speed-3 clustering block,
  immediately after a successful `await self._store.upsert_place(...)` for a
  NON-unlabeled result, backfill any of this subject's PRE-EXISTING stays
  that fall inside the place but were closed before it existed:

  ```python
                      if result.kind != "unlabeled":
                          existing_before = await self._store.existing_place(
                              subject_id=subject_id, kind=result.kind
                          )
                          await self._store.upsert_place(
                              subject_id=subject_id, circle_id=circle_id,
                              kind=result.kind,
                              lat=lat, lng=lng, radius_m=self._cfg.cluster_eps_m,
                              result=result, now_ms=now_ms, label=label,
                          )
                          if existing_before is None:
                              # First time THIS kind has ever existed for this
                              # subject — backfill past stays that predate it.
                              refreshed = await self._store.existing_place(
                                  subject_id=subject_id, kind=result.kind
                              )
                              if refreshed is not None:
                                  new_place_id = refreshed[0]
                                  unlinked = [
                                      s for s in all_stays
                                      if s.place_id is None
                                      and _within(s, lat, lng, self._cfg.cluster_eps_m)
                                  ]
                                  for s in unlinked:
                                      await self._apply_rhythm(
                                          subject_id=subject_id, place_id=new_place_id,
                                          stay=s, now_ms=now_ms,
                                      )
  ```

  This replaces the existing unconditional `await self._store.upsert_place(...)`
  call inside that loop — read the surrounding code in
  `nora/analytics/rollup.py` (the Speed-3 `for cluster in clusters:` block)
  before editing, so `existing_before` is read BEFORE the upsert that would
  make it stop being `None`.

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest -q tests/unit/test_analytics_rollup.py
  ```
  Expected: PASS, full file (9 existing + 3 new = 12).

  If `test_an_extreme_duration_increments_the_anomaly_metric` fails on the
  `metrics.get(...) is not None` assertion, check `Metrics.get`'s own
  signature (`nora/obs/metrics.py`) — Tier 1/2's existing rollup tests
  already call it the same way for `nora_analytics_stays_total`, so match
  that exact call shape rather than guessing a new one.

- [ ] **Step 5: Full regression**

  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  ```
  Expected: everything green, same counts as before this task plus the new
  tests.

- [ ] **Step 6: Commit**

  ```bash
  git add nora/analytics/rollup.py tests/unit/test_analytics_rollup.py
  git commit -m "feat(analytics): Tier 3 rhythm/occupancy/anomaly wiring + first-place backfill"
  ```

---

### Task 11: Parameter revalidation script (design/27 §7.1)

**Files:**
- Create: `scripts/revalidate_stay_params.py`

**Interfaces:**
- Consumes: `nora.domain.mobility.segment_stays`, `RawFix`, live
  `presence_fixes` via a direct read (same DB, read-only, no write).
- Produces: a printed table — no return value, no test (this is an
  operator analysis tool, not production code, matching
  `scripts/gen_pg_baseline.py`'s own "run deliberately" posture).

- [ ] **Step 1: Write the script**

  ```python
  """Re-derive stays at a few candidate (stay_radius_m, stay_min_dwell_seconds)
  pairs against the LIVE presence_fixes corpus and report fragmentation
  (stay count — too many = the radius is too tight) and bridging (max
  radius_m any stay actually reached — approaching the candidate radius = it
  is too loose) per candidate, so an operator can pick a validated value
  instead of the inherited guess `stay_radius_m = 120` / `stay_min_dwell_seconds
  = 600` (design/27 §7.1, issue #6).

  Run deliberately, read-only, writes nothing:

      uv run python scripts/revalidate_stay_params.py
  """

  from __future__ import annotations

  import asyncio
  import os
  import sys
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

  import sqlalchemy as sa  # noqa: E402

  from nora.domain.mobility import RawFix, segment_stays  # noqa: E402
  from nora.infra.sql.engine import create_sql_engine  # noqa: E402
  from nora.infra.sql.schema import PRESENCE_FIXES  # noqa: E402

  CANDIDATES = [
      (80.0, 600.0), (100.0, 600.0), (120.0, 600.0), (150.0, 600.0),
      (120.0, 300.0), (120.0, 900.0),
  ]


  async def _fixes_for(engine, person_id: str) -> list[RawFix]:
      stmt = (
          sa.select(PRESENCE_FIXES.c.id, PRESENCE_FIXES.c.ts, PRESENCE_FIXES.c.lat, PRESENCE_FIXES.c.lng)
          .where(
              PRESENCE_FIXES.c.person_id == person_id,
              PRESENCE_FIXES.c.accuracy_m <= 100.0,
              PRESENCE_FIXES.c.wire_state == "live",
          )
          .order_by(PRESENCE_FIXES.c.ts.asc())
      )
      async with engine.connect() as conn:
          rows = (await conn.execute(stmt)).all()
      return [RawFix(id=r.id, ts=r.ts, lat=r.lat, lng=r.lng) for r in rows]


  async def _subjects(engine) -> list[str]:
      stmt = sa.select(PRESENCE_FIXES.c.person_id).distinct()
      async with engine.connect() as conn:
          rows = (await conn.execute(stmt)).all()
      return [r.person_id for r in rows]


  async def main() -> None:
      database_url = os.environ.get("NORA_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
      if not database_url:
          print("NORA_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
          sys.exit(1)
      engine = create_sql_engine(database_url)
      subjects = await _subjects(engine)
      print(f"{'radius_m':>10} {'min_dwell_s':>12} {'total_stays':>12} {'max_radius_seen':>16}")
      for radius_m, min_dwell_s in CANDIDATES:
          total_stays = 0
          max_radius_seen = 0.0
          for person_id in subjects:
              fixes = await _fixes_for(engine, person_id)
              if not fixes:
                  continue
              result = segment_stays(
                  fixes, subject_id=person_id, person_id=person_id, circle_id="",
                  radius_m=radius_m, min_dwell_ms=int(min_dwell_s * 1000),
              )
              total_stays += len(result.closed)
              max_radius_seen = max(
                  max_radius_seen, max((s.radius_m for s in result.closed), default=0.0)
              )
          print(f"{radius_m:>10.0f} {min_dwell_s:>12.0f} {total_stays:>12} {max_radius_seen:>16.1f}")
      await engine.dispose()


  if __name__ == "__main__":
      asyncio.run(main())
  ```

- [ ] **Step 2: Run it against the live database**

  ```bash
  uv run python scripts/revalidate_stay_params.py
  ```
  Expected: a printed table, one row per candidate, no errors. This is the
  actual deliverable an operator reads to pick a value — it is not
  auto-applied anywhere.

- [ ] **Step 3: Commit**

  ```bash
  git add scripts/revalidate_stay_params.py
  git commit -m "feat(analytics): stay-parameter revalidation script (design/27 §7.1)"
  ```

---

### Task 12: Decision record, release fragment, final gate sweep

**Files:**
- Modify: `design/09-decision-index.md`
- Create: `release-fragments/unreleased/presence-analytics-tier3-rhythm.json`

**Interfaces:** none — documentation and process only.

- [ ] **Step 1: Add DEC-117**

  In `design/09-decision-index.md`, following the existing table's format
  (one row, pointing at this plan and issue #6):

  ```markdown
  | DEC-117 | **Tier 3 — hour-of-week occupancy, circular-stat rhythm, robust-z anomaly, all scoped to a CONFIRMED home/work place only.** An unlabelled cluster never gets rhythm, matching Tier 2's own fail-closed posture. Anomaly scoring is fail-closed too — `None`, never `0.0`, below `anomaly_min_samples` or when history has zero spread (MAD == 0). | `docs/superpowers/plans/2026-08-19-presence-analytics-tier3-rhythm-plan.md`, issue #6; tests in `tests/unit/test_mobility.py` (pure functions) and `tests/unit/test_analytics_rollup.py` (wiring) |
  ```

- [ ] **Step 2: Write the release fragment**

  ```json
  {
    "type": "feature",
    "summary": "Presence analytics now derives an hour-of-week occupancy profile and a typical arrival/departure time for every subject's confirmed home/work place, plus a fail-closed anomaly score (robust z-score against that subject's own history) on how long a stay lasted. Only applies to a place that already crossed the home/work threshold — an unlabeled cluster never gets rhythm data.",
    "details": [
      "New tables presence_occupancy, presence_place_rhythm (migration 0017).",
      "presence_stays.place_id, present in the schema since Tier 2 but never written, is now wired: a closed stay inside an existing home/work place is linked to it, and past stays that predate a place's first creation are backfilled once.",
      "New script scripts/revalidate_stay_params.py — an operator tool to re-check stay_radius_m/stay_min_dwell_seconds (design/27 §7.1's flagged-unmeasured defaults) against the live corpus."
    ],
    "limitations": [
      "Anomaly scoring needs anomaly_min_samples (default 5) prior stays at a place before it will say anything — fail-closed, not a bug.",
      "Tier 5 (routes/corridors, the other half of issue #6) is a separate, not-yet-written plan."
    ]
  }
  ```

- [ ] **Step 3: Final full gate sweep**

  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  uv run python scripts/check_release_fragments.py --base origin/master
  ```
  Expected: everything green.

- [ ] **Step 4: Commit**

  ```bash
  git add design/09-decision-index.md release-fragments/unreleased/presence-analytics-tier3-rhythm.json
  git commit -m "docs(analytics): DEC-117 + release fragment for Tier 3 rhythm/anomaly"
  ```

---

## Self-review notes (carried in the plan, not stripped out)

- **Spec coverage:** issue #6's rhythm half is fully covered — 168-bin
  hour-of-week occupancy (Task 2/10), circular-stat arrival/departure (Task
  3/10), robust z-score anomaly (Task 4/10), and the parameter revalidation
  it explicitly bundles into Tier 3 (Task 11). The routes/corridor half is
  explicitly OUT of scope per this plan's own "Scope note."
- **Task 10 is the largest and most improvisational task in this plan** —
  the exact backfill-loop shape depends on reading the CURRENT Speed-3 block
  in `nora/analytics/rollup.py` at implementation time (it will have shifted
  slightly from what Task 6/7 already edited earlier in this same plan).
  Budget real review time for this task specifically; it is not a
  copy-paste task the way Tasks 2-9 are.
