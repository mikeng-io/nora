from __future__ import annotations

import logging
from typing import Literal

from nora.agent.catalog.groups import ROOM_ONLY_MEMORY_ADAPTORS
from nora.agent.memory_search import MemorySearch, room_label
from nora.agent.room import RoomView
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.results import (
    _memory_day,
    _memory_hits_result,
    _memory_result,
    _record_room,
    _with_note,
)
from nora.agent.toolkit.rooms import _asked_about, _miss
from nora.domain.external_files import (
    render_file_links,
)
from nora.domain.lineage import (
    LiveRoom,
)
from nora.domain.memory_index import KINDS_BY_CATEGORY, MemoryKind
from nora.domain.models import (
    PersonRef,
)
from nora.domain.ports import (
    MessageHits,
)
from nora.memory.adapters.tools.shared import (
    _GRAPH_READ_NOTES,
    _INDEX_UNAVAILABLE,
    _MEMORY_BAD_DATE,
    _MEMORY_BAD_KIND,
    _MEMORY_BAD_SPAN,
    _MEMORY_UNLISTED,
    _REASONING_LEVELS,
)

log = logging.getLogger("nora.tools")


def _make__index_room_label(deps):
    lineage = deps.lineage

    def _index_room_label(key: str) -> str:
        """A room key as she reads it: its policy label, else its network."""
        return room_label(lineage.rooms if lineage is not None else (), key)

    return _index_room_label


def _make__memory_search_index(deps):
    content_checks = deps.content_checks
    dream_adaptor = deps.dream_adaptor
    lineage = deps.lineage
    metrics = deps.metrics
    persons = deps.persons
    room = deps.room
    bound_conversation_id = deps.bound_conversation_id
    graph_reads = deps.graph_reads
    now_fn = deps.now_fn

    async def _memory_search_index(
        search: MemorySearch,
        text: str,
        ref: PersonRef | None,
        view: RoomView,
        kinds: tuple[str, ...],
        since_ms: int | None,
        until_ms: int | None,
        depth: int = 2,
        seed: tuple[str, str] | None = None,
    ) -> dict:
        """`memory_search` while `[memory.index] read`: the one retrieval"""
        asked: dict = {"person": ref.name} if ref is not None else {}
        if lineage is None:
            _record_room(metrics, "memory_search", "unavailable", reason="unwired")
            return {"status": "unavailable", **asked, "note": _INDEX_UNAVAILABLE}
        # Fail closed (R-27): anything that raises on the way, the policy's
        # render of this room, the search, or the packet, is a counted
        # `unavailable` with its note, never an exception out of the tool.
        try:
            target = lineage.render_room(
                platform=room.platform,
                conversation_id=bound_conversation_id,
                live=LiveRoom(
                    person_ids=frozenset(p.person_id for p in view.people),
                    unnamed=view.unnamed,
                    available=view.available,
                    degraded=view.degraded,
                ),
            )
            listed = target.room.persons if target.room is not None else None
            if ref is not None and (listed is None or ref.person_id not in listed):
                # The live roster resolved them, but policy does not list them
                # for this room (or the room is `*`, persons=None): the
                # index's person filter would read false everywhere and answer
                _record_room(metrics, "memory_search", "unavailable", reason="unlisted")
                return {**_MEMORY_UNLISTED, **asked}
            now = now_fn()
            result = await search.read(
                query=text[:500],
                seed=seed,
                depth=depth,
                person_id=ref.person_id if ref else None,
                target=target,
                now_ms=int(now.timestamp() * 1000),
                people=view.people,
                kinds=kinds,
                since_ms=since_ms,
                until_ms=until_ms,
                own_room=dream_adaptor in ROOM_ONLY_MEMORY_ADAPTORS,
                social_off=persons.social_off_ids(listed or ()),
                cache=graph_reads,
                content_checks=content_checks,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("memory graph tool failed (%s)", type(exc).__name__)
            result = {"status": "unavailable", "note": _INDEX_UNAVAILABLE}
        _record_room(metrics, "memory_read" if seed else "memory_search", result["status"])
        return _memory_result(result) if result["status"] in {"ok", "partial"} else result

    return _memory_search_index


def _make_memory_read(deps):
    content_checks = deps.content_checks
    dream_adaptor = deps.dream_adaptor
    lineage = deps.lineage
    memory_retrieval = deps.memory_retrieval
    metrics = deps.metrics
    persons = deps.persons
    room = deps.room
    bound_conversation_id = deps.bound_conversation_id
    graph_reads = deps.graph_reads
    now_fn = deps.now_fn

    async def memory_read(kind: str, id: str, depth: int = 1) -> dict:
        """Read one memory node and its connections. Call this when a graph node
        needs closer inspection.

        Returns `status` with a note:
        `ok`: scoped node and evidence.
        `partial`: more memory is available.
        `nothing_found`: no node matched in scope.
        `unavailable`: the read could not finish.
        Depth zero returns full fields and relation evidence.
        Use it to check an entity or statement the graph may already hold
        before proposing it as new.

        Args:
            kind: The node kind shown in the graph.
            id: The node id shown in the graph.
            depth: Hops from this node, zero to four.
        """
        if memory_retrieval is None:
            _record_room(metrics, "memory_read", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": "The graph could not be read."}
        if lineage is None:
            _record_room(metrics, "memory_read", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": "The read scope is unavailable."}
        try:
            view = await room.scope()
            target = lineage.render_room(
                platform=room.platform,
                conversation_id=bound_conversation_id,
                live=LiveRoom(
                    person_ids=frozenset(p.person_id for p in view.people),
                    unnamed=view.unnamed,
                    available=view.available,
                    degraded=view.degraded,
                ),
            )
            listed = target.room.persons if target.room else ()
            result = await memory_retrieval.read(
                seed=(kind, id),
                depth=depth,
                target=target,
                now_ms=int(now_fn().timestamp() * 1000),
                own_room=dream_adaptor in ROOM_ONLY_MEMORY_ADAPTORS,
                social_off=persons.social_off_ids(listed or ()),
                cache=graph_reads,
                content_checks=content_checks,
            )
            _record_room(metrics, "memory_read", result["status"])
            if result["status"] in {"ok", "partial"}:
                result.update(_memory_result(result))
            return _with_note(result, _GRAPH_READ_NOTES)
        except Exception as exc:  # noqa: BLE001
            log.warning("memory_read failed (%s)", type(exc).__name__)
            _record_room(metrics, "memory_read", "unavailable", reason="error")
            return {"status": "unavailable", "note": "The graph could not be read."}

    return memory_read


def _make_memory_search(deps):
    content_checks = deps.content_checks
    memory_retrieval = deps.memory_retrieval
    metrics = deps.metrics
    question = deps.question
    room = deps.room
    _memory_search_index = _make__memory_search_index(deps)

    async def memory_search(
        query: str,
        person: str | None = None,
        since: str | None = None,
        until: str | None = None,
        kinds: list[MemoryKind] | None = None,
        depth: int = 2,
    ) -> dict:
        """Search what this group said, shared, kept, planned and where people
        went. Call this when an answer needs something older than what you see.

        It returns matches as an aged, linked graph. Narrow by person, dates or
        kind; set `person` for what you know about someone. For everything said
        on a day or span with no topic, use `record_query`. Check the graph for
        an entity or statement before proposing it as new.

        SHOULD: write `query` yourself, as what you are looking for, not the raw
        tag message.

        Returns `status`: ok, partial, nothing_found, unavailable, not_found or
        ambiguous, with a `note` on how to report it.

        Args:
            query: What you are looking for: a topic, a phrase or a question.
            person: Only what is about this person or said by them: the name
                or nickname the message used. Leave it out for everyone.
            since: The first day to include, as YYYY-MM-DD.
            until: The last day to include, as YYYY-MM-DD.
            kinds: Only these kinds of memory. `media` is pictures and videos
                sent in a chat; a shared link is a message. Leave it out to
                search them all.
            depth: Links out from each match, 0 to 4; default 2.
        """
        # The model writes the dates, so a bad one is an ordinary event, never
        # an exception (R-5) and never silently dropped: `unavailable` with
        # how to fix the call.
        since_at = _memory_day(since, end=False) if since and since.strip() else None
        until_at = _memory_day(until, end=True) if until and until.strip() else None
        if (since and since.strip() and since_at is None) or (
            until and until.strip() and until_at is None
        ):
            _record_room(metrics, "memory_search", "unavailable", reason="bad_date")
            return dict(_MEMORY_BAD_DATE)
        if since_at is not None and until_at is not None and until_at < since_at:
            _record_room(metrics, "memory_search", "unavailable", reason="bad_span")
            return dict(_MEMORY_BAD_SPAN)
        wanted = tuple(dict.fromkeys(kinds or ()))
        if any(k not in KINDS_BY_CATEGORY for k in wanted):
            _record_room(metrics, "memory_search", "unavailable", reason="bad_kind")
            return dict(_MEMORY_BAD_KIND)
        node_kinds = tuple(n for k in wanted for n in KINDS_BY_CATEGORY[k])
        # An empty `query` falls back to the tag message (`question`), as `ask_memory`
        # does, kept on purpose: the docstring asks the model to write its own, and a
        # blank one is still a search for what was asked, never an error.
        text = query.strip() or question
        view: RoomView | None = None
        ref: PersonRef | None = None
        if person is not None and person.strip():
            view = await room.scope()
            if not view.available:
                _record_room(metrics, "memory_search", "unavailable")
                return {
                    "status": "unavailable",
                    "note": "I can't work out who that is right now. Say you can't "
                    "check, and do not say you don't know them.",
                }
            matches = view.find(person)
            if not matches:
                miss = _miss(person, view, content_checks=content_checks)
                _record_room(metrics, "memory_search", miss["status"], candidates=0)
                return miss
            if len(matches) > 1:
                _record_room(metrics, "memory_search", "ambiguous", candidates=len(matches))
                return {
                    "status": "ambiguous",
                    **_asked_about(person, content_checks=content_checks),
                    "could_be": [p.name for p in matches],
                    "note": "More than one person here goes by that. Ask which one.",
                }
            ref = matches[0]
            text = text or ref.name
        if memory_retrieval is None:
            # Not reachable through `build_tool_specs`, which offers this verb
            # only with the index wired (`GRAPH_MEMORY_TOOLS`). Said, not raised,
            # if a caller builds it some other way: there is no Honcho behind it
            _record_room(metrics, "memory_search", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": _INDEX_UNAVAILABLE}
        return await _memory_search_index(
            memory_retrieval,
            text,
            ref,
            view if view is not None else await room.scope(),
            node_kinds,
            int(since_at.timestamp() * 1000) if since_at is not None else None,
            int(until_at.timestamp() * 1000) if until_at is not None else None,
            depth,
        )

    return memory_search


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
