from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nora.app.composition_readers import (
    _story_frames_embedded,
    social_person_rooms,
    social_person_units,
    social_unit_rooms,
)
from nora.app.social_expiry import StoryMediaExpiry
from nora.app.social_perception import SocialPerception
from nora.app.social_poller import SocialPoller
from nora.app.summons_inbox import SummonsInbox
from nora.domain.live import LiveMapping
from nora.infra.embedder import fingerprint
from nora.infra.sql.social_stream import SqlSocialStreamStore, readable_social_store
from nora.infra.sql.source_health import SqlSourceHealthLog

"""Social runtime wiring steps: inbox, summons, poller."""


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class InboxWiring:
    summons_inbox: Any
    summons_wired: Any


def inbox(cfg: Any, metrics: Any)-> InboxWiring:

    # The public Instagram summons' intake: the Instagram adapter hands every `summons` SSE
    # event here. ALWAYS built, so a card that arrives while the summons is off is counted
    # `disabled` rather than silently absent; `enabled` only under BOTH `[social.instagram]…
    summons_wired = bool(cfg.social.instagram.enabled and cfg.social.instagram.summons.enabled)
    summons_inbox = SummonsInbox(enabled=summons_wired, metrics=metrics)
    return InboxWiring(summons_inbox=summons_inbox, summons_wired=summons_wired)


@dataclass(frozen=True)
class SummonsWiring:
    pass


def summons(
    background: Any, cfg: Any, clock: Any, content_checks: Any,
    doctrine_store: Any, engine: Any, limits: Any, metrics: Any,
    persons: Any, registry: Any, social_subjects: Any, summons_inbox: Any,
    summons_runner: Any, summons_wired: Any, tier: Any, tool_registry: Any,
) -> SummonsWiring:

    # The public Instagram summons consumer, gated on BOTH `[social.instagram] enabled` and
    # its own `summons.enabled` (the config refuses the second without the first) — the same
    # `summons_wired` that enabled the inbox above, so a queued card always has a consumer.…
    if summons_wired and summons_runner is not None and social_subjects is not None:
        from nora.app.summons import SummonsOccasion
        from nora.app.summons_consumer import SummonsConsumer
        from nora.infra.social.summons_client import InstagramSummonsClient
        from nora.infra.sql.summons import SqlSummonsLedger

        summons_cfg = cfg.social.instagram.summons
        summons_client = InstagramSummonsClient(
            base_url=cfg.social.instagram.base_url,
            timeout_seconds=cfg.social.instagram.timeout_seconds,
        )
        summons_ledger = SqlSummonsLedger(engine)
        summons_consumer = SummonsConsumer(
            inbox=summons_inbox,
            occasion=SummonsOccasion(
                client=summons_client,
                ledger=summons_ledger,
                summoners=social_subjects,
                persons=persons,
                doctrine_store=doctrine_store,
                registry=tool_registry,
                runner=summons_runner,
                metrics=metrics,
                canary=cfg.doctrine.canary,
                handle=summons_cfg.handle,
                mode=summons_cfg.mode,
                max_age=timedelta(minutes=summons_cfg.max_age_minutes),
                limits=limits,
                timeout_seconds=tier.timeout_seconds,
                clock=clock,
                content_checks=content_checks,
            ),
            ledger=summons_ledger,
            metrics=metrics,
            clock=clock,
        )
        background.spawn(summons_consumer.run())
        log.info("instagram summons consumer wired (mode=%s)", summons_cfg.mode)
        if "instagram" not in registry:
            # The cards arrive on the MESSAGING adapter's SSE stream, so a
            # summons needs `[platforms.instagram]` too. Said loudly rather
            # than refused at load: the consumer idles harmlessly without it.
            log.error(
                "[social.instagram.summons] is enabled but [platforms.instagram] "
                "is not: summons cards arrive on the Instagram adapter's event "
                "stream, so no summons will ever be received",
            )
    return SummonsWiring()


@dataclass(frozen=True)
class PollerWiring:
    social_perception: Any
    social_poller: Any


def poller(
    allowlist: Any, blobs: Any, cfg: Any, comprehension: Any,
    engine: Any, media_objects: Any, media_sources: Any, metrics: Any,
    persons: Any, social_subjects: Any, stimulus_store: Any,
) -> PollerWiring:

    # The social stream's poller (design/cognition-loop/22-social-stream.md): built here,
    # started by `nora/__main__.py`, because the eval subject composes this graph too and
    # must not read members' accounts through the deployment's login. `[social.poll]…
    social_poller: SocialPoller | None = None
    social_perception: SocialPerception | None = None
    if cfg.social.poll.enabled and social_subjects is not None:
        from nora.infra.social.instagram_source import InstagramSocialSource
        from nora.infra.social.subjects import instagram_roster

        # ONE store for the poller and every reader, holding who opted out:
        # the read-side exclusion lives in the store's reads (§2).
        social_store = readable_social_store(engine, persons, metrics=metrics)
        social_poller = SocialPoller(
            source=InstagramSocialSource(
                base_url=cfg.social.instagram.base_url,
                timeout_seconds=cfg.social.instagram.timeout_seconds,
            ),
            store=social_store,
            health=SqlSourceHealthLog(engine),
            roster=functools.partial(instagram_roster, social_subjects),
            settings=cfg.social.poll,
            metrics=metrics,
            units=LiveMapping(lambda: social_person_units(allowlist)),
            rooms=LiveMapping(lambda: social_unit_rooms(allowlist)),
            timezone=cfg.cognition.timezone,
            # The same custody every room's media goes through; `None` while
            # media custody is off, and then items are recorded without bytes.
            media=media_sources.for_platform("instagram") if media_sources else None,
            blobs=blobs,
            media_objects=media_objects,
        )
        # Perception needs `[media] comprehension` (`comprehension` is None
        # without it, and then items stay unperceived and their reads are
        # announced as recorded); the stimulus needs `[cognition] enabled`.
        social_perception = SocialPerception(
            store=social_store,
            media_objects=media_objects,
            comprehension=comprehension,
            stimuli=stimulus_store,
            units=LiveMapping(lambda: social_person_units(allowlist)),
            rooms=LiveMapping(lambda: social_person_rooms(allowlist)),
            daily_cap=cfg.social.poll.perception_daily_cap,
            tz=ZoneInfo(cfg.cognition.timezone),
            chunk_seconds=cfg.media.describe.video.chunk_seconds,
            chunk_max=cfg.media.describe.video.chunk_max,
            tick_seconds=cfg.social.poll.tick_seconds,
            metrics=metrics,
        )
        log.info("social poller wired (instagram)")
    return PollerWiring(social_perception=social_perception, social_poller=social_poller)


@dataclass(frozen=True)
class ExpiryWiring:
    story_expiry: Any


def expiry(blobs: Any, cfg: Any, engine: Any, metrics: Any)-> ExpiryWiring:

    # The bound is on bytes the poller already kept, so it holds while the
    # poller is off; it reads every person's frames, opted out or not.
    story_expiry = (
        StoryMediaExpiry(
            store=SqlSocialStreamStore(engine),
            blobs=blobs,
            keep_days=cfg.social.story_media_keep_days,
            max_days=cfg.social.story_media_keep_max_days,
            tick_seconds=cfg.social.poll.tick_seconds,
            metrics=metrics,
            embedded=_story_frames_embedded(engine, fingerprint(cfg.memory.index.embedder_model)),
        )
        if blobs is not None and cfg.social.story_expiry_enabled
        else None
    )
    return ExpiryWiring(story_expiry=story_expiry)
