---
type: design
title: "Data sources — Steam Web API + Discord gateway state"
description: "What is already wired (identity steam_id, voice_states intent), what needs a new credential (Steam key) and what is explicitly deferred (rich presence intent)."
timestamp: '2026-09-15'
edges:
  - {src: 00-overview, rel: realizes}
  - {src: 01-tool-shape-and-naming, rel: realizes}
---

# Data sources

## Already wired — no new plumbing

### identity.toml already carries Steam IDs

12 `steam_id` values exist in `identity.toml` today (grepped 2026-09-15),
added for Pulse (game-server-monitor) player attribution. The comment at
`identity.toml:85` explains why SteamID64 is the stable key: "steam_id is
stable across every Steam-launched game, matched against Abiotic Factor's
EOS player_key; GET /v1/servers/{id}/players/{playerKey}.steamId". So the
person → SteamID64 mapping the tool needs is **already the identity layer's
data model**, not a new table.

### Discord voice_states intent is already on

`nora/platform/discord/adapter.py:104-107`:

```python
intents = discord.Intents.default()
intents.message_content = True  # privileged; see design/discord-integration/01
intents.guilds = True
intents.members = True          # privileged; the roster read needs it
```

`discord.Intents.default()` includes `voice_states=True` (verified against
the installed discord.py, 2026-09-15). Nora's music player already consumes
voice state. Reading "who is in which voice channel" is therefore **zero new
privileges, zero portal changes** — the listener already holds the gateway
state; the tool reads it.

## New credential — Steam Web API key

Steam's `ISteamUser/GetPlayerSummaries/v2` needs an API key. `.env` has no
`STEAM_API_KEY` today (grep = 0, 2026-09-15).

- Key is **free** (Steam Web API registration: login → key form), standard
  rate limits (100k calls/day per key in practice; GetPlayerSummaries is
  cheap and batchable — up to 100 steamids per call).
- Open question for the operator (see `00-overview.md`): obtain a fresh
  key, or check whether the Pulse stack (game-server-monitor on orion)
  already holds one that can be reused with the operator's blessing.
- Config shape follows the established `[search]` / `[presence]` precedent:
  `[online]` section, `api_key_env = "STEAM_API_KEY"` env-indirection,
  `timeout_seconds` — same fail-closed posture as `PulseGamesClient`:
  missing key / non-200 / non-JSON → `unavailable`, never `nothing_found`
  (R-5, three-state).

## What is read, per arm

| Arm | Source | Call | Shape |
|---|---|---|---|
| `online` | Steam | `GetPlayerSummaries` (batch) + Discord voice states | merged set: person → `{online, in_voice, game?}` |
| `playing` | Steam | `GetPlayerSummaries` → `gameextrainfo` + `gameid` | person → title (absent when not in game) |
| `voice` | Discord | gateway `voice_states` | person → `{channel_id, channel_name?}` |

Note on "online": Steam `personastate` distinguishes online/away/Snooze/in-game.
The tool's `online` arm reports `online | away | in_game` honestly — it must
NOT flatten away/Snooze to online (age-honesty rule, same class as
`where_been` never presenting derived history as "right now").

Fetch cadence: live calls per invoke, no cache in v1. `cacheable=False`
because live state is the point — a cached "playing" answer replayed 30
minutes later is the exact stale-note failure (#248) this tool exists to
kill. (Distinct from `transit`'s cacheable=True: transit answers a stable
schedule; this answers a mutable instant.)

## Explicitly deferred — Discord rich presence

Custom status / playing activity on Discord requires `intents.presences =
True`, which is a **privileged intent** — Discord Developer Portal toggle +
restart, not a code change, and it raises privacy surface (every member's
presence streamed to the bot). v1 ships voice-only on Discord; the Steam arm
already covers "playing what" for the people who have steam_ids. Rich
presence is recorded as deferred in `_index.yaml` `scope.deferred`, and would
be an operator decision at portal level before any code.
