---
type: design
title: "The worker port: a session, shaped on ACP, called only by the sub-agent"
description: "The port's verbs and their semantics (start, prompt, updates, permission, cancel, result), why ACP's session model is the shape, how the sub-agent calls it with one tool, what comes back (claims apart from observed outputs), and the room rule."
tags: [nora, agent-worker, port, acp, session, permissions, checkpoint]
timestamp: '2026-10-25'
edges:
  - {dst: 30-sandbox-port, rel: depends_on, provenance: asserted}
  - {dst: ../subagent/00-design.md, rel: extends, provenance: asserted}
---

# 10 · The worker port

## 1. Why ACP's shape

The Agent Client Protocol is a JSON-RPC protocol over stdio for a client
driving a coding agent: a client starts a session in a working directory,
sends prompts, receives a stream of updates (the agent's plan, its tool calls
and their results, file diffs), answers the agent's permission requests, and
can cancel. That is exactly the relationship here, with Nora's sub-agent as
the client. Shaping the port on it means an ACP-speaking harness needs no
adapter of its own, and a harness that does not speak ACP needs an adapter
that maps onto five verbs.

## 2. The verbs

```
start(scope, brief, limits, policy)   -> session     # in the room's workspace
prompt(session, text)                 -> stream of updates, ending in a stop
cancel(session)                       -> None        # idempotent
result(session)                       -> WorkerResult
```

- **updates** are typed: `plan`, `tool_call` (name, arguments, status),
  `tool_result`, `diff` (path and hunks), `message` (the worker's prose),
  `permission_request`, `stop` (reason).
- **permission requests** are answered by Nora's policy in code, never by a
  model and never by a person mid-run ([40](40-models-egress-permissions.md)
  §3). An unanswerable request is denied with its reason, and the worker
  continues or stops on its own.
- **limits** are the sub-agent's (turns, tokens, wall clock: DEC-247 as the
  sub-agent design applies it), enforced by the adapter by cancelling, not by
  asking the worker to stop.
- **NEVER raises**, the `ComputeBackend` and `SandboxRuntime` rule: a harness
  that will not start, a sandbox that is down, a protocol error are a typed
  `unavailable` result.

## 3. The one caller

The sub-agent gets **one tool** that runs a worker session to its stop and
returns the result: **`coding_task_run`**. It meets ADR 0015's name rule as written
(`run` is an allowed verb, `tests/architecture/test_tool_names.py:43`), so no
verb is added. `computer_delegate` was rejected because `delegate` is not an
allowed verb, and ADR 0015 §«planned tools» applies to working names too.
Its description and routing are measured under ADR 0015
([50](50-verification-and-migration.md) §2). A tag turn does not get
it: long work already goes through `subagent_create`, so there is one
delegation path, not two. The worker inherits the sub-agent's room: its
`scope` is derived by code from the job's room through the existing unit
resolver, `computer_unit_scope(unit)` (`nora/agent/tools.py:14585`), never
named by a model. That is the one workspace the unit's native computer tools
and staged inputs already share, so a worker sees the same files as
`computer_run`.

## 4. What comes back

`WorkerResult` keeps the split `nora/language/checkpoint_render.py` already
makes between a worker's **claims** and **outputs code observed**:

| part | source | trusted as |
|---|---|---|
| `summary`, `done`, `next` | the worker's own final message | its claims, rendered as such |
| `files_changed` | a content manifest of the scope before and after, by code: path, size and SHA-256 of every file, dot-directories included, with no entry cap. An over-budget manifest reports `files_changed: unknown` | observed |
| `commands` | the `tool_call` updates of kind command, with exit codes | observed |
| `tests` | a command the brief named as the check, its exit code | observed |
| `rounds`, `tokens`, `seconds` | counted by the adapter | observed |
| `stop` | the stop reason: done, limit, cancelled, error | observed |

A claim never becomes an output: "all tests pass" is a claim until the named
check's exit code says so.

**Why not the file listing.** `files` returns path and size only, skips
dot-directories and truncates (`deploy/computer/computerd.py:434`). An edit
from `x = 1` to `x = 2` leaves the listing identical. So `files_changed` needs
the content manifest above, a new computerd read (step 1). Until it exists,
the field is not reported.

**The record.** The whole stream is kept as the session's record. Making it a
citable `turn(...)` depends on the standing ledger's turn-record view,
citation validation and reader
([standing-ledger 50](../standing-ledger/50-migration.md)), which do not
exist yet and define no worker-session occasion. Until they ship, the record
is kept and readable by the operator, and is **not** citable. Making it
citable is step 6 ([50](50-verification-and-migration.md) §4), and its
disclosure follows the ledger's per-item rules.
