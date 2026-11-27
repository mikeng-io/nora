---
class: design
subject: a sub-agent takes a long job in the background and reports its result once
owns:
  - sub-agent job: "delegated work run by a background agent of Nora's own, with a closed tool surface and no voice"
  - brief: "the typed hand-over of a job, interpreted once by Nora, from which the sub-agent never re-reads the room"
  - checkpoint: "a job's own record of what is done, doing, next and decided, kept apart from what code observed"
area: subagents
links:
  - {to: docs/design/cognition/acts.md, rel: uses}
  - {to: docs/design/cognition/execution.md, rel: uses}
  - {to: docs/design/agent/tools.md, rel: uses}
---

# Sub-agents

A turn in a room is the wrong container for a job of minutes with no conversation in the middle: its
bounds are sized for a chat reply, and the operator would be the loop, writing "next" each time. A
**sub-agent job** is delegated work ([acts](../cognition/acts.md#delegated-work)): Nora hands over a
brief, says she is on it, and the job runs on its own executor, reads and writes through its own
tools and reports once when it ends. The room hears the result from Nora.

## Parts

| part | is |
|---|---|
| **the tools** | start, list, read and cancel a job; each is a delegation or a read, and only a turn in a room calls them |
| **the sub-agent** | a background agent of Nora's own, built per job from the brief, with no voice and no memory of people |
| **the executor** | runs the job to its end and records its outcome as an item, as every executor does |
| **the worker** | an optional coding harness that is not Nora and does the multi-step computer work for the sub-agent ([worker](worker.md)) |

## The brief

The sub-agent starts blank: no transcript, no profile, no room block. Everything it needs is in the
brief Nora writes once.

| field | carries |
|---|---|
| title | a short name the room would recognise |
| objective | the job, in Nora's words |
| decisions | what was settled with the room, each with its question, answer and who answered |
| inputs | paths in the room's computer, copied into the job's own folder |
| documents | documents to revise |
| deliverable | what done looks like |

Code adds the room, requester, originating pass and time; the model never chooses them. One renderer
makes the prompt, and every block says what it is and who wrote it, so "the operator decided" is
distinguishable from "Nora assumed". The brief is kept whole.

## Reach

- **One room, fixed at launch.** The job's room and requester are written by code from the launching
  pass. The job has no send path of any kind: its result reaches that room only, through Nora. To
  share it elsewhere, someone asks Nora in a turn, under her guards.
- **A closed tool list.** The computer, with export to a kept copy only; documents, read and publish;
  the read-only lookups a turn in that room holds; the open-web reads; skills; and its own checkpoint.
  Never a send, a link or chat export, a reminder, or a write to the graph. Hers to make; the job
  reports and she decides.
- **Launching counts as asking** for the document tools, whose results reach the room only through
  Nora's own message. It does not count for an export to a link or chat, which needs a person who
  asked in that turn.
- **The job's computer work runs in its own folder under its own lease,** so a turn in the room never
  blocks it. It shares the scope's workspace with the room's turns and is separated from them by
  convention, not by enforcement.

## The checkpoint

A job of hundreds of rounds outruns any history, and one that has lost its place repeats work or
reports what it never finished. The job keeps a **checkpoint**: what is done, what it is doing, what is
next and what it decided. It is written by the sub-agent through one tool that replaces the whole of
it, so a correction leaves nothing stale, and it is never the record of what exists: code records the
documents published and files kept from the tool results that came back `ok`, and counts the rounds.
The sub-agent's words are shown as its words, unchecked; code's outputs are shown as observed.

A checkpoint is read by the room's running-jobs block (what is doing and next, briefly), by the job's
read tool (everything), by the sub-agent itself after its history trims (re-anchored into its first
message) and by the delivery of a job that stopped early. A job that finished delivers its report, which
ends with what is done, what is not done and why, and what it decided.

## Bounds

- **Turns, tokens and wall clock bound a job, never a per-tool allowance.** A call ceiling sized for a
  chat reply ended the work, not its length. What a guard keeps is the safety half: a tool not held,
  an argument not declared, a result size cap.
- **The limits are enforced in code and never stated in the prompt:** a cap written in words makes the
  model decline work it could do. A limit stop or a timeout ends the job failed with its reason, and
  the room is told.
- **A job never claims what it did not check.** Before it says a thing is done it reads it back, and
  it carries on through an ambiguity, listing each decision in its report.

## Reporting

A job reports when it ends, never mid-job. Between launch and delivery Nora answers "how is it going"
from the job's observed state: state, age, rounds and last tool, never from a model's summary, and
every turn in the room sees a block of that room's running and recently finished jobs, built fresh by
code. Delivery follows [execution](../cognition/execution.md): the outcome is recorded, a proactive pass
composes the message, and it is delivered at most once. The links in it come from the documents code
read back, never from the sub-agent's prose, and a document that cannot be read back is reported
missing, never linked.

A job lost across a restart is unknown and is reported to the room as lost, never failed and never
assumed done.

## Not in scope

A sub-agent asking the room a question, which is a waiting state, a paused clock and a routed answer,
a different order of work; agent teams; resuming a job across a restart; a mid-job message to a
running job, so a correction is cancel and a new job.

## Why

- **A job is delegated work** so that finishing it and telling the room are two outcomes, each with
  its own verdict ([acts](../cognition/acts.md)).
- **A blank start and a typed brief** because a model's input has to explain itself, and re-reading a
  chat would spend the job on conversation.
- **No voice and one room** because a background job has no person asking and no disclosure check, so
  its only exit is Nora.
