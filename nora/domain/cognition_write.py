"""Cognition's write-verb DRY RUN: the pure half shared by the note, event
and Standing dry-run wrappers (design/cognition-loop/70-migration.md step
3a: "its note, event and Standing verbs in dry run (would-write rows
recorded, nothing committed)").

**Validation is not duplicated here.** Each tier already has its own pure
checks — `nora.domain.note_body.parse_note_body`, `nora.domain.gatherings`'s
`Gathering.__post_init__` / `validate_event_field_mode` /
`validate_event_field_confidence` / `StaleEventRevisionError`, and
`nora.domain.standing_revise`'s `check_schema` / `check_mode` /
`check_stale_base` — and `nora.agent.cognition_dry_run` (the impure
wrappers) calls exactly those same functions before recording a
`WouldWrite` row, so a row cognition would have written in shadow and one
the live path actually writes pass the SAME validator
(`design/cognition-loop/30-cognition.md` §2's own rule for the note/event
verbs, extended here to the shadow path rather than restated). This module
holds only the shape both wrappers record, and the one piece of pure
arithmetic (the event tier's stale-base comparison) that has nowhere else
to live because, unlike Standing, no `check_stale_base`-equivalent exists
for events yet — `StaleEventRevisionError` is raised by the SQL store
itself, past the boundary a dry run must not cross.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The three tiers 3a's dry run covers (30-cognition.md §2's write-verb
#: table, minus `memo` — memo ships at step 7 and no verb exists yet).
Tier = Literal["note", "event", "standing"]
Outcome = Literal["would_commit", "refused"]

TIERS: tuple[Tier, ...] = ("note", "event", "standing")
OUTCOMES: tuple[Outcome, ...] = ("would_commit", "refused")


@dataclass(frozen=True, slots=True)
class WouldWrite:
    """One write-verb call in shadow: what it WOULD have done, never what it
    did. `nora.agent.cognition_dry_run`'s wrappers construct this and hand
    it to `CognitionDryRunStore.record_would_write`
    (`nora.domain.ports`); nothing here touches a store, and nothing here
    is itself a store row — `recorded_at` and the session id are the SQL
    layer's job, matching every other append-only table in this tree
    (`nora.infra.sql.history.APPEND_ONLY`).

    `args` is the verb's own arguments, as given — the session's replay
    input (`30-cognition.md` §1, "a session is replayable"). `candidate` is
    the row that WOULD have been written, only present on `would_commit`;
    `refusal_reason`/`refusal_detail` are only present on `refused`, and
    `refusal_reason` is the tier's own refusal vocabulary (`bad_mode`,
    `missing_required_section`, `stale_base`, a `note_body` rule name, …) —
    this module does not narrow it further, because narrowing it here would
    be a second copy of each tier's own list to keep in sync."""

    tier: Tier
    verb: str
    platform: str
    conversation_id: str
    args: dict[str, object]
    outcome: Outcome
    refusal_reason: str | None = None
    refusal_detail: str | None = None
    candidate: dict[str, object] | None = None
    base_revision_id: str | None = None

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"not a 3a dry-run tier: {self.tier!r}")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"not a dry-run outcome: {self.outcome!r}")
        if self.outcome == "would_commit" and self.candidate is None:
            raise ValueError("a would_commit WouldWrite must carry its candidate row")
        if self.outcome == "refused" and self.refusal_reason is None:
            raise ValueError("a refused WouldWrite must carry a refusal_reason")


def stale_event_base(
    *, base_revision_id: str | None, current_head_revision_id: str | None,
) -> bool:
    """Mirrors `nora.domain.gatherings.StaleEventRevisionError`'s condition
    without raising and without a store: `base_revision_id` omitted
    (`None`) always passes — `event_revise`'s own contract is "leave it out
    to write regardless of what changed since" (`10-tiers.md` §3) — and,
    given, it must equal the row's current `head_revision_id`. Returns
    `True` when the base is stale (a refusal), mirroring the boolean shape
    `nora.domain.standing_revise.check_stale_base` uses for the tier next
    door."""
    if base_revision_id is None:
        return False
    return base_revision_id != current_head_revision_id


def note_base_refusal(
    *, parent_revision_id: str, head_revision_id: str | None, retired: bool,
) -> tuple[str, str] | None:
    """The refusal the live note store makes on an append, as `(reason, detail)`,
    or `None` when the append would land (`nora.infra.sql.notes.SqlNoteStore.
    _append_on_conn`'s compare-and-swap: the note must exist in the unit, be
    open, and have `parent_revision_id` as its head). `head_revision_id` is
    `None` for a note the unit does not hold. The order is the store's own: a
    missing note, then a retired one, then a head that moved. The reasons are
    this tier's names for what the store raises (`StaleNoteHeadError` with no
    observed head, `RetiredNoteError`, `StaleNoteHeadError`), spelled as the
    event and Standing tiers spell theirs (`not_found`, `stale_base`)."""
    if head_revision_id is None:
        return "not_found", "no note by that id in this unit; read the notebook again"
    if retired:
        return "note_retired", "the note was retired; read the notebook again"
    if parent_revision_id != head_revision_id:
        return "stale_base", f"the note's head is {head_revision_id!r}; read it again"
    return None
