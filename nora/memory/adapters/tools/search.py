from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.results import (
    _memory_day,
    _memory_hits_result,
    _memory_result,
    _record_room,
    _with_note,
)
from nora.agent.toolkit.rooms import _asked_about, _miss
from nora.domain.external_files import render_file_links
from nora.domain.lineage import LiveRoom
from nora.domain.ports import MessageHits
from nora.memory.adapters.graph_row_render import render_graph_rows
from nora.memory.adapters.retrieval_scope import RetrievalAccess, scoped_rows
from nora.memory.adapters.tools.shared import _REASONING_LEVELS
from nora.memory.app.act_scope import active_act_authority, pass_shown
from nora.memory.app.retrieval import RetrievalService
from nora.memory.domain.pass_declarations import declaration
from nora.memory.domain.pass_inputs import PassScope
from nora.memory.domain.retrieval import ReadingRoom, RetrievalAnchors, RetrievalUnavailableError

log = logging.getLogger("nora.tools")


class GraphReadTools:
    def __init__(
        self, retrieval: RetrievalService, engine: AsyncEngine, access: IdentityAccess,
        lineage: LineagePolicy,
    ) -> None:
        self._retrieval = retrieval
        self._engine = engine
        self._access = RetrievalAccess(access, lineage)

    async def read(
        self, room: Room, conversation_id: str, scope: PassScope | None, now: datetime, *,
        anchors: RetrievalAnchors, depth: int, person: str | None = None,
        since: datetime | None = None, until: datetime | None = None, content_checks: bool = True,
    ) -> dict:
        authority = active_act_authority()
        context = authority.pass_context if authority is not None else None
        occasion = context.occasion if context else "called"
        if occasion == "operator":
            return {"status": "unavailable", "note": "No graph read tool on the operator occasion."}
        clock = context.clock if context is not None else None
        historical = scope.as_of if scope is not None and clock is None else None
        now = clock or now
        bounds = declaration(occasion).bounds(historical or now, clock=clock)
        start = max(bounds.since, since) if since is not None else bounds.since
        end = min(bounds.until, until) if until is not None else bounds.until
        if start > end:
            return {"status": "nothing_found", "note": "No time in that window is in scope."}
        bounds = replace(bounds, since=start, until=end, depth=min(max(depth, 0), bounds.depth))
        view = await room.scope()
        reading = scope.room if scope is not None else ReadingRoom(
            room.platform, conversation_id,
            LiveRoom(frozenset(p.person_id for p in view.people), view.unnamed,
                     view.available, view.degraded),
        )
        concerns = scope.concerns if scope is not None else frozenset()
        subject_note = ""
        try:
            target, resolved, dm_rooms = await self._access.authority(reading, concerns, historical)
            if target.room is None or (scope is not None and resolved.unit != scope.unit) or (
                context is not None and (
                    resolved.unit != context.unit
                    or (context.room is not None and not target.room.is_room(context.room))
                )
            ):
                raise RetrievalUnavailableError("reading_room_unavailable")
            if target.room.persons is None and not self._access.access.is_file_backend:
                raise RetrievalUnavailableError("access_view_unavailable")
            if person is not None:
                if (room.platform != reading.platform
                        or not target.room.is_room(conversation_id)
                        or not view.available):
                    raise RetrievalUnavailableError("person_lookup_unavailable")
                matches = view.find(person)
                if target.room.persons is not None:
                    members = {target.canonical(member) for member in target.room.persons}
                    matches = tuple(p for p in matches if target.canonical(p.person_id) in members)
                if not matches:
                    return {"status": "nothing_found", "note": "No one by that name is known here."}
                if len(matches) != 1:
                    return {
                        "status": "nothing_found",
                        "note": "More than one person goes by that name here. Ask which one.",
                    }
                person_id = matches[0].person_id
                concerns |= {person_id}
                pointer = await self._pointer(
                    reading, concerns, historical, person_id, ceiling=clock,
                )
                if pointer is None:
                    subject_note = " Nothing is remembered about them as a subject."
                else:
                    anchors = replace(anchors, graph_ids=(pointer,))
            shown = pass_shown()
            result = await self._retrieval.retrieve(
                reading, anchors, bounds, concerns=concerns, as_of=historical,
                already_shown=frozenset(shown or ()), now=now,
            )
            if result.status == "unavailable":
                return {"status": "unavailable", "note": f"Graph read unavailable: {result.cause}."}
            rows = render_graph_rows(
                result.rows, now=now, reading_room=target.room, dm_rooms=dm_rooms,
            )
            if scan_identifiers(rows, content_checks=content_checks) is not None:
                return {"status": "unavailable", "note": "The graph read contains protected data."}
            if shown is not None:
                shown.update(row.graph_id for row in result.rows)
        except (RetrievalUnavailableError, sa.exc.SQLAlchemyError, OSError) as exc:
            cause = str(exc) if isinstance(exc, RetrievalUnavailableError) else "statement_failed"
            return {"status": "unavailable", "note": f"Graph read unavailable: {cause}."}

        if result.status == "partial":
            reasons = ", ".join((*result.limits, *((result.cause,) if result.cause else ())))
            note = f"Graph read is partial: {reasons}."
        elif result.status == "nothing_found":
            note = "No graph rows matched in this scope."
        else:
            note = "These graph rows matched in this scope."
        payload = {
            "status": result.status, "note": note + subject_note, "rows": rows,
            "keys": [[row.kind, row.graph_id] for row in result.rows],
        }
        return _memory_result(payload) if result.rows else payload

    async def _pointer(
        self, reading: ReadingRoom, concerns: frozenset[str], historical: datetime | None,
        person: str, *, ceiling: datetime | None = None,
    ) -> str | None:
        target, scope, dm_rooms = await self._access.authority(reading, concerns, historical)
        async with self._engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="REPEATABLE READ")
            rows = (await scoped_rows(conn, scope, target, dm_rooms, ceiling=ceiling)).c
            return cast(str | None, await conn.scalar(sa.select(rows.graph_id).where(
                rows.kind == "entity", rows.state == "admitted",
                rows.image["pointer_kind"].astext == "person",
                rows.image["pointer_ref"].astext == person,
            ).order_by(rows.graph_id).limit(1)))


_GRAPH_NOTES = {
    "ok": "These graph rows matched in this scope.",
    "partial": "The graph read reached a limit.",
    "nothing_found": "No graph rows matched in this scope.",
    "unavailable": "The graph could not be read.",
}


def _make_memory_search(deps):
    graph, metrics, room, scope = deps.graph_retrieval, deps.metrics, deps.room, deps.pass_scope

    async def memory_search(
        query: str, person: str | None = None, since: str | None = None,
        until: str | None = None, depth: int = 2,
    ) -> dict:
        """Search the scoped graph for related rows. Call this when an answer
        needs a fact, item or intention beyond the current context.

        Returns `status`: ok, partial, nothing_found or unavailable, with a `note`.

        Args:
            query: Topic or phrase to find.
            person: Name of a person to use as an additional graph anchor.
            since: First day to include, as an ISO calendar date.
            until: Last day to include, as an ISO calendar date.
            depth: Maximum hops from each match.
        """
        since_at = _memory_day(since, end=False) if since is not None else None
        until_at = _memory_day(until, end=True) if until is not None else None
        if (since is not None and since_at is None) or (until is not None and until_at is None):
            _record_room(metrics, "memory_search", "unavailable", reason="bad_date")
            return {"status": "unavailable", "note": "Use ISO dates for since and until."}
        if graph is None or room is None:
            _record_room(metrics, "memory_search", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": "The graph reader is unavailable."}
        result = await graph.read(
            room, deps.bound_conversation_id, scope, deps.now_fn(),
            anchors=RetrievalAnchors(query=query), depth=depth,
            person=person, since=since_at, until=until_at, content_checks=deps.content_checks,
        )
        _record_room(metrics, "memory_search", result["status"])
        return _with_note(result, _GRAPH_NOTES)

    return memory_search


def _make_memory_read(deps):
    graph, metrics, room, scope = deps.graph_retrieval, deps.metrics, deps.room, deps.pass_scope

    async def memory_read(id: str, depth: int = 1) -> dict:
        """Read a graph row and its neighbours. Call this when you have a graph
        ID and need to inspect it or its connections.

        Returns `status`: ok, partial, nothing_found or unavailable, with a `note`.

        Args:
            id: The graph ID shown on a row.
            depth: Maximum hops from the requested row.
        """
        if graph is None or room is None:
            _record_room(metrics, "memory_read", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": "The graph reader is unavailable."}
        result = await graph.read(
            room, deps.bound_conversation_id, scope, deps.now_fn(),
            anchors=RetrievalAnchors(graph_ids=(id,)), depth=depth,
            content_checks=deps.content_checks,
        )
        _record_room(metrics, "memory_read", result["status"])
        return _with_note(result, _GRAPH_NOTES)

    return memory_read


def _make_ask_memory(deps):
    ai_peer = deps.ai_peer
    content_checks = deps.content_checks
    honcho = deps.honcho
    metrics = deps.metrics
    question = deps.question
    room = deps.room
    _memory_route = deps._memory_route

    async def ask_memory(
        person: str,
        depth: Literal["minimal", "low", "medium", "high", "max"] = "low",
        query: str | None = None,
    ) -> dict:
        """Work out an answer to a specific QUESTION about someone, not a profile
        dump. Reach for it when a profile lookup is not enough.

        It reasons over everything you have gathered about the person, where
        `about` returns the stored conclusions: use it for a judgement that has
        to be pieced together. It is slow (several seconds or more), so call it
        only when `about` would not answer. It knows only people in THIS
        conversation.

        SHOULD: write `query` yourself, as the question you need answered, not
        the raw tag message.

        Returns `status`: ok (`answer`), nothing_known, unavailable, not_found
        or ambiguous, with a `note` on how to report it.

        Args:
            person: The name or nickname of the person, as the message said it.
            depth: How hard to reason: low for a quick read, high or max only
                when the question needs it. Deeper is slower.
            query: The question to reason about; defaults to the tag message.
        """
        level = depth if depth in _REASONING_LEVELS else "low"
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "ask_memory", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who that is right now. Say you can't "
                "check, and do not say you don't know them.",
            }
        matches = view.find(person)
        if not matches:
            miss = _miss(person, view, content_checks=content_checks)
            _record_room(metrics, "ask_memory", miss["status"], candidates=0)
            return miss
        if len(matches) > 1:
            _record_room(metrics, "ask_memory", "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous",
                **_asked_about(person, content_checks=content_checks),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        ref = matches[0]
        try:
            # OBSERVER = Nora, OBSERVED = the person: she reasons from HER model
            # of them. The QUESTION is the tagging message, hers to supply
            # (DEC-24), not a value the model invents, unless the model supplies a
            answer = await honcho.chat(
                peer_id=ai_peer,
                target=ref.honcho_peer_id,
                query=(query or question or ref.name)[:500],
                reasoning_level=level,
                **_memory_route(),
            )
        except Exception:
            log.exception("ask_memory failed for %s", ref.person_id)
            answer = None
        if answer is None:
            # chat returns None for both unreachable and nothing-to-say; neither
            # is a licence to claim ignorance of the PERSON. Report unavailable.
            metrics.inc("nora_recall_total", status="unavailable")
            _record_room(metrics, "ask_memory", "unavailable")
            return {
                "status": "unavailable",
                "person": ref.name,
                "note": "I couldn't work that out just now, either because memory "
                "did not answer or it had nothing to work from. Say you "
                "can't check right now, not that you don't know them.",
            }
        # R-18 fails closed on the WHOLE reply, so a reasoned answer carrying an
        # identifier would cost the entire message. Unlike `about`'s list, this is one
        # indivisible string — if it names an identifier it cannot be part- kept, so
        if scan_identifiers(answer, content_checks=content_checks) is not None:
            # `status="empty"` to match `about`'s all-withheld path (nothing usable
            # surfaced), not a new label value: the recall metric keeps its
            # ok/empty/unavailable cardinality.
            metrics.inc("nora_recall_total", status="empty")
            _record_room(metrics, "ask_memory", "ok", withheld=1)
            return {
                "status": "nothing_known",
                "person": ref.name,
                "withheld": 1,
                "note": "I worked something out but can't repeat it here. Answer "
                "from what you already know, or say you're not sure.",
            }
        metrics.inc("nora_recall_total", status="ok")
        _record_room(metrics, "ask_memory", "ok", depth=level)
        return _memory_result(
            {
                "status": "ok",
                "person": ref.name,
                "answer": render_file_links(answer),
                "partial_view": view.degraded,
            }
        )

    return ask_memory


def _make_search_memory(deps):
    content_checks = deps.content_checks
    honcho = deps.honcho
    metrics = deps.metrics
    room = deps.room
    bound_conversation_id = deps.bound_conversation_id

    async def search_memory(query: str) -> dict:
        """Find where something was actually SAID in this group's history, in the
        exact words. Use it to recall a specific thing somebody said a long time
        ago.

        It reaches past `load_transcript`, and returns words, not a profile
        (`about`) or a judgement (`ask_memory`).

        Returns `status`: ok, nothing_found, withheld or unavailable, with a
        `note`.

        Args:
            query: What to look for: a phrase, a topic, a thing that was said.
        """
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "search_memory", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who is here, so I can't look back through "
                "what was said. Say you can't check, and do not guess.",
            }
        # No room-wide gate here. The guard is the per-hit filter right below: a hit
        # is surfaced only if its sender resolves to a NAMED person in `roster`. An
        # unmapped account in the room, or a DEGRADED view (roster unreadable,
        roster = {p.honcho_peer_id: p.name for p in view.people}
        try:
            # SESSION-scoped to THIS channel: a workspace-wide search would surface a
            # roster member's PRIVATE DM words into this room. `bound_conversation_id`
            # is the tag's channel, captured once (DEC-24), never a model-supplied value.
            found = await honcho.search(query=query[:500], conversation_id=bound_conversation_id)
        except Exception:
            # Belt-and-braces only. The adapter catches everything itself, so this
            # handler is NOT where the outage is detected: `MessageHits.reachable` is the
            # real signal, carried out of the place that knows it.
            log.exception("search_memory failed")
            found = MessageHits(reachable=False)
        hits = found.hits
        if not found.reachable:
            _record_room(metrics, "search_memory", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't look back through what was said. Say you "
                "couldn't check, and do not say there was nothing.",
            }
        # The roster filter, the withhold and the status shaping are shared with
        # `search_memory_workspace` in `_memory_hits_result`: one filter, so the two
        # verbs can never drift into two disclosure decisions. What stays HERE is the
        return _memory_hits_result(
            hits=hits,
            roster=roster,
            record=lambda s, **kw: _record_room(metrics, "search_memory", s, **kw),
            partial_view=view.degraded,
            content_checks=content_checks,
        )

    return search_memory
