---
class: design
subject: the computer runtime, its sessions and audit, and how files move in and out
owns:
  - runtime port: "the one interface Nora uses to run commands, read and write files and move bytes in a computer"
  - computer session: "one task continuing across passes, derived from observed events and never model-written"
  - lease: "exclusive hold on the resource being changed, not on a room"
area: computer
links:
  - {to: docs/design/computer/README.md, rel: uses}
  - {to: docs/design/cognition/execution.md, rel: uses}
---

# Runtime, sessions and files

## Runtime

- **The port** runs commands, reads and writes files, makes exact edits, lists state and moves bounded
  bytes in both directions. Its implementation is independent of the VM technology, and a fake meets
  the same contract suite. A long-lived streamed process is the one verb a worker adds
  ([worker](../subagents/worker.md)).
- **Files have tools of their own** so a command never carries quoting or a silent edit miss. Long
  jobs run in named terminal sessions, never in a hidden persistent kernel.
- **A scope has an owner and a workspace.** Paths resolve only inside the scope, with no traversal,
  absolute escape or link trick reaching another. Two scopes never share an abbreviated owner; an
  existing owner that is not provably the same scope refuses before anything is changed. There is no
  shared scratch space, and no scope inherits another's fallback.
- **The host owns the lifecycle.** One host controller creates, restarts and stops computers, on a
  narrow authenticated interface that accepts no free-form create parameters, shell or destroy. Work
  claims its scope's execution segment first and nested work inherits it; a stopped computer is
  restarted from its disk, never recreated. A computer stops only when positively quiet: no lease,
  admitted work, resumable tab or detached process, and a failed read is not quiet.
- **Capacity is reserved before creation** and counts starting and running guests, including ones
  found after a restart. Unknown inventory or insufficient headroom refuses; active work is never
  killed to make room.
- **Health reads real responses,** distinguishing process down, daemon down, policy denial and
  upstream failure. A connection accepted is not a service working, and a missing optional integration
  is not unhealthy.
- **The endpoint registry is a read-only host mount.** An absent mount is its own state: `/health`
  reports the computer degraded with the reason and the deploy read-back warns, while Nora keeps
  serving and every scope reads as unavailable. It is never confused with a scope that has no VM,
  which is the same answer for a different cause.

## Sessions and audit

- **A session is observed work, not model memory.** Code opens it before a scope's first audited call
  and continues it on later calls. It stores the objective, scope and provenance; its progress and
  state derive from its events and fresh observation, with no model-written `done`, `blocked` or
  `next`. A pass sees the computer as it is: recent files, live terminals, browser state and the
  latest events, with ages.
- **A lease covers one resource,** a project directory, a browser context or a task, so independent
  work progresses in parallel and reads take none. A pass waits briefly and may preempt background
  work at a safe boundary; background work defers rather than holding up a reply, and stops with its
  evidence intact when preempted.
- **A session expires** after inactivity with no owned live work, when its resources are gone or when
  its lifetime ends. Expiry is a compare-and-set that releases the lease and records the event. It
  never deletes files, projects, private browser profiles or audit, and a later call opens a new
  session instead of pretending the old one continued.
- **Every command, mutation, import, export, transition, preemption, cancellation and lifecycle action
  appends an event** with request, result, timing, actor and content references. Full output is kept
  by digest in bounded custody. An operation with no required row is a coverage defect, not a quiet
  success. Host events are spooled first and replayed idempotently into the same record; the spool is
  a delay, not a second truth.

## Files in and out

- **Import** resolves a media object already admitted in the room, validates its custody and writes
  bounded bytes into the scope's inbox. The computer never contacts Nora's object store, and the
  source stays attributable to the room and media row, not to a filename the model chose.
- **Working files** live on ordinary storage in the scope and persist across passes while the
  conversation stays stateless. File state is read fresh, never remembered in doctrine.
- **Export** has three destinations: a chat attachment to the admitted target, a link published
  through Nora's own custody, or a kept copy with a manifest for later continuation. Each records the
  room, scope, source path, digest and provenance. The computer never holds a publication credential
  or chooses its own URL.
- **Active web content is not a file export.** A page is a [document](../documents/README.md), never an
  uploaded download.
- **A destructive recreate needs a verified backup** or an operator decision, and recovery never maps
  one scope into another.
