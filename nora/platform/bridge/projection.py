"""The standalone bridge admission-projection publisher (design/platform-bridges §02).

One long-running process per platform emits `/bridge-projection/{platform}.json`
— the read-only transport admission scope every bridge polls by mtime. It is
built from the SAME loaders the app composes (`config.load` for boot state,
`nora.config_reload.ReloadFiles` wrapping `load_policy`/`load_persons` +
`validate_pair` for the live files), never a second authorization parser, so
the file can only ever say what the app's own policy says.

This module is a PURE component in `nora.platform`'s layering sense: it cannot
import `nora.config`/`nora.policy`/`nora.engagement` (import-linter contracts —
platform is a lower layer that receives policy objects, never reads files). The
composition root — `deploy/bridge-projection/entry.py` — therefore injects the
two edges that would violate those contracts: `config_loader` (the schema-only
`config.load`) and `ack_emoji` (the app's queue-receipt constant). Everything
else — `ReloadFiles`, `Persons`, `Metrics`, the `ChannelDirectory` port — is
imported directly and legally.

Semantics, matching `nora.ingest.stages.scope` rather than re-deriving it:

* `rooms` carries every admitted conversation id — a denied room is ABSENT, so
  the bridge drops its bodies/attachments before journaling anything. Discord
  guild entries expand through the same `ChannelDirectory` port the app feeds
  (a live `/v1/reads` snapshot — never a guessed room set).
* `room_receipt_senders` maps every admitted room to its engage/receipt grant:
  the literal `"*"` only for a `persons = "*"` policy wildcard, else the
  sender-id set derived THROUGH `Policy.is_allowed` membership resolved to
  platform ids by `Persons.platform_ids` — the reverse read of the same index
  `Persons.resolve` matches on, so the two can never disagree. Capture and
  receipts are distinct grants: a denied sender's content absorbs into an
  allowed room while they hold no receipt eligibility anywhere in the file,
  and a room-allowed person needs no DM grant for a group receipt.
* `dm_allowed_senders` is `[dm] persons` resolved the same way — a DM from
  anyone else is denied before capture, exactly as scope denies unknown DMs.
* `self_aliases` is the bridge's own `/v1/self` answer unioned with the
  platform's configured account id when the platform carries one (Signal).
  On a failed or absent lookup the FETCHED set empties — self-reply receipt
  eligibility fails closed on identity never established — while room capture
  keeps publishing. `account_key` is `NORA_BRIDGE_ACCOUNT_KEY` verbatim —
  REQUIRED, opaque, ASCII — cross-checked against `platforms.<p>.account_key`
  whenever the config schema carries that field and the platform is enabled.

Failure posture (config_reload's own): an invalid boot publishes an explicit
`enabled: false` deny artifact — fail-closed with nothing authorized. An
invalid RELOAD keeps the last-good holders, so the file simply does not change
and `status()` reports the file stale; the same honesty covers a failed
directory refresh, a failed write, and an env/config account-key mismatch.
None of this needs the app process: the publisher reads files and the bridge's
`/v1/self` + `/v1/reads`, nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import stat
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import aiohttp

from nora.config_reload import POLL_SECONDS, ReloadFiles
from nora.obs.metrics import Metrics
from nora.persons import Persons

__all__ = [
    "DEFAULT_PROJECTION_DIR",
    "ENV_ACCOUNT_KEY",
    "ENV_API_TOKEN",
    "ENV_BASE_URL",
    "ENV_BRIDGE_PORT",
    "ENV_HEALTH_PORT",
    "ENV_PLATFORM",
    "ENV_PROJECTION_DIR",
    "ENV_READS_URL",
    "ENV_SELF_URL",
    "ENV_TOKEN_FALLBACK",
    "PLATFORMS",
    "BridgeChannelDirectory",
    "ProjectionPublisher",
    "run_fleet",
]

log = logging.getLogger("nora.bridge.projection")

#: The platforms contract v1 serves: the projection file is `{platform}.json`,
#: one stream `nq:in:{platform}:{account_key}` behind it.
PLATFORMS: tuple[str, ...] = ("signal", "discord", "whatsapp", "instagram")

DEFAULT_PROJECTION_DIR = "/bridge-projection"
#: Bounded well under POLL_SECONDS so a hung bridge cannot stall a reload tick.
_BRIDGE_FETCH_TIMEOUT = 2.0
#: A directory snapshot older than this is treated as unavailable rather than
#: re-presented as current — the same TTL discipline the discord control
#: worker's own BridgeChannelDirectory holds (reads refresh every tick, so a
#: stale snapshot means the bridge is genuinely gone, not merely quiet).
_DIRECTORY_TTL_SECONDS = 30.0

ENV_PLATFORM = "NORA_BRIDGE_PLATFORM"
ENV_ACCOUNT_KEY = "NORA_BRIDGE_ACCOUNT_KEY"
ENV_PROJECTION_DIR = "NORA_BRIDGE_PROJECTION_DIR"
ENV_SELF_URL = "NORA_BRIDGE_SELF_URL"
ENV_BASE_URL = "NORA_BRIDGE_BASE_URL"
ENV_READS_URL = "NORA_BRIDGE_READS_URL"
ENV_BRIDGE_PORT = "NORA_BRIDGE_PORT"
ENV_API_TOKEN = "NORA_BRIDGE_API_TOKEN"  # noqa: S105 — names an env var, not a secret
ENV_TOKEN_FALLBACK = "NORA_BRIDGE_TOKEN"  # noqa: S105 — contract: API_TOKEN first, TOKEN second
ENV_HEALTH_PORT = "NORA_BRIDGE_PROJECTION_PORT"

#: The exact field set the frozen contract permits — no extras, so no
#: credential, roster entry or message metadata can leak through this file.
_CONTRACT_KEYS = frozenset({
    "revision", "generated_ms", "enabled", "account_key", "rooms",
    "dm_allowed_senders", "self_aliases", "deterministic_triggers",
    "receipt_emoji", "room_receipt_senders", "music_channels",
})

#: Contract bound: revision must survive JSON round-trips in JS exactly.
_MAX_JS_SAFE_INT = (1 << 53) - 1


def _canonical(obj: Any) -> bytes:
    """Sorted-key compact JSON — the only serialization, so two snapshots of
    the same truth are byte-identical and never rewrite the file."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _account_key_ok(key: str) -> bool:
    """The stream leg is exact ASCII: printable, no whitespace, and none of
    the separators the stream/dedupe name formats split on (`:`, `/`)."""
    return bool(key) and key.isascii() and not any(
        ord(c) < 33 or ord(c) > 126 or c in ":/" for c in key
    )


def _revision(semantic: dict[str, Any]) -> int:
    """Content-addressed revision: the hash of the semantic fields (everything
    except `revision`/`generated_ms`), masked to the contract's JS-exact range
    [0, 2**53-1] so `admission_rev` provenance never rounds in a consumer.
    Deterministic — a revert to an earlier state reproduces that state's
    revision, and two publishers that agree on policy agree on the number."""
    return int.from_bytes(hashlib.sha256(_canonical(semantic)).digest()[:8], "big") & (
        _MAX_JS_SAFE_INT
    )


def _semantic(
    *, cfg: Any, policy: Any, persons: Persons, platform: str,
    account_key: str, self_aliases: frozenset[str], ack_emoji: str,
) -> dict[str, Any]:
    """The authorization content of one projection — everything the contract
    fields carry except revision and write time.

    `cfg` is the loaded `nora.config.Config` (read: `platforms`, `engagement`);
    `policy` is the loaded `nora.policy.Policy` (read: `rooms()`,
    `is_allowed`, `dm_persons`, `conversations`) — both duck-typed because
    `nora.platform` may not import their modules (import-linter: the room
    policy is composed in, never imported by a lower layer). `persons` is
    typed: platform is allowed to hold it.
    """
    platform_cfg = getattr(cfg.platforms, platform)
    rooms = sorted(
        {cid for convo in policy.rooms() if convo.platform == platform for cid in convo.ids}
    )
    dm_senders = sorted(
        {i for pid in policy.dm_persons for i in persons.platform_ids(platform, pid)}
    )
    # Per-room receipt/engage grants — derived THROUGH the grant predicate,
    # not beside it: `is_allowed` decides each person's membership exactly as
    # scope does, and `platform_ids` is the reverse read of `resolve`'s index.
    # A room absent here is a receipt deny, matching the contract rule.
    room_receipt_senders: dict[str, Any] = {}
    for convo in policy.rooms():
        if convo.platform != platform:
            continue
        if convo.persons is None:
            grant: list[str] = ["*"]
        else:
            grant = sorted({
                sid for pid in convo.persons
                if policy.is_allowed(
                    platform=platform, conversation_id=convo.primary_id,
                    person_id=pid,
                )
                for sid in persons.platform_ids(platform, pid)
            })
        for cid in convo.ids:
            room_receipt_senders[cid] = grant
    aliases = set(self_aliases)
    if account := getattr(platform_cfg, "account", None):
        aliases.add(account)
    # Music notification routing, Discord only: {guild_id: channel_id} via
    # `Policy.music_channel_for` — an OUTBOUND destination the bridge needs,
    # NOT an admitted room (the loader itself refuses a music_channel that is
    # also a listed conversation). Enumerate the declaring entries and ask
    # the policy; other platforms and unconfigured guilds get {}.
    music_channels: dict[str, str] = {}
    if platform == "discord":
        for convo in policy.conversations:
            if convo.platform != "discord" or not convo.music_channel:
                continue
            guild_id, _, _ = convo.primary_id.partition("/")
            if guild_id and (
                dest := policy.music_channel_for("discord", guild_id)
            ):
                music_channels[guild_id] = dest
    return {
        "enabled": bool(platform_cfg.enabled),
        "account_key": account_key,
        "rooms": rooms,
        "dm_allowed_senders": dm_senders,
        "self_aliases": sorted(aliases),
        "deterministic_triggers": (
            [cfg.engagement.trigger.text] if cfg.engagement.trigger.enabled else []
        ),
        "receipt_emoji": ack_emoji if cfg.engagement.reactions.enabled else None,
        "room_receipt_senders": room_receipt_senders,
        "music_channels": music_channels,
    }


def _deny(account_key: str) -> dict[str, Any]:
    """Boot-invalid semantic: an explicit deny, not silence and never a
    guessed grant. `enabled: false` plus empty scopes authorize no capture
    while still carrying the account leg's identity."""
    return {
        "enabled": False,
        "account_key": account_key,
        "rooms": [],
        "dm_allowed_senders": [],
        "self_aliases": [],
        "deterministic_triggers": [],
        "receipt_emoji": None,
        "room_receipt_senders": {},
        "music_channels": {},
    }


def _write_atomic(path: Path, data: bytes) -> None:
    """temp file in the destination dir -> fsync -> rename -> fsync dir.

    Bridges poll the path by mtime; a torn write would read as a corrupt
    projection and deny everything (fail-closed but wrong) — so the file only
    ever appears whole, and the directory entry is durable before we return.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class BridgeChannelDirectory:
    """`ChannelDirectory` served by the bridge's authenticated `POST /v1/reads`
    `channels` op — the wire shape the discord control worker exposes:
    `{available: bool, value: {channels: {id: name}, threads: {id: parent}}}`.

    The policy port is SYNCHRONOUS and the bridge is not, so — exactly like
    the worker's own `BridgeChannelDirectory` — this answers a TTL-bounded
    snapshot refreshed by the publisher tick: `guild_channels`/`parent_channel`
    read the last successfully fetched map and return `None` the moment it
    goes stale, the same "unavailable" a dead client gives. A failed read
    drops that guild from the next snapshot entirely — absent is deny, never
    a synthesized empty — while `stale` reports it for health. A local
    monotonic timestamp only proves READ time, so freshness below TTL comes
    from the refresh cadence, not the snapshot's own claim.
    """

    def __init__(
        self, *, reads_url: str | None, token: str | None = None,
        ttl_seconds: float = _DIRECTORY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url, self._token = reads_url, token
        self._ttl, self._clock = ttl_seconds, clock
        self._fetched_at = 0.0
        self._channels: dict[str, dict[str, str]] = {}
        self._threads: dict[str, str] = {}
        self._needed: set[str] = set()
        self._failed: set[str] = set()

    def _live(self) -> bool:
        return self._fetched_at > 0 and self._clock() - self._fetched_at < self._ttl

    def guild_channels(self, *, guild_id: str) -> Mapping[str, str] | None:
        if not self._live():
            return None                      # stale == unavailable, deny
        return self._channels.get(guild_id)  # absent == unread/denied guild

    def parent_channel(self, *, channel_id: str) -> str | None:
        if not self._live():
            return None
        return self._threads.get(channel_id)

    @property
    def needed(self) -> set[str]:
        return set(self._needed)

    @property
    def stale(self) -> bool:
        """Health truth: a needed guild whose last read failed, a snapshot
        past TTL, or a guild never enumerated at all."""
        if not self._needed:
            return False
        return (
            bool(self._failed & self._needed)
            or not self._live()
            or bool(self._needed - set(self._channels))
        )

    async def refresh(
        self, session: aiohttp.ClientSession, guild_ids: Iterable[str],
    ) -> None:
        """One read per needed guild → one swapped snapshot. Runs on the
        publisher tick cadence (~3s), far inside the TTL."""
        self._needed = {str(g) for g in guild_ids}
        if not self._needed or self._url is None:
            self._failed = set(self._needed) if self._url is None else set()
            return
        headers = (
            {"Authorization": f"Bearer {self._token}"} if self._token else {}
        )
        channels: dict[str, dict[str, str]] = {}
        threads: dict[str, str] = {}
        failed: set[str] = set()
        for guild_id in self._needed:
            try:
                async with session.post(
                    self._url, headers=headers,
                    json={"op": "channels", "params": {"guild_id": guild_id}},
                ) as resp:
                    resp.raise_for_status()
                    body = await resp.json()
                if not body.get("available"):
                    failed.add(guild_id)
                    continue
                value = body.get("value") or {}
                gmap = value.get("channels")
                if isinstance(gmap, dict):
                    channels[guild_id] = {str(k): str(v) for k, v in gmap.items()}
                tmap = value.get("threads")
                if isinstance(tmap, dict):
                    threads.update({str(k): str(v) for k, v in tmap.items()})
            except Exception as exc:  # noqa: BLE001 — a guild read fails closed
                failed.add(guild_id)
                log.warning(
                    "channel directory read failed for a guild: %s",
                    type(exc).__name__,
                )
        self._channels, self._threads, self._failed = channels, threads, failed
        self._fetched_at = self._clock()


class ProjectionPublisher:
    """Owns the load → reload → serialize → atomic-publish loop for one platform.

    `tick()` is the whole cycle and the test seam: ensure config and the
    policy/identity holders exist (retrying construction while boot sources are
    invalid), poll live files for swaps, refresh the guild directory and self
    aliases, then publish — only when the semantic content actually changed or
    the file vanished or was edited underneath us.

    `config_loader` is the ONLY seam this class cannot supply itself: reading
    config.toml is a composition-root act (import-linter: only app/__main__
    read config), so the deploy entry injects `config.load` — the real loader,
    `require_secrets=False`, not a reimplementation.
    """

    def __init__(
        self, *, platform: str, config_path: Path, out_dir: Path,
        account_key: str, config_loader: Callable[[Path], Any], ack_emoji: str,
        metrics: Metrics | None = None,
        self_url: str | None = None, self_token: str | None = None,
        reads_url: str | None = None,
    ) -> None:
        if platform not in PLATFORMS:
            raise ValueError(f"unknown bridge platform {platform!r}")
        if not _account_key_ok(account_key):
            raise ValueError(
                f"{ENV_ACCOUNT_KEY} is required: non-empty, printable ASCII, "
                "no whitespace or stream-name separators"
            )
        self.platform = platform
        self.config_path = Path(config_path)
        self.out_dir = Path(out_dir)
        self.account_key = account_key
        self._config_loader = config_loader
        self._ack_emoji = ack_emoji
        self.metrics = metrics if metrics is not None else Metrics()
        self.self_url = self_url
        self._reads_url = reads_url
        self._self_token = self_token
        self._cfg: Any = None
        self._reloads: ReloadFiles | None = None
        self._directory = BridgeChannelDirectory(
            reads_url=reads_url, token=self_token,
        )
        self._self_aliases: frozenset[str] = frozenset()
        self._self_stale = True
        self._self_floor = False
        self._disabled = False
        self._key_mismatch = False
        self._write_stale = False
        self._session: aiohttp.ClientSession | None = None
        self.last_write_ms: int | None = None

    @property
    def target(self) -> Path:
        return self.out_dir / f"{self.platform}.json"

    # ---- sources -----------------------------------------------------------

    def _ensure_sources(self) -> bool:
        """True when a validated config AND policy/identity pair are live.

        config.toml is boot-static like the app (`ReloadFiles` watches only the
        two live files), but a FAILED boot load retries every tick — a file
        fixed in place is picked up without a restart, while an invalid one
        keeps the deny artifact in force. The channel directory is attached
        here so every policy candidate — boot and reload — expands guild
        entries through the same live source.
        """
        if self._cfg is None:
            try:
                self._cfg = self._config_loader(self.config_path)
            except Exception as exc:  # noqa: BLE001 — deny, keep running
                log.warning(
                    "config load refused: %s", type(exc).__name__,
                )
                return False
            if self._reads_url is None:
                base = getattr(
                    getattr(self._cfg.platforms, self.platform), "base_url", None,
                )
                if base:
                    self._directory._url = f"{base}/v1/reads"
        if self._reloads is None:
            try:
                self._reloads = ReloadFiles(
                    policy_path=self._cfg.policy_file,
                    identity_path=self._cfg.identity_file,
                    ai_peer=self._cfg.honcho.ai_peer,
                    default_workspace=self._cfg.honcho.workspace_id,
                    metrics=self.metrics,
                    policy_channels=self._directory,
                )
            except Exception as exc:  # noqa: BLE001 — deny, keep running
                log.warning(
                    "policy/identity load refused: %s", type(exc).__name__,
                )
                return False
        return True

    async def _refresh_self(self) -> None:
        """Fetch the bridge's `/v1/self` into the alias set — replace whole.

        A FAILED or absent lookup empties the fetched set: self-reply receipt
        eligibility fails closed until real identity exists, never retaining a
        possibly-rotated alias. The configured `account` floor (Signal) is
        re-added in `_semantic` regardless, and the miss is reported through
        `status()`."""
        if self.self_url is None:
            self._self_aliases = frozenset()
            self._self_stale = not self._self_floor
            return
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_BRIDGE_FETCH_TIMEOUT),
            )
        try:
            headers = (
                {"Authorization": f"Bearer {self._self_token}"}
                if self._self_token else {}
            )
            async with self._session.get(self.self_url, headers=headers) as resp:
                resp.raise_for_status()
                body = await resp.json()
            got = {body.get("primary"), body.get("self_id")} | set(
                body.get("aliases") or ()
            )
            self._self_aliases = frozenset(str(v) for v in got if v)
            self._self_stale = False
        except Exception as exc:  # noqa: BLE001 — fail closed on identity
            self._self_aliases = frozenset()
            self._self_stale = True
            log.warning("self alias refresh failed: %s", type(exc).__name__)

    async def _refresh_directory(self) -> None:
        """Refresh the guild snapshot for exactly the guilds this policy
        covers — nothing read means nothing to deny but also nothing to
        report stale (non-Discord platforms carry no guild entries)."""
        guild_ids = {
            c.guild for c in self._reloads.policy.conversations
            if c.platform == self.platform and c.guild
        }
        if guild_ids and self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_BRIDGE_FETCH_TIMEOUT),
            )
        if self._session is not None:
            await self._directory.refresh(self._session, guild_ids)
        else:
            self._directory._needed = guild_ids  # never-read => stale

    # ---- publish -----------------------------------------------------------

    def _needs_write(self, semantic: dict[str, Any]) -> bool:
        """Write only when the file's truth diverges: first write, a content
        change, or an external edit to heal. `generated_ms`/`revision` are
        excluded from the comparison — identical truth must not churn mtime,
        which is the signal bridges poll."""
        try:
            if stat.S_IMODE(self.target.stat().st_mode) != 0o600:
                return True
            existing = json.loads(self.target.read_bytes())
        except (OSError, json.JSONDecodeError):
            return True
        if not isinstance(existing, dict) or set(existing) != _CONTRACT_KEYS:
            return True
        return any(existing.get(k) != v for k, v in semantic.items())

    def _publish(self, semantic: dict[str, Any]) -> None:
        """Write the artifact, tracking write health HONESTLY: a failed write
        marks the file stale until a new truth actually lands (or the file is
        verified to already hold it) — never `ok` on the strength of an older
        write while the current truth is unwritten."""
        try:
            if not self._needs_write(semantic):
                self._write_stale = False    # file already holds current truth
                return
            generated_ms = int(time.time() * 1000)
            payload = {
                "revision": _revision(semantic),
                "generated_ms": generated_ms,
                **semantic,
            }
            data = _canonical(payload) + b"\n"
            _write_atomic(self.target, data)
        except OSError:
            self._write_stale = True
            raise
        self._write_stale = False
        self.last_write_ms = generated_ms
        log.info("published %s revision %s", self.target.name, payload["revision"])

    async def tick(self) -> None:
        """One poll-and-publish cycle; every failure keeps the loop alive and
        fails closed through the deny artifact or the last-good file."""
        if self._ensure_sources():
            self._reloads.poll()  # swaps only validated candidates; last-good survives refusal
            platform_cfg = getattr(self._cfg.platforms, self.platform)
            self._self_floor = bool(getattr(platform_cfg, "account", None))
            # The env leg must equal config's `platforms.<p>.account_key` when
            # the schema carries that field (merged tree) and the platform is
            # enabled — otherwise the producer names a stream leg the app
            # reader never opens, which is a guessed grant, so deny.
            expected = getattr(platform_cfg, "account_key", None)
            self._key_mismatch = bool(
                platform_cfg.enabled
                and expected is not None
                and expected != self.account_key
            )
            self._disabled = not platform_cfg.enabled
            if self._key_mismatch:
                semantic = _deny(self.account_key)
            elif self._disabled:
                # Disabled: the honest-scope deny file IS the truthful
                # projection — no grants exist for identity or directory
                # freshness to protect, and a platform we never use must not
                # wedge fleet readiness on a missing self source.
                self._self_stale = False
                semantic = _semantic(
                    cfg=self._cfg, policy=self._reloads.policy,
                    persons=self._reloads.persons, platform=self.platform,
                    account_key=self.account_key, self_aliases=self._self_aliases,
                    ack_emoji=self._ack_emoji,
                )
            else:
                await self._refresh_directory()
                await self._refresh_self()
                semantic = _semantic(
                    cfg=self._cfg, policy=self._reloads.policy,
                    persons=self._reloads.persons, platform=self.platform,
                    account_key=self.account_key, self_aliases=self._self_aliases,
                    ack_emoji=self._ack_emoji,
                )
        else:
            self._key_mismatch = False
            semantic = _deny(self.account_key)
        try:
            self._publish(semantic)
        except OSError:
            log.exception("projection write failed; keeping previous file")

    def status(self) -> dict[str, Any]:
        """Honest staleness: true whenever the published file may not reflect
        current truth — boot sources still invalid, a refused reload, a
        missing/stale guild directory, a missing self-identity source, a
        failed write that never landed, or an env/config key mismatch."""
        files = self._reloads.status() if self._reloads is not None else {}
        file_stale = any(f.get("stale", False) for f in files.values())
        dir_stale = self._directory.stale and not self._disabled
        any_stale = (
            file_stale or self._reloads is None or self._self_stale
            or self._write_stale or self._key_mismatch or dir_stale
        )
        return {
            "ok": not any_stale and self.last_write_ms is not None,
            "loaded": self._reloads is not None,
            "stale": any_stale,
            "files": files,
            "self_stale": self._self_stale,
            "write_stale": self._write_stale,
            "key_mismatch": self._key_mismatch,
            "directory": {
                "guilds": sorted(self._directory.needed),
                "stale": dir_stale,
            },
            "last_write_ms": self.last_write_ms,
        }

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def run(self, *, health_port: int = 0) -> None:
        """The long-running loop: tick on the existing reload cadence, forever,
        whether or not the app process exists. Optional /health + /metrics."""
        health = (
            asyncio.create_task(self._serve_health(health_port))
            if health_port else None
        )
        try:
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("projection tick failed")
                await asyncio.sleep(POLL_SECONDS)
        finally:
            if health is not None:
                health.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await health
            await self.close()

    # ---- observability -----------------------------------------------------

    async def _serve_health(self, port: int) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/live", _live)
        app.router.add_get("/metrics", self._metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        # All interfaces: this endpoint is read by a healthcheck from OUTSIDE
        # the container (compose), never published to the host.
        site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
        await site.start()
        log.info("projection health on :%s", port)
        await asyncio.Event().wait()

    async def _health(self, _request: Any) -> Any:
        from aiohttp import web

        st = self.status()
        return web.json_response(
            {"ok": st["ok"], "detail": None if st["ok"] else "stale", **st},
            status=200 if st["ok"] else 503,
        )

    async def _metrics(self, _request: Any) -> Any:
        from aiohttp import web

        return web.Response(text=self.metrics.render())


async def _live(_request: Any) -> Any:
    """Process liveness only — never truth. `/health` stays the readiness
    signal (503 while any grant may be stale); a container orchestrator that
    would restart or gate on staleness MUST probe this route instead, the
    same shape as the sibling bridges' healthy-but-degraded /health."""
    from aiohttp import web

    return web.json_response({"live": True})


async def run_fleet(
    publishers: Iterable[ProjectionPublisher], *, health_port: int = 0,
    metrics: Metrics | None = None,
) -> None:
    """One process hosting every platform's publisher (`--all` mode).

    Each publisher keeps its own loop, file and staleness ledger; the shared
    surface aggregates their `status()` honestly — `/health` is 200 only when
    EVERY running publisher is fresh, `/live` answers while the process
    serves, and `/metrics` renders the one shared registry the fleet was
    built with. A platform's failure degrades its own grants, never a
    sibling's: one bridge down cannot starve another platform's file.
    """
    pubs = list(publishers)
    aggregate = (
        asyncio.create_task(_serve_fleet_health(pubs, health_port, metrics))
        if health_port else None
    )
    try:
        await asyncio.gather(*(p.run() for p in pubs))
    finally:
        if aggregate is not None:
            aggregate.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await aggregate


async def _serve_fleet_health(
    pubs: list[ProjectionPublisher], port: int, metrics: Metrics | None,
) -> None:
    from aiohttp import web

    async def health(_request: Any) -> Any:
        platforms = {p.platform: p.status() for p in pubs}
        ok = all(st["ok"] for st in platforms.values())
        return web.json_response(
            {"ok": ok, "platforms": platforms}, status=200 if ok else 503)

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/live", _live)
    if metrics is not None:
        app.router.add_get(
            "/metrics", lambda _req: web.Response(text=metrics.render()))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    await site.start()
    log.info("fleet projection health on :%s (%d platforms)", port, len(pubs))
    await asyncio.Event().wait()
