---
kind: design
title: "Nora — Strands Agents SDK Reuse Map"
description: "Which Strands built-in primitives replace which hand-rolled wheels: agent loop, tools, conversation management, hooks/plugins, MCP, model providers — and how sessionless-per-tag is expressed in SDK terms."
tags: [nora, design, strands, reuse, sdk, tools, hooks, mcp]
timestamp: '2026-08-03'
edges:
  - {dst: ../00-overview.md, rel: decided_by, provenance: asserted}
  - {dst: ../02-tagging-path.md, rel: used_by, provenance: asserted}
---

# Nora — Strands Agents SDK Reuse Map

## Principle

**Reuse, don't rebuild.** Everything the Strands SDK already provides is used as-is.
Nora's own code is limited to: platform adapters (Signal), store adapters (SQLite,
Honcho), the persona prompt, and configuration. No hand-rolled agent loop, no
hand-rolled context builder, no hand-rolled session store.

## The Reuse Map

| Nora need | Strands built-in | Hand-written code |
|---|---|---|
| Agent loop (reason → tool → loop) | `Agent(model, tools, system_prompt)` | **none** |
| Chat turns / context window | `ConversationManager` (SlidingWindow default; Null for stateless-per-tag) | **none** (choose manager) |
| Skills | **Plugins** — bundle hooks + tools + config, auto-discovery, tool registration | each skill = one `Plugin` class |
| Tools | `@tool` decorator; `strands-agents-tools` (file_read, shell, calculator…); `./tools/` auto-load + hot-reload | only Signal + Honcho adapters |
| Connections | **MCP built-in** (client; thousands of pre-built servers) | Signal MCP server or `@tool` wrapper |
| Subagents / multi-agent | **Agent-as-Tool, Graph, Swarm** built in | **none** (future v2.1) |
| Guardrails / observation | **Hooks** — `BeforeToolCallEvent`, `interrupt()`, plugins | a few callbacks |
| Observability | OTel tracing built in — **enabled by default** (audit A5) | **none** (config toggle) |
| Model provider | `OpenAIModel(custom client)` → OmniRoute | 3 lines |
| Evaluation | `strands-agents-evals` (see `05-evaluation.md`) | eval cases + corpus |

## Verified Strands Facts (2026-08-03)

- **Package:** `strands-agents` v1.50.2, PyPI, Apache-2.0, Python ≥3.10 (host: 3.12.13).
- **Install:** `pip install 'strands-agents[openai]' strands-agents-tools`.
- **OpenAI provider accepts a custom client** — `OpenAIModel(model_id=..., client=AsyncOpenAI(api_key=..., base_url=...))` — caller owns lifecycle (`client.close()`). This is the OmniRoute integration point; no AWS/Bedrock involvement. **Client is a process-wide singleton**, shared across per-tag agent constructions (audit T2) — never rebuilt per tag.
- **Context pre-fill:** `Agent(messages=[...])` initializes conversation history — this is the injection mechanism for the tagging path.
- **State model:** conversation history (`agent.messages`), agent state, invocation state (`invocation_state` — per-run dict, not in model context).
- **Tools:** `@tool` on Python functions; docstring is the LLM-facing schema; tools can be loaded from file paths and auto-reloaded from `./tools/`.
- **Hooks:** typed lifecycle events (`BeforeInvocationEvent`, `BeforeToolCallEvent`, `AfterToolCallEvent`, interrupts); plugins bundle hooks + tools + config.
- **Multi-agent:** Agent-as-Tool, Graph, Swarm patterns available.
- **Evaluation:** `strands-agents-evals` on PyPI — OutputEvaluator, TrajectoryEvaluator, HelpfulnessEvaluator, CorrectnessEvaluator, deterministic evaluators, experiment management, tool simulation, red-team strategies. See `05-evaluation.md`.
- **Pin:** `strands-agents==1.50.2` pinned in `pyproject.toml`; upgrades manual and gated on the smoke test (audit A10).

## Sessionless-per-tag in SDK Terms

Decision D4 (00-overview) — stateless, fresh invocation per tag — maps to:

1. **Fresh `Agent` per tag.** Construct, invoke, discard. No `agent.messages` reuse.
2. **`ConversationManager` = Null** (or per-invocation only). No cross-tag history management.
3. **No Session Management / snapshots.** Strands offers persistence across restarts; we deliberately do not use it — our persistence is SQLite + Honcho, re-injected as pre-fill.
4. **`invocation_state`** may carry per-run metadata (conversation_id, sender_id); scoped to the single invocation, never persisted, never in model context.

## Tool Contracts

### read_buffer
- Signature: `read_buffer(conversation_id: str, conversation_type: str = "group", limit: int = 40) -> list[dict]`
- Reads recent messages from SQLite audit. Returns oldest-first rows.
- Docstring instructs the model: "Recent conversation transcript; use when context is thin."

### read_peer_card
- Signature: `read_peer_card(sender_id: str) -> list[str]`
- Resolves `sender_id` → `person_id` via the persons.yaml reverse index, then fetches
  the person's Honcho peer card (durable recall). Returns empty list for unmapped
  senders or when Honcho is unavailable.
- Docstring: "Durable facts about the person who tagged you."

### send_reply
- Signature: `send_reply(conversation_id: str, conversation_type: str, text: str) -> bool`
- signal-cli JSON-RPC `send`. **The only side-effect tool.**
- Guardrail (hook): at most one call per invocation; empty/error output → veto.
- **Delivery order: audit-first** (see `02-tagging-path.md §4 Return`) — the reply
  is recorded in SQLite before this tool runs; a failed send never loses context.

### feed_honcho
- Signature: `feed_honcho(sender_id: str, text: str) -> bool`
- Fire-and-forget to Honcho peer chat → Deriver. Never blocks the reply.

## Skills → Plugins Mapping

"Skills" in the Strands world are **Plugins**: a class bundling hooks, tools, and
config, with auto-discovery and tool registration. Nora's skills:

| Skill | Plugin contents |
|---|---|
| Group norms / persona behavior | system prompt fragment + prompt assembly |
| Deep recall (future) | hooks that enrich context on demand |
| External lookup (future, e.g. maps/restaurants) | MCP server or `@tool` wrapper |

Skills are additive: a new skill = a new Plugin class, not a change to the loop.

## Subagents (future, v2.1)

Strands' native Agent-as-Tool pattern covers "deeper work" without a custom build:
a sub-agent wrapped as a tool (e.g. a recall/research agent) that the main agent can
call. Not in v2 — noted so the architecture doesn't need to change to add it.

## MCP (future)

MCP client is built in. When Nora needs external tools (search, maps, etc.), wire a
pre-built MCP server or write a small one — no SDK changes, no new abstractions.

## Agent Factory Boundary (audit 2nd-pass F8/F15/F16)

Agent construction is isolated behind a thin factory so a Strands SDK API change
touches **one file**, not every tag invocation:

```python
# nora/agent_factory.py — the only module that imports Strands constructors
def create_model_provider(cfg) -> OpenAIModel: ...   # OpenAIModel + singleton AsyncOpenAI client
def create_agent(model_cfg, system_prompt, tools, pre_fill_messages) -> Agent: ...
def invoke(agent, tag_message) -> str: ...           # wraps agent.invoke_async
```

- `02-tagging-path.md` orchestration calls `factory.create_agent(...)` /
  `factory.invoke(...)` — never Strands constructors directly.
- If Strands renames `messages=` → `history=`, or `invoke_async` → `run`, only this
  file changes. Nora is still Strands-based (D3); the factory is a single-file
  coupling boundary, not an abstraction layer.

## What We Still Write (the honest list)

1. `listener.py` — SSE client for signal-cli (platform adapter; nothing to reuse).
2. `parser.py` — envelope → message record (platform adapter).
3. `db.py` — SQLite audit store (thin; standard `aiosqlite`).
4. `honcho.py` — Honcho REST client (store adapter).
5. `signal_send.py` — signal-cli JSON-RPC send (platform adapter).
6. `tools.py` — the four `@tool` wrappers above (thin).
7. `persona.py` / config — Nora's identity and configuration (content, not machinery).

Everything else — loop, context, tools registry, hooks, tracing — is Strands.

## Verification

1. `pip install 'strands-agents[openai]' strands-agents-tools` succeeds in the venv.
2. A bare `Agent` with `OpenAIModel(custom client → OmniRoute)` returns a non-empty
   reply for `"ping"` — proves the provider wiring before any Signal work.
3. Tagging path roundtrip: tag → pre-fill → invoke → tools available → reply → send →
   audit → Honcho feed (see `02-tagging-path.md`).
4. Stateless check: two tags produce two independent invocations; no `agent.messages`
   carry-over; SQLite + Honcho are the only continuity.
5. Silent-failure check: OmniRoute down → tag → logged, nothing sent.
