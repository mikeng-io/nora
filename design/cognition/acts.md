# Acts

An act is anything Nora does that changes something outside the graph: a
message, a reaction, a song played, a document written, a job started, a post
published. Every act goes through one envelope, so every act is admitted,
recorded, and able to close an intention the same way.

## The envelope

| field | states |
|---|---|
| **kind** | delivery (reaches a room), effect (changes something outside a room: a document, the computer, a queue, a network), or delegation (starts work another executor carries on) |
| **executor** | what performs it: the engagement turn's `send_reply`, an area's tool, a deterministic notifier, a job runner |
| **destination** | the room, surface or system it affects |
| **authority** | the pass and person on whose behalf it runs, and the intention it serves, if any |
| **identity** | a stable id, so a retry recognises the same act |
| **outcome** | an observed item: done, failed, or uncertain |

**Rules**

- An act's outcome is an item, recorded by observe; the act counts only once it
  is recorded.
- An uncertain outcome — the call timed out after it may have taken effect — is
  never replayed blindly. It is read back by its identity first; until then it
  is unknown.
- Deliveries pass the delivery arbitration and the room's guards. Effects and
  delegations pass their area's admission and the same identity check, so the
  same act is never performed twice.
- A deterministic notification — a song starting, a job finishing — is an act
  with a code executor and no model. It is recorded like any other.
- Only the tag pipeline and the fire path deliver to a room. An effect or
  delegation reaches a room only through a later delivery.
- An act closes an intention only through its outcome item and observe's
  compute.

## Delegated work

Delegated work — a sub-agent job, a computer task, a long browser session —
runs outside both loops under its own executor.

- Starting it is a delegation act. The work is an achieve intention naming its
  executor and the job's identity; it is done when the job's terminal outcome
  item arrives.
- The executor's own job state is authoritative for the job. Its events and
  results enter through observe as items.
- Reporting the result is a separate child intention, delivered by a proactive
  pass: finishing the work and telling the room are two outcomes, each with its
  own verdict.
- Work an executor lost track of across a restart is unknown, never failed and
  never assumed done.

## What is never an act

- Writing to the graph: that is admission.
- A read, including a paid outside read: it is a read, bounded by its spend
  controls, and its result is an item.
- Silence: it is the recorded outcome of a pass that chose not to act.
