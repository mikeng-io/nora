"""`InMemoryTurnVerdictStore` — the domain-only double for
`nora.infra.sql.turn_verdicts.SqlTurnVerdictStore`, bound to it by
`tests/contract/test_turn_verdict_store_contract.py`.

**It refuses what Postgres refuses, and that is not automatic.** Writing
`InMemoryStandingStore` this afternoon taught the lesson the hard way: a fake
that stores whatever it is handed is MORE PERMISSIVE THAN THE REAL STORE, and a
contract suite cannot see it unless a test reaches the store by going *around*
the domain type. So this mirrors both CHECKs and — the one that matters most —
the duplicate-key refusal that IS the never-rewritten property.
"""

from __future__ import annotations

from nora.domain.turn_verdict import UNSUPPORTED_MAX_ITEMS, TurnVerdict


class DuplicateVerdictError(Exception):
    """A second verdict for a turn that already has one.

    Named rather than a bare `ValueError` because it is the never-rewritten
    property announcing itself: the SQL store raises `IntegrityError` from the
    primary key, and a caller distinguishing "this turn was debriefed twice"
    from "this verdict is malformed" needs the two to be different types.
    """


class InMemoryTurnVerdictStore:
    def __init__(self) -> None:
        self._rows: dict[str, TurnVerdict] = {}

    async def save(self, *, verdict: TurnVerdict) -> None:
        # The never-rewritten property. `SqlTurnVerdictStore.save` is a plain
        # INSERT against a `tag_id` primary key, so a second save raises; a
        # double that silently replaced would let a test prove the guarantee
        # holds when it does not.
        if verdict.tag_id in self._rows:
            raise DuplicateVerdictError(
                f"turn {verdict.tag_id} already has a verdict; "
                "a TurnVerdict is never rewritten"
            )
        # `turn_verdict_grounded_has_no_unsupported_check` and
        # `turn_verdict_unsupported_fits_the_list_check`. Unreachable through
        # `TurnVerdict.__post_init__`, which is exactly why they are here: the
        # CHECKs exist for a writer that got a value in some other way, and a
        # double without them cannot stand in for the real store on that input.
        if verdict.grounded and verdict.unsupported:
            raise ValueError("turn_verdict_grounded_has_no_unsupported_check")
        if len(verdict.unsupported) > UNSUPPORTED_MAX_ITEMS:
            raise ValueError("turn_verdict_unsupported_fits_the_list_check")
        self._rows[verdict.tag_id] = verdict

    async def read(self, *, tag_id: str) -> TurnVerdict | None:
        return self._rows.get(tag_id)

    async def latest(self, *, conversation_id: str) -> TurnVerdict | None:
        """Newest-first by `created_at`, with `tag_id` breaking a tie.

        The tie-break is not decoration: two turns in one millisecond are
        reachable, and `max()` over an unordered dict would pick whichever
        Python happened to hash first — so the double would answer a different
        verdict than Postgres for the same rows, which is the fake-diverges
        failure this file already exists to avoid.
        """
        rows = [v for v in self._rows.values()
                if v.conversation_id == conversation_id]
        if not rows:
            return None
        return max(rows, key=lambda v: (v.created_at, v.tag_id))
