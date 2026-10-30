# 0034 Self-hosted SigNoz unifies application and LLM observability

- **Status**: accepted (operator, 2026-10-30); implementation pending
- **Date**: 2026-10-30
- **Amends on implementation**: R-76 and DEC-143 backend/root-selection policy;
  retains DEC-141's context ownership and single execution trace
- **Index**: none yet; this records the accepted direction, not landed behavior

## Context

Nora splits application traces into Jaeger and model traces into Langfuse,
with structured container stdout, Prometheus/Alertmanager and Sentry errors.
The operator primarily uses LLM traces for debugging, not Langfuse's evaluation
suite, and needs one investigation to join an application failure, its model
calls/token usage and logs surviving container recreation. Issue
[#440](https://github.com/mikeng-io/nora/issues/440) records the source audit,
synthetic SDK proof and live readbacks separately.

The existing shared OTel provider and Strands instrumentation can be retained.
The gaps are upstream root dropping, incomplete raw-model/entry-point coverage,
identity/error/log correlation and durable collection. A backend URL change
cannot restore spans discarded at the source or logs deleted with a container.

[SigNoz v0.143.0](https://github.com/SigNoz/signoz/releases/tag/v0.143.0) added
dedicated AI Observability. The released
[Community entry point](https://github.com/SigNoz/signoz/blob/v0.143.0/cmd/community/server.go)
and [core modules](https://github.com/SigNoz/signoz/blob/v0.143.0/pkg/signoz/module.go)
include span mapping and LLM pricing; the
[AI documentation](https://signoz.io/docs/ai-observability/) covers self-hosted
usage. Token totals and estimated cost require compatible attributes and prices.
Strands' agent aggregates must not duplicate the usage on child model calls.

Historical Trustless operations exposed ClickHouse disk/merge pressure, collector
OOM during write stalls, query-memory exhaustion and duplicate log ingestion.
Those are operating costs to measure; similar container counts are not proof
that SigNoz costs the same as Langfuse. K3S/Longhorn storage failures are not
requirements to reproduce in Nora's Compose deployment.

## Decision

Adopt **self-hosted SigNoz Community** as the target for application and LLM
traces plus centralized logs. Keep Sentry for error grouping initially. Keep
Prometheus scraping and required absence-alert delivery until replacement parity
is read back. Do not add a separate log backend or a new evaluation platform.

Update [the existing Observability document](../observability.md#accepted-direction-self-hosted-signoz)
as the single target-design and acceptance owner. Keep
[the trace-content investigation](../../design/13-trace-content-and-redaction.md)
as historical SDK evidence, not a competing implementation spec. Issue #440
owns delivery/evidence links. No new decision-index row claims behavior this
ADR does not implement; amend the old rules and their enforcing tests together
when the implementation lands.

Retain vendor-neutral OTel export, one provider per process, Strands-owned
model/tool spans and domain outcome spans. Centralize bootstrap/identity and
format normalization; repair observed missing boundaries rather than wrapping
all calls again. Recording, content capture, retention and alerting are distinct
choices. Preserve complete selected traces and pre-model failures, while keeping
routine polling noise out. Existing content authorization and secret handling
remain; this decision authorizes no cloud export or data deletion.

Collect logs once from structured stdout through a persistent host source and
compatible reader, with persisted recovery state and measured buffering limits.
Use official SigNoz MCP for read-only investigations and Sentry MCP for errors.
Prove token totals, readable contents, parentage, logs after recreation and
error correlation against the pinned license-free Community deployment before
cutover. Retire shared Langfuse only after its other producers, including Cortex,
and dependent readers are accounted for; retain historical data under existing
controls rather than assuming a historical import into SigNoz exists.

## Consequences

One backend can answer an application/LLM/log investigation while the application
keeps its existing OTel/Strands instrumentation. Consolidation can release the
old tracing stack after all producers migrate; it does not do so on Nora alone.

**Worse**:

- We operate ClickHouse capacity, backups, query load, collector buffering and
  ingestion visibility ourselves; TTL does not prevent a full disk.
- Langfuse's purpose-built prompt/evaluation workflows are not feature-parity
  requirements, and dependent readers need explicit migration or continued support.
- SDK schema changes, content display and parent/child usage aggregation need
  compatibility proof; raw OTLP acceptance is insufficient.
- Sentry and the existing metric/alert path remain during acceptance, so initial
  deployment is not immediately a one-service stack or a proven resource saving.
- A single local backend increases the shared failure domain for investigations.
  The existing absence of an external dead-man receiver remains a limitation.

Reopen if representative retention/query load exceeds available host capacity,
required trace/content/usage fidelity needs substantial bespoke instrumentation,
or Langfuse-only consumers prevent the expected retirement savings. No resource
minimum from vendor documentation substitutes for those measurements.

## Rejected

- **Keep adding independent stores:** Langfuse plus Jaeger plus a new log backend
  retains the split investigation and increases the maintained surface.
- **Cloud-first consolidation:** changes the deployment/content boundary and
  recurring cost despite existing self-host capacity and the selected direction.
- **SigNoz alone immediately:** errors, absence rules, durable logs and dependent
  consumers have distinct acceptance; removing their existing paths first loses
  evidence rather than consolidating it.
- **A new observability framework or per-backend application API:** the existing
  OTel provider, SDK spans and composition roots already provide the needed seams.
