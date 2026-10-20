---
type: overview
title: Start here
description: A connected map of Nora's sessionless social runtime, memory, cognition and persistent computer work.
tags: [overview, architecture, getting-started]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:28:42.613Z
sources:
  - id: openwiki-source-062d8cd4ca9712a96239bbd4
    resource: repo://nora/app/dispatch.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "codex", at: "2026-10-20T00:28:42.613Z" }
---

Nora is an experimental, self-hosted social AI agent built to participate in everyday life over time. Her continuity lives in persistent state around people, rooms and unfinished matters, beyond any individual exchange. Model inference and connected services can still be external.

This wiki explains how the implementation supports that framing. It does not assert that every capability is enabled, that a migration is complete or that model behaviour has passed a live evaluation. The source repository remains private; reading this site requires no repository access or account.

## Choose a question

| You want to understand… | Read |
| --- | --- |
| What makes “sessionless” meaningful | [Continuity beyond a session](concepts/continuity.md) |
| When Nora observes, thinks or speaks | [From observation to participation](architecture/ambient-cognition.md) |
| Where experience is stored and retrieved | [Records, memory and Persona](architecture/memory.md) |
| How work survives a speaking turn | [Persistent computer and browser work](workflows/computer.md) |
| What an operator must configure and verify | [Configure and operate Nora](operations/configuration.md) |
| How contributors change and measure the system | [Contribute and verify changes](operations/contributing.md) |

## Follow one interaction

Platform adapters normalize a message. Ingestion records it and determines admission before the dispatcher starts an engagement request. A tag can start a speaking turn; an eligible untagged message can join a participation window instead.

Context assembly reads scoped transcript and persistent state, then constructs the context for a fresh Strands agent. Tools add evidence or take governed actions. Delivery is explicit, and stored outcomes can later feed cognition and contextual learning.

Between interactions, durable stimuli can wake bounded cognition sessions. Working state, notes and scheduled occasions connect that work back to future turns. A persistent room workspace supplies a place for files and processes to survive. These paths share policy and state; they are not independent chatbots attached to each interface.

## Begin contributing

With repository access, start in a separate worktree. Install Python 3.12 and uv, run `uv sync --frozen --extra evals`, then the focused checks for the subsystem you are changing. Database and object-store tests require Docker. Model evaluation and production access are separate operator-owned paths.

Read current source and tests before relying on a design document. Keep credentials, personal messages, account identifiers and location histories out of contributions. The [contribution workflow](operations/contributing.md) explains the final gate and review path.

## Read the evidence correctly

OpenWiki maintains page Claims and source-version bookkeeping. That makes a proposition traceable; it does not make generated prose an independent proof of truth. The pages distinguish deterministic tests, model acceptance and deployment readback.

<!-- openwiki: broken internal link [/wiki/graph/] link "/wiki/graph/" is root-absolute, which no real consumer resolves against the repository root (not a coding agent reading the page, not GitHub's Markdown renderer, not a local viewer); use a path relative to this file instead. Fix the href or restore the target, then delete this comment. -->
Use the site's static search to find a concept or [explore the graph](/wiki/graph/) to follow relationships between pages. A public ask-agent service is not implemented. The reviewed Markdown can support that later without making the reader depend on inference today.
