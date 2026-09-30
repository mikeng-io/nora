"""`standing_revise` — the async half of the write verb; the pure checks are
`nora.domain.standing_revise` (its module docstring carries the full
argument). Both `nora.engagement.stages.debrief` (the tag path's fold) and
`nora.ingest.stages.standing_event` (the rollup fold) call THIS function
instead of constructing a `Standing` and calling `SqlStandingStore.write`
themselves — from `design/cognition-loop/70-migration.md` step 3 row 3b, "the
debrief and the ingest fold both write through it from here, so the head has
one shape."

**What changed for each caller, and what did not.** Neither caller's own
logic moves: the body's overflow retry, the disclosure screen
(`nora.agent.dream_screen`), and the working set's own validator
(`nora.engagement.stages.debrief._working_set`) all still run in the caller,
before this verb is reached — this module does not re-implement them,
because they are not part of the schema or the mode, they are part of
producing the CANDIDATE row a write verb then checks. What moves into this
module is the part `design/cognition-loop/30-cognition.md` §2 lists as
"enforced by code on every write verb": the schema check and the mode check
(`nora.domain.standing_revise.check_schema`/`check_mode`), and the
concurrency check (`check_stale_base`, on `standing_revision.id`).

**`prior` is a PARAMETER, not a read this function makes.** Both callers
already read the room's current Standing before this verb is ever reached —
`nora.domain.ports.StandingStore`'s own docstring calls that read "the
non-negotiable one" — and `nora.domain.standing.STANDING_STORE_CALLS_PER_FOLD`
pins how many bounded store awaits ONE fold makes; a second, hidden read in
here would silently widen that count past what the slot-hold budget test
(`test_the_slot_hold_counts_every_store_await_the_stage_makes`) counts in the
STAGE's own source, and it would run unbounded (outside the stage's
`asyncio.wait_for`) on a path held under `ConversationSlots`. So this
function takes the prior its caller already has, and the ONE store call it
makes itself is the write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from nora.domain.ports import StandingStore
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.standing_revise import (
    Refusal,
    check_mode,
    check_schema,
    check_stale_base,
)


@dataclass(frozen=True, slots=True)
class Revision:
    """The outcome of one `standing_revise` call.

    `committed=True` iff the write happened; `standing` is then the row as
    written. `committed=False` carries `refusal` and, when a prior existed,
    the CURRENT row (`standing`) the caller was refused against — the same
    "refused... with the current row" `30-cognition.md` §3 rule 6 asks for.
    """

    committed: bool
    standing: Standing | None
    refusal: Refusal | None


async def standing_revise(
    *,
    store: StandingStore,
    prior: Standing | None,
    platform: str,
    conversation_id: str,
    where_it_stands: str,
    working_set: tuple[WorkingSetItem, ...] = (),
    hold_open: str | None = None,
    i_owe: str | None = None,
    mode: str = "deduced",
    last_folded_row_id: int | None,
    increments_fold_count: bool = True,
    base_revision_id: str | None = None,
    prior_revision_id: str | None = None,
    now_ms: int | None = None,
) -> Revision:
    """Validate, then write, one room Standing revision.

    `prior` is the row the caller already read for this room (or `None`, the
    cold-start case) — see the module docstring for why this function does
    not read it again itself. `platform`/`conversation_id` are asserted
    against it when it is not `None`, because a caller's `prior` from a
    DIFFERENT room would make `check_stale_base` compare the wrong row's
    `fold_count`, and `fold_count`/`last_folded_row_id` are exactly the two
    columns `SqlStandingStore.write_if_unchanged`'s docstring already warns
    a mismatched key silently corrupts.

    `base_revision_id` and `prior_revision_id` are `standing_revision.id`s
    (M6, DEC-249): the token the caller read, and the id of the row that IS the
    head now (`StandingHead.revision_id`, which the caller read with `prior`).
    Both None, the two folds today, asserts nothing.

    `mode` defaults `"deduced"`: both of today's callers synthesise a
    position from the conversation they just read, which is what `deduced`
    means (`10-tiers.md` §4) — neither yet asserts `abduced` (a guess going
    out as a question, which Standing has no channel to ask today) or `said`
    (a position resting only on Nora's own prior rows, the echo rule's
    narrowest case). A future caller that needs either passes it explicitly.

    `increments_fold_count` is the ONE behavioural difference the two
    callers keep (D3, `nora.ingest.stages.standing_event`'s own module
    docstring): the tag path's fold counts; the rollup fold does not. It is a
    parameter, not two code paths, because everything else here — the
    checks and the write — is identical for both.

    `working_set` is passed through UNVALIDATED: `_working_set`
    (`nora.engagement.stages.debrief`) already ran its own screen, TTL and
    budget checks on it before either caller reaches this function, matching
    `12-schemas.md` §1 rule 5 (`Found, not yet given` is code-written, never
    a model argument this verb parses).
    """
    if prior is not None and (
        prior.platform != platform or prior.conversation_id != conversation_id
    ):
        raise ValueError(
            "standing_revise: prior is for a different room than the one "
            "being revised"
        )
    schema_refusal = check_schema(
        where_it_stands=where_it_stands, hold_open=hold_open, i_owe=i_owe,
    )
    if schema_refusal is not None:
        return Revision(committed=False, standing=None, refusal=schema_refusal)
    mode_refusal = check_mode(mode)
    if mode_refusal is not None:
        return Revision(committed=False, standing=None, refusal=mode_refusal)
    stale_refusal = check_stale_base(
        base_revision_id=base_revision_id,
        current_revision_id=prior_revision_id,
    )
    if stale_refusal is not None:
        return Revision(committed=False, standing=prior, refusal=stale_refusal)

    # CONSTRUCTED IN ITS OWN `try`, deliberately, and separately from the
    # store call below (R-27 regression, PR #624 review). `Standing(...)` is
    # an argument expression: a caller ahead of this verb (`debrief.py`'s
    # tag-path fold) used to build it outside its own `try` for exactly this
    # reason, and moving the construction into this shared verb without
    # keeping the same discipline let `__post_init__`'s `ValueError` — a
    # domain-invariant refusal, never a store failure — fall through
    # uncaught into EACH caller's `except Exception` around the whole
    # `standing_revise(...)` call, where it was indistinguishable from
    # "Postgres did not respond" and counted `unavailable`. `check_schema`
    # above now enforces every invariant this constructor does (including
    # the body-length ceiling on `hold_open`/`i_owe`), so this `except`
    # is belt-and-suspenders — unreachable today, cheap to keep, and it means
    # a FUTURE invariant added to `__post_init__` and not mirrored above
    # fails as a counted `refused`, never as an uncaught exception one layer
    # up.
    try:
        revised = Standing(
            platform=platform,
            conversation_id=conversation_id,
            body=where_it_stands,
            hold_open=hold_open,
            i_owe=i_owe,
            fold_count=(
                (prior.fold_count + 1) if increments_fold_count else prior.fold_count
            ) if prior is not None else (1 if increments_fold_count else 0),
            updated_at=now_ms if now_ms is not None else int(time.time() * 1000),
            last_folded_row_id=last_folded_row_id,
            working_set=working_set,
        )
    except ValueError as exc:
        return Revision(
            committed=False,
            standing=prior,
            refusal=Refusal(reason="invalid_standing", detail=str(exc)),
        )
    # `source` stays each store's own default (`SqlStandingStore.write`'s
    # `"fold"`) — the `StandingStore` protocol (`nora.domain.ports`) does not
    # carry the parameter, and neither existing caller passed it before this
    # verb existed (`nora.testing.standing_store.InMemoryStandingStore`, the
    # domain-only double bound to `SqlStandingStore` by
    # `tests/contract/test_standing_store_contract.py`, has no `source`
    # kwarg at all). Calling through the protocol type, not the concrete SQL
    # class, is what keeps this verb usable against either.
    await store.write(standing=revised)
    return Revision(committed=True, standing=revised, refusal=None)
