"""What every Character session shares: its two write verbs, what they staged,
and whether the session settled its entity.

A Character session is one Dreamer run for one `(unit, entity)` key, whatever
the entity is: a room (`nora.language.culture.CultureAdaptor`), a person, the
unit or Nora herself (`nora.language.character`). Each is handed its evidence
by code, may look things up with its read tools, and writes through
`character_revise(entity, body, citations)` or closes with
`character_no_change(entity, why)`. Code checks each write AS IT IS MADE
(`nora.domain.culture.check_revision`) and answers a refusal the model can act
on; an accepted one is STAGED. `accept` returns what was staged, the runtime
stamps its provenance, and the adaptor's own `commit` appends it. So a refused
write never reaches the store, and the model never writes one directly: «the
model proposes, code disposes», exactly as with an envelope.

Nothing here awaits a store: the verbs and `accept` work on what `inputs`
recorded for the run in flight (`DreamerRuntime` serializes runs per
`(adaptor, key)`, so no two runs share a key's state).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence

from nora.domain.culture import (
    MAX_CONSECUTIVE_REJECTIONS,
    Citable,
    CultureProposal,
    Refusal,
    check_revision,
)
from nora.domain.errors import AccountNotRecordableError

log = logging.getLogger(__name__)

__all__ = ["AccountNotRecordableError", "EntitySessions"]


#: What one session's entity resolves to: `None` is the session's own entity
#: (a room, a person, the unit, Nora). A string would name a person written
#: beside it; no session does, and a `CultureProposal` carries no person to
#: stage one.
Entity = str | None


class EntitySessions:
    """The verbs, the staging and the settle rule, for one adaptor's runs.

    A subclass calls `_begin` from `inputs` with what the run may cite and
    which entity names it writes, implements `_count(outcome)` for its own
    metric, and returns `accept`'s proposals from its own `commit`. Holding no
    store, this class is invisible to `tests/architecture/
    test_dream_store_calls_are_declared.py`, which is right: it awaits none.
    """

    def _sessions_init(self) -> None:
        self._citable: dict[str, Citable] = {}
        self._entities: dict[str, Mapping[str, Entity]] = {}
        self._staged: dict[str, dict[Entity, CultureProposal]] = {}
        self._kept: dict[str, set[Entity]] = {}
        self._batch_start: dict[str, int | None] = {}
        # ponytail: in memory, so a restart forgets a rejection run and the
        # batch gets up to `MAX_CONSECUTIVE_REJECTIONS` more tries; a column on
        # `dream_schedule_state` if that is ever seen to matter.
        self._rejections: dict[str, tuple[int | None, int]] = {}

    def _count(self, outcome: str) -> None:
        raise NotImplementedError

    def _begin(
        self, key: str, *, citable: Citable, entities: Mapping[str, Entity],
        batch_start: int | None,
    ) -> None:
        """Open the run for `key`: what it may cite, the entity names it may
        write (`name -> Entity`, matched case-insensitively), and what names
        the batch for the rejection run: the key's HELD watermark, which only a
        success moves, so the same backlog is the same batch however many items
        arrive while it is retried (`None`: every unsettled session counts)."""
        self._citable[key] = citable
        self._entities[key] = {name.casefold(): e for name, e in entities.items()}
        self._staged[key] = {}
        self._kept[key] = set()
        self._batch_start[key] = batch_start

    # -- the verbs ------------------------------------------------------

    def verbs(self, *, key: str) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`character_revise` and `character_no_change`, bound to `key`."""

        async def character_revise(
            entity: str, body: str, citations: list[str],
        ) -> dict[str, object]:
            """Write the new account of an entity of this session. Give the
            WHOLE account, not a change to it. A later call for the same
            entity replaces the earlier one.

            Returns `status`: ok, or refused with a `reason` and a `detail`
            saying what to do instead.

            Args:
                entity: Which account, exactly as your instructions name it.
                body: The account, in prose. No headings, no bullet points,
                    no dates.
                citations: What the account rests on, at least one, as
                    strings: the `n` of a message you were shown, or a
                    `response:<n>` episode you were shown. Nothing else.
            """
            return self._revise(key, entity, body, tuple(str(c) for c in citations))

        async def character_no_change(entity: str, why: str) -> dict[str, object]:
            """Close an entity of this session with its account unchanged.
            Call this when what you were given adds nothing the account does
            not already say, or there is nothing you can point at. The
            previous account stands.

            Returns `status`: ok, or refused with a `reason` and a `detail`.

            Args:
                entity: Which account, exactly as your instructions name it.
                why: One short sentence: why nothing changes.
            """
            return self._keep(key, entity, why)

        return [character_revise, character_no_change]

    def _entity(self, key: str, entity: str) -> Entity | Refusal:
        names = self._entities.get(key, {})
        found = names.get(entity.strip().casefold(), Refusal("", ""))
        if not isinstance(found, Refusal):
            return found
        return Refusal("unknown_entity", f"{entity!r} is not an entity of this "
                       f"session; write one of: {', '.join(sorted(names))}")

    def _refused(self, key: str, refusal: Refusal) -> dict[str, object]:
        log.warning("character: key=%s write refused: %s", key, refusal.reason)
        self._count("refused")
        return {"status": "refused", "reason": refusal.reason, "detail": refusal.detail}

    def _over(self, key: str) -> dict[str, object] | None:
        if key in self._staged:
            return None
        return {"status": "unavailable", "reason": "no_session",
                "detail": "this session is over; nothing was written"}

    def _revise(
        self, key: str, entity: str, body: str, citations: tuple[str, ...],
    ) -> dict[str, object]:
        over = self._over(key)
        if over is not None:
            return over
        resolved = self._entity(key, entity)
        if isinstance(resolved, Refusal):
            return self._refused(key, resolved)
        refusal = check_revision(
            body=body, citations=citations, citable=self._citable[key],
        )
        if refusal is not None:
            return self._refused(key, refusal)
        self._kept[key].discard(resolved)
        self._staged[key][resolved] = CultureProposal(
            body=body.strip(), citations=citations,
        )
        return {"status": "ok"}

    def _keep(self, key: str, entity: str, why: str) -> dict[str, object]:
        over = self._over(key)
        if over is not None:
            return over
        resolved = self._entity(key, entity)
        if isinstance(resolved, Refusal):
            return self._refused(key, resolved)
        self._staged[key].pop(resolved, None)
        self._kept[key].add(resolved)
        log.info("character: key=%s entity=%s unchanged: %s", key, entity, why[:200])
        return {"status": "ok"}

    # -- accept ---------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """What this run's `character_revise` calls staged, or a refusal to
        spend the batch.

        `items` is empty: a Character session answers through its verbs,
        never an envelope (`DreamerRuntime._sample`). Every staged account was
        checked when it was written, so nothing is re-judged here; what is
        judged is whether the session SETTLED its own entity (`None`):
        revised it, or closed it unchanged. A session that did neither (it ran
        out of turns, or every write was refused and it stopped) is
        `unsettled` and RAISES `AccountNotRecordableError`, so the runtime
        reports `unavailable` and HOLDS the watermark.

        **A run of unsettled sessions is bounded** (DEC-280): the
        `MAX_CONSECUTIVE_REJECTIONS`-th in a row of the same batch for one key
        (named by its held watermark, which new evidence does not move) does not raise;
        it is logged, counted `abandoned`, and returns `()`, so the key
        advances instead of re-reading a batch for ever.
        """
        staged = self._staged.pop(key, {})
        kept = self._kept.pop(key, set())
        self._citable.pop(key, None)
        self._entities.pop(key, None)
        if None not in staged and None not in kept:
            log.warning(
                "character: key=%s ended without revising or keeping its "
                "account; HOLDING the watermark", key,
            )
            self._count("unsettled")
            self._reject(key, f"the session for key={key} did not settle its entity")
            return ()
        self._rejections.pop(key, None)
        for _ in kept:
            self._count("kept")
        own = [p for entity, p in staged.items() if entity is None]
        others = [p for entity, p in staged.items() if entity is not None]
        return (*own, *others)

    def _reject(self, key: str, why: str) -> None:
        """Refuse this run. RAISES `AccountNotRecordableError` (the runtime
        reports `unavailable` and holds the watermark) unless this is the
        `MAX_CONSECUTIVE_REJECTIONS`-th rejection in a row of the same batch for
        `key`: then it RETURNS, after a WARNING and `abandoned`, and the caller
        returns `()` so the batch is consumed (DEC-280)."""
        start = self._batch_start.get(key)
        previous, run = self._rejections.get(key, (start, 0))
        run = run + 1 if previous == start else 1
        if run < MAX_CONSECUTIVE_REJECTIONS:
            self._rejections[key] = (start, run)
            raise AccountNotRecordableError(why)
        self._rejections.pop(key, None)
        log.warning(
            "character: key=%s rejected %d times in a row on the batch from %s "
            "(%s); advancing past it with no account written from it",
            key, run, start, why,
        )
        self._count("abandoned")
