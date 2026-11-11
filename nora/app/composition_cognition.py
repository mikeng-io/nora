"""The Cognition session's wiring — the `if cfg.cognition.enabled` body of
`composition_dreamer.enabled`, split by the pieces it wires."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from zoneinfo import ZoneInfo

from nora.agent import factory
from nora.agent.dreamer import TOOL_RESULT_CAPS
from nora.agent.room import Room
from nora.app.cognition_grounding import (
    build_grounder,
    place_release,
    stay_context_reader,
    unit_self_profile_reader,
    unit_self_reader,
)
from nora.app.cognition_wiring import (
    COGNITION_ADAPTOR_NAME,
    SkillNotInstalledError,
    build_cognition_runner,
    cognition_budget,
    open_events_reader,
    read_surface,
    units_with_home_rooms,
    upcoming_reader,
)
from nora.app.cognition_wiring import build_context_reader as cognition_context_reader
from nora.app.composition_helpers import _roster_names_of
from nora.app.startup import StartupError
from nora.domain.live import LiveMapping, LiveSequence, LiveSet
from nora.infra.sql.cognition_store import SqlCognitionStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.instructions.frontmatter import InstructionError
from nora.language.cognition_render import RenderBudget

log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class CognitionSessionWiring:
    cognition_runner: Any
    cognition_session_units: Any
    cognition_upcoming: Any


@dataclass(frozen=True)
class SessionUnitsWiring:
    cognition_session_units: Any
    homes: Any


@dataclass(frozen=True)
class SessionReadersWiring:
    cognition_room_labels: Any
    cognition_unit_rooms: Any
    cognition_upcoming: Any
    note_index: Any
    note_titles: Any
    open_events: Any


async def cognition_session(
    allowlist: Any, audit: Any, cfg: Any, clock: Any,
    cognition_context_screen: Any, cognition_lessons: Any, cognition_memory: Any,
    content_checks: Any, culture_reader: Any, document_service: Any, documents_context: Any,
    dream_model: Any, dream_models: Any, dream_tools: Any, dreamer_instructions: Any,
    dreamer_runtime: Any, engine: Any, event_store: Any, events: Any,
    ledger_store: Any, lineage: Any, media_join: Any, memo_store: Any,
    metrics: Any, mobility: Any, note_store: Any, observation_store: Any,
    persons: Any, presence_circles: Any, presence_reader: Any, profile_reader: Any,
    registry: Any, reminder_caps: Any, reminders: Any, rosters: Any,
    session_guidance: Any, shared: Any, standing_fold: Any, standing_store: Any,
    stimulus_store: Any, tag_trace_store: Any, tier: Any, tool_registry: Any,
    unit_rooms: Any, unit_scopes: Any,
) -> CognitionSessionWiring:
    """A Cognition session per unit with a home room, hosted on the Dreamer."""
    cognition_upcoming = None
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
    units = _session_units(
        allowlist=allowlist, cfg=cfg, metrics=metrics,
        note_store=note_store, unit_scopes=unit_scopes,
    )
    readers = _session_readers(
        allowlist=allowlist, cfg=cfg, event_store=event_store,
        note_store=note_store, unit_scopes=unit_scopes,
    )
    cognition_upcoming = readers.cognition_upcoming
    grounder = _session_grounder(
        allowlist=allowlist, audit=audit, cfg=cfg, clock=clock,
        cognition_context_screen=cognition_context_screen,
        content_checks=content_checks, culture_reader=culture_reader,
        documents_context=documents_context, engine=engine, lineage=lineage,
        media_join=media_join, memo_store=memo_store, metrics=metrics,
        mobility=mobility, observation_store=observation_store,
        persons=persons, presence_circles=presence_circles,
        presence_reader=presence_reader, profile_reader=profile_reader,
        readers=readers, registry=registry, reminders=reminders,
        rosters=rosters, unit_rooms=unit_rooms,
    )
    cognition_runner = _session_runner(
        allowlist=allowlist, cfg=cfg, clock=clock,
        cognition_lessons=cognition_lessons, cognition_memory=cognition_memory,
        document_service=document_service, documents_context=documents_context,
        dream_model=dream_model, dream_models=dream_models,
        dream_tools=dream_tools, dreamer_instructions=dreamer_instructions,
        dreamer_runtime=dreamer_runtime, engine=engine, events=events,
        grounder=grounder, ledger_store=ledger_store, memo_store=memo_store,
        metrics=metrics, note_store=note_store,
        observation_store=observation_store, persons=persons, readers=readers,
        registry=registry, reminder_caps=reminder_caps, reminders=reminders,
        rosters=rosters, session_guidance=session_guidance, shared=shared,
        standing_fold=standing_fold, standing_store=standing_store,
        stimulus_store=stimulus_store, surface=surface,
        tag_trace_store=tag_trace_store, tier=tier, unit_rooms=unit_rooms,
        units=units,
    )
    return CognitionSessionWiring(
        cognition_runner=cognition_runner,
        cognition_session_units=units.cognition_session_units,
        cognition_upcoming=cognition_upcoming,
    )


def _session_units(
    allowlist: Any, cfg: Any, metrics: Any, note_store: Any,
    unit_scopes: Any,
) -> SessionUnitsWiring:

    """The units a session can run for (a home room each); only they are woken."""
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
    return SessionUnitsWiring(
        cognition_session_units=cognition_session_units,
        homes=homes,
    )


def _session_readers(
    allowlist: Any, cfg: Any, event_store: Any, note_store: Any,
    unit_scopes: Any,
) -> SessionReadersWiring:

    """The note/event/unit-room readers a session grounds on."""
    async def _note_titles(unit: str)-> Sequence[str]:

        if note_store is None:
            return ()
        return [n.title for n in await note_store.standing(unit=unit)]

    async def _note_index(unit: str)-> Sequence[Any]:

        if note_store is None:
            return ()
        return cast(Sequence[Any], await note_store.standing(unit=unit))

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
    return SessionReadersWiring(
        cognition_room_labels=cognition_room_labels,
        cognition_unit_rooms=cognition_unit_rooms,
        cognition_upcoming=cognition_upcoming,
        note_index=_note_index,
        note_titles=_note_titles,
        open_events=_open_events,
    )


def _session_grounder(
    allowlist: Any, audit: Any, cfg: Any, clock: Any,
    cognition_context_screen: Any, content_checks: Any, culture_reader: Any,
    documents_context: Any, engine: Any, lineage: Any, media_join: Any,
    memo_store: Any, metrics: Any, mobility: Any, observation_store: Any,
    persons: Any, presence_circles: Any, presence_reader: Any, profile_reader: Any,
    readers: Any, registry: Any, reminders: Any, rosters: Any,
    unit_rooms: Any,
) -> Any:
    """The grounding read a session is built on."""
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
        note_index=readers.note_index,
        events_in=readers.open_events,
        memo_index=lambda unit: memo_store.kept(unit=unit),
        pending_reminds=lambda: reminders.pending_of(cfg.honcho.ai_peer),
        lineage=lineage,
        unit_rooms=lambda unit: readers.cognition_unit_rooms.get(unit, set()),
        ai_display_name=cfg.doctrine.display_name,
        room_label=lambda platform, cid: readers.cognition_room_labels.get((platform, cid), ""),
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
    return grounder


def _session_runner(
    allowlist: Any, cfg: Any, clock: Any, cognition_lessons: Any,
    cognition_memory: Any, document_service: Any, documents_context: Any, dream_model: Any,
    dream_models: Any, dream_tools: Any, dreamer_instructions: Any, dreamer_runtime: Any,
    engine: Any, events: Any, grounder: Any, ledger_store: Any,
    memo_store: Any, metrics: Any, note_store: Any, observation_store: Any,
    persons: Any, readers: Any, registry: Any, reminder_caps: Any,
    reminders: Any, rosters: Any, session_guidance: Any, shared: Any,
    standing_fold: Any, standing_store: Any, stimulus_store: Any, surface: Any,
    tag_trace_store: Any, tier: Any, unit_rooms: Any, units: Any,
) -> Any:
    """The runner itself; a bad objective or skill names the fault at startup."""
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
            note_titles=readers.note_titles,
            open_events_in=readers.open_events,
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
            homes=units.homes,
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
    return cognition_runner
