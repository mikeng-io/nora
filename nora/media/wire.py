import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.agent.telemetry import RawSpanRecorder
from nora.agent.video_desk import VideoDesk
from nora.app.composition_media import (
    _build_media_custody,
    _describe_max_bytes,
    _describe_models,
    _describe_providers,
    _direct_describer_clients,
    _image_gen_wiring,
)
from nora.app.composition_readers import fetch_provider, search_provider
from nora.domain.media_join import MediaJoin
from nora.domain.video import VIDEO_KIND
from nora.infra.sql.media_descriptions import SqlMediaDescriptionStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.vision import MediaComprehender, VisionClient
from nora.ingest.comprehension import MediaComprehension
from nora.ingest.stickers import StickerReader
from nora.media.adapters.tools import build as _build

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


def media_fetch_impl(deps):
    from nora.media.adapters.tools.fetch import _make_media_fetch

    return _make_media_fetch(deps)


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class StoresWiring:
    media_objects: Any


def stores(engine, metrics) -> StoresWiring:
    # The unified media index (DEC-138), built HERE rather than beside the vision client
    # below because THREE readers now hold it and the earliest is the context assembler:
    # ingest writes the custody row, the comprehension job writes the read, the assembler…
    media_objects = SqlMediaObjectStore(engine, metrics=metrics)
    return StoresWiring(media_objects=media_objects)


@dataclass(frozen=True)
class JoinWiring:
    media_join: Any


def join(cfg, content_checks, media_objects, metrics) -> JoinWiring:
    # ONE media join for the whole process, handed to every transcript builder there is: the
    # tag assembler, the fire path, the tag-path debrief, the Cognition session and lazy
    # adoption, so none renders bare handles where another renders the comprehension.…
    media_join = MediaJoin(
        store=media_objects,
        screen=functools.partial(scan_identifiers, content_checks=content_checks),
        metrics=metrics,
        render_chars=cfg.media.comprehension_render_chars,
        # The per-QUERY timeout, on every path. The two slot-held callers wrap `enrich` in their
        # own `wait_for` as well; this is what bounds the store read for the six that do not,
        # and it is deliberately shorter than `[standing] store_timeout_seconds` so the inner…
        timeout_seconds=cfg.media.join_timeout_seconds,
    )
    return JoinWiring(media_join=media_join)


@dataclass(frozen=True)
class SourcesWiring:
    blobs: Any
    media_sources: Any


async def sources(cfg) -> SourcesWiring:

    media_sources, blobs = await _build_media_custody(cfg)
    return SourcesWiring(blobs=blobs, media_sources=media_sources)


@dataclass(frozen=True)
class DescribersWiring:
    comprehension: Any
    media_descriptions: Any
    sticker_reader: Any
    transcriber: Any
    vision_client: Any


def describers(
    _spawn,
    blobs,
    cfg,
    content_checks,
    engine,
    media_objects,
    memory,
    metrics,
    model_client_for,
    route_model,
    tier,
) -> DescribersWiring:
    # `media_fetch` (DEC-56) reads a stored blob back through MiMo. Its two collaborators
    # are wired together: the blob store (already built above, `None` while `[media]
    # enabled=false` or unkeyed) and a `VisionClient` over the process-wide OmniRoute…
    media_descriptions = SqlMediaDescriptionStore(engine, metrics=metrics)
    # The per-modality DIRECT lanes (DEC-197): a describer configured with a direct provider
    # travels through its own client, NOT the process-wide OmniRoute client — OmniRoute
    # drops `video_url` content blocks, so a video described through it would reach the…
    vision_client = VisionClient(
        client=model_client_for("vision"),
        direct_clients=_direct_describer_clients(cfg),
        model=route_model("vision", cfg.media.vision_model),
        max_tokens=cfg.media.vision_max_tokens,
        reasoning_effort=cfg.media.vision_reasoning_effort,
        max_bytes=cfg.media.vision_max_bytes,
        # THE DERIVATION'S INPUTS, NOT ITS RESULT. The divisor is the SERIAL factor — how many
        # 90-second windows one invocation reads in sequence — and `cut_video` only produces
        # that per request, at read time, a process after this line runs. The client derives…
        timeout_seconds=cfg.media.vision_timeout_seconds,
        loop_timeout_seconds=tier.timeout_seconds,
        # The model-span seam (ADR 0034): the raw vision call gets its own
        # per-call span with usage, served model and provider — injected
        # because `nora.infra` may not reach `nora.agent`.
        span_recorder=RawSpanRecorder(),
    )
    # The sticker IDENTITY ladder — custody, not description. Its model rung is the spawned
    # comprehension job below, so this needs no interpreter and no second `VisionClient`:
    # what it buys is a repeat sighting costing zero platform RPC, decided at parse time…
    sticker_reader = None
    if blobs is not None:
        sticker_reader = StickerReader(
            blobs=blobs,
            descriptions=media_descriptions,
            metrics=metrics,
        )
    # THE COMPREHENSION JOB — one model pass per medium into title/description/content,
    # spawned off the ingest path. Wired only when custody is on AND `[media] comprehension`
    # is set: with either off, every attachment keeps its bare handle and nothing anywhere…
    comprehension: MediaComprehension | None = None
    if cfg.media.comprehension and blobs is not None:
        comprehension = MediaComprehension(
            blobs=blobs,
            store=media_objects,
            interpreter=MediaComprehender(
                client=vision_client,
                describe_models=_describe_models(cfg),
                describe_providers=_describe_providers(cfg),
                describe_max_bytes=_describe_max_bytes(cfg),
                text_model=(
                    cfg.endpoint("vision", cfg.media.comprehension_text_model)[2]
                    if cfg.media.comprehension_text_model
                    else ""
                ),
                chunk_seconds=cfg.media.describe.video.chunk_seconds,
                max_chunks=cfg.media.describe.video.chunk_max,
            ),
            memory=memory,
            metrics=metrics,
            spawn=_spawn,
            stickers_enabled=cfg.media.sticker_descriptions,
            max_concurrent=cfg.media.comprehension_max_concurrent,
            content_checks=content_checks,
        )
    # `[speech.stt]`'s transcriber: wired ONLY when enabled — the same inert-ship rule as
    # `stickers`. Off leaves the ingest stage without a transcriber and a voice note is
    # recorded with a bare handle. On, the local `stt` service transcribes voice notes…
    transcriber = None
    if cfg.speech.stt.enabled:
        from nora.infra.speech import SenseVoiceTranscriber

        transcriber = SenseVoiceTranscriber(
            endpoint=cfg.speech.stt.endpoint,
            language=cfg.speech.stt.language,
            timeout_seconds=cfg.speech.stt.timeout_seconds,
            model=cfg.speech.stt.model,
        )
    return DescribersWiring(
        comprehension=comprehension,
        media_descriptions=media_descriptions,
        sticker_reader=sticker_reader,
        transcriber=transcriber,
        vision_client=vision_client,
    )


@dataclass(frozen=True)
class SearchWiring:
    fetch_client: Any
    search_client: Any


def search(cfg) -> SearchWiring:
    # `web_search`/`web_fetch` (DEC-73/DEC-116) are wired ONLY when `[search] enabled` AND
    # the SELECTED backend's key is present — the `route`-keyless gate. Off (or keyless)
    # leaves the registry without a provider and both tools answer `unavailable` (R-5),…
    search_client = search_provider(cfg)
    # `web_fetch`'s OWN backend (`[search] fetch_provider`): None means the
    # one search client serves both verbs, the pre-split shape.
    fetch_client = fetch_provider(cfg)
    return SearchWiring(fetch_client=fetch_client, search_client=search_client)


@dataclass(frozen=True)
class ImagesWiring:
    image_generator: Any
    image_refs_allowed: Any
    oauth_keepalive: Any
    preview_source: Any


def images(cfg, metrics) -> ImagesWiring:
    # Image generation ([image_gen], DEC-137): enabled AND refresh token present → build the
    # lane (the same keyless-`enabled` gate `route` uses). Off (or tokenless) leaves the
    # `generate_image` tool answering `unavailable` — never a crash, never `nothing_found`…
    image_refs_allowed = cfg.image_gen.enabled and cfg.image_gen.allow_reference_images
    # Image custody: generated images are stored in Nora's
    # OWN blob_store (rustfs) and sent as platform attachments.
    image_generator, oauth_keepalive = _image_gen_wiring(
        cfg,
        metrics,
        cfg.credentials,
        state_dir=Path("/app/state"),
    )
    # Sender-generated link previews ([preview]). Keyless, so `enabled` is the whole gate.
    # Off is the shipped default and is not a degradation: a reply carrying a link goes out
    # with no card. It is ONE instance for the process (it holds no per-turn state), handed…
    preview_source = None
    if cfg.preview.enabled:
        from nora.infra.preview import OpenGraphPreviewSource

        preview_source = OpenGraphPreviewSource(
            timeout_seconds=cfg.preview.timeout_seconds,
            max_bytes=cfg.preview.max_bytes,
            fetch_image=cfg.preview.image,
            image_max_bytes=cfg.preview.image_max_bytes,
        )
    return ImagesWiring(
        image_generator=image_generator,
        image_refs_allowed=image_refs_allowed,
        oauth_keepalive=oauth_keepalive,
        preview_source=preview_source,
    )


@dataclass(frozen=True)
class VideoWiring:
    video_desk: Any


def video(cfg, clock, compute_backends, compute_jobs, metrics) -> VideoWiring:
    # Video generation ([video_gen]): enabled AND the key named by `[video_gen.minimax_cn]
    # api_key_env` present builds the MiniMax CN backend, registered under `VIDEO_KIND` for
    # the poller, and the desk `video_create` calls. The config refuses `enabled` without…
    video_desk: VideoDesk | None = None
    if cfg.video_gen.enabled:
        video_cfg = cfg.video_gen.minimax_cn
        video_key = cfg.credentials.get(video_cfg.api_key_env, "")
        if video_key:
            from nora.infra.video_gen import MiniMaxVideoBackend

            video_backend = MiniMaxVideoBackend(
                api_key=video_key,
                model=video_cfg.model,
                resolution=cfg.video_gen.resolution,
                duration_seconds=cfg.video_gen.duration_seconds,
                ratio=cfg.video_gen.ratio,
                metrics=metrics,
                host=video_cfg.host,
                timeout_seconds=video_cfg.timeout_seconds,
                reference_seconds=cfg.video_gen.reference_seconds,
            )
            compute_backends[VIDEO_KIND] = video_backend
            video_desk = VideoDesk(
                jobs=compute_jobs,
                backend=video_backend,
                clock=clock,
                tz=cfg.analytics.tz_name,
                max_per_room_per_day=cfg.video_gen.max_clips_per_room_per_day,
            )
        else:
            log.warning(
                "[video_gen] enabled but %s is unset; video_create is withheld",
                video_cfg.api_key_env,
            )
    return VideoWiring(video_desk=video_desk)
