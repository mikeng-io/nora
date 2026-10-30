---
name: nora-readback
description: Reads back the evidence that a Nora turn, background pass, deploy or fix did what is claimed, from Langfuse traces, Prometheus counters, Postgres rows and container logs on nora-host. Load when a task says read back, verify it ran, check the trace, is it live, did it write, count tool calls, or measure tokens, cache or cost. Not for building, recreating or rolling back containers (nora-deploy).
---

# nora-readback

`docs/observability.md` is the map (the three signals, Reading it back, When
a span is missing). This skill is the procedure and the traps. Before reading
anything, write down what a positive result would look like; a null from an
instrument that cannot see the thing is not evidence (`AGENTS.md`, Never list).

- Credentials are named by env var only; never print, copy or paste a value.
  Run authenticated reads ON nora-host so the key never leaves it.
- Write responses to a file, then parse the file.
- Report each claim as: store, query, window, reading, VERIFIED / UNVERIFIED /
  FAILED.

## 1. Pick the store by the question

| question | store | what it cannot tell you |
|---|---|---|
| what happened in THIS turn, in what order, at what cost | Langfuse trace | anything that is not a model call (plumbing is dropped) |
| did a path run, how often, with what outcome | Prometheus counter | anything before the last restart |
| what was written, and is it still there | Postgres row | why it was written |
| what one line of code saw | container log | counts (a log line is not a metric) |
| is the process on the new image or config | nora-deploy (verify-deploy, `/health`) | behaviour |

A write verb is measured by fetching the thing back, never by the response: an
HTTP 200 is a claim about the request (an Instagram reaction returned success
and never arrived, recorded PASS for a day). Record `accepted` as UNVERIFIED.

## 2. Langfuse (one turn, one pass)

Langfuse runs as its own compose project on nora-host, port 3100. The key is
`LANGFUSE_OTLP_HEADERS`, resolved through the deployment's credential source —
`scripts/secret_delivery.py`'s `source()`, process env > `.env` > `secret.toml`
— shaped `Authorization=Basic%20<base64>`; its `=` padding is URL-encoded, and
without decoding `%3D` every call is 401 (measured 2026-09-24). Decode and send
inside one Python process: the value never reaches argv, a log or stdout, and
missing/empty fails closed. (`--compose-exec` is exec-only, not a curl wrapper.)

```bash
ssh nora-host bash -s > lf.log 2>&1 <<'EOF'
cd ~/workspace/nora || exit 1
python3 - <<'PY'
import sys, urllib.parse, urllib.request
from pathlib import Path
from scripts.secret_delivery import source

header = source(Path.cwd()).get("LANGFUSE_OTLP_HEADERS", "")
name, _, encoded = header.partition("=")
if not name or not encoded:
    sys.exit("LANGFUSE_OTLP_HEADERS missing or empty in the deployment's credentials")
req = urllib.request.Request(
    "http://127.0.0.1:3100/api/public/traces?name=engagement&limit=50&page=1",
    headers={name: urllib.parse.unquote(encoded)})
with urllib.request.urlopen(req) as r:
    Path("/tmp/lf.json").write_bytes(r.read())
    print(f"HTTP={r.status}")
PY
jq -r '.meta, (.data[] | [.id, .timestamp, .name] | @tsv)' /tmp/lf.json
EOF
```

Check: `HTTP=200` and a `meta.totalItems`. Other reads, same header:

- one trace: `/api/public/traces/<full-id>`
- a tool's calls: `/api/public/observations?name=<tool>&fromStartTime=<ISO>&limit=100&page=<n>`
  (`type` is `TOOL`; group by caller through each `traceId`'s trace `name`)
- model calls: `/api/public/observations?type=GENERATION&limit=100`
- health, no key, also from the dev machine: `/api/public/health`

Traps, one line each:

- A short window reads a rare tool as dead: 3 days said 22 of 54 tools unused,
  40 days said 4 (2026-09-22). Page to 2,000 or more rows and print the
  `startTime` min and max beside every count.
- A token split that sums to `input_tokens` proves nothing. Cross-check it
  against `cache_read_input_tokens` (in `usageDetails` and
  `metadata.attributes["gen_ai.usage.cache_read_input_tokens"]`); a match to
  1.1% is what placed the tool schemas at 62% (2026-09-22). Tool schemas are
  not in the trace input; build them locally. The field is absent on some
  generations: absent is not 0.
- Tokens are not cost under a prefix cache: deferring tools cut input 36% and
  cost more, because uncached input doubled (2026-09-23). Price the cached and
  uncached split per turn.
- Not in Jaeger is not dropped: generations go to Langfuse only, and Jaeger
  holds the rest (`docs/observability.md`).

**Replaying recorded turns** (for a doctrine, voice or tool-description arm;
the 2026-09-24 scripts are not in the repo, rebuild them): take each
`engagement` trace's first GENERATION `input`; export tool schemas locally
from `ToolRegistry` specs (the trace holds only names); render the arm's
doctrine and first check the unchanged render is byte-identical to the
recorded one; POST to the deployed model alias on nora-host, one variable per arm,
always with an as-recorded arm. At temperature 0.7 one turn decides nothing;
read rates over 80 or more samples. For a labelled before/after, use nora-eval.

## 3. Prometheus (did it run)

Two sources, both on nora-host only (the dev machine gets no answer from 9190):

```bash
ssh nora-host 'curl -s localhost:9100/metrics | grep ^nora_dream_'       # nora; presence :9101, analytics :9103
ssh nora-host 'docker inspect nora --format "{{.State.StartedAt}} restarts={{.RestartCount}}"'
ssh nora-host bash -s > prom.log 2>&1 <<'EOF'
curl -sG 127.0.0.1:9190/api/v1/query_range -o /tmp/q.json -w 'HTTP=%{http_code}\n' \
  --data-urlencode 'query=<metric>' --data-urlencode "start=$(date -d '-14 days' +%s)" \
  --data-urlencode "end=$(date +%s)" --data-urlencode step=300
jq -r '.data.result[] | [(.metric|tostring), (.values|length)] | @tsv' /tmp/q.json
EOF
```

- An absent series means "nothing since the last restart", never "never
  happened": labels are created lazily and nora restarted 19 times in 7 days
  (2026-09-17). A 2026-09-12 design doc concluded "the gate never admitted"
  from exactly that.
- A rare counter reads empty almost always; never close an issue on one
  (#118, #232). Ask a durable row or a trace instead.
- "Ever / how often": `query_range` over the retention, sum positive deltas and
  treat a drop as a reset. `increase()` extrapolates to non-integers.
- Retention: flag `30d`; on 2026-09-24 `up` reached back about 15 days. Measure
  with `count_over_time(up[30d])` before quoting a window.
- A query with `[...]` goes through `curl -G --data-urlencode` (or `-g`); curl
  otherwise treats the brackets as a glob and fetches nothing (2026-09-24).
- A zero needs a positive control: a crashed stage also reads 0.

## 4. Postgres rows (what was written)

```bash
ssh nora-host 'python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T db psql -U nora -d nora -Atc "select count(*) from note"'
```

- Install the runtime `scripts/secret_delivery.py` as described in
  [configuration](../../../docs/configuration.md#host-migration-and-installed-files)
  before retiring `.env` credentials. Use its `--compose-exec -T db` mode,
  never by grepping `docker ps`: other stacks' databases on the host match.
- A standing population ("how many duplicates exist") survives restarts; ask
  it instead of an arrival counter.
- Rows are private: never paste them into a fixture, an issue, a PR or a
  commit (`AGENTS.md`, Never list).

## 5. Container logs (what a line saw)

```bash
ssh nora-host bash -s > log.txt 2>&1 <<'EOF'
cd ~/workspace/nora || exit 1
docker logs --since 1h nora | jq -c 'select(.severity_number >= 13) | {ts, level, logger, message}'
EOF
```

- `docker logs` reads the installed `nora` container without Compose interpolation
  or a Compose service-name prefix.
- An idle process logs nothing at INFO, so the log alone does not separate
  idle from dead; read `/health` or a counter.
- A recreate discards the old container's log: capture it before recreating.
- `data/nora.log` is stale (logs moved to stdout 2026-09-09).

## 6. Inside the container

```bash
ssh nora-host 'python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora /app/.venv/bin/python -c "from nora.config import load; ..."'
```

- Plain `python` lacks the dependencies; `uv run` installed 33 dev packages
  into the live venv (2026-09-17). Use `/app/.venv/bin/python` only.
- This reads the FILE, not the running process. For a loaded-once file it is
  the process's value only when `StartedAt` is after the edit and
  `curl -s localhost:9100/health | jq .config` shows `"stale": false`.

## Tool notes

- Claude Code with the rtk hook: `curl` piped into a parser returns a schema
  summary instead of JSON. Use `-o <file>` (as above) or `rtk proxy curl`.

## Credential-source amendment

Use [the configuration migration procedure](../../../docs/configuration.md) for
secret.toml, installed helper delivery and scoped recreation. `source()` reads
`.env` too, so the recipes above work unchanged on a legacy `.env`-only
deployment. Do not print values or copy rotating OAuth state into the
bootstrap source.
