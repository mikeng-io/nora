---
type: design
title: "Data sources — Steam Web API + Discord gateway state"
description: "What is already wired (identity steam_id, voice_states intent), what needs a new credential (Steam key) and what is explicitly deferred (rich presence intent)."
timestamp: '2026-09-15'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-tool-shape-and-naming, rel: depends_on, provenance: asserted}
---

# Data sources

## Already wired — no new plumbing

### identity.toml already carries Steam IDs

6 `steam_id` values exist in `identity.toml` today (grepped 2026-09-15:
miles_novak, tyler_cortez, grant_carver, blake_lindqvist, aaron_larsen,
clara_lawson), added for Pulse (game-server-monitor) player attribution. The comment at
`identity.toml` explains why SteamID64 is the stable key —
"SteamID64, not the game's own EOS player_key — steam_id is stable across
every Steam-launched game, matched against Abiotic Factor's login line
(\"山田花子.하나\"/\"熱咖啡\"/... aliases above are the same in-game names Pulse
reports) and confirmed live 2026-08-19 via
GET /v1/servers/{id}/players/{playerKey}.steamId."
So the person → SteamID64 mapping the tool needs is **already the identity
layer's data model**, not a new table. The ids live under
`[[persons.<id>.pulse]]` (nora/domain/identity.py), and the implementation
reads them through `persons.platform_ids("pulse", ...)` — a person with no
`pulse` block has no steam_id and answers `nothing_found`, never a guess.

### Discord presence intents are ON (operator 2026-09-15)

`nora/platform/discord/adapter.py:104-107` (pre-correction):

```python
intents = discord.Intents.default()
intents.message_content = True  # privileged; see design/discord-integration/01
intents.guilds = True
intents.members = True          # privileged; the roster read needs it
```

**CORRECTION (operator 2026-09-15, `#nora` thread):** the Discord half of
this tool is **per-person presence** — status (online / idle / dnd / offline)
plus rich presence (playing a game, listening to music) — NOT voice-channel
membership. The v1 design shipped the wrong preposition: it read "who is in
which voice channel" and called that Discord online state. The operator's
example: a member typing in a forum thread is Discord-online but in no voice
channel; the v1 tool reported "Discord 冇人上線".

The operator has enabled **all three** privileged gateway intents in the
Discord Developer Portal — presence, server members, and message content.
The adapter must add `intents.presences = True` so `member.status` and
`member.activities` are populated. Presence events then stream to the
gateway exactly like voice state does — still a read of state the listener
already holds, now the presence dimension.

Voice-channel membership remains a **narrow sub-arm** (`what="voice"`), never
the meaning of "online".

## New credential — Steam Web API key

Steam's `ISteamUser/GetPlayerSummaries/v2` needs an API key. `.env` has
`STEAM_WEB_API_KEY` now (operator 2026-09-15). Live verification against
the real key succeeded (2026-09-15): all 6 mapped steam_ids returned with
`communityvisibilitystate=3` (**public profiles**), so `gameextrainfo` /
`gameid` are readable for every current member. A privacy caveat governs
future members: Steam honors per-user profile visibility — a Public profile
returns `personastate` + `gameextrainfo`; a Friends-Only/Private profile
hides `gameextrainfo` (and limits state fields). Fail-closed handling: when
`gameextrainfo` is absent but the person is present, the tool reports
`profile_hidden` alongside the state — hidden is NOT offline, and the tool
must never claim "not playing" when the field is simply withheld.

- Key is **free** (Steam Web API registration: login → key form), standard
  rate limits (100k calls/day per key in practice; GetPlayerSummaries is
  cheap and batchable — up to 100 steamids per call).
- Open question for the operator (see `00-overview.md`): obtain a fresh
  key, or check whether the Pulse stack (game-server-monitor on orion)
  already holds one that can be reused with the operator's blessing.
- Config shape follows the established `[search]` / `[presence]` precedent:
  a `[online]` section that **ships INERT by default** — no section, or
  `enabled = false`, registers the tool but has it answer `unavailable`
  (same posture as whereis with no `[presence]` wiring —
  `composition.py:1654-1672`); `api_key_env = "STEAM_API_KEY"`
  env-indirection, `timeout_seconds`. Missing key / non-200 / non-JSON →
  `unavailable`, never `nothing_found` (R-27 core vocabulary; no `refused`
  — there is no guard that says no, see 03-guards).

## What is read, per arm (post-correction)

| Arm | Source | Call | Shape |
|---|---|---|---|
| `online` | Steam + Discord | `GetPlayerSummaries` (batch) + gateway presence | merged set: person → `{status, game?, discord_status?, rich?}` |
| `playing` | Steam | `GetPlayerSummaries` → `gameextrainfo` + `gameid` | person → title (absent when not in game) |
| `voice` | Discord | gateway voice state | person → `{channel_id, channel_name?}` (narrow sub-arm) |

Correction (operator 2026-09-15): the `online` arm's DISCORD side reads
**per-person presence** — `member.status` (`online` / `idle` / `dnd` /
`offline`) plus `member.activities` rich presence (game title from
`discord.Game`, music from `discord.Spotify`/listening) — NOT just voice
channel membership. The two are different questions: "is Blake online on
Discord" vs "is Blake sitting in a voice channel". v1 answered only the
second and the model rendered it as the first.

The `voice` arm is preserved as the explicit channel-membership question —
"有冇人喺 voice channel 傾緊偈" — it does not collapse into `online`.

Note on "online": Steam `personastate` distinguishes online/away/Snooze/in-game.
The tool's `online` arm reports `online | away | in_game` honestly — it must
NOT flatten away/Snooze to online (age-honesty rule, same class as
`where_been` never presenting derived history as "right now").

Fetch cadence: live calls per invoke, no cache in v1. `cacheable=False`
because live state is the point — a cached "playing" answer replayed 30
minutes later is the exact stale-note failure (#248) this tool exists to
kill. (Distinct from `transit`'s cacheable=True: transit answers a stable
schedule; this answers a mutable instant.)

## Explicitly deferred — activity history (still deferred)

"兩三個鐘之前打過咩 game" / "琴日一齊打緊機" needs an **activity event
store** (person → time → state snapshot) — a substrate, not a tool read.
Deferred to the #250 backlog (DEC-115-derived pattern: raw presence samples
rolled into per-person activity spans; a writer clock named at design time).
The v1 live tool answers THIS instant only and must never render like history.

(2026-09-15 correction removed the old "Discord rich presence deferred"
paragraph — that was the wrong preposition; rich presence is now v1-core
with the operator's portal intents.)
