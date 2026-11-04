---
type: design
title: "The sandbox port: a long-lived streamed process, and the shape other VMs share"
description: "What SandboxRuntime lacks for a worker, the one verb it gains (spawn, streamed over a websocket as /v1/browser already is), what stays, and the provider-neutral contract that lets another VM be an adapter later."
tags: [nora, sandbox, sbx, computerd, port, spawn, websocket, providers]
timestamp: '2026-10-25'
edges:
  - {dst: ../sandbox-runtime/00-design.md, rel: amends, provenance: asserted}
---

# 30 · The sandbox port

## 1. What is missing

`SandboxRuntime.run` runs one command and waits for it, capped at 900
seconds, and returns its output when it ends (`deploy/computer/computerd.py`).
A harness is a process that lives for the whole session and talks over stdio
the whole time. Nothing in the port can hold one.

## 2. The verb it gains

```
spawn(scope, argv, env, limits) -> Process
  Process.send(bytes)           # its stdin
  Process.receive() -> bytes    # its stdout, streamed; stderr on a second channel
  Process.wait() -> exit code
  Process.kill()
```

- **computerd serves it as a websocket**, the way it already serves the
  browser's CDP connection (`/v1/browser`): one frame per chunk, each way. No
  new transport kind.
- **Bound to the job.** A spawned process is killed when its session is
  cancelled, its limits are reached, or the job ends; computerd keeps a table
  of what it spawned per scope, so `state` reports it and a restart of
  computerd kills them.
- **Scoped** like every other verb: the working directory is the scope's, and
  `env` is filtered by computerd (no variable the port did not pass).
- **NEVER raises**: a refused spawn, a dead socket, a killed process are typed
  results.

Everything else (`run`, `read`, `write_text`, `edit`, `files`, `fetch`,
`put`, `state`) stays as it is; the native computer tools keep using them.

## 3. The provider-neutral contract

The operator's later step: the VM becomes switchable (another microVM
service, a hosted sandbox). What a provider must give for the port:

| capability | used for |
|---|---|
| a persistent workspace per scope | the room's files survive between sessions |
| exec with streamed stdio | `spawn` |
| file read and write | the existing verbs |
| an egress policy Nora sets | [40](40-models-egress-permissions.md) §2 |
| lifecycle: create, stop, delete, health | the supervisor (`nora-computer.service` today) |

`tests/contract/test_sandbox_runtime_contract.py` already holds the fake and
computerd to one suite; `spawn` joins it, and a new provider is an adapter
that passes it. Only the sbx adapter ships in this bundle.

## 4. Scoped VM routing (2026-11-02; NOT BUILT)

[Issue #520](https://github.com/mikeng-io/nora/issues/520) includes the
[current scoped browser contract](../browser-runtime/00-design.md#current-target--scoped-anonymous-browser-2026-11-02).
One policy unit or canonical person+anchor-unit DM scope resolves one microVM;
Pi first and ACP later run inside the same VM used by that scope's native
computer tools. Resolve scope once from the admitted job and carry it through
spawn, file/state operations, process handles, cancellation, browser tabs and
management/CDP routing. A worker never selects its own VM or falls back to the
shared computer. Nora/subagent identities, external browser loop and model relay
remain as designed. This changes VM cardinality, not the later-provider boundary.

The existing `job:<id>` lease does not exclude a unit turn or another job.
Browser task ownership must isolate tabs and serialize operations on the same
tab for both native and worker paths; workspace filesystem exclusion is not
newly promised. Routing/negative/concurrency and migration acceptance are in the
linked browser contract and join the existing contract/corpus checks in
[50](50-verification-and-migration.md). A process handle from another scope or
VM instance is refused. Resource admission failure is unavailable, never a
shared-VM fallback. Do not replace the shipped unit migration or claim private-DM
prerequisites are complete. No worker or host operation follows from this doc.

### Scope-wide execution queue amendment (2026-11-04)

The [computer queue contract](../browser-runtime/00-design.md#per-scope-computer-queue-operator-scope-addition-2026-11-04)
serializes first-version execution segments per VM, across native tools and
worker jobs. Separate task tabs remain, but concurrent same-VM execution is
not required. Reuse Redis Streams infrastructure and existing durable job
delivery; do not mix computer commands into bridge streams. Worker child tools
inherit their admitted scope ownership instead of requeueing behind the parent.
The existing job-only lease is not sufficient. Stop/reconcile old VM processes
before a successor dispatches after ownership loss; queue recovery is not
permission to replay an uncertain side effect.
