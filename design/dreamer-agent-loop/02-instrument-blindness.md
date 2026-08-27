---
type: analysis
title: "Instrument Blindness — Six Layers That Cannot Measure Themselves"
description: "Independently, at six layers — one of them in another tier entirely — an instrument cannot observe the thing it exists to observe. This is one missing discipline, not six bugs, and it is why these failures stayed invisible for weeks."
tags: [nora, dreamer, observability, metrics, evidence, r-27]
timestamp: '2026-08-22'
edges:
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

# Instrument Blindness — Six Layers That Cannot Measure Themselves

CLAUDE.md carries this rule:

> **Never read an instrument's output as evidence without first checking it can
> measure the thing** — state what a positive result would look like before
> concluding from a null one.

It is violated at six independent layers. They were found by six separate
investigations that were not looking for the same thing — and Layer 6 is not in
the Dreamer at all, which is what settles this as a repo-wide discipline gap
rather than one subsystem's bug.

The common cause is structural: **a background loop has nobody waiting on it.**
On the tag path a broken instrument is discovered within one turn, because a
human is holding the phone. Here, a null reading and a broken gauge are the
same observation, and nothing forces the question.

One layer below is in the note tier, not the Dreamer. That is what settles this
as a repo-wide discipline gap rather than one subsystem's bug.

## Layer 1 — The stop-reason counter is blind to both caps that bind

`nora_dream_stop_reason_total` exists to answer *"did a budget cap bind?"*. It
is incremented **only on the success path**, after the try block returns.

- The timeout branch constructs its outcome with `stop_reason="timeout"` and
  hands it to the finaliser — which does not touch that series. So
  `reason="timeout"`, a value the metric's own help text advertises, **can
  never appear**.
- `MaxTokensReachedException` leaves through the generic exception branch with
  `stop_reason=None`.

Both of the caps that actually bounded this subsystem in production are
invisible to the one counter designed to see them. All thirteen failures appear
only as `nora_dream_run_total{status="unavailable"}` — indistinguishable from a
provider 429, a prose reply, or a network fault.

## Layer 2 — The unmigrated tiers emit no trace and log nothing on success

The legacy model client has no OpenTelemetry instrumentation, so the language
and exchange tiers produce **no Langfuse trace at all**. The consolidator path
logs nothing on success. There is no per-cycle counter.

> For the entire language/exchange tier, **"ran and found nothing" is
> indistinguishable from "never ran."**

This is exactly the `unavailable`-reported-as-`nothing_found` substitution R-27
exists to forbid, promoted from a tool result to a whole subsystem. It is also
why §6 of [01-live-state.md](01-live-state.md) cannot answer whether two rows
from 963 messages is correct conservatism or a starved tier.

The migrated fact adaptors *do* trace. The asymmetry means the tier we can see
is the one that has produced nothing, and the tier that produced the wrong rows
is the one we cannot see.

## Layer 3 — Backoff and skip decisions leave no evidence

Neither the exponential backoff nor the in-flight skip emits a log line.
`grep -ic backoff` over the whole 88,678-line log returns **zero**. Their only
signal is a Prometheus counter.

Those counters are **in-process**, and the container was recreated. The numbers
that would have quantified the incident are gone and are not recoverable. The
tick-by-tick reconstruction in [01-live-state.md](01-live-state.md) had to be
rebuilt by joining log timestamps against message arrival times in the
database, because the instrument that was supposed to record it had already
been reset.

## Layer 4 — The wrong-gloss guard cannot falsify a wrong gloss

The language renderer refuses to render a lexeme with no quotable example, and
its stated reason is that *"the verbatim quote is the only thing that makes a
wrong gloss visible."*

Both live rows satisfy it. Both quotes contain 「big walk」. Neither quote
falsifies either gloss, because the term appearing in a message is not evidence
about what the term means. The guard measures *presence of a quote*, and the
property it is asserted to protect is *refutability*. Those are different
properties, and the gap is invisible from the code.

## Layer 5 — The egress screen cannot see the content class it is asked to guard

`dream_screen` walks every string in the model's envelope and tests four
identifier patterns plus a coordinate pair. That is its entire alphabet.

It is cited — including in an earlier draft of this bundle — as the reason a
background loop may safely read a room. It cannot see names, nicknames, quoted
messages, memory conclusions, roster composition, membership counts, or
relationship claims. It also never sees tool *arguments* or tool *results*,
only the model's final restatement.

Full argument in [11-disclosure.md](11-disclosure.md). It is listed here
because the failure mode is identical to the other five: a real instrument,
correctly implemented, measuring a narrower property than the one it is
credited with.

## Layer 6 — A tier declares ten metrics and ships none of them

> **RESOLVED 2026-08-27, and not by building ten.** The tier ships **three**;
> four were already counted by `nora_tool_calls_total` and a second series would
> double any dashboard summing tool volume; three more measure an adaptor that
> does not exist, and declaring those would have manufactured a fresh instance of
> **Layer 7 below** — a name with no call site. See
> [27](27-note-dreamer.md) §6.1. The layer's finding stands; the fix was to
> decide which ten were real, not to build ten.

Not the Dreamer. The **note tier**, found by an independent investigation
looking for something else.

Its design declares ten counters by name — candidates by trigger, writes by
kind, reads and searches by result, index size, curator and revisit outcomes,
refusals by reason. **Not one of them is declared in the metrics module**, so
none can ever be incremented. Note tool calls fall through to the generic
per-tool counter.

What that costs is written in the tier's own spec, which describes the
diagnostic it believes it has:

> *"a climbing `candidates_total` with a flat `writes_total` → she recognises
> and does not act. **That is the inert-skill failure, made visible.**"*

It is not visible. The single most important failure mode the tier names for
itself is the one it cannot observe — and the tier is presently sitting at zero
notes with one miss-cache row, which is exactly the state that diagnostic exists
to disambiguate.

It also blocks something concrete: the note tier's re-derivation sweep is
deferred **behind an evidence gate naming two of these ten metrics**. The gate
cannot be satisfied, because the instruments it requires do not exist.


## Layer 7 — The trace is the bundle's provenance claim, and Nora emits no spans

**Found 2026-08-27, by grep, and it is the widest layer here.**

This bundle's spine says the Langfuse trace is *"the provenance every claim about
a turn re-derives from."* Four nodes rest on it. The reality:

```
start_span / start_as_current_span sites in nora/ :  0
```

**Nora creates no spans anywhere.** `nora/obs/tracing.py` builds a real
`TracerProvider` and an OTLP exporter to Langfuse, and then nothing in the
application ever opens a span. Every span that reaches Langfuse is emitted by
**Strands**, from inside the agent loop, because `factory.py` hands it
`trace_attributes`.

**So the trace covers the model call and nothing around it.** Dark today:

| dark | consequence |
|---|---|
| ingest → normalize → policy → admission | a message refused before the agent exists leaves **no trace at all** |
| prefill assembly | which blocks were built, from which reads, at what size — the thing every disclosure and budget argument reasons about |
| store reads on the turn's critical path | the unbounded reads [26](26-standing.md) §5 names as the class defect are invisible |
| delivery | the strict-silence boundary — the one authored surface — is untraced |
| the debrief stage | a stage whose whole risk is holding a slot |
| the Dreamer's loop around its adaptors | the loop this bundle exists to fix |

**The asymmetry is the finding.** A turn that *reaches the model* is
well-traced. A turn that fails *before* it — refused at admission, starved at
prefill, blocked at a store read — produces **nothing**, which is
indistinguishable from a turn that never happened. That is the exact shape of
Layers 1–6, one level up: the instrument covers the healthy path and goes silent
on the failing one.

**And it undercuts a load-bearing claim rather than a nice-to-have.** "No
self-attestation — re-derivable, so no actor is trusted" is the third of the
three rules the spine says make it hold. Re-derivation currently stops at the
model call.

**What this does NOT need.** Not a span per function. The gap is that the
turn has no *skeleton*: one span for the turn, children for the named stages the
architecture already has — admit, comprehend, prefill, invoke, deliver, debrief —
so a turn that ends early ends *visibly*, at a named stage. The stage boundaries
already exist in code; nothing is traced against them.

**Why it was invisible.** `[obs.tracing]` is configured, the exporter is real,
and traces *do* arrive — so every check short of grepping for span sites reports
a working tracing setup. **A partially-instrumented system reads exactly like an
instrumented one from the outside**, which is Layer 5's argument applied to the
tracer itself.

## What follows from this

**Every fix in this bundle ships its own measurement, or it is not done**
(D7 in [00-overview.md](00-overview.md)).

Concretely, that means a change is incomplete until someone can state what a
positive reading looks like *and* produce it:

- A cap that binds must be distinguishable from a provider failure.
- A loop that ran and found nothing must be distinguishable from one that never
  fired — for every adaptor, including the migrated ones.
- A backoff engagement and an in-flight skip must survive a restart, which
  means a log line, not only a counter.
- A guard must be tested against the property it is *claimed* to protect, not
  the property it is *easy* to assert. Layer 4 is the cautionary case: a test
  that a quote is present would have passed on both wrong rows.

This is the negative half of the repo's own `measure` verb, applied to the
subsystems that most need it and currently have it least. Layer 6 is the reason
to state it as a repo-wide discipline rather than a Dreamer fix: a tier written
by a different session, at a different time, reproduced it exactly.

## Status / Checkpoint

2026-08-27 — **Layer 7 added: Nora emits ZERO spans.** All tracing comes from
Strands' agent-loop instrumentation, so the trace covers the model call and
nothing around it — and a turn that fails before the model produces nothing at
all. The bundle's provenance claim currently holds only for the half of the turn
that already worked.

2026-08-27 — **a TENTH instance, and a named class rather than an anecdote:
a copy of an operator-owned value, with no parity guard, is a fork with a
delayed fuse.**

Four of these were found in one afternoon, and the shape is identical every
time. A value the operator owns — a trigger phrase, a token budget, a timeout —
gets *restated* somewhere: a test fixture, a corpus, a design paragraph. The
restatement stays plausible after the original moves, so **nothing fails at the
moment of divergence.** When it finally fails it fails somewhere else and blames
something else.

| the copy | how it failed |
|---|---|
| the near-miss eval corpus hardcoded `nora.000` | the operator re-enabled the plain `nora` trigger; two cases stopped being near-misses and became real TAGS. The corpus swore the model would not be invoked, it was, and the gate blamed **the model** |
| four `tests/test_config.py` assertions on that same string | three were `str.replace` calls that had **silently become no-ops** — the tests loaded a perfectly valid config while asserting it fails to load. Invert the assertion and they pass while measuring nothing |
| `config.example.toml` behind the deployment on three keys | caught, because this one **has** a parity guard |
| [26](26-standing.md) §5 reasoning about `[llm] timeout_seconds = 60` | the value does not exist; the field is required and set to 120. **A design document is the same bug in prose** — and the only one here with no possible automated guard |

**The two with guards were found; the two without rotted for weeks.** That is
the whole finding, and it is the instrument-blindness thesis applied to
configuration rather than to metrics: an unguarded copy is not "slightly stale",
it is a second source of truth that nothing is watching.

**The rule.** A test, corpus or design node that needs an operator-owned value
**reads it** — from the config under test, from the rendered corpus, from the
loaded object. Where a value is deliberately pinned as a tripwire (this repo
pins the token budget on purpose, so a change forces review), the pin is only
safe **beside a parity guard** that fails when the two diverge. A pin without a
guard is the fork.

**And a stale copy can hide a second one.** Raising the synced budget exposed
`_OVER_BUDGET = 130_000`, a constant whose only job was to sit *past* the
budget and which now sat below it — so the scenario designed to blow the budget
would have quietly stopped blowing it, with two tests still green. Its own
comment named the budget from **two generations earlier**.

2026-08-27 — **a ninth instance, and it is this bundle's own authors.** The
Graphiti investigation reversed its own conclusion four times, and every reversal
had one cause: **a signature was read and a conclusion stated before the thing
was run.** Twice the error was identical — reading a plural orchestrator that
queries the graph and concluding the singular unit beside it was coupled too.
The corrective is in [25](25-knowledge-pipeline.md) §2.1: probes replaced the
driver, embedder and cross-encoder with stubs that **raise**, so "it never
touches a database" became a claim that fails loudly instead of an inference from
a type. **But running once was not enough either** — the predicate-collapse
result was a single sample of a stochastic LLM call reported as a table; re-run
five times it reproduced 3/5 and once false-merged the control. CLAUDE.md already
carried both rules; the node diagnosing instrument blindness broke them while
investigating a library.

2026-08-24 — Six layers when written. **Now eight**: `nora_fact_entity_unresolved_total` has no call site ([21](21-substrate-contract.md) §6.4), and the note tier declares ten metrics and ships zero ([27](27-note-dreamer.md) §6). The pattern kept finding new instances as later nodes were written, which is the finding.
