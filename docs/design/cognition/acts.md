---
class: design
subject: "the act envelope and delegated work"
owns:
  - surface: "where Nora's words land: a room is a private surface she belongs to, a public post a public surface read by strangers, and a voice channel a surface of a room, not a room"
area: cognition
links:
  - {to: docs/design/cognition/README.md, rel: uses}
---

# Acts

An act is anything Nora does that changes something outside the graph: a
message, a reaction, a song played, a document written, a job started, a post
published. Every act goes through one envelope, so every act is admitted,
recorded, and able to close an intention the same way.

## The envelope

| field | states |
|---|---|
| **kind** | delivery (reaches a room or an output channel: a message, a reaction, an image, a GIF, an announcement), effect (changes something that is not a conversation: a document, the computer, a play queue, a network), or delegation (starts work another executor carries on) |
| **executor** | what performs it: an engagement pass's delivery or act tool, a deterministic notifier, a job runner |
| **destination** | the room, surface or system it affects |
| **authority** | the pass and person on whose behalf it runs, and the intention it serves, if any |
| **identity** | a stable id, so a retry recognises the same act |
| **outcome** | an observed item reporting **done**, **failed** or **uncertain** |

**Rules**

- An act's outcome is an item, recorded by observe; the act counts only once it
  is recorded, and it counts as done only when that item reports done.
- An uncertain outcome — the call timed out after it may have taken effect — is
  never replayed blindly. It is read back by its identity first; until then it
  is unknown.
- Deliveries also pass the delivery arbitration; effects and delegations pass
  their area's admission.
- An act's outcome closes only an intention whose condition is that act being
  done; an intention waiting on a person or a state closes on that witness
  instead ([graph](graph.md)). A failed outcome closes nothing: it is a
  stimulus, and decide retries or drops the intention, citing the failure.
- Public publication on a surface without a unit records its outcome in its
  own ledger, not as a graph item.

**What identity guarantees.** Every act carries a stable identity, checked
before it is invoked, so Nora never *invokes* the same act twice. Whether the
outside effect happens once depends on the destination, and each executor
declares which guarantee it provides:

| guarantee | means | requires |
|---|---|---|
| **at-most-once invocation** | Nora never starts the same act twice | always |
| **idempotent effect** | repeating the call adds no second effect | the destination accepts an idempotency key |
| **read-back reconciliation** | an uncertain outcome is settled by reading the destination | the destination can be read back by identity |
| **unknown** | neither: an uncertain outcome stays unknown and is never retried | — |

No executor claims an exactly-once effect its destination cannot provide.

**Who delivers**

| delivery | route |
|---|---|
| composed by the model | an engagement pass's `send_reply` and delivery tools, through the tag pipeline or the fire path and the room's guards |
| deterministic notification — a song advancing, a job finishing | a code executor and no model, through the same delivery guards, to its resolved room or output channel; an output channel is not a room and gains no ingest or trust meaning |
| public publication on a surface without a unit | its own public ledger, not graph items ([boundaries](boundaries.md)) |

Only engagement passes invoke act tools. The reflection loop acts only by
committing an intention a proactive pass later carries out.

## Delegated work

Delegated work — a sub-agent job, a computer task, a long browser session, a
handed-over tool call — runs outside both loops under its own executor.

- Starting it is a delegation act: a delegation tool the model calls, or a
  tool call handed over when its wait window ended
  ([execution](execution.md#tool-calls)), which keeps its original identity
  and authority.
- The work is an achieve intention naming its executor and the job's
  identity; it is done when the job's outcome item reports success. A failed
  or cancelled job closes nothing: decide retries or drops the intention,
  citing that outcome.
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
  controls, and its result is an item. A read handed over at the end of its
  wait window becomes delegated work.
- Silence: it is the recorded outcome of a pass that chose not to act.
