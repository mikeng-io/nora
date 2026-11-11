from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Literal

from nora.agent.toolkit.media import _CONCLUSION_MAX_CHARS
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
    _PROFILE_DISPUTE_NOTES,
    _PROFILE_DISPUTE_REFUSED_NOTES,
    _PROFILE_DISPUTE_UNAVAILABLE,
    _PROFILE_REFUSED_NOTES,
    _PROFILE_UNAVAILABLE,
)

log = logging.getLogger("nora.tools")


def _make_remember(deps):
    ai_peer = deps.ai_peer
    honcho = deps.honcho
    metrics = deps.metrics
    room = deps.room
    _memory_route = deps._memory_route

    async def remember(content: str) -> dict:
        """Record a settled fact about the person speaking. Call this when
        somebody tells you something about themselves that is now just TRUE.

        The trigger word is «記低». It writes to your long-term memory of THAT
        person, which `about` and `ask_memory` read back; nothing else can write a
        person's settled fact. Check what it is first: a fact about the speaker
        is this tool; one about the group or anyone else your notebook keeps after
        this turn, so say it will be kept; an open question nothing settles is
        `note_create`, something later is `remind`. It is not a scratchpad.

        Returns `status`: ok, refused or unavailable, with a `note` on how to
        report it.

        Args:
            content: The settled fact itself, one line, in the speaker's own
                words."""
        tagger = room.tagger
        if tagger is None:
            # Fail-closed on identity, the same refusal `note_create` makes:
            # without a resolved tagger there is no observed peer to attach
            # the fact to, and a conclusion written under a guessed peer id
            # would be a memory about nobody that `about` can never find.
            _record(metrics, "remember", "refused", reason="no_tagger")
            return {
                "status": "refused",
                "note": "Nothing was saved: I don't know who you are well enough "
                "to attach this to. Say you can't, and don't guess.",
            }
        text = content.strip()
        if not text:
            _record(metrics, "remember", "refused", reason="empty")
            return {
                "status": "refused",
                "note": "Nothing was saved: there's no fact in that. Say the "
                "fact itself, one line, then call again.",
            }
        if len(text) > _CONCLUSION_MAX_CHARS:
            # The endpoint's own bound (see `_CONCLUSION_MAX_CHARS`): refuse
            # with the fix rather than let the write die as an `unavailable`
            # that reads like memory being down.
            _record(metrics, "remember", "refused", reason="too_long")
            return {
                "status": "refused",
                "note": f"Nothing was saved: that's past even a long memory's "
                f"limit ({_CONCLUSION_MAX_CHARS} characters). Say the "
                "fact itself, in one line, then call again.",
            }
        try:
            written = await honcho.conclude(
                peer_id=ai_peer,
                target=tagger.honcho_peer_id,
                content=text,
                **_memory_route(),
            )
        except Exception:
            # Belt-and-braces only, the same shape `note_create` carries: the
            # adapter/double catches everything itself, so this handler is NOT
            # where the outage is detected. `conclude`'s False on failure is
            # the real signal; this just keeps a raised error from becoming an
            # ERROR dressed as a PASS (R-27).
            log.exception("remember failed")
            written = False
        if not written:
            # THE WHOLE POINT OF THREE STATES: memory got the call and could
            # not commit it. This is `unavailable` — never a silent success,
            # and never a fallback to the note tier (a failed conclusion is a
            # failure; writing it somewhere else would teach tomorrow's `about`
            # to lie by omission).
            _record(metrics, "remember", "unavailable")
            return {
                "status": "unavailable",
                "note": "My memory isn't accepting writes right now, so nothing "
                "was saved. Say so, never say 記低咗, and don't write it "
                "down as a note instead.",
            }
        _record(metrics, "remember", "ok")
        # ADR 0015: «say 記低咗 only when it is saved» is an OUTPUT rule, so
        # it rides on the one result where it is true.
        return {
            "status": "ok",
            "person": tagger.name,
            "note": "It is saved. Only now may you say 記低咗.",
        }

    return remember


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
        k_, _, v_ = await _remember_told__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _remember_told__p2(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return remember_told


def _make_profile_dispute(deps):
    content_checks = deps.content_checks
    lineage = deps.lineage
    metrics = deps.metrics
    profile = deps.profile
    room = deps.room
    _now_ms = deps._now_ms
    _unit = deps._unit

    async def profile_dispute(
        claim: str,
        person: str = "",
        kind: Literal["dispute", "forget"] = "dispute",
    ) -> dict:
        """Call this when somebody disputes a remembered fact, or its person asks you to forget it.

        It records who disputed it and what they said, against your account of that
        person. What the person themselves disputes is hidden at once; anyone else's
        dispute is marked and weighed later. Only the person themselves may ask to
        forget.

        Returns `status`: ok (with `effect`: hidden, disputed or recorded), refused,
        not_found, ambiguous or unavailable, with a `note` on how to report it.

        Args:
            claim: What is wrong, one line, in the speaker's own words.
            person: Whose remembered fact it is, as named here. Leave empty when it
                is the speaker's own.
            kind: dispute (the default: it is wrong) or forget (take it away, which
                only the person themselves may ask)."""
        # The dispute door (35 §2), mirroring `remember_told`: the speaker is
        # the disputer, the named person is the account's subject, and the
        # store rules on it in one transaction. The line the claim names is
        # matched by code (`nora.domain.profile.match_disputed_line`), never
        # by the model: an unmatched claim is recorded, not guessed at.
        tagger = room.tagger
        if tagger is None:
            _record(metrics, "profile_dispute", "refused", reason="no_tagger")
            return {
                "status": "refused",
                "note": "Nothing was recorded: I don't know who you are well "
                "enough to say who disputes it. Say you can't, and "
                "don't guess.",
            }
        text = claim.strip()
        if not text:
            _record(metrics, "profile_dispute", "refused", reason="empty")
            return {
                "status": "refused",
                "note": "Nothing was recorded: say what is wrong, one line, then call again.",
            }
        if len(text) > PROFILE_LINE_MAX_CHARS:
            _record(metrics, "profile_dispute", "refused", reason="too_long")
            return {
                "status": "refused",
                "note": f"Nothing was recorded: a dispute fits in "
                f"{PROFILE_LINE_MAX_CHARS} characters. Say it shorter, "
                "then call again.",
            }
        cites = room.trigger_message_ids
        unit = _unit()
        if profile is None or lineage is None or unit is None or not cites:
            # A wiring gap, or a turn no Record message raised: «I couldn't»,
            # never a record.
            _record(metrics, "profile_dispute", "unavailable", reason="unwired")
            return dict(_PROFILE_DISPUTE_UNAVAILABLE)
        subject = tagger
        if person.strip():
            view = await room.scope()
            if not view.available:
                _record(metrics, "profile_dispute", "unavailable", reason="roster")
                return dict(_PROFILE_DISPUTE_UNAVAILABLE)
            matches = view.find(person)
            if not matches:
                miss = _miss(person, view, content_checks=content_checks)
                _record(metrics, "profile_dispute", miss["status"], candidates=0)
                return miss
            if len(matches) > 1:
                _record(metrics, "profile_dispute", "ambiguous", candidates=len(matches))
                return {
                    "status": "ambiguous",
                    **_asked_about(person, content_checks=content_checks),
                    "could_be": [p.name for p in matches],
                    "note": "More than one person here goes by that. Ask which one.",
                }
            subject = matches[0]
        try:
            result = await profile.record_dispute(
                account=ProfileAccount(
                    key=character_key(unit, subject.person_id),
                    entity_kind="human",
                    unit=unit,
                    subject=subject.person_id,
                ),
                disputed_by=tagger.person_id,
                claim=text,
                kind=kind,
                message_id=cites[0],
                now_ms=_now_ms(),
            )
        except Exception:
            log.exception("profile_dispute failed")
            _record(metrics, "profile_dispute", "unavailable")
            return dict(_PROFILE_DISPUTE_UNAVAILABLE)
        if result.status == "ok":
            _record(metrics, "profile_dispute", "ok", effect=result.effect)
            return {
                "status": "ok",
                "effect": result.effect,
                "person": subject.name,
                "note": _PROFILE_DISPUTE_NOTES.get(result.effect or "", "It is recorded."),
            }
        if result.status == "conflict":
            _record(metrics, "profile_dispute", "unavailable", reason="conflict")
            return dict(_PROFILE_DISPUTE_UNAVAILABLE)
        reason = result.refusal.reason if result.refusal else "refused"
        _record(metrics, "profile_dispute", "refused", reason=reason)
        return {
            "status": "refused",
            "note": _PROFILE_DISPUTE_REFUSED_NOTES.get(
                reason, "Nothing was recorded. Say you couldn't record it."
            ),
        }

    return profile_dispute


async def _remember_told__p1(c):
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


async def _remember_told__p2(c):
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
