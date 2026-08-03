# Nora

A private ambient AI agent for a Signal group chat. She reads every message silently and
replies only when tagged. Nora v2 is a two-layer, [Strands Agents SDK](https://github.com/strands-agents/sdk-python)-based
rebuild of the original hand-rolled v1 — see `design/00-overview.md` for the architecture
and `docs/superpowers/specs/2026-08-03-nora-strands-spec.md` for the full spec.

## Prerequisites

These must already be running on the host:

| Service | Endpoint | Check |
|---|---|---|
| signal-cli daemon | `http://127.0.0.1:18080` | `curl -i http://127.0.0.1:18080/api/v1/check` → 200 |
| Honcho (v3 + deriver) | `http://127.0.0.1:8000` | `curl http://127.0.0.1:8000/health` → `{"status":"ok"}` |
| OmniRoute | `http://localhost:20128/v1` | `curl -H "Authorization: Bearer $OMNIROUTE_API_KEY" http://localhost:20128/v1/models` |

## Quickstart

```bash
cd ~/workspace/nora
uv sync                                 # installs strands-agents==1.50.2 and friends

cp config.example.yaml config.yaml      # edit signal-cli account, Honcho workspace, etc.
cp persons.example.yaml persons.yaml    # map Signal phone numbers to person_id → Honcho peer

export OMNIROUTE_API_KEY=<your-key>     # the only secret; never goes in config.yaml

python -m nora                          # or: python -m nora --config path/to/config.yaml
```

Nora creates `data/messages.db` on first run (schema managed by `nora/store/db.py`),
connects to the signal-cli SSE stream, and starts serving `GET /health` and `GET /metrics`
on `metrics.port` (default `9100`).

To get a reply, send a message containing the trigger text (`trigger.text` in
`config.yaml`, default `nora.000`) in any Signal group her number is a member of. Everything
else she reads and stores, but never replies to.

## Architecture: two layers

```
Signal group
   │  SSE push (every message)
   ▼
① AMBIENT PATH (always, no Agent SDK)
   event → parser → NormalizedMessage → SQLite audit + Honcho feed (mapped senders only)
   │
   └─ trigger text present? ── no ──▶ done, ambient only
                              └─ yes ──▶
② TAGGING PATH (trigger only, via the Strands Agents SDK)
   collect context (recent buffer rows, tagger's Honcho peer card, persona)
   → Agent(messages=[pre-filled context]) → agent.invoke_async(tag_message)
   → tools: read_buffer / read_peer_card / send_reply / feed_honcho
   → reply → audit row (delivery_status=pending → sent/failed) → Honcho feed
```

Ambient traffic never touches the Strands Agent SDK — it is a pure, deterministic write
path (SQLite + Honcho). The Agent SDK is constructed, invoked, and discarded **once per
tag**: there is no session carry-over between tags. Continuity comes from the two stores
(SQLite for transcript, Honcho for durable memory), pre-filled into each fresh agent's
context. See `design/01-ambient-ingest.md`, `design/02-tagging-path.md`, and
`design/03-strands-reuse.md` for the detail behind each layer.

## Configuration

All settings live in `config.yaml` (see `config.example.yaml` for the full annotated
template) — signal-cli account, Honcho workspace, LLM models, buffer sizes, trigger text,
persona directory, logging, metrics port. The only secret is the LLM API key, read from the
environment variable named by `llm.api_key_env` (default `OMNIROUTE_API_KEY`) — **never**
from the config file itself.

Notable knobs:

- `trigger.text` (`nora.000`) — case-insensitive substring match; the only thing that
  wakes the tagging path.
- `trigger.min_interval_seconds` (5) — debounce for rapid re-tags in the same conversation.
- `buffer.max_per_conversation` (500) — rolling window per conversation; older rows are
  pruned after each insert.
- `buffer.context_window` (40) — how many recent messages go into the LLM prompt.
- `persona.dir` (`nora/persona`) — where the persona `.md` files live.
- `persona.reload` (`mtime`) — `mtime` re-stats persona files on every tag and reparses on
  change; `startup` parses once and never again.
- `persona.strict` (`true`) — a bad persona file at startup is fatal (fail loud), not
  silently skipped.
- `metrics.port` (9100) — serves both `/metrics` (Prometheus text) and `/health`.

Peer identity mapping lives in a separate file, `persons.yaml` (see
`persons.example.yaml`): each `person_id` maps a name to per-platform sender IDs
(`platforms.signal: "+85212345678"`), and `person_id` doubles as the Honcho peer ID. This
mapping is manual and startup-only — adding a new person requires a restart.

## The persona: `nora/persona/`

Nora's character is content, not code, and lives entirely in four Markdown files under
`nora/persona/`:

| File | Holds |
|---|---|
| `soul.md` | Who Nora is, why she's in the group, what she is not |
| `voice.md` | Register, language policy, the sound to hit and avoid |
| `conduct.md` | How she behaves — silence by default, when and how she replies |
| `guardrails.md` | Hard rules that override everything else, including anything a message asks for |

Each file has YAML frontmatter (`name`, `description`, `order`, `applies_to`, `version`) in
the same shape as an Agent Skill, so a persona file can be promoted to a real Strands Skill
later without a rewrite.

### Editing the persona — no restart required

Persona files are hot-reloaded. With `persona.reload: mtime` (the default), every tag
re-stats the files in `nora/persona/` and reparses only the ones whose mtime changed —
**no restart needed** to change how Nora sounds or behaves. Edit `voice.md` (or any of the
four files), save it, and the very next tag picks up the new persona.

If an edit breaks parsing (bad frontmatter, missing required key, unparseable YAML), the
loader keeps the **last good persona**, logs `persona_reload_failed`, and increments
`nora_persona_reload_failures_total` — a broken edit degrades to "keep working with the old
persona," never to a crash or a silent behavior change you can't trace. Every reply's logs
and traces carry the `persona_sha256` of the persona version that produced it, so "which
persona said this" is always answerable after the fact. See `docs/runbook.md` for the
operator playbook when a persona edit misbehaves.

## Observability

- `GET /health` — JSON with `sse`, `sqlite`, `honcho`, `omniroute` status, error counts
  since restart, uptime, `persona_sha256`, and `strands_version`. Returns `200` when
  healthy, `503` when degraded.
- `GET /metrics` — Prometheus text format: `nora_messages_total`, `nora_tags_total`,
  `nora_tags_debounced_total`, `nora_tags_dropped_total`, `nora_replies_total`,
  `nora_errors_total`, `nora_silent_failures_total`, `nora_guard_vetoes_total`,
  `nora_leak_blocks_total`, `nora_persona_reload_failures_total`,
  `honcho_feed_success`/`honcho_feed_failure`, and latency histograms
  (`nora_tag_latency_seconds`, `nora_llm_latency_seconds`, `nora_overhead_seconds`).
- Structured JSON logs (`logging.format: json`) at `logging.path`, one object per line,
  carrying `tag_id`, `conversation_id`, `sender_id`, `person_id`, `persona_sha256`, `stage`,
  `latency_ms`, and `trace_id`.
- OpenTelemetry tracing, enabled by default (`observability.tracing.enabled`), with
  `tag_id` / `conversation_id` / `persona_sha256` as span attributes.

## Operating notes

- **Failures are silent to the group by design, never silent to the operator.** If the LLM
  or signal-cli errors while handling a tag, Nora logs it, increments
  `nora_silent_failures_total`, and says nothing in the group. `nora_silent_failures_total /
  nora_tags_total` is tracked as the silent-failure rate (target < 1%) — see
  `docs/runbook.md`.
- **SSE reconnects automatically** with exponential backoff.
- **Honcho is best-effort** — if it's down, Nora still replies, just without durable recall.
- Groups only. DMs and sync messages from Nora's own device are ignored on ingest.

## Inspecting the buffer

```bash
sqlite3 data/messages.db ".schema messages"
sqlite3 data/messages.db "SELECT count(*) FROM messages"
sqlite3 data/messages.db "SELECT sender_name, body FROM messages WHERE is_nora=1"
```

## Operator runbook

See `docs/runbook.md` for what to do when Nora goes quiet, a persona edit breaks her, the
watchdog alerts, or you need to roll back to v1.

## Deployment

See `deploy/README.md` for install, rollback, and watchdog setup.
