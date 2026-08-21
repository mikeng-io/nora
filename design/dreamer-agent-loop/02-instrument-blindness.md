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

Full argument in [08-disclosure.md](08-disclosure.md). It is listed here
because the failure mode is identical to the other four: a real instrument,
correctly implemented, measuring a narrower property than the one it is
credited with.

## Layer 6 — A tier declares ten metrics and ships none of them

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
