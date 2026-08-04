---
kind: design
title: "Nora — Ambient Ingest Path"
description: "Every Signal group message is written to SQLite (audit) and fed to Honcho (semantic ingest) without passing through the Agent SDK. Deterministic write path; tag detection is the only branch."
tags: [nora, design, ambient, ingest, signal, sqlite, honcho]
timestamp: '2026-08-03'
edges:
  - {dst: ../00-overview.md, rel: decided_by, provenance: asserted}
  - {dst: ../02-tagging-path.md, rel: triggers, provenance: asserted}
  - {dst: ../04-config-identity.md, rel: uses, provenance: asserted}
---

# Nora — Ambient Ingest Path

## Role

The ambient path is Nora's always-on ear. Every incoming Signal group message is:

1. **Parsed** into a message record (group, sender, name, timestamp, body, attachment placeholder).
2. **Appended to SQLite** — the audit record (raw, complete, queryable).
3. **Fed to Honcho** — the semantic ingest (peer chat endpoint → Deriver builds peer model).

This path **never invokes the Agent SDK**. No LLM, no tools, no loop. It is a
deterministic write path with exactly one branch: tag detection.

## Flow

```
SSE event (signal-cli /api/v1/events)
   │
   ▼
parse_event(envelope) → NormalizedMessage
   │  extract: conversation_id (base64 group ID), conversation_type="group",
   │           sender_id, sender_name, ts, body, attachments (→ placeholder)
   │  filter:  drop own messages (Nora's account), drop syncMessage,
   │           drop DMs (group messages only)
   ▼
db.append(...)                    ──▶ SQLite audit (rolling window, per-conversation cap)
   │
   ▼
peer_id = lookup(sender_id)       ──▶ identity.toml reverse index
   │
   ├─ mapped ──▶ honcho.feed(peer_id, text) ──▶ Honcho peer chat → Deriver (async, best-effort)
   │
   └─ unmapped ──▶ skip Honcho (SQLite-only; audit F3/F18)
   │
   ▼
tag.is_tagged(body, trigger) ── no ──▶ done
   │
   └─ yes ──▶ TAGGING PATH (02-tagging-path.md)
```

## Components

### PlatformAdapter (abstraction seam, audit A1)

Every platform-facing concern goes through a `PlatformAdapter` protocol; Signal is
the first implementation:

```python
class PlatformAdapter(Protocol):
    platform: str                                   # "signal" | "discord" | ...
    async def listen(self, on_event) -> None: ...   # long-lived connection
    async def parse(self, raw_event) -> NormalizedMessage | None: ...
    async def send(self, target: ConversationTarget, text: str) -> bool: ...
```

`NormalizedMessage` carries `platform`, `conversation_id`, `conversation_type`
(`group` | `dm`), `sender_id`, `sender_name`, `timestamp`, `body`. The SQLite store
and the tagging path see only this normalized shape — never a platform envelope.
Signal-specific logic (SSE, envelope parsing, JSON-RPC send) lives inside the Signal
adapter, exactly like `listener.py`/`parser.py`/`signal_send.py` today but behind the
seam.

### Listener (SSE) — Signal adapter

- Consumes `GET /api/v1/events` (`text/event-stream`, chunked, long-lived).
- Reconnect with exponential backoff (1s → 60s cap) on disconnect/error.
- One bad event is logged and skipped — never kills the stream.

### Parser
- Parses the envelope into a `NormalizedMessage` (`conversation_id` from the base64
  group ID, `conversation_type="group"`).
- **Group messages only** in this version (requires `groupInfo`); DMs and `syncMessage`
  dropped — DM support is a later adapter/config change (`conversation_type="dm"`).
- Attachments become placeholders (`[image]`, `[attachment: <filename>]`) — no
  download in this version.
- Nora's own sent messages are filtered on ingest (her replies enter the buffer only
  via the tagging-path append, tagged with her configured sender id).

### SQLite audit store

- Single table `messages` (platform-neutral schema, audit 2nd-pass):

```sql
CREATE TABLE messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT NOT NULL,               -- "signal" | "discord" | ...
    conversation_id    TEXT NOT NULL,               -- platform conversation key (base64 group ID for Signal)
    conversation_type  TEXT NOT NULL CHECK (conversation_type IN ('group','dm')),
    sender_id          TEXT NOT NULL,
    sender_name        TEXT,
    ts                 INTEGER NOT NULL,            -- platform timestamp (ms epoch)
    body               TEXT,
    is_nora            INTEGER NOT NULL DEFAULT 0,
    delivery_status    TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | failed (audit F17)
    raw_json           TEXT,                        -- full platform envelope for debugging
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_conv_ts ON messages(conversation_id, ts DESC);
CREATE INDEX idx_messages_is_nora ON messages(is_nora);
```

- `platform` column enables per-platform audit/metrics from day one (audit F9).
- `delivery_status` records whether Nora's reply was delivered (audit F17) —
  `pending` until `send_reply` runs, then `sent` or `failed`. No auto-retry in v2;
  the operator sees undelivered replies in the audit.
- **Role: audit record + context source for the tagging path.** No semantics live
  here. `raw_json` keeps the full envelope for debugging.

### Honcho feed (ingest model corrected 2026-08-03)

**Correction:** the v1 design said "feed to `peers/{id}/chat`" — that was wrong. `chat` is
a dialectic *query* endpoint, not ingest. The real ingest endpoint is
`POST /v3/workspaces/{ws}/sessions/{session_id}/messages`.

- Every group message from a **mapped** sender is appended to the sender's Honcho
  session via `POST /v3/workspaces/{ws}/sessions/{session_id}/messages` — fire-and-forget.
  Each mapped peer gets one standing session (idempotent get-or-create at first sighting).
- **Unmapped senders are skipped** (audit A2/F3): no Honcho peer, no session, no feed —
  avoids stranding history on sanitized fallback IDs. SQLite still captures their messages.
- The Deriver (`honcho-deriver-1`) digests asynchronously and maintains peer cards.
- **Best-effort:** if Honcho is down, the ambient path still appends to SQLite and
  replies without durable recall. Ambient ingest never blocks on Honcho.

## Non-Goals

- No LLM calls, no agent loop, no tools on this path.
- No media download / vision.
- No semantic processing — meaning is Honcho's job, derived asynchronously.
- No DM support in this version.

## Environment & Isolation (audit A4, 2026-08-03)

Nora reads private messages and sends to real groups — it runs with real authority.
The baseline posture:

- **Runtime:** uv-managed venv; dedicated `nora` system user (no sudo, no shell, no
  extra group memberships).
- **Filesystem:** write access limited to `~/workspace/nora/data/` only.
- **Network egress:** only the three localhost endpoints — `127.0.0.1:18080`
  (signal-cli), `127.0.0.1:8000` (Honcho), `127.0.0.1:20128` (OmniRoute). Enforced at
  the application layer (validated HTTP client transport) and/or iptables
  owner-match rules for the `nora` user.
- **Supervision:** systemd unit (`Restart=always`, `RestartSec=10`) or Docker Compose
  (`restart: unless-stopped`) with a healthcheck. Watchdog: alert if SSE is silent
  for >300s.
- **Secret:** `OMNIROUTE_API_KEY` from environment only. Defense-in-depth option:
  a localhost auth proxy injects the `Authorization` header so the agent process
  never holds the raw key (v2.1).
- **send_reply gate (defense-in-depth):** the agent writes its reply to a queue; a
  separate gate process validates (rate limit, trigger-received check) before
  relaying to signal-cli. The Strands Hook stays as the first line; the gate is the
  process boundary (v2.1 — v2 ships the Hook-only guardrail).

## Observability (audit A5/A6, 2026-08-03)

- **Tracing: OTel enabled by default.** Strands SDK already instruments the agent
  loop, tools, and model calls — enabling is a config toggle, not a build. v2 ships
  with a console/OTLP exporter to a local collector or trace file; logs carry trace
  IDs for correlation. `enabled: false` is a deliberate production-silence override,
  not the default.
- **Structured logs:** JSON lines with `tag_id` (per-tag UUID generated at trigger
  detection), `conversation_id`, `sender_id`, `tool_name`, `latency_ms`.
  Propagated ambient → tagging boundary.
- **Health:** `GET /health` is non-optional — SSE connection status + last-event age,
  SQLite reachability + row count, Honcho reachability, OmniRoute last-success
  timestamp, error counters since restart, uptime.
- **Metrics:** counters (`nora_tags_total`, `nora_replies_total`,
  `nora_errors_total`, `honcho_feed_success`, `honcho_feed_failure`) and latency
  histograms (per-tag end-to-end, LLM call) — Prometheus text format on a separate
  port, or OTel metrics SDK.
- **Heartbeat:** INFO log line every 60s with ingest stats (messages processed, tags
  detected, errors) so a dead process is visible as a missing heartbeat.

## Error Handling

| Failure | Behavior |
|---|---|
| SSE disconnect | reconnect with backoff; log each attempt |
| DB write fails | log, drop the message, never crash the listener |
| Honcho feed fails | log warning, continue (SQLite already appended) |
| Parse failure | log, skip event |

**Principle: invisible when broken.** Everything on this path fails silently into
`data/nora.log` — the operator diagnoses from logs, the group never sees an error.
