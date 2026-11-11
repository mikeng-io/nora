from __future__ import annotations

import logging

from nora.agent.lineage import memo_permits
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.results import _memory_hits_result, _record, _superseded
from nora.domain.lineage import (
    ROW,
    UNRESOLVED,
)
from nora.domain.ports import (
    MessageHits,
)
from nora.domain.turn_record import turn_handles_in

log = logging.getLogger("nora.tools")


def _make_memo_read(deps):
    content_checks = deps.content_checks
    memos = deps.memos
    metrics = deps.metrics
    _lineage_target = deps._lineage_target
    _unit = deps._unit
    memos_off = deps.memos_off

    async def memo_read(memo_id: str) -> dict:
        """Read what a memo keeps, when you need it.

        <memos> lists each kept memo's id and title, never what it keeps.

        Returns `status`: ok (`title`, `body`, `superseded`, `revision_id`),
        nothing_found or unavailable, with a `note` on how to report the last
        two.

        Args:
            memo_id: The memo's id, exactly as <memos> shows it.
        """
        unit = _unit()
        if memos is None or not unit:
            _record(metrics, "memo_read", "unavailable")
            return dict(memos_off)
        try:
            memo = await memos.get(unit=unit, memo_id=memo_id.strip())
            chain = (
                await memos.revisions(unit=unit, memo_id=memo.id)
                if memo is not None and memo.is_kept
                else ()
            )
        except Exception:
            log.exception("memo_read failed")
            _record(metrics, "memo_read", "unavailable")
            return dict(memos_off)
        if memo is None or not memo.is_kept:
            _record(metrics, "memo_read", "nothing_found")
            return {
                "status": "nothing_found",
                "note": "No memo by that id is kept here. Only the ones in <memos> "
                "exist, so check it, and don't describe one that isn't there.",
            }
        if any(turn_handles_in(r.body) for r in (memo.head, *chain)):
            target = await _lineage_target("memo_read")
            if target is None:
                return dict(memos_off)
            entries = memo.head.lineage.section(ROW)
            if turn_handles_in(memo.head.body) and (
                not entries or any(e.kind == UNRESOLVED for e in entries)
            ):
                _record(metrics, "memo_read", "unavailable")
                return dict(memos_off)
            if not memo_permits(memo.head, target[0]):
                _record(metrics, "memo_read", "nothing_found")
                return {
                    "status": "nothing_found",
                    "note": "This room cannot read that memo's cited evidence.",
                }
            chain = tuple(r for r in chain if memo_permits(r, target[0]))
        _record(metrics, "memo_read", "ok", chars=len(memo.head.body))
        result: dict = {
            "status": "ok",
            "memo_id": memo.id,
            "title": memo.title,
            "body": memo.head.body,
            "superseded": _superseded(chain),
            "revision_id": memo.head.id,
        }
        superseded = result["superseded"]
        if any(
            scan_identifiers(t, content_checks=content_checks) is not None
            for t in (memo.head.body, *superseded)
        ):
            result["note"] = (
                "This memo holds a value a reply cannot carry (a phone number, an "
                "id or a key-shaped string): a reply containing it is not sent. "
                "Say what it is and that it is kept, not the value itself."
            )
        return result

    return memo_read


def _make_search_memory_workspace(deps):
    ai_peer = deps.ai_peer
    content_checks = deps.content_checks
    honcho = deps.honcho
    metrics = deps.metrics
    persons = deps.persons
    _unit = deps._unit

    async def search_memory_workspace(query: str) -> dict:
        """Find where something was actually SAID, across EVERY room of this
        workspace. Call this when the words you need could have been said
        anywhere the same people talk.

        `search_memory` searches one room; this reaches the whole workspace.

        Returns `status`: ok (`results`), nothing_found, withheld or
        unavailable, with a `note`.

        Args:
            query: What to look for: a phrase, a topic, a thing that was said.
        """
        by_person = getattr(persons, "by_person", None)
        if by_person is None:
            # A WIRING gap (identity.toml was never threaded into this build), not
            # an empty address book: an empty one is a roster that legitimately
            # filters every hit, a missing one is «I could not check» — and R-27's
            _record(metrics, "search_memory_workspace", "unavailable", reason="no_roster")
            return {
                "status": "unavailable",
                "note": "I can't work out who is here, so I can't look back "
                "through what was said. Say you can't check, and do not "
                "guess.",
            }
        # The roster is identity.toml's WHOLE address book — not a room's live view,
        # and not a unit's either: `PersonRef` carries no unit, so this filter CANNOT
        # be unit-shaped. Nora's OWN peer is excluded the same way the room path drops
        roster = {
            ref.honcho_peer_id: ref.name
            for person_id, ref in by_person.items()
            if person_id != ai_peer
        }
        try:
            # No `conversation_id`, so the client hits Honcho's WORKSPACE
            # `/search`, narrowed to THIS unit's named scope (DEC-214): `unit` is
            # the run's own key, never a room, and `None` (a unit that cannot be
            found = await honcho.search(query=query[:500], unit=_unit())
        except Exception:
            # Belt-and-braces, exactly as on the room path: the adapter catches
            # everything itself and `MessageHits.reachable` is the real signal.
            log.exception("search_memory_workspace failed")
            found = MessageHits(reachable=False)
        if not found.reachable:
            _record(metrics, "search_memory_workspace", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't look back through what was said. Say you "
                "couldn't check, and do not say there was nothing.",
            }
        # The filter/withhold/status tail is SHARED with `search_memory` —
        # `_memory_hits_result` — so the two verbs can never drift into two disclosure
        # decisions. This verb counts on `nora_tool_calls_total` (it has no room, so
        return _memory_hits_result(
            hits=found.hits,
            roster=roster,
            record=lambda s, **kw: _record(metrics, "search_memory_workspace", s, **kw),
            partial_view=None,
            content_checks=content_checks,
        )

    return search_memory_workspace
