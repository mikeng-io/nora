---
type: design
title: "Adapters: Pi first, then ACP for the rest"
description: "Which harnesses, why Pi first, what each adapter must map, what to verify about each before building on it, and what a new adapter has to pass."
tags: [nora, agent-worker, adapters, pi, hermes, acp, claude-code, codex]
timestamp: '2026-10-25'
edges:
  - {dst: 10-worker-port, rel: realizes, provenance: asserted}
---

# 20 · Adapters

## 1. Order

| adapter | harness | why this position |
|---|---|---|
| **Pi** | Pi, the minimal coding agent | the operator's preference: small core tool set (read, write, edit, bash), built to be embedded and driven programmatically, and the same core set the computer tools copied (DEC-243's comment in `tests/architecture/test_tool_names.py`) |
| **ACP** | any harness that speaks the Agent Client Protocol, natively or through its published adapter (candidates: Hermes, Claude Code, Codex, Gemini CLI) | one adapter for many; the port is shaped on it ([10](10-worker-port.md) §1) |
| later, as needed | a harness with neither | its own adapter onto the five verbs |

## 2. What each adapter maps

| port | the adapter must provide |
|---|---|
| `start` | launching the harness inside the sandbox as a long-lived process in the scope's directory ([30](30-sandbox-port.md)), with its model pointed at the relay ([40](40-models-egress-permissions.md) §1) |
| `prompt` | sending the brief and turning the harness's own events into the port's typed updates |
| permission | routing the harness's approval requests to Nora's policy and the answer back; a harness with no approval hook runs with a tool allowlist instead, and says so in its result |
| `cancel` | stopping the session, then the process |
| `result` | the final message as claims; everything else is computed by the port from the stream and the sandbox ([10](10-worker-port.md) §4) |

## 3. Verify before building

This bundle does not assert any harness's current interface: harness
interfaces change faster than a design does. Step 2 of
[50](50-verification-and-migration.md) is a spike that reads, for Pi:

- its programmatic mode (an RPC or JSON mode over stdio, or an SDK), and
  whether it speaks ACP itself or through an adapter;
- how it is pointed at a custom model endpoint and key;
- whether it has a per-tool approval hook, or only an allowlist;
- what it writes outside the working directory (config, caches, session
  files), so the sandbox scope holds it.

The same four questions are answered for each ACP candidate before its
adapter is enabled. The answers go into the adapter's PR, not into this
bundle.

## 4. The contract every adapter passes

One shared contract suite, the house rule for ports (`tests/contract/`),
against a scripted harness and each real adapter: a session starts in its
scope and cannot write outside it; updates arrive typed and in order; a
permission request reaches the policy and its denial reaches the harness; a
limit cancels; a crash is `unavailable`, never an exception; the result's
observed fields match the sandbox, not the harness's claims.
