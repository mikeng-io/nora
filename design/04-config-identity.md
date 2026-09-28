---
kind: design
title: "Nora — Configuration & Identity"
description: "Everything identity-related is config, never hardcoded: trigger text, Signal account (from signal-cli's own configuration), Honcho workspace, model, peer mapping (manual). Secrets from environment."
tags: [nora, design, config, identity, dynamic, peer-mapping]
timestamp: '2026-08-03'
edges:
  - {dst: ../00-overview.md, rel: decided_by, provenance: asserted}
  - {dst: ../01-ambient-ingest.md, rel: used_by, provenance: asserted}
  - {dst: ../02-tagging-path.md, rel: used_by, provenance: asserted}
---

# Nora — Configuration & Identity

## Principle

**Nothing identity-related is hardcoded.** Every value that identifies Nora or her
world — the trigger text, the Signal account, the Honcho workspace, the LLM model,
the peer mapping — is read from configuration at runtime. The only secret
(`OMNIROUTE_API_KEY`) comes from the environment, never from a config file.

## Configuration Sources

| Source | Holds | Notes |
|---|---|---|
| `config.toml` | trigger, buffer, tag, logging, endpoints, peer mapping | operator-edited, git-tracked |
| environment | `OMNIROUTE_API_KEY` | the only secret |
| signal-cli daemon config | the Signal account Nora runs as | Nora derives her identity from the daemon, not from a copy in code |

### Signal account — derived, not duplicated

Nora's Signal identity is the account the signal-cli daemon is running as
(single-account mode: `--account +852****7304`). Nora does **not** hardcode the
number; she reads it from the daemon's configuration/health at startup, or receives
it via config when the daemon is multi-account. The point: one source of truth for
who Nora is on Signal — the daemon's configuration.

## config.toml (target shape, audit 2nd-pass)

Note: this record was drafted while the config format was still planned as YAML; the
shape below is unchanged, but the implemented file is `config.toml` (Phase 1a), not
`config.yaml` — the fence is left as YAML since it is illustrating shape, not syntax.

```yaml
# Nora configuration
signal_cli:
  base_url: "http://127.0.0.1:18080"
  account: ""                # empty = derive from signal-cli daemon configuration
  nora_sender_id: "nora"     # how Nora's own messages are tagged in DB

honcho:
  base_url: "http://127.0.0.1:8000"
  workspace_id: "nora"

llm:
  base_url: "http://localhost:20128/v1"   # OmniRoute OpenAI-compatible (shared)
  api_key_env: "OMNIROUTE_API_KEY"         # env var name, not the value
  default:                                 # flash tier — casual replies
    model: "deepseek-v4-flash"
    max_tokens: 400
    temperature: 0.7
  deep:                                    # escalation tier (v2.1 policy)
    model: "deepseek-v4-pro"
    max_tokens: 1200
    temperature: 0.5

trigger:
  text: "nora.000"            # dynamic — any string, case-insensitive substring
  min_interval_seconds: 5     # debounce

buffer:
  db_path: "~/workspace/nora/data/messages.db"
  max_per_conversation: 500   # per-conversation rolling cap (platform-neutral)
  context_window: 40

persons_file: "persons.yaml"  # manual relation mapping (operator-maintained)

logging:
  level: "INFO"
  path: "~/workspace/nora/data/nora.log"

metrics:
  port: 9100                  # Prometheus text format on a separate port
```

## Peer Mapping — manual, single relation file (audit A2, 2026-08-03)

**Why manual:** mapping platform sender IDs to human identities is operator
knowledge — the Deriver can build a peer card, but it cannot decide "this number is
Miles". So the operator maintains **one relation file** (`identity.toml` — named
`persons.yaml` at the time of this record; see the note under "config.toml" above),
referenced from config. No service, no UI, no schema machinery — it is a simple
manual mapping, exactly as it must be.

**Two-level mapping for cross-platform identity:** a stable `person_id` is the
anchor; each person lists per-platform sender IDs. The Honcho peer ID is derived
from `person_id`. One person = one Honcho peer across Signal today and
Discord/Telegram/WhatsApp later.

```yaml
# persons.yaml — manual relation mapping (operator-maintained)
persons:
  miles:
    name: "Miles"
    platforms:
      signal: "+85212345678"
      # discord: "123456789012345678"     # future
      # telegram: "123456789"            # future
      # whatsapp: "85212345678@s.whatsapp.net"  # future
  alice:
    name: "Alice"
    platforms:
      signal: "+85287654321"
```

**Derived rules:**
- `honcho_peer_id = person_id` (e.g. `miles`) — Honcho's `^[a-zA-Z0-9_-]+$` constraint
  is satisfied by construction (the `+` in phone numbers never reaches Honcho).
- Lookup is by (platform, sender_id) → person_id → name + Honcho peer ID. A reverse
  index is built at startup.

**Behavior:**
- On first sighting of an **unmapped** sender, Nora logs `unknown peer <id>` and uses
  a sanitized fallback (`852...`) — she still records and can reply, just without the
  human identity. **Fix (audit A2):** ambient Honcho feed for unmapped senders is
  skipped (SQLite still captures them for context) — avoids stranding Honcho history
  on fallback IDs that would later need a rename/migration. The operator adds the
  mapping; the next tag uses it.
- Renaming a person (`miles` → `michael`) is a Honcho peer-rename operation — document
  it as a migration, not an edit.

## Trigger Text — fully dynamic

`trigger.text` is any string (default `nora.000`), matched case-insensitively as a
substring in the message body, in the ambient layer, before any agent work. Changing
the trigger is a config edit — no code change. The trigger is deliberately not tied
to Signal mentions (`@Nora`) because Signal mention support in signal-cli is
inconsistent; substring matching on a distinctive token is the robust primitive.

## Honcho Workspace

> **Superseded 2026-09-28 (DEC-214):** one Honcho workspace per Nora
> WORKSPACE (a group of units in `policy.toml` `[units]`, bound in
> `identity.toml` `[workspaces]`); `workspace_id` is only the binding of the
> implicit workspace when there is no `[units]`. The paragraph below is the
> original design.

One Honcho workspace per Nora instance (`workspace_id: "nora"`), created idempotently at
startup. Peers are created idempotently on first sighting **using the mapped
`honcho_peer_id` only** (audit 2nd-pass F10/F19): unmapped senders do not get a
Honcho peer — their ambient messages are SQLite-only, and the tagging path simply
has no peer card for them.

## Secrets

- `OMNIROUTE_API_KEY` — environment only. Never in `config.toml`, never in logs,
  never in the repo. `.env` is gitignored; `.env.example` is the template.

## Open Questions

1. **Multi-account signal-cli:** if the daemon ever runs multiple accounts, Nora needs
   an explicit `account` in config (the field exists, default empty). Single-account
   today — derive from daemon; multi-account later — set explicitly.
2. **Peer mapping UX:** plain YAML today. If the group grows, consider a small
   admin command (e.g. `nora map <sender> <name>`) — out of scope for v2.
