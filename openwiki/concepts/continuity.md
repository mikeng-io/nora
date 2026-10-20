---
type: concept
title: Continuity beyond a session
description: Why Nora's continuity lives in persistent social state rather than an indefinitely open model conversation.
tags: [continuity, sessions, social-agent, learning]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:28:42.613Z
sources:
  - id: openwiki-source-7aa209ee4f993345d7092214
    resource: repo://config.example.toml
  - id: openwiki-source-30349f3247150dbe36ca063a
    resource: repo://nora/agent/factory.py
  - id: openwiki-source-f732aae31c1f25710cf2df5f
    resource: repo://nora/engagement/context.py
  - id: openwiki-source-125d0edfa9f40761d4c3b9ae
    resource: repo://nora/language/culture_render.py
generated: { by: "codex", at: "2026-10-20T00:28:42.613Z" }
---

Nora is a self-hosted social AI agent whose continuity belongs to persistent state around people, rooms and ongoing matters. Messaging, voice and computer work are interfaces into that continuity. The distinguishing architecture is the connection between these systems, not the number of tools attached to a chatbot.

## What sessionless means

Each speaking turn constructs a fresh Strands agent with an assembled prefill, a null conversation manager and no SDK session manager. Previous context is deliberately read and rendered; it is not recovered by resuming one global SDK conversation.

“Sessionless” therefore means continuity independent of a chat session. Bounded sessions still exist inside the implementation: a speaking invocation, a cognition run or a browser task has its own lifecycle and limits. Persistent memory does not imply that the model runs continuously.

Consider this **synthetic example**: a room discusses an outing, later changes its time and then returns to the plan after a quiet interval. The relevant continuity may be in records, a maintained note, an event and a scheduled occasion. A subsequent turn reconstructs what matters from current state. This example illustrates the mechanism; it is not a measured transcript or a guarantee that every deployment will handle the situation correctly.

## How context returns

`TranscriptInjection.assemble` resolves the speaker, reads scoped state and freezes a `TagContext`. It gathers transcript, memory, notes, standing context, presence, culture or Persona views, lessons and computer/task state. Naming and media enrichment happen before the final context is assembled.

This makes state ownership visible. The speaking agent consumes the result; it does not privately own the whole history. A new interface should reuse the same scoped state and policy instead of creating a parallel memory island.

## Social participation and initiative

Ambient ingestion can preserve an observation without speaking. Participation can wait for a conversation window and consult the relevance gate. Cognition can maintain an ongoing matter and arrange another look. A later proactive occasion re-reads the situation and can choose silence.

These are distinct admission paths into shared context and delivery. “Social” does not grant access to every room: unit boundaries, room grants, membership and disclosure checks still constrain what Nora may read or say.

## Learning changes context

Response episodes connect Nora's messages with later replies, reactions or silence. Optional lessons sessions use closed episodes to write evidence-backed lessons; a separate render switch determines whether those lessons enter future prompts. Her own account can also describe her behaviour within a unit, subject to the room's permission to see it.

This is contextual learning. It changes stored accounts and rendered context, not model weights or human-authored doctrine. An episode containing silence is an observation to interpret, not automatic evidence of success or failure.

Self-hosting concerns the runtime and state under the operator's control. Configured model providers may still perform inference externally. Capability implementation, deployment enablement and measured effectiveness are three separate facts.

Follow [the observation-to-action flow](../architecture/ambient-cognition.md), [memory ownership](../architecture/memory.md) and [persistent computer work](../workflows/computer.md).
