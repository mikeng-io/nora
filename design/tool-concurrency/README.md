# Tool concurrency

Nora bounds how many model-controlled tools run at once across the whole
process, not merely how many calls a tool may make in a turn. Interactive
replies retain capacity when background work saturates the shared pool.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Ftool-concurrency%2F%22)

## Parts

- **The shared slot pool** is the single authority for in-flight tool work in
  one event-loop process. Interactive and background executors draw from it.
- **The background reserve** prevents Dreamer and subagent work from occupying
  every slot while a user-facing turn needs one.
- **The bounded executor** wraps Strands' concurrent execution per tool task,
  acquiring before work and releasing on every exit path. [Execution](execution.md)
  defines ordering, cancellation, failure and timeout behavior.
- **The call ledger** remains a separate per-tool budget: how many calls may be
  admitted, not how many may overlap. The two bounds compose rather than
  replacing one another.
- **Execution observations** expose in-flight, waiting and peak work so a
  ceiling can be reviewed from real contention rather than guessed.

## Terms and promises

The limit is process-wide across tag turns, Dreamer runs and subagents; it is
not one pool per agent and not a distributed fleet-wide guarantee. Every agent
built through the standard factories uses the appropriate shared executor by
default, so a new loop cannot quietly opt back into unbounded fan-out.

Excess calls wait rather than being refused. Useful parallelism remains, but a
large batch cannot monopolize upstream connections, decrypted media, sockets
or model calls. Interactive work can use the full pool; background work is
capped below it to preserve reply capacity.

Count admission and concurrency are different. A tool can still exceed its
budget slowly, four at a time, and a large admitted batch can still exceed its
wall-clock budget while queued. The call ledger's reservations and refusal
semantics stay unchanged.

## Change and failure

Ceiling and reserve changes need evidence from waiting time, peaks, upstream
capacity and user-visible latency. Per-tool or per-agent pools would be a
different resource-isolation decision, not an implementation detail.

A slot is released after success, error, early abandonment and cancellation.
A tool that never returns holds its slot until its own timeout or the turn is
cancelled. A tool must not wait on another agent using the same pool, because
nested slot ownership can deadlock both.
