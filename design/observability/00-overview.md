# Observability — four signals, one boundary rule

## 0. The invariant

> **A trace reaches Langfuse when the work under it can contain a model call.
> Everything else is a counter's question, not a trace's.**

That single rule (R-76) is the whole architecture. Nothing below is a separate
design; it is R-76 applied to a sampler, a span helper, a redaction boundary,
and a config surface, plus the two things R-76 does not decide (what content a
generation may carry, and what Sentry sees instead of a trace).

## 1. Why this bundle exists

`docs/observability.md` is the living how-to and is **authoritative** for how
each piece works — read it for the how. It also carries most of the
measurements this bundle cites (the 97/100 figure, the 1041-trace read-back,
the log census), and this bundle does not re-derive a single one of them: it
borrows them, same as `docs/CONVENTIONS.md` and every architecture-test
docstring in this repo already do. Treat every specific number below as a
pointer to that source, not as independently confirmed here — an earlier draft
of this bundle claimed the opposite (that it duplicated nothing from
`docs/observability.md`), which was checked against that file section by
section and was false: entire paragraphs matched near-verbatim. This bundle
now states its actual, narrower job — two things a how-to page does not do:

- record **why** the architecture has the shape it has — the measurements that
  forced each boundary, so a future change can be checked against the evidence
  that produced the current one rather than against taste;
- hold the **open decisions** in one place — the redaction lever
  (`design/13-trace-content-and-redaction.md`, open since 2026-08-06) and the
  small number of not-yet-implemented follow-ups a past session already
  decided but never wired.

Read order: this file, then `01-implementation.md` for what exists and where,
then `02-open-decisions.md` for what is still unresolved.

## 2. The four signals

| signal | store | answers | shape |
|---|---|---|---|
| metrics | Prometheus (`:9100/metrics`) | how many, how often, with what outcome | aggregate, over the deployment |
| traces | Langfuse (+ Jaeger) | what happened in this turn, in what order, at what cost | one instance, re-derivable |
| logs | stdout, JSON lines | what a specific line of code saw | forensic, after the fact |
| errors | Sentry | which stack trace, how often, is it new | grouped, per exception |

The boundary between the first two was not a style choice — it was measured,
and getting it wrong once cost the collector its usefulness: on 2026-08-28,
**97 of the operator's last 100 Langfuse traces were ingest plumbing**, across
twelve rooms Nora does not serve (`docs/observability.md` §"The three
signals"). A collector that is 97% plumbing cannot be read for the turns that
matter. That single number is R-76's entire justification and is why the
sampler in `01-implementation.md` §1 exists at all.

**Scope boundary, stated up front rather than discovered by omission.** This
bundle's depth is uneven across the four rows: traces and errors (§3 below,
`01-implementation.md` §§1-4) are covered in the detail the title implies.
Logs and metrics are not — `nora/obs/metric_specs.py` alone is 120 declared
counters, 14 gauges and 11 histograms, and `nora/obs/health.py` (which serves
the `/metrics` endpoint this table cites, and the `/health` endpoint
`01-implementation.md` §6's alerting rules fire on) is not mentioned anywhere
in this bundle before `01-implementation.md` §7. That section records the
handful of decisions behind those two modules; it is a pointer layer, not a
design record at the depth this file gives tracing. Read it as scoped, not as
complete.

## 3. Two tracing paths, one reason they're separate

Nora instruments model calls two different ways, and the split is not
accidental:

1. **The tag turn runs through the Strands Agent SDK**, which instruments
   itself — agent span, tool spans, the model call — using whatever
   `TracerProvider` is globally registered when it runs. Nora never calls
   `strands.telemetry.StrandsTelemetry()`; it registers the provider itself in
   `setup_tracing()` (`nora/obs/tracing.py:135`, called from
   `nora/__main__.py:77` before any Agent is built). This works not because of
   call order as such, but because `trace.set_tracer_provider` is one-shot
   (`opentelemetry.trace`: a second call is silently discarded with a
   warning) and OTel's own proxy resolves the tracer lazily — the same rule
   `tests/conftest.py` states for test providers. `StrandsTelemetry()` would
   additionally install its own propagator composite, which Nora does not use
   and has no current need for. One path (`nora/subject/obtain.py:349`, the
   eval harness) calls `build_dependencies` directly and never calls
   `setup_tracing` at all — that Agent runs untraced, which is a known,
   accepted gap for that entry point, not a defect in this argument. This is
   recorded here explicitly so a future session does not reopen "adopt
   StrandsTelemetry" as an unmet obligation.
2. **Two raw `chat.completions.create` paths bypass the Agent SDK
   deliberately** — the relevance gate (`nora/app/composition.py:503`) and the
   vision read (`nora/infra/vision/client.py:344,391`) — because a fail-closed
   gate and a vision describer must not depend on the SDK's own agent-loop
   machinery. The debrief fold and the standing fold moved onto the SDK
   transport in `4448c737` (`composition.py:397` records the move) and now
   call `ask_text(...)`, not a raw client. All four, though, still instrument
   by hand through `nora.obs.tracing`'s `span`/`generation`/`annotate`/
   `annotate_generation` helpers (`01-implementation.md` §2) rather than
   relying on the SDK's own spans — the SDK's instrumentation covers the Agent
   loop, not a raw client or a debrief call made outside it. Before
   2026-08-27 none of the four opened any span at all; a turn that failed
   before reaching the model was indistinguishable from a turn that never
   happened.

Both paths converge on the same sampler and the same collector, and both are
subject to the same redaction obligation (§4) — but the SDK path's redaction
lever lives inside Strands (`design/13`, an OTel-native opt-in), while the
hand-rolled path's lever is a plain function call the caller must remember to
make. They are not unified because they cannot be: one is inside a dependency,
the other is this codebase's own code.

## 4. The layering constraint that shapes the redaction boundary

`nora.obs` sits **below** `nora.agent` in the layered-import contract
(`pyproject.toml`). `nora.agent.security.trace_safe` — the identifier/
coordinate screen every hand-rolled generation's `prompt`/`completion` must
pass through first — therefore cannot live beside the span helper that needs
it; `generation()` (`nora/obs/tracing.py:250`) takes already-screened text and
documents that it cannot enforce the screening itself.

This was investigated as a candidate defect in a prior session (a TRIAGE run
that treated it as a design gap requiring `nora.obs` to move above `nora.agent`
so the redactor could be an injected port). That investigation is corrected
here, in both directions:

- **Importing `nora.agent.security` directly from `nora.obs` would violate the
  layered-import contract** (`pyproject.toml:219-238`, enforced by `uv run
  lint-imports`) — `trace_safe` needs `scan_identifiers` from
  `nora/agent/egress.py`, which imports `nora.doctrine.loader`, which imports
  `nora.obs.metrics`, closing `nora.obs → nora.agent → nora.doctrine →
  nora.obs`. That is a real, checked contract violation. It is **not**,
  however, "a real Python import cycle" in the sense of an unavoidable
  `ImportError` — both `nora/obs/__init__.py` and `nora/agent/__init__.py` are
  empty, and nothing today actually imports along that chain at runtime. A
  prior draft of this bundle called it an import cycle, which overstates the
  blocker; corrected here.
- **An injected port would sidestep the contract violation entirely, and is
  not a pattern this codebase lacks** — `nora/obs/tracing.py` could declare a
  `Protocol` for a screener and take it as a parameter, the same structural
  pattern already used for `StandingReader`
  (`nora/ingest/relevance_gate.py:185`) and `_PlaceSource`
  (`nora/agent/security.py`). No new import edge is required for a `Protocol`.
  **This was not adopted, and whether it should be is recorded as an open
  question rather than a closed one** — see `02-open-decisions.md` §2: today's
  design puts the screening obligation on the *caller* (§3 below), which costs
  nothing extra for the three call sites that never send real content, but is
  enforced only by convention, not by any test.

The two raw ingest call sites this investigation started from
(`nora/ingest/relevance_gate.py`, `nora/ingest/comprehension.py`) genuinely
turned out not to need `trace_safe` — neither passes prompt or completion
content to a span at all, so there was nothing there to screen
(`01-implementation.md` §3 has the call-by-call table). That narrow finding
holds. What did not hold was generalizing from it to "the layering is settled
and this is not a defect" — the layering choice and the redaction-enforcement
gap are two separate questions, and only the first is actually closed.

## 5. Where the rest of the design lives (pointers, not copies)

- **How to instrument a call, how a span reaches a collector, what counts as a
  generation, how to read a trace back, known gaps** — `docs/observability.md`.
- **The open redaction decision** — `design/13-trace-content-and-redaction.md`,
  `status: open-decision` since 2026-08-06.
- **The war-stories behind each rule** (R-76, R-27, the file-vs-stdout log
  incident, the OTLP path 404) — `docs/CONVENTIONS.md`.
- **The terse enforceable rules** (which module may hold `gen_ai.*` literals,
  what a raw model call must carry) — `AGENTS.md`.
- **Metrics, health, and the config surface** — not covered above; a short
  pointer layer at `01-implementation.md` §7, per the scope boundary in §2.
