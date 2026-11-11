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

The graph-only replay scorer, its `--rescore` and `--decision-judge` entries
are retired; `python -m scripts.memory_graph_spike` refuses. The Cognition-loop
evaluator — `python -m scripts.cognition_loop` — is the evaluator: it composes
the deployment's real session runner per case, advances the sandbox's Record
between the case's recorded wakes, diffs every owned store, and rolls two
judges' answers up fail-closed.
links and scoring-input order. New verdicts retain explicit source IDs/digests.

Decision inputs project the statement's agenda item(s), cited rows, assembled
model-call responses and tool results. Stream chunks and repeated request
histories are not sent. Unsupported statements stop at their saved proposal;
misses run from the item's first showing through the session's end. Observations
use reasoning marked `observed`; `not_mentioned` never covers absent/redacted
reasoning. Call anchors and response/end ranges retain original event IDs.

The final formatted user prompt is capped at 64,000 UTF-8 bytes. An input over
that ceiling is recorded as truncated and is not sent; its statement and receipt
are ERROR. HTTP failures, context overflow and invalid judge JSON record
`judge_error` and per-statement `judge_unavailable`, counted separately from
`insufficient_evidence`. A lack of relevant emitted reasoning remains
`insufficient_evidence`. No automatic paid retry is added.

Judge requests use non-streamed JSON responses so the gateway's served-model and
provider headers are present. Streamed responses without those headers record
identity as `unavailable`, even if their body contains a model name; aliases are
never served identity. A missing identity in the original writer record remains
unavailable and cannot be recovered by re-scoring.

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
- Cost rows read `REPORTED`: total tokens and sessions per work item, per step.
  Money is tokens times the route's price; receipts carry no input/output split
  or failed-call usage. A missing usage figure turns the row `ERROR`.
- The egress row can only fire on a screen bypass: step one's own screen refuses
  a flagged query before it is logged, with a superset of the names this check
  uses. Zero here means the screen held, not that the check was exercised.
