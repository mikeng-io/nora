import functools
from dataclasses import dataclass
from typing import Any

from nora.agent.dream_screen import text_offence
from nora.agent.memory_search import MemorySearch
from nora.agent.room import Room
from nora.app.cognition_grounding import place_release
from nora.app.composition_dreamer import _unified_profile_writers_retired
from nora.app.memory_routing import MemoryRoutingError, build_workspace_router
from nora.app.startup import StartupError
from nora.domain.live import LiveMapping
from nora.infra.embedder import QUERY_TIMEOUT_SECONDS, LlamaEmbedder, fingerprint, model_file
from nora.infra.honcho import HonchoClient
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.standing_ledger import SqlStandingLedger
from nora.memory.adapters.tools import build as _build
from nora.memory.adapters.tools.notes import _make__resolve

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


def make_resolve(deps):
    return _make__resolve(deps)


@dataclass(frozen=True)
class StoresWiring:
    standing_store: Any


def stores(engine) -> StoresWiring:
    # Standing. The STORE is built unconditionally, REUSING `engine` for the same reason its
    # four neighbours above do — the read half feeds `TranscriptInjection`, whose own
    # `_standing` returns `None` for a `None` store, so an always-built store costs one…
    standing_store = SqlStandingStore(engine)
    return StoresWiring(standing_store=standing_store)


@dataclass(frozen=True)
class LedgerWiring:
    ledger_store: Any


def ledger(engine, metrics) -> LedgerWiring:
    ledger_store = SqlStandingLedger(engine, metrics=metrics)
    return LedgerWiring(ledger_store=ledger_store)


@dataclass(frozen=True)
class NotesWiring:
    memo_store: Any
    note_store: Any


def notes(cfg, engine) -> NotesWiring:
    # The note tier's store, REUSING `engine` like every store above, and gated on its OWN
    # flag. Off, `TranscriptInjection` gets `notes=None` and `_notes` answers `()` without a
    # query. No `metrics=`: `SqlNoteStore.__init__` takes an engine and nothing else on…
    note_store: SqlNoteStore | None = SqlNoteStore(engine) if cfg.notes.enabled else None
    # The memo tier (DEC-306): what a unit settled and keeps. No flag: only a Cognition
    # session writes a memo (`[cognition] enabled`), and with none kept every read renders
    # nothing. ONE instance for the tag and fire `<memos>`, `memo_read` and the session's…
    memo_store = SqlMemoStore(engine)
    return NotesWiring(memo_store=memo_store, note_store=note_store)


@dataclass(frozen=True)
class HonchoWiring:
    memory: Any


async def honcho(allowlist, cfg, persons) -> HonchoWiring:
    # workspace > unit > room (DEC-214): every Honcho call is routed from its room or unit
    # to the Honcho workspace its Nora workspace is bound to, and to the unit's named scope
    # inside it. Resolved ONCE, here, from the three files that each hold one part of it…
    try:
        router = build_workspace_router(
            policy=allowlist,
            bindings=persons.honcho_workspaces,
            default_workspace=cfg.honcho.workspace_id,
        )
    except MemoryRoutingError as exc:
        raise StartupError(f"memory routing error: {exc}") from exc
    memory = HonchoClient(
        cfg.honcho.base_url,
        cfg.honcho.workspace_id,
        ai_peer=cfg.honcho.ai_peer,
        workspace_router=router,
    )
    # Best-effort, never fatal — no try/except. Every bound Honcho workspace,
    # the Nora aiPeer in each (her replies feed under it to build her
    # self-model; peers are per Honcho workspace), and every unit's scope.
    await memory.ensure_workspace()
    # EXCEPT a bound Honcho workspace that belongs to another application: that is a wrong
    # line in identity.toml, and running on it would write people's speech into someone
    # else's peers. The client has already refused every call to it; this makes the refusal…
    foreign = memory.foreign_workspaces()
    if foreign:
        raise StartupError(
            f"memory routing error: Honcho workspace(s) {list(foreign)} hold "
            "sessions and are not marked as this Nora's. Another application "
            "uses them — bind the Nora workspace to a different "
            "honcho_workspace in identity.toml."
        )
    return HonchoWiring(memory=memory)


@dataclass(frozen=True)
class SearchWiring:
    memory_index_reader: Any
    memory_search: Any


def search(cfg, engine, metrics) -> SearchWiring:

    # `[memory.index] read` (ships off): the one retrieval service, held by the tag path's
    # pre-fetch (`<recalled>`) and by the tool registry's `memory_search` below, one
    # instance for both; `None` keeps the Honcho surface and no pre-fetch. The same…
    memory_index_reader = SqlMemoryIndex(engine)
    memory_search = (
        MemorySearch(
            memory_index_reader,
            metrics,
            graph=SqlMemoryGraph(engine, metrics=metrics),
            profile_render=cfg.memory.profile.write and cfg.memory.profile.read,
            # `[memory.index] embed`: dense and exact seeds, the
            # lexical seed only as the fallback; off, every answer is
            # the fallback's `partial`.
            embedder=LlamaEmbedder(
                cfg.memory.index.embedder_url,
                timeout_seconds=QUERY_TIMEOUT_SECONDS,
                query_model_file=model_file(cfg.memory.index.embedder_model),
            )
            if cfg.memory.index.embed
            else None,
            model=(
                fingerprint(cfg.memory.index.embedder_model) if cfg.memory.index.embed else None
            ),
            # The embedding worker's tick: a node waiting less than a
            # few of them is fresh, not a backlog that answers `partial`.
            drain_seconds=cfg.memory.index.refresh_seconds,
        )
        if cfg.memory.index.read
        else None
    )
    return SearchWiring(memory_index_reader=memory_index_reader, memory_search=memory_search)


@dataclass(frozen=True)
class GraphWiring:
    cognition_memory: Any


def graph(
    allowlist,
    cfg,
    content_checks,
    engine,
    media_join,
    metrics,
    persons,
    presence_circles,
    profile_index,
    registry,
    rosters,
    route_model,
    tier,
    unit_scopes,
) -> GraphWiring:
    if cfg.memory.graph.enabled and cfg.memory.graph.comprehend.enabled:
        from nora.infra.sql.memory_graph import SqlMemoryGraph

        memory_graph = SqlMemoryGraph(engine, metrics=metrics)
        from nora.app.cognition_memory import WakeMemory
        from nora.infra.sql.comprehend import SqlComprehendRecords

        rooms_by_unit = LiveMapping(
            lambda: {
                u: [
                    (c.platform, c.primary_id)
                    for c in allowlist.rooms()
                    if c.unit == u and c.primary_id
                ]
                for u in unit_scopes()
            }
        )
        graph_records = SqlComprehendRecords(
            engine,
            media_join=media_join,
            model_id=route_model("tag", tier.model),
            rooms_by_unit=rooms_by_unit,
            persons_by_unit=LiveMapping(
                lambda: {
                    u: {p for c in allowlist.rooms() if c.unit == u for p in (c.persons or ())}
                    for u in unit_scopes()
                }
            ),
            social_off_ids=persons.social_off_ids(),
            can_release_place=place_release(
                rooms_of=lambda u: [
                    (c.platform, c.primary_id, c.location)
                    for c in allowlist.rooms()
                    if c.unit == u and c.primary_id
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
        )
        # THE PROMOTED PROFILE WRITER (27 §9, 31 §5): the same `[memory.profile] write && read`
        # switch (`profile_index`) as the read surface and index projection; `None` under every
        # other flag combination, where `profile_revise` keeps recording shadow receipts and…
        wake_profile_store = (
            SqlProfileStore(
                engine,
                screen=functools.partial(text_offence, content_checks=content_checks),
                index=profile_index,
            )
            if _unified_profile_writers_retired(
                profile_index=profile_index,
                graph_comprehend=True,
                cognition_enabled=cfg.cognition.enabled,
                units=frozenset(unit_scopes()),
                home_units=frozenset(allowlist.home_rooms()),
            )
            else None
        )
        wake_listed_rooms = LiveMapping(
            lambda: {
                (u, p): tuple(
                    c.primary_id
                    for c in allowlist.rooms()
                    if c.unit == u and c.primary_id and c.persons and p in c.persons
                )
                for u in unit_scopes()
                for p in {x for c in allowlist.rooms() if c.unit == u for x in (c.persons or ())}
            }
        )
        cognition_memory = WakeMemory(
            graph=memory_graph,
            records=graph_records,
            metrics=metrics,
            content_checks=content_checks,
            profile=wake_profile_store,
            listed_rooms=(lambda u, p: wake_listed_rooms.get((u, p))),
            private_names=tuple(
                {
                    name
                    for p in persons.by_person.values()
                    for name in (p.person_id, p.name, p.first_name, p.last_name, *p.aliases)
                    if name
                }
                | {value for (_, value) in persons.index}
                | set(persons._instagram_handles.values())
                | set(persons._instagram_usernames)
            ),
        )
    return GraphWiring(cognition_memory=cognition_memory)
