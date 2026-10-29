# Operator memory graph spike

Run manually on the host, from the reviewed checkout. Never schedule it. The
application process is never started; only the explicit projection and steps one
and two are composed. No transport, step three, reminder or note writer is held.
Identity and policy are read only. All SQL engines used by the experiment point
at the snapshot or the separate AGE container.

## Snapshot from a dump

Use a new Compose project and empty volumes for every experiment. Set the dump
path to an operator-created custom-format `pg_dump --no-owner` archive outside
any checkout. The dump must include the schema through `0139_comprehend`.
These containers use loopback-only ports and contain private data.

```sh
umask 077
export SPIKE_DUMP="$HOME/nora-spikes/input/snapshot.dump"
export SPIKE_PROJECT="nora-graph-spike-$(date +%Y%m%d%H%M%S)"
export SPIKE_COMPOSE="$PWD/deploy/memory-graph-spike/compose.yaml"
docker compose -p "$SPIKE_PROJECT" -f "$SPIKE_COMPOSE" up -d --wait snapshot
docker compose -p "$SPIKE_PROJECT" -f "$SPIKE_COMPOSE" exec -T snapshot \
  pg_restore --username postgres --dbname nora_snapshot --no-owner --no-privileges \
  --exit-on-error < "$SPIKE_DUMP"
```

The main compose project and its volumes are never referenced. Keep the dump and
volumes under operator custody; no data files belong in the repository.

## AGE

```sh
docker compose -p "$SPIKE_PROJECT" -f "$SPIKE_COMPOSE" up -d --wait age
```

The spike checks extension version `1.8.0` and server major `18` before loading.
The separate image is [Apache's PG18 AGE 1.8.0 build](https://hub.docker.com/r/apache/age/tags?name=release_PG18_1.8.0).
AGE maintains its own graph schema; it cannot read the `relation` table directly.
Its image and compatibility with future Postgres upgrades are adoption costs.

## Run

In the operator shell, retain the configured production database environment
variable: it is the refusal reference, never the destination. Load model/search
credentials by the host's normal mechanism; do not put them in command arguments
or copy local config files into this branch. Set the following model route and
vendor variables to the two independently selected judges (different vendors).
No vendor or model is selected by the harness.

```sh
export SPIKE_OUTPUT="$HOME/nora-spikes/memory-graph-$(date +%Y%m%d%H%M%S)"
uv run python -m scripts.memory_graph_spike \
  --config "$HOME/workspace/nora/config.toml" \
  --identity "$HOME/workspace/nora/identity.toml" \
  --policy "$HOME/workspace/nora/policy.toml" \
  --database-url postgresql+asyncpg://postgres@127.0.0.1:55432/nora_snapshot \
  --snapshot --per-stratum 20 --minimum 20 --seed 20261029 \
  --judge-a "$SPIKE_JUDGE_A" --judge-vendor-a "$SPIKE_JUDGE_VENDOR_A" \
  --judge-b "$SPIKE_JUDGE_B" --judge-vendor-b "$SPIKE_JUDGE_VENDOR_B" \
  --age-url postgresql+asyncpg://postgres@127.0.0.1:55433/nora_age_spike \
  --output "$SPIKE_OUTPUT"
```

`SPIKE_OUTPUT` must not already exist. The harness creates it mode **0700**;
files are mode **0600** and written exclusively. `sample.json` freezes source
snapshots before loop model calls. `questions.json` freezes SQL-derived gold
answers from the built graph before query-writer calls. Their SHA256 values
appear in `report.json`. `receipts.json`, `web-lookups.json`, `judgements.json`
and `query-results.json` are private per-item evidence. Only aggregate results
reach stdout. Exit 0 requires every gate row PASS; ERROR, INSUFFICIENT and FAIL
produce nonzero exits. A crash emits only a generic ERROR, never private SQL or
provider error details. Dispose of the experiment containers/volumes according
to the host's private-data retention policy.

## Measurement limits

- The checked-in October 27 precision-spike implementation could not be found in
  the extraction/backfill code or extraction design. The harness reuses the
  shared grounding criterion/parser in `evals/rebuilt/judge.py`; continuity with
  the historical spike prompt is **unverified**.
- Recall judges whether each source's storable statements are covered, including
  stored attachment and page readings. It measures source coverage, not an
  independently human-labelled count of individual facts. Missing strata cannot
  pass. Runtime sources without stored readings cannot be reconstructed here;
  the harness offers local graph search and existing public web providers only.
- Monetary cost is **ERROR**: current comprehend receipts retain total tokens,
  without input/output billing split or usage from failed calls. Reported token
  medians/p95 and reshape sessions/item are diagnostics, never dollar estimates.
- The AGE model arm deliberately accepts a bounded Cypher path grammar, executed
  in a graph populated only by code-selected unit/person scope. The SQL arm
  accepts relation names compiled into the production `WALK_SQL`. Neither model
  chooses the unit, graph name or SQL. This compares bounded depth-three path
  writing, not arbitrary Cypher or unconstrained SQL. One question is generated
  per distinct unit/seed/relation; a small graph yields INSUFFICIENT.
- Both latency paths include database execution/round-trip time; AGE population
  and model latency are excluded. A single pass is a pilot, not a load test.
  AGE adoption requires p95 below 200 ms and strictly more correct model queries;
  the operator decides whether that difference is clear enough.
- Nothing in a local test proves the host spike passed. Production data has not
  been read or run by this change.
