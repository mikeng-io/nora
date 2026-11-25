from __future__ import annotations

import logging
from typing import Literal

from nora.agent.toolkit.results import _record
from nora.memory.adapters.tools.shared import (
    _PROFILE_DISPUTE_NOT_FOUND,
    _PROFILE_DISPUTE_NOTES,
    _PROFILE_DISPUTE_REFUSED_NOTES,
    _PROFILE_DISPUTE_UNAVAILABLE,
)

log = logging.getLogger("nora.tools")


def _make_profile_dispute(deps):
    intake = deps.memory_intake
    metrics = deps.metrics
    room = deps.room
    _unit = deps._unit

    async def profile_dispute(
        claim_id: str,
        kind: Literal["dispute", "forget"] = "dispute",
    ) -> dict:
        """Call this when somebody disputes a remembered claim, or asks you to
        forget one about them.

        Name the claim by the graph ID its row starts with. What the person it
        is about disputes is hidden at once; anyone else's dispute marks it to be
        weighed later. Only the person it is about may ask to forget it.

        Returns `status`: ok (with `effect`: hidden or disputed), refused,
        not_found or unavailable, with a `note` on how to report it.

        Args:
            claim_id: The graph ID shown first on the claim's row.
            kind: dispute (the default: it is wrong) or forget (take it away, which
                only the person it is about may ask)."""
        tagger = room.tagger
        if tagger is None:
            _record(metrics, "profile_dispute", "refused", reason="no_tagger")
            return _refused("no_tagger")
        target = claim_id.strip()
        if not target:
            _record(metrics, "profile_dispute", "refused", reason="empty")
            return _refused("empty")
        unit = _unit()
        if intake is None or unit is None:
            _record(metrics, "profile_dispute", "unavailable", reason="unwired")
            return dict(_PROFILE_DISPUTE_UNAVAILABLE)
        try:
            identities = await intake.claim_identities(unit=unit, claim_id=target)
            if identities is None:
                _record(metrics, "profile_dispute", "not_found")
                return dict(_PROFILE_DISPUTE_NOT_FOUND)
            subject = tagger.person_id in identities
            if kind == "forget" and not subject:
                # The self-only rule. The store hides every target a forget
                # request names from every read, whoever asked.
                _record(metrics, "profile_dispute", "refused", reason="forget_not_subject")
                return _refused("forget_not_subject")
            at = deps.now_fn()
            if kind == "forget":
                await intake.forget(unit=unit, author=tagger.person_id, targets=(target,), at=at)
            else:
                await intake.dispute(unit=unit, author=tagger.person_id, claim_id=target, at=at)
        except Exception:
            log.exception("profile_dispute failed")
            _record(metrics, "profile_dispute", "unavailable")
            return dict(_PROFILE_DISPUTE_UNAVAILABLE)
        effect = "hidden" if subject else "disputed"
        _record(metrics, "profile_dispute", "ok", effect=effect)
        return {"status": "ok", "effect": effect, "note": _PROFILE_DISPUTE_NOTES[effect]}

    return profile_dispute


def _refused(reason: str) -> dict:
    return {"status": "refused", "note": _PROFILE_DISPUTE_REFUSED_NOTES[reason]}
