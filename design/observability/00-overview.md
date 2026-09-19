# Observability — four signals, one boundary rule

## 0. The invariant

> **A trace reaches Langfuse when the work under it can contain a model call.
> Everything else is a counter's question, not a trace's.**

That single rule (R-76) is the whole architecture. Nothing below is a separate
design; it is R-76 applied to a sampler, a span helper, a redaction boundary,
and a config surface, plus the two things R-76 does not decide (what content a
generation may carry, and what Sentry sees instead of a trace).

## 1. Why this bundle exists

`docs/observability.md` is the living how-to — read it for the how. This
bundle exists because that file is deliberately not a design record: it states
current behavior and links its reasoning to `docs/CONVENTIONS.md` rather than
repeating it, on the same "two copies drift" principle every doc in this repo
follows. Nothing here duplicates it. This bundle instead does the two things a
how-to page cannot:

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
rooms Nora does not serve, in a system whose highest-volume entry point
(`nora/ingest/pipeline.py`) opens six spans with no model call under any of
them. A collector that is 97% plumbing cannot be read for the turns that
matter. That single number is R-76's entire justification and is why the
sampler in `01-implementation.md` §1 exists at all.

## 3. Two tracing paths, one reason they're separate

Nora instruments model calls two different ways, and the split is not
accidental:

1. **The tag turn runs through the Strands Agent SDK**, which instruments
   itself — agent span, tool spans, the model call — using whatever
   `TracerProvider` is globally registered when it runs. Nora never calls
   `strands.telemetry.StrandsTelemetry()`; it registers the provider itself in
   `setup_tracing()` (`nora/obs/tracing.py:135`) before the Agent is
   constructed, which is sufficient — the wrapper class exists to do the same
   registration for a caller who has not already done it, and calling both
   would either double-register or race depending on call order. This is
   recorded here explicitly so a future session does not reopen "adopt
   StrandsTelemetry" as an unmet obligation; the outcome it produces
   (a correctly registered global provider before the Agent runs) already
   exists, reached a different way.
2. **Four raw `chat.completions.create` paths bypass the Agent SDK
   deliberately** — the relevance gate, the debrief fold, the standing fold,
   media comprehension — and instrument themselves by hand through
   `nora.obs.tracing`'s `span`/`generation`/`annotate`/`annotate_generation`
   helpers, because nothing wraps them otherwise. Before 2026-08-27 these
   opened **zero** spans; a turn that failed before reaching the model was
   indistinguishable from a turn that never happened.

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
it; `generation()` (`nora/obs/tracing.py:249`) takes already-screened text and
documents that it cannot enforce the screening itself.

This was investigated as a candidate defect in a prior session (a TRIAGE run
that treated it as a design gap requiring `nora.obs` to move above `nora.agent`
so the redactor could be an injected port) and the investigation's own finding
overturned that premise: moving the redactor into `nora.obs` would create a
real Python import cycle, and the actual gap it was standing in for was three
lines — two raw-call sites (`nora/ingest/relevance_gate.py`,
`nora/ingest/comprehension.py`) that, on inspection, do not pass raw content to
a span at all (§ below, and `01-implementation.md` §3). The layering is
therefore load-bearing as designed, not a defect — recorded here so it is not
re-litigated from the same false premise.

## 5. Where the rest of the design lives (pointers, not copies)

- **How to instrument a call, how a span reaches a collector, what counts as a
  generation, how to read a trace back, known gaps** — `docs/observability.md`.
- **The open redaction decision** — `design/13-trace-content-and-redaction.md`,
  `status: open-decision` since 2026-08-06.
- **The war-stories behind each rule** (R-76, R-27, the file-vs-stdout log
  incident, the OTLP path 404) — `docs/CONVENTIONS.md`.
- **The terse enforceable rules** (which module may hold `gen_ai.*` literals,
  what a raw model call must carry) — `AGENTS.md`.
