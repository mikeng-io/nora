from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import boto3
from openai import AsyncOpenAI

from nora.app.oauth_keepalive import KeepaliveTiming, KeptLane, OAuthKeepalive
from nora.config import Config
from nora.domain.ports import BlobStore, ImageGenerator
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.obs.metrics import Metrics
from nora.platform.bridge.client import BridgeEndpoint, BridgeMediaSource
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.registry import MediaSourceRegistry
from nora.platform.signal.media import SignalMediaSource

"""Media describer and custody wiring helpers."""


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class _DirectLane:
    """One DIRECT describer transport: where it goes, and whose key it reads.

    `session_header` is OpenCode Go's mandatory `x-opencode-session` (DEC-197):
    its endpoint answers `400 MissingSessionID` without one, so its client is
    built on `opencode_go_http_client`, which stamps a fresh id per request.
    """

    base_url: str
    default_key_env: str
    session_header: bool = False


_DIRECT_LANES = {
    # OmniRoute drops `video_url` content blocks, so video
    # needs a lane of its own; Kimi's endpoint serves K3 with base64 video.
    "kimi": _DirectLane(
        base_url="https://api.kimi.com/coding/v1",
        default_key_env="KIMI_CODE_API_KEY",
    ),
    # MiMo v2.6 Flash, which Kimi's endpoint does not serve and OmniRoute
    # cannot carry video to.
    "opencode-go": _DirectLane(
        base_url="https://opencode.ai/zen/go/v1",
        default_key_env="OPENCODE_GO_API_KEY",
        session_header=True,
    ),
}


def _describe_models(cfg: Config) -> dict[str, str]:
    """Modality → the model that describes it, for every modality that CAN."""
    resolved = {}
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if describer is not None and describer.enabled and describer.model:
            if describer.provider == "omniroute" and "vision" in cfg.model_routes:
                resolved[modality] = cfg.endpoint("vision", describer.model)[2]
            else:
                resolved[modality] = describer.model
    return resolved


def _describe_providers(cfg: Config) -> dict[str, str]:
    """Modality → the provider its describer travels through."""
    resolved: dict[str, str] = {}
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if describer is not None and describer.enabled and describer.model:
            resolved[modality] = describer.provider
    return resolved


def _direct_lane_key_env(cfg: Config, provider: str) -> str:
    """The env var name `provider`'s direct lane reads its key from."""
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if (
            describer is not None
            and describer.enabled
            and describer.model
            and describer.provider == provider
        ):
            return describer.api_key_env or _DIRECT_LANES[provider].default_key_env
    return _DIRECT_LANES[provider].default_key_env


def _direct_describer_clients(cfg: Config) -> dict[str, AsyncOpenAI]:
    """Provider → its direct client, for every lane some live describer selects."""
    from nora.agent.factory import create_client
    from nora.infra.vision.client import opencode_go_http_client

    clients: dict[str, AsyncOpenAI] = {}
    for provider in sorted(
        (set(_describe_providers(cfg).values()) & set(_DIRECT_LANES)) - set(cfg.providers)
    ):
        lane = _DIRECT_LANES[provider]
        key_env = _direct_lane_key_env(cfg, provider)
        api_key = cfg.credentials.get(key_env, "")
        if not api_key:
            log.warning(
                "media describer provider %r selected but %s is unset — "
                "its modalities answer unavailable",
                provider,
                key_env,
            )
            continue
        clients[provider] = create_client(
            base_url=lane.base_url,
            api_key=api_key,
            http_client=opencode_go_http_client() if lane.session_header else None,
        )
    for name in sorted(set(_describe_providers(cfg).values()) & set(cfg.providers)):
        endpoint = cfg.providers[name]
        api_key = cfg.credentials.get(endpoint.api_key_env, "")
        if not api_key:
            log.warning(
                "media describer provider %r has no configured credential — "
                "its modalities answer unavailable",
                name,
            )
            continue
        clients[name] = create_client(
            base_url=endpoint.base_url,
            api_key=api_key,
            http_client=(opencode_go_http_client() if name == "opencode-go" else None),
        )
    return clients


def _describe_max_bytes(cfg: Config) -> dict[str, int]:
    """Modality → the byte ceiling ITS describer is allowed to send."""
    resolved: dict[str, int] = {}
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if describer is not None and describer.enabled and describer.model and describer.max_bytes:
            resolved[modality] = describer.max_bytes
    return resolved


def _image_gen_wiring(
    cfg: Config,
    metrics: Metrics,
    environ: Mapping[str, str],
    *,
    state_dir: Path,
) -> tuple[ImageGenerator | None, OAuthKeepalive | None]:
    """The `generate_image` provider and the OAuth keepalive ([image_gen])."""
    if not cfg.image_gen.enabled:
        return None, None
    from nora.infra.image_gen import (
        OpenAICodexOAuth,
        XaiOAuth,
        build_image_generator,
    )

    image_generator: ImageGenerator | None
    xai_cfg = cfg.image_gen.xai
    codex_cfg = cfg.image_gen.openai_codex
    xai_token = environ.get(xai_cfg.refresh_token_env, "")
    codex_token = environ.get(codex_cfg.refresh_token_env, "")
    xai_lane = (
        XaiOAuth(
            refresh_token=xai_token,
            state_file=state_dir / "xai-oauth.json",
            timeout_seconds=xai_cfg.timeout_seconds,
        )
        if xai_token
        else None
    )
    # DEC-304: the env var holds only the BOOTSTRAP token; the rotating
    # one lives in the state file beside xAI's.
    codex_lane = (
        OpenAICodexOAuth(
            bootstrap_refresh_token=codex_token,
            state_file=state_dir / "openai-codex-oauth.json",
        )
        if codex_token
        else None
    )
    if cfg.image_gen.provider == "openai_codex":
        token_env = codex_cfg.refresh_token_env
        image_generator = (
            build_image_generator(
                provider="openai_codex",
                oauth=codex_lane,
                model=codex_cfg.model,
                responses_model=codex_cfg.responses_model,
                standard_quality=codex_cfg.standard_quality,
                high_quality=codex_cfg.high_quality,
                timeout_seconds=codex_cfg.timeout_seconds,
                max_reference_images=codex_cfg.max_reference_images,
            )
            if codex_lane is not None
            else None
        )
        selected_token = codex_token
    else:
        token_env = xai_cfg.refresh_token_env
        image_generator = (
            build_image_generator(
                provider=cfg.image_gen.provider,
                oauth=xai_lane,
                model=xai_cfg.model,
                quality_model=xai_cfg.quality_model,
                timeout_seconds=xai_cfg.timeout_seconds,
                max_reference_images=xai_cfg.max_reference_images,
            )
            if xai_lane is not None
            else None
        )
        selected_token = xai_token
    if not selected_token:
        log.warning(
            "image_gen enabled but %s is unset — generate_image stays unavailable",
            token_env,
        )
    elif image_generator is None:
        # An unknown provider (or unconstructable lane) degrades
        # the tool, never crashes the daemon.
        log.warning(
            "image_gen enabled but provider %r could not be built — "
            "generate_image stays unavailable",
            cfg.image_gen.provider,
        )
    keeper = OAuthKeepalive(metrics)
    kept: tuple[tuple[KeptLane | None, bool, float], ...] = (
        (xai_lane, xai_cfg.keepalive, xai_cfg.refresh_interval_hours),
        (codex_lane, codex_cfg.keepalive, codex_cfg.refresh_interval_hours),
    )
    for lane, wanted, hours in kept:
        if lane is not None and wanted:
            keeper.register(lane, KeepaliveTiming(interval_seconds=hours * 3600.0))
    return image_generator, (keeper if keeper.lanes else None)


async def _build_media_custody(
    cfg: Config,
) -> tuple[MediaSourceRegistry | None, BlobStore | None]:
    """The `(sources, blobs)` pair the ingest pipeline takes for byte custody, or"""
    media = cfg.media
    if not media.enabled:
        return None, None
    if not media.encryption_key_env:
        log.warning(
            "media.enabled is true but media.encryption_key_env is unset — media "
            "custody is OFF (attachments recorded without custody). Set it to the "
            "name of an env var holding a base64 32-byte key to turn custody on."
        )
        return None, None
    raw = cfg.credentials.get(media.encryption_key_env, "")
    if not raw:
        log.warning(
            "media custody is OFF: %s (media.encryption_key_env) is not set in the "
            "environment. Attachments are recorded without custody until it is.",
            media.encryption_key_env,
        )
        return None, None
    try:
        cipher = BlobCipher.from_base64(raw)
    except ValueError as exc:
        log.warning(
            "media custody is OFF: the key in %s is not a valid base64 32-byte "
            "AES-256 key (%s). Attachments are recorded without custody until it is.",
            media.encryption_key_env,
            exc,
        )
        return None, None
    bridge_token = cfg.credentials.get(cfg.bridges.token_env, "")
    sources = MediaSourceRegistry(
        [
            (
                BridgeMediaSource(
                    "signal", BridgeEndpoint(cfg.platforms.signal.base_url, bridge_token)
                )
                if cfg.platforms.signal.mode == "bridge"
                else SignalMediaSource(
                    base_url=cfg.platforms.signal.base_url,
                    timeout_seconds=media.timeout_seconds,
                    max_bytes=media.max_bytes,
                )
            ),
            (
                BridgeMediaSource(
                    "discord", BridgeEndpoint(cfg.platforms.discord.base_url, bridge_token)
                )
                if cfg.platforms.discord.mode == "bridge"
                else DiscordMediaSource(
                    timeout_seconds=media.timeout_seconds, max_bytes=media.max_bytes
                )
            ),
            *(
                [
                    BridgeMediaSource(
                        "whatsapp", BridgeEndpoint(cfg.platforms.whatsapp.base_url, bridge_token)
                    )
                ]
                if cfg.platforms.whatsapp.mode == "bridge"
                else []
            ),
            (
                BridgeMediaSource(
                    "instagram", BridgeEndpoint(cfg.platforms.instagram.base_url, bridge_token)
                )
                if cfg.platforms.instagram.mode == "bridge"
                else InstagramMediaSource(
                    timeout_seconds=media.timeout_seconds, max_bytes=media.max_bytes
                )
            ),
        ]
    )
    blobs = await _build_blob_store(cfg, cipher=cipher)
    return sources, blobs


async def _build_blob_store(cfg: Config, *, cipher: BlobCipher) -> BlobStore:
    """The filesystem default, or RustFS when `[media.rustfs]` is on and its"""
    rustfs = cfg.media.rustfs
    fallback = FilesystemBlobStore(cfg.store.data_dir / "media", cipher=cipher)
    if not rustfs.enabled:
        return fallback
    if not rustfs.endpoint:
        log.warning(
            "media.rustfs.enabled is true but media.rustfs.endpoint is unset — "
            "falling back to the filesystem blob store."
        )
        return fallback
    access_key = cfg.credentials.get(rustfs.access_key_env, "")
    secret_key = cfg.credentials.get(rustfs.secret_key_env, "")
    if not access_key or not secret_key:
        log.warning(
            "media.rustfs is on but %s/%s did not both resolve in the "
            "environment — falling back to the filesystem blob store.",
            rustfs.access_key_env,
            rustfs.secret_key_env,
        )
        return fallback
    s3 = boto3.client(
        "s3",
        endpoint_url=rustfs.endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    store = RustfsBlobStore(s3_client=s3, bucket=rustfs.bucket, cipher=cipher)
    try:
        await asyncio.to_thread(store.ensure_bucket)
    except Exception:
        # RustFS unreachable, or misconfigured beyond what the checks above catch (wrong
        # credentials, wrong bucket permissions). This runs at STARTUP, before
        # build_dependencies' own per-message protection (nora.ingest.stages.media._take…
        log.exception(
            "media.rustfs is configured but the bucket could not be reached "
            "or created — falling back to the filesystem blob store."
        )
        return fallback
    return store
