# Execution and sharing

## One pool, two admission classes

`shared_tool_slots()` returns the process's single `ToolSlots` instance.
Interactive agents receive the shared interactive executor; Dreamer and
subagent factories receive the shared background executor. They are different
admission classes over the same total, not independent pools.

The background semaphore admits only the total less the interactive reserve,
while the total semaphore bounds both classes together. Acquisition releases
the background permit if total acquisition fails, so a failed attempt cannot
leak capacity. The reserve must leave some background capacity as well as
interactive capacity.

The pool belongs to one event loop. Process-wide sharing is what prevents
overlapping agents from multiplying a per-agent ceiling into an unbounded
upstream burst; separate processes still require their own deployment-level
capacity reasoning.

## Per-task execution

Strands creates one task for every tool-use block. `BoundedToolExecutor`
overrides the per-task seam to acquire before delegating and release in
`finally`, preserving the library's batch lifecycle rather than copying its
drain loop. This intentionally binds to a pinned framework seam that upgrade
checks must exercise.

Waiting tasks queue for capacity instead of being refused. A semaphore, rather
than fixed chunks, lets later work start as soon as any slot frees. Results
retain the model's original tool-use order even when later calls finish first.

The wrapper does not alter tool budgets, idempotency, result payloads or the
terminal reply guard. Call-ledger reservations still count admitted-but-
unfinished calls by their framework identity, so a vetoed sibling cannot free
another call's budget.

## Failure and cancellation

Success, exception, fail-fast abandonment and cancellation all release every
held permit. A following turn must be able to acquire immediately; an exception
is never swallowed merely to tidy up. Waiting counters return to zero as well
as in-flight counters.

The executor supplies no timeout of its own. Each tool needs a real bounded
operation or the owning turn's cancellation. A never-returning tool otherwise
retains capacity indefinitely. Tools that call or await other agents using the
same pool risk deadlock through nested slot ownership and are outside the
supported shape.

Queueing changes wall-clock arithmetic. A batch larger than the pool becomes
multiple waves, so media and other per-call timeouts must fit the turn's
overall deadline after queuing, not only when every call starts together.
Raising a per-tool budget without checking that relationship can silently kill
the whole turn even though the resource ceiling works.

## Observation and review

In-flight, waiting and peak gauges show actual sharing and contention. Waiting
duration, not merely the presence of a wait, is the tuning signal: a slot may
be held across framework scheduling gaps, so a counter alone does not prove
saturation.

Verification drives real framework batches larger than the ceiling and checks
observed concurrency, result order, overlapping turns, background reserve,
cancellation and later acquisition. Production traces can independently derive
start and end overlap, but a correct-looking span duration alone is not proof
of concurrency or of the ceiling.
