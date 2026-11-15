# Execution

Execution is how passes and actions run: which executor owns each, when work
is eligible, and what a pass's outcome settles.

## Executors

| executor | owns |
|---|---|
| **ingest** | observe for platform events and Nora's own acts |
| **watch poller** | observe's receive for watched sources |
| **wake evaluator** | claiming stimuli and admitting a subconscious pass, with no model call |
| **Dreamer runtime** | the subconscious pass: admission, single-flight per unit, global concurrency, cancellation, the model call, budgets |
| **conscious turn** | the called and ambient passes for one room |
| **fire path** | the proactive pass |
| **verifier** | study's verify and report |
| **judge** | study's grade, on a model independent of the writer |

No other launcher runs cognition. Collectors, reconnects and maintenance are
plumbing, not executors of the loop.

## Waking

The wake evaluator considers a unit when its input has settled, a strong cue
needs a quicker look, enough work has accumulated, the oldest input has waited
too long, or a clock stimulus is due. These are reasons to consider a pass,
never reasons to speak.

**Brakes**: single-flight per unit, global concurrency, cooldown, failure
backoff, spend limits, and an empty agenda — decided without a model.

A wake claims a set of stimuli. Only a closed outcome consumes its claims; a
failed, cancelled, expired or bounded pass leaves them pending. Durable rows,
never process memory, reconstruct pending and in-flight work after a restart.

**A later look is an intention's review time.** When a pass wants to return to
a matter, it commits or revises an intention with a review time; when due, the
clock stimulus starts a pass that reads the subjects as they are then. There
is no separate self-wake.

## Time

- A logical tick is a position on a stable UTC grid, not a count of passes.
  Wall time places schedules; monotonic time bounds running work.
- A schedule produces occurrences. **Due**, **claimed**, **started** and
  **completed** are separate facts; only an execution outcome says what
  happened.
- A deadline stores one authoritative instant with its calendar provenance.
  Recurrence is computed in its requested zone before it becomes due.
- A slow pass may miss evaluation opportunities but never loses durable due
  work. A restart reads overdue work under ordinary admission and never
  replays empty ticks.

## Settlement and failure

- A pass is open until its first durable outcome; closing settles its claims
  atomically. A late pass cannot close over work a later one has claimed.
- Reaching a round, token or time bound is a failed pass, not a normal end.
  What was admitted stays durable and is safe to meet again on retry.
- Input that repeatedly cannot be processed is parked for the operator, never
  silently consumed. An outage never deletes human input.

## Tracing

- One root span per pass; stages and actions are its children.
- Spawned work attaches its intended trace context explicitly.
- Every instrument keeps three outcomes apart: found, nothing found,
  unavailable.
