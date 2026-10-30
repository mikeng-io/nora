---
kind: design
title: "Nora — what her traces carry, and the four ways to change it"
description: "The group transcript reaches the trace exporter unredacted. What the SDK's own lever does, what it costs, and what it cannot reach. Open operator decision."
tags: [nora, design, telemetry, langfuse, privacy, strands]
timestamp: '2026-08-06'
status: open-decision
---

# Trace content and redaction

> **Historical SDK/content investigation (2026-08-06), retained as evidence.**
> The selected backend and current migration design live in
> [Observability](../docs/observability.md#accepted-direction-self-hosted-signoz)
> and [ADR 0034](../docs/adr/0034-self-hosted-signoz-unifies-application-and-llm-observability.md).
> Their acceptance includes readable content with the pinned SDK/backend; this
> does not select a new field-level redaction policy or authorize cloud export.
> The endpoint, storage and missing-collector statements below are historical,
> not an inventory of the current deployment.

**Status: this document does not describe a change. It describes a decision the
operator has to make**, and records the investigation so it is not repeated.
Everything below was verified against the installed `strands-agents==1.50.2`
source and by running its `Tracer` under each configuration, not read from
documentation.

## 1. What reaches the exporter today

Every tag emits an agent span and one `chat` span per model call. Between them
they carry:

| what | where it lands | redactable by the SDK lever? |
|---|---|---|
| the whole `<group_transcript>` — real names, real message text | `gen_ai.user.message` event, `content` | yes, under `gen_ai.input.messages` |
| the peer card and roster (also inside the prefill) | same | yes, same key |
| Nora's replies and her tool-call arguments | `gen_ai.assistant.message` / `gen_ai.choice` | yes, under `gen_ai.output.messages` |
| `read_buffer`'s returned rows (i.e. more group messages) | tool span, `tool.result` | yes — policed under `gen_ai.input.messages` |
| the system prompt on the `chat` span | `gen_ai.system.message` event | yes, under `gen_ai.system_instructions` |
| **the entire doctrine on the AGENT span** | plain `system_prompt` span attribute | **no — see §4** |
| `tag_id`, `conversation_id`, `doctrine_sha256` | span attributes | n/a — provenance only, no content (`nora.obs.tracing.trace_attributes`, and a test pins it) |

R-18 (`nora.agent.egress`) guards what reaches the GROUP. Nothing guards what
reaches the trace, and design/12 §3 already states the general form of this
mistake: *scope is enforced at assembly, never at egress*. By the time a span
exists the disclosure is already authorised.

## 2. Two corrections to the premise

Both matter, because they change how bad this is.

**The endpoint is a self-hosted Langfuse on the same host.** `config.toml` has
`exporter = "otlp_http"`, `endpoint = "http://127.0.0.1:3100/api/public/otel/v1/traces"`.
The transcript is not being shipped to a third party; it is being written to a
service on the same machine that already stores the same messages in
`data/messages.db`. The disclosure boundary is "whoever can read Langfuse",
which is a real boundary but not the one the phrase "shipping to Langfuse"
implies.

**Langfuse auto-detection is NOT active in this process.** `Tracer.is_langfuse`
(tracer.py:193) tests for the substring `langfuse` in the *environment*
variables `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
or `LANGFUSE_BASE_URL`. `nora.obs.tracing` builds `OTLPSpanExporter(endpoint=...)`
programmatically and never sets any of them, so `is_langfuse` is False, and
therefore `_span_attributes_only` is False. Message content is emitted as span
EVENTS rather than as `gen_ai.input.messages`/`gen_ai.output.messages` span
attributes. This changes nothing about what is transmitted — the events carry
the same bytes — but it does mean a Langfuse UI that reads input/output from
span attributes may already be showing less than the wire does. Anyone judging
"how much visibility would redaction cost" should look at what Langfuse
actually renders today before deciding.

## 3. The native lever, and exactly what it does

`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=<list>`
(tracer.py:127–133). It is an **allowlist**, so:

- token absent → `_redaction_enabled = False` → **everything unredacted.** This
  is today.
- token present with an empty value → redaction on, allowlist empty → all three
  sensitive keys become `[REDACTED]`.
- `...=gen_ai.output.messages` → outputs pass, inputs and system instructions
  are redacted.
- separator is `;`, and only a single TRAILING `*` is honoured
  (`gen_ai.output.*` works, `gen_ai.*.messages` matches nothing).

Observed, running the installed `Tracer` against a fake transcript:

```
               <unset> | redaction=False in='TRANSCRIPT' out='REPLY' sys='PERSONA'
gen_ai_unredacted_attributes= | redaction=True  in='[REDACTED]' out='[REDACTED]' sys='[REDACTED]'
           output only | redaction=True  in='[REDACTED]' out='REPLY'      sys='[REDACTED]'
```

The granularity is the problem. `gen_ai.input.messages` is one policy key
covering three different things: the group transcript, the arguments Nora
passes to a tool, and the results a tool returns. There is no way to redact the
transcript and keep the trajectory — turning it off removes both.

## 4. What the lever cannot reach

`Agent._start_agent_trace_span` (agent.py:1470) passes `system_prompt=self.system_prompt`
into `Tracer.start_agent_span` as a `**kwargs` extra, and `start_agent_span`
does `attributes.update({k: v for k, v in kwargs.items() if isinstance(v, (str, int, float, bool))})`.
So the doctrine lands on the agent span as a plain attribute literally named
`system_prompt`, never passing through `_redact`. Observed in all three
configurations above, including the fully-redacted one:

```
"attributes": { ..., "system_prompt": "PERSONA-SOUL-TEXT", ... }
```

The doctrine is Nora's own text rather than anyone's messages, so it is a
different kind of disclosure from the transcript — but it is worth knowing that
"turn on redaction" does not mean "the span carries no prompt".

## 5. The four options, with costs

**A. Native lever, redact everything.**
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=` in
`docker-compose.yml`. One line, no code, no new service.
*Cost:* every trace becomes structure-only — span tree, tool NAMES, token
counts, latency, `tag_id`, `conversation_id`. You keep "she called `now` then
`send_reply` and it took 9s"; you lose every string. Evals that judge output
text (`evals/judge.py`) do not read traces, so they are unaffected — but a
human debugging "why did she answer that" has nothing to read. Does not redact
`system_prompt` (§4).

**B. Native lever, outputs only.**
`...=gen_ai.output.messages`. Best fit for the stated concern: what Nora says
is already in the room, so tracing it discloses nothing new; what the GROUP
says is what should not be mirrored anywhere else.
*Cost:* you lose tool RESULTS as well as the transcript, because `tool.result`
is policed under the input key — so `read_buffer`'s rows disappear, and a
trace showing "she called `read_buffer` then said X" cannot show what she read.
Also does not redact `system_prompt`.

**C. Collector-level masking.** Put an OTel Collector between Nora and
Langfuse with a `transform`/`redaction` processor. Only option that gives
per-field, regex-level control — strip E.164 numbers and ACIs (R-18's own
patterns) while keeping the text, drop the `system_prompt` attribute
specifically, keep tool results.
*Cost:* a new always-on service in the compose project, plus its config,
plus the failure mode where a misconfigured processor silently passes
everything (an R-5 shape: indistinguishable from working). This is the only
option that both preserves trace usefulness AND covers §4.

**D. Langfuse SDK `mask=`.** Not reachable from here without a rewrite:
`mask=` is a feature of the Langfuse Python client, and Nora exports through
the raw OTel SDK (`OTLPSpanExporter`), not through Langfuse's client. Adopting
it means a new dependency and a second export path. Listed only so it is not
re-proposed as a cheap alternative — it is not cheap, and it buys roughly what
C buys.

## 6. Recommendation

**B if a decision is needed today, C if traces are meant to stay useful.**

B is one line, matches the threat model (the group's words, not Nora's, are
the sensitive half), and is trivially reversible. It costs tool-result
visibility, which is the part worth arguing about.

But the honest answer is that the native lever is too blunt to be the end
state: it cannot separate "the group's transcript" from "what a tool
returned", and it cannot touch the doctrine attribute at all. If traces are
load-bearing for evaluation, C is the option that actually does what is
wanted, and its cost is operational rather than informational.

Doing nothing is also defensible **given §2** — a self-hosted collector on
`127.0.0.1` holding messages that are already in `data/messages.db` on the same
disk. It stops being defensible the moment `endpoint` points anywhere off-host,
and there is nothing in the code that would notice that change.
