from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

from nora.agent import factory, tool_choice
from nora.agent.dream_screen import offence as dream_offence
from nora.agent.dream_screen import text_offence
from nora.agent.dreamer import (
    TOOL_RESULT_CAPS,
    AdaptorBudget,
    AdaptorGrant,
    AdaptorSchedule,
    DreamerRuntime,
    HomeRoom,
    RegistryToolSource,
    culture_grant,
    verify_tool_subsets,
)
from nora.agent.egress import scan_identifiers
from nora.agent.room import Room
from nora.agent.security import looks_like_coordinate
from nora.app.cognition_grounding import (
    build_grounder,
    place_release,
    stay_context_reader,
    unit_self_profile_reader,
    unit_self_reader,
)
from nora.app.cognition_session import CognitionSessionRunner
from nora.app.cognition_wiring import (
    COGNITION_ADAPTOR_NAME,
    SESSION_SHUTDOWN_GRACE_SECONDS,
    CognitionShutdown,
    SkillNotInstalledError,
    build_cognition_runner,
    cognition_budget,
    open_events_reader,
    read_surface,
    require_session_hook,
    units_with_home_rooms,
    upcoming_reader,
)
from nora.app.cognition_wiring import build_context_reader as cognition_context_reader
from nora.app.cognition_wiring import session_guidance as cognition_session_guidance
from nora.app.composition_helpers import (
    _collective_members,
    _now_ms,
    _profile_room_keys,
    _reasoning_effort,
    _roster_names_of,
    character_person_keys,
    character_unit_keys,
)
from nora.app.composition_readers import scope_home_rooms
from nora.app.raw_clients import _RelevanceGateModelClient
from nora.app.startup import StartupError
from nora.app.wake import Floor, WakeEvaluator
from nora.config import Config, DreamBudgetConfig
from nora.domain.culture import nora_character_key
from nora.domain.live import LiveMapping, LiveSequence, LiveSet
from nora.domain.ports import DreamAdaptor
from nora.domain.stimuli import WakeHook, WakePolicy
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.cognition_store import SqlCognitionStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.dream_schedule import SqlDreamScheduleClock
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.profile_inputs import SqlProfileInputs
from nora.infra.sql.record_query import SqlRecordQuery
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.social_stream import readable_social_store
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.infra.sql.tension import SqlTensionStore
from nora.instructions.frontmatter import InstructionError
from nora.language.character import (
    CHARACTER_NORA_ADAPTOR_NAME,
    CHARACTER_PERSON_ADAPTOR_NAME,
    CHARACTER_UNIT_ADAPTOR_NAME,
    CharacterNoraAdaptor,
    CharacterPersonAdaptor,
    CharacterUnitAdaptor,
    NoraEvidence,
    NoraKey,
    PersonEvidence,
    UnitEvidence,
)
from nora.language.cognition_render import RenderBudget
from nora.language.culture import CULTURE_ADAPTOR_NAME, CultureAdaptor
from nora.language.doctrine_check import CHECK_OBJECTIVE, DoctrineCheck
from nora.language.lesson_book import LessonBook
from nora.language.lessons import CognitionLessons, LessonEvidence, LessonsKey
from nora.language.profile import PROFILE_PERSON_ADAPTOR_NAME, ProfileEvidence, ProfilePersonAdaptor
from nora.language.profile_collective import (
    PROFILE_COLLECTIVE_ADAPTOR_NAMES,
    PROFILE_NORA_ADAPTOR_NAME,
    PROFILE_ROOM_ADAPTOR_NAME,
    PROFILE_UNIT_ADAPTOR_NAME,
    CollectiveEvidence,
    CollectiveKey,
    ProfileCollectiveAdaptor,
)
from nora.language.profile_verdict import (
    PROFILE_VERDICT_ADAPTOR_NAME,
    DocketAccount,
    ProfileVerdictAdaptor,
)
from nora.obs.metrics import Metrics

"""Dreamer and Cognition wiring steps."""


log = logging.getLogger("nora.app.composition")


CHARACTER_ADAPTOR_NAMES: tuple[str, ...] = (
    CHARACTER_PERSON_ADAPTOR_NAME,
    CHARACTER_UNIT_ADAPTOR_NAME,
    CHARACTER_NORA_ADAPTOR_NAME,
)


def _build_wake_evaluator(
    cfg: Config,
    *,
    store: SqlStimulusStore,
    units: Callable[[], Sequence[str]],
    metrics: Metrics,
    on_wake: WakeHook | None,
    session_units: Callable[[], Collection[str]] | None = None,
    floors_ready: bool = True,
    upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None,
) -> WakeEvaluator:
    """The cognition loop's wake evaluator: every"""
    return WakeEvaluator(
        store=store,
        units=units,
        policy=WakePolicy(
            quiet_seconds=cfg.cognition.quiet_seconds,
            cue_quiet_seconds=cfg.cognition.cue_quiet_seconds,
            batch_max=cfg.cognition.batch_max,
            max_age_seconds=cfg.cognition.max_age_seconds,
            cooldown_seconds=cfg.cognition.cooldown_seconds,
            max_concurrent_sessions=cfg.cognition.max_concurrent_sessions,
            session_timeout_seconds=cfg.cognition.session_timeout_seconds,
            backoff_max_seconds=cfg.cognition.backoff_max_seconds,
            max_consecutive_failures=cfg.cognition.max_consecutive_failures,
        ),
        metrics=metrics,
        floors=tuple(Floor.parse(f) for f in cfg.cognition.time_floors),
        tz=ZoneInfo(cfg.cognition.timezone),
        floor_grace_seconds=cfg.cognition.floor_grace_seconds,
        mode="live",
        store_timeout_seconds=cfg.cognition.store_timeout_seconds,
        on_wake=on_wake,
        session_units=session_units,
        floors_ready=floors_ready,
        upcoming=upcoming,
    )


def _adaptor_budget(b: DreamBudgetConfig) -> AdaptorBudget:
    """`DreamBudgetConfig` -> `AdaptorBudget`: the one place the two shapes meet."""
    return AdaptorBudget(
        turns=b.turns,
        total_tokens=b.total_tokens,
        output_tokens=b.output_tokens,
        timeout_seconds=b.timeout_seconds,
        max_memory_calls=b.max_memory_calls,
        # Copied, not aliased: `DreamBudgetConfig` is frozen but its `dict`
        # field is not, and an `AdaptorBudget` handed the same object would
        # share it with the config tree for the process's life.
        tool_calls=dict(b.tool_calls),
        max_tool_calls=b.max_tool_calls,
    )


def _unified_profile_writers_retired(
    *,
    profile_index: bool,
    graph_comprehend: bool,
    cognition_enabled: bool,
    units: frozenset[str],
    home_units: frozenset[str],
) -> frozenset[str]:
    """The Dreamer profile writers the unified Cognition session replaces —"""
    return (
        frozenset(
            {
                PROFILE_PERSON_ADAPTOR_NAME,
                PROFILE_ROOM_ADAPTOR_NAME,
                PROFILE_UNIT_ADAPTOR_NAME,
                PROFILE_NORA_ADAPTOR_NAME,
            }
        )
        if (
            profile_index
            and graph_comprehend
            and cognition_enabled
            and units
            and units <= home_units
        )
        else frozenset()
    )


@dataclass(frozen=True)
class PreambleWiring:
    dream_adaptors: Any
    dream_schedule_clock: Any
    dream_schedules: Any
    dreamer_runtime: Any


def preamble(engine) -> PreambleWiring:

    # ---- the Dreamer runtime -------------------- Constructed only when `[dreamer]
    # enabled` is true, the shape every gated subsystem in this file uses: a subsystem that
    # is off builds NOTHING, so a wiring mistake in it cannot cost a deployment that never…
    dream_adaptors: tuple[DreamAdaptor, ...] = ()
    dreamer_runtime: DreamerRuntime | None = None
    dream_schedules: Mapping[str, AdaptorSchedule] = {}
    # ONE schedule-state store, shared by the loop's slots and
    # any other schedule clock: two instances would be two
    # engines over one table for no reason.
    dream_schedule_clock = SqlDreamScheduleClock(engine)
    return PreambleWiring(
        dream_adaptors=dream_adaptors,
        dream_schedule_clock=dream_schedule_clock,
        dream_schedules=dream_schedules,
        dreamer_runtime=dreamer_runtime,
    )


@dataclass(frozen=True)
class GuidanceWiring:
    cognition_lessons: Any
    cognition_memory: Any
    cognition_runner: Any
    session_guidance: Any


def guidance(dreamer_instructions) -> GuidanceWiring:
    # The Cognition session: built inside the Dreamer block below ONLY under `[cognition]
    # enabled`; off builds nothing here, no model client and no agent-runner import. : Hot-
    # loaded session guidance, gated by the offered tools during assembly.
    session_guidance = cognition_session_guidance(dreamer_instructions)
    cognition_runner: CognitionSessionRunner | None = None
    cognition_memory = None
    # The lesson evidence seam the session reads and settles (`[lessons]
    # enabled` fills it inside the Dreamer block below; Cognition needs the
    # Dreamer anyway — `_lessons_need_cognition`).
    cognition_lessons: CognitionLessons | None = None
    return GuidanceWiring(
        cognition_lessons=cognition_lessons,
        cognition_memory=cognition_memory,
        cognition_runner=cognition_runner,
        session_guidance=session_guidance,
    )


@dataclass(frozen=True)
class CognitionInitsWiring:
    cognition_session_units: Any
    cognition_upcoming: Any


def cognition_inits() -> CognitionInitsWiring:
    #: The units a session can run for (a home room each); only they are woken.
    cognition_session_units: Collection[str] | None = None
    cognition_upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None
    return CognitionInitsWiring(
        cognition_session_units=cognition_session_units, cognition_upcoming=cognition_upcoming
    )


@dataclass(frozen=True)
class EnabledWiring:
    cognition_runner: Any
    cognition_session_units: Any
    cognition_upcoming: Any
    dream_adaptors: Any
    dream_schedules: Any
    dreamer_runtime: Any


async def enabled(
    allowlist,
    audit,
    cfg,
    clock,
    cognition_context_screen,
    cognition_lessons,
    cognition_memory,
    content_checks,
    culture_reader,
    doctrine_store,
    document_service,
    documents_context,
    dream_adaptors,
    dream_schedules,
    dreamer_instructions,
    dreamer_runtime,
    engine,
    event_store,
    events,
    ledger_store,
    lineage,
    media_join,
    memo_store,
    memory,
    metrics,
    mobility,
    model_client_for,
    note_store,
    observation_store,
    persons,
    presence_circles,
    presence_reader,
    proactive_homes,
    profile_index,
    profile_reader,
    reasoning_strip_store,
    registry,
    reminder_caps,
    reminders,
    rosters,
    route_model,
    session_guidance,
    shared,
    standing_fold,
    standing_store,
    stimulus_store,
    tag_trace_store,
    tier,
    tool_registry,
    unit_rooms,
    unit_scopes,
) -> EnabledWiring:
    dream_adaptors = ()
    dreamer_runtime = None
    dream_schedules = {}
    cognition_runner = None
    cognition_session_units = None
    cognition_upcoming = None
    if cfg.dreamer.enabled:
        # THE CULTURE TIER (DEC-182..185). An account of how each room works and why, folded
        # from that room's own messages when it has said enough (DEC-288) and appended to a
        # chain. READ BY THE PROMPT AND BY THE CHARACTER SESSIONS: `<culture>` renders a room's…
        if cfg.culture.enabled or cfg.memory.profile.write:
            dream_schedules = {
                **dream_schedules,
                "culture": AdaptorSchedule(
                    # DEC-288: fire on `wake_batch` new human messages, or on any new one once th…
                    # is `stale_seconds` old. A SILENT ROOM STILL NEVER FIRES, which is correct r…
                    # a defect: nothing in this tier retires, so a quiet room's account is accura…
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.culture.wake_batch,
                    # THE BATCH IS THE CORPUS — see `CultureConfig.batch_limit`.
                    # This is how much of the room the model gets to see, not a
                    # throttle.
                    batch_limit=cfg.culture.batch_limit,
                    # START FROM WHAT THE ROOM HAS BEEN SAYING LATELY, not from its origin story.
                    # `seed_watermarks` inherits from the `language` cursor, and that tier is DEL…
                    # room it never covered would seed at 0 and spend days walking a backlog, ins…
                    seed_at_head=True,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                CultureAdaptor(
                    culture=SqlCultureStore(engine),
                    # THE NOTE INDEX: an account of how a room works that cannot see what the roo…
                    # is inferring a thing the note index already states. `None` for the tier bei…
                    # real configuration and renders no block, not an empty one. NOT `standing`:…
                    notes=note_store if cfg.notes.enabled else None,
                    # The THREE policy resolutions, every one of them the same `.get` over the al…
                    # keyed on `conv.primary_id` — which is this adaptor's own key, because a Dre…
                    # adaptor is handed `scopes()` (GLOSSARY.md). Built here, in one place, so th…
                    unit_for=LiveMapping(
                        lambda: {
                            conv.primary_id: conv.unit
                            for conv in allowlist.rooms()
                            if conv.primary_id and conv.unit
                        }
                    ).get,
                    # What the room is CALLED and what the operator says it IS (`Conversation.lab…
                    # `.description`), for the `<room>` block. A room with neither is simply abse…
                    # map and the block falls back without ever rendering an id.
                    label_for=LiveMapping(
                        lambda: {
                            conv.primary_id: conv.label
                            for conv in allowlist.rooms()
                            if conv.primary_id and conv.label
                        }
                    ).get,
                    description_for=LiveMapping(
                        lambda: {
                            conv.primary_id: conv.description
                            for conv in allowlist.rooms()
                            if conv.primary_id and conv.description
                        }
                    ).get,
                    metrics=metrics,
                    # THE ROOM'S PLATFORM and A NAME RESOLVER, off the SAME allowlist read
                    # `label_for`/`description_for` already are, plus `persons` — the one instanc…
                    # other write path in this file already holds. This is what lets `inputs()` f…
                    platform_for=LiveMapping(
                        lambda: {
                            conv.primary_id: conv.platform
                            for conv in allowlist.rooms()
                            if conv.primary_id
                        }
                    ).get,
                    resolve_names=(
                        lambda rows, platform: persons.name_transcript(rows, platform=platform)
                    ),
                ),
            )
            # CHARACTER · PERSON: one session per (unit, person listed in that unit's rooms), woken
            # by that person's own messages across the unit's rooms. Same store, same chain, the
            # stale rule `[culture] stale_seconds` shares.
            person_keys = LiveMapping(lambda: character_person_keys(allowlist, persons))
            culture_store = SqlCultureStore(engine)
            dream_schedules = {
                **dream_schedules,
                CHARACTER_PERSON_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.memory.profile.person.update_every_messages,
                    # THE WAKE BATCH, as Nora's session reads its own: a person's session reads i…
                    # message wake threshold messages, not a room's `batch_limit`, which sizes a…
                    # whole corpus.
                    batch_limit=cfg.memory.profile.person.update_every_messages,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                CharacterPersonAdaptor(
                    culture=culture_store,
                    evidence=PersonEvidence(
                        store=SqlCharacterEvidence(engine),
                        keys=person_keys,
                        # Their social items are evidence too (design/
                        # cognition-loop/22 §6), read whether or not the poller
                        # runs, opted-out persons left out by the store.
                        social=readable_social_store(engine, persons),
                        former_ids=persons.former_ids_for,
                    ),
                    keys=person_keys,
                    metrics=metrics,
                    screen=functools.partial(scan_identifiers, content_checks=content_checks),
                    render_chars=cfg.media.comprehension_render_chars,
                    # A caption keeps only the author's own @handle.
                    handle_owners=persons.instagram_username_owners,
                ),
            )

            # CHARACTER · UNIT: one session per unit, woken by new
            # revisions of its rooms' cultures.
            unit_keys = LiveMapping(lambda: character_unit_keys(allowlist))
            dream_schedules = {
                **dream_schedules,
                CHARACTER_UNIT_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.memory.profile.unit.update_every_room_profile_changes,
                    batch_limit=cfg.culture.batch_limit,
                ),
            }
            # CHARACTER · NORA: her own account per unit, from closed
            # response episodes; read back as the `self` block.
            nora_keys = LiveMapping(
                lambda: {
                    nora_character_key(uk.unit): NoraKey(unit=uk.unit, rooms=uk.rooms)
                    for uk in unit_keys.values()
                }
            )
            dream_schedules = {
                **dream_schedules,
                CHARACTER_NORA_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.memory.profile.nora.update_every_replies,
                    batch_limit=cfg.memory.profile.nora.update_every_replies,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                CharacterNoraAdaptor(
                    culture=culture_store,
                    evidence=NoraEvidence(
                        store=SqlCharacterEvidence(engine),
                        keys=nora_keys,
                    ),
                    keys=nora_keys,
                    metrics=metrics,
                    name_of=lambda pid: (
                        ref.name
                        if pid and (ref := persons.by_person.get(pid)) is not None
                        else "someone"
                    ),
                ),
            )
            dream_adaptors = (
                *dream_adaptors,
                CharacterUnitAdaptor(
                    culture=culture_store,
                    evidence=UnitEvidence(
                        store=SqlCharacterEvidence(engine),
                        keys=unit_keys,
                    ),
                    keys=unit_keys,
                    metrics=metrics,
                ),
            )

            # PROFILE · COLLECTIVES' keys (45 §1's key forms): a room's account under its
            # `primary_id`, the unit's and Nora's under the culture keys — the same key the prose
            # head they backfill from is written under.
            profile_room_keys = LiveMapping(lambda: _profile_room_keys(allowlist))
            profile_unit_keys = LiveMapping(
                lambda: {
                    key: CollectiveKey(unit=uk.unit, rooms=uk.rooms, label=uk.unit)
                    for key, uk in unit_keys.items()
                }
            )
            profile_nora_keys = LiveMapping(
                lambda: {
                    key: CollectiveKey(
                        unit=nk.unit, rooms=nk.rooms, label=cfg.doctrine.display_name
                    )
                    for key, nk in nora_keys.items()
                }
            )
            profile_collective_keys = LiveMapping(
                lambda: {
                    **profile_room_keys,
                    **profile_unit_keys,
                    **profile_nora_keys,
                }
            )

            # PROFILE · PERSON, in shadow (design/unified-memory/45 §4): one session per (unit,
            # listed person), on the person's Character wake plus a turn's `told` line; it writes
            # `profile_line`, which nothing renders until the switch. Inside the culture block: it…
            if cfg.memory.profile.write:
                profile_store = SqlProfileStore(
                    engine,
                    screen=functools.partial(text_offence, content_checks=content_checks),
                    index=profile_index,
                )
                profile_reads = SqlCharacterEvidence(engine)
                # The person accounts' social input (45 §4): the readable
                # store's (opted-out, gone and hidden left out there), the
                # same store the social cursor and the counts come from.
                profile_social = readable_social_store(engine, persons)
                dream_schedules = {
                    **dream_schedules,
                    PROFILE_PERSON_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.memory.profile.update_every_seconds,
                        wake_batch=cfg.memory.profile.person.update_every_messages,
                        batch_limit=cfg.memory.profile.person.update_every_messages,
                    ),
                    # The verdict judge (45 §3): one unit's docket of
                    # window-closed open hypotheses, fired the tick one is due
                    # (wake_batch 1), never by its writer.
                    PROFILE_VERDICT_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.memory.profile.update_every_seconds,
                        wake_batch=1,
                        batch_limit=cfg.memory.profile.person.update_every_messages,
                    ),
                }
                dream_adaptors = (
                    *dream_adaptors,
                    ProfilePersonAdaptor(
                        profile=profile_store,
                        culture=culture_store,
                        render=profile_index,
                        media_join=media_join,
                        evidence=ProfileEvidence(
                            store=profile_reads,
                            profile=profile_store,
                            keys=person_keys,
                            wake_batch=cfg.memory.profile.person.update_every_messages,
                            source_inputs=SqlProfileInputs(engine, social=profile_social),
                            source_timeout_seconds=cfg.standing.store_timeout_seconds,
                            social=profile_social,
                            former_ids=persons.former_ids_for,
                        ),
                        reads=profile_reads,
                        keys=person_keys,
                        name_of=lambda pid: (
                            ref.name
                            if pid and (ref := persons.by_person.get(pid)) is not None
                            else "someone"
                        ),
                        clock_ms=lambda: int(clock().timestamp() * 1000),
                        clock=clock,
                        metrics=metrics,
                        # The social items' render (45 §4): screened like the
                        # Character session's, captions redacted to the author's
                        # own handle, each reading clipped as the media reads.
                        social=profile_social,
                        coordinate_screen=looks_like_coordinate if content_checks else None,
                        screen=functools.partial(scan_identifiers, content_checks=content_checks),
                        render_chars=cfg.media.comprehension_render_chars,
                        handle_owners=persons.instagram_username_owners,
                    ),
                    ProfileVerdictAdaptor(
                        store=profile_store,
                        reads=profile_reads,
                        rollup=SqlRollupRead(engine),
                        # The docket covers every account of the unit, a
                        # collective's included: each mapped to who the judge
                        # says it is about and the rooms its window covers.
                        keys=LiveMapping(
                            lambda: {
                                **{
                                    key: DocketAccount(
                                        about=(
                                            ref.name
                                            if (ref := persons.by_person.get(pk.person.person_id))
                                            is not None
                                            else "someone"
                                        ),
                                        rooms=tuple(
                                            (r.platform, r.key)
                                            for r in pk.rooms
                                            if r.key in pk.listed
                                        ),
                                    )
                                    for key, pk in person_keys.items()
                                },
                                **{
                                    key: DocketAccount(
                                        about=ck.label or key,
                                        rooms=tuple((r.platform, r.key) for r in ck.rooms),
                                    )
                                    for key, ck in profile_collective_keys.items()
                                },
                            }
                        ),
                        name_of=lambda pid: (
                            ref.name
                            if pid and (ref := persons.by_person.get(pid)) is not None
                            else "someone"
                        ),
                        clock_ms=_now_ms,
                        metrics=metrics,
                    ),
                )

            # PROFILE · COLLECTIVES, in shadow (45 §4): a room's, the unit's and Nora's own account,
            # each woken by its own evidence — the room's messages, the rooms' culture revisions,
            # the unit's closed response episodes.
            if cfg.memory.profile.write:
                dream_schedules = {
                    **dream_schedules,
                    PROFILE_ROOM_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.memory.profile.update_every_seconds,
                        wake_batch=cfg.culture.wake_batch,
                        batch_limit=cfg.culture.batch_limit,
                    ),
                    PROFILE_UNIT_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.memory.profile.update_every_seconds,
                        wake_batch=cfg.memory.profile.unit.update_every_room_profile_changes,
                        batch_limit=cfg.culture.batch_limit,
                    ),
                    PROFILE_NORA_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.memory.profile.update_every_seconds,
                        wake_batch=cfg.memory.profile.nora.update_every_replies,
                        batch_limit=cfg.memory.profile.nora.update_every_replies,
                    ),
                }
                collective_kinds: tuple[
                    tuple[Literal["room", "unit", "nora"], Mapping[str, CollectiveKey]],
                    ...,
                ] = (
                    ("room", profile_room_keys),
                    ("unit", profile_unit_keys),
                    ("nora", profile_nora_keys),
                )
                dream_adaptors = (
                    *dream_adaptors,
                    *(
                        ProfileCollectiveAdaptor(
                            kind=kind,
                            profile=profile_store,
                            culture=culture_store,
                            render=profile_index,
                            media_join=media_join,
                            screen=functools.partial(text_offence, content_checks=content_checks),
                            coordinate_screen=looks_like_coordinate if content_checks else None,
                            evidence=CollectiveEvidence(
                                store=profile_reads,
                                keys=keys,
                                kind=kind,
                                render=profile_index,
                                source_inputs=SqlProfileInputs(engine, social=profile_social),
                                members=LiveMapping(
                                    functools.partial(
                                        _collective_members,
                                        allowlist,
                                        keys,
                                    )
                                ),
                            ),
                            reads=profile_reads,
                            keys=keys,
                            name_of=lambda pid: (
                                ref.name
                                if pid and (ref := persons.by_person.get(pid)) is not None
                                else "someone"
                            ),
                            clock_ms=_now_ms,
                            metrics=metrics,
                        )
                        for kind, keys in collective_kinds
                    ),
                )
        if profile_index or not cfg.culture.enabled:
            retired = {
                CULTURE_ADAPTOR_NAME,
                CHARACTER_PERSON_ADAPTOR_NAME,
                CHARACTER_UNIT_ADAPTOR_NAME,
                CHARACTER_NORA_ADAPTOR_NAME,
            }
            retired |= _unified_profile_writers_retired(
                profile_index=profile_index,
                graph_comprehend=(cfg.memory.graph.enabled and cfg.memory.graph.comprehend.enabled),
                cognition_enabled=cfg.cognition.enabled,
                units=frozenset(unit_scopes()),
                home_units=frozenset(allowlist.home_rooms()),
            )
            dream_adaptors = tuple(a for a in dream_adaptors if a.name not in retired)
            dream_schedules = {k: v for k, v in dream_schedules.items() if k not in retired}
        if cfg.lessons.enabled:
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
        dream_model = route_model("dreamer", cfg.dreamer_model())
        dream_effort = cfg.dreamer.reasoning_effort or tier.reasoning_effort
        _reasoning_effort(
            dream_effort,
            source="[dreamer] reasoning_effort (or [llm]'s, inherited)",
        )
        dream_models = factory.ModelSource(
            client=model_client_for("dreamer"),
            spec=factory.ModelSpec(
                # `cfg.dreamer_max_tokens()` and NOT `tier.max_tokens`. The tier's number is size…
                # TAG TURN; a batch pass whose objective asks for one verdict per message has an…
                # that scales with the batch, and a tag-sized ceiling fails every run with…
                model=dream_model,
                max_tokens=cfg.dreamer_max_tokens(),
                temperature=tier.temperature,
                # `[dreamer] reasoning_effort` where set, else the tier's. At the tier's "low" this
                # model reasons its way to `{"candidates": []}` on a transcript that plainly cont…
                # claims, and to `MaxTokensReachedException` on a real one — reasoning is billed as…
                reasoning_effort=dream_effort,
                tool_choice=tool_choice.AFTER_SPEAKING,
            ),
        )
        dream_tools = RegistryToolSource(
            registry=tool_registry,
            store=audit,
            persons=persons,
            honcho=memory,
            metrics=metrics,
            ai_peer=cfg.honcho.ai_peer,
            # The home rooms, from the SAME allowlist read the adaptor's room map came from, with
            # each room's `location` flag threaded as DEC-74 threads it on a tag. Passed whether or
            # not the proactive adaptor is wired: the map is inert for an adaptor with no home-room…
            home_rooms=LiveMapping(
                lambda: {
                    ws: HomeRoom(
                        unit=ws,
                        platform=conv.platform,
                        conversation_id=conv.primary_id,
                        conversation_type="group",
                        location_trusted=conv.location,
                    )
                    for ws, conv in proactive_homes.items()
                }
            ),
            # EVERY policy conversation, keyed by every id form it answers to — the SCOPE-keyed half
            # of a home-room grant (`AdaptorGrant.home_room`). A scope-keyed adaptor's key already
            # IS a conversation, so there is no room to choose and no `home_room = true` to read:…
            scope_rooms=LiveMapping(lambda: scope_home_rooms(allowlist)),
            rosters=rosters,
            # EVERY platform's aliases, for `_presence_preflight`'s reason: a home room's roster
            # must skip Nora's own account rather than count it as one unnamed stranger — which
            # alone makes `where_is` refuse in a location-trusted room.
            self_ids=registry.self_aliases(),
        )
        dream_grants: dict[str, AdaptorGrant] = {
            # The culture adaptor's own room, and no act verb (`culture_grant`). SCOPE-keyed, so
            # this resolves through `scope_rooms` above rather than through `home_rooms`: its key
            # already names the conversation, so the grant is an identity and there is nothing for…
            CULTURE_ADAPTOR_NAME: culture_grant(),
            # A person session's read tools stand in the first of its unit's
            # rooms that lists the person (`CharacterPersonAdaptor.tool_room`),
            # resolved through `scope_rooms` exactly as the room fold's are.
            CHARACTER_PERSON_ADAPTOR_NAME: culture_grant(),
            # The Profile session stands where the person's Character session
            # does (`ProfilePersonAdaptor.tool_room`), with the same reads.
            PROFILE_PERSON_ADAPTOR_NAME: culture_grant(),
            # A room's Profile session holds Culture's read tools, scoped to
            # its own room (`ProfileCollectiveAdaptor.tool_room`).
            PROFILE_ROOM_ADAPTOR_NAME: culture_grant(),
            # The verdict judge holds no read tool (its windows are in the
            # prompt) and writes only through `record_verdict`; the default
            # grant (nothing) is its ceiling, kept here by omission.
        }
        dream_budgets = {name: _adaptor_budget(b) for name, b in cfg.dreamer.budgets.items()}
        if "culture" in dream_budgets:
            for name in (
                *CHARACTER_ADAPTOR_NAMES,
                PROFILE_PERSON_ADAPTOR_NAME,
                PROFILE_VERDICT_ADAPTOR_NAME,
                *PROFILE_COLLECTIVE_ADAPTOR_NAMES,
            ):
                dream_budgets.setdefault(name, dream_budgets["culture"])
        fallback = cfg.dreamer.budget_for("")
        dreamer_runtime = DreamerRuntime(
            models=dream_models,
            instructions=dreamer_instructions,
            tools=dream_tools,
            budgets=dream_budgets,
            default_budget=_adaptor_budget(fallback),
            # The SAME derivation the tag path uses, so a throttle can never be
            # misreported as a timeout (`factory.build_retry_policy`).
            retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
            metrics=metrics,
            model_name=dream_model,
            schedules=dream_schedules,
            # The SAME `[llm.budgets]` the tag path applies through
            # `ToolRegistry(budget_overrides=...)`, so one number in
            # `config.toml` is one ceiling whichever loop asks.
            tool_budget_overrides=cfg.llm.budgets,
            grants=dream_grants,
            # The per-CALL-SITE allowance for `inputs`/`commit`,
            # multiplied by each adaptor's own declared `store_calls_per_tick`.
            store_timeout_seconds=cfg.dreamer.store_timeout_seconds,
            # The Dreamer's reasoning-strip store: a tool-using run at `[dreamer] reasoning_effort =
            # low` is the multi-turn shape the transport strips. Low-traffic and judgement-forming,
            # so a discarded reasoning there is both closer to being the verdict and harder to…
            reasoning_strip_store=reasoning_strip_store,
            content_checks=content_checks,
        )
        verify_tool_subsets(
            dream_adaptors,
            tools=dream_tools,
            grants=dream_grants,
            # The per-adaptor per-tool ceilings, read off the SAME budgets the runtime got above —
            # so «this ceiling names a tool the adaptor does not hold» is a boot failure rather than
            # a raise the operator made that quietly does nothing.
            tool_calls={name: b.tool_calls for name, b in cfg.dreamer.budgets.items()},
        )
        if cfg.cognition.enabled and stimulus_store is not None:
            # Sessions ride the Dreamer's model, tool source and objective
            # corpus (the config validator refuses sessions without
            # `[dreamer] enabled`), and write through the tag path's stores.
            surface = read_surface(
                read_only=dream_tools.read_only(),
                enabled_flags=(
                    *(("record_query.enabled",) if cfg.record_query.enabled else ()),
                    *(("culture.enabled",) if cfg.culture.enabled else ()),
                ),
                # The memory surface the registry selected (DEC-316), read
                # from the registry that builds the tools, not from config.
                registry_withheld=tool_registry.withheld_tools(),
            )
            log.info(
                "cognition sessions: read tools wired=%s missing=%s withheld=%s",
                sorted(surface.wired),
                sorted(surface.missing),
                sorted(surface.withheld),
            )
            homes = LiveMapping(lambda: allowlist.home_rooms())
            cognition_wakeable, cognition_skipped = units_with_home_rooms(
                list(unit_scopes()),
                homes,
            )
            cognition_session_units = LiveSet(lambda: frozenset(allowlist.home_rooms()))
            # ponytail: boot-time snapshot; a live home_room edit moves
            # `cognition_session_units` but not this gauge until restart
            # (the apply path has no consumer hook to re-set it through).
            metrics.gauge(
                "nora_cognition_session_units",
                float(len(cognition_wakeable)),
                state="runnable",
            )
            metrics.gauge(
                "nora_cognition_session_units",
                float(len(cognition_skipped)),
                state="no_home_room",
            )
            if cognition_skipped:
                log.error(
                    "cognition sessions: unit(s) %s name no home room in policy.toml, so no "
                    "session can start for them and they are not woken "
                    "(nora_cognition_session_units{state=no_home_room}). Set `home_room = "
                    "true` on one of their rooms to cover them",
                    cognition_skipped,
                )
            if note_store is None:
                raise StartupError(
                    "[cognition] enabled needs [notes] enabled: a session writes the notebook",
                )

            async def _note_titles(unit: str) -> Sequence[str]:
                if note_store is None:
                    return ()
                return [n.title for n in await note_store.standing(unit=unit)]

            async def _note_index(unit: str) -> Sequence[Any]:
                if note_store is None:
                    return ()
                return await note_store.standing(unit=unit)

            _open_events = open_events_reader(event_store)

            # The unit -> its rooms' every id form, for the grounding read: a
            # stimulus naming a room outside its unit is never read (DEC-231).
            cognition_unit_rooms = LiveMapping(
                lambda: {
                    unit: {
                        (c.platform, cid)
                        for c in allowlist.rooms()
                        if c.unit == unit and c.absorbed
                        for cid in c.ids
                    }
                    for unit in unit_scopes()
                }
            )
            if cfg.cognition.upcoming_days > 0:
                upcoming_rooms = LiveMapping(
                    lambda: {
                        unit: [
                            (c.platform, c.primary_id, c.ids)
                            for c in allowlist.rooms()
                            if c.unit == unit and c.absorbed
                        ]
                        for unit in unit_scopes()
                    }
                )
                cognition_upcoming = upcoming_reader(
                    _open_events,
                    lambda unit: upcoming_rooms.get(unit, []),
                    days=cfg.cognition.upcoming_days,
                    room_proactive=lambda platform, cid: allowlist.proactive_allowed(
                        platform=platform,
                        conversation_id=cid,
                    ),
                )
            # The policy's label for each of those ids: the prompt calls a
            # room by it and never by the conversation id.
            cognition_room_labels: Mapping[tuple[str, str], str] = LiveMapping(
                lambda: {
                    (conv.platform, cid): conv.label
                    for conv in allowlist.rooms()
                    for cid in conv.ids
                }
            )

            cognition_context = cognition_context_reader(
                engine=engine,
                allowlist=allowlist,
                persons=persons,
                lineage=lineage,
                presence_reader=presence_reader,
                audit=audit,
                rosters=rosters,
                registry=registry,
                media_join=media_join,
                record_chars=cfg.cognition.record_chars,
                documents_context=documents_context,
                screen=cognition_context_screen,
                content_checks=content_checks,
            )
            grounder = build_grounder(
                # NOT `tool_registry`'s `record`, which is None unless `[record_query]
                # enabled`: the rows that raised a stimulus are code's own input, and a
                # session must be grounded with the tool off.
                records=SqlRecordQuery(engine),
                persons=persons,
                media_join=media_join,
                context=cognition_context,
                note_index=_note_index,
                events_in=_open_events,
                memo_index=lambda unit: memo_store.kept(unit=unit),
                pending_reminds=lambda: reminders.pending_of(cfg.honcho.ai_peer),
                lineage=lineage,
                unit_rooms=lambda unit: cognition_unit_rooms.get(unit, set()),
                ai_display_name=cfg.doctrine.display_name,
                room_label=lambda platform, cid: cognition_room_labels.get((platform, cid), ""),
                clock=lambda: clock().timestamp(),
                own_send_lookback_seconds=cfg.cognition.own_send_lookback_seconds,
                own_sends_max=cfg.cognition.own_sends_max,
                tz=ZoneInfo(cfg.cognition.timezone),
                # A deviation line says where the stay was and what is usual
                # for the person (DEC-279), scored with the analytics process's
                # own thresholds, as the rollup that raised it was.
                stay_context=stay_context_reader(
                    mobility,
                    tz_name=cfg.analytics.tz_name,
                    min_samples=cfg.analytics.anomaly_min_samples,
                    z_threshold=cfg.analytics.anomaly_z_threshold,
                    novelty_radius_m=cfg.analytics.cluster_eps_m,
                ),
                # ...but names a place only where `where_been` would (R-35):
                # every room of the unit, read live, a subset of a circle the
                # person is in, or location-trusted (DEC-79).
                place_release=place_release(
                    rooms_of=lambda unit: [
                        (conv.platform, conv.primary_id, conv.location)
                        for conv in allowlist.rooms()
                        if conv.unit == unit and conv.primary_id
                    ],
                    room_view=lambda platform, conversation_id: Room(
                        platform=platform,
                        conversation_id=conversation_id,
                        roster_source=rosters.get(platform),
                        persons=persons,
                        self_ids=registry.self_aliases(),
                    ).members(),
                    circles=presence_circles,
                    content_checks=content_checks,
                ),
                # Her own account in the unit, under `[culture] enabled` like every Character rea…
                # own account in the unit, shown only where every room of the unit agrees on `loc…
                # (R-31; `unit_self_reader`).
                self_account=unit_self_reader(culture_reader, allowlist),
                # THE SWITCH (45 §5): when `[memory.profile] read` is on, her
                # Profile account replaces the prose one; the SAME reader the
                # tag and fire paths hold.
                self_profile=unit_self_profile_reader(
                    profile_reader,
                    allowlist,
                    lineage=lineage,
                    room_for=lambda platform, conversation_id: Room(
                        platform=platform,
                        conversation_id=conversation_id,
                        roster_source=rosters.get(platform),
                        persons=persons,
                        self_ids=registry.self_aliases(),
                    ),
                ),
                metrics=metrics,
                # The agenda's observation stimuli and the Standing block's
                # source objects read through the one store the bridge wrote.
                observations=observation_store,
            )

            try:
                cognition_runner = build_cognition_runner(
                    memory=cognition_memory,
                    documents=document_service if cfg.documents.enabled else None,
                    documents_context=documents_context,
                    tag_trace_store=tag_trace_store,
                    cfg=cfg,
                    models=dream_models,
                    retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
                    metrics=metrics,
                    tools=dream_tools,
                    instructions=dreamer_instructions,
                    store=SqlCognitionStore(engine),
                    stimulus_store=stimulus_store,
                    note_titles=_note_titles,
                    open_events_in=_open_events,
                    notes=note_store,
                    events=events,
                    standing=standing_store,
                    memos=memo_store,
                    reminders=reminders,
                    ai_peer=cfg.honcho.ai_peer,
                    ledger=ledger_store,
                    shared=shared,
                    standing_fold=standing_fold,
                    rooms_of_unit=lambda unit: unit_rooms.get(unit, ()),
                    reminder_caps=reminder_caps,
                    room_proactive=lambda platform, conversation_id: allowlist.proactive_allowed(
                        platform=platform,
                        conversation_id=conversation_id,
                    ),
                    # The HEAD with its `standing_revision` id: the token a
                    # `standing_revise` base is checked against (DEC-250).
                    standing_of=lambda platform, conversation_id: standing_store.read_head(
                        platform=platform,
                        conversation_id=conversation_id,
                    ),
                    roster_of=_roster_names_of(rosters, persons, registry.self_aliases()),
                    nora_names=frozenset({cfg.doctrine.display_name}),
                    homes=homes,
                    units=LiveSequence(lambda: tuple(allowlist.home_rooms())),
                    skills=session_guidance,
                    grounder=grounder,
                    person_known=lambda person_id: (
                        persons.by_person.get(
                            persons.subject_id_for(person_id),
                        )
                        is not None
                    ),
                    render_budget=RenderBudget(
                        record_chars=cfg.cognition.record_chars,
                        message_chars=cfg.cognition.message_chars,
                        register_lines=cfg.cognition.register_lines,
                    ),
                    surface=surface,
                    clock=lambda: clock().timestamp(),
                    model_id=dream_model,
                    model_route=cfg.endpoint("dreamer")[0],
                    budget=cognition_budget(cfg.dreamer.budget_for(COGNITION_ADAPTOR_NAME)),
                    result_caps=TOOL_RESULT_CAPS,
                    observations=observation_store,
                    # Cognition hosted as a Dreamer adaptor (node 30): every admitted wake is dis…
                    # onto this runtime's lane — the same instance `dream_loop` runs the other ad…
                    # through.
                    runtime=dreamer_runtime,
                    lessons=cognition_lessons,
                )
            except (InstructionError, SkillNotInstalledError) as exc:
                # A missing or malformed `cognition` objective, or a mapped skill that
                # is not installed: named at startup, not the first wake's failure and
                # never a raw `LookupError`.
                raise StartupError(f"Cognition session error: {exc}") from exc
    return EnabledWiring(
        cognition_runner=cognition_runner,
        cognition_session_units=cognition_session_units,
        cognition_upcoming=cognition_upcoming,
        dream_adaptors=dream_adaptors,
        dream_schedules=dream_schedules,
        dreamer_runtime=dreamer_runtime,
    )


@dataclass(frozen=True)
class WakeWiring:
    cognition_shutdown: Any
    wake_evaluator: Any


def wake(
    cfg, cognition_runner, cognition_session_units, cognition_upcoming, metrics, stimulus_store
) -> WakeWiring:

    # The wake evaluator, after the Dreamer block so it can be handed the
    # runner's hook. The boot check below refuses `enabled` with no runner or
    # no hook. Only units a session can run for are woken.
    wake_evaluator: WakeEvaluator | None = None
    cognition_shutdown: CognitionShutdown | None = None
    if stimulus_store is not None:
        wake_evaluator = _build_wake_evaluator(
            cfg,
            store=stimulus_store,
            units=lambda: sorted(cognition_session_units or ()),
            metrics=metrics,
            on_wake=cognition_runner.wake_hook if cognition_runner is not None else None,
            session_units=(
                None if cognition_session_units is None else (lambda: cognition_session_units)
            ),
            floors_ready=True,
            upcoming=cognition_upcoming,
        )
        if cognition_runner is not None:
            cognition_shutdown = CognitionShutdown(
                evaluator=wake_evaluator,
                runner=cognition_runner,
                grace_seconds=SESSION_SHUTDOWN_GRACE_SECONDS,
            )
    try:
        require_session_hook(
            enabled=cfg.cognition.enabled,
            runner=cognition_runner,
            evaluator=wake_evaluator,
            session_units=cognition_session_units,
        )
    except RuntimeError as exc:
        raise StartupError(str(exc)) from exc
    return WakeWiring(cognition_shutdown=cognition_shutdown, wake_evaluator=wake_evaluator)
