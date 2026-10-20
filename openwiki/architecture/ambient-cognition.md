---
type: architecture
title: From observation to participation
description: How observations become durable stimuli, bounded cognition sessions and governed speaking occasions.
tags: [ingestion, cognition, participation, proactivity]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:40:47.765Z
sources:
  - id: openwiki-source-d40f41f81ec219c8ffd32493
    resource: repo://nora/app/cognition_session.py
  - id: openwiki-source-062d8cd4ca9712a96239bbd4
    resource: repo://nora/app/dispatch.py
  - id: openwiki-source-613ef58e2958d234dcf6ba67
    resource: repo://nora/app/fire.py
  - id: openwiki-source-ec8cef678835e5cd034ccc9f
    resource: repo://nora/app/participation.py
  - id: openwiki-source-27eadbd2050d27b47126921c
    resource: repo://nora/domain/stimuli.py
  - id: openwiki-source-9f7a259c2615cc5928d25ac3
    resource: repo://tests/unit/test_cognition_session_runner.py
generated: { by: "codex", at: "2026-10-20T00:40:47.765Z" }
---

Nora has several clocks. Receiving a message, thinking about an ongoing matter and speaking in a room are separate events. This separation lets her retain context without treating every observation as a request for a reply.

## Observation comes first

Platform adapters deliver normalized messages to `Dispatcher.on_message`. The ingestion pipeline runs before the dispatcher considers engagement. An admitted tag creates an engagement request and starts background execution of the speaking pipeline. An untagged message in a participating room is instead noted by the participation trigger; that message does not itself invoke the relevance model or the speaking agent.

```text
normalized message → ingestion → record and admission decision
                                    ├─ admitted tag → speaking turn
                                    └─ participating room → conversation window
                                                               ↓
                                                        optional relevance gate
                                                               ↓
                                                         speaking occasion
```

Participation waits for conversation windows rather than judging each message independently. When the optional relevance gate is wired, its verdict decides admission. Missing context, missing instructions, malformed responses and timeouts fail closed. With no gate wired, the trigger retains its pre-gate admission behaviour; the gate must not be described as mandatory for all deployments.

## Thinking between interactions

Durable stimuli give cognition work to revisit. The wake policy applies per-unit single flight, cooldown and failure backoff before starting work. A strong cue can cut a cooldown once under the policy's conditions; it cannot bypass failure backoff indefinitely.

A cognition session receives a grounded agenda, room labels, relevant records and a fixed time base. Its tools can maintain notes and other working state and arrange a later look or occasion. This is a bounded model session, not an endless autonomous loop. A wall-clock timeout or model limit is a failed session. Previously recorded writes remain recorded; unfinished stimuli are not silently treated as completed.

## Deciding now, acting later

A proactive reminder stores an earlier intent. When it becomes due, the fire path constructs an occasion in which the earlier self is the requester, rather than borrowing a human's authority. Current notes and context are read at action time. The occasion can still remain silent: scheduling a turn does not guarantee a message.

Explicit delivery and room policy remain downstream boundaries. Ambient processing can also involve transcription or media interpretation, so observing is not universally model-free.

## Verification and extension

`tests/unit/test_relevance_gate.py` exercises malformed responses, missing instructions and timeout paths. `tests/unit/test_cognition_session_runner.py` supplies a deterministic cognition tool sequence. These checks establish specific control-flow properties; they do not measure live conversational judgement.

Use the existing dispatch, stimulus and occasion paths when extending a trigger. A second scheduler that writes replies directly would bypass the shared admission and delivery model.

Continue with [memory ownership](memory.md), [continuity](../concepts/continuity.md) or [operating configuration](../operations/configuration.md).
