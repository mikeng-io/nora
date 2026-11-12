"""The Cognition session's topic-account verbs — `topic_create`,
`topic_revise`, `topic_set_status` — over the shared entry store. Built in
`session_verbs` with the notebook verbs; grouped here so the file's verbs
stay their tier's own.
"""

from __future__ import annotations

from typing import Any, Literal

from nora.app.session_verbs import SessionDeps, _refused
from nora.domain.entries import TOPIC_BODY_MAX_CHARS, EntryContent


def topic_verbs(d: SessionDeps) -> tuple[Any, ...]:
    return (_make_topic_create(d), _make_topic_revise(d), _make_topic_set_status(d))


def _entries_off() -> dict[str, object]:
    return {"status": "unavailable", "reason": "unwired",
            "detail": "the topic account store, or the custody a citation is "
                      "held to, is not wired here; nothing was written",
            "note": "Topic operations are unavailable in this session."}


def _topic_ready(d: SessionDeps) -> dict[str, object] | None:
    """The store, the shown-source custody and the write's provenance."""
    self = d.runner
    if self._entries is None or self._memory is None:
        return _entries_off()
    if self._model_id is None or d.doctrine_sha256 is None:
        return {"status": "unavailable", "reason": "unwired",
                "detail": "the write's provenance is not wired here; nothing was written",
                "note": "Topic operations are unavailable in this session."}
    return None


def _topic_citations(
    d: SessionDeps, cited: Any, carried: tuple[tuple[str, str], ...] = (),
) -> tuple[tuple[str, str], ...] | dict[str, object]:
    """`[kind, id]` keys shown to this session, or the account's own when omitted."""
    if cited is None:
        return tuple(dict.fromkeys(carried))
    sources = d.state.graph_fetched.sources
    keys: list[tuple[str, str]] = []
    for pair in cited or ():
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(x, str) and x for x in pair)):
            return _refused(
                "bad_citation",
                "each citation is [kind, id], a Record or Rollup row you were shown",
            )
        key = (pair[0], pair[1])
        source = sources.get(key)
        if source is None:
            return _refused(
                "unseen_citation",
                f"{pair[0]}:{pair[1]} was not shown to this session; cite a "
                "row the agenda showed or one a read tool returned",
            )
        if source.layer not in ("record", "rollup"):
            return _refused(
                "citation_layer",
                f"an account cites a Record or Rollup row, not a {pair[0]!r} node",
            )
        keys.append(key)
    return tuple(dict.fromkeys(keys))


def _topic_content(
    d: SessionDeps,
    *, about: tuple[tuple[str, str], ...], body: str,
    confidence: Literal["low", "medium", "high"],
    state: Literal["active", "dormant", "retired"],
    citations: tuple[tuple[str, str], ...],
) -> EntryContent:
    """The revision's content with THIS session's provenance."""
    self = d.runner
    return EntryContent(
        about=about, body=body, citations=citations, confidence=confidence,
        state=state,
        writer=d.established_by, session_id=str(d.state.session_id),
        model=self._model_id or "", instruction_hash=d.doctrine_sha256 or "",
    )


def _make_topic_create(d: SessionDeps) -> Any:
    self, unit, wrote = d.runner, d.unit, d.wrote
    async def topic_create(
        subject: str, body: str, confidence: Literal["low", "medium", "high"],
        citations: list[list[str]],
    ) -> dict[str, object]:
        """Open an account of a subject this unit keeps coming back to.
        Call this when a subject recurs across people or days, a game the
        group returns to, a place it keeps going, a show it follows, and no
        account of it is held yet. One mention never earns an account, and
        an account already held is revised, never started a second time.

        Returns `status`: ok (with the `entry_id` and `revision_id`),
        refused or unavailable, with a `reason` and `detail` when it is not
        ok. A held account about the same subject is refused `duplicate`.

        Args:
            subject: What the account is about, named the way the unit
                says it.
            body: The whole account, at most 1600 characters: what this
                subject is to the unit.
            confidence: How sure the account is: `low`, `medium` or `high`.
            citations: What the account rests on, at least one, each a
                [kind, id] source key of a Record or Rollup row you were
                shown, like `["message", "5201"]`, the `@kind:id` the
                graph block renders.
        """
        if (off := _topic_ready(d)) is not None:
            return off
        subject = subject.strip()
        if not subject:
            return _refused("bad_subject", "an account is about one subject, named")
        if not body.strip():
            return _refused("empty_body", "an account says what the subject is to this unit")
        if len(body) > TOPIC_BODY_MAX_CHARS:
            return _refused(
                "too_long", f"an account is at most {TOPIC_BODY_MAX_CHARS} characters")
        if confidence not in ("low", "medium", "high"):
            return _refused("confidence", "confidence is low, medium or high")
        cited = _topic_citations(d, citations)
        if isinstance(cited, dict):
            return cited
        if not cited:
            return _refused(
                "uncited", "an account rests on rows; cite at least one you were shown")
        async with self._lock(unit):
            held = {
                e.head.content.about[0][1].casefold(): e
                for e in await self._entries.held(unit=unit)
            }
            twin = held.get(subject.casefold())
            if twin is not None:
                return _refused(
                    "duplicate",
                    f"an account about {twin.head.content.about[0][1]!r} is already "
                    f"held ({twin.id}); revise it with topic_revise instead of "
                    "starting a second",
                )
            try:
                entry = await self._entries.create(
                    kind="topic", unit=unit,
                    content=_topic_content(
                        d, about=(("topic", subject),), body=body.strip(),
                        confidence=confidence, state="active", citations=cited))
            except ValueError as exc:
                # `duplicate` is the store's atomic answer — the live-subject
                # uniqueness — when two writes raced past the held() read.
                if str(exc) == "duplicate":
                    return _refused(
                        "duplicate",
                        f"an account about {subject!r} is already held; "
                        "revise it with topic_revise instead of starting a second",
                    )
                return _refused("invalid_entry", str(exc))
        return wrote("", "", None, entry_id=entry.id, revision_id=entry.head.id)
    return topic_create


def _make_topic_revise(d: SessionDeps) -> Any:
    self, unit, wrote = d.runner, d.unit, d.wrote
    async def topic_revise(
        entry_id: str, base_revision_id: str,
        body: str, confidence: Literal["low", "medium", "high"],
        citations: list[list[str]] | None = None,
    ) -> dict[str, object]:
        """Revise a held topic account. Call this when what the subject is
        to the unit has changed or grown. Give back the WHOLE account with
        your change folded in: `body` replaces it, and the old revision is
        kept. A retired account takes nothing more; it is refused.

        Returns `status`: ok (with the new `revision_id`), refused or
        unavailable, with a `reason` and `detail` when it is not ok
        (`stale_base` when the account changed since you were shown it).

        Args:
            entry_id: The account's id, as <topics> shows it.
            base_revision_id: The `revision` <topics> showed for it. A
                stale one is refused.
            body: The whole account after your change.
            confidence: How sure the account is now: `low`, `medium` or `high`.
            citations: Optional. What the revision rests on, each a [kind,
                id] source key of a Record or Rollup row you were shown.
                Left out, the account keeps the citations it already holds.
        """
        if (off := _topic_ready(d)) is not None:
            return off
        if not body.strip():
            return _refused("empty_body", "an account says what the subject is to this unit")
        if len(body) > TOPIC_BODY_MAX_CHARS:
            return _refused(
                "too_long", f"an account is at most {TOPIC_BODY_MAX_CHARS} characters")
        if confidence not in ("low", "medium", "high"):
            return _refused("confidence", "confidence is low, medium or high")
        entry = await self._entries.get(unit=unit, entry_id=entry_id)
        if entry is None:
            return _refused("not_found", "there is no account by that id in this unit")
        if entry.head.content.state == "retired":
            return _refused(
                "lifecycle", "that account is retired; nothing writes after it")
        cited = _topic_citations(
            d, citations, carried=entry.head.content.citations)
        if isinstance(cited, dict):
            return cited
        if not cited:
            return _refused(
                "uncited", "an account rests on rows; cite at least one you were shown")
        content = _topic_content(
            d, about=entry.head.content.about,
            body=body.strip(), confidence=confidence,
            state=entry.head.content.state, citations=cited)
        try:
            revision = await self._entries.append(
                unit=unit, entry_id=entry_id,
                base_revision_id=base_revision_id, content=content)
        except LookupError:
            return _refused("not_found", "there is no account by that id in this unit")
        except ValueError as exc:
            if str(exc) == "stale_base":
                return _refused(
                    "stale_base", f"the account's head is now {entry.head.id}; "
                    "redo your change against what it says now")
            return _refused("invalid_entry", str(exc))
        return wrote("", "", None, revision_id=revision.id)
    return topic_revise


def _make_topic_set_status(d: SessionDeps) -> Any:
    self, unit, wrote = d.runner, d.unit, d.wrote
    async def topic_set_status(
        entry_id: str, base_revision_id: str,
        status: Literal["dormant", "active", "retired"],
        citations: list[list[str]] | None = None,
    ) -> dict[str, object]:
        """Move a held topic account along its life. Call this when the
        subject stops coming back (dormant), comes back (active), or the
        account no longer describes this unit (retired). Nothing writes
        after retired; it is refused.

        Returns `status`: ok (with the new `revision_id`), refused or
        unavailable, with a `reason` and `detail` when it is not ok.

        Args:
            entry_id: The account's id, as <topics> shows it.
            base_revision_id: The `revision` <topics> showed for it. A
                stale one is refused.
            status: `dormant` when the subject stopped coming back,
                `active` when it returned, `retired` when the account no
                longer describes the unit.
            citations: Optional. What the move rests on, each a [kind, id]
                source key of a Record or Rollup row you were shown. Left
                out, the account keeps the citations it already holds.
        """
        if (off := _topic_ready(d)) is not None:
            return off
        if status not in ("dormant", "active", "retired"):
            return _refused("bad_status", "status is dormant, active or retired")
        entry = await self._entries.get(unit=unit, entry_id=entry_id)
        if entry is None:
            return _refused("not_found", "there is no account by that id in this unit")
        if entry.head.content.state == "retired":
            return _refused(
                "lifecycle", "that account is retired; nothing writes after it")
        cited = _topic_citations(
            d, citations, carried=entry.head.content.citations)
        if isinstance(cited, dict):
            return cited
        if not cited:
            return _refused(
                "uncited", "an account rests on rows; cite at least one you were shown")
        content = _topic_content(
            d, about=entry.head.content.about,
            body=entry.head.content.body,
            confidence=entry.head.content.confidence,
            state=status, citations=cited)
        try:
            revision = await self._entries.append(
                unit=unit, entry_id=entry_id,
                base_revision_id=base_revision_id, content=content)
        except LookupError:
            return _refused("not_found", "there is no account by that id in this unit")
        except ValueError as exc:
            if str(exc) == "stale_base":
                return _refused(
                    "stale_base", f"the account's head is now {entry.head.id}; "
                    "redo your change against what it says now")
            return _refused("invalid_entry", str(exc))
        return wrote("", "", None, revision_id=revision.id)
    return topic_set_status


__all__ = ["topic_verbs"]
