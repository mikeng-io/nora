# Nora — Ambient Signal Group Agent

**Status:** Design spec — implementation-ready
**Owner:** Miles
**Location:** `~/workspace/nora/` (local-only git repo)
**Scope:** private ambient agent for Signal group chats, built on the **Strands Agents SDK**

---

## 1. Purpose

Nora is a **private ambient agent** for Signal group chats. She is silent by default and
responds only when tagged (`@Nora ...`) in a group she's a member of. She has visibility of
recent group history for context and uses Honcho for durable peer/relationship recall across
sessions.

Nora is **not** built on Hermes Agent — Hermes is request-response by design and does not
support ambient mode. Nora is a standalone Python service: signal-cli's SSE stream is the
event source, the Strands Agents SDK is the agent loop, Honcho is the memory, OmniRoute is
the model router.

---

## 2. Design Decisions

### 2.1 Stateless DB-only — no session object

There is no in-memory conversation object, no session handle, no session store. The LLM call
is the only stateful moment; before and after it the system is just rows in SQLite.

- Each incoming message → **append row to `messages` table**, close DB connection, done.
- On tag → query recent rows from `messages` table → run Strands agent → append Nora's reply
  as a row → send via signal-cli → close DB connection, done.
- No `conversations` table. No `sessions` table. No `agent_state` table.
- **Strands session state is deliberately not used.** Strands supports sessions, but this
  design keeps the fresh-call-per-tag invariant: a throwaway session per tag (or none), never
  persistent conversation state.

### 2.2 Fresh call per tag — not continue-per-group

Each `@Nora` tag triggers a **fresh agent invocation** with no carryover from previous calls.
Nora's previous replies exist only as rows in the ambient buffer, the same as anyone else's
messages — they age out the same way.

**Single store, not two:** the ambient buffer captures *every* message in the group including
Nora's own responses (tagged with her sender ID). There is no separate "session" store for
Nora's turns.

### 2.3 Ambient buffer = single store, text-only

The `messages` table is the **only** state. It is a rolling window — old rows age out. Nora's
reply is appended with her own sender ID, so the next tag sees it as part of group history.

**Media handling: deferred — placeholders only.** When signal-cli delivers an attachment,
store a placeholder in the text/body column and move on:

| Signal payload | Stored as |
|---|---|
| Text message | the text |
| Image | `[image]` |
| Video / audio / file | `[attachment: <filename>]` |
| Link preview | the URL as plain text (signal-cli includes it in the message body) |
| Sticker / reaction | drop, or `[reaction]` |

**Why:** The buffer's job is conversational context, not multimodal. A `[image]` placeholder
is enough for the LLM to know "someone shared a photo here." Downloading + storing binaries
adds a whole subsystem (disk lifecycle, cleanup, vision-model invocation, cost) that doesn't
earn its keep. Revisit when there's an actual case where Nora needed to *see* the image and
couldn't.

### 2.4 Honcho for durable recall — reactive digestion

Don't digest proactively. Digest **reactively** — only after Nora has been invoked and a
response has added value. Honcho's async Deriver worker (`honcho-deriver-1` container,
already running) derives peer models from fed messages; we don't build our own.

- **Workspace:** one Honcho workspace for Nora (`nora`).
- **Peer per participant:** each Signal group participant gets a Honcho peer (created
  idempotently on first sighting).
- **Feed messages after tag:** when Nora is tagged and responds, feed the recent buffer
  (including her reply) to Honcho via the peer's chat endpoint — the Deriver does the
  modeling asynchronously.
- **Read peer card before responding:** on tag, fetch the tagger's peer card to inject
  durable recall into the agent's context (as a tool the agent calls).
- **Conclusions (optional):** if Nora derives a stable fact about a participant, store via
  `POST /conclusions`. May be skipped; the Deriver covers v1 needs.

### 2.5 Strands Agents SDK as the agent base

**The agent loop and tool layer is the Strands Agents SDK (Python).** We do not hand-roll an
LLM loop; the SDK owns the reasoning/tool loop, context management, execution limits, and
observability.

**Why Strands (verified 2026-08-03):**
- Model-driven loop: `Agent(model=..., tools=[...])` — define model + tools + prompt, the SDK
  runs the loop.
- **OmniRoute compatibility verified:** Strands' `OpenAIModel` accepts a **custom client** —
  `AsyncOpenAI(api_key=..., base_url=...)` — so OmniRoute's OpenAI-compatible endpoint
  (`http://localhost:20128/v1`) plugs in directly. No AWS credentials, no Bedrock.
- **Tools via `@tool` decorator:** buffer reads, Honcho peer card, signal-cli send become
  tools the model decides to call.
- **Multi-agent patterns built in** (Agent-as-Tool, Swarm) — a later "subagent" need resolves
  to Strands' native patterns, not a custom build.
- **Built-in MCP:** thousands of pre-built tools available later without code changes.
- **Production posture:** guardrails, steering handlers, hooks, OTel tracing,
  event-triggered agents supported.
- **Package reality:** `strands-agents` v1.50.2 on PyPI, Apache-2.0, Python ≥3.10
  (host: 3.12.13), active maintenance.

**Not chosen, with reason:**
- *Hermes profile bridge:* reuse of skills/subagents/persona, but adds per-tag cold start, a
  second runtime, and request-response gateway semantics — heavier than the ambient use case
  needs.
- *Pi Agent:* minimal coding-agent harness; SDK layer is TypeScript-centric and
  terminal-oriented; no Honcho-style memory or event-triggered operation without the same
  custom glue.
- *Google ADK / OpenAI Agents SDK / LangGraph:* no structural advantage for this shape;
  Strands' OpenAI-compatible custom-client path is the decisive fit with the existing stack
  (OmniRoute + DeepSeek V4 Flash).

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
│  │ DB Append    │             │ 1. Read recent N msgs     │ │
│  │ (messages)   │             │ 2. Read tagger's Honcho   │ │
│  └──────────────┘             │    peer card              │ │
│                               │ 3. Strands Agent          │ │
│                               │    system prompt: persona │ │
│                               │    tools: read_buffer,    │ │
│                               │      read_peer_card,      │ │
│                               │      send_reply,          │ │
│                               │      feed_honcho          │ │
│                               │ 4. Reply → DB → signal-cli│ │
│                               │ 5. Honcho feed (Deriver)  │ │
│                               └───────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
          │              │                    │
          ▼              ▼                    ▼
   ┌────────────┐  ┌────────────┐    ┌──────────────────┐
   │  SQLite    │  │  Honcho    │    │  OmniRoute       │
   │ messages.db│  │ :8000 v3   │    │  :20128/v1       │
   └────────────┘  └────────────┘    │  (OpenAI-compat) │
                                     │  DeepSeek V4     │
                                     └──────────────────┘
                            ▲
                            │  Strands OpenAIModel
                            │  (custom AsyncOpenAI client)
```

**Data flow per incoming message:**

1. SSE listener receives `receive` event from signal-cli.
2. Parse envelope → extract `groupId`, `source`, `sourceName`, `timestamp`, `dataMessage.message`, attachment info.
3. **Always:** append row to `messages` table (group_id, sender_id, sender_name, ts, body, raw_json). Use placeholder for attachments.
4. **If tagged (`@Nora` in body):** run tag handler.
5. Tag handler: query last N messages in this group → invoke Strands agent with persona
   system prompt + tag context; the agent calls its tools (peer card, buffer, send reply,
   honcho feed) as it sees fit → append reply row (sender_id = `nora`) → send reply via
   signal-cli RPC `send` (groupId) → feed recent exchange to Honcho peer chat (Deriver
   digests async).

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

**Rolling window:** enforce a per-group cap (500 rows). After each insert, delete oldest rows
beyond cap for that group. This keeps the buffer bounded.

**Why a single table:** stateless DB-only design. No `conversations`, no `sessions`, no
`peers` table (Honcho owns peer state). The `messages` table is the ambient buffer.

---

## 5. Verified External Contracts

These were verified against live endpoints on 2026-08-01 (signal-cli, Honcho) and
2026-08-03 (Strands). Treat as ground truth; do not re-derive.

### 5.1 signal-cli daemon

- **Base URL:** `http://127.0.0.1:18080`
- **Process:** `java ... org.asamk.signal.Main --account +852****7304 daemon --http 127.0.0.1:18080`
- **Version:** `0.14.4.1` (verified via RPC `version` method)

#### `GET /api/v1/check`
- Returns `200 OK` with empty body if daemon is running.

#### `GET /api/v1/events` — SSE receive stream
- `Content-Type: text/event-stream`, chunked, long-lived.
- Pushes JSON-RPC `receive` notifications when messages arrive. Stream is silent when no
  messages — that's expected, not a failure.
- **Event payload shape:**

```json
{
  "jsonrpc": "2.0",
  "method": "receive",
  "params": {
    "envelope": {
      "source": "+852****5678",
      "sourceNumber": "+852****5678",
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
- `envelope.dataMessage.groupInfo.groupId` is the **base64** group ID — use as `group_id` in DB.
- `envelope.source` is the sender's phone number — use as `sender_id` (unless it's Nora's own
  number, in which case skip — don't double-append).
- `envelope.sourceName` is the display name — store as `sender_name`.
- Attachments: `dataMessage.attachments` non-empty → store placeholder. Do **not** download.
- Group messages have `groupInfo`; DMs do not. v1 handles groups only.
- Sync messages (`syncMessage`) from other devices are ignored — only `dataMessage`.

#### `POST /api/v1/rpc` — JSON-RPC send
- `Content-Type: application/json`; single request body.

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
**Response (success):** `{"jsonrpc": "2.0", "result": {"timestamp": 999}, "id": 4}`
**Response (error — e.g. unregistered recipient):**
`{"jsonrpc": "2.0", "error": {"code": -1, "message": "Failed to send message", "data": {...}}, "id": 4}`

**Note:** `account` param not needed in single-account mode (this daemon is single-account).

### 5.2 Honcho API

- **Base URL:** `http://127.0.0.1:8000` (v3 API)
- **Health:** `GET /health` → `{"status":"ok"}`
- **Deriver:** `honcho-deriver-1` container already running — async peer-modeling worker.
- **37 endpoints** total (verified via OpenAPI at `/openapi.json`).

#### Workspace — `POST /v3/workspaces`
- Idempotent "get or create." Body: `{"id": "nora"}`.

#### Peer — `POST /v3/workspaces/{workspace_id}/peers`
- Idempotent "get or create." Body: `{"id": <peer_id>}`.
- **Peer ID = Signal phone number, `+` stripped** (Honcho enforces `^[a-zA-Z0-9_-]+$`;
  `+852...` → `852...`).

#### Peer card — `GET /v3/workspaces/{workspace_id}/peers/{peer_id}/card`
- Response: `{"peer_card": ["fact1", ...] | null}`.

#### Peer chat — `POST /v3/workspaces/{workspace_id}/peers/{peer_id}/chat`
- Body: `{"query": "<exchange text>"}`. Fire-and-forget; Deriver digests async.

#### Conclusions — `POST /v3/workspaces/{workspace_id}/conclusions`
- Optional: `{"conclusions": [{"content": "...", "observer_id": "...", "observed_id": "..."}]}`.

### 5.3 OmniRoute (LLM)

- **OpenAI-compatible endpoint:** `http://localhost:20128/v1`, auth via `OMNIROUTE_API_KEY`.
- **Model:** `deepseek-v4-flash` (configurable in `config.yaml`).

### 5.4 Strands Agents SDK

- **PyPI:** `strands-agents` v1.50.2 (Apache-2.0, Python ≥3.10). Tools: `strands-agents-tools`.
- **Install:** `pip install 'strands-agents[openai]' strands-agents-tools`
- **OpenAI provider:** `OpenAIModel(model_id=..., client=AsyncOpenAI(...))` — custom client
  supported; caller owns lifecycle (`client.close()`).
- **Tools:** `@tool` decorator on Python functions; docstring is the LLM-facing schema.
- **Multi-agent:** Agent-as-Tool and Swarm patterns available (not used in this version).
- **MCP:** built-in support; not wired in this version.

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

**Tool instructions (added to prompt or tool schemas):**
- `read_buffer` and `read_peer_card` are read-only; call them when context is thin.
- `send_reply` is the **only** side-effect tool; call it exactly once per tag, and only
  when you have a reply. On failure, stay silent.
- `feed_honcho` runs after sending; never blocks the reply.

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

**Secrets:** OmniRoute API key read from env var `OMNIROUTE_API_KEY`. Never in `config.yaml`.

---

## 8. File Layout

```
~/workspace/nora/
├── DESIGN.md              # this file (the spec)
├── README.md              # operator quickstart
├── config.yaml            # configuration (operator edits)
├── .env.example           # OMNIROUTE_API_KEY=...  (template; .env is gitignored)
├── .gitignore             # ignores data/, .env, __pycache__/
├── pyproject.toml         # dependencies incl. strands-agents[openai], strands-agents-tools
├── nora/
│   ├── __init__.py
│   ├── __main__.py        # entry: python -m nora
│   ├── config.py          # load + validate config.yaml
│   ├── listener.py        # SSE client (signal-cli /api/v1/events)
│   ├── parser.py          # parse signal-cli envelope → message row
│   ├── db.py              # SQLite: append, query recent, enforce rolling cap
│   ├── tag.py             # tag detection + debounce
│   ├── handler.py         # tag handler: build context → Strands agent → reply
│   ├── honcho.py          # Honcho REST client (workspace, peer, card, chat, conclusions)
│   ├── agent.py           # Strands Agent construction (model, system prompt, tools)
│   ├── tools.py           # @tool wrappers: read_buffer, read_peer_card, send_reply, feed_honcho
│   ├── signal_send.py     # signal-cli RPC send client
│   └── persona.py         # system prompt constant
└── data/                  # gitignored runtime data
    ├── messages.db
    └── nora.log
```

**Estimated size:** ~350-450 lines across the `nora/` package plus Strands dependency.
Single-process, async (`aiohttp` for SSE + HTTP; `aiosqlite` for DB; Strands for the loop).

---

## 9. Scope Boundary

### In scope
- SSE listener for incoming group messages
- DB append for every message (text + placeholders for attachments)
- Tag detection (`@Nora` substring, case-insensitive, debounced)
- Tag handler: context build → **Strands agent** (persona prompt + tools) → reply → DB
  append → signal-cli send → Honcho feed
- Tools: `read_buffer`, `read_peer_card`, `send_reply`, `feed_honcho`
- Tool-safety: no double-send, silent failure on error
- Rolling window cap per group
- Single config file, env-var secret
- Graceful SSE reconnection
- Health check endpoint for Nora itself (optional but useful)

### Out of scope (deferred)
- **Media download / vision** — placeholders only
- **DM support** — group messages only
- **Explicit Honcho conclusions** — Deriver only
- **Multi-agent (Agent-as-Tool / Swarm)** — one agent + tools; Strands makes this a later
  extension, not a rebuild
- **MCP servers** — available but not wired
- **Strands sessions/state** — deliberately unused (stateless invariant)
- **Web UI / admin panel** — none
- **Tests** — smoke test (tag → reply) is the acceptance; full suite later
- **Docker** — plain Python process
- **Discord extension** — future; platform-agnostic architecture but Signal only for now

---

## 10. Error Handling

- **SSE disconnect:** reconnect with exponential backoff (1s → 2s → 4s → ... → 60s cap).
  Log each attempt.
- **signal-cli down:** log error, keep retrying SSE. Health endpoint reports `signal_cli: down`.
- **Honcho down:** proceed without peer card — log a warning; `read_peer_card` tool returns
  empty. Don't block the reply on Honcho.
- **LLM call fails:** log error, do **not** send an error message to the group. Stay silent.
- **Send RPC fails:** log error; the reply is already in DB (context isn't lost). No
  auto-retry.
- **Strands guardrails/steering (new):** a steering handler vetoes `send_reply` on empty or
  error output and enforces the once-per-tag rule.
- **DB write fails:** log error, drop the message. Don't crash the listener.

**Principle:** Nora is invisible when things break — silent failure, logged, not noisy.
Miles reads logs to diagnose.

---

## 11. Verification (for the implementer to demonstrate)

After implementation, the implementer must demonstrate **real execution**, not a description:

1. **Strands installs cleanly** in the project venv — `pip install 'strands-agents[openai]'`.
2. **Agent constructs with OmniRoute** — a bare `agent("ping")` returns a non-empty reply
   (proves custom-client wiring before any Signal work).
3. **SSE listener starts and stays connected** — test message appears in `messages.db`.
4. **DB append works** — count increments after a test message.
5. **Tag detection fires** — `@Nora hello` logs the tag.
6. **Full roundtrip:** tag → Strands agent (tools available) → reply sent via signal-cli →
   reply row in DB → Honcho feed POST 2xx.
7. **Silent failure:** kill OmniRoute → tag → Nora logs error, sends nothing.
8. **No double-send:** rapid double tag within debounce window → one reply.
9. **Rolling cap works** — >500 rows for a group → oldest pruned.

**The roundtrip is the deliverable.** A working tag → reply cycle in a real Signal group
with the Strands agent as the brain.

---

## 12. Implementation Notes

- Strands is a **library**, not a service — it runs inside the Nora process. No AWS
  credentials, no Bedrock, no cloud deployment.
- Do **not** enable Strands session state — stateless per tag (§2.1–2.2).
- Custom `AsyncOpenAI` client: create once at startup, close on shutdown.
- The persona prompt and tool descriptions are the contract with the model — implement
  verbatim; Miles iterates after seeing it live.
- **Do not** modify signal-cli or Honcho — talk to their APIs only.
- **Do not** add Docker, systemd, or process management — `python -m nora`.
- **Stateless DB-only is the core constraint.** If you find yourself adding a session object,
  a conversation store, or in-memory agent state, stop — re-read §2.1.

---

## 13. Open Questions (resolve during implementation)

- **Tool granularity:** should `read_buffer` return raw rows or a pre-formatted transcript?
  (Assumed: pre-formatted — the tool's docstring tells the model the shape.)
- **Strands version pin:** pin `strands-agents==1.50.2` in pyproject? (Assumed: yes, upgrade
  deliberately.)
- **Strands OTel:** v1 logs to `data/nora.log`; Strands emits OTel traces — wire to a local
  OTLP collector or disable? (Assumed: disable OTel in v2, keep file logs; revisit if
  debugging demands traces.)

---

*Spec drafted 2026-08-03. External contracts verified against live endpoints: signal-cli +
Honcho on 2026-08-01, Strands + PyPI on 2026-08-03.*
