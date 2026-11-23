from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Literal

from nora.agent.toolkit.results import _record
from nora.agent.toolkit.rooms import _asked_about, _miss
from nora.domain.culture import character_key
from nora.domain.profile import (
    FACT_KINDS,
    SINGLETON_FACTS,
    ProfileCitable,
    handles_for,
)
from nora.domain.profile import LINE_MAX_CHARS as PROFILE_LINE_MAX_CHARS
from nora.domain.profile import Account as ProfileAccount
from nora.domain.profile import AddOp as ProfileAddOp
from nora.domain.profile import (
    ReviseOp as ProfileReviseOp,
)
from nora.domain.profile import Said as ProfileSaid
from nora.memory.adapters.tools.shared import (
    _PROFILE_DISPUTE_NOT_FOUND,
    _PROFILE_DISPUTE_NOTES,
    _PROFILE_DISPUTE_REFUSED_NOTES,
    _PROFILE_DISPUTE_UNAVAILABLE,
    _PROFILE_REFUSED_NOTES,
    _PROFILE_UNAVAILABLE,
)

log = logging.getLogger("nora.tools")


def _make_remember_told(deps):
    async def remember_told(
        content: str,
        fact_key: Literal[
            "name",
            "role",
            "relationship",
            "gender",
            "sex",
            "birthday",
            "residence",
            "languages",
            "occupation",
            "dietary_restrictions",
            "food_preferences",
            "instruction",
            "attribute",
        ],
        fact_value: str | list[str] | dict[str, Any],
        person: str = "",
        valid_from: int | None = None,
        valid_until: int | None = None,
    ) -> dict:
        """Save a stable personal fact when someone tells it; returns save status.

        The trigger is «記低». MUST ground the named value in the message. Plans
        belong in notes or reminders. Unknown fields stay absent. Returns ok,
        refused, not_found, ambiguous or unavailable, with a reporting note.

        Args:
            content: One factual claim in the teller's words.
            fact_key: Use the dedicated key for common fields; attribute for other
                explicitly stated stable human facts.
            fact_value: Common fields use their exact types: scalar string; languages,
                dietary_restrictions and food_preferences string lists; birthday
                month/day with optional year; relationship relation and unresolved_name.
                Attribute uses name (lowercase label with underscores), value (string,
                finite number, boolean or nonempty string list), optional source unit.
                Age and height require a numeric value and source unit from actual
                evidence; never invent units or convert them. No nested objects or null.
                Never alias a dedicated key. Preserve language,
                instruction and workplace qualifiers in their existing typed fields.
                The typed value alone must carry the full supported claim. Age stays
                source-reported age, never an inferred birthday or calculated current age.
            person: Who it is about; empty means the speaker.
            valid_from: Evidence-grounded effective start, epoch milliseconds or null.
            valid_until: Evidence-grounded effective end, epoch milliseconds or null."""
        c = SimpleNamespace(
            content=content,
            fact_key=fact_key,
            fact_value=fact_value,
            person=person,
            valid_from=valid_from,
            valid_until=valid_until,
            content_checks=deps.content_checks,
            lineage=deps.lineage,
            metrics=deps.metrics,
            profile=deps.profile,
            room=deps.room,
            transcript=deps.transcript,
            _now_ms=deps._now_ms,
            _unit=deps._unit,
            bound_conversation_id=deps.bound_conversation_id,
        )
        k_, _, v_ = await _remember_told__tagger(c)
        if k_:
            return v_
        k_, _, v_ = await _remember_told__room(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return remember_told


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
            persons = await intake.claim_persons(unit=unit, claim_id=target)
            if persons is None:
                _record(metrics, "profile_dispute", "not_found")
                return dict(_PROFILE_DISPUTE_NOT_FOUND)
            subject = tagger.person_id in persons
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


async def _remember_told__tagger(c):
    c.tagger = c.room.tagger
    if c.tagger is None:
        _record(c.metrics, "remember", "refused", reason="no_tagger")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "Nothing was saved: I don't know who "
                    "you are well enough to say who told me. Say you can't, and don't guess."
                ),
            },
        )
    c.text = c.content.strip()
    if not c.text:
        _record(c.metrics, "remember", "refused", reason="empty")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "Nothing was saved: there's no fact in "
                    "that. Say the fact itself, one line, then call again."
                ),
            },
        )
    if len(c.text) > PROFILE_LINE_MAX_CHARS:
        _record(c.metrics, "remember", "refused", reason="too_long")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    f"Nothing was saved: one fact fits in "
                    f"{PROFILE_LINE_MAX_CHARS} characters. Say it shorter, then call again."
                ),
            },
        )
    c.cites = c.room.trigger_message_ids
    c.unit = c._unit()
    if c.profile is None or c.lineage is None or c.unit is None or (not c.cites):
        _record(c.metrics, "remember", "unavailable", reason="unwired")
        return (True, None, dict(_PROFILE_UNAVAILABLE))
    c.subject = c.tagger
    if c.person.strip():
        c.view = await c.room.scope()
        if not c.view.available:
            _record(c.metrics, "remember", "unavailable", reason="roster")
            return (True, None, dict(_PROFILE_UNAVAILABLE))
        c.matches = c.view.find(c.person)
        if not c.matches:
            c.miss = _miss(c.person, c.view, content_checks=c.content_checks)
            _record(c.metrics, "remember", c.miss["status"], candidates=0)
            return (True, None, c.miss)
        if len(c.matches) > 1:
            _record(c.metrics, "remember", "ambiguous", candidates=len(c.matches))
            return (
                True,
                None,
                {
                    "status": "ambiguous",
                    **_asked_about(c.person, content_checks=c.content_checks),
                    ("could_be"): [p.name for p in c.matches],
                    "note": "More than one person here goes by that. Ask which one.",
                },
            )
        c.subject = c.matches[0]
    if c.fact_key == "relationship" and isinstance(c.fact_value, dict):
        c.unresolved = c.fact_value.get("unresolved_name")
        if isinstance(c.unresolved, str):
            c.scope = await c.room.scope()
            c.matches = c.scope.find(c.unresolved) if c.scope.available else ()
            if len(c.matches) == 1:
                c.fact_value = {
                    "relation": c.fact_value.get("relation"),
                    "person_id": c.matches[0].person_id,
                }
    c.now_ms = c._now_ms()
    return (False, None, None)


async def _remember_told__room(c):
    try:
        c.target = c.lineage.render_room(
            platform=c.room.platform, conversation_id=c.bound_conversation_id
        )
        c.listed = c.target.room.persons if c.target.room is not None else None
        c.here = c.bound_conversation_id
        c.source = {r.id: r for r in c.transcript or () if r.id in c.cites}
        if set(c.source) != set(c.cites):
            _record(c.metrics, "remember", "unavailable", reason="source")
            return (True, None, dict(_PROFILE_UNAVAILABLE))
        c.said = {
            n: ProfileSaid(ts=r.ts, person_id=r.person_id, room=c.here) for n, r in c.source.items()
        }
        c.account = ProfileAccount(
            key=character_key(c.unit, c.subject.person_id),
            entity_kind=("human"),
            unit=c.unit,
            subject=c.subject.person_id,
        )
        c.revision, c._ = await c.profile.account_state(key=c.account.key)
        c.heads = await c.profile.account_heads(key=c.account.key)
        c.candidates = [
            h
            for h in c.heads
            if c.fact_key in SINGLETON_FACTS
            and h.part == "facts"
            and (h.fact_key == c.fact_key)
            and (h.state in (("current"), "disputed"))
        ]
        c.old = c.candidates[0] if len(c.candidates) == 1 else None
        c.handle = next(
            (h for h, lid in handles_for(c.heads).items() if c.old is not None and lid == c.old.id),
            None,
        )
        c.cites_text = tuple(str(n) for n in c.cites)
        c.op = (
            ProfileReviseOp(
                line=c.handle,
                citations=c.cites_text,
                text=c.text,
                fact_key=c.fact_key,
                fact_value=c.fact_value,
                valid_from=c.valid_from,
                valid_until=c.valid_until,
            )
            if c.old is not None
            and c.handle is not None
            and (c.old.mode == ("told"))
            and (c.old.told_by == c.tagger.person_id)
            else ProfileAddOp(
                part="facts",
                kind=FACT_KINDS[c.fact_key],
                text=c.text,
                mode="told",
                fact_key=c.fact_key,
                fact_value=c.fact_value,
                valid_from=c.valid_from,
                valid_until=c.valid_until,
                citations=c.cites_text,
                told_by=c.tagger.person_id,
                conflicts_with=c.handle,
            )
        )
        c.result = await c.profile.profile_write(
            account=c.account,
            ops=(c.op,),
            expected_revision=c.revision,
            citable=ProfileCitable(
                messages=c.said,
                listed_rooms=frozenset({c.here})
                if c.listed is not None and c.subject.person_id in c.listed
                else frozenset(),
            ),
            now_ms=c.now_ms,
            written_by_session=("tag"),
            apply_decay=False,
        )
    except Exception:
        log.exception("remember (profile) failed")
        _record(c.metrics, "remember", "unavailable")
        return (True, None, dict(_PROFILE_UNAVAILABLE))
    if c.result.status == "ok":
        _record(c.metrics, "remember", "ok")
        return (
            True,
            None,
            {
                "status": "ok",
                "person": c.subject.name,
                "note": ("It is saved. Only now may you say 記低咗."),
            },
        )
    if c.result.status == "conflict":
        _record(c.metrics, "remember", "unavailable", reason="conflict")
        return (True, None, dict(_PROFILE_UNAVAILABLE))
    c.reason = c.result.refusals[0].reason if c.result.refusals else "refused"
    _record(c.metrics, "remember", "refused", reason=c.reason)
    return (
        True,
        None,
        {
            "status": "refused",
            "note": _PROFILE_REFUSED_NOTES.get(
                c.reason, ("Nothing was saved. Say you couldn't keep it, and don't say 記低咗.")
            ),
        },
    )
    return (False, None, None)
