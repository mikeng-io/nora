# Execution

Execution is how passes and actions run: which executor owns each, how long a
pass waits for a tool call, when work is eligible, and what a pass's outcome
settles.

## Executors

| executor | owns |
|---|---|
| **ingest** | observe for platform events and Nora's own acts |
| **watch poller** | observe's receive for watched sources |
| **wake evaluator** | claiming stimuli and admitting a wake pass, with no model call |
| **Dreamer runtime** | the reflection loop's wake pass (the reflection session): admission, single-flight per unit, global concurrency, cancellation, the model call, budgets |
| **tag pipeline** | the engagement loop's called pass for one room, followed by its debrief |
| **fire path** | the engagement loop's ambient and proactive passes for one room, each followed by its debrief |
| **bridge work runner** | durable intake from transport bridges: owns each event before acknowledging it, and launches the pass |
| **job executors** | delegated work: sub-agent jobs, computer and browser tasks, handed-over tool calls ([acts](acts.md)) |
| **verifier** | study's verify and report |
| **judge** | study's grade, on a model independent of the writer |

No other launcher starts a pass. Collectors, reconnects and maintenance are
plumbing, not executors of the loops.

## Tool calls

A pass's tool calls run under the pass's own executor; a call is an action,
never a pass.

- The calls of one model step run concurrently, and their results return in
  the order the model asked for them.
- All passes draw on one shared pool of tool slots. Engagement passes keep a
  reserve that reflection passes and delegated work cannot take, so a busy
  background never stalls a room.
- In an engagement pass each call has a **wait window**: a configured bound on
  how long the pass waits for it. A call that settles within its window
  returns its result to the step.
- A call still running when its window ends is **handed over**: it keeps
  running as [delegated work](acts.md#delegated-work), gives up any slot of
  the engagement reserve, and the step receives **started** with the work's
  identity in place of a result. The pass continues and ends as usual,
  holding the room's slot no longer than its own steps.
- A step continues once each of its calls has settled or been handed over.
- `send_reply`, deliveries and writes to the graph are never handed over: they
  mean something only inside their pass.
- The handed-over work and its report are intentions like those of any
  delegated work: the pass commits them when its reply promises the result,
  and the debrief commits what the pass left. A proactive pass delivers the
  report once the work's outcome is recorded and may stay silent. A proactive
  pass that hands over a call moves its own intention to that outcome rather
  than creating another.
- Reflection passes have no wait window; their calls, like a handed-over
  call's, are bounded by the pass's or the job's own limits.

The window is what keeps a room answered: however long the work runs, the
room hears from Nora within it, and the next messages are not queued behind
work that no longer needs the slot. The clock decides, not a declaration made
in advance per tool, because a tool's duration varies with its input. Waiting
for a background call to rejoin the same pass was rejected: it keeps the
slot held for the whole run and needs a second, interim kind of reply. A
separate timeout result was rejected too: work still running at the window is
started, and work stopped at a bound is unavailable with its reason, or
uncertain when an effect may have happened ([acts](acts.md)).

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

## Replaying history

A wake pass over history runs in source-time order with the instant of
its evidence as its clock. It reads only what existed then, proposes
understanding only, and never creates present-day intentions, lessons or
sends. A replay that closes records one receipt naming the exact inputs it
observed; a failed replay records none and never takes a live slot.

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

- A pass is open until its outcome is durable, and closing settles its claims
  atomically. An engagement pass settles on its **reply outcome** — the send or
  the silence, recorded. The reply is never retried because its debrief failed.
- A debrief that fails leaves a durable **debrief-pending** obligation for that
  pass, retried on its own: in the room's next slot first, otherwise by a wake.
  A standalone retry takes the room's slot like any pass. The debrief settles
  its own obligation, never the pass.
- An inbound event is acknowledged only once its owner and work are durable.
  A started act whose outcome is uncertain is read back by identity, never
  replayed.
- **While a room's slot is held, the engagement loop wins**: a reflection write
  to an intention of that room is refused as room-busy and retried after the
  slot is released, against the then-current revision.
- A late pass cannot close over work a later one has claimed.
- Reaching a round, token or time bound is a failed pass, not a normal end.
  What was admitted stays durable and is safe to meet again on retry.
- Input that repeatedly cannot be processed is parked for the operator, never
  silently consumed. An outage never deletes human input.

## Tracing

- One root span per pass; stages and actions are its children.
- Spawned work attaches its intended trace context explicitly.
- Every instrument keeps three outcomes apart: found, nothing found,
  unavailable.
