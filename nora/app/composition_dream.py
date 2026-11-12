"""The Dreamer's per-objective adaptors and its runtime — the `if cfg.dreamer.enabled`
body of `composition_dreamer.enabled`, split by objective."""

from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from nora.agent.dream_screen import offence as dream_offence
from nora.agent.dream_screen import text_offence
from nora.agent.dreamer import (
    AdaptorGrant,
    AdaptorSchedule,
    HomeRoom,
    RegistryToolSource,
    culture_grant,
)
from nora.agent.egress import scan_identifiers
from nora.agent.security import looks_like_coordinate
from nora.app.composition_helpers import (
    CHARACTER_ADAPTOR_NAMES,
    _adaptor_budget,
    _collective_members,
    _now_ms,
    _profile_room_keys,
    _reasoning_effort,
    _unified_profile_writers_retired,
    character_person_keys,
    character_unit_keys,
)
from nora.app.composition_readers import scope_home_rooms
from nora.app.raw_clients import _RelevanceGateModelClient
from nora.app.startup import StartupError
from nora.domain.culture import nora_character_key
from nora.domain.live import LiveMapping
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.profile_inputs import SqlProfileInputs
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.social_stream import readable_social_store
from nora.infra.sql.tension import SqlTensionStore
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

log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class CharacterAdaptorWiring:
    adaptors: Any
    culture_store: Any
    nora_keys: Any
    person_keys: Any
    schedules: Any
    unit_keys: Any


@dataclass(frozen=True)
class ProfileAdaptorWiring:
    adaptors: Any
    schedules: Any


def _character_adaptors(
    allowlist: Any, cfg: Any, content_checks: Any, engine: Any,
    metrics: Any, note_store: Any, persons: Any,
) -> CharacterAdaptorWiring:
    """Culture's room fold and the Character sessions (person, unit, Nora)."""
    schedules, adaptors = _culture_adaptor(
        allowlist=allowlist, cfg=cfg, engine=engine,
        metrics=metrics, note_store=note_store, persons=persons,
    )
    person_keys = LiveMapping(lambda: character_person_keys(allowlist, persons))
    culture_store = SqlCultureStore(engine)
    p_sched, p_ad = _character_person_adaptor(
        cfg=cfg, content_checks=content_checks, culture_store=culture_store,
        engine=engine, metrics=metrics, person_keys=person_keys, persons=persons,
    )
    c_sched, c_ad, unit_keys, nora_keys = _character_collective_adaptors(
        allowlist=allowlist, cfg=cfg, culture_store=culture_store, engine=engine,
        metrics=metrics, persons=persons,
    )
    return CharacterAdaptorWiring(
        adaptors=(*adaptors, *p_ad, *c_ad),
        culture_store=culture_store,
        nora_keys=nora_keys,
        person_keys=person_keys,
        schedules={**schedules, **p_sched, **c_sched},
        unit_keys=unit_keys,
    )


def _culture_adaptor(
    allowlist: Any, cfg: Any, engine: Any, metrics: Any,
    note_store: Any, persons: Any,
) -> Any:

    # THE CULTURE TIER (DEC-182..185) is the first schedule registered.
    schedules = {
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
    adaptors = (
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
    return schedules, adaptors


def _character_person_adaptor(
    cfg: Any, content_checks: Any, culture_store: Any, engine: Any,
    metrics: Any, person_keys: Any, persons: Any,
) -> Any:
    schedules = {
        CHARACTER_PERSON_ADAPTOR_NAME: AdaptorSchedule(
            min_interval_seconds=cfg.culture.stale_seconds,
            wake_batch=cfg.memory.profile.person.update_every_messages,
            # THE WAKE BATCH, as Nora's session reads its own: a person's session reads i…
            # message wake threshold messages, not a room's `batch_limit`, which sizes a…
            # whole corpus.
            batch_limit=cfg.memory.profile.person.update_every_messages,
        ),
    }
    adaptors = (
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
    return schedules, adaptors


def _character_collective_adaptors(
    allowlist: Any, cfg: Any, culture_store: Any, engine: Any,
    metrics: Any, persons: Any,
) -> Any:


    # CHARACTER · UNIT: one session per unit, woken by new
    # revisions of its rooms' cultures.
    unit_keys = LiveMapping(lambda: character_unit_keys(allowlist))
    schedules = {
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
    schedules = {
        **schedules,
        CHARACTER_NORA_ADAPTOR_NAME: AdaptorSchedule(
            min_interval_seconds=cfg.culture.stale_seconds,
            wake_batch=cfg.memory.profile.nora.update_every_replies,
            batch_limit=cfg.memory.profile.nora.update_every_replies,
        ),
    }
    adaptors: tuple[Any, ...] = (
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
    adaptors = (
        *adaptors,
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
    return schedules, adaptors, unit_keys, nora_keys


def _profile_adaptors(
    allowlist: Any, cfg: Any, character: Any, clock: Any,
    content_checks: Any, engine: Any, media_join: Any, metrics: Any,
    persons: Any, profile_index: Any,
) -> ProfileAdaptorWiring:
    """The Profile sessions (person, verdict judge, collectives), in shadow."""

    # PROFILE · COLLECTIVES' keys (45 §1's key forms): a room's account under its
    # `primary_id`, the unit's and Nora's under the culture keys — the same key the prose
    # head they backfill from is written under.
    profile_room_keys = LiveMapping(lambda: _profile_room_keys(allowlist))
    profile_unit_keys = LiveMapping(
        lambda: {
            key: CollectiveKey(unit=uk.unit, rooms=uk.rooms, label=uk.unit)
            for key, uk in character.unit_keys.items()
        }
    )
    profile_nora_keys = LiveMapping(
        lambda: {
            key: CollectiveKey(
                unit=nk.unit, rooms=nk.rooms, label=cfg.doctrine.display_name
            )
            for key, nk in character.nora_keys.items()
        }
    )
    profile_collective_keys = LiveMapping(
        lambda: {
            **profile_room_keys,
            **profile_unit_keys,
            **profile_nora_keys,
        }
    )
    schedules = {}
    adaptors = ()
    if cfg.memory.profile.write:
        p_sched, p_ad, profile_store, profile_reads, profile_social = (
            _profile_person_adaptors(
                allowlist=allowlist, cfg=cfg, character=character,
                clock=clock, content_checks=content_checks, engine=engine,
                media_join=media_join, metrics=metrics, persons=persons,
                profile_collective_keys=profile_collective_keys,
                profile_index=profile_index,
            )
        )
        c_sched, c_ad = _profile_collective_adaptors(
            allowlist=allowlist, cfg=cfg, character=character,
            content_checks=content_checks, engine=engine,
            media_join=media_join, metrics=metrics, persons=persons,
            profile_index=profile_index, profile_reads=profile_reads,
            profile_room_keys=profile_room_keys,
            profile_social=profile_social, profile_store=profile_store,
            profile_unit_keys=profile_unit_keys,
            profile_nora_keys=profile_nora_keys,
        )
        schedules = {**p_sched, **c_sched}
        adaptors = (*p_ad, *c_ad)
    return ProfileAdaptorWiring(adaptors=adaptors, schedules=schedules)


def _profile_person_adaptors(
    allowlist: Any, cfg: Any, character: Any, clock: Any,
    content_checks: Any, engine: Any, media_join: Any, metrics: Any,
    persons: Any, profile_collective_keys: Any, profile_index: Any,
) -> Any:
    """The person account's session and the verdict judge."""
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
    schedules = {
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
    adaptors = (
        ProfilePersonAdaptor(
            profile=profile_store,
            culture=character.culture_store,
            render=profile_index,
            media_join=media_join,
            evidence=ProfileEvidence(
                store=profile_reads,
                profile=profile_store,
                keys=character.person_keys,
                wake_batch=cfg.memory.profile.person.update_every_messages,
                source_inputs=SqlProfileInputs(engine, social=profile_social),
                source_timeout_seconds=cfg.standing.store_timeout_seconds,
                social=profile_social,
                former_ids=persons.former_ids_for,
            ),
            reads=profile_reads,
            keys=character.person_keys,
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
                        for key, pk in character.person_keys.items()
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
    return schedules, adaptors, profile_store, profile_reads, profile_social


def _profile_collective_adaptors(
    allowlist: Any, cfg: Any, character: Any, content_checks: Any,
    engine: Any, media_join: Any, metrics: Any, persons: Any,
    profile_index: Any, profile_reads: Any, profile_room_keys: Any, profile_social: Any,
    profile_store: Any, profile_unit_keys: Any, profile_nora_keys: Any,
) -> Any:
    """The room, unit and Nora collective accounts."""
    schedules = {
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
    adaptors = (
        *(
            ProfileCollectiveAdaptor(
                kind=kind,
                profile=profile_store,
                culture=character.culture_store,
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
    return schedules, adaptors



@dataclass(frozen=True)
class DreamObjectivesWiring:
    adaptors: Any
    schedules: Any


def _dream_objectives(
    allowlist: Any, cfg: Any, clock: Any, content_checks: Any,
    engine: Any, media_join: Any, metrics: Any, note_store: Any,
    persons: Any, profile_index: Any, unit_scopes: Any,
) -> DreamObjectivesWiring:
    """Every objective session and its wake schedule, minus the retired."""
    adaptors: tuple[Any, ...] = ()
    schedules: dict[str, Any] = {}
    if cfg.culture.enabled or cfg.memory.profile.write:
        character = _character_adaptors(
            allowlist=allowlist, cfg=cfg, content_checks=content_checks,
            engine=engine, metrics=metrics, note_store=note_store,
            persons=persons,
        )
        adaptors = (*adaptors, *character.adaptors)
        schedules = {**schedules, **character.schedules}
        profile = _profile_adaptors(
            allowlist=allowlist, cfg=cfg, character=character,
            clock=clock, content_checks=content_checks, engine=engine,
            media_join=media_join, metrics=metrics, persons=persons,
            profile_index=profile_index,
        )
        adaptors = (*adaptors, *profile.adaptors)
        schedules = {**schedules, **profile.schedules}
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
        adaptors = tuple(a for a in adaptors if a.name not in retired)
        schedules = {k: v for k, v in schedules.items() if k not in retired}
    return DreamObjectivesWiring(adaptors=adaptors, schedules=schedules)

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
    allowlist: Any, audit: Any, cfg: Any, memory: Any,
    metrics: Any, persons: Any, proactive_homes: Any, registry: Any,
    rosters: Any, tool_registry: Any,
) -> Any:
    """The registry-backed tool source every Dreamer session speaks through."""
    return RegistryToolSource(
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


def _dream_grants_and_budgets(cfg: Any) -> Any:

    """Adaptor grants and the per-objective budget table."""
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
    return dream_grants, dream_budgets, fallback


