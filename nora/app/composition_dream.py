"""Build lesson evidence and room-scoped tools."""

from __future__ import annotations

import functools
import logging
from typing import Any

from nora.agent.dream_screen import offence as dream_offence
from nora.agent.dreamer import (
    RegistryToolSource,
)
from nora.app.composition_helpers import (
    _now_ms,
    _reasoning_effort,
    character_unit_keys,
)
from nora.app.composition_readers import scope_home_rooms
from nora.app.raw_clients import _RelevanceGateModelClient
from nora.app.startup import StartupError
from nora.domain.live import LiveMapping
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.tension import SqlTensionStore
from nora.language.doctrine_check import CHECK_OBJECTIVE, DoctrineCheck
from nora.language.lesson_book import LessonBook
from nora.language.lessons import CognitionLessons, LessonEvidence, LessonsKey

log = logging.getLogger("nora.app.composition")


def _lessons_evidence(
    allowlist: Any, cfg: Any, content_checks: Any, doctrine_store: Any,
    dreamer_instructions: Any, engine: Any, metrics: Any, model_client_for: Any,
    persons: Any, registry: Any, route_model: Any,
) -> Any:
    """The lesson evidence seam the Cognition session reads and settles."""
    if not cfg.lessons.enabled:
        return None
    lessons_keys = LiveMapping(
        lambda: {
            uk.unit: LessonsKey(unit=uk.unit, rooms=uk.rooms)
            for uk in character_unit_keys(allowlist).values()
        }
    )
    # THE DOCTRINE CHECK: every lesson is read against the law by a separate call before it
    # is written; a conflict is a tension row. Its instruction is read once HERE so a
    # missing file stops startup instead of refusing every lesson as `check_unavailable`.
    if not dreamer_instructions.objective_body(CHECK_OBJECTIVE).strip():
        raise StartupError(f"the {CHECK_OBJECTIVE} objective is empty")
    lesson_check = DoctrineCheck(
        # The relevance gate's raw JSON completion, not the Strands `_DebriefModelClient`…
        # Strands path returns an EMPTY answer (`end_turn`) on a live lesson, while the same
        # request raw returns `{"verdict": "consistent"}`. Empty is an `error`, so every…
        client=_RelevanceGateModelClient(
            client=model_client_for("lesson_check"),
            model=route_model("lesson_check", cfg.lessons.check_model),
            max_tokens=cfg.lessons.check_max_tokens,
            temperature=0.0,
            timeout_seconds=cfg.lessons.check_timeout_seconds + 1.0,
            reasoning_effort=_reasoning_effort(
                cfg.lessons.check_reasoning_effort or "low",
                source="[lessons] check_reasoning_effort",
            ),
        ),
        law=doctrine_store.law_rules,
        instruction=lambda: dreamer_instructions.objective_body(CHECK_OBJECTIVE),
        timeout_seconds=cfg.lessons.check_timeout_seconds,
        metrics=metrics,
    )
    lesson_book = LessonBook(
        store=SqlLessonStore(engine),
        law=lambda: [r.text for r in doctrine_store.law_rules()],
        screen=functools.partial(dream_offence, content_checks=content_checks),
        clock_ms=_now_ms,
        check=lesson_check,
        tensions=SqlTensionStore(engine),
        metrics=metrics,
    )
    cognition_lessons = CognitionLessons(
        evidence=LessonEvidence(
            store=SqlCharacterEvidence(engine),
            book=lesson_book,
            keys=lessons_keys,
            clock_ms=_now_ms,
            correction_quiet_seconds=(cfg.lessons.correction_quiet_seconds or None),
            stale_seconds=cfg.lessons.stale_seconds,
            self_ids=frozenset(registry.self_aliases()),
        ),
        book=lesson_book,
        keys=lessons_keys,
        # The `lessons` row of the consolidation watermark table: the
        # cursor the retired adaptor wrote, read and advanced from
        # here so a deployment's progress carries over.
        cursor=SqlLanguageStore(engine),
        batch_limit=cfg.lessons.batch_limit,
        horizon_seconds=cfg.lessons.horizon_seconds,
        name_of=lambda pid: (
            ref.name
            if pid and (ref := persons.by_person.get(pid)) is not None
            else "someone"
        ),
        clock_ms=_now_ms,
        metrics=metrics,
    )
    return cognition_lessons


def _dream_tool_source(
    allowlist: Any, audit: Any, cfg: Any,
    metrics: Any, persons: Any, registry: Any,
    rosters: Any, tool_registry: Any, access: Any,
) -> Any:
    """The registry-backed tool source every Dreamer session speaks through."""
    return RegistryToolSource(
        registry=tool_registry,
        store=audit, access=access,
        persons=persons,
        metrics=metrics,
        ai_peer=cfg.agent.name,
        scope_rooms=LiveMapping(lambda: scope_home_rooms(allowlist)),
        rosters=rosters,
        # EVERY platform's aliases, for `_presence_preflight`'s reason: a home room's roster
        # must skip Nora's own account rather than count it as one unnamed stranger — which
        # alone makes `where_is` refuse in a location-trusted room.
        self_ids=registry.self_aliases(),
    )
