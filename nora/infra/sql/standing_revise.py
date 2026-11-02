"""`standing_revise` — the async half of the write verb; the pure checks are
`nora.domain.standing_revise` (its module docstring carries the full
argument). Callers (`nora.engagement.stages.debrief`, the Cognition session's
`standing_revise` tool) call THIS function instead of constructing a `Standing`
and calling `SqlStandingStore.write` themselves, so the head has one shape.

**What stays in the caller.** The body's overflow retry, the disclosure screen
(`nora.agent.dream_screen`) and the working set's own validator
(`nora.engagement.stages.debrief._working_set`) run before this verb is
reached: they produce the CANDIDATE row a write verb then checks. What this
module enforces on every write is the schema check and the mode check
(`nora.domain.standing_revise.check_schema`/`check_mode`) and the concurrency
check (`check_stale_base`, on `standing_revision.id`).

**`prior` is a PARAMETER, not a read this function makes.** Callers already
read the room's current Standing before this verb is reached, and
`nora.domain.standing.STANDING_STORE_CALLS_PER_FOLD` pins how many bounded
store awaits ONE fold makes; a second, hidden read in here would widen that
count past what the slot-hold budget test
(`test_the_slot_hold_counts_every_store_await_the_stage_makes`) counts in the
STAGE's own source, and it would run unbounded (outside the stage's
`asyncio.wait_for`) on a path held under `ConversationSlots`. So this function
takes the prior its caller already has, and the ONE store call it makes itself
is the write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

from nora.domain.lineage import Lineage
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
    the CURRENT row (`standing`) the caller was refused against.
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
    exclude_persons: tuple[str, ...] | None = None,
    lineage: Any = None,
    mode: str = "deduced",
    last_folded_row_id: int | None,
    increments_fold_count: bool = True,
    base_revision_id: str | None = None,
    prior_revision_id: str | None = None,
    now_ms: int | None = None,
    source: str = "fold",
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
    (M6, DEC-250): the token the caller read, and the id of the row that IS the
    head now (`StandingHead.revision_id`, which the caller read with `prior`).
    Both None, the two folds today, asserts nothing.

    `mode` defaults `"deduced"`: both of today's callers synthesise a
    position from the conversation they just read, which is what `deduced`
    means — neither yet asserts `abduced` (a guess going
    out as a question, which Standing has no channel to ask today) or `said`
    (a position resting only on Nora's own prior rows, the echo rule's
    narrowest case). A future caller that needs either passes it explicitly.

    `increments_fold_count` is the one behavioural switch: whether this write
    counts as a fold (`fold_count + 1`). It is a parameter, not two code
    paths, because the checks and the write are identical either way.

    `working_set` is passed through UNVALIDATED: `_working_set`
    (`nora.engagement.stages.debrief`) already ran its own screen, TTL and
    budget checks on it before the caller reaches this function
    (`Found, not yet given` is code-written, never a model argument this verb
    parses).
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

    # CONSTRUCTED IN ITS OWN `try`, separately from the store call below.
    # `Standing(...)` is an argument expression: a `ValueError` from its
    # `__post_init__` is a domain-invariant refusal, never a store failure, and
    # uncaught it would reach the caller's `except Exception` around the whole
    # `standing_revise(...)` call, indistinguishable from "Postgres did not
    # respond" and counted `unavailable`. `check_schema` above enforces every
    # invariant this constructor does (including the body-length ceiling on
    # `hold_open`/`i_owe`), so this `except` is belt-and-suspenders: a future
    # invariant added to `__post_init__` and not mirrored above fails as a
    # counted `refused`, never as an uncaught exception one layer up.
    try:
        revised = Standing(
            platform=platform,
            conversation_id=conversation_id,
            body=where_it_stands,
            hold_open=hold_open,
            i_owe=i_owe,
            # An omitted section or gate is PRESERVED, never cancelled:
            # the fold's output names what changed; the prior carries what
            # did not (design/notebook-memory-types/20 §5, "a missing output
            # section does not cancel an obligation").
            exclude_persons=(
                exclude_persons if exclude_persons is not None
                else (prior.exclude_persons if prior is not None else ())
            ),
            lineage=(
                lineage if lineage is not None
                else (prior.lineage if prior is not None else Lineage())
            ),
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
    # NO-CHANGE IS IDEMPOTENT (design/notebook-memory-types/20 §6): a revise
    # whose produced CONTENT is the head writes nothing — content is body,
    # hold_open, i_owe, working_set and the fold watermark, so fold
    # bookkeeping (`fold_count`, `updated_at`) is compared OUT of the check:
    # a `source="cognition"` verbatim re-assert does not count as a fold
    # either, while an actual content change or a watermark advance still
    # does.
    if prior is not None and replace(
        revised, fold_count=prior.fold_count, updated_at=prior.updated_at,
    ) == prior:
        return Revision(committed=True, standing=prior, refusal=None)
    # `source` stays each store's own default (`SqlStandingStore.write`'s
    # `"fold"`) unless the caller passes one. Calling through the protocol type
    # (`nora.domain.ports.StandingStore`), not the concrete SQL class, keeps
    # this verb usable against the in-memory double
    # (`nora.testing.standing_store.InMemoryStandingStore`) too.
    await store.write(standing=revised, source=source)
    return Revision(committed=True, standing=revised, refusal=None)
