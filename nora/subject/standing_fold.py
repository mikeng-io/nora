"""The standing fold, driven as the tag path drives it — on ONE real turn.

The seam that measures a fold: a real tag turn's `tag_trace` row, the position as it stood BEFORE
that turn, and the deployed pipeline's own `_debrief` run against them.

The composed historical fold supplies its model client, objective, bounds, and
store. Missing Standing wiring is a LookupError.

**Two things the sandbox copy gets wrong, and this corrects them, for this
seam only.**

- *Row ids.* `sandbox_from_snapshot` renumbers every row, so the prior's
  `last_folded_row_id` and the trace's own transcript ids are in the SOURCE's
  id space while the sandbox's record store is in its own. The fold reads
  `since_including_nora(after_id=watermark)` and merges the trace's rows into
  that batch by id, so an untranslated id reads the wrong stretch and
  duplicates the turn. Both are translated through the snapshot's own
  `rows` map, and an id the map does not hold is an ERROR rather than a guess.
- *Delivery.* `know()` writes her rows `PENDING`, so every row of hers in the
  batch would render `(not delivered)` — an input defect (a fold
  that concludes she sent nothing with her delivered answer in front of it).
  Her rows get back the status the source recorded.

**Two instants, and which one each input is read at is the point.** The
turn finished at `tag_trace.created_at`; production's fold ran a few hundred
milliseconds LATER and read the room then — and on a `recovered` turn her reply
is written in that gap, so a room cut at `created_at` is a room without her
reply. The fold's own instant is recorded exactly once: the `updated_at` of the
first `standing_revision` the room got after the turn (`folded_at`). So:

- the room (the sandbox, the batch, delivery) is read at `folded_at`;
- the prior and the verdict chain are read as of `created_at` — what stood
  BEFORE the turn, never the revision this turn's own fold wrote.

A turn after which production wrote no revision has no recorded fold instant,
and is refused rather than cut at a guess.

**The verdict chain is seeded too.** Master's fold re-derives the position
when the newest ungrounded `turn_verdict` is one the prior has not consumed;
without those rows its arm cannot reproduce what production did. The table
exists in both arms' schema and nothing on this branch reads it, so seeding it
changes one arm's behaviour only where production's behaviour changed.

**One counterfactual: an ABSENT prior** (`read_fold_turn(prior_absent=True)`).
A turn from before the chain began has a real record and a real
room but no recoverable position. A case may declare that tier varied — the
fold is given no prior and renders «no position yet» — and it is refused
wherever the chain DOES hold a revision at or before the turn, so the
counterfactual can never stand in for a real prior. No revision is read into
the fold on this path. Its anchor cannot be a later revision (there is none
that day), so it is the instant her reply to the turn landed: her first row
within `_REPLY_GRACE_MS` after `created_at` (a `recovered` reply is written
after the record closes), or `created_at` when she wrote none there.

**Two ways to call the model, one of them is not calling it.** `call_model=
False` swaps the client for a recorder that returns an empty completion: the
fold renders its whole input, records it, and stops at the parse (`malformed`)
without any provider being reached. That is how the two arms are proven to
DIFFER before a model call is spent («a suite that cannot see the change
measures noise»).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.tag_trace import TagTrace

# The table's ONE (de)serialisation of a transcript row, reused rather than
# copied: `SqlTagTraceStore.recent` is the store's only read and it is keyed on
# a conversation, not a turn, so a turn is read here by its `tag_id` and
# decoded exactly as the store decodes it.


__all__ = ["FoldCall", "FoldTurn", "Folded"]

#: The counter every exit of the debrief lands on exactly once
#: (`nora.engagement.stages.debrief.run`'s own docstring). Reading its delta is
#: how the outcome is known: `_debrief` returns nothing, on purpose.
_OUTCOMES = "nora_debrief_total"

#: How long after a turn's record closes her own reply to it may land and still
#: be the turn's. Five seconds is headroom over the observed `recovered` delays: a
#: later row of hers is another turn's.
_REPLY_GRACE_MS = 5_000


@dataclass(frozen=True, slots=True)
class FoldTurn:
    """One real tag turn, read out of the source, in the SOURCE's id space."""

    platform: str
    trace: TagTrace
    #: The position as it stood before the turn — the newest `standing_revision`
    #: at or before the turn's `created_at`. `None` when the chain holds none
    #: then, which is ALSO what an instant before the chain began reads as; the two cannot be told
    #: apart from here, so a caller that needs a prior must refuse rather than fold against a blank.
    prior: Standing | None
    #: Her rows' delivery status as the source recorded it, by source row id,
    #: at `folded_at`.
    delivered: Mapping[int, DeliveryStatus]
    #: Production's fold instant: the first revision's `updated_at` after the
    #: turn. `None` when production wrote none.
    folded_at: int | None = None
    #: The rows production's fold read — the watermark batch at `folded_at`
    #: plus the turn's own rows, by id — for the JUDGE. Computed from the
    #: source the way `since_including_nora` reads it (`id > watermark`,
    #: ascending, `[standing] fold_batch` of them).
    seen: tuple[TranscriptRow, ...] = ()
    #: `turn_verdict` rows of this conversation as of the turn, verbatim.
    verdicts: tuple[Mapping[str, Any], ...] = ()
    #: The instant the ROOM is read at: `folded_at` for a real prior; for a
    #: counterfactual absent prior, the instant her reply landed.
    room_at: int = 0
    #: True when the prior is the declared counterfactual: ABSENT, because
    #: the chain holds nothing that old. `prior` is then `None` by
    #: construction, never by a read that came back empty.
    counterfactual_prior: bool = False


@dataclass(frozen=True, slots=True)
class FoldCall:
    """One call the fold made to its model client, verbatim."""

    #: The fold's own label for the call (`nora.debrief.call`: `fold`,
    #: `consolidate`, …), or `""` when the arm sets none.
    call: str
    system: str
    prompt: str
    #: What came back. `None` when no model was called, or when the call raised.
    content: str | None
    finish_reason: str | None


@dataclass(frozen=True, slots=True)
class Folded:
    """What one fold did."""

    #: The `nora_debrief_total{outcome}` label this fold moved — `written`,
    #: `malformed`, `unavailable`, `timeout`, `refused_overflow`, `screened`,
    #: `error`, `cancelled`. `written` is the only one that wrote anything.
    outcome: str
    #: State read back after a successful fold: a room fold writes it; a unit
    #: fold may also retain existing state under `no_change`.
    position: str | None
    #: `Standing.working_set` the store holds after the fold — what it found
    #: this turn and has not given the room, read back beside
    #: `position` and by the SAME condition: `()` unless `outcome == "written"`.
    #: Never backfilled and never guessed at — `working_set_from_json`'s own
    #: reading of a row with nothing in the column is `()`, which is exactly
    #: what a fold that found nothing new should read as.
    working_set: tuple[WorkingSetItem, ...] = ()
    calls: tuple[FoldCall, ...] = ()


def _translated(row_id: int, rows: Mapping[int | str, int], *, what: str) -> int:
    mapped = rows.get(row_id)
    if mapped is None:
        raise LookupError(
            f"{what} names source row {row_id}, which the sandbox was not "
            "seeded with; the fold would read a different stretch than it did"
        )
    return mapped
