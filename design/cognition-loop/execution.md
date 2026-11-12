# Execution and time

## Two layers, one owner each

Conscious turns belong to the engagement and fire paths. An addressed message
or another admitted occasion opens a room-bound turn; its delivery outcome is
recorded even when the outcome is deliberate silence.

Subconscious work belongs to the Dreamer runtime. The wake evaluator decides
that work is eligible, then submits Cognition as a session-shaped adaptor.
Dreamer owns admission, occupancy, task lifetime, cancellation and the model
call. Cognition owns its agenda, verbs, typed writes and session outcome. There
is no sibling task launcher or model-selection route for Cognition.

Other Dreamer adaptors use the same execution owner. A proposal-envelope
adaptor returns candidates for code to validate and commit; Cognition uses its
typed session writes instead. This difference does not create another runtime.
Neither kind may deliver directly to a room.

Collectors, transport reconnects and operational maintenance are plumbing, not
additional cognitive layers. Multiple drivers may share a process without
sharing execution ownership.

## Time vocabulary

A logical tick is an integer position on a stable UTC grid, not a count of
passes through a loop. Wall time establishes schedule position; monotonic time
bounds running work. Persisted deadlines derive from real instants and remain
meaningful across restart and deployment.

A schedule produces occurrences. Eligibility says an occurrence is due;
admission says its owner may execute it now. Due, claimed, started and completed
are different facts, and only an execution outcome says what happened.

Time-driven work stores one authoritative deadline representation together
with the original instant and calendar provenance. A duration means at least
that duration. Calendar recurrence is calculated in its requested zone before
becoming a due occurrence. Tick alignment never changes tool grants or batches
unrelated work.

Drivers sleep to relevant boundaries and recalculate time after work. A slow
pass may miss evaluation opportunities but does not lose durable due work.
Restart reads overdue work under ordinary admission rather than replaying
empty ticks. Clock movement does not reopen completed occurrences.

## Settlement and failure

A tracked wake is open until its first durable outcome. Closing settles its
claimed stimuli and any carry state atomically. Failure, timeout, cancellation
and expiry leave those stimuli pending. A late session cannot close over work
a later wake has claimed.

Single-flight is scoped to the unit, with a separate global concurrency bound.
Backoff limits repeated failure; cooldown limits ordinary frequency. Neither
may turn an infrastructure outage into deletion of human input. Input that
repeatedly proves unprocessable is parked for operator attention rather than
silently consumed.

Resource limits cover rounds, tokens and elapsed time. Reaching one is a failed
session, not normal termination. Work already committed remains durable and
must be safe to encounter on retry; uncommitted understanding is not advanced.

## Observation and provenance

Every entry point has one trace root, with execution stages as children.
Spawned work attaches its intended trace context rather than accidentally
inheriting a caller's. A trace containing a model generation goes to the model
observability path; plumbing outcomes belong in metrics.

An instrument records the result and the cost of the step: admission and
outcome, pending work, read bounds, model spend and delivery. It preserves the
three-state distinction between something found, genuinely nothing found and
being unable to check. A log line does not substitute for a counter, and an
empty span does not substitute for an outcome.
