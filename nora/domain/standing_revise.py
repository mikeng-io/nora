"""`standing_revise` — the write verb for room Standing, the PURE half.

`design/cognition-loop/70-migration.md` step 3 row **3b**: "`standing_revise`
and the room Standing schema, live: the debrief and the ingest fold both
write through it from here, so the head has one shape." Two callers wrote
`Standing` rows directly before this — the tag path's debrief
(`nora.engagement.stages.debrief`) and the rollup fold
(`nora.ingest.stages.standing_event`) — each constructing a `Standing` and
calling `SqlStandingStore.write` on its own. From this step both go through
one verb (`nora.infra.sql.standing_revise.standing_revise`, the async half
that resolves a caller's `base_revision_id` against the stored head and then
calls the checks below), so a row written by the tag path's debrief and one
written by the rollup fold pass the SAME validator
(`design/cognition-loop/30-cognition.md` §2, "a note or event written by
cognition and one written on a tag turn pass the same validator" — the same
argument, one tier over).

This module holds the checks that need no store: the tier's allowed
reasoning modes (`design/cognition-loop/10-tiers.md` §4 — `deduced`,
`abduced` and `said` are the three Standing allows) and the schema
(`design/cognition-loop/12-schemas.md` §5 — `Where it stands` is required,
non-blank; `I hold open` and `I owe` are optional and, when given, must not
be blank — a section a caller means to clear is omitted, `None`, never sent
empty). `nora.infra.sql.standing_revise` is the impure half: it reads the
current head, checks `base_revision_id` against it (needs a store, so it is
not here), calls these functions, and only then calls
`SqlStandingStore.write`. `nora/domain/`'s own rule: stdlib and dataclasses
only, no store, no clock read (every timestamp is a caller's argument).

**Scope of this step.** `I hold open` and `I owe` are new columns
(`nora.domain.standing.Standing.hold_open`/`.i_owe`) with a real schema
check, a real render (`nora.language.standing_render.render_standing`) and a
real history (`standing_revision`, migration `0076_standing_sections`) —
but neither existing caller's objective
(`nora/dreamer/objectives/standing-debrief.md`) asks the model for them yet,
so both stay `None` on every row this step writes. That is a deliberate cut,
recorded in `design/09-decision-index.md`'s row for this step and in
`70-migration.md` §4's open positions: turning the model on for these two
sections is a doctrine change, gated by its own read-back
(`AGENTS.md`, "gate-prompt-changes-need-the-eval-suite"), not a migration
step. The schema is live and enforced; population is not this step's job.
"""

from __future__ import annotations

from dataclasses import dataclass

#: `design/cognition-loop/10-tiers.md` §4: the reasoning modes Standing
#: allows. `computed` is Rollup-only, `told` and `induced` are Register/
#: Character-only; a Standing row is `deduced`, `abduced` or `said`.
ALLOWED_MODES: tuple[str, ...] = ("deduced", "abduced", "said")

#: The section names a `standing_revise` caller may write, closed
#: (`design/cognition-loop/12-schemas.md` §1 rule 1 — "a heading not in the
#: schema... is refused"). `Found, not yet given` (`working_set`) is not
#: here: it has its own validator (`nora.engagement.stages.debrief._working_set`)
#: already run before a caller reaches this verb, the same way a Register
#: row's code-written sections (history, folds) are never a model argument
#: (12-schemas.md §1 rule 5).
REQUIRED_SECTION = "where_it_stands"
OPTIONAL_SECTIONS: tuple[str, ...] = ("hold_open", "i_owe")


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused `standing_revise` call — `ADR 0009`'s `refused` status,
    answered with a reason from a closed vocabulary
    (`design/cognition-loop/12-schemas.md` §1 rule 6: "the refusal names the
    rule... so it can rewrite") and counted by that reason, never silently
    dropped.
    """

    reason: str
    detail: str


def check_mode(mode: str) -> Refusal | None:
    """`10-tiers.md` §4: the mode must be one Standing allows."""
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
    """`12-schemas.md` §5: `Where it stands` is required and non-blank; a
    given optional section must not be blank — a caller that means "nothing
    to add here" omits the section (`None`), it does not send an empty one.
    """
    if not where_it_stands or not where_it_stands.strip():
        return Refusal(
            reason="missing_required_section",
            detail="'Where it stands' is required and was blank",
        )
    for name, value in (("I hold open", hold_open), ("I owe", i_owe)):
        if value is not None and not value.strip():
            return Refusal(
                reason="blank_section",
                detail=f"{name!r} was given blank; omit it (None) instead "
                       f"of sending an empty section",
            )
    return None


def check_stale_base(
    *, base_revision_id: int | None, current_fold_count: int | None,
) -> Refusal | None:
    """`30-cognition.md` §3 rule 6: "A `base_revision_id` that is not the
    current revision is refused `stale_base`, with the current row."

    Room Standing's revision counter is `fold_count` (the plain count of
    folds written onto the head, `nora.domain.standing.Standing.fold_count`)
    — there is no separate revision id on this tier, and adding one would be
    a second counter a caller could disagree with the first about.
    `base_revision_id=None` means the caller is not asserting a base at all
    (both today's callers, serialized through `ConversationSlots` and so
    never racing each other) and always passes.
    """
    if base_revision_id is None:
        return None
    if current_fold_count is None:
        return Refusal(
            reason="stale_base",
            detail="no revision exists yet for this room",
        )
    if base_revision_id != current_fold_count:
        return Refusal(
            reason="stale_base",
            detail=f"base_revision_id={base_revision_id} but the current "
                   f"revision is {current_fold_count}",
        )
    return None
