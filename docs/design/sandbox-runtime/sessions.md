# Sessions and audit

## Observed work, not model memory

A computer session is one task continuing across stateless turns. Code opens
it before the first audited computer call and continues it on later calls in
that scope. It is not the VM, the workspace or a model-written summary.

The row stores the objective, scope, status, provenance and resource pointers.
Progress and display state are derived from its own event history and fresh
observed state. There is no model-authored `done`, `blocked`, `paused` or
`next_step` field to drift away from reality.

The declared computer block reads recent files, live terminal sessions,
browser state and the latest computer events at assembly time. It therefore
shows what the computer actually holds, including ages, without a Standing
fold that could drop unfinished work.

## Leases and concurrency

Every effect carries its actor, requesting person, scope, turn, tool and
trace correlation. Conscious and background work use the same audit contract
but different contention behavior: interactive turns wait briefly and can
preempt background work at a safe boundary; background work defers rather
than holding up a reply.

A lease covers the resource being changed, not an entire room. Project
directories, browser contexts and independent tasks can progress in parallel.
Reads of observed state do not take the mutation lease. Long-running work
uses named terminal sessions so cancellation, resumption and ownership remain
visible.

A background browser or agent loop checks preemption between steps and stops
with its current evidence intact. A tool must not wait recursively on another
agent sharing the same resource pool. Interactive priority does not remove
the global tool-slot ceiling.

## Expiry and continuation

A session expires after inactivity with no owned live work, when its
resources are gone or when its bounded lifetime ends. Expiry uses a
compare-and-set transition, releases the lease and records a sweeper event.
Live terminal work keeps the session open until its limit; it is not silently
killed merely because a row aged.

Files, project directories, private browser profiles and audit history are
not deleted by expiry. A lapsed session remains visible for a bounded period
so Nora can explain where it stopped and resume from real state; no
unsolicited reminder is implied. A later computer call opens a new session
rather than pretending the old one never ended.

## Complete evidence

Every command, file mutation, import, export, session transition, preemption,
cancellation and lifecycle action appends a computer event with request,
result, timing, actor and content references. Full output lives in bounded
external custody where needed, linked by digest, so the row stays small
without losing evidence.

Nora writes the authoritative audit because the VM is less trusted. Host
lifecycle events spool first and replay idempotently into the same store; the
spool is a delay, not a second truth. Audit failures and daemon failures are
visible operational outcomes, and an operation with no required row is a
coverage defect rather than a silent success.
