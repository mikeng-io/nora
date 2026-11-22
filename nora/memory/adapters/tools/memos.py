from __future__ import annotations

import logging

from nora.agent.lineage import memo_permits
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.results import _record, _superseded
from nora.domain.lineage import (
    ROW,
    UNRESOLVED,
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
