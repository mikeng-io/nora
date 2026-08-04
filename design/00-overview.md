---
kind: design
title: "Nora — Ambient Signal Group Agent: Overview & Topology"
description: "Two-layer architecture: ambient ingest (no Agent SDK) writes Signal traffic into SQLite audit + Honcho; tagging path runs a stateless Strands agent that reuses SDK primitives for context, reasoning, and reply."
tags: [nora, design, architecture, strands, ambient, signal]
timestamp: '2026-08-03'
supersedes: "DESIGN.md (2026-08-01, from-scratch no-framework v1)"
edges:
  - {dst: 01-ambient-ingest, rel: decides_on, provenance: asserted}
  - {dst: 02-tagging-path, rel: decides_on, provenance: asserted}
  - {dst: 03-strands-reuse, rel: motivated_by, provenance: asserted}
  - {dst: 04-config-identity, rel: decided_by, provenance: asserted}
---

# Nora — Ambient Signal Group Agent: Overview & Topology

## Identity & Scope

Nora is a **private ambient agent** for Signal group chats. She is silent by default;
she replies only when a configured trigger text (e.g. `nora.000`) appears in a group
message. Every group message is ingested ambiently — recorded and fed into Honcho —
but **no agent loop runs on ambient traffic**. The Strands Agents SDK is involved only
on the tagging path.

This document is the topology. The four companion documents hold the detail:

| Doc | Holds |
|---|---|
| `01-ambient-ingest.md` | Ambient path: SSE → parse → SQLite audit + Honcho feed (no Agent SDK) |
| `02-tagging-path.md` | Reactive path: trigger → context injection → Strands agent → reply → feed-back |
| `03-strands-reuse.md` | Strands SDK reuse map: which built-in primitives replace which hand-rolled wheels |
| `04-config-identity.md` | Dynamic config: trigger text, Signal account, peer mapping |

## The Two Layers

```
Signal group
   │  SSE push (every message)
   ▼
┌──────────────────────────────────────────────────────────────┐
│ ① AMBIENT PATH (always, no Agent SDK)                       │
│                                                              │
│   event → parser → NormalizedMessage                        │
│                  → SQLite append (audit, platform-neutral)  │
│                  → Honcho feed (mapped senders only)        │
│                                                              │
│   ┌─ trigger text present? ── no ──▶ done (ambient only)    │
│   │                                                          │
│   └─ yes ─────────────────────────────────────────────────┐  │
└────────────────────────────────────────────────────────────┘  │
                                                               ▼
┌──────────────────────────────────────────────────────────────┐
│ ② TAGGING PATH (trigger only, via Strands Agents SDK)       │
│                                                              │
│   collect context:                                           │
│     • recent N messages (SQLite audit)                       │
│     • tagger's Honcho peer card (durable recall)             │
│     • peer mapping config (identity.toml)                    │
│     • the tag message itself                                 │
│                                                              │
│   → Agent(messages=[pre-fill context])                       │
│   → agent.invoke_async(tag_message)                          │
│        Strands loop: reason → tool call → execute → repeat   │
│        tools: read_buffer / read_peer_card / send_reply /    │
│               feed_honcho                                    │
│   → reply → db.append (audit first, delivery_status=pending)│
│           → send_reply (Signal) → delivery_status=sent/failed│
│           → Honcho feed (Deriver)                            │
└──────────────────────────────────────────────────────────────┘
```

**Boundary rule:** the Agent SDK never sees ambient-only traffic. It is constructed,
invoked, and discarded **per tag**. Ambient ingest is a deterministic write path.

## Locked Decisions

These were settled in design discussion (2026-08-03). Do not re-derive.

### D1 — Two layers, one boundary

Ambient ingest does **not** pass through the Agent SDK. The SDK is only on the tagging
path. Rationale: ambient traffic is high-volume, low-value-per-message; running an
agent loop on it burns tokens and adds latency to nothing. The ambient path is a pure
write path (SQLite + Honcho).

**Amended (2026-08-03, audit A3):** the ambient path MAY later host a **lightweight
relevance classifier** (a single cheap LLM completion, NOT the Agent SDK) that decides
whether a message warrants the tagging path. Today the gate is the deterministic
trigger (`tag.is_tagged`); the classifier is a future milestone (true ambient) that
replaces the boolean gate with a score + threshold. This does not violate the layer
boundary — the full Agent SDK still runs only on the tagging path.

### D2 — SQLite is an audit record, not the semantic store

The `messages` table stores raw events (body, sender, group, ts, full envelope JSON)
for audit and context retrieval. It does **not** hold derived meaning. Honcho is the
semantic store: peer cards, relationships, durable recall — built asynchronously by
the already-running `honcho-deriver-1` container.

### D3 — Strands Agents SDK as the tagging-path engine

The agent loop, tool execution, context management, and observability on the tagging
path come from the Strands Agents SDK (Python). See `03-strands-reuse.md` for the
exact reuse map. Explicitly not: a hand-rolled LLM loop, a hand-rolled context
builder, a hand-rolled session store.

### D4 — Stateless / sessionless per tag

Every tag is a **fresh agent invocation**. There is no carry-over of conversation
state between tags, no Strands session persistence, no SDK snapshots. Continuity
comes from the two stores — SQLite (transcript) and Honcho (memory) — which are
**pre-filled into the fresh agent's context** via `Agent(messages=[...])`. See
`02-tagging-path.md §Injection` and `03-strands-reuse.md §Sessionless`.

### D5 — Everything identity-related is config, never hardcoded

Trigger text, Signal account, peer mapping, model, endpoints — all read from
`config.toml` / environment / signal-cli's own configuration. See
`04-config-identity.md`.

### D6 — Peer mapping is manual, one file

Mapping platform sender IDs to human identities cannot be fully automatic (the
Deriver cannot learn "this phone number is Miles"). It is a **manual, operator-maintained
relation mapping in a single YAML file** — no service, no UI, no schema machinery.
See `04-config-identity.md`.

**Amended (2026-08-03, audit A2):** the mapping is two-level for cross-platform
identity: `persons:` (stable `person_id` → name + per-platform sender IDs) and the
Honcho peer ID is derived from `person_id`. One person = one Honcho peer across all
platforms (Signal today, Discord/Telegram/WhatsApp later). See
`04-config-identity.md §Persons`.

## Platform Abstraction (audit A1, 2026-08-03)

Nora is designed platform-first: Signal is the first implementation, but every
platform-facing concern goes through an abstraction seam so DM/WhatsApp/Discord/
Telegram are extensions, not rebuilds.

- **`PlatformAdapter` protocol** — `listen()`, `parse(raw_event) -> NormalizedMessage`,
  `send(target, text)`.
- **`NormalizedMessage`** — `platform`, `conversation_id`, `conversation_type`
  (`group` | `dm`), `sender_id`, `sender_name`, `timestamp`, `body`.
- **Store schema** — `conversation_id` (platform-neutral, not Signal-specific), plus
  `conversation_type` and `platform` columns.
- **Tools** — `send_reply(conversation_id, conversation_type, text)`.
- **Peer mapping** — keyed by platform sender ID, mapped to one `person_id` → one
  Honcho peer (`04-config-identity.md §Persons`).

Signal is the only adapter in this version. See `01-ambient-ingest.md`, `02-tagging-path.md`.

## Runtime Operations (audit A4/A5/A6, 2026-08-03)

- **Isolation:** dedicated `nora` system user, venv runtime, network egress limited to
  the three localhost endpoints, filesystem writes limited to `data/`, process
  supervision (systemd `Restart=always` or Docker Compose `restart: unless-stopped`),
  `send_reply` decoupled behind a gate process as defense-in-depth. See
  `01-ambient-ingest.md §Environment & Isolation`.
- **Observability:** OTel **enabled by default** (Strands SDK already instruments the
  loop; a config toggle, not a build), structured logs with per-tag correlation ID,
  `GET /health` (non-optional) with SSE/DB/Honcho/LLM status, and a metrics endpoint
  (tags/replies/errors counters + latency histograms). See `01-ambient-ingest.md
  §Observability`.
- **Evaluation:** `strands-agents-evals` is the framework (OutputEvaluator,
  TrajectoryEvaluator, deterministic ToolCalled, tool simulation, red-team corpus);
  latency SLOs (p95 ≤ 5s, p99 ≤ 15s); A/B of Honcho memory vs no-memory. See
  `05-evaluation.md`.

## External Services (unchanged, verified)

| Service | Endpoint | Role | Verification |
|---|---|---|---|
| signal-cli daemon | `http://127.0.0.1:18080` | SSE events in; JSON-RPC send out | verified 2026-08-01 |
| Honcho v3 | `http://127.0.0.1:8000` | workspace, peers, cards, chat feed | verified 2026-08-01 |
| Honcho Deriver | `honcho-deriver-1` container | async peer modeling from fed messages | verified 2026-08-01 |
| OmniRoute | `http://localhost:20128/v1` | OpenAI-compatible LLM routing | verified 2026-08-01 |
| Strands Agents SDK | PyPI `strands-agents` v1.50.2 | agent loop, tools, hooks, MCP | verified 2026-08-03 |
| Strands Evals | PyPI `strands-agents-evals` | evaluation framework | verified 2026-08-03 (audit) |
