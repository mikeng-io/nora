"""`standing_revise`: the write verb for room Standing, the PURE half.

Every writer of a room's `Standing` goes through one verb
(`nora.infra.sql.standing_revise.standing_revise`, the async half that resolves
a caller's `base_revision_id` against the stored head and then calls the checks
below), so every row passes the SAME validator and the head has one shape.

This module holds the checks that need no store: the tier's allowed reasoning
modes (`deduced`, `abduced` and `said`) and the schema (`Where it stands` is
required and non-blank; `I hold open` and `I owe` are optional and, when
given, must not be blank: a section a caller means to clear is omitted,
`None`, never sent empty). `nora.infra.sql.standing_revise` reads the current
head, checks `base_revision_id` against it (needs a store, so it is not here),
calls these functions, and only then calls `SqlStandingStore.write`.
`nora/domain/`'s own rule: stdlib and dataclasses only, no store, no clock read
(every timestamp is a caller's argument).

`I hold open` and `I owe` (`nora.domain.standing.Standing.hold_open`/`.i_owe`)
have a schema check, a render (`nora.language.standing_render.render_standing`)
and a history (`standing_revision`). The debrief objective
(`nora/dreamer/objectives/standing-debrief.md`) does not ask the model for
them, so both stay `None` on rows it writes; asking for them is a doctrine
change gated by the eval suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.standing import STANDING_BODY_CEILING_CHARS

#: The reasoning modes Standing allows. `computed` is Rollup-only, `told` and
#: `induced` are Register/Character-only; a Standing row is `deduced`, `abduced` or `said`.
ALLOWED_MODES: tuple[str, ...] = ("deduced", "abduced", "said")

#: The section names a `standing_revise` caller may write, closed: a heading
#: not in the schema is refused. `Found, not yet given` (`working_set`) is not
#: here: it has its own validator (`nora.engagement.stages.debrief._working_set`)
#: run before a caller reaches this verb, and code-written sections (history,
#: folds) are never a model argument.
REQUIRED_SECTION = "where_it_stands"
OPTIONAL_SECTIONS: tuple[str, ...] = ("hold_open", "i_owe")


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused `standing_revise` call: `ADR 0009`'s `refused` status,
    answered with a reason from a closed vocabulary (the refusal names the rule
    so the caller can rewrite) and counted by that reason, never silently
    dropped.
    """

    reason: str
    detail: str


def check_mode(mode: str) -> Refusal | None:
    """The mode must be one Standing allows."""
    if mode not in ALLOWED_MODES:
        return Refusal(
            reason="bad_mode",
            detail=f"{mode!r} is not one of {ALLOWED_MODES} — Standing's "
                   f"allowed reasoning modes (10-tiers.md §4)",
        )
    return None


def check_schema(
    *, where_it_stands: str, hold_open: str | None, i_owe: str | None,
) -> Refusal | None:
    """`Where it stands` is required and non-blank; a given optional section
    must not be blank. A caller that means "nothing to add here" omits the
    section (`None`), it does not send an empty one.

    Mirrors every invariant `nora.domain.standing.Standing.__post_init__`
    enforces on these three fields, including the `STANDING_BODY_CEILING_CHARS`
    ceiling on `hold_open`/`i_owe`, not just the blank check. A caller must be
    able to tell "the model's answer breaks the schema" (refused) from "the
    store did not respond" (unavailable) (R-27); checking the ceiling HERE,
    before `Standing(...)` is constructed, keeps the constructor's own
    `ValueError` unreachable through this verb, so it is never mistaken for a
    store failure.
    """
    if not where_it_stands or not where_it_stands.strip():
        return Refusal(
            reason="missing_required_section",
            detail="'Where it stands' is required and was blank",
        )
    if len(where_it_stands) > STANDING_BODY_CEILING_CHARS:
        return Refusal(
            reason="section_too_long",
            detail=f"'Where it stands' is {len(where_it_stands)} characters, "
                   f"over the {STANDING_BODY_CEILING_CHARS}-character ceiling",
        )
    for name, value in (("I hold open", hold_open), ("I owe", i_owe)):
        if value is not None and not value.strip():
            return Refusal(
                reason="blank_section",
                detail=f"{name!r} was given blank; omit it (None) instead "
                       f"of sending an empty section",
            )
        if value is not None and len(value) > STANDING_BODY_CEILING_CHARS:
            return Refusal(
                reason="section_too_long",
                detail=f"{name!r} is {len(value)} characters, over the "
                       f"{STANDING_BODY_CEILING_CHARS}-character ceiling",
            )
    return None


def check_stale_base(
    *, base_revision_id: str | None, current_revision_id: str | None,
) -> Refusal | None:
    """A `base_revision_id` that is not the current revision is refused
    `stale_base`.

    The token is `standing_revision.id`, the id `standing_read` shows as
    `revision` (DEC-250). Every write to the head appends a `standing_revision`
    row in the same transaction (`SqlStandingStore.write`), so the id changes
    whenever the head does; `fold_count` would not, since it does not move when
    a write passes `increments_fold_count=False`.

    `base_revision_id=None` means the caller asserts no base (a caller
    serialized through `ConversationSlots`) and always passes. A base against a
    head that has no revision id (no head, or a head the chain does not end at)
    is stale: a token that names nothing cannot be the current one.
    """
    if base_revision_id is None:
        return None
    if current_revision_id is None:
        return Refusal(
            reason="stale_base",
            detail="this room's Standing has no revision to write against; "
                   "read it again",
        )
    if base_revision_id != current_revision_id:
        return Refusal(
            reason="stale_base",
            detail=f"base_revision_id={base_revision_id} but the current "
                   f"revision is {current_revision_id}",
        )
    return None
