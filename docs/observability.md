# Observability

**The accepted observability design, the deployed instrumentation baseline, and
how to tell them apart when something is missing.**

`AGENTS.md` carries the terse rules and `docs/maintainers/conventions-rationale.md` the war-stories
behind them; neither says how the pieces fit together, which is the gap this
file exists to close. It is the map — what the three signals are for, how a
span physically reaches a collector, what makes a span a *generation*, and how
to read any of it back. Rules are stated here only where the how-to needs them;
their reasoning stays in `docs/maintainers/conventions-rationale.md` and is linked, never copied, because
two copies drift and the one that drifts is the one you did not read
(`docs/adr/0010-agents-md-is-the-entry-point.md`).

- [Accepted direction: self-hosted SigNoz](#accepted-direction-self-hosted-signoz)
- [The three signals, and the boundary between them](#the-three-signals)
- [How a span reaches a collector](#how-a-span-reaches-a-collector)
- [Instrumenting a model call](#instrumenting-a-model-call)
- [What counts as a generation](#what-counts-as-a-generation)
- [Reading it back](#reading-it-back)
- [When a span is missing: the five places it can die](#when-a-span-is-missing)
- [Logs](#logs)
- [Known gaps](#known-gaps)

---

## Accepted direction: self-hosted SigNoz

**Decision accepted 2026-10-30; implemented and proven locally, not yet
deployed.** [ADR 0034](adr/0034-self-hosted-signoz-unifies-application-and-llm-observability.md)
records why. [Issue #440](https://github.com/mikeng-io/nora/issues/440) tracks
implementation and its evidence. This section owns the target design and
acceptance; the sections below describe the existing Langfuse/Jaeger deployment
until the replacement is deployed on the host — a read-back backend is not a
running one until then.

**Local acceptance record (synthetic, Docker Desktop, `signoz:v0.148.1` +
collector `v0.144.15`):** the repository's own ingester config runs the local
stack (custom image `deploy/signoz/collector/Dockerfile` adding `journalctl`);
synthetic generation/plumbing/error/undeclared traces exercise the tail
policies; the filelog pipeline delivers docker-json lines with `trace_id`/
`span_id` promoted for log↔trace correlation; all 19 alert rules provision via
`scripts/signoz_provision.py` and evaluate — `TargetDown` fired and
`IngestionSilent` resolved into a webhook sink end-to-end; the SigNoz MCP
server answers trace/log/metric queries. What remains unproven is only what
locality cannot prove: Linux journald (Docker Desktop has no journal — the
receiver retries every 2s against an empty mount, as expected), a real Discord
delivery, and the host's own volumes/capacity.

### Scope and ownership

Use **SigNoz Community** for application APM, LLM/agent traces and centralized
logs. Retain Sentry for grouped error reporting, linked to the same OTel trace.
The standalone Prometheus, Alertmanager and alert-bridge services are RETIRED in
this change — `deploy/signoz/alerts/rules.yaml` ports all 19 rules 1:1 (verified
against `deploy/prometheus/rules/nora.yml` in git history) and the SigNoz
webhook channel reaches Discord directly; `prometheus-client` and each service's
`/metrics` endpoint stay (the ingester's prometheus receiver scrapes them).
Sentry errors alone do not replace detection of a stopped collector, stale work
or an absent target.

The dedicated AI Observability experience shipped in
[SigNoz v0.143.0](https://github.com/SigNoz/signoz/releases/tag/v0.143.0).
The [Community server](https://github.com/SigNoz/signoz/blob/v0.143.0/cmd/community/server.go)
uses the shared core, whose
[modules](https://github.com/SigNoz/signoz/blob/v0.143.0/pkg/signoz/module.go)
include pricing and span mapping. Pin a supported Community release and matching
collector; verify that exact build without a paid license. Do not reuse the
historical Trustless chart merely because it already runs SigNoz.

Use the supported [Foundry Docker deployment](https://signoz.io/docs/install/docker/)
to render inspectable Compose configuration; retain the repository's existing
GitOps/deployment ownership. Version the non-secret declaration, rendered pipeline
and version pins with their deployment inputs; keep actual host settings and
credentials private. The generated backend is not a second owner of Nora's
application releases. Its local data volumes need persistent storage and backup.
Do not share Langfuse's existing ClickHouse data directory or schemas with SigNoz.

No additional Loki, Grafana or Quickwit stack is part of this decision. No prompt
management/evaluation platform or replacement for the eval runner is introduced.

Related bundles keep their own responsibilities: [platform bridge operations](../design/platform-bridges/04-operations.md#short-linked-otel-operations-and-routing-amendment)
carry queue lifetimes/links and the transitional route; [evaluation SDK compatibility](../design/evaluation/08-the-sdk-surface.md#7-observability-migration-compatibility--2026-10-30)
requires populated Sessions after any span-format change. Neither duplicates the
backend/cutover contract here.

### One instrumentation path

Keep one `TracerProvider` per process. Application/domain spans use OTel; Strands
owns agent/model/tool spans. Raw model clients use the shared SDK span boundary,
without moving a raw request onto an agent loop to obtain telemetry. Preserve
import-layer boundaries: composition roots wire collaborators, and an infra
module does not import a higher layer for a tracer. An Agent call gets no second
model wrapper and no overlapping OpenAI auto-instrumentor.

Reuse the existing bootstrap functions at every actual process entry point.
Main Nora's startup cannot initialize presence, analytics or a bridge process.
Cover application HTTP routes and measured DB/client gaps with standard OTel
instrumentation where appropriate; retain domain spans for outcomes a successful
HTTP/SQL call cannot describe. Routine health/metrics polling remains noise.

Every entry point has one execution root with child stages. Preserve W3C context
propagation and the captured context for queued work; independent background work
starts its own root. A conversation/session identifier joins related executions,
not one indefinitely open trace. Stamp service name, deployed source/version and
environment consistently; use the actual installed release, never a guessed SHA.
Cognition uses its session identity rather than borrowing a room's identity.

### LLM contents, usage and cost

The [AI Explorer](https://signoz.io/docs/ai-observability-explorer/) must show one
complete application trace, its individual model/tool calls, input/output tokens
and trace totals. Captured input/output must be readable, not merely present on
the ingestion wire. Capture policy and field format are separate decisions.

Keep Strands as the model instrumentation library. An isolated synthetic probe of
installed Strands 1.50.2 verified that
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_span_attributes_only`
emits input/output message attributes while preserving input/output token counts.
This is an SDK-format result, not proof of SigNoz UI compatibility or a changed
runtime setting. Check system messages, tool arguments/results, cache fields,
errors and multi-call agents against the pinned collector/backend before enabling
it. Existing content authorization and secret redaction remain; changing the
backend does not decide the open field-level redaction questions in
[the content investigation](../design/13-trace-content-and-redaction.md).
No cloud export or additional content capture is implied.

Use the [documented GenAI attributes](https://signoz.io/docs/ai-observability-telemetry-requirements/)
and one shared SDK/collector normalization boundary. The stock contrib collector
is not assumed to contain SigNoz's `signozspanmapper` or `signozllmpricing`:
configure the matching SigNoz collector pipeline explicitly where required.
Keep requested gateway alias, served model and provider distinguishable. Configure
[model prices](https://signoz.io/docs/ai-observability-model-pricing/) explicitly,
including provider-specific cached-token accounting. Report estimated cost,
not an authoritative invoice; an unpriced model is unknown, not free.

**Only actual model invocations contribute to call/token/cost totals.** Strands
can attach a model and aggregate usage to an agent parent as well as its child
model spans. Read back classification and totals to prevent double counting;
retain the parent as an agent span. Unknown usage stays unknown, not fabricated
zero. Raw vision calls need their own model span and per-call usage, including
individual video windows; an outer comprehension span is not that measurement.

### Recording, content and retention are separate

Replace the Langfuse-oriented assumption that non-LLM roots can be discarded
before the backend sees their eventual failure. This amends the target of R-76
and DEC-143; their current sampler/tests remain active until the implementation
changes them together. Preserve their no-noise intent, one-root context model,
and counters for routine outcomes.

The SigNoz path branches from incoming OTLP **before** the old Jaeger `chat`
filter and Langfuse has-model selector. Retain complete selected traces, including
user-visible failures before any model call. Dropping a span at SDK head sampling
cannot be undone by a collector after an error occurs.

Select whole traces using actual entry-point outcomes, not one global latency
threshold for HTTP requests and long agent turns. Tail-sampling decisions need a
window/capacity appropriate to Nora's measured durations and late spans; do not
copy Trustless's window or threshold. If there are multiple sampling collectors,
route all spans of a trace to the same sampler. Compute rate metrics from the
unsampled stream when deriving them from spans.

Selecting a trace and retaining its content are independent. Outcome-dependent
content capture needs buffering or another proven mechanism; a stock tail sampler
does not automatically retain skeletons of all traces and full contents of some.
At the observed LLM volume there is no demonstrated need to drop healthy AI traces
just to imitate high-volume API sampling. Start with complete AI traces; set
broader application retention from measured ingestion, queries and disk growth.

### Durable logs and correlated errors

Keep structured stdout as the single application log source. Include active
trace/span IDs when available, alongside existing severity, exception fields and
turn provenance; a line outside a span has no invented correlation ID. Sentry
continues error capture, with the same active OTel trace and deployed release,
without creating a second tracing owner.

Persist logs outside the lifetime of a container. Use persistent host journald
with Docker's journald driver and a compatible reader, then forward to SigNoz.
The currently deployed collector image does not include `journalctl`; a journald
receiver declaration alone cannot read the journal. Verify a compatible reader
image/runtime and host mounts before switching the driver. Keep journal size/time
retention explicit. A durable source survives container recreation; it does not
reconstruct logs already deleted with an old container.

Persist reader cursors and exporter queues where supported, and measure replay,
rotation and downstream-outage recovery. Cursor persistence alone is not a durable
export queue. Bound batches/queues and configure `memory_limiter`/backpressure;
state what happens after buffers fill. Delivery is not claimed exactly once:
verify one canonical ingestion path and quantify any retry/replay duplicates.
Do not also send the same records through an application OTLP logging bridge.

### Capacity and failure boundaries

Trustless's historical incidents establish reachable costs, not Nora's required
resource sizes: ClickHouse ran out of merge/TTL scratch space; write stalls grew
collector memory; percentile queries hit query memory limits; duplicate log
paths inflated storage. Its Longhorn expansion incident was a storage-layer
failure that this Compose deployment does not inherit.

Measure ingest bytes, retained storage growth, query load and peak memory under
Nora's intended retention. Leave disk headroom for ClickHouse merges/cleanup;
TTL is not a storage quota. Verify the chosen release's reserve/backpressure
settings rather than blindly copying old ClickHouse configuration. A 4 GB Docker
prerequisite is not a measured steady-state footprint. Missing ingestion must
be distinguishable from a genuine zero-error/zero-usage interval.

### Cutover and acceptance

One integrated implementation follows this design; these are evidence boundaries,
not parallel task lists. Use generated data, with no production messages copied
into fixtures and no paid model calls required for the ingestion proof.

1. **Application/SDK:** export a multi-call agent with a tool, a raw vision call,
   and a failure before any model call. Check parentage, session/service/release
   identity, explicit outcomes, missing-usage behavior and leaf-model totals.
2. **Collector/Community UI:** send those spans through the pinned real pipeline.
   Read full traces and input/output in the license-free Community UI; match
   per-call and trace token/cache/cost values to the generated source. Confirm
   parent summaries add no second charge. No HTTP 200-only acceptance.
3. **Logs/errors:** emit an identifiable synthetic exception under a span. Query
   the line via SigNoz, follow matching trace IDs from Sentry, then recreate the
   source container and restart the reader/backend. Verify retained history and
   replay after a bounded backend outage, including any duplicates or losses.
4. **Debug access:** use official SigNoz MCP with read-only investigative access
   to find the synthetic log, trace and usage. Reuse Sentry MCP for error issues;
   do not add Nora runtime tools for operating the observability backend.
5. **Capacity and operations:** measure representative queries and storage growth;
   prove missing-ingestion visibility and absence-alert delivery before retiring
   the old scrape/alert path. An alert evaluated by a stopped backend cannot
   report that backend's death; the existing lack of an external dead-man path
   is a known ceiling, not solved by consolidation.
6. **Retirement:** migrate every producer before stopping shared services.
   Langfuse also serves Cortex; moving Nora alone cannot retire its stack.
   Keep historical data/backups accessible under existing access/retention
   controls; no bulk import into SigNoz is assumed and no data deletion is
   authorized here. Verify Langfuse-dependent eval/readback consumers have a
   replacement or remain supported. Retire Jaeger and old collector filters only
   after trace acceptance. Alertmanager and alert-bridge are retired in this
   change — the 19-rule port and webhook delivery are verified locally above.

Rollback before retirement selects the previous backend routing and application
configuration; it does not erase the new data. Do not assume SDK-dropped or
expired data can be recovered by changing the exporter. Deployment and retirement
follow the existing deploy/readback skills, with the operator notified before a
live deploy. SDK proof, backend proof, merged code and deployed acceptance remain
separate states.

---

## The three signals

The boundary is not a style preference. It was measured, and getting it wrong
once cost the collector its usefulness: on 2026-08-28 **97 of the operator's
last 100 Langfuse traces were ingest plumbing**, across twelve rooms Nora does
not serve. A collector that is 97% plumbing cannot be read for the turns that
matter.

| signal | store | answers | the shape of its question |
|---|---|---|---|
| **metrics** | Prometheus (`:9100/metrics`) | how many, how often, with what outcome | *aggregate*, over the deployment |
| **traces** | Langfuse (+ Jaeger) | what happened in THIS turn, in what order, at what cost | *one instance*, re-derivable |
| **logs** | stdout, JSON lines (`docker logs nora`) | what a specific line of code saw | *forensic*, after the fact |
| **errors** | Sentry | which stack trace, how often, and is it new | *grouped*, per exception |

**The rule that keeps them apart:** Langfuse is an LLM-observability tool, so a
trace reaches it when the work under it *can contain a model call*. Everything
else Nora does — sequencing ingest stages, writing a row, resolving an identity,
counting a refusal — is plumbing, and plumbing's questions are Prometheus'.
That is R-76; `docs/maintainers/conventions-rationale.md` has the argument and the numbers.

Metric names are append-only. Extraction records through `nora_extraction_*`;
the six retired `nora_message_reading_*` counters stay declared at zero so saved
queries remain valid. Their new names start fresh series; historical samples
are not moved. `nora_extraction_failures_total{stage}` identifies the failing
boundary: input, request, usage, provider_model, incomplete, schema, claims,
publish, cancelled or storage. The vocabulary and metric help derive from
`ExtractionFailureStage`; no exception text or private content is recorded.
Failure counts retain their existing semantics, including one failure when
recording a failed attempt also encounters a storage outage.

`nora_extraction_dropped_claims_total{reason}` counts individual claims that fail
a provenance check. Valid claims still publish; a nonempty output with no valid
claims fails at `claims`, while zero claims still completes. Schema-invalid
output fails as a whole at `schema`, with no salvage. The reason vocabulary and
help derive from `ClaimDropReason` and are runtime-checked. Each rejected claim
gets its first failing reason, not one count per failed check.
`extraction_attempt.dropped_claims` durably holds only reason-to-count JSON,
never dropped claim content or message text. Counts are saved before publication
and retained if publication or the all-invalid check fails. A failed count write
prevents publication (`storage`). Counters count attempts, including retries;
they are process-local and are not a precision measurement. A drop is a recorded
rejection, never a silent skip or proof that every assertion was extracted.

Extraction allows five attempts per batch: transient failures can recover, while
identical invalid output cannot consume calls forever. A failed fifth attempt
sets `extraction.state = 'failed'`, never `done`; it is no longer automatically
selected. `nora_extraction_terminal_batches_total` counts successful transitions
to this terminal state (a repeated/stale failure report does not count again).
Counters are process-local; `SELECT id, attempts, due_at FROM extraction WHERE
state = 'failed' ORDER BY id` is the durable reconciliation view. After repairing
the cause, run `uv run python scripts/extraction_requeue.py ID` with the
existing `DATABASE_URL` environment set. The script calls `SqlExtraction.requeue`. It retires the failed batch, retains snapshots and attempt
history, and releases its queue rows to form a fresh batch with a new allowance.
Re-queueing an already reconciled batch returns false. No claims are published
by failure or re-queueing. Cancellation leaves the lease for recovery. An expired
fifth lease or a legacy over-ceiling pending batch transitions to failed on selection, counted without
a further model call.

New snapshots show each new message's `quoted_ts` and resolved `reply_to` ID
(or null when missing, hidden, ambiguous or outside the context allowance).
Eligible direct reply parents from the same queued unit/platform/room take
priority within `context_messages`; nearby conversation fills the remainder.
There is no age cutoff or recursive parent walk, and sender-supplied quoted text
is never used. Context stays uncitable. Existing pending snapshots remain
frozen without reply fields and can still publish against their original shape;
`VERSION = 1` remains replay-compatible.

**Three states on every instrument, never two** — `ok` / `nothing_found` /
`unavailable`. A wiring gap is `unavailable` ("I could not check"), never
`nothing_found` ("there is nothing"). This is R-27, and it applies to a span
attribute exactly as it does to a counter label: `nora.outcome` on a
`comprehend` span carries the same word its counter does, so an operator holding
one media handle does not have to reason about a deployment-wide rate.

---

## How a span reaches a collector

Four hops, and **two independent filters**. Both can silently discard your span,
and only the first is visible from inside this repo — which is why the second is
written down here at all.

```
  nora process                     otel-collector (nora-host)              stores
  ────────────                     ──────────────────────              ──────
  span(...)                                                        ┌─▶ Langfuse
      │                                                            │   :3100
      ▼                                                            │
  ① build_sampler()  ──▶ OTLP/HTTP ──▶ ② tail_sampling/langfuse ───┘
    ParentBased(          :4318/          keeps a WHOLE trace iff it has
      _PlumbingIs         v1/traces       a chat or invoke_agent span
      NotATrace)                     ──▶ ② filter/jaeger  ──────────┬─▶ Jaeger
                                          drops chat spans only     │   :16686
                                                                    ┘
```

**① The SDK sampler (`nora/obs/tracing.py`)** decides *roots*, and children
inherit under `ParentBased` — a child never re-decides. A root declaring
`nora.trace_kind = "plumbing"` is dropped; anything else is kept, including an
undeclared root (getting it wrong towards export costs noise, which complains
about itself; getting it wrong towards silence costs an invisible gap).

Two consequences worth holding on to:

* **`nora.trace_kind` on a non-root does nothing.** It is not an error, it is
  inert, and it reads to the next person as though it were working.
* **The sampler reads attributes set AT SPAN START.** `span()` takes its
  attributes as arguments for exactly this reason; an attribute added by
  `annotate()` one line later is one the sampling decision was made without.

**② The collector filters** (`~/workspace/otel-collector/config.yaml` on nora-host)
are a second stage that this repo does not own and cannot see. Read from the
file 2026-10-01; an earlier version of this section described a
`service.name != "nora"` filter that is no longer there:

* the Langfuse pipeline is **tail sampling per trace**: it keeps the whole
  trace iff it holds at least one span whose `gen_ai.operation.name` is `chat`
  or `invoke_agent`, after a 90 s decision wait. A per-span filter cannot do
  this: the app root carries no GenAI marker, so a span filter drops the root
  and orphans the model call (measured 2026-09-14/15);
* the Jaeger pipeline drops **only `chat` spans**, which carry the prompts and
  reasoning. Strands also stamps `gen_ai.operation.name` on `invoke_agent`,
  `execute_event_loop_cycle` and `execute_tool`, so the earlier "has the
  attribute" test dropped the whole agent and tool tree (measured 2026-09-23).
  Read back 2026-10-01: two engagement traces in Jaeger had 1 root, 0 orphans,
  and lacked exactly their `chat` spans.

So "it is not in Jaeger" is evidence about `chat` spans only: a generation is
in Langfuse, its agent, cycle and tool spans are in both, and the plumbing
under a generation root is in both.

The deployed endpoint is `http://host.docker.internal:4318/v1/traces`. The
`/v1/traces` path is **required**: `OTLPSpanExporter` appends it only when the
endpoint comes from an environment variable, and a literal `endpoint=` is used
verbatim — without the path it POSTs to the collector root and 404s (seen live
2026-09-04, 31 export failures). The collector's own exporter is the opposite
and auto-appends, which is why Langfuse there is configured with
`traces_endpoint`. Two SDKs, two conventions, one afternoon lost.

### Session and observation type (`TraceIdentityProcessor`)

Langfuse is a trace store; what makes it more than a list of traces is that a
trace belongs to a **session** and each span has a **type**. Read back
2026-10-01: `/api/public/sessions` held 0 items and 100 of 100 recent traces had
a null `sessionId`; 55-70% of an engagement trace's observations were untyped
`SPAN`s (`store.*` reads, stage spans, one `execute_event_loop_cycle` per model
call) against 4-16 generations.

`nora.agent.telemetry.TraceIdentityProcessor` is registered on the provider in
`setup_agent_tracing`, so it sees Strands spans and Nora's own alike:

* **Session.** A span started with `nora.conversation_id` (the roots and the
  stages that pass it to `span()`) gets `session.id`, which Langfuse reads, and
  `gen_ai.conversation.id`, which OTel GenAI defines as "session, thread". A
  span without it inherits its parent's `session.id`, because Langfuse asks for
  it on each span. Strands spans always take this path: Strands opens a span
  and sets `trace_attributes` after it started, past what the processor sees,
  so the bare `conversation_id` in `trace_attributes` stamps nothing. Inheritance
  follows trace parentage, so a detached span cannot take another room's
  session. The session is the room, so a
  group room is one long session: the docs are silent on session length, and
  OTel's own definition of a conversation is the thread, not a day.
  An id Langfuse would drop (over 200 characters, or non-ASCII) is left
  unstamped, never truncated.
  A Dreamer unit has no room. `dream_loop` opens each (adaptor, key) unit as
  its own `dream:<adaptor>` root carrying adaptor, key and `nora.outcome`, and
  its `nora.dreamer.pass_id` is the session, so one tick's units group as one
  session. Before 2026-10-09 a tick was one `dream.pass` trace that named no
  adaptor.
* **User is not stamped.** A Langfuse trace has one `user.id` and a group
  room has many senders, and it would name a person on a trace whose
  attributes are provenance only (`trace_attributes`). Whether a person's id
  belongs there is the operator's call, not a gap this processor fills.
* **Type.** `langfuse.observation.type` is set from the span name, first match
  wins: `engagement.admission` is a `guardrail`, `store.*` a `retriever`, other
  `engagement.*`, `agent.prefill` and `execute_event_loop_cycle` a `chain`.
  Anything else is left to Langfuse, which types model calls, agents and tools
  from their GenAI attributes (read back as GENERATION, AGENT, TOOL). A span
  that already declares a type keeps it.

The processor is never load-bearing: a failure costs the stamp, not the span.
Enforced by `tests/unit/test_trace_identity.py`, including a test that reads the
stamp through the provider `setup_agent_tracing` registers, because a class
that exists and passes alone says nothing about the provider the deployment
runs.

---

## Instrumenting a model call

**As of 2026-09-19, Nora no longer hand-spells the GenAI/Langfuse dialect.**
There are two paths, and which one a call site uses depends on whether it goes
through the Strands Agent SDK at all:

**Agent-backed calls** (the tag turn, the debrief fold, the standing fold —
all three via `nora.agent.structured.ask_text`/`ask_structured`, since
`4448c737`) need **no instrumentation code at all**. Strands' own `Agent`
instruments itself: an `invoke_agent {name}` span plus a `chat` model-invoke
span underneath it, both carrying the OTel GenAI semantic conventions and
Strands' own redaction lever (see "The open redaction question" below). A
caller that wants extra structured fields on those spans (`nora.tag_id`,
`nora.conversation_id`, a call label) passes them as `Agent(trace_attributes=
{...})` — `ask_text`'s own `trace_attributes` parameter threads this through:

```python
got = await ask_text(
    model=provider, prompt=prompt, timeout_seconds=timeout, system_prompt=system,
    agent_name="debrief",
    trace_attributes={"nora.tag_id": tag_id, "nora.conversation_id": conversation_id},
)
```

Do **not** additionally wrap an `ask_text`/`ask_structured` call in a span of
your own — the Agent already opens one, and a second span around it produces
two overlapping traces for one model call (exactly the defect this replaced;
see `nora/engagement/stages/debrief.py::_complete`'s own docstring for the
incident).

**Raw `chat.completions.create` calls** (the relevance gate, the vision
describer — the two that must stay off the Agent SDK; see "What counts as a
generation" below for why) instrument through
`nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`, thin
wrappers around Strands' own `Tracer.start_model_invoke_span`/
`end_model_invoke_span` — the same span shape and vocabulary a real Agent
invocation produces, without needing one:

```python
from nora.agent.telemetry import end_raw_model_span, start_raw_model_span

span = start_raw_model_span(
    messages=[{"role": "user", "content": [{"text": prompt}]}],
    model_id=model, system_prompt=system,
)
response = await client.chat.completions.create(...)
end_raw_model_span(
    span=span, content=content, finish_reason=finish_reason,
    input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
    total_tokens=usage.total_tokens,
)
```

`nora.infra.vision.client`'s raw call is the one exception that instruments
neither way — see "Known gaps" below.

**Enforced, in the narrower sense that survives the migration**:
`tests/architecture/test_raw_model_calls_use_strands_telemetry.py` still scans
for a hand-typed `gen_ai.*`/`langfuse.observation.*` string literal outside
`nora/agent/telemetry.py`, so a call site cannot reinvent a THIRD dialect
alongside Strands' own. It does **not** — and, given `nora.obs` may not import
`nora.agent`, cannot — bind `trace_safe` (or any redaction) to a call site the
way the old `generation()` docstring implied; see the redaction section below.

**Why this replaced the old helper.** `nora.obs.tracing.generation`/
`annotate_generation` were a hand-rolled `langfuse.observation.*` + `gen_ai.*`
dialect that two call sites had already spelled two different ways before the
helper existed (read back 2026-09-09: 1041 live traces, four dialects, one
modality invisible) — and Strands' own `Tracer` already implements the same
convention, upstream-maintained, for the SDK path. Keeping a second,
hand-rolled copy of that convention for the four raw/hand-instrumented call
sites meant maintaining two things that had to agree with each other and with
Strands' own spelling; adopting Strands' vocabulary for the raw path too
removes the second copy. It also fixed a live bug the old design carried
silently: the debrief/standing fold moved onto a real Strands `Agent` in
`4448c737`, but nothing removed the OLD hand-rolled `generation()` wrapper
still sitting around that call — so every fold, consolidate, and verdict call
produced two nested, overlapping traces (the hand-rolled span AND Strands' own
agent/model-invoke spans underneath it) for one model call, which is the
concrete shape behind "10+ separate traces per message" in Langfuse.

### The open redaction question

Strands' own `Tracer` has a **native redaction lever**
(`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=<list>`), which is
what `design/13-trace-content-and-redaction.md` (still `status: open-decision`)
investigates. It is **off by default**, meaning every Agent-backed model call
— including the tag turn's own — sends its real content to the collector
unredacted today. That is a config surface, not a per-call-site obligation:
there is no `trace_safe`-style call a debrief/standing-fold caller makes
anymore, because there is no seam left to intercept Strands' own automatic
event recording (`Tracer._add_event_messages` writes the real messages
directly). `nora.agent.security.trace_safe` still exists — its identifier/
coordinate screen is unrelated to Agent-backed spans and still guards every
other exit it always did (dream-screened replies, place lookups) — but no
current call site uses it to pre-screen content bound for a trace: the two
raw call sites below never send real content in the first place (see "What a
raw call owes", above), so there is nothing left for it to screen there
either. For anything Agent-backed, `design/13`'s lever is the only mechanism,
and it is not yet turned on.

---

## What counts as a generation

**A call to a language model.** Not everything that runs a model:

| call | generation? | why |
|---|---|---|
| the tag turn, the debrief fold, the standing fold (Strands) | yes | instrumented by the SDK itself — a real `Agent` invocation since `4448c737` |
| the relevance gate | yes | raw `chat.completions.create`, instrumented through `nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span` (must stay off the Agent SDK — see `nora.agent.structured`'s own docstring for the measured safety reason) |
| the media comprehension (`comprehend`) | yes | a describer or a summarizer under every branch, though the describer's own raw call has no model-invoke span of its own (Known gaps, below) |
| the STT service (`deploy/stt`) | **no** | local ASR on Nora's own private network, not an LLM |

The STT line is the one that needs saying out loud, because its absence looks
like an oversight. Its outcome ships on `nora_speech_transcriptions_total` with
R-27's three states — the Prometheus question it actually is — and the words it
produces *do* reach a trace, through the summary pass over the transcript, which
runs under `comprehend`. Putting local ASR in an LLM-observability tool is a
small version of the 97%-plumbing mistake.

**A raw model call owes more than a span.** Two paths bypass the agent SDK
deliberately (the relevance gate and the vision describer — down from four
before `4448c737` moved the debrief and standing fold onto the SDK
transport), and what such a client must carry itself — a bound, a
`finish_reason` read, a usage annotation where the layering allows it — is
enforced separately by `tests/architecture/
test_raw_model_calls_carry_their_own_machinery.py`. That gate and the dialect
gate above (`test_raw_model_calls_use_strands_telemetry.py`) are
complementary: one is about what the *client* does, the other about how the
*span* is spelled.

---

## Reading it back

No claim about tracing is finished until the trace has been read back. The
Langfuse and Jaeger UIs answer from a dev machine on the LAN, but authenticated
reads run ON the deploy host (`ssh nora-host`), so the key never leaves it.

**Langfuse.** Credentials are the base64 blob inside `LANGFUSE_OTLP_HEADERS` in
the deploy host's `~/workspace/nora/.env`, URL-encoded (`%20` after `Basic`,
`%3D` for each `=` of padding): decode the `%3D` or every call is 401. Use it as
HTTP Basic auth. Other `workspace/*/.env` files hold keys for *different*
Langfuse projects and will 401 against Nora's. Run authenticated reads on the
deploy host, so the key never leaves it (the `nora-readback` agent skill has
the full command):

```bash
# on the deploy host; B64 is the header value after `Basic%20`, with %3D decoded to =
curl -s -H "Authorization: Basic $B64" "127.0.0.1:3100/api/public/traces?limit=50&page=1" -o traces.json
curl -s -H "Authorization: Basic $B64" "127.0.0.1:3100/api/public/traces/<full-32-char-id>" -o one.json
```

Write to a file and parse the file: with `rtk` on the path, piping `curl` into a
parser gets you a *schema summary* instead of JSON.

**Jaeger.** `127.0.0.1:16686/api/services` on the deploy host, then
`/api/services/nora/operations` — the fastest way to answer "does this span name
reach a collector at all", remembering that Jaeger holds only the spans that are
*not* generations.

**Prometheus.** `ssh nora-host 'curl -s localhost:9100/metrics | grep ^nora_'`.
Read the counter first: it tells you whether the work is happening, which
separates "not instrumented" from "not running" before you go looking for a
span.

---

## When a span is missing

In the order it is worth checking, because each step is cheaper than the next:

1. **Is the work running at all?** The Prometheus counter for that path. A
   missing span and an idle path look identical in a collector.
2. **Was it declared plumbing?** A root with `nora.trace_kind = "plumbing"` is
   dropped by the SDK sampler. This is the one that hid the media read.
3. **Is it a root when you think it is a child, or the reverse?** Trace ids
   answer this and nothing else does — a rootless span *is* a trace, so "one
   trace with gaps" and "N traces" are the same picture until you compare ids.
   `create_task` inheritance is the usual cause.
4. **Which collector should have it?** A generation goes to Langfuse and is
   dropped from Jaeger by the collector filter. Absence from one is not absence.
5. **Did the export itself fail?** Export happens on `BatchSpanProcessor`'s
   background thread and never surfaces in the app. A wrong endpoint path 404s
   silently (see above).

Tests assert against the collector the deployment actually has: the
`exported_spans` fixture in `tests/conftest.py` is built with production's own
`build_sampler()`, so an assertion about "what reaches the collector" is not
measuring a provider no deployment runs.

---

## Logs

`nora/obs/logging.py` — structlog in front of the root logger, rendering **JSON
lines** to **stdout** (a `StreamHandler`; read them with `docker logs
nora`, and `| jq` because they are JSON), with secret
redaction (R-16) and `tag_id` bound through
`structlog.contextvars` so every line emitted during a turn carries it without
being threaded through call signatures.

Every call site uses plain stdlib `logging.getLogger`; there is no
`structlog.get_logger()` anywhere. Third-party libraries' records ride the same
`foreign_pre_chain` into the same JSON stream.

**This was a file until 2026-09-09**, and `docker compose logs nora` was
empty — a `FileHandler` that predated the container by one day and was never
revisited. Anything that tells you to grep `data/nora.log` is stale.

### The fields

| field | on | what it is |
|---|---|---|
| `ts` | every line | RFC 3339, UTC, `…Z`. Never naive — a store windows and sorts on it |
| `level` | every line | `INFO`/`WARNING`/… — what a HUMAN greps, and what `docs/runbook.md`'s saved queries were written against |
| `severity_number` | every mapped level | OTel SeverityNumber — what a MACHINE compares |
| `logger` | every line | dotted module name |
| `message` | every line | the event |
| `tag_id` | inside a turn | bound by the engagement pipeline and the fire path |
| `exception`, `exception.type`, `exception.message`, `exception.stacktrace` | on an exception | the blob, plus OTel's three parts |

### Severity is a number, not just a word

`level` sorts alphabetically — ERROR before INFO before WARNING — so "at least
a warning" cannot be expressed as a comparison over it. The [OTel Logs Data
Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) exists for
that, and defines the answer as a number in **bands**: TRACE 1-4, DEBUG 5-8,
INFO 9-12, WARN 13-16, ERROR 17-20, FATAL 21-24. `severity_number` carries it,
and `SEVERITY_NUMBERS` in `nora/obs/logging.py` is the mapping.

We emit the BASE of each band, because nothing here distinguishes "a slightly
worse ERROR". **An unmapped level gets no field rather than a guessed one** —
the same three-state discipline R-27 imposes on every other instrument: absent
means "not stated", never "zero".

### An exception is three fields, not one blob

`format_exc_info` renders the whole traceback into one `exception` string with
the type and message buried in its last line, which makes "how many
`TimeoutError`s since the deploy" a substring search over multi-line blobs. The
[OTel exception semantic
conventions](https://opentelemetry.io/docs/specs/semconv/exceptions/exceptions-logs/)
name the three parts and `_exception_fields` writes them; the blob stays, so
saved greps keep working. Those conventions also say an unhandled exception
SHOULD be recorded at ERROR (severity 17), which is what `log.exception` does.

**A traceback we cannot parse sets neither derived field** rather than half of
one — the last line of a Python traceback is `Type: message`, or a bare `Type`,
and anything else is left alone.

### A log line is not a metric

This is R-76's judgement, one signal over. It was learned the same way and cost
the same thing: measured over 37 days on the deployment, **78% of 205,099 log
lines said that nothing had happened**.

- `aiohttp.access` — 108,441 lines (53%), one INFO per poll of a server that
  routes `/health` and `/metrics`. Silenced at the source (`access_log=None` in
  `nora/obs/health.py`). How often something scraped is a question for the
  scraper; whether the endpoint is healthy is the healthcheck's own verdict.
- `nora.heartbeat` — 51,964 lines (25%), two counters restated every minute
  whether or not they moved. Now **INFO when they moved, DEBUG when they did
  not**, with the first beat always INFO because it records where the counters
  started.

The rule that generalises: **if the answer is "how many, how often, with what
outcome", it is a counter, and a log line that restates it every interval is a
third copy of a number two other systems already hold.** Log an EVENT — a
decision, a refusal, a failure, a state change — not a reading.

**The known cost, stated rather than discovered later:** an idle-but-alive
deployment now emits nothing at the default level, so the log alone no longer
distinguishes idle from dead. That was always better served by `/health` and by
the `nora_*` gauges, and it comes back the moment anything scrapes them — see
the gap below.

### Not settled

What to log at WARNING versus ERROR, and which fields a given event must carry,
is still per-author. The transport, the field set and the two rules above are
conventioned; the editorial judgement is not.

## Alerting

Three stores and no reader was the state until 2026-09-09; the reader is now
the SigNoz backend itself. The ingester's prometheus receiver scrapes the
same three endpoints, `deploy/signoz/alerts/rules.yaml` carries the same 19
rules (`scripts/signoz_provision.py` applies them to the backend), and a
SigNoz notification channel POSTs the alert to its sink — a Discord webhook
in deployment, an HTTP endpoint in test.

```
nora:9100  ─┐
presence:  ─┼─▶ signoz-ingester ──▶ ClickHouse ──▶ SigNoz rules ──▶ Discord
  9101      │   (scrape 30s,       (series)        (every 1m,       (webhook
analytics: ─┘    prometheus        store)          per-rule)       receiver)
  9103          receiver)
```

**It lands in the same Discord channel the participation mirror posts to**, so
an alert arrives beside the determinations it is about rather than in a second
place to remember.

**The rule semantics ported intact** — every expression, `for` window, severity
and annotation of the old `deploy/prometheus/rules/nora.yml` (in git history)
carries across. Two changes the port required, both recorded at the top of
`rules.yaml`: scrape `job`/`instance` labels do not survive the OTel prometheus
receiver — queries and annotations use `service.name`/`service.instance.id`
instead — and `PrometheusNotIngesting` is replaced by `IngestionSilent`, which
measures the ingestion path's own scrape (`absent(up{service.name=
"/signoz-otel-collector"})`), not the old Prometheus self-scrape.

**An alert path that shares a failure domain with what it watches is
decoration**, so the delivery chain is still outside Nora's process: the
backend evaluates and the webhook POST goes from the SigNoz container, not
through her. A Discord webhook URL is created by hand in the Discord UI and
carried as `NORA_ALERT_WEBHOOK` (secret.toml delivery) — the retired
alert-bridge's `DISCORD_BOT_TOKEN` REST path is gone with it.

**Every rule is an ABSENCE**, which is the whole reason this exists alongside
Sentry rather than instead of it — see "what counts as an error" below.

| alert | fires on | the incident behind it |
|---|---|---|
| `TargetDown` | `up == 0` for 3m | `nora-egress.service` sat `failed` for sixteen days |
| `HeartbeatStalled` | tick age > 300s | a stalled loop in a process that still answers HTTP |
| `PlatformSilent` | adapter event age > 900s (keep-alives and heartbeats count, so it is a silent socket, not a quiet room) | the one condition the old shell watchdog checked — on no timer, into no channel |
| `PresenceCollectionStalled` | a circle's last successful collection is over 1800s old | 2026-09-24: Life360 stopped accepting the collector's login and the container stayed healthy while nothing recorded |
| `PresenceNotRecording` | no location fix written for anyone in 2h, restarts included | the restart-proof twin of the rule above: a gauge that counts from boot is reset by every recreate |
| `PresenceAuthRefused` | Life360 refused the login (`auth_403`, `banned`, `challenge`) | a refused login does not heal on its own and a restart does not fix it |
| `Life360TokenRejected` | the `LIFE360_AUTH_TOKEN` was rejected | token mode never falls back to the password, so a dead token stays dead until a person copies a new one |
| `MusicWedged` | nora believes a track is playing and the audio controller reports no sound | the divergence is the failure; a wedged controller sat green for days |
| `MusicControllerUnreadable` | 5+ unreadable controller polls in 10m | «I do not know» is not «not playing», and needs different hands |
| `MusicStreamFailing` | 3+ music streams failed in 15m | the loud failure the silent one hides behind: a track that keeps failing burns the queue |
| `UnavailableOutcomes` | any `*_total{status="unavailable"}` increasing | R-27's third state — the thing this codebase produces *instead of* raising |
| `MediaCustodyNotConfigured` | attachments arriving with custody off | the silent degrade `media.py` counts rather than raises |
| `ComputerDown` | `nora_computer_up == 0` for 5m | the 2026-10-01 audit of Nora's computer found no series at all that said computerd was down, hung, stopped by a lapsed Docker login, or answering 401 after a token rotation |
| `ComputerCallsFailing` | 5+ computer calls answered `unavailable` in 15m | computerd answering and failing (a full disk, an OS error) keeps `nora_computer_up` at 1 |
| `ComputerAuditFailing` | any computer audit write failing, by stage | `nora_computer_audit_failures_total` was never seen live: its series did not exist until the first failure |
| `OAuthRefreshStale` | an image OAuth lane has not refreshed for twice its `refresh_interval_hours` (DEC-308), on the timestamp the boot republishes | 2026-10-09: the xAI login was refused on the first call after twelve idle days, and nothing had logged it |
| `OAuthAccessTokenExpired` | a lane's access token is past its expiry for 30m | xAI's token lives ~6h, so waiting for the 48h staleness window would leave a lane stuck on a transient failure broken for most of two days |
| `OAuthRefreshRefused` | a lane's refresh token was refused, or its state dir is unwritable | neither heals by retrying: it needs a new login, so it fires at once instead of waiting for the staleness window |
| `IngestionSilent` | the ingester's own scrape produces no `up` for 5m | a monitoring stack's own failure mode is to go quiet and leave everything green |

**What this pipeline cannot say, stated rather than discovered.** There is **no
dead-man switch**: `IngestionSilent` is evaluated BY the SigNoz backend, so a
backend that is down cannot report its own death — the same ceiling the old
stack had. A dead-man switch needs a receiver OUTSIDE this deployment that
alerts on the absence of a regular ping (an external heartbeat service); the
repo carries none. **Not alerted on, on purpose (2026-10-03,
AGENTS.md «A guard needs a consequence»):** a restart loop, a rising error count, a
stalled cognition wake loop, a stimulus backlog, failing cognition sessions and a
lost delete. Five of them were built 2026-10-02 around a loop that had never run, and
the sixth guarded a held delete that was never once held. A cognition problem is read
from the rows (`cognition_wake_outcome`, `cognition_session`) when someone notices.

`UnavailableOutcomes` is written generically over every `*_total` carrying the
label, so a counter added next month is covered the day it ships — the same
derive-don't-hand-copy rule AGENTS.md holds for label vocabularies. Its summary
used to read `{{ $labels.__name__ }}`, which rendered empty (Prometheus drops the
metric name in `increase` and `sum`; read back with promtool 2026-10-01), so it
now says which query names the counter. `nora_computer_calls_total` is the one
series left to its own thresholded rule.

**The computer is a labelled gauge on purpose (DEC-261).** `nora_computer_up` is
`{target="computerd"}`, set by a 30s authenticated probe and by every computer
call. An unlabelled gauge is exported at 0 from the moment it is declared, so a
deployment with `[computer] enabled = false` would read as a computer that is
down; a labelled one has no sample until it is set, so "not applicable" is the
absence of the series and `ComputerDown` (`== 0`) stays silent. The cost of an
absence-free rule is that a dead Nora exports nothing either, and `TargetDown` is
the rule for that. The two computer counters are created at zero at start
(`nora.app.computer_metrics.ComputerMetrics.prime`), because `increase()` over a
series whose first sample is already 1 reads 0 and a rare failure after one of
nora's 2 to 24 daily restarts (the 2026-10-02 audit: 47 starts in 4.5 days) would be invisible to the rule written for it.
Read back: `ssh nora-host 'curl -s localhost:9100/metrics | grep ^nora_computer_'` shows
`nora_computer_up` (1 when healthy) and the zero-valued counters; with the computer
off it shows neither.

**Thresholds are deliberately slack** (900s for platform silence, not the old
script's 300s; a 12h repeat interval). A rule that cries wolf is a rule
somebody mutes, and a muted channel is the state we started from.

### What counts as an error

Sentry (`nora/obs/errors.py`, `[obs.errors]`) holds the other half, and the
split is not about tooling preference:

|  | Sentry | Prometheus |
|---|---|---|
| answers | "what threw?" | "what stopped?" |
| detects | a bad event **appearing** | a good event **disappearing** |
| sees | the 223 `log.exception` sites, plus anything escaping to the top | a counter label, a gauge that stopped moving, a target that went away |

**Neither substitutes for the other here, and the asymmetry is architectural.**
This codebase is built not to raise — 210 docstrings promise it, 45 metric specs
declare an `unavailable` state — so its characteristic failure is a counter
label rather than a traceback. Nothing threw when the media read exported zero
traces for two weeks; nothing threw when `nora-egress.service` was dead for
sixteen days. Sentry would have shown green through both.

Sentry is errors **only** — `traces_sample_rate` is 0. A second tracing
pipeline would write a second, incompatible trace id for the same turn, and a
trace that exists twice differently cannot be re-derived from either copy.
Tracing stays with `nora.obs.tracing` and lands in Langfuse.

## Known gaps

Recorded here so they are findings rather than surprises.

- **The main startup does not pass a release to Sentry.** Images now carry
  source/release provenance, but that does not automatically populate error
  events or the OTel resource. The target design wires the installed identity
  into both; a historical statement that images are deliberately unstamped is
  not the current reason for this gap.
- **WARNING-versus-ERROR is still per-author**, as is which fields an event
  must carry. The transport and the field set are conventioned now; the
  editorial judgement is not.
- **A vision read exports as a generation costing zero tokens.** `nora.infra`
  may not import `nora.obs`, so the vision client cannot annotate its own span;
  the counts would have to travel out to a caller that can — `MediaDescription.
  provenance` already travels that way. It is a port-shape decision, weighed
  against the rule that usage belongs on the span and never in a return type.
  Recorded next to the exemption it justifies in
  `test_raw_model_calls_carry_their_own_machinery.py`.
- **A video comprehension is one span covering several sequential model calls.**
  The per-chunk calls are not individually visible; the caveat about what the
  read did not see rides in `provenance` instead.
- **Debug spans from probes share the production Langfuse project.**
  `probe2`, `in-container-probe`, `nora-plain-span`, `fanout-test-span-2` and
  others are still in it (counted 2026-09-09).
- **The debrief/standing fold lost a redaction guarantee they used to have,
  moving onto strands.telemetry (2026-09-19).** Before this, the hand-rolled
  wrapper around those calls ran `nora.agent.security.trace_safe` on the
  prompt/completion before putting either on a span — a whole-text identifier/
  coordinate screen, independent of `design/13`'s native-lever question. Now
  that the call runs through a real Strands `Agent`, its content reaches the
  collector however Strands' own event recording writes it, with no seam left
  to substitute pre-screened text — so this content is exactly as unredacted
  as the tag turn's own Agent-backed content already was (consistent with the
  existing precedent, not a new exposure class, but a real guarantee this
  specific call site used to have and no longer does). `design/13`'s native
  lever is the only mechanism left that could restore it, for every
  Agent-backed call at once, and it is off by default.
- **The relevance gate's prompt (every room's transcript, including rooms
  Nora does not serve) now reaches the collector too — an OPERATOR DECISION,
  not a side effect discovered after the fact.** Moving the gate's raw call
  onto `nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`
  (2026-09-19) means Strands' `Tracer` records `gen_ai.system.message`/
  `.user.message` span events unconditionally — the same mechanism that
  carries real content for every Agent-backed call, which this raw call
  never went through before. Before this change, the gate's span carried
  only its own safe verdict JSON; the comment in `nora/ingest/
  relevance_gate.py` that said the prompt was never put on the wire was true
  until this date. `design/evaluation/08-the-sdk-surface.md` §4.2 had
  already named this exact fork — "either unscreened model text goes on the
  collector wire, or a second gate-specific mapper is written… 40 of 150
  [eval] cases wait on it" — one day before the session that resolved it.
  The operator was asked directly and chose to accept it, specifically to
  unblock that eval-suite gap; this was not verified by re-running the eval
  mapper against a live case and should be confirmed by whoever picks that
  up next. `design/13`'s native redaction lever governs this content too
  (`Tracer._redact` runs on both the Agent path and this raw-span path), so
  turning it on would cover both at once.
