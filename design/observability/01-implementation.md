# Implementation — what exists, where, and what pins it

Everything in this file is grounded in code read at commit `7e036c12` on
`feat/evaluation-rebuild`. File:line citations are load-bearing — re-check
them before trusting a claim here after a refactor.

## 1. The sampler — `nora/obs/tracing.py:67-132`

`_PlumbingIsNotATrace(Sampler)` reads exactly one attribute,
`nora.trace_kind` (`TraceKind = Literal["generation", "plumbing"]`), and only
on a **root** span — it is composed under `ParentBased`
(`build_sampler() = ParentBased(root=_PlumbingIsNotATrace())`), so a child
never re-decides; it inherits whatever its root was given.

Two properties are load-bearing and easy to get backwards:

- **Default is EXPORT.** An undeclared root, or a value outside the closed
  `TraceKind` set, is kept and logged — never dropped. Getting it wrong
  towards export costs noise (visible, self-complaining); getting it wrong
  towards silence costs an invisible gap in exactly the traces the sampler
  exists to protect. Only an explicit `"plumbing"` drops.
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
`nora_media_comprehension_total{status="ok"}` was moving). The fix keeps them
independent: `attached(None)` avoids holding the ingest trace open regardless
of trace kind; `generation(..., root=True)` — never `plumbing` — because a
describer call is exactly the kind of work R-76 exists to keep.

## 2. The span/generation helper contract — `nora/obs/tracing.py:159-421`

Three functions, one discipline, never load-bearing:

- **`span(name, **attrs)`** — one span, catches `BaseException` (not
  `Exception` — a cancellation must be recorded too), always re-raises, and
  guards every OTel call so a broken tracer degrades to an untraced body
  rather than taking down the turn. `_safe_attributes` coerces per-key so one
  unstringable value costs only itself, not every attribute after it.
- **`generation(name, model=, root=, prompt=, completion=, attributes=)`** —
  the one spelling for "this span is a model call", stamping both
  `gen_ai.operation.name` (OTel GenAI convention) and
  `langfuse.observation.type` (what the collector actually renders a
  generation from) together, always. `prompt`/`completion` are optional and
  **omitted rather than blanked** when absent — an empty string reads as "the
  prompt was empty", which is a different, false claim.
- **`annotate_generation(...)` / `annotate(**attrs)`** — the half only known
  on the way out (completion, finish reason, token counts), applied to
  whatever span is current; a no-op, not an error, on a span that has already
  ended (the tag path's root closes at enqueue time for a QUEUED tag, so
  annotating "the root" from a worker would otherwise write into nothing).

**Enforced, not just documented**: `tests/architecture/
test_generation_spans_use_the_helper.py` derives its forbidden-literal list
from `GENERATION_ATTRIBUTES = ("langfuse.observation.type",
"gen_ai.operation.name")` (`tracing.py:246`) rather than hand-copying it, so no
module outside `nora/obs/tracing.py` may spell a generation by hand. Before
this existed, two call sites had already invented two different dialects —
one carried input/output, the other neither, and no raw client carried token
usage, so every gate/debrief call in the collector was priced at zero. Read
back 2026-09-09: 1041 live traces, four dialects for one fact.

A companion gate, `tests/architecture/
test_raw_model_calls_carry_their_own_machinery.py`, enforces the complementary
rule for the four raw-call paths themselves: each must carry its own bound,
`finish_reason` read, and usage annotation, since nothing wraps them.

## 3. Redaction — `nora/agent/security.py:72` (`trace_safe`)

`trace_safe(text) -> (safe_text, reason)` reuses the same identifier/
coordinate predicates `nora.agent.dream_screen` calls (`scan_identifiers`,
`looks_like_coordinate`) rather than inventing a third screen, and fails
closed at **whole-text granularity** — one flagged coordinate anywhere
withholds the whole prompt, with a `*_sha256` provenance hash surviving on the
row so the text remains re-derivable by reference. It cannot live in
`nora.obs` (§4 of `00-overview.md`), so every hand-rolled `generation()` call
site is responsible for calling it first — `docs/observability.md`'s
"Instrumenting a model call" section is the enforced how-to.

**Whose words decide whether the text goes at all, separately from whether it
is screened.** As of this read:

| call site | passes `prompt=`/`completion=`? | why |
|---|---|---|
| `nora/engagement/stages/debrief.py:303,323` | yes, through `trace_safe` | this runs on a turn Nora was tagged into — the words are already addressed to her |
| `nora/ingest/relevance_gate.py:636` | **no** — only the gate's own verdict JSON (`annotate_generation(completion=_verdict_json(verdict))`, line 607) | this reads **every** room including ones Nora does not serve; only a digest goes on the wire, the retained transcript re-derives the rest |
| `nora/ingest/comprehension.py:244,360` | **no** — only `model=` on the way out | the description is model prose about somebody's photo; the row plus `nora.media_handle` re-derives it |

A prior session's TRIAGE treated the last two rows as a missing-`trace_safe`
defect (`00-overview.md` §4). They are not: neither site puts prompt or
completion content on the wire in the first place, so there is nothing for
`trace_safe` to screen at those two call sites. `debrief.py` is the one
generation whose text genuinely reaches the collector, and it already screens.

## 4. Sentry — `nora/obs/errors.py`

Errors-only by declaration: `traces_sample_rate=0.0` (line ~87), because a
second SDK instrumenting spans would write a second, incompatible trace id for
the same turn — "a trace that exists twice, differently, cannot be
re-derived from either copy." `LoggingIntegration(level=WARNING,
event_level=ERROR)` turns the 223 `log.exception` sites into issues, with
WARNING as the breadcrumb floor so an issue arrives with the warnings that led
to it.

**Auto-enabling integrations are off, not for tidiness.** With them on,
`sentry_sdk.init` itself raised (`ImportError: jinja2 must be installed to use
Jinja2Templates`) and error reporting was silently off in the deployment,
discovered 2026-09-09 — an observability instrument whose own setup can take
itself out is the same failure class R-76 exists to catch one layer up.

**What Sentry structurally cannot see, by design of the rest of the
system**: an R-27 `unavailable`. This codebase is built not to raise (210
docstrings promise it, 45 metric specs declare an `unavailable` state), so its
characteristic failure is a counter label, not a traceback — nothing threw
when the media read exported zero traces for two weeks, nothing threw when
`nora-egress.service` was dead for sixteen days. That is why the alerting
stack (§6) exists beside Sentry rather than instead of it.

`include_local_variables` is **not currently passed** to `sentry_sdk.init` —
see `02-open-decisions.md` §2.

## 5. Logging — `nora/obs/logging.py`

structlog in front of the root logger, rendering JSON lines to **stdout**
(`docker compose logs nora`). Was a `FileHandler` for five weeks until
2026-09-09 — landed one day before the Dockerfile and never revisited — during
which `docker compose logs nora` was empty and the file grew unrotated to 66
MB beside the media/database volumes. `tag_id` rides `structlog.contextvars`
so every line in a turn carries it without threading it through call
signatures.

`severity_number` (OTel Logs Data Model bands: TRACE 1-4 … FATAL 21-24) exists
because `level` sorts alphabetically (ERROR before INFO before WARNING), so "at
least a warning" cannot be expressed as a comparison over the word. An
unmapped level gets no field rather than a guessed one — R-27's three-state
discipline applied to a log line. An exception is split into three named
fields (`exception.type`/`.message`/`.stacktrace`) in addition to the blob, so
"how many `TimeoutError`s since the deploy" is a field match, not a substring
search over multi-line text.

**A log line is not a metric — measured, not asserted.** 78% of 205,099 log
lines over 37 days said nothing had happened (`aiohttp.access` access-log
noise, and a heartbeat restating two counters every minute regardless of
movement). Both are now silenced/demoted at the source rather than filtered
downstream.

## 6. Alerting — `deploy/prometheus/`, `deploy/alertmanager/`,
`deploy/alert-bridge/`

Three stores existed with no reader until 2026-09-09. The bridge is a
**separate container that does not go through Nora**, deliberately — the most
important alert this stack sends ("Nora is down") must not share a failure
domain with the thing it watches. It posts via Discord's REST API (not the
Gateway, not a webhook) using the same `DISCORD_BOT_TOKEN` already in `.env`,
stdlib-only so nothing it depends on can itself be the thing failing during an
incident.

Six rules, every one an **absence** (`TargetDown`, `HeartbeatStalled`,
`PlatformSilent`, `UnavailableOutcomes`, `MediaCustodyNotConfigured`,
`PrometheusNotIngesting`) — the same asymmetry as §4: this system's
characteristic failure is a thing that stops, not a thing that throws, so its
alerting is built to notice absences. `UnavailableOutcomes` is written
generically over every `*_total` carrying the label, so a counter added next
month is covered the day it ships.
