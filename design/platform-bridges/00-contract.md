# Platform bridges — proposed contract

**Proposed, not built.** This bundle answers [issue #422](https://github.com/mikeng-io/nora/issues/422), whose current body was read on 2026-10-30, including its per-message tracing amendment. A design-only PR does not close it. No decision numbers are reserved, no runtime behavior is claimed, and no deployment is authorized.

The target is continuity across application stops/replacements: admitted events received by an independently running bridge remain owned until processing finishes or durable downstream work owns them. Optional receipt reactions mean **queued**, never completed. Host/disk destruction, upstream transport loss before bridge capture, and exactly-once arbitrary external effects are outside this guarantee. Concrete limits and retry behavior are in [recovery](03-ownership-and-recovery.md).

The authenticated live voice-utterance HTTP route remains app-local, non-queued ingress. Its 202 means accepted into the current dispatcher, not durable completion; app downtime yields a caller-visible connection/HTTP failure, with no queue receipt or restart-durability promise. It must not fabricate a platform source key or bypass the shared speaking slot, policy or bridge delivery contract. Controller connection/playout continuity is separate from accepting new spoken turns while the app is absent. Extending durable intake to voice requires a real producer utterance identity and an explicit contract amendment.

## Responsibilities and non-negotiable scope

| Owner | Responsibility |
|---|---|
| signal-bridge, discord-bridge, whatsapp-bridge, instagram-bridge | Independent process/container; account session, normalization, durable enqueue, attachments, synchronous transport operations, optional queued receipt |
| Redis | Streams directly, consumer group delivery/pending state, AOF and durable volume; no eviction or unsafe trimming |
| Nora app | Current identity/policy admission, context assembly, model/tool execution, authored replies through existing Deliverer and send_reply |
| Postgres | Recorded ingestion, per-event phase ownership/completion, deferred obligations, stable action evidence |
| Existing audio controllers | Voice connections, playout and their existing durable controller protocol |

The bridges may remain in this repository. Names are exactly lowercase `<platform>-bridge`. Signal's executable remains **signal-cli**, with the same upstream version/signature verification, Java 25 and account/device store; a wrapper must own capture/enqueue, not merely rename the CLI. See [platform integration](02-wire-and-platforms.md).

The single app-side bridge client implements the existing PlatformAdapter port. New platforms supply handlers and contract fixtures, not another app/model reply workflow. Preserve platform-qualified targets, decoration degradation, opaque quote/reaction references, roster/channel reads and true platform-confirmed send results.

Implementation is one integrated runtime change, with ordered internal commits and synthetic proof. No shadow week, speculative split PRs, live paid/model experiments, real message fixtures or production account changes. [Build acceptance](05-build-and-acceptance.md) is mandatory coverage, not evidence already obtained.

## Choices and conditions to reopen

Choose Redis Streams over Celery, Kafka and RabbitMQ: the requirement is durable event delivery, not a task framework. Existing Postgres could avoid a service, but the operator's current brief selects Redis directly. Reopen only on measured operational cost or changed operator requirement.

Choose `appendfsync everysec`: app/container replacement preserves the queue; host power failure can lose roughly the most recent second of acknowledged Redis writes, potentially longer during storage failures. Choose `always` only if the accepted host-failure target changes; neither protects destroyed disks. See the [Redis persistence contract](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).

Do not add an inbound transactional outbox: producers enqueue directly into Redis. App deferred work is stored and consumed in Postgres, with no DB-to-broker publication. Reopen an outbox only when an actual state change and publication must be atomic. Do not add an outbound queue: initial send/react is synchronous with durable action identity and explicit uncertainty.

Reuse transcript/edit/touch, stimulus, extraction, participation and turn records. Add a small inbox/phase ownership store because current records neither uniquely identify source events nor prove all their work finished. This holds unless the implementation source trace finds an existing store that provides both properties atomically.

## Design admission and remaining limits

Data loss, disclosure and concurrency require **critical execution reasoning**. Catalogue availability is not acceptance: [the exact-head source map](01-source-map.md), [failure windows](03-ownership-and-recovery.md) and executable proposed controls establish the authored argument. Independent scrutiny, final gate and publication belong to the parent.

Five Cs: Coherence assigns one owner per lifecycle; Consistency preserves app ports/stores; Conformance follows the locked service and privacy requirements; Correctness needs injected crash/concurrency proof; Completeness includes deferred tasks, Discord capabilities and every rename surface. None is satisfied merely by merging this proposal.

Open questions are bounded implementation prerequisites, not alternative architectures: prove identity for upstream transitions lacking revisions; validate installed collector routing; inspect installed wrapper and exact private state mount only during a separately authorized cutover. Until then no trace visibility, host readiness or lossless cutover claim is valid.
