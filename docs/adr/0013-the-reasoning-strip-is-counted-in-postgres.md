# 0013 — A dropped reasoning is a row in Postgres, not a Prometheus counter

- **Status:** accepted
- **Date:** 2026-09-17
- **Index:** none — this changes no rule about what Nora may do and no
  behaviour at all. It adds one observation. The properties are pinned by named
  tests instead: `tests/unit/test_strands_reasoning_transport_contract.py` (the
  third-party behaviour the detector is grounded in),
  `tests/unit/test_reasoning_strip_probe.py` (the counter moves when the condition
  occurs, and does not when it does not),
  `tests/unit/test_reasoning_strip_watch.py` (a retry is not a second
  opportunity), `tests/infra/test_reasoning_strip_store.py` (the row carries
  its denominator, an absent tag is stored absent, an impossible row is
  refused). Issue #325; the same transport limitation on a different caller is
  #186 and ADR 0011.

## Context

OmniRoute's Chat Completions transport cannot carry `reasoningContent` in a
multi-turn position. Strands' `OpenAIModel._format_regular_messages` drops
every such content block out of the request, logs one WARNING from
`strands.models.openai`, and continues.

ADR 0011 measured what that costs on the **relevance gate**, where the SDK's
`structured_output` produces the multi-turn shape by construction: safety 6/6
held on a raw `json_object` completion and **1/6 — breached** through the SDK
path, at 2.4x the tokens. Its fix was scoped to the gate alone; the debrief and
the standing fold stayed on the transport deliberately.

**What is new is the caller.** The same limitation fires from the **agent loop
with real tools**, which ADR 0011's analysis never covered. Read back off
production on 2026-09-17 by log query, no eval run: the warning fired **twice
in roughly two hours** of uptime, both immediately after a tool returned
(`weather`, `play_music`), and **it did not raise** — `event loop cycle failed`
and `APIError` were both absent over the same window. The loop ran on to the
next completion and the turn reached TTS. She spoke, with her reasoning
discarded.

**Nothing anywhere counted it.** A dropped-reasoning turn was indistinguishable,
in every metric the system kept, from a turn that reasoned fine. There is no
`reasoning_strip_count` anywhere in the tree today; the signal lived only inside
a third-party logger, and `docs/CONVENTIONS.md`'s own rule — *a log line is not
a metric* — names that as the defect it is.

Two things moved on the same day and changed who is exposed:

| | before | after |
|---|---|---|
| `[llm] reasoning_effort` (tag, participation) | `low` | **`none`** |
| `[dreamer] reasoning_effort` (every adaptor) | `none` | **`low`** |

At `none` a path emits no `reasoningContent` at all and **cannot** hit this, so
the tag path — which produced both production hits — is out of scope. The
Dreamer is now in it, and could not have been before: `[dreamer]` was `none`
for the whole period #325 covers, which is why the four pre-switch tool calls
in that issue's confound table could never have warned. In the same week
`CultureAdaptor.tools` went from `frozenset()` to nine read verbs, and
`proactive` already holds tools. So the population at risk moved from
*high-traffic, short, conversational* to *low-traffic, long,
judgement-forming* — where a discarded reasoning is closer to being the verdict
itself, which is the shape ADR 0011 measured as a breach rather than as
degradation.

Separately and still open: OmniRoute combo `nora` moved from
`ollama-cloud/deepseek-v4-flash` to `deepseek-v4.1-flash` between 08:52Z and
09:42Z the same day, and **both** warned turns were served by the new one.
Whether the combo change *caused* the drop is **untested** — the before-window
contains zero comparable events, so the obvious 0-of-4 / 2-of-2 reading is
worthless. That is not a question this ADR answers; it is the question the
instrument exists to make cheap next time.

## Decision

**Three decisions, and the second is the one worth arguing about.**

### 1. The detector binds to the request shape, never to the warning string

The only signal the SDK emits is one English sentence at WARNING level.
Matching it would make the instrument depend on third-party prose that can be
reworded in a patch release, and **a detector that silently stops detecting is
worse than none** — its failure mode is a series that goes quietly to zero and
reads as a fix.

So `nora.agent.reasoning_strip.reasoning_blocks` counts the `reasoningContent`
blocks in the message list the pending call is about to be formatted from. That
is the same predicate the SDK's own filter applies one loop below the warning,
computed first-party.

**The failure is loud, and it is a test rather than a hope.**
`tests/unit/test_strands_reasoning_transport_contract.py` formats a synthetic
multi-turn exchange through the real `OpenAIModel` and asserts both halves
together: the SDK still drops the reasoning, **and** the detector agrees it
will. If a future SDK carries reasoning through, that test goes RED and says
so — rather than leaving the table quietly over-counting a drop that no longer
happens. That is the no-self-attestation rule applied to a detector: it is
judged against the formatter's own output, not against itself.

### 2. The record is a Postgres row. The Prometheus counter is not the record

A plain counter is the wrong instrument here, and this is the repo's own
measured rule rather than a preference: `nora` restarts two or three times a
day and prometheus-client creates label values lazily, so **a low-frequency
series reads EMPTY forever, and an absent series means "nothing since the last
restart", never "never happened"**. This event is exactly that shape — two
occurrences in two hours, on a path that now produces roughly one run per
adaptor per **day**. A counter would report zero across a deploy and be
believed.

`reasoning_strip_log` (migration 0057) is therefore the record: one row per
multi-turn agent invocation, carrying `path`, `model`, `reasoning_effort`,
`tag_id`, `model_calls`, `continuation_calls`, `strip_calls`,
`stripped_blocks`, `created_at`. Postgres because that is where application
state that must survive belongs and no new store is introduced; the shape is
the one accepted in PR #318 — a narrow table, verbs split on the read/write
asymmetry, absence representable per row rather than defaulted.

**The counter is still declared, and its own design answers the objection.**
`nora_reasoning_strip_total{path, outcome}` records the DENOMINATOR in the same
series: every continuation call is `stripped` or `clean`. A numerator-only
counter cannot separate «no strips» from «no observation» — both read as an
absent series. With `clean` recorded, an absent series means no observation and
a present `clean` with no `stripped` is a real zero. That is a genuine
fail-closed improvement over a bare counter, and it is still only good for the
live window; the question spans deploys, so the answer lives in the table.

### 3. The unit of record is the OPPORTUNITY, not the event

A raw count answers nothing. Two strips is a catastrophe at two opportunities
and a rounding error at two hundred, and #325's production reading could not
tell which it was. The denominator is **continuation calls**: model calls made
with a prior assistant turn already in the list, which is exactly the set the
transport can strip — a `reasoningContent` block can only be there because a
previous answer this loop received put it there.

Rows are written **only** when `continuation_calls >= 1`, enforced by a CHECK.
A single-call turn has zero opportunity; admitting it would make this a log of
every model call in the system rather than a census of the calls at risk.

`model` and `reasoning_effort` are read off the model provider **at the moment
of the call**, not from `nora.config`. Config says what was configured; the row
says what was sent. That is the difference between a `GROUP BY` and the two
hours of log-diving #325 records.

**Observation only.** No retry, no transport change, no `reasoning_effort`
touched. The drop is deterministic — a request-shape incompatibility, not
flakiness — and a retry would convert it into an apparently intermittent one,
which on ADR 0011's evidence is the branch that costs a safety case.

## Consequences

**What this buys.** The three questions #325 could not answer are now one SQL
statement (`docs/runbook.md`): which path, at what rate per opportunity, and
whether it moved when the model or the effort did. The Dreamer — the path the
config change moved the exposure onto, and the one where a run produces one row
a day rather than a reply somebody reads — is labelled by adaptor name.

**What got worse.** Five call sites gained a keyword, and three of them
(`invoke.run`, `FirePath._invoke`, `DreamerRuntime._sample`) gained an explicit
`await watch.flush()` that a reader has to notice. `flush` is idempotent and
swallows its own exceptions precisely because it is called from more than one
exit, and that is a weaker guarantee than one call site would have been. The
Dreamer writes it out three times rather than hiding it in a `finally`, because
awaiting in a `finally` on a cancelled task re-raises out of the cleanup path —
so a turn cancelled outright writes nothing, and that is stated rather than
hidden.

`reasoning_strip_log` has **no sweep**. Its bound is stated (rows only on
multi-turn invocations) rather than enforced by arithmetic the way
`tag_debounce_state`'s is, because pruning here would be a retention judgement
and the window this table has to answer over is longer than one deploy. If it
grows past usefulness, that is a decision for whoever reads it, not a default
chosen here.

**What this does NOT fix, stated rather than left to be found.** Nothing acts
on the signal: a turn whose reasoning is discarded still speaks, exactly as it
did on 2026-09-17. Nothing here measures whether that turn is WORSE — ADR 0011's
breach was on the gate, where the dropped reasoning was the verdict's own, and
whether a tag or Dreamer turn that loses its reasoning mid-loop is merely
degraded or is exploitable is an open question that needs its own instrument.
And the combo-change question is still untested: this makes the NEXT one
legible, it does not retroactively answer the last one.

**What is now load-bearing that was not.** The watch's registration order.
`BeforeModelCallEvent` dispatches in registration order, so a watch registered
*before* `ReplyGuard` would not see the `event.cancel` the guard sets — and the
guard cancels the pass that follows a terminal verb, which on the tag path is
most turns, at the exact moment the message list holds a reasoning block. That
would inflate both halves of the ratio on most turns.
`test_a_cancelled_model_call_is_not_an_opportunity` is what holds the order; a
comment did not stop the analogous wiring mistake in ADR 0011's story, so this
one is a test.
