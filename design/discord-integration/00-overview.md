---
type: design
title: "Discord Integration — Overview & Scope"
description: "Why Discord is first, what v1 covers, what's explicitly deferred, and how this bundle fits the wider portal initiative."
tags: [nora, discord, portal, scope]
timestamp: '2026-08-14'
edges: []
---

# Discord Integration — Overview & Scope

## Why Discord, why first

Nora's platform layer already anticipates more than one adapter:
`nora.domain.ports.PlatformAdapter` is a `Protocol`, and
`nora/platform/registry.py::AdapterRegistry` builds and health-checks every
enabled adapter at startup. Only `SignalAdapter` exists today. Discord is the
first realization of that extension point, and simultaneously the first real
exercise of the workspace concept sketched in
`design/proposed/24-honcho-integration-v2.md` — a social unit (a friend group)
spanning more than one platform, where the same person's Signal and Discord
accounts must resolve to one `person_id` / one Honcho peer.

Discord is also the entry point to a wider portal initiative the operator
wants eventually: a PWA conversational surface, STT/TTS, and a real-time
presence map. This bundle is sub-project 1 of that initiative — it covers
**only** Discord bot presence and the portal's login mechanism. The other
three are separate future bundles, not designed here.

## What v1 is

- Nora reachable inside Discord servers as a bot, ambient + tag-to-reply, in
  channels an operator has explicitly allowlisted — see
  `02-engagement-and-policy.md`.
- A resilient message listener that survives Nora being down without losing
  messages, using Discord's own platform guarantees rather than new
  infrastructure — see `01-listener-and-resilience.md`.
- Discord OAuth as a login mechanism for a (future) web portal, gated to
  people already known in `identity.toml` — see
  `03-identity-and-portal-auth.md`.

## What's explicitly out of scope for v1

- **No Signal 1:1 DMs.** Decided during brainstorming (2026-08-14): the
  future portal chat surface is the intended one-on-one surface. Adding
  Signal DMs would be a second way to get the same interaction, and Signal's
  guardrails (roster-scoped disclosure, R-31's fail-closed-on-any-unknown)
  are built around *group* semantics — a DM has no roster, so it's not a
  small addition.
- **No Discord 1:1 DMs**, for the same reason. However, the conversation/room
  model built here must **not** assume "always has a roster" — the identifier
  scheme should be able to represent a DM-shaped conversation later without a
  redesign of the engagement path. This is a forward-compatibility
  constraint on how the room model is shaped, not a feature being built now.
- **No self-service identity provisioning.** A Discord account with no
  existing `identity.toml` binding is refused at login, not
  auto-provisioned. See `03-identity-and-portal-auth.md`.
- **No new "collector" middleware process for Discord.** Discord's own REST
  API already provides the durability signal-cli's separate daemon exists to
  provide for Signal. See `01-listener-and-resilience.md`.
- **The portal's actual chat UI, STT/TTS, and the real-time map** are not
  designed here — this bundle produces only the auth foundation they'll sit
  on top of.

## Backlogged: gap-filling is a PORT concern, not a Discord one

**Backlogged 2026-08-14 (operator).** This started as two separate notes —
"does signal-cli's SSE replay what it buffered while we were disconnected?"
and "Discord needs REST-history backfill after an invalidated session"
(`01-listener-and-resilience.md`). They are the same question asked twice:

> *After Nora was not listening for a while, how does a platform get her the
> messages she missed — and how does she know whether it did?*

Every platform answers it differently (Discord replays on `RESUME` and
otherwise needs `GET /channels/{id}/messages`; signal-cli's replay behaviour
is unverified; a future platform will differ again), but the QUESTION belongs
to `PlatformAdapter`, not to any one adapter. Solving it per-adapter would
produce two unrelated backfill implementations with two different silent
failure modes — and under R-5 the failure mode is the whole point: a gap that
was never filled must be **loud and measured**, not indistinguishable from a
quiet room.

What a design for this must settle, at the port:
- Where the resume point lives (last recorded message id per conversation is
  already in the audit store — no new state should be invented for this).
- The three-state shape of a backfill attempt, R-27's discipline applied to
  history: replayed / nothing-to-replay / **could not tell** — where "could
  not tell" is never rendered as "nothing happened".
- Whether backfilled messages re-enter ingest normally (they should: ambient
  audit + Honcho feed) while never re-triggering an engagement whose moment
  has passed.
- An observable per platform, so an adapter that silently stopped filling
  gaps is visible before someone notices Nora missed a conversation.

Not designed here, not built. It supersedes both original notes; the Discord
half is no longer tracked as a Discord to-do below.

## Backlogged: the operator CLI is Signal-only

**Backlogged 2026-08-14.** `nora groups` and `nora persons pending`
(`nora/app/cli.py`) both read `cfg.platforms.signal` and call
`nora.platform.signal.directory` directly, so neither can name a Discord guild,
channel, or account. The consequence is narrow but real, and it lands squarely
on the identity workflow:

- **Discovering ids is manual.** A guild/channel id comes from the Discord
  client (Developer Mode → Copy Server ID / Copy Channel ID); there is no
  `nora groups` equivalent that prints them beside their names.
- **`persons pending` half-works.** `RecordStore.unattributed` is
  platform-agnostic and `UnattributedRow` carries `platform`, so a Discord
  sender DOES appear once she is ambient in a channel — but the directory
  correlation that turns an id into a human-readable name is Signal-only, so
  it surfaces a bare snowflake with no name beside it.

Same shape as the gap-filling item above: the right fix is a directory
capability on the port (or an explicit "this platform has no directory"
answer), not a second Signal-shaped code path per adapter.

## Status / Checkpoint

_2026-08-14_ — design bundle drafted during brainstorming session, then
built out the same day on `worktree-discord-integration`
(`.claude/worktrees/discord-integration`), merged to master the same day
(`ecd09af`) and deployed.

**Built and green** (`uv run pytest`, `ruff check`, `mypy`, `lint-imports`
all clean; zero regressions against the pre-existing baseline, which has 44
unrelated failing tests of its own):
- `nora/platform/discord/` — `parser.py` (Discord message → `NormalizedMessage`),
  `snowflake.py` (id → timestamp), `send.py` (REST send), `self_id.py`
  (`GET /users/@me`), `listener.py` (Gateway wrapper over `discord.py`),
  `react.py` (`DiscordReactor`, the 👀/✅ path), `roster.py` (`DiscordRoster`,
  the guild members list the room-scoped tools read),
  `adapter.py` (`DiscordAdapter`, satisfies `PlatformAdapter` — proven by
  `tests/contract/test_port_conformance.py::test_discord_adapter_satisfies_platform_adapter`).
- `nora/config.py` — `DiscordPlatformConfig` (`enabled`, `token_env`),
  defaulted so a config.toml written before Discord existed still loads.
- `nora/platform/registry.py` — `AdapterRegistry.from_config` builds and
  resolves a `DiscordAdapter` when `platforms.discord.enabled`, fails fast
  on a missing token; `adapters`/`get()` widened from `dict[str,
  SignalAdapter]` to the `PlatformAdapter` port.
- `nora/app/composition.py` — resolves `DISCORD_BOT_TOKEN` from env
  (`platforms.discord.token_env`) the same way every other `*_env` secret is
  resolved, and passes it to `AdapterRegistry.from_config`.
- `.env`/`.env.example` — `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`,
  `DISCORD_CLIENT_SECRET` placeholders (the latter two for
  `03-identity-and-portal-auth.md`'s OAuth login, not yet built). A real bot
  token is live in both `.env` copies (main checkout and this worktree) and
  was verified against `GET /users/@me`.

_2026-08-14 (second pass)_ — dispatch, outbound routing and health are now
built (DEC-102/R-46), so `platforms.discord.enabled = true` produces a bot that
actually listens and answers in its own channels:
- **Live dispatch wiring.** `App.run` iterates `deps.registry`, one supervised
  task per platform. `Dependencies` gained `registry` beside `adapter`.
- **Outbound routing.** `EngagementPipeline` and `FirePath` hold a
  `platform -> adapter` map keyed by `NormalizedMessage.platform` /
  `Occasion.platform`. This could NOT ship separately from dispatch: live
  dispatch without routing answers Discord tags into Signal groups, so the
  intermediate state is a leak, not a smaller step. An absent route raises
  rather than falling back (R-46).
- **`/health` reporting.** A `platforms` block, one entry per adapter, each
  counting toward the status code. `sse` is untouched (additive-only;
  `deploy/watchdog.sh` parses it), and the per-platform liveness gauge is a NEW
  series `nora_adapter_last_event_age_seconds{platform}` rather than a label
  added to the unlabelled series the watchdog matches on.

**Explicitly NOT built yet** — the next slice, not silently skipped:
- `02-engagement-and-policy.md`'s `policy.toml` schema extension for
  platform-qualified Discord rooms — turned out to already exist
  (`_ConversationEntry.platform`, keyed `(platform, conversation_id)`,
  `policy.example.toml` already documents the `"<guild_id>/<channel_id>"`
  convention) — nothing to build there.
- `03-identity-and-portal-auth.md` (OAuth login, portal auth service) — not
  started.
- REST-history backfill on reconnect (`01-listener-and-resilience.md`) —
  **re-scoped, no longer a Discord to-do.** The Gateway RESUME path is what
  `discord.py`'s `Client.start()` gives for free; the REST fallback for a
  fully invalidated session is now tracked as the cross-platform
  gap-filling item under "Backlogged" above, because Signal has the same
  unanswered question and two per-adapter implementations would grow two
  different silent failure modes.
