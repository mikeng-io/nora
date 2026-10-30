# Persistence, telemetry and cutover — proposed

## Redis lifecycle

Use a pinned supported Redis image with command flags `--appendonly yes --appendfsync everysec --maxmemory-policy noeviction --no-appendfsync-on-rewrite no`, `/data` on a named durable volume, private network and authenticated ACLs. These non-secret flags belong in Compose; named credential references follow `docs/configuration.md`. No host ports, credentials in command-line examples, cache eviction, TTL on live work or `down -v`. Size disk/memory for retained backlog and AOF rewrite headroom; full disk/OOM is enqueue failure, never a successful receipt.

The default targets application interruption. Redis [documents AOF fsync trade-offs](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/): everysec may lose recent acknowledged writes under host failure. Bridge journals improve retry recovery but do not justify a blanket zero-loss promise. App restart with Redis/volumes intact is the required proof; Redis power-loss behavior is a separately documented limit.

Use XREADGROUP, XACK and XAUTOCLAIM directly, with pinned minimum Redis version supporting those commands. Reclaim pending entries before reading new entries after reader takeover. [Streams consumer groups](https://redis.io/docs/latest/develop/data-types/streams/) track pending delivery separately from retained stream length. One group is initially supported; adding another requires retention review.

Default **no automatic stream trimming** for this small installation. This is the first pending-safe retention rule, not an unbounded memory policy disguised as safety: expose usage and stop acceptance on storage failure, retaining the bridge journal. A future/manual compactor may remove only a contiguous prefix proven terminal/transferred in DB, ACKed and not pending, strictly before the group's last-delivered position. It must never remove never-delivered entries. Retain dedupe tombstones through the supported upstream replay horizon, and DB source identities while old input can recur; unknown horizon means no automatic purge. Delete attachment bytes only after their pins and work dispositions permit it. Restart/replay tests must pass before enabling a compactor.

## Aggregates with actual callsites

Proposed metric vocabulary is derived from closed code constants and declared alongside callsites in `nora/obs/metrics.py` (bridge equivalents use the same bounded vocabulary). No event/conversation/account/stream/attempt IDs or text in labels. Platform is one of the four platforms; stage/outcome are closed, not exception strings.

| Measurement | Actual proposed increment/read location |
|---|---|
| receive/enqueue/dedupe/storage failures and receipt result | Bridge capture journal and Redis Lua result boundary, action receipt confirmation |
| attempts/reclaims/retries/duplicate consumes/completion/silence/failed-retained/ACK | Reader claim, DB transition, retry scheduling and XACK result; failed-retained is never completion |
| send confirmed/rejected/uncertain/unsupported | Durable bridge_action result boundary, not HTTP request acceptance |
| waiting depth/oldest wait and pending count/oldest pending age | Inspector reads group last-delivered ID, XRANGE strictly after it for never-delivered count/oldest, plus XPENDING entries/times; waiting = never-delivered + pending entries not currently executing; report pending/in-flight separately to avoid summing overlap |
| queue wait/processing/send duration | Source-independent receive/enqueue timestamps, attempt start/end and action start/end; histogram labels only platform/stage/outcome |
| DB deferred depth/oldest due/failure retention | Actual bridge_work states, including ACKed transfers; Redis empty does not mean all work finished |
| connectivity/liveness/storage health | Redis authenticated PING + stream/group inspection + persistence INFO status, bridge upstream/capture heartbeat, reader heartbeat and last successful claim/completion |

Use a bounded paged snapshot inspector for the initial low volume; if cap/timeout/race prevents a coherent reading, mark partial/unavailable, never an exact zero. For a coherent sample, recheck stream/group progress after scanning and retry once; do not block the reader for a long scan. XLEN is retained entries, not backlog. Healthy empty produces nothing_found/zero; storage or inspection errors publish unavailable and freshness, with invalid backlog gauges omitted rather than reset to zero. Prime applicable counters at boot; absent disabled platforms stay absent. Alerts cover old waiting/pending work, retained failure, missed reader heartbeat while backlog exists, Redis persistence/disk errors and inspection unavailable. Thresholds belong in current runbook, not incident essays in Compose.

## Per-event structured lifecycle logs

Required by the operator, in addition to metrics. Reuse the stdlib/structlog pipeline, numeric severity and configured secret redaction in `nora/obs/logging.py`; bridge runtimes emit the same JSON field contract. Every transition logs a closed `event` name: captured, enqueued, duplicate_suppressed, claimed, reclaimed, attempt_started, deferred, attempt_failed, delivery_started, delivery_confirmed, delivery_uncertain, completed, intentional_silence, failed_retained, acked, ack_failed. A duplicate record says which prior event/action was reused; it must not imply a second send occurred.

Safe fields: opaque event_id, Redis entry ID (no private stream key), random attempt_id, action_id, stable tag_id when available, owner generation, platform, phase, bounded reason/outcome, source/receive/enqueue/attempt times, queue_wait_ms, duration_ms, trace_id/span_id and linked producer/previous-attempt span IDs. Source identity is hashed/canonicalized privately before telemetry; random account/conversation correlation tokens may be persisted privately if needed, never raw platform IDs. Do not log envelope/raw/body, filenames/URLs, peer names, credentials, request/response dumps or unsanitized SDK exception messages. Safe error type/reason is sufficient; private durable receipts hold bounded reconciliation evidence under existing storage access rules.

Persist attempt identity/state and original/previous attempt traceparent in the private work receipt before the work; telemetry failure never prevents enqueue, completion, recovery or ACK. Logs are searchable JSON via existing container/host log access, with bounded rotation. DB event/work/action records reconstruct truth if logs rotated or spans were dropped; neither trace export nor a log line is an execution fence.

## Short linked OTel operations and routing amendment

Current `nora/obs/tracing.py` only admits generation and plumbing; plumbing roots DROP before export. Adding queue spans with that kind would create phantom instrumentation. Propose a third **queue** root kind, an explicit operator-requested exception to deterministic-root suppression. Sample queue roots at 100% initially at this installation's volume; retain ordinary plumbing DROP and existing generation behavior. Start queue roots with this kind at creation, not annotate later. No broad “export all plumbing” switch.

Producer creates one short `queue.enqueue` span for capture/publication; its validated W3C context rides the envelope and durable receipt. If the producer retries later, new bounded span links to the original context. Consumer creates a fresh root per attempt with **SpanKind.CONSUMER**, explicit empty parent and links to producer plus previous attempt when known. The span covers one active phase only, never the queue wait or an entire deferred lifetime. Claim/reclaim/ACK and bridge send attempts are bounded queue operations linking the same event/action; no span stays open over a deployment. Use existing capture/attach helpers extended to expose safe link/parent support, and reset contextvars in finally. A linked remote parent is not installed as an unrelated active context.

When model work starts, its existing engagement/fire generation root stays a **separate trace**, linked to the relevant consumer attempt and carrying safe event/attempt identity and queue timing. Do not make it a child of a queue trace: a queue attempt and a model turn have distinct lifetimes. Under the existing deployment, ParentBased would also blur the Langfuse routing boundary; the accepted SigNoz target below removes that backend split, not the lifetime distinction. Model stages remain children of their existing turn root with current doctrine/tag provenance; queue attempt and turn trace cross-reference IDs. The queue attempt need not span the whole model turn: close handoff and open a linked completion operation when the turn ends.

Python producer/consumer uses OTel SDK through the existing provider setup; Go/Node producers need minimal OTLP SDK initialization/export and the same resource/kind/field contract. Export queue OTLP/HTTP to the existing collector, with bridge service.name reflecting service names. No hand-written GenAI attributes and no Strands wrapper around bridge operations. The queue span helper records only allowlisted error type/reason/status, not the existing generic helper’s automatic exception message/stack capture; exceptions still propagate unchanged to the work seam. Test this with a synthetic secret-bearing SDK error. Export setup/timeout/failure remains best effort.

**Collector contract amended 2026-10-30:** the accepted
[SigNoz design](../../docs/observability.md#accepted-direction-self-hosted-signoz)
owns backend selection and whole-trace retention. Queue operations and complete
model traces target SigNoz, with their separate roots and links retained. The
queue root kind remains a semantic classification; it must not prescribe Jaeger
as a permanent destination. If bridges ship before the SigNoz cutover, the
transitional route sends queue traces to Jaeger and excludes queue-only traces
from Langfuse. Apply that route before Langfuse's selector; do not carry Jaeger's
chat filter into the SigNoz path. Record which pipeline the acceptance measured.
Installed collector configuration/readback remains a separately owned prerequisite.

Synthetic readback must inspect exported span documents in the selected backend
and lifecycle JSON, not merely local exporter counts. Verify links, actual attempt
IDs, queue timing, terminal failed-retained versus silence and absence of sensitive
fields. For the transitional pipeline verify queue traces in Jaeger and no
queue-only Langfuse exports; for the target verify queue traces and complete
model traces in SigNoz. Collector outage must leave delivery behavior identical;
inspection during outage is unavailable, never “zero traces proves none ran.”
Use the observability design's durable log path once installed; container log
capture alone does not prove retention after recreation.

## Rename and lifecycle cutover

One implementation covers service/DNS/container/build-image/manifest/publisher/wrapper/verifier references, config schema/examples, docs, tests, dependency edges and touched Compose comments. Preserve truthful upstream `signal-cli` executable/artifact/version references and historical evidence. Proposed build directory `deploy/signal-bridge/` may reuse verified upstream fetch inputs; `deploy/signal-cli/` history is not rewritten. Inventory strings before changing them, classify service reference versus upstream/history, then verify no live old-service route remains. Add `deploy/discord-bridge/` and pinned bridge images with independent build fingerprints so an app-only change cannot alter bridge images/config.

The existing release wrapper already reconciles service configuration without force-recreating everything. Extend its explicit service cohort and verifier expectations; keep Redis/bridge data separate from app image volumes and no dependency restart cascade. Never run broad compose down/up as a release step. Installed wrappers live outside the fetched clone: update and verify compatibility **before** applying a manifest using new service keys; old/new manifest support must be explicit during cutover. No MASTER_DEPLOY_ENABLED flag change is part of this work.

Proposed authorized cutover order: backup canonical private config and account state without exposing contents; record bind source/destination, inode/device and uid/gid/mode privately; stop app listeners; stop the old signal-cli service; start exactly one signal-bridge with the exact same mount and account arguments; verify account identity without printing it and real daemon/capture/Redis health. Never run two CLI processes on the registration store. WA/IG reconnect under their existing state mounts; Discord app client stops before bridge token session starts. Start new app consumer at stream origin; prove synthetic backlog and send checks. All of this is future procedure, not an authorized production experiment now.

Backout preserves queue/journal/media/DB ownership data. Stop app claims; settle or retain active/uncertain actions; leave independent receivers and Redis running. Prefer rollback to a compatible prior consumer/schema; additive tables stay. If old code cannot consume v1, pause the consumer and retain backlog rather than reconnecting the old app listeners and double-processing. Signal service-name backout stops wrapper/CLI first and reuses the identical registration mount, never resets it. Restore installed wrapper/config compatibility under deploy lock; never discard streams to make a verifier green. App rollback that cannot process backlog is degraded, not success.
