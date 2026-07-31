# Nora

A private ambient agent for Signal group chats. Silent by default; replies only when
tagged with `@Nora`. See [DESIGN.md](DESIGN.md) for the spec.

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
pip install -r requirements.txt        # or: pip install -e .

export OMNIROUTE_API_KEY=<your-key>    # or: cp .env.example .env && edit, then source it

python -m nora
```

Nora creates `data/messages.db` and `data/nora.log` on first run, connects to the
signal-cli SSE stream, and logs `Nora listening as +852...`.

To tag her, send `@Nora <something>` in any Signal group her number is a member of.

## Configuration

All settings live in `config.yaml` — signal-cli account, Honcho workspace, LLM model,
buffer sizes, tag trigger, log level. The only secret is `OMNIROUTE_API_KEY`, read from
the environment (never from the config file).

Notable knobs:

- `buffer.max_per_group` (500) — rolling window; older rows are pruned after each insert.
- `buffer.context_window` (40) — how many recent messages go into the LLM prompt.
- `tag.trigger` (`@Nora`) — case-insensitive substring match.
- `tag.min_interval_seconds` (5) — debounce for rapid re-tags in the same group.

## How it works

Every incoming group message is appended to the `messages` table in SQLite — that table
is the *only* state. There is no session object and no conversation store. When a message
contains the tag, Nora reads the last N rows for that group, fetches the tagger's Honcho
peer card, makes one fresh LLM call, appends her reply as another row, sends it via
signal-cli's JSON-RPC `send`, and feeds the exchange to Honcho's Deriver.

## Operating notes

- **Failures are silent by design.** If the LLM or signal-cli errors, Nora logs it and
  says nothing in the group. Read `data/nora.log` to diagnose.
- **SSE reconnects automatically** with exponential backoff (1s → 60s cap).
- **Honcho is best-effort** — if it's down, Nora replies without durable recall.
- **Media is deferred to v2.** Attachments are stored as `[image]` /
  `[attachment: <filename>]` placeholders; nothing is downloaded.
- Groups only. DMs and sync messages from other devices are ignored.

## Resolved open questions (DESIGN.md §13)

- **Nora's own messages** — filtered entirely on ingest. `parser.parse_event` drops any
  envelope whose `source` is the configured account, and drops `syncMessage` payloads.
  Her replies enter the buffer only via the reply path, with `sender_id='nora'`.
- **Honcho peer ID ≠ phone number.** The spec assumed `+85212345678` would be accepted;
  the live API enforces `^[a-zA-Z0-9_-]+$` and returns 422 on the `+`. `honcho.peer_id()`
  strips the leading `+` (`+85212345678` → `85212345678`). The raw number is still what's
  stored in `messages.sender_id`.
- **Group ID stability** — base64 group IDs are used verbatim as `messages.group_id`.

## Inspecting the buffer

```bash
sqlite3 data/messages.db ".schema messages"
sqlite3 data/messages.db "SELECT count(*) FROM messages"
sqlite3 data/messages.db "SELECT sender_name, body FROM messages WHERE is_nora=1"
```
