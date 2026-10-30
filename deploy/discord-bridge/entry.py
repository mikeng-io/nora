"""discord-bridge — the durable Discord boundary (design/platform-bridges).

Owns the Discord Gateway connection, the deterministic slash-command surface
(`/music`, `/discord`), the `MusicPlayerManager` over the existing SQL
queue/session stores, inbound normalization + capture journal + Redis
enqueue, the membership/presence/channel reads, the durable action API —
AND the `VoiceControllerHub` that adopts and reconciles the external
audio-controllers' voice generations, plus the controller → nora event
consumer. An app-only release leaves all of it running: slash commands keep
working while the app is down, and the app's music/voice sends are forwarded
here (`POST /v1/music/{op}`, `POST /v1/voice/{guild}/speech`) to the SAME
manager and the SAME controller generation.

The bridge owns NO discord.py `VoiceClient`: join/leave go through the hub,
which drives the external controller's `/voice/*` endpoints — the
controller keeps the Discord voice connection and all media playout
(design/platform-bridges/03-ownership-and-recovery.md). The non-queued
wake→utterance HTTP path is unchanged and stays in the app.

Env: DISCORD_BOT_TOKEN, DATABASE_URL, NORA_REDIS_URL,
NORA_BRIDGE_API_TOKEN (or NORA_BRIDGE_TOKEN),
NORA_BRIDGE_CORRELATION_KEY(+_VERSION), NORA_BRIDGE_JOURNAL,
NORA_BRIDGE_PROJECTION_DIR, NORA_BRIDGE_ATTACH_DIR, NORA_BRIDGE_PORT,
NORA_BRIDGE_ACCOUNT_KEY, DISCORD_API_BASE.

Configuration input is the deployment's own `config.toml` (the `--config`
arg, mounted read-only at `/app/config.toml` — the same file and the same
`config.load(require_secrets=False)` reader the app and the bridge
projection use): `[speech.tts] sidecars` maps each guild to its
audio-controller endpoint and `[music]` carries the manager's gates —
there is NO bridge-only voice/music env, because a second truth would
silently diverge from the values the app composed. The graph is built only
when `[platforms.discord] mode = "bridge"` — the exact predicate that makes
the APP relinquish the hub, the manager and the controller-event consumer —
so a `direct` config never produces two owners.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from aiohttp import web

from nora import config
from nora.app.voice_events import VoiceEventConsumer
from nora.infra.music.ytdlp_source import YtDlpSource
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.infra.sql.music_session import SqlMusicSessionStore
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.obs.metrics import Metrics
from nora.platform.bridge.journal import Journal
from nora.platform.bridge.queue import StreamProducer
from nora.platform.bridge.service import (
    DurableActionService,
    JournalCaptureSink,
    JournalPublisher,
    receipt_intent_ids,
)
from nora.platform.discord.bridge_app import (
    DiscordBridge,
    DiscordRestActionService,
)
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.roster import DiscordRoster
from nora.platform.discord.send import DEFAULT_API_BASE

log = logging.getLogger("discord-bridge")
PLATFORM = "discord"

CORRELATION_KEY = bytes.fromhex(os.environ["NORA_BRIDGE_CORRELATION_KEY"])
KEY_VERSION = int(os.environ.get("NORA_BRIDGE_CORRELATION_KEY_VERSION", "1"))
# Required, explicit, opaque — never "default", never derived from a platform
# id: the stream leg must equal the app's [platforms.discord] account_key or
# producer and reader silently name different streams.
ACCOUNT_KEY = os.environ["NORA_BRIDGE_ACCOUNT_KEY"]
if not ACCOUNT_KEY or ACCOUNT_KEY == "default" or not ACCOUNT_KEY.replace(
        "-", "").replace("_", "").isalnum():
    raise SystemExit(
        "NORA_BRIDGE_ACCOUNT_KEY must be a set, opaque [a-z0-9_-] value")
STREAM = f"nq:in:{PLATFORM}:{ACCOUNT_KEY}"
# Canonical name going forward; NORA_BRIDGE_TOKEN is the deployed fallback the
# older bridge services still read — same secret, coherent migration.
TOKEN = os.environ.get("NORA_BRIDGE_API_TOKEN") or os.environ["NORA_BRIDGE_TOKEN"]
BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
PORT = int(os.environ.get("NORA_BRIDGE_PORT", "18093"))
JOURNAL = Journal(Path(os.environ["NORA_BRIDGE_JOURNAL"]))
PROJ_DIR = Path(os.environ.get("NORA_BRIDGE_PROJECTION_DIR", "/bridge-projection"))
CUSTODY_DIR = (
    Path(os.environ["NORA_BRIDGE_ATTACH_DIR"])
    if os.environ.get("NORA_BRIDGE_ATTACH_DIR") else None)
if CUSTODY_DIR is not None:
    # `write_bytes` on an absent dir silently drops every inbound attachment
    # (the custody write is best-effort and only logs) — create it once, at
    # the boundary that owns it, like Journal's parent mkdir.
    CUSTODY_DIR.mkdir(parents=True, exist_ok=True)
API_BASE = os.environ.get("DISCORD_API_BASE", DEFAULT_API_BASE)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="discord-bridge")
    parser.add_argument(
        "--config", default="config.toml",
        help="the deployment's config.toml (mounted read-only in compose)")
    return parser.parse_args(argv)


def load_projection() -> dict[str, Any]:
    try:
        return json.loads((PROJ_DIR / f"{PLATFORM}.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"revision": 0, "enabled": False}


def _client() -> Any:
    """A fresh `discord.Client` per connection attempt — the same
    single-use rule `DiscordAdapter.listen` documented: `start()` on a
    closed client returns immediately, so an attempt owns its instance."""
    import discord
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True          # privileged; the roster/member reads need it
    intents.presences = True        # privileged; member_presence needs it
    # `socket_raw_receive` — the boundary where the Gateway dispatch
    # sequence (`s`) is still attached; the bridge reads it for the
    # clockless reaction identities Discord's parsed events drop.
    return discord.Client(intents=intents, enable_debug_events=True)


def _voice_graph(
        cfg: Any, engine: Any,
) -> tuple[VoiceControllerHub | None, MusicPlayerManager | None,
           VoiceEventConsumer | None]:
    """The graph the APP relinquishes — the exact `_discord_bridge`
    predicate mirrored (config.platforms.discord.enabled AND
    mode == "bridge"), so the hub, the manager and the consumer exist in
    exactly one process for a given config. A `direct` config builds
    nothing here. Values come from `config.load` — `[speech.tts] sidecars`
    and `[music]`, the same source the app composed from; there is no
    bridge-only env truth."""
    owns_voice = (
        cfg.platforms.discord.enabled
        and getattr(cfg.platforms.discord, "mode", "direct") == "bridge")
    if not owns_voice:
        return None, None, None
    # ONE hub — the external controllers own voice connections and playout;
    # the hub owns adoption/reconciliation and the generation every call
    # addresses. `guild_ids` IS the join allow-list, the same object the app
    # handed `voice_guild_ids` — `[speech.tts] sidecars`, read canonically.
    hub = (
        VoiceControllerHub({
            guild_id: VoiceControllerClient(endpoint)
            for guild_id, endpoint in cfg.speech.tts.sidecars_by_guild.items()})
        if cfg.speech.tts.sidecars else None)
    # ONE manager — the deterministic surface's only mutation path. Wired
    # exactly as the app's composition did: existing SQL stores, the real
    # track source, and the hub's probes so liveness/generation teardown
    # reconcile the same way.
    manager = (
        MusicPlayerManager(
            queue_store=SqlMusicQueueStore(engine),
            session_store=SqlMusicSessionStore(engine),
            track_source=YtDlpSource(),
            idle_timeout_seconds=cfg.music.idle_timeout_seconds,
            spawn=asyncio.ensure_future,
            autoplay_enabled=cfg.music.autoplay_enabled,
            autoplay_prewarm_seconds=cfg.music.autoplay_prewarm_seconds,
            playback_probe=hub.expected_playback_id if hub is not None else None,
            read_playback=hub.read_playback if hub is not None else None,
            generation_probe=hub.generation_for if hub is not None else None,
            drop_voice_session=hub.drop if hub is not None else None,
            exit_voice_session=hub.exit if hub is not None else None,
        )
        if cfg.music.enabled else None)
    consumer = (
        VoiceEventConsumer(manager, hub)
        if manager is not None and hub is not None else None)
    return hub, manager, consumer


async def main() -> None:
    cfg = config.load(Path(_parse_args().config), require_secrets=False)
    engine = create_sql_engine(DATABASE_URL)
    hub, manager, consumer = _voice_graph(cfg, engine)
    voice_guild_ids = hub.guild_ids if hub is not None else frozenset()

    redis = aioredis.from_url(os.environ["NORA_REDIS_URL"])
    metrics = Metrics()
    # The shared durable halves (nora.platform.bridge.service) — the same
    # collaborators Signal runs: the ONE journal→stream publisher (capture
    # never enqueues directly; the head blocks on a Redis failure and
    # retries without a restart, so a recovered outage can never let a new
    # event outrun an older journaled one), the capture sink that appends
    # and wakes it, and the ledger-fenced action service whose executor is
    # the REAL Discord REST path — SDK send/react/edit/typing semantics
    # stay behind `DiscordRestActionService`, never reimplemented.
    publisher = JournalPublisher(
        journal=JOURNAL, producer=StreamProducer(redis, STREAM),
        metrics=metrics)
    bridge = DiscordBridge(
        api_token=TOKEN, account_key=ACCOUNT_KEY,
        client_factory=_client,
        bot_token=BOT_TOKEN,
        api_base=API_BASE,
        capture=JournalCaptureSink(
            journal=JOURNAL, publisher=publisher, metrics=metrics),
        publisher=publisher,
        journal=JOURNAL,
        correlation_key=CORRELATION_KEY, key_version=KEY_VERSION,
        custody_dir=CUSTODY_DIR,
        projection=load_projection,
        manager=manager, hub=hub, voice_guild_ids=voice_guild_ids,
        roster=DiscordRoster(BOT_TOKEN, base_url=API_BASE),
        voice_event_sink=consumer,
    )
    bridge.attach(actions=DurableActionService(
        store=SqlBridgeStore(engine), platform=PLATFORM,
        account_key=ACCOUNT_KEY,
        execute=DiscordRestActionService(bridge).run,
        receipt_intents=lambda: receipt_intent_ids(JOURNAL),
        ops=frozenset({"send", "react", "edit", "typing"}),
        metrics=metrics))

    app = bridge.routes()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()  # noqa: S104 — private nora network, token-authed
    log.info("discord-bridge serving :%d (account_key=%s)", PORT, ACCOUNT_KEY)
    # The supervised loop `DiscordBridge.start`'s docstring names: the /v1
    # site and the journal→stream publisher outlive a dead Gateway session
    # (a bad token, an upstream outage) instead of crash-looping the whole
    # process — that is what keeps inbound durably receivable while the app
    # is replaced.
    backoff = 1.0
    while True:
        try:
            await bridge.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — supervisor boundary; logged by type
            log.error(
                "discord gateway listener failed (%s); retrying in %.0fs",
                type(exc).__name__, backoff)
        else:
            log.warning("discord gateway listener returned; reconnecting")
            backoff = 1.0
        # `close()` releases the client AND the publisher; `start()` rebuilds
        # both — a clean client object is the only safe retry surface
        # (discord.py clients are single-use).
        with contextlib.suppress(Exception):
            await bridge.close()
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _bootstrap_obs(config_path: str) -> None:
    """The same observability bootstrap `nora/__main__.py` runs — structured
    JSON logging with the deployment's own redaction values, then tracing
    when `[obs.tracing].enabled`. The bridge reads the SAME config.toml the
    app does, so its lifecycle lines and spans land in the same pipeline.

    Tracing is never load-bearing (`nora.obs.tracing.span`'s own rule): a
    bad endpoint or an unreachable collector warns and the bridge still
    captures."""
    try:
        cfg = config.load(Path(config_path), require_secrets=False)
    except Exception:  # noqa: BLE001 — logging must still come up on a bad config
        cfg = None
    from nora.obs.logging import setup_logging

    if cfg is not None:
        setup_logging(level=cfg.obs.logging.level,
                      format=cfg.obs.logging.format,
                      secrets=cfg.redaction_values())
    else:
        setup_logging(level="INFO", format="json", secrets=[])
    if cfg is None or not cfg.obs.tracing.enabled:
        return
    try:
        from nora.agent.telemetry import setup_agent_tracing
        from nora.obs.tracing import build_sampler

        setup_agent_tracing(
            sampler=build_sampler(), service_name="discord-bridge",
            exporter=cfg.obs.tracing.exporter,
            endpoint=cfg.obs.tracing.endpoint,
            headers_env=cfg.obs.tracing.headers_env,
            credentials=cfg.credentials)
    except Exception as exc:  # noqa: BLE001 — tracing is never load-bearing
        log.warning("tracing setup failed; continuing without tracing: %s",
                    type(exc).__name__)


if __name__ == "__main__":
    _bootstrap_obs(_parse_args().config)
    asyncio.run(main())
