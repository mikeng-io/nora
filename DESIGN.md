# Nora — Ambient Signal Group Agent

**Status:** Design spec — implementation-ready
**Owner:** Miles
**Location:** `~/workspace/nora/` (local-only git repo)
**Scope:** v1 — private ambient agent for Signal group chats

---

## 1. Purpose

Nora is a **private ambient agent** for Signal group chats. She is silent by default and responds only when tagged (`@Nora ...`) in a group she's a member of. She has visibility of recent group history for context and uses Honcho for durable peer/relationship recall across sessions.

Nora is **not** built on Hermes Agent — Hermes is request-response by design and does not support ambient mode without core modifications that aren't worth the maintenance tax. Nora is a from-scratch Python service (~300-400 lines) that talks directly to signal-cli's HTTP daemon and Honcho's REST API.

---

## 2. Design Decisions (locked)

These decisions were settled through design discussion before this spec was written. They are **not** to be re-derived by the implementer.

### 2.1 Stateless DB-only — no session object

There is no in-memory conversation object, no session handle, no session store. The LLM call is the only stateful moment; before and after it the system is just rows in SQLite.

- Each incoming message → **append row to `messages` table**, close DB connection, done.
- On tag → query recent rows from `messages` table → build LLM context → call LLM → append Nora's reply as a row → send via signal-cli → close DB connection, done.
- No `conversations` table. No `sessions` table. No `agent_state` table.

### 2.2 Fresh session per tag — not continue-per-group

Each `@Nora` tag triggers a **fresh LLM call** with no carryover from previous LLM calls. Nora's previous replies exist only as rows in the ambient buffer, the same as anyone else's messages — they age out the same way.

**Single store, not two:** the ambient buffer captures *every* message in the group including Nora's own responses (tagged with her sender ID). There is no separate "session" store for Nora's turns.

### 2.3 Ambient buffer = single store, text-only

The `messages` table is the **only** state. It is a rolling window — old rows age out. Nora's reply is appended with her own sender ID, so the next tag sees it as part of group history.

**Media handling (v1): deferred — placeholders only.** When signal-cli delivers an attachment, store a placeholder in the text/body column and move on:

| Signal payload | Stored as |
|---|---|
| Text message | the text |
| Image | `[image]` |
| Video / audio / file | `[attachment: <filename>]` |
| Link preview | the URL as plain text (signal-cli includes it in the message body) |
| Sticker / reaction | drop, or `[reaction]` |

**Why:** The buffer's job is conversational context, not multimodal. A `[image]` placeholder is enough for the LLM to know "someone shared a photo here." Downloading + storing binaries adds a whole subsystem (disk lifecycle, cleanup, vision-model invocation, cost) that doesn't earn its keep for v1. Revisit when there's an actual case where Nora needed to *see* the image and couldn't.

### 2.4 Honcho for durable recall — reactive digestion (Model B)

Don't digest proactively. Digest **reactively** — only after Nora has been invoked and a response has added value. Honcho's async Deriver worker (`honcho-deriver-1` container, already running) derives peer models from fed messages; we don't build our own.

- **Workspace:** one Honcho workspace for Nora (e.g. `nora`).
- **Peer per participant:** each Signal group participant gets a Honcho peer (created idempotently on first sighting).
- **Feed messages after tag:** when Nora is tagged and responds, feed the recent buffer (including her reply) to Honcho via the peer's chat endpoint — the Deriver does the modeling asynchronously.
- **Read peer card before responding:** on tag, fetch the tagger's peer card to inject durable recall into LLM context.
- **Conclusions (optional, v1):** if Nora derives a stable fact about a participant, store via `POST /conclusions`. v1 may skip this and rely on the Deriver only.

### 2.5 From-scratch Python

No Pi Agent, no Strands, no Hermes profile, no framework. ~300-400 lines. Dependencies: `aiohttp`/`httpx`, `sqlite3` (stdlib), OmniRoute (OpenAI-compatible). The loop is small: SSE listener → tag detection → context build → LLM call → reply send + DB append + Honcho feed.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Signal Group                          │
│              (Miles + others + Nora's number)                 │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              signal-cli daemon (port 18080)                  │
│   GET /api/v1/events   → SSE stream of incoming messages    │
│   POST /api/v1/rpc      → JSON-RPC send                      │
│   GET /api/v1/check     → health (200 empty)                │
└─────────────────────────────────────────────────────────────┘
                            │
                  SSE event (push)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Nora service (Python)                    │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │
│  │ SSE Listener │───▶│ Tag Detector│───▶│ Tag Handler │    │
│  └──────────────┘    └──────────────┘    └──────┬──────┘    │
│         │                                       │           │
│         │ (every msg)                           │ (on tag)  │
│         ▼                                       ▼           │
│  ┌──────────────┐             ┌───────────────────────────┐ │
│  │ DB Append    │             │ 1. Query recent N msgs    │ │
│  │ (messages)   │             │ 2. Get tagger's Honcho    │ │
│  └──────────────┘             │    peer card (durable)    │ │
│                               │ 3. Build LLM context      │ │
│                               │ 4. Call LLM (OmniRoute)   │ │
│                               │ 5. Append reply to DB     │ │
│                               │ 6. Send via signal-cli RPC│ │
│                               │ 7. Feed to Honcho (Deriver)│ │
│                               └───────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
          │              │                    │
          ▼              ▼                    ▼
   ┌────────────┐  ┌────────────┐    ┌──────────────────┐
   │  SQLite    │  │  Honcho    │    │  OmniRoute       │
   │ messages.db │  │ :8000 v3   │    │  (OpenAI-compat) │
   └────────────┘  └────────────┘    └──────────────────┘
```

**Data flow per incoming message:**

1. SSE listener receives `receive` event from signal-cli.
2. Parse envelope → extract `groupId`, `source`, `sourceName`, `timestamp`, `dataMessage.message`, attachment info.
3. **Always:** append row to `messages` table (group_id, sender_id, sender_name, ts, body, raw_json). Use placeholder for attachments.
4. **If tagged (`@Nora` in body):** run tag handler.
5. Tag handler: query last N messages in this group → fetch tagger's Honcho peer card → build context → LLM call → append reply row (sender_id = `nora`) → send reply via signal-cli RPC `send` (groupId) → feed recent exchange to Honcho peer chat (Deriver digests async).

---

## 4. Data Model — SQLite

Single database file: `~/workspace/nora/data/messages.db`

### Table: `messages`

```sql
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      TEXT NOT NULL,          -- signal-cli group ID (base64)
    sender_id     TEXT NOT NULL,          -- source number or 'nora'
    sender_name   TEXT,                  -- sourceName from signal-cli, or 'Nora'
    ts            INTEGER NOT NULL,       -- signal timestamp (ms epoch)
    body          TEXT,                   -- message text or placeholder
    is_nora       INTEGER NOT NULL DEFAULT 0,  -- 1 if this is Nora's reply
    raw_json      TEXT,                   -- full envelope JSON for debugging
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))  -- local ingest time
);

CREATE INDEX idx_messages_group_ts ON messages(group_id, ts DESC);
CREATE INDEX idx_messages_is_nora ON messages(is_nora);
```

**Rolling window:** enforce a per-group cap (e.g. 500 rows). After each insert, delete oldest rows beyond cap for that group. This keeps the buffer bounded.

**Why a single table:** stateless DB-only design. No `conversations`, no `sessions`, no `peers` table (Honcho owns peer state). The `messages` table is the ambient buffer.

---

## 5. Verified External Contracts

These were verified against live endpoints on 2026-08-01. The implementer should treat these as ground truth, not re-derive them.

### 5.1 signal-cli daemon

- **Base URL:** `http://127.0.0.1:18080`
- **Process:** `java ... org.asamk.signal.Main --account +852****7304 daemon --http 127.0.0.1:18080`
- **Version:** `0.14.4.1` (verified via RPC `version` method)

#### `GET /api/v1/check`
- Returns `200 OK` with empty body if daemon is running.

#### `GET /api/v1/events` — SSE receive stream
- `Content-Type: text/event-stream`, chunked, long-lived.
- Pushes JSON-RPC `receive` notifications when messages arrive. Stream is silent when no messages — that's expected, not a failure.
- **Event payload shape** (from upstream man page + verified structure):

```json
{
  "jsonrpc": "2.0",
  "method": "receive",
  "params": {
    "envelope": {
      "source": "+85212345678",
      "sourceNumber": "+85212345678",
      "sourceUuid": "uuid-string",
      "sourceName": "Miles",
      "sourceDevice": 1,
      "timestamp": 1631458508784,
      "dataMessage": {
        "timestamp": 1631458508784,
        "message": "foobar",
        "expiresInSeconds": 0,
        "viewOnce": false,
        "mentions": [],
        "attachments": [],
        "contacts": [],
        "groupInfo": {
          "groupId": "GROUP_ID_BASE64=",
          "name": "Group Name",
          "type": 2
        }
      }
    },
    "account": "+852****7304"
  }
}
```

**Notes for implementer:**
- `envelope.dataMessage.groupInfo.groupId` is the **base64** group ID — use this as `group_id` in DB.
- `envelope.source` is the sender's phone number — use as `sender_id` (unless it's Nora's own number, in which case skip — don't double-append).
- `envelope.sourceName` is the display name — store as `sender_name`.
- Attachments: `dataMessage.attachments` is an array; if non-empty, store placeholder (`[image]` / `[attachment: <filename>]`). Do **not** download in v1.
- Group messages have `groupInfo`; DMs do not. v1 handles groups only (DM path is out of scope unless trivial).
- Sync messages (`syncMessage`) from other devices should be ignored for v1 — only handle `dataMessage`.

#### `POST /api/v1/rpc` — JSON-RPC send
- `Content-Type: application/json`
- Single request body (batch also supported but not needed).

**Send to group:**
```json
{
  "jsonrpc": "2.0",
  "method": "send",
  "params": {
    "groupId": "GROUP_ID_BASE64=",
    "message": "Nora's reply text"
  },
  "id": 4
}
```
**Response (success):**
```json
{"jsonrpc": "2.0", "result": {"timestamp": 999}, "id": 4}
```
**Response (error — e.g. unregistered recipient):**
```json
{"jsonrpc": "2.0", "error": {"code": -1, "message": "Failed to send message", "data": {...}}, "id": 4}
```

**Note:** the `account` param is **not** needed when daemon is started with `-a ACCOUNT` (single-account mode), which this one is. For multi-account mode, add `"account": "+852****7304"` to `params`.

### 5.2 Honcho API

- **Base URL:** `http://127.0.0.1:8000` (v3 API)
- **Health:** `GET /health` → `{"status":"ok"}`
- **Deriver:** `honcho-deriver-1` container already running — async peer-modeling worker. Feed it messages; it derives. Do **not** build our own Deriver.
- **37 endpoints** total (verified via OpenAPI at `/openapi.json`).

#### Workspace — `POST /v3/workspaces`
- Idempotent "get or create."
- **Body schema (`WorkspaceCreate`):**
  - `id*` (string) — workspace ID, e.g. `"nora"`
  - `metadata` (object)
  - `configuration` (`WorkspaceConfiguration`)
- **Use:** create workspace `nora` once at startup (idempotent).

#### Peer — `POST /v3/workspaces/{workspace_id}/peers`
- Idempotent "get or create."
- **Body schema (`PeerCreate`):**
  - `id*` (string) — peer ID. **Use the Signal phone number** as peer ID for stable identity (e.g. `+85212345678`).
  - `metadata` (object | null)
  - `configuration` (object | null)
- **Use:** create a peer for each group participant on first sighting.

#### Peer card — `GET /v3/workspaces/{workspace_id}/peers/{peer_id}/card`
- **Params:** `workspace_id`, `peer_id`, `target` (query — optional, the peer whose card to read)
- **Response schema (`PeerCardResponse`):** `{ "peer_card": ["fact1", "fact2", ...] | null }`
- **Use:** fetch tagger's peer card before building LLM context → inject as durable recall.

#### Peer chat — `POST /v3/workspaces/{workspace_id}/peers/{peer_id}/chat`
- **Body schema (`DialecticOptions`):**
  - `session_id` (string | null)
  - `target` (string | null) — the peer being chatted about
  - `query*` (string) — the message/query
  - `stream` (boolean)
  - `reasoning_level` (string)
- **Use:** feed the recent exchange (the tag + Nora's reply) to the Deriver via this endpoint. The Deriver digests asynchronously. We do **not** read the chat response back into our flow — feeding is fire-and-forget; the derived model surfaces later via the peer card.

#### Conclusions — `POST /v3/workspaces/{workspace_id}/conclusions`
- **Body schema (`ConclusionBatchCreate`):** `{ "conclusions": [ { "content": "...", "observer_id": "...", "observed_id": "...", "session_id": null } ] }`
- **Use (optional in v1):** if Nora derives a stable fact about a participant, store it here. v1 may rely on the Deriver only and skip explicit conclusions.

#### List peers — `POST /v3/workspaces/{workspace_id}/peers/list`
- **Body:** (empty) — params via query: `reverse`, `page`, `size`
- **Use:** enumerate known peers (debugging/admin).

### 5.3 OmniRoute (LLM)

- **OpenAI-compatible endpoint.** Use the `openai` Python SDK pointed at the local OmniRoute base URL.
- **Model:** configurable in `config.yaml` (see §7). Default to a cheap/fast model for v1 (e.g. `deepseek-v4-flash` or similar — set in config, not hardcoded).
- **System prompt:** Nora persona — see §6.
- **Context build:** system prompt + recent N messages from buffer (formatted as `[{name}]: {body}`) + tagger's peer card (if available) + the tag message itself.

---

## 6. Nora Persona (System Prompt)

Drafted as a starting point — Miles may revise. Keep it short.

```
You are Nora, a participant in a private Signal group chat. You are silent
by default and only respond when someone tags you (@Nora).

You have access to recent messages in this group for context, plus durable
recall about the person who tagged you (their peer card) when available.

Be concise, warm, and useful. You're a friend in the group, not a service.
Match the language the group is using. Don't over-explain. If you don't
know something, say so.

When responding, reply as a group participant — no preamble, no "Hi, I'm
Nora", no service voice. Just talk.
```

---

## 7. Configuration — `config.yaml`

Single config file at `~/workspace/nora/config.yaml`. Do **not** hardcode secrets in code.

```yaml
# Nora configuration
signal_cli:
  base_url: "http://127.0.0.1:18080"
  account: "+852****7304"        # Nora's Signal number (the linked account)
  nora_sender_id: "nora"          # how Nora's own messages are tagged in DB

honcho:
  base_url: "http://127.0.0.1:8000"
  workspace_id: "nora"

llm:
  base_url: "http://localhost:20128/v1"   # OmniRoute OpenAI-compatible
  model: "deepseek-v4-flash"               # configurable; cheap default
  api_key_env: "OMNIROUTE_API_KEY"         # read from env, not hardcoded
  max_tokens: 400
  temperature: 0.7

buffer:
  db_path: "~/workspace/nora/data/messages.db"
  max_per_group: 500            # rolling window cap
  context_window: 40            # how many recent msgs to inject as context

tag:
  trigger: "@Nora"             # case-insensitive substring match
  min_interval_seconds: 5      # debounce: ignore rapid re-tags

logging:
  level: "INFO"
  path: "~/workspace/nora/data/nora.log"
```

**Secrets:** OmniRoute API key read from env var `OMNIROUTE_API_KEY`. Do not put it in `config.yaml`.

---

## 8. File Layout

```
~/workspace/nora/
├── DESIGN.md              # this file (the spec)
├── README.md              # operator quickstart (Claude Code writes after impl)
├── config.yaml            # configuration (operator edits)
├── .env.example           # OMNIROUTE_API_KEY=...  (template; .env is gitignored)
├── .gitignore             # ignores data/, .env, __pycache__/
├── pyproject.toml         # or requirements.txt — dependencies
├── nora/
│   ├── __init__.py
│   ├── __main__.py        # entry: python -m nora
│   ├── config.py          # load + validate config.yaml
│   ├── listener.py        # SSE client (signal-cli /api/v1/events)
│   ├── parser.py          # parse signal-cli envelope → message row
│   ├── db.py              # SQLite: append, query recent, enforce rolling cap
│   ├── tag.py             # tag detection + debounce
│   ├── handler.py         # tag handler: context build → LLM → send → feed Honcho
│   ├── honcho.py          # Honcho REST client (workspace, peer, card, chat, conclusions)
│   ├── llm.py             # OmniRoute OpenAI-compatible client
│   ├── signal_send.py     # signal-cli RPC send client
│   └── persona.py         # system prompt constant
└── data/                  # gitignored runtime data
    ├── messages.db
    └── nora.log
```

**Estimated size:** ~300-400 lines across the `nora/` package. Single-process, async (`aiohttp` for SSE + HTTP clients; `sqlite3` via `aiosqlite` or a thread executor).

---

## 9. v1 Scope Boundary

### In scope
- SSE listener for incoming group messages
- DB append for every message (text + placeholders for attachments)
- Tag detection (`@Nora` substring, case-insensitive, debounced)
- Tag handler: context build (recent N + peer card) → LLM → reply → DB append → signal-cli send → Honcho feed
- Rolling window cap per group
- Single config file, env-var secret
- Graceful reconnection on SSE disconnect
- Health check endpoint for Nora itself (optional but useful)

### Out of scope (deferred)
- **Media download / vision** — placeholders only in v1
- **DM support** — group messages only (DMs are technically possible but not the use case)
- **Explicit conclusions** — rely on Honcho Deriver; manual conclusions can come later
- **Multi-account signal-cli** — single account mode only
- **Web UI / admin panel** — none
- **Tests** — a minimal smoke test (send a tag, get a reply) is good; full test suite is v2
- **Docker** — run as a plain Python process for v1; containerization is v2
- **Discord extension** — future; the architecture is platform-agnostic but only Signal is wired in v1

---

## 10. Error Handling

- **SSE disconnect:** reconnect with exponential backoff (1s → 2s → 4s → ... → 60s cap). Log each attempt.
- **signal-cli down:** log error, keep retrying SSE. Health endpoint should report `signal_cli: down`.
- **Honcho down:** proceed without peer card — log a warning, send context = recent messages only. Don't block the reply on Honcho.
- **LLM call fails:** log error, do **not** send an error message to the group. Stay silent. (Better silent than noisy.)
- **DB write fails:** log error, drop the message. Don't crash the listener.
- **Send RPC fails:** log error, the reply is already in DB (so context isn't lost). Don't retry the send automatically in v1.

**Principle:** Nora should be invisible when things break — silent failure, logged, not noisy. The operator (Miles) reads logs to diagnose.

---

## 11. Verification (for the implementer to demonstrate)

After implementation, the implementer must demonstrate **real execution**, not a description:

1. **SSE listener starts and stays connected** — verify by sending a test message in a Signal group and confirming it appears in `messages.db`.
2. **DB append works** — `sqlite3 data/messages.db "SELECT count(*) FROM messages"` shows increment after a test message.
3. **Tag detection fires** — send `@Nora hello` in a group; confirm the handler logs the tag.
4. **LLM call succeeds** — confirm via log: the LLM returned a non-empty reply.
5. **Reply lands in the group** — the reply message actually appears in Signal (Miles observes this).
6. **Reply appended to DB** — `SELECT * FROM messages WHERE is_nora=1` shows the reply row.
7. **Honcho feed fires** — log line confirms the peer chat POST returned 2xx.
8. **Rolling cap works** — insert >500 rows for a group, confirm oldest are pruned.

**The roundtrip is the deliverable.** A working tag → reply cycle in a real Signal group is the success criterion.

---

## 12. Implementation Notes for Claude Code

- This is a **from-scratch Python service**, not a contribution to an existing framework. No upstream conventions to follow except this doc.
- Use `aiohttp` for SSE (it handles `text/event-stream` well) and HTTP clients. `sqlite3` via `aiosqlite` is fine, or use a thread executor for DB writes.
- **Do not** download or store attachment binaries in v1 — placeholders only.
- **Do not** build a Honcho Deriver — `honcho-deriver-1` is already running. Just feed messages.
- **Do not** modify signal-cli configuration — it's running and correct. Talk to its HTTP API only.
- **Do not** add Docker, systemd, or process management in v1 — run with `python -m nora` for now.
- The persona prompt in §6 is a draft — implement it verbatim; Miles will iterate after seeing it live.
- Keep the code small and readable. No abstractions that aren't used. No plugin systems. No extensibility hooks. v1 is a single-purpose script that runs.
- **Stateless DB-only is the core constraint.** If you find yourself adding a session object, a conversation store, or in-memory agent state, stop — re-read §2.1.

---

## 13. Open Questions (resolve during implementation, not before)

- **Nora's own number:** the spec uses `sender_id = "nora"` for her replies in DB, but signal-cli events for her own sent messages (sync messages) will have her real number. Decide: filter sync messages entirely (recommended), or map her number → `"nora"` on ingest. Filter is simpler.
- **Group ID stability:** base64 group IDs from signal-cli are stable per group. Confirm by checking the same group across SSE restarts. (Should be fine — this is documented behavior.)
- **Honcho peer ID = phone number:** using the Signal phone number as Honcho peer ID is stable and simple. Confirm no collision risk (Honcho peer IDs are strings; phone numbers with `+` prefix are fine).

---

*Spec written 2026-08-01. All external contracts verified against live endpoints on the same date.*