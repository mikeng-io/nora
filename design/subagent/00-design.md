---
type: design
title: "Sub-agent — Nora hands a long job to a background worker and reports its result"
description: "Not built. `subagent_create(objective)` starts a background Strands agent inside Nora's process with a closed tool surface (the computer, documents, read-only lookups) and no voice of its own. It is bounded by turns, tokens and wall clock only, never per tool (DEC-247 applied to it). It reports once, when it ends, through the kept deferred-job outbox (`compute_jobs`, `ComputeJobPoller`, the fire path), and Nora says the result in the room. Replaces issue #22's Smith-based design, whose subject was retired by DEC-254. Mid-job messages from Nora (`subagent_send`) are a named later step; two-way questions are out of scope."
tags: [nora, subagent, delegation, background, compute-jobs, outbox, strands, limits]
timestamp: '2026-10-04'
status: proposed
governance: operator-directed
edges:
  - {dst: ../sandbox-runtime/00-design.md, rel: depends_on, provenance: asserted}
  - {dst: ../artifact/00-design.md, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/30-cognition.md, rel: depends_on, provenance: asserted}
  - {dst: ../video-generation/00-design.md, rel: relates_to, provenance: asserted}
---

# Sub-agent

Issue #22 asked for a general-purpose delegated worker by generalising Smith.
Smith, `make_artifact`, the Docker backend and Drop's job tokens were retired
on 2026-10-01 (DEC-254), which kept the deferred-job outbox "for a future
`delegate_task`". This document is that design, re-derived from what runs
today. Its tool is named `subagent_create`, not `delegate_task` (§3).

## 1. The case, from a live trace (2026-10-04, 13:36–13:44 HKT)

An operator sent an archive of 8 planning sheets and asked for 8 documents,
rebuilt for reading on a phone. Six tag turns later, 40–55 s and 16–23 model
calls each, 2 of the 8 were published. What stopped it:

- **A per-tool ceiling ended the work, not the work's length.** Turn 1 spent
  `computer_run`'s 12 calls (`nora/agent/tools.py:1525`) reading the sheets
  and published nothing; turn 2 still had no `computer_run` left and published
  one document with two sheets never read. About five `document_publish`
  calls came back `bad_arguments` on a malformed spec, and each retry spent
  the same turn's allowance.
- **The operator was the loop.** Every step needed a new tag ("next"), and he
  wrote it himself: "if u out of budget, u can break it into multiple steps or
  session".
- **She claimed what she had not checked**, twice: an index document that
  did not exist, and a link said to be published before it was read back.

The job is a few minutes of work with no conversation in the middle. A tag
turn is the wrong container for it: 20 rounds, 12,000 output tokens, 360 s,
and per-tool allowances sized for a chat reply.

**The same job with a sub-agent.** Nora opens the archive, settles what only
the operator can answer (keep the layout or rebuild it; leave personal data in
or not), then calls `subagent_create` with the objective and the archive's
path, and says she is on it. The sub-agent reads all 8 sheets, publishes 8
documents, reads each one back, and ends. The poller sees the job finished;
Nora posts one message with the 8 links, built from the `document_id`s the
tools returned. A correction afterwards ("make it mobile-first") is a new
sub-agent handed those `document_id`s to revise.

## 2. Two things, two layers

| | `subagent_create` | a deferred job (`compute_jobs` row) |
|---|---|---|
| what | a tool Nora calls | one row in the outbox |
| does | starts a worker on an objective | remembers who is waiting where, delivers the result once |
| owns | model, tools, doctrine, limits | room, requester, state, result |
| relation | one kind of worker | one row per piece of slow work, from any worker |

The outbox was built 2026-08-13 for `make_artifact` (DEC-99): launch the
work, wait about 15 s, and on a slower job write a row so the result arrives
later through `FirePath.speak`, delivered at most once by a compare-and-set
claim. Smith was its only worker; the mechanism was always generic. Video
generation (`design/video-generation/00-design.md`) is a second worker
waiting for the same outbox, which is why the table stays generic (§6).

## 3. The tool family

ADR 0015 §1 names a new tool `<domain>_<verb>`, verbs from
`tests/architecture/test_tool_names.py::VERBS`. `delegate_task` puts a verb
in the domain slot and `task` is not a verb there. The domain is the thing
that runs:

| tool | verb | who calls it | built |
|---|---|---|---|
| `subagent_create(title, objective, deliverable, decisions?, inputs?, document_ids?)` | `create` | Nora | step 3 |
| `subagent_list()` | `list` | Nora | step 3 |
| `subagent_read(id)` | `read` | Nora | step 3 |
| `subagent_cancel(id)` | `cancel` | Nora | step 3 |
| `subagent_send(id, message)` | `send` | Nora | later (§9) |

All five verbs are already on the list. `subagent_create` returns
`{status: "started", id}` at once and never waits for the job; the turn goes
on to say she is on it. `inputs` are paths in the room's computer workspace;
`document_ids` are documents to revise. The name is measured before it is
final (ADR 0004, `nora-tool`'s `tool_selection` routing check).

**Every job has a title** (operator, 2026-10-05): `subagent_create` requires one, empty or over
60 characters is `bad_arguments`, and it is kept in `compute_jobs.payload` (no migration). The
worker's brief carries it, the delivery message names the job by it, and `subagent_list` and
`subagent_read` return per job `title`, `description` (the objective: cut to 120 characters in a
list, whole in a read), state, and either live progress (rounds, last tool, age) or the outcome,
the report (cut to 200 in a list, whole in a read) and document titles and ids. No model writes
a summary of any job. A row or payload from before titles shows its objective cut to 60.

## 4. The worker

**Where it runs.** In Nora's process, as an asyncio task, the shape
`LiveCognitionAgentRunner` already uses (`nora/app/cognition_agent_runner.py`):
`factory.create_dream_agent`, a `DreamGuard`, `factory.invoke` with
`factory.build_limits`, `turn_deadline` and `asyncio.wait_for`. No container,
no RPC: issue #22's open fork (a background job cannot reuse a turn's tool
closures) goes away because the worker builds its own tools.

**Its room, fixed at launch.** The launching turn's `platform`,
`conversation_id`, `conversation_type` and requester are written to the job
row by code, never chosen by the model. The worker's tools are built from
those facts when it starts, the way a cognition session builds tools for its
item's room (`CognitionSessionRunner._build_tools`), and its computer scope
is `computer_scope(platform, conversation_id)` (`nora/domain/computer.py:70`),
so it sees the same workspace as the turn that launched it, including the
`inbox/` file the turn imported. The room facts reach the backend in
`launch()` itself: `backend_job_id` is NOT NULL and comes back from `launch`,
so the row is written after the worker already exists.

**Its tools.** A closed list, checked in code (widened in §4c):

- the computer: `computer_run`, `computer_read`, `computer_write`,
  `computer_edit`, `computer_import`, and `computer_export` with `to="keep"`
  only;
- documents: `document_publish`, `document_read`;
- read-only lookups: `web_search`, `web_fetch`, `media_fetch`;
- `skills` and `load_tools`, so task know-how arrives as skills, the way
  `nora/skills/document-creation` does today.
- `checkpoint_update`, its own place-keeper (§4d), offered to no other occasion.

Not on it: `send_reply` and every other send path (it has no voice), `remind`,
note and event writes, memory writes. A later worker that needs a write gets
it by a row in this document, not by default.

**Neither precedent can build that list.** Review of this design (2026-10-04)
found the Dreamer and cognition paths stop write tools on purpose:

- `RegistryToolSource.build` keeps only `read_only()` tools
  (`nora/agent/dreamer.py`, `wanted = frozenset(names) & self.read_only()`),
  the Dreamer's "may look, may not act" ceiling (`ToolRegistry.read_only_tools`);
  cognition's `_guarded_read_tools` raises `NotReadOnlyToolError` on anything
  else. `computer_run` and `computer_write` are not read-only.
- `document_publish` refuses any call whose actor is not `tag` or whose room
  has no tagger: `unavailable`, `no_requester` (`nora/agent/tools.py`).
- `computer_export` to a link or the chat is refused for any actor but `tag`:
  the containment line of `design/sandbox-runtime/00-design.md` §15.4,
  decision 6 ("an effect outside the VM waits for a person who asked in THIS
  turn").
- `create_dream_agent` takes no `skills_dir` by design (`nora/agent/factory.py`),
  and `read_only_tools()` excludes the tool loader.

So the worker needs its own tool source: `build_tool_specs` called with a
`Room` whose tagger is the recorded requester, and a new actor, `subagent`,
added to `ACTORS` and to the `ck_computer_events_actor` check (a migration);
its computer calls use `path="subagent"`, already reserved in
`nora/domain/computer_events.py`. `create_dream_agent` gains an optional
skills directory, or the worker uses its own factory call.

**The ruling this needs (operator, at the step-2 DEC).** That a person's
launch of a job counts as "asked" for `document_publish`'s requester check,
for the job's lifetime. Proposed: yes for documents, which reach the room
only through Nora's delivery message (§6, gap 5); no for `computer_export` to
a link or the chat, which stays `tag`-only, so decision 6's line holds for
files.

**The computer lease, as the code already decides it.** A background actor
never waits for the room's lease and never opens a computer session; it only
continues one a live turn opened, and a held lease answers it `busy` at once
(`_computer_lease`, `_computer_session` in `nora/agent/tools.py`). Since the
worker has no per-tool ceiling, `busy` retries would spend its turns. The
`subagent` actor therefore gets one of two behaviours, chosen at step 2: wait
for the lease with a bound, as a live turn does; or run under its own lease
key in a job folder (`projects/<job id>/`) so a tag turn in the room never
blocks it. Proposed: its own key and folder, with the launching turn's
`inbox/` files copied in at launch.

**Its doctrine.** One short new file, `subagent.md` (not yet written), of the
kind `nora/dreamer/doctrine/dreamer.md` is, plus
`nora/doctrine/core/constraints.md` reused as is. Room doctrine (voice,
silence, belonging, participation, speech format) is about speaking in a room
and does not apply. The file says:

- it works for Nora on one objective; its output is a report to her, not a
  message to the room;
- before saying a thing is done, it reads it back; it reports what is done,
  what is not and why, and never claims what it did not check;
- it cannot ask anyone anything, so on an ambiguity it decides sensibly,
  carries on, and lists each decision in the report.

It never states a limit (§5).

## 4a. The brief: how Nora hands context over (operator, 2026-10-04)

The worker starts blank: no transcript, no peer card, no room block. What it
needs is in the brief, interpreted once by Nora; it never re-reads the chat.

`subagent_create` takes a typed brief:

| field | carries |
|---|---|
| `title` | a short name the room would recognise (required, at most 60 characters; operator, 2026-10-05) |
| `objective` | the job, in Nora's words |
| `decisions` | what was settled with the room, each `{question, answer, by}` |
| `inputs` | paths in the room's workspace; code copies them into the job folder |
| `document_ids` | documents to revise |
| `deliverable` | what "done" looks like |

Code adds, never the model: room, requester, `tag_id`, launch time, timezone.
One formatter (a `SubagentBrief` render, the shape of cognition's
`render_agenda`) makes the worker's prompt; every block says what it is and who
wrote it, so "the operator decided" is distinguishable from "Nora assumed"
(`AGENTS.md`: a model's input has to explain itself).

## 4b. What is kept

| what | where |
|---|---|
| the brief, in full | `compute_jobs.payload` (step 1) |
| report, document ids | `result_report`, `result_document_ids` (step 1) |
| trace link | `trace_id`, and `tag_id` back to the launching turn (step 1) |
| every model and tool call | the Langfuse `subagent` trace (step 2) |
| every computer action | `computer_events`, actor `subagent` (step 2) |
| rounds, tokens, last tool while running | in memory, served by `subagent_read` (step 2) |
| the worker's message history | not stored; Langfuse holds it for reading. Needed only to survive a restart (§9) |

## 4c. Reach: one room, a closed list (operator, 2026-10-04)

**No other room.** The worker has no send path. Its result reaches the
launching room only, through Nora. Delivering elsewhere would move room A's
content into room B from a background job with no person asking and no
disclosure check (R-31, sandbox decision 6). To share it elsewhere, someone
asks Nora later and she uses `send_to` in a turn, under her guards.

**Not every tool.** The list widens §4's to the read-only tools a tag turn in
the launching room holds, scoped to that room and its disclosure rules:

| | on the worker's list |
|---|---|
| yes | the computer (`computer_export` `keep` only); `document_publish`, `document_read`; `web_search`, `web_fetch`, `media_fetch`; `skills`; the room's read-only lookups (`note_read`, `note_search`, `record_query`, `place_info`, `route`, `transit`, `weather`, …) |
| never | `send_reply`, `send_to`, `send_gif`, `media_send`, every other send path; `computer_export` to a link or chat |
| not now | memory writes: `note_create`, `note_revise`, event writes, `remind`, `remember`. Hers to make in a turn; the worker reports, she decides |

## 4d. The checkpoint: a job keeps its place (operator, 2026-10-05; DEC-285)

A job of hundreds of rounds outruns any history, and one that has lost its place repeats work
or reports what it never finished. The worker therefore keeps a **checkpoint**: what is done,
what it is doing, what is next, what it decided.

**Why not Standing.** Standing is a model's fold over a conversation, and a fold drops an open
plan; what it says exists is the model's word. The checkpoint differs on the two points that
failure turns on. It is **typed and written by the worker itself**, through one tool that
replaces the whole of it (no merge, so a correction leaves nothing stale), and it is **never the
record of what exists**: code keeps the hard facts outside it, from tool results.

**Typed, then markdown.** `checkpoint_update(done, doing, next, decisions)` takes four lists of
at most 8 items of at most 160 characters; past either, `bad_arguments` and nothing changes. It
is on the worker's closed list and offered to no other occasion (`ToolScope.worker`). Its
docstring says to call it at each step boundary and says nothing of the run's limits. The
lists are stored on the job row (`compute_jobs.checkpoint` JSONB, `checkpoint_at`), written by
code on each update and held in memory beside the rounds. One renderer
(`nora.language.checkpoint_render`) turns them into compact markdown in a self-describing
`<subagent_checkpoint job="..." updated="...">` block: **Done / Doing / Next / Decisions** are the
worker's words, unchecked, and **Outputs** are code's.

**What code records.** The documents are the ids of `document_publish` results that came back
`ok` (a conflict result carries an id and is not one); the kept files are the paths of
`computer_export` `to="keep"` results that came back `ok`, stored under a `kept_files` key only
code writes; the rounds are counted by a hook. A checkpoint that names a document code does not
have is shown as the worker's words and produces no output line.

**Where it is used.**

| reader | what it gets |
|---|---|
| the `<subagents>` block (§7), a running job | Doing and Next only, three items each, cut; from the worker's memory, else the row |
| `subagent_read` | everything, with Outputs and the rounds |
| the worker after its history trims | everything and Outputs, no rounds, re-anchored into the pinned first message |
| the delivery to the room | where a job that **stopped** had got to, plus the kept files; a job that finished delivers its report, which has the same three sections |

**Compaction.** The worker's history is a Strands `SlidingWindowConversationManager` (40
messages, `per_turn=True` so it trims before every model call, the brief pinned with
`pin_first=1`) instead of `NullConversationManager`. After the window has trimmed anything, a
`BeforeModelCallEvent` callback in `SubagentProgress` replaces an anchor block in the pinned
first message with the latest rendered checkpoint and outputs, whenever either moves. Nothing is
anchored before the first trim.

**The report.** The doctrine asks the worker to keep its place with `checkpoint_update` at step
boundaries and to end with **Done**, **Not done** (with the reason) and **Decisions**. It still
states no limit.

**Old rows.** Both columns are NULL on every row from before and on a job that wrote none; every
reader renders that as nothing. A running job from before the build has no tool and no anchor;
its worker simply carries on as it did.

## 5. Bounds: turns, tokens, time; never per tool

DEC-247, applied to the sub-agent. Its `DreamGuard` is built with
`per_tool_ceilings=False`, every tool budget at 0, and no memory or pass-wide
budget, exactly cognition's `_guard` (`cognition_agent_runner.py`). What the
guard keeps is the safety half: R-7 (a tool it does not hold), R-99 (an
argument a tool does not declare), and the result size cap.

The bounds are Strands' own `limits=` (`turns`, `total_tokens`,
`output_tokens`) plus a wall clock, in a `[dreamer.budgets.subagent]` block
(the `DreamBudgetConfig` class). Start at cognition's numbers (200 turns,
10,240,000 total tokens, 120,000 output tokens, 3600 s) and size them on
traces, never on one sample. Two load-time checks, mirroring cognition's
(`nora/config.py`, `_cognition_has_no_call_ceilings` and
`_cognition_sessions_need_their_own_budget`):

- the block refuses `max_memory_calls`, `max_tool_calls` and `tool_calls`;
- enabling the sub-agent refuses to load without the block.

The limits are enforced in code and never written into its prompt: stating a
cap in words cost another case 5/5 to 1/5 (`nora/config.py`, the
`max_memory_calls` comment in `DreamBudgetConfig`). A limit stop (`stop_reason` in
`LIMIT_STOP_REASONS`, `nora/domain/stimuli.py`) or a timeout ends the job
`failed` with the reason, counted by a metric, and the room is told.

The tag turn is unchanged: it keeps its per-tool allowances and `[llm.limits]`.

## 6. The outbox: what changes

The port, store, poller, fire-path delivery and boot recovery all exist
(`nora/domain/ports.py` `ComputeBackend`, `nora/domain/compute.py`,
`nora/app/compute_poller.py`, `nora/app/fire.py` `compute_occasion`). Nine
gaps, all confirmed in code:

1. **One backend.** Composition holds a single `compute_backend`, the poller
   takes one, and the table has no column naming its worker. Add `kind`
   (`subagent` now, `video` later) to the row; the poller resolves the backend
   by kind. A row from before the column is `kind = 'legacy'` and is already
   terminal.
2. **The result is a summary plus a URL.** `JobResult` gains the worker's
   report text and a list of `document_id`s. Delivery renders the ids from the
   tool results the worker actually got back, not from its prose: the
   no-self-attestation rule, and the failure in §1.
3. **The wording says "build".** `ComputeHeader` (`nora/domain/framing.py`)
   says the person "asked you to build something"; `ComputeBody` renders a
   summary and one link. Both become kind-neutral: what was asked, what came
   back, what did not finish.
4. **`launch` has no options.** `launch(task, artifact_id, context)` gains a
   typed per-kind payload (objective, inputs, document ids) and the room
   facts (§4).
5. **R-100 would strip the links.** The delivery is a fire turn with
   documents wired, so the link carrier is armed, and a document link it was
   not handed in that turn is removed (`nora/agent/links.py`). The ids in
   `ComputeBody` alone would arrive as text with their links stripped.
   Delivery seeds the carrier (`mint_document`) from code's read-back of each
   `document_id` before the fire turn composes.
6. **States.** `ck_compute_jobs_state` allows `pending`, `firing`, `done`,
   `failed` only (`nora/infra/sql/schema.py`). `cancelled` is a new state;
   `lost` and each limit stop are failure reasons in a new column, not
   states.
7. **No cancel.** `ComputeBackend` has `launch` and `poll` only;
   `subagent_cancel` needs `cancel(job_id)`.
8. **No progress source.** `poll` returns status, url, summary and error, so
   `subagent_read`'s rounds used and last tool have nowhere to come from. The
   in-process backend keeps them in memory per running job (a hook counts
   rounds and records the last tool name); reading Langfuse at runtime is not
   a dependency Nora takes.
9. **No link to the launching turn.** The row gains `tag_id` for §8's trace
   link.

**A restart loses a running job.** `recover()` already fails every pending
row at boot and the room is told (`lost_job_occasion`). That stays: resuming
would mean saving the worker's conversation, a later step if lost jobs prove
common.

**The name.** `compute_jobs` was Smith's; the rows are now any deferred job.
Renaming it (e.g. `deferred_jobs`) is optional and its own migration; it is
not needed for step 1.

## 7. Reporting: once, at the end

The sub-agent reports when it ends, never mid-job (operator, 2026-10-04).
Between launch and delivery Nora answers "how is it going" from
`subagent_read`: state, age, rounds used, and the last tool it called, read
from the job and its trace, without touching the worker. Delivery follows the
existing rule: the poller waits for the room's slot, claims the row, and Nora
writes the message herself through `FirePath.speak`.

**The `<subagents>` block (operator, 2026-10-05; DEC-284).** Nora also sees, on every TAG turn
and without asking, a `<subagents>` block of this room's jobs: the running ones with their
rounds and last tool, and the ones finished in the last 24 hours with outcome, age, the start
of the report, the documents and whether the room was already told. Code builds it fresh from
`ComputeJobStore.room_jobs` and `SubagentBackend.status`; no model writes it. It is not carried
by Standing because Standing is a model's fold and a fold drops an open plan, and a job that is still running is exactly an open plan. It is absent
under `[subagent] enabled = false` and when the room has no job in the window, and it is never
rendered on a fire turn, a Dreamer pass or a cognition session. `subagent_read` stays the way
to the whole report.

## 8. What is measured

- **Read-back.** Each delivered `document_id` is read back by code before the
  message is composed; a missing one is reported missing, never linked.
- **Traces.** A `subagent` trace per job in Langfuse, linked to the launching
  tag's trace by `tag_id`, with `nora.subagent.id`, `kind`, outcome and limit
  hit.
- **Counters.** Jobs started, finished by outcome (`done`, `failed`,
  `limit_turns`, `limit_total_tokens`, `limit_output_tokens`, `timeout`,
  `cancelled`, `lost`).
- **The case in §1** becomes an eval case: an archive of sheets, objective
  given; pass is N documents published and read back in one job, and one
  delivered message.
- **Reconsider** the flat bounds if traces show a runaway job the bounds did
  not stop, or limit stops are frequent (DEC-247's own condition).

## 9. Later, not now

- **`subagent_send(id, message)`: Nora to a running worker, one way.** A
  per-job inbox in memory; a Strands hook drains it before each model call
  and appends the message as a user turn. No waiting state, no pause, no
  change to the wall clock, because the worker never waits for an answer. Its
  acknowledgement shows only in the final report. Until then, a mid-job
  correction is `subagent_cancel` plus a new job.
- **Two-way questions (the worker asks the room) and agent teams.** Out of
  scope (operator, 2026-10-04): that is a waiting state, a clock that pauses,
  and routing an answer back, a different order of work.
- **Surviving a restart.** Persist the worker's conversation and resume.
- **The video worker** (design 33) on the same outbox, once `kind` exists.

## 10. Open questions

- **The two rulings in §4**: launch counts as "asked" for documents; the
  worker's own lease key and folder.
- **The browser worker.** `design/sandbox-runtime/00-design.md` §14 plans
  `computer_browse` as a Strands sub-agent and reserves `path="subagent"` for
  it. Proposed: it is a kind of this worker (synchronous, inside a tag turn,
  browser tools only) sharing the runner, guard and actor, so the two do not
  drift.
- **Model.** Nora's alias or a cheaper one; measured on the §8 case.
- **One workspace.** The worker and a tag turn in the same room share the room's workspace; only the job folder `projects/<id>/` separates them, by convention, not by enforcement.
- **How many at once.** One running sub-agent per room, or more.
- **Who may launch.** Any member who can tag her, or a policy row.

## 11. Build order

1. Outbox: `kind`, `tag_id`, failure reason and the `cancelled` state;
   `JobResult` report and `document_id`s; kind-neutral
   `ComputeHeader`/`ComputeBody`; delivery seeds the link carrier; poller
   resolves by kind.
2. Worker: the `subagent` actor and its migration, its tool source, runner,
   doctrine, `[dreamer.budgets.subagent]` with its two load-time checks,
   in-process `ComputeBackend` adapter with `cancel`.
3. Tools: `subagent_create`, `subagent_list`, `subagent_read`,
   `subagent_cancel`, through `nora-tool` (ADR 0015), with the routing
   measurement.
4. The §1 eval case, then live.

Each step lands with a DEC row (`nora-decision`); step 1's names the
supersession of issue #22. Step 2's amends DEC-247, whose text keeps every
other adaptor's guards unchanged and whose
`test_every_other_adaptor_keeps_its_per_tool_ceiling` enforces it, and
records the §4 rulings.

**Step 2 as built (DEC-282).** Off by default (`[subagent] enabled = false`). Three places
differ from the text above, none of them a reversal:

- the job folder `projects/<id>/` is named by the backend's job id (the row does not exist
  until `launch` returns), and it is a convention the brief states plus the worker's own lease
  key, not a rewrite of every path argument: `computer_import` writes to `inbox/`, which a
  rewritten `computer_read` could not then find;
- the trace id travels on `JobResult.trace_id` and `mark` writes it to `compute_jobs.trace_id`
  when the job is delivered; while running it is held in memory beside the rounds and last tool
  (`SubagentBackend.status`);
- the worker's model runs at `[subagent] reasoning_effort = "low"` (operator, 2026-10-04), on
  the Dreamer's model with `ReasoningStripWatch` wired;
- enabling the sub-agent requires `[compute] enabled` and `[dreamer] enabled` at load rather
  than implying the poller.

**Step 3 as built (DEC-283).** The four tools, tag turn only, withheld unless `[subagent]
enabled`. Three places differ from the text above, none of them a reversal:

- the result status is `started`, a new declared extension beside `already_present` (work
  running, no result yet), because `ok` reads as done;
- `subagent_cancel` claims the row with the poller's own compare-and-set before it stops the
  worker, so a job being delivered at that instant is never also cancelled, and a second cancel
  answers `already_present`;
- the tools reach the worker through `nora.agent.subagent_desk.SubagentDesk`, which the
  composition attaches the backend to after the registry is built (the backend's tool list is
  built from the registry), and `ComputeJobStore.room_jobs` is the one new store read.

The routing of `subagent_create` against a reply is not yet measured on the live model: five
`tool_selection` cases are in the corpus, the positives for the sub-agent-on arm only.

**Step 6 as built (DEC-285).** The checkpoint of §4d. Three places differ from the text above,
none of them a reversal:

- a worker knows its backend job id, never the row's (the row is written after `launch`
  returns), so `ComputeJobStore.set_checkpoint` finds the still-pending row by `backend_job_id`;
  an update that comes before the row exists stays in memory and the next one lands;
- a published document's title is not in `document_publish`'s result, so Outputs carry ids and
  the readers that can look a title up (the block, `subagent_read`, delivery) do; the worker's
  own anchor shows ids;
- `kept_files` lives inside the `checkpoint` JSONB under a key only code writes, rather than in
  a column of its own.
