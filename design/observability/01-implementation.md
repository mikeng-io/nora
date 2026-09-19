# Implementation — what exists, where, and what pins it

Everything in this file is grounded in code read at commit `b8f69093` (the
Sentry fix that landed immediately before this bundle) and re-checked against
`c7d3ca0a` and later after a five-reviewer audit found the first draft's
`nora/obs/errors.py` claims stale — it had been grounded one commit early.
File:line citations are load-bearing — re-check them before trusting a claim
here after a refactor, and re-anchor this header if this file is edited again
without re-reading the code.

## 1. The sampler — `nora/obs/tracing.py:67-132`

`_PlumbingIsNotATrace(Sampler)` reads exactly one attribute,
`nora.trace_kind` (`TraceKind = Literal["generation", "plumbing"]`), and only
on a **root** span — it is composed under `ParentBased`
(`build_sampler() = ParentBased(root=_PlumbingIsNotATrace())`), so a child
never re-decides; it inherits whatever its root was given.

Two properties are load-bearing and easy to get backwards:

- **Default is EXPORT.** An undeclared root is kept **silently**; a root that
  declares a value outside the closed `TraceKind` set is kept **and logged**
  (`tracing.py:107-115` — the `log.warning` only fires on that second case).
  Getting it wrong towards export costs noise (visible, self-complaining);
  getting it wrong towards silence costs an invisible gap in exactly the
  traces the sampler exists to protect. Only an explicit `"plumbing"` drops.
  Grounded in tests, not just asserted:
  `tests/test_span_skeleton.py::test_a_root_that_declares_nothing_is_exported`
  and `::test_an_unknown_trace_kind_is_exported_and_reported`.
- **The sampler reads attributes set at span START, not after.** `span()`
  therefore takes attributes as constructor arguments rather than exposing a
  span object to set them on later — an attribute added one line in is one the
  sampling decision has already been made without, and `nora.trace_kind` on a
  non-root is inert for the same reason (it's not read there at all).

`build_sampler()` is exported so `tests/conftest.py`'s `exported_spans`
fixture (`tests/conftest.py:125`) is built with **production's own rule** —
an assertion about "what reaches the collector" is measuring the sampler the
deployment actually runs, not a test double of it.

**Detach and drop are two separate decisions, and conflating them hid a
whole modality for two weeks.** `nora/ingest/comprehension.py:244`'s media
read used to declare `plumbing` *and* detach from its parent context
(`attached(None)`) in the same breath — until 2026-09-09 — which meant every
photo/voice-note describer call exported nothing to any collector while its
Prometheus counter recorded the reads happening (1041 live traces held zero
`comprehend` roots, read back from Langfuse 2026-09-09, while
`nora_media_comprehension_total{status="ok"}` was moving — figure carried from
`docs/observability.md`, not re-derived here). The fix keeps them
independent: `attached(None)` avoids holding the ingest trace open regardless
of trace kind; `generation(..., root=True)` — never `plumbing` — because a
describer call is exactly the kind of work R-76 exists to keep.

`nora/ingest/pipeline.py`'s highest-volume root opens **eight** `span()`
calls, not six: the `ingest` root itself (`pipeline.py:256`) plus seven
children (`.scope:283`, `.identity:297`, `.media:312`, `.audit:324`,
`.memory_feed:331`, `.standing_event:349`, conditional, `.gate:355`). "Six" is
the count of unconditional children only, excluding both the root and the
later-added `standing_event` span, and is the number carried in
`docs/observability.md` and in this file's earlier draft — restated here as
measured rather than repeated. The substantive half of the claim holds
regardless of which count is used: none of the eight wraps a model call
directly (`ingest.gate` wraps the deterministic `EngagePolicy.should_engage`,
not the LLM relevance gate, which runs from `nora/app/participation.py` with
no enclosing span of its own and opens `generation("relevance_gate",
root=True)` as a genuine root). `ingest.media` is the one child under which a
model call *can* happen (the media describer), which is exactly why that path
was re-rooted with `attached(None)` rather than left plumbing — see the
paragraph above.

## 2. The span/generation/annotate helper contract — `nora/obs/tracing.py:159-421`

Four functions, one discipline, and each guard in them is load-bearing —
never load-bearing *for the turn itself*, meaning a failure inside any of them
degrades to an untraced body rather than breaking the caller:

- **`span(name, **attrs)`** — one span, catches `BaseException` (not
  `Exception` — a cancellation must be recorded too), always re-raises, and
  guards every OTel call so a broken tracer degrades to an untraced body
  rather than taking down the turn. `_safe_attributes` coerces per-key so one
  unstringable value costs only itself, not every attribute after it.
- **`generation(name, model=, root=, prompt=, completion=, attributes=)`**
  (`tracing.py:250`) — the one spelling for "this span is a model call",
  stamping both `gen_ai.operation.name` (OTel GenAI convention) and
  `langfuse.observation.type` (what the collector actually renders a
  generation from) together, always. `prompt`/`completion` are optional and
  **omitted rather than blanked** when absent — an empty string reads as "the
  prompt was empty", which is a different, false claim.
- **`annotate_generation(...)` / `annotate(**attrs)`** — the half only known
  on the way out (completion, finish reason, token counts), applied to
  whatever span is current; a no-op, not an error, on a span that has already
  ended (the tag path's root closes at enqueue time for a QUEUED tag, so
  annotating "the root" from a worker would otherwise write into nothing).

**One dialect gate is enforced by a test; the redaction obligation below it is
not.** `tests/architecture/test_generation_spans_use_the_helper.py` derives
its forbidden-literal list from `GENERATION_ATTRIBUTES =
("langfuse.observation.type", "gen_ai.operation.name")` (`tracing.py:246`)
rather than hand-copying it, so no module outside `nora/obs/tracing.py` may
spell a generation by hand. Before this existed, two call sites had already
invented two different dialects — one carried input/output, the other
neither, and no raw client carried token usage, so every gate/debrief call in
the collector was priced at zero. Read back 2026-09-09: 1041 live traces, four
dialects for one fact (carried from `docs/observability.md`).

A second, companion gate — `tests/architecture/
test_raw_model_calls_carry_their_own_machinery.py` — enforces that each
required raw-call site carries its own timeout bound and `finish_reason` read.
**Usage annotation is not required of every site**: its `REQUIRED` map lists
`nora/app/composition.py` (the relevance gate) with all three
(`timeout`, `finish_reason`, `usage`), but `nora/infra/vision/client.py`
with only two — `nora.infra` may not import `nora.obs`, so the vision client
cannot annotate its own span, an exemption recorded next to its reasoning in
that test file and revisited in `02-open-decisions.md` §6.

**What neither gate enforces: that `prompt=`/`completion=` passed to
`generation()` was actually screened first.** §3 below shows every call site
that currently reaches `generation()` is either safe by construction (it never
passes real content) or screens by hand (`debrief.py`) — but nothing in
`tests/architecture/` binds the two together. A future call site that passes
unscreened text would not fail any test; it would just leak. This is the gap
`00-overview.md` §4 and `02-open-decisions.md` §2 hold open.

## 3. Redaction — `nora/agent/security.py:72` (`trace_safe`)

`trace_safe(text) -> (safe_text, reason)` reuses the same identifier/
coordinate predicates `nora.agent.dream_screen` calls (`scan_identifiers`,
`looks_like_coordinate`) rather than inventing a third screen, and fails
closed at **whole-text granularity** — one flagged coordinate anywhere
withholds the whole prompt, with a `*_sha256` hash surviving as a span
attribute (`nora.debrief.prompt_sha256`/`.output_sha256`) alongside a
separately retained Postgres row, so the text remains re-derivable by
reference through either path. It cannot live in `nora.obs`
(`00-overview.md` §4), so every hand-rolled `generation()` call site is
responsible for calling it first — `docs/observability.md`'s "Instrumenting a
model call" section is the enforced how-to, "enforced" meaning documented and
followed today, not gated by a test (§2 above).

**Whose words decide whether the text goes at all, separately from whether it
is screened.** As of this read, three call sites reach `generation()`; the
standing fold shares the debrief's underlying client rather than opening a
fourth:

| call site | passes `prompt=`/`completion=`? | why |
|---|---|---|
| `nora/engagement/stages/debrief.py:303,323` (covers both the debrief fold and the standing fold, which calls through the same client) | yes, through `trace_safe` | this runs on a turn Nora was tagged into — the words are already addressed to her |
| `nora/app/composition.py:607` (`_RelevanceGateModelClient`, backing `nora/ingest/relevance_gate.py:636`) | **no** — only the gate's own verdict JSON (`annotate_generation(completion=_verdict_json(verdict))`) | this reads **every** room including ones Nora does not serve; only a digest goes on the wire, the retained transcript re-derives the rest |
| `nora/ingest/comprehension.py:244,360` | **no** — only `model=` on the way out | the description is model prose about somebody's photo; the row plus `nora.media_handle` re-derives it |

A prior session's TRIAGE treated the last two rows as a missing-`trace_safe`
defect. They are not: neither site puts prompt or completion content on the
wire in the first place, so there is nothing for `trace_safe` to screen at
those two call sites. `debrief.py` is the one generation whose text genuinely
reaches the collector, and it already screens — but see §2's closing
paragraph: that it screens today is a fact about what it happens to send, not
a guarantee a test would catch breaking.

## 4. Sentry — `nora/obs/errors.py`

Errors-only by declaration: `traces_sample_rate=0.0` (line 87), because a
second SDK instrumenting spans would write a second, incompatible trace id for
the same turn. `LoggingIntegration(level=WARNING, event_level=ERROR)` turns
the `log.exception` sites (256 as of this read — a live count, not a stamped
figure carried from elsewhere) into issues, with WARNING as the breadcrumb
floor. Auto-enabling integrations are off — not tidiness: with them on,
`sentry_sdk.init` itself raised on a transitive `jinja2` dependency and error
reporting was silently off in the deployment, discovered 2026-09-09
(`docs/observability.md` has the full incident).

**`include_local_variables=False` is passed explicitly** (`errors.py:125`),
alongside `send_default_pii=False`. The two flags are independent — a fact
this codebase got wrong until 2026-09-19, see `02-open-decisions.md` §3 for
the correction and how it was verified.

What Sentry structurally cannot see, by design of the rest of the system: an
R-27 `unavailable`. This codebase is built not to raise, so its
characteristic failure is a counter label, not a traceback — nothing threw
when the media read exported zero traces for two weeks, nothing threw when
`nora-egress.service` was dead for sixteen days. That is why the alerting
stack (§6) exists beside Sentry rather than instead of it. Full detail —
`docs/observability.md`, "What counts as an error".

## 5. Logging — `nora/obs/logging.py`

structlog to JSON-lines stdout, `tag_id` carried via `structlog.contextvars`,
OTel severity-number bands, and a three-field exception split
(`exception.type`/`.message`/`.stacktrace`) alongside the raw blob. Full
detail, including the FileHandler incident and the 78%-noise log census —
`docs/observability.md`, "Logs". Nothing in this bundle adds to that section;
it is not restated here.

## 6. Alerting — `deploy/prometheus/`, `deploy/alertmanager/`,
`deploy/alert-bridge/`

Six rules, every one an absence, read by a bridge container that deliberately
does not go through Nora so the most important alert ("Nora is down") does not
share a failure domain with the thing it watches. Full detail —
`docs/observability.md`, "Alerting". Not restated here.

## 7. Metrics, health, and the config surface — the pointer layer

Named in `00-overview.md` §2's scope boundary as covered by pointer only.

**Metrics** — `nora/obs/metric_specs.py` (120 counters, 14 gauges, 11
histograms) and `nora/obs/metrics.py` (the recorder). Four decisions worth
knowing before touching either: every `Metrics()` owns its own
`CollectorRegistry` rather than a module-level global, enforced by
`tests/architecture/test_no_module_level_mutable_singletons.py`; `get`/
`snapshot` do partial label matching on purpose, so a call site that has not
yet learned a new label still resolves; metric names are append-only —
`DECLARED_NAMES` in `tests/test_metrics_export.py` fails a rename, because a
rename breaks a dashboard or an alert silently; and the split between the two
files is a consequence of this repo's 300-line module cap, not a designed
seam. `tests/test_metrics_export.py`, `tests/test_metrics_stub.py`,
`tests/architecture/test_every_recorded_metric_is_declared.py`.

**Health** — `nora/obs/health.py` serves both `/health` and the `/metrics`
port cited in `00-overview.md` §2's table. Three decisions: a bind-mount
inode canary (`config_staleness()`) that answers "was this container
recreated with fresh config" after a 2026-08-08 incident where a stale
`identity.toml`/`policy.toml` was served twice — a recreate is needed, not a
restart; a pinned top-level `sse` key (a contract an external reader depends
on directly, not just this codebase's own tests) with a per-platform block
added after `/health` reported 200 on Signal alone while Discord was wedged;
and `access_log=None`, the fix for the
53%/108,441-line `aiohttp.access` noise `01`'s logging section (now just a
pointer, §5) used to describe in full. `tests/test_health.py`.

**The config surface** — `config.example.toml`'s `[obs.*]` block, not named
anywhere else in this bundle: `[obs.logging]` (`level`, `format` — `path` was
**removed**, not merely unused, so a leftover line is a loud startup failure
under `extra = "forbid"`, the honest outcome after a `FileHandler` shipped for
five weeks by accident); `[obs.metrics]` (`port = 9100`, doubling as the
health port); `[obs.tracing]` (`enabled`, `exporter = "console" | "otlp_http"
| "file"`, `endpoint`, `headers_env` — `headers_env` names an environment
variable and never inlines the value, DEC-8; `otlp_http` **replaced** the old
`otlp` value outright because nothing installs the gRPC exporter package);
`[obs.errors]` (`enabled = false` by default, `dsn_env`, `environment`). One
open question this config surface raises and does not answer:
`nora/obs/tracing.py`'s own module docstring states "the live `config.toml`
has always been `file`" while `docs/observability.md` states the deployed
endpoint is `http://host.docker.internal:4318/v1/traces` (which implies
`otlp_http`) — this bundle does not resolve which is currently true of the
live deployment and neither claim was re-verified against a running
container for this pass.

**The broader test surface**, for a reader who wants to change any of the
above: `tests/test_tracing.py`, `tests/test_span_skeleton.py`,
`tests/test_logging.py`, `tests/test_tool_logging.py`,
`tests/unit/test_determination.py`, `tests/unit/test_gate_model_client_trace.py`,
`tests/unit/test_tag_trace_{gate,extract,sanitize}.py`,
`tests/unit/test_metric_specs_stages.py`, plus the architecture gates named
throughout this file. There is no single `tests/obs/` directory — the surface
is scattered across `tests/`, `tests/unit/`, and `tests/architecture/`, which
is itself a reason a design record naming it is worth more than leaving a
future reader to rediscover it by grep.
