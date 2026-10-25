---
type: design
title: "Intent: Nora directs computer work; a harness built for it does it"
description: "What runs today, where the reinvention is, the layering the operator set (Nora, her sub-agent, a worker that is not Nora), and what stays native."
tags: [nora, agent-worker, computer-use, harness, pi, hermes, acp, subagent]
timestamp: '2026-10-25'
edges:
  - {dst: 10-worker-port, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. What runs today

- **The computer** ([sandbox-runtime](../sandbox-runtime/00-design.md)): one
  persistent sbx microVM serving computerd, Nora its only client, through the
  `SandboxRuntime` port (`nora/domain/ports.py`): `run` (a command, capped at
  900 seconds, `MAX_RUN_SECONDS` in `deploy/computer/computerd.py`), `read`,
  `write_text`, `edit`, `files`, `fetch`, `put`, `state`, each scoped by
  `computer_scope(platform, conversation_id)`.
- **The sub-agent** ([subagent](../subagent/00-design.md)): `subagent_create`
  starts a background Strands agent (`SubagentBackend`,
  `nora/app/subagent_backend.py`, a `ComputeBackend`) with a closed tool
  surface that includes the computer tools, reporting once through the
  compute-job outbox.

So a coding task of thirty steps is thirty model rounds of Nora or her
sub-agent, each one a tool call across the VM boundary, with Nora's own tool
loop as the harness. That loop is general-purpose; coding harnesses are built
for this one job (planning, editing with diffs, running tests, recovering from
a failed command) and improve faster than Nora's copy of them would.

## 2. The layering

| layer | is | does |
|---|---|---|
| **Nora** (a turn) | herself | decides what to do; talks to the room |
| **her sub-agent** | still Nora, behind the scenes | holds the objective; reads, plans, reports |
| **the worker** | **not Nora**: Pi, Hermes, another harness | does the computer work inside the sandbox |

The worker never speaks to a room, holds no memory of people, and sees only
the brief the sub-agent writes and the workspace it is given. Its result comes
back to the sub-agent, which reports to Nora's room as today.

## 3. What stays native

The `computer_*` tools stay for single operations (read one file, run one
command), where a worker session's start-up would cost more than the work.
The worker is for multi-step work. Which one a task gets is the sub-agent's
choice, measured ([50](50-verification-and-migration.md) §1).

## 4. Two ports, both switchable

- **The worker port** ([10](10-worker-port.md)), with a Pi adapter first and an
  ACP adapter for any harness that speaks the Agent Client Protocol
  ([20](20-adapters.md)).
- **The sandbox port** ([30](30-sandbox-port.md)): today's `SandboxRuntime`
  plus a long-lived streamed process, which the worker needs, and which is the
  shape other VM providers share. The operator wants the VM switchable "in the
  future, not right now": this bundle fixes the port so it can be, and ships
  only the sbx adapter.
