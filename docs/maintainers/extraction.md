# Extraction: write-only Record arrival perception

This implements phase 2 of [the reading design](../../design/unified-memory/18-extraction.md)
and its [claim slots](../../design/unified-memory/47-character-dimensions.md#reading-claims).
Migration `0131_extraction_names` renames the original Record tables to `extraction`,
`extraction_claim`, `extraction_queue` and `extraction_attempt`.
The default is `[memory.extraction] enabled = false`.

No production retrieval consumer, prompt block, history backfill, Character
consumption, Honcho cutover or `memory_node` projection is added. The existing
Honcho feed continues. No new decision number is needed: the settled phase-2
design is the authority; this document records its implementation and limits.

## Arrival, publication and retirement

The audit transaction queues each new incoming message while enabled. Nora's
own authored replies are context, not extraction sources. Re-delivery of the
same platform message handle (or sender/timestamp when no handle is supplied)
does not queue a second reading. The source and its queue entry commit together.
Flag-off compositions enqueue nothing and start no worker; existing versions
still retire when their sources change, even after disabling the writer.

One background worker checks once a second, grouping at most `messages_per_call`
arrivals once the first queued message reaches `min_message_age_seconds`. Platform, room and recorded
unit must all match. The batch stores its source/context snapshot, version and
model before a model request. Context is at most `context_messages` earlier queued
messages from that same recorded owner, oldest first; it is never an extraction
source. This intentionally does not import pre-enable history whose unit at
arrival is unknown: `messages` has no unit column, and message index nodes are
room-scoped. The first batch has only its own surrounding conversation; later
batches accumulate context. The arrival boundary, rather than today's mutable
policy, owns this context.

The shared gateway endpoint/credential/client comes from `[llm]`; the model is
selected explicitly by `memory.extraction.model`, using the existing
`ollamacloud/deepseek-v4.1-flash` route documented for the Flash eval arm in
[the eval SOP](../eval-sop.md). There is no model fallback or inherited tag lane.
The schema refuses a non-Flash route when enabled, and the writer also refuses
a response whose reported model is absent or differs from DeepSeek V4.1 Flash.
No live provider availability or model acceptance is established by offline tests.

Each claim has structured subjects (unresolved references stay explicit),
statement, speech act, optional stated place, its own batch-member source IDs,
and exact supporting spans. Code resolves event expressions against that
source's assertion time in HKT. ISO dates/intervals and today/tomorrow/yesterday
(including their supported Cantonese/Chinese forms) resolve; other expressions
keep their source wording and reference time as unknown. No date is guessed.
Assertion times, resolved event dates and the database build timestamp are
separate. Category, medium and sensitivity never exclude a supported claim.
Exact quote/citation checks establish source grounding, not semantic entailment
of every paraphrase; that still needs an independent model acceptance measure.

An attempt has an expiring lease and attempt number. Retries use the same
frozen batch and exponential backoff, capped at one hour between attempts.
There is one transport request per attempt, a configured wall timeout and an
output ceiling. Oversized input (over 512 KB), malformed JSON, invalid citations,
missing usage, model mismatch and truncated output publish no claims. Nothing
is silently trimmed or marked read. Failed work remains pending for retry;
there is no daily spending budget yet. Operators must inspect failures and
pending work, particularly a persistently oversized or truncated batch.

Claim publication and the done transition are one fenced transaction. A crash
cannot publish a partial result, double-write a completed batch or lose pending
work. A crash after a provider processed a request but before publication can
repeat that request: the gateway has no exactly-once transaction with Postgres.
This is at-least-once transport and exactly-once committed publication, not a
claim of exactly-once provider billing.

Database triggers retire a whole reading and its claims if any source or context
message is hidden, deleted, physically pruned, edited or materially changed.
This conservative batch invalidation follows the design's reading lifecycle.
Eligible arrival rows are queued again for a valid replacement; older versions
remain provenance. Publication locks source rows and checks the frozen snapshot,
so a deletion/edit during a model request cannot resurrect stale claims.
All four tables use the existing system-time history machinery and are included
in restore contracts. There is no model-facing read API.

The writer assembles its source input through the existing context-assembly
machinery, reusing the existing `cited_sources` type with escaped JSON. It adds
no new prompt block type and sends no readings to any consumer.

## Enabling later

These are operator steps **after merge, release and verified deployment of this
code**, through [nora-deploy](../../.agents/skills/nora-deploy/SKILL.md).
This PR does not execute them. The migration runs during normal startup; no old
message is queued by migration. Confirm the single Alembic head first.

Use the installed deployment mode. In Komodo mode, use the existing native Stack
for recreation; the config CLI's printed Compose restart hint is not that mode's
deployment command. Follow [the installed procedure](../../deploy/komodo/README.md)
and retain its shared lock, backups, candidate validation and readback.

Under `flock -o -E 75 -w 900 /tmp/nora-deploy.lock`, back up the live runtime
root's `config.toml`. Copy it into the **running new-code container** at
`/app/extraction.candidate.toml` so its relative paths retain `/app` as base.
Run the existing config CLI on that candidate before copying it into the live file.

The complete host-side candidate operation, after the new release is verified,
is below. It requires the [installed host helper](../configuration.md#host-migration-and-installed-files)
and a runtime `.env` sufficient for the bare Compose copy commands. It creates
a private backup and preserves the live bind-mount inode:

```bash
cd ~/workspace/nora
flock -o -E 75 -w 900 /tmp/nora-deploy.lock bash <<'SH'
set -euo pipefail
umask 077
backup=$(mktemp /tmp/nora-before-extraction.XXXXXX.toml)
candidate=$(mktemp /tmp/nora-extraction.XXXXXX.toml)
trap 'rm -f "$candidate"' EXIT
cp config.toml "$backup"
docker compose cp config.toml nora:/app/extraction.candidate.toml
python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T --user root nora chown nora:nora /app/extraction.candidate.toml
python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora /app/.venv/bin/nora config set memory.extraction.model \
  'ollamacloud/deepseek-v4.1-flash' --config /app/extraction.candidate.toml
python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora /app/.venv/bin/nora config set memory.extraction.enabled true \
  --config /app/extraction.candidate.toml
python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora /app/.venv/bin/nora config validate \
  --config /app/extraction.candidate.toml
docker compose cp nora:/app/extraction.candidate.toml "$candidate"
cp "$candidate" config.toml
python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora rm /app/extraction.candidate.toml
printf 'Private config backup: %s\n' "$backup"
SH
```

Release the shared lock before native
`DeployStack` on `nora-releases` (its wrapper reacquires it). Verify the selected
release receipt, healthy recreated process, loaded config freshness and newly
arriving queue/attempt rows. Do not infer enablement from a file edit alone.
Restore the backup under the lock and use native recovery on failure. Disable
with the same candidate procedure and `memory.extraction.enabled false`.
Disabling preserves queued work for resumption and stops new enqueues/calls.

## One day of measured tokens

Counters are append-only, have no person/room/unit labels, and export on the
existing Nora metrics endpoint:

- `nora_extraction_calls_total`
- `nora_extraction_input_tokens_total`
- `nora_extraction_output_tokens_total`
- `nora_extraction_failures_total`
- `nora_extraction_claims_written_total`
- `nora_extraction_usage_missing_total`

Each request uses the existing raw-generation telemetry helpers under
`extraction`, with actual token usage. Only structural placeholders enter
traces: source text, subject links, identifiers and output claims stay in the DB.
Attempt rows bind to the trace (NULL when tracing is off), and durably record
reported provider identity and usage **before** output
validation, so rejected/truncated responses count toward cost. An interrupted or
unreported request keeps NULL usage, never a fabricated zero.
Prometheus still counts reported tokens if persisting that usage fails; the
attempt remains unknown in the durable query and no claims are published.

After one complete day, run this read-only query through
`python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T db psql -X -U nora -d nora` on the host. Pass `-v` values
for `start`, `pi` and `po`: use an explicit timezone for the start of a 24-hour
half-open window and the operator's verified route prices per million tokens. For
example the synthetic window `2030-04-02T00:00:00+08:00` through
`2030-04-03T00:00:00+08:00` is exactly one HKT day. Supply actual measurement
dates and prices; none are assumed here.

```bash
: "${READING_DAY_START:?set the measured day start with timezone}"
: "${READING_INPUT_PRICE:?set verified input price per million tokens}"
: "${READING_OUTPUT_PRICE:?set verified output price per million tokens}"
python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T db psql -X -U nora -d nora \
  -v start="$READING_DAY_START" -v pi="$READING_INPUT_PRICE" -v po="$READING_OUTPUT_PRICE" <<'SQL'
BEGIN READ ONLY;
WITH day AS (
  SELECT * FROM extraction_attempt
  WHERE started_at >= :'start'::timestamptz
    AND started_at < :'start'::timestamptz + interval '24 hours'
), totals AS (
  SELECT count(*) AS attempts,
         count(*) FILTER (WHERE outcome = 'ok') AS published_attempts,
         count(*) FILTER (WHERE input_tokens IS NULL OR output_tokens IS NULL) AS unknown_usage,
         sum(input_tokens) AS i, sum(output_tokens) AS o
  FROM day
)
SELECT attempts, published_attempts, unknown_usage, i, o,
       CASE WHEN attempts > 0 AND unknown_usage = 0
            THEN (i * :'pi'::numeric + o * :'po'::numeric) / 1000000
            ELSE NULL END AS c,
       CASE WHEN attempts = 0 OR unknown_usage > 0 THEN 'UNMEASURED'
            ELSE 'MEASURED' END AS status
FROM totals;
SELECT state, count(*) FROM extraction GROUP BY state;
COMMIT;
SQL
```

The day is assigned by attempt start, including retries, rather than successful
claim build time. Wait for that day's outstanding requests to settle before
querying. `I` and `O` are reported tokens across all responses, including failed
validation; `C = (I × Pi + O × Po) / 1e6`. No attempts or any unknown usage makes
the full cost UNMEASURED. A lost response may have been billed. Retired claims
do not erase spent tokens. If the provider prices cache hits separately, use
verified effective input prices or its billing receipt; a two-price estimate is
not an invoice. Choose the daily budget only after this real measurement.

Prometheus `increase(nora_extraction_*_total[24h])` is a useful cross-check
across restarts; it extrapolates and may be fractional. The durable query above
is the exact reported-token count. Failures, pending rows and unknown usage must
be reported beside cost; zero claims alone does not establish a working reader.

The retired `nora_message_reading_*` metric names remain declared at zero.
Active counters use `nora_extraction_*`; update dashboards and saved queries accordingly.
