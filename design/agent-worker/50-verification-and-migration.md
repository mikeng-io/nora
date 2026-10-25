---
type: design
title: "Verification and migration: the same tasks both ways, then one adapter at a time"
description: "A task corpus run through the native computer tools and through the worker (success by a check's exit code, rounds, tokens, wall clock), the routing measurement for the new tool, the negatives, and five steps from spawn to the ACP adapter."
tags: [nora, agent-worker, verification, migration, pi, acp]
timestamp: '2026-10-25'
edges:
  - {dst: 10-worker-port, rel: measures, provenance: asserted}
---

# 50 · Verification and migration

## 1. The same tasks, both ways

A corpus of computer tasks with a **check** each (a command whose exit code
says done: tests pass, a file builds, an output matches): small ones (fix one
function), medium (add a feature across three files), long (build a small tool
from a description). Each runs through the sub-agent with the native computer
tools and through the sub-agent with the worker, on the same model and limits.

| measure | read from |
|---|---|
| success | the check's exit code, never the worker's claim |
| rounds, tokens, wall clock | counted by the sub-agent and the adapter |
| stop reasons | the result |

The worker ships as the default for multi-step work only if it wins on
success at no worse cost on medium and long tasks. Small tasks are expected to
stay native, which is [00](00-intent.md) §3's split, measured rather than
assumed.

## 2. Routing

`computer_delegate` is a new tool on the sub-agent's surface, so its name,
level and description follow ADR 0015, and `tool_selection` cases put it
beside `computer_run`, `computer_edit` and `computer_write` in `not_tools`
both ways (a one-line fix must not be delegated, a multi-file feature must).

## 3. The negatives

- No provider key inside the VM, read back by listing the worker's
  environment and config files in a session.
- No egress to any private address but the relay's (`egress check`).
- A write outside the scope, a push, an upload, a secret request: each denied,
  and the denial in the record.
- A limit reached: the session is cancelled and the process killed, and
  `state` shows nothing left running.
- A claim of success with a failing check reports failure.

## 4. Steps

| step | ships | gate |
|---|---|---|
| **1. `spawn`** | the verb in the port, computerd's websocket, the fake, the contract suite | the contract suite on the fake and on computerd |
| **2. Pi spike** | no production code: the four questions of [20](20-adapters.md) §3 answered for Pi, inside the VM | a written read-back |
| **3. The relay and its egress rule** | the per-session token relay, its model allowlist and budget; the one allow rule | the key and egress negatives (§3) |
| **4. The Pi adapter and the tool** | the adapter behind a `[subagent]` flag, `computer_delegate` on the sub-agent's surface, the permission policy | the adapter contract; the task corpus (§1); `tool_selection` (§2) |
| **5. The ACP adapter** | one adapter, enabled per harness after its four questions are answered | the adapter contract per harness; the task corpus for any harness offered as a default |

The VM provider adapters are not steps of this bundle; [30](30-sandbox-port.md)
§3 is the contract they will meet.

## Open

| question | who decides |
|---|---|
| which models the relay allows a worker, and their budget | the operator |
| whether a worker session may outlive one sub-agent job (resume a workspace's session later) | measured after step 4 |
