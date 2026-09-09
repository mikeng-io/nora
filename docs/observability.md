# Observability

**How Nora is instrumented, and where to look when something is missing.**

`AGENTS.md` carries the terse rules and `docs/CONVENTIONS.md` the war-stories
behind them; neither says how the pieces fit together, which is the gap this
file exists to close. It is the map — what the three signals are for, how a
span physically reaches a collector, what makes a span a *generation*, and how
to read any of it back. Rules are stated here only where the how-to needs them;
their reasoning stays in `CONVENTIONS.md` and is linked, never copied, because
two copies drift and the one that drifts is the one you did not read
(`docs/adr/0010-agents-md-is-the-entry-point.md`).

- [The three signals, and the boundary between them](#the-three-signals)
- [How a span reaches a collector](#how-a-span-reaches-a-collector)
- [Instrumenting a model call](#instrumenting-a-model-call)
- [What counts as a generation](#what-counts-as-a-generation)
- [Reading it back](#reading-it-back)
- [When a span is missing: the five places it can die](#when-a-span-is-missing)
- [Logs](#logs)
- [Known gaps](#known-gaps)

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
| **logs** | `data/nora.log`, JSON lines | what a specific line of code saw | *forensic*, after the fact |
| **errors** | Sentry | which stack trace, how often, and is it new | *grouped*, per exception |

**The rule that keeps them apart:** Langfuse is an LLM-observability tool, so a
trace reaches it when the work under it *can contain a model call*. Everything
else Nora does — sequencing ingest stages, writing a row, resolving an identity,
counting a refusal — is plumbing, and plumbing's questions are Prometheus'.
That is R-76; `CONVENTIONS.md` has the argument and the numbers.

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
  nora process                     otel-collector (norty)              stores
  ────────────                     ──────────────────────              ──────
  span(...)                                                        ┌─▶ Langfuse
      │                                                            │   :3100
      ▼                                                            │
  ① build_sampler()  ──▶ OTLP/HTTP ──▶ ② filter/langfuse ──────────┘
    ParentBased(          :4318/          drops service.name != "nora"
      _PlumbingIs         v1/traces
      NotATrace)                     ──▶ ② filter/jaeger  ──────────┬─▶ Jaeger
                                          drops spans that HAVE     │   :16686
                                          gen_ai.operation.name     │
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

**② The collector filters** (`workspace/otel-collector/config.yaml` on norty)
are a second stage that this repo does not own and cannot see:

* the Langfuse pipeline keeps only `service.name == "nora"`;
* the Jaeger pipeline drops every span that **has** `gen_ai.operation.name` —
  i.e. the fan-out is deliberate and complementary. **Generations go to
  Langfuse; infrastructure spans go to Jaeger.** A span is not in both.

That second filter is why "it is not in Jaeger" is *not* evidence a generation
was dropped, and why routing a span through `generation()` removes it from
Jaeger by design.

The deployed endpoint is `http://host.docker.internal:4318/v1/traces`. The
`/v1/traces` path is **required**: `OTLPSpanExporter` appends it only when the
endpoint comes from an environment variable, and a literal `endpoint=` is used
verbatim — without the path it POSTs to the collector root and 404s (seen live
2026-09-04, 31 export failures). The collector's own exporter is the opposite
and auto-appends, which is why Langfuse there is configured with
`traces_endpoint`. Two SDKs, two conventions, one afternoon lost.

---

## Instrumenting a model call

**Use `nora.obs.tracing.generation`. Never write the attributes by hand.**

```python
from nora.agent.security import trace_safe
from nora.obs.tracing import annotate_generation, generation

body, withheld = trace_safe(prompt)          # a trace is an egress — see below
with generation(
    "engagement.debrief.fold",
    model=client.model,
    prompt=body,
    attributes={"nora.tag_id": tag_id, "nora.debrief.prompt_withheld": withheld},
):
    reply = await client.complete(prompt=prompt)
    annotate_generation(
        completion=trace_safe(reply.content)[0],
        finish_reason=reply.finish_reason,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
```

`tests/architecture/test_generation_spans_use_the_helper.py` enforces this: no
module outside `nora/obs/tracing.py` may write a `gen_ai.*` or
`langfuse.observation.*` literal, and it derives that forbidden vocabulary from
`GENERATION_ATTRIBUTES` rather than repeating it, so a new key added to the
helper is guarded the moment it is added.

**Why a helper at all.** The spelling was hand-rolled at two call sites and
absent from every other, and by the time there were two they had already
diverged: one carried input and output, the other neither, and *no* raw client
carried token usage — so every gate and debrief call in the collector was priced
at zero. Read back 2026-09-09: 1041 live traces, four dialects for one fact.

### Four rules that are not obvious

**1. `root=True` only for a span that genuinely opens its own trace.** It is
what puts `nora.trace_kind` on the span. On a child it is inert (see above).
Pass it for work reached off a path that is itself plumbing, or for spawned work
that has detached with `attached(None)`.

**2. Spawned work does not inherit what you think.** `asyncio.create_task`
**copies** the caller's contextvars, so a span opened inside a spawned job is a
*child* of whatever was current at `create_task` time — often a completely
unrelated turn. `attached(None)` detaches; `capture_context()` /
`attached(carrier)` re-attaches deliberately.

**Detaching and dropping are two decisions.** Conflating them is what hid the
media read for two weeks: `comprehend` correctly detached from the ingest trace
*and* declared `plumbing`, so it never reached any collector while its counter
happily recorded reads. Detach to avoid holding a trace open; declare
`generation` because there is a model call under it. Both, independently.

**3. A trace is an egress.** The collector is a third party over the network, so
prompt and completion text must pass `nora.agent.security.trace_safe` first —
the same identifier and coordinate floor that guards every other exit. It fails
closed at whole-text granularity and names the pattern that fired, because "the
trace shows nothing" and "the trace refused to show it" are different readings
and only one is a bug.

It cannot live beside the span helper: **`nora.obs` sits BELOW `nora.agent` in
the import contract** (`pyproject.toml`, "layered architecture"), so
`generation` cannot reach the egress predicates and does not pretend to. Its
`prompt`/`completion` parameters take *already screened* text.

**4. Not every model call may carry its text.** Screening decides whether text
*can* go; whose words they are decides whether it *should*. The debrief puts its
prompt on the span because it runs on a turn Nora was tagged in. The relevance
gate does not, and that is deliberate rather than an omission — it reads
**every** room, including rooms Nora does not serve, so only a digest goes on
the wire and the retained transcript re-derives the rest. The media
comprehension does the same for a description of somebody's photo.

---

## What counts as a generation

**A call to a language model.** Not everything that runs a model:

| call | generation? | why |
|---|---|---|
| the tag turn (Strands) | yes | instrumented by the SDK itself |
| the relevance gate, the debrief, the standing fold | yes | raw `chat.completions.create`, instrumented by hand through the helper |
| the media comprehension (`comprehend`) | yes | a describer or a summarizer under every branch |
| SenseVoice STT | **no** | local ASR on Nora's own private network, not an LLM |

The STT line is the one that needs saying out loud, because its absence looks
like an oversight. Its outcome ships on `nora_speech_transcriptions_total` with
R-27's three states — the Prometheus question it actually is — and the words it
produces *do* reach a trace, through the summary pass over the transcript, which
runs under `comprehend`. Putting local ASR in an LLM-observability tool is a
small version of the 97%-plumbing mistake.

**A raw model call owes more than a span.** Four paths bypass the agent SDK
deliberately, and what such a client must carry itself — a bound, a
`finish_reason` read, a usage annotation — is enforced separately by
`tests/architecture/test_raw_model_calls_carry_their_own_machinery.py`. That
gate and the dialect gate above are complementary: one is about what the *client*
does, the other about how the *span* is spelled.

---

## Reading it back

No claim about tracing is finished until the trace has been read back. Both
stores are reachable **directly from a dev machine on the LAN** — no tunnel.

**Langfuse.** Credentials are the base64 blob inside `LANGFUSE_OTLP_HEADERS` in
`/home/norty/workspace/nora/.env`; decode it to `pk-lf-…:sk-lf-…` and use it as
HTTP Basic auth. Other `workspace/*/.env` files hold keys for *different*
Langfuse projects and will 401 against Nora's.

```bash
curl -s -u "$PK:$SK" "http://192.168.1.10:3100/api/public/traces?limit=50&page=1" -o traces.json
curl -s -u "$PK:$SK" "http://192.168.1.10:3100/api/public/traces/<full-32-char-id>" -o one.json
```

Write to a file and parse the file: with `rtk` on the path, piping `curl` into a
parser gets you a *schema summary* instead of JSON.

**Jaeger.** `http://192.168.1.10:16686/api/services`, then
`/api/services/nora/operations` — the fastest way to answer "does this span name
reach a collector at all", remembering that Jaeger holds only the spans that are
*not* generations.

**Prometheus.** `ssh norty 'curl -s localhost:9100/metrics | grep ^nora_'`.
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
lines** to `[obs.logging] path` (`data/nora.log` — a `FileHandler`, nothing on
stdout, which is why `docker compose logs nora` is empty), with secret
redaction (R-16) and `tag_id` bound through
`structlog.contextvars` so every line emitted during a turn carries it without
being threaded through call signatures.

Every call site uses plain stdlib `logging.getLogger`; there is no
`structlog.get_logger()` anywhere. Third-party libraries' records ride the same
`foreign_pre_chain` into the same JSON stream.

**Container logs are not on the docker log driver** — `docker compose logs nora`
is empty. The file is `data/nora.log`.

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

Three stores and no reader was the state until 2026-09-09. `deploy/prometheus/`
and `deploy/alertmanager/` are the reader; `deploy/alert-bridge/` is how an
alert reaches a person.

```
nora:9100  ─┐
presence:  ─┼─▶ Prometheus ──▶ Alertmanager ──▶ alert-bridge ──▶ Discord
  9101      │   (scrape 30s,     (group,          (webhook →      review
analytics: ─┘    rules)           throttle)        REST POST)     channel
  9103
```

**It lands in the same Discord channel the participation mirror posts to**, so
an alert arrives beside the determinations it is about rather than in a second
place to remember.

**The bridge is a separate container and does not go through Nora.** Nora
already holds a Discord connection, so an `/alerts` endpoint on its aiohttp app
would have been three lines — and it would have made the most important alert
this stack can send, "Nora is down", depend on Nora being up. An alerting path
that shares a failure domain with the thing it watches is decoration. It is
stdlib only for the same reason: every dependency it does not have is one that
cannot fail during an incident.

**Discord's REST API, not the Gateway and not a webhook.** The Gateway is a
persistent connection with a reconnect state machine — a lot of moving parts
for one POST an hour at most. A webhook would work and would avoid handling a
token, but it must be created by hand in the Discord UI and kept as a second
credential, where `DISCORD_BOT_TOKEN` is already in `.env`. Alertmanager's own
`discord_configs` receiver is the same trade and posts a fixed template we
would then work around. The REST call does not care whether Nora's Gateway
session is alive, which is the whole point of this container being separate.

**Every rule is an ABSENCE**, which is the whole reason this exists alongside
Sentry rather than instead of it — see "what counts as an error" below.

| alert | fires on | the incident behind it |
|---|---|---|
| `TargetDown` | `up == 0` for 3m | `nora-egress.service` sat `failed` for sixteen days |
| `HeartbeatStalled` | tick age > 300s | a stalled loop in a process that still answers HTTP |
| `PlatformSilent` | adapter event age > 900s | the one condition the old shell watchdog checked — on no timer, into no channel |
| `UnavailableOutcomes` | any `*_total{status="unavailable"}` increasing | R-27's third state — the thing this codebase produces *instead of* raising |
| `MediaCustodyNotConfigured` | attachments arriving with custody off | the silent degrade `media.py` counts rather than raises |
| `PrometheusNotIngesting` | it stops scraping itself | a monitoring stack's own failure mode is to go quiet and leave everything green |

`UnavailableOutcomes` is written generically over every `*_total` carrying the
label, so a counter added next month is covered the day it ships — the same
derive-don't-hand-copy rule AGENTS.md holds for label vocabularies.

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

- **No release is attached to a Sentry issue.** `scripts/build-image.sh` builds
  UNSTAMPED on purpose ("deployments are not releases"), so the container has
  neither `NORA_VERSION` nor `version.json` and "is this new since the last
  deploy" is not answerable from an issue.
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
