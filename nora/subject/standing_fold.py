"""The standing fold, driven as the tag path drives it — on ONE real turn.

`design/standing-compact/20-verification.md` §1: nothing measured a fold. The
debrief ran inside every eval turn and nobody graded what it wrote, so a change
to what the fold is shown and told had no instrument. This is the seam that
makes one: a real tag turn's `tag_trace` row, the position as it stood BEFORE
that turn, and the deployed pipeline's own `_debrief` run against them.

**Why `EngagementPipeline._debrief` and not `debrief.run`.** The two arms the
corpus compares are master's fold and the branch's, and `run` has a different
signature on each (master's takes a verdict store and a rebuild ceiling). The
pipeline's `_debrief(trace=, platform=, notes_read=)` is the same on both, and
it is the exact call production makes: it reads the composed `Wiring` — the
deployment's model client, objective source, `max_chars`, `fold_batch`,
timeouts — and passes it the way the live turn does. Reaching a private method
of the composed graph is the price of measuring the deployed path rather than
assembling a second one, which is the one thing this package exists to refuse.
A missing wiring (`[standing] enabled = false`) is a `LookupError`, never a
fold that silently did nothing.

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
  batch would render `(not delivered)` — the exact input defect issue #199 was
  (a fold that concluded she had sent nothing with her delivered answer in
  front of it). Her rows get back the status the source recorded.

**Two ways to call the model, one of them is not calling it.** `call_model=
False` swaps the client for a recorder that returns an empty completion: the
fold renders its whole input, records it, and stops at the parse (`malformed`)
without any provider being reached. That is how the two arms are proven to
DIFFER before a model call is spent (`20-verification` §1: «a suite that cannot
see the change measures noise»).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from nora.domain.models import DeliveryStatus
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.engagement.stages.debrief import Completion
from nora.infra.sql import create_readonly_sql_engine
from nora.infra.sql.schema import TAG_TRACE
from nora.infra.sql.snapshot import snapshot

# The table's ONE (de)serialisation of a transcript row, reused rather than
# copied: `SqlTagTraceStore.recent` is the store's only read and it is keyed on
# a conversation, not a turn, so a turn is read here by its `tag_id` and
# decoded exactly as the store decodes it.
from nora.infra.sql.tag_traces import _json_to_row

from .obtain import Subject

__all__ = ["FoldCall", "FoldTurn", "Folded", "fold", "read_fold_turn"]

#: The counter every exit of the debrief lands on exactly once
#: (`nora.engagement.stages.debrief.run`'s own docstring). Reading its delta is
#: how the outcome is known: `_debrief` returns nothing, on purpose.
_OUTCOMES = "nora_debrief_total"


@dataclass(frozen=True, slots=True)
class FoldTurn:
    """One real tag turn, read out of the source, in the SOURCE's id space."""

    platform: str
    trace: TagTrace
    #: The position as it stood before the turn — the newest `standing_revision`
    #: at or before the turn's `created_at`. `None` when the chain holds none
    #: then, which is ALSO what an instant before migration 0059's chain began
    #: reads as; the two cannot be told apart from here, so a caller that needs
    #: a prior must refuse rather than fold against a blank.
    prior: Standing | None
    #: Her rows' delivery status as the source recorded it, by source row id.
    delivered: Mapping[int, DeliveryStatus]


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
    #: The position the store holds after the fold, when `outcome == "written"`.
    position: str | None
    calls: tuple[FoldCall, ...]


async def read_fold_turn(subject: Subject, *, source_url: str, tag_id: str) -> FoldTurn:
    """The turn `tag_id` names, in the subject's room, as the source holds it.

    Read-only by construction (`create_readonly_sql_engine`). Refused — never
    substituted — when the id names no turn, or a turn in another room: the
    room is the subject's, resolved from the deployment's policy, and a trace
    from elsewhere would fold one room's turn into another's position.
    """
    platform, conversation_id = subject.room
    engine = create_readonly_sql_engine(source_url)
    try:
        async with engine.connect() as conn:
            found = (await conn.execute(
                sa.select(TAG_TRACE).where(TAG_TRACE.c.tag_id == tag_id)
            )).all()
        if len(found) != 1:
            raise LookupError(
                f"the source holds {len(found)} tag_trace rows for tag {tag_id!r}; "
                "a case names exactly one real turn"
            )
        [row] = found
        if row.conversation_id != conversation_id:
            raise ValueError(
                f"tag {tag_id!r} was a turn in another conversation; the case's "
                "snapshot names the room the fold writes into"
            )
        trace = TagTrace(
            tag_id=row.tag_id,
            conversation_id=row.conversation_id,
            messages=tuple(row.messages),
            transcript=tuple(_json_to_row(r) for r in row.transcript),
            outcome=row.outcome,
            created_at=row.created_at,
        )
        # The SAME reconstruction the sandbox was seeded from, at the turn's own
        # instant: `standing` is `SqlStandingStore.read_as_of`, the transcript
        # carries each row's recorded delivery status.
        shot = await snapshot(
            engine,
            platform=platform,
            conversation_id=conversation_id,
            culture_key=subject.culture_key,
            unit=subject.unit,
            before_ms=trace.created_at,
        )
    finally:
        await engine.dispose()
    return FoldTurn(
        platform=platform,
        trace=trace,
        prior=shot.standing,
        delivered={r.id: r.delivery_status for r in shot.transcript if r.is_nora},
    )


class _Recording:
    """The fold's `ModelClient`, wrapped: every call is recorded, and with
    `call_model=False` none is made."""

    def __init__(self, inner: Any, *, call_model: bool) -> None:
        self._inner = inner
        self._call_model = call_model
        self.calls: list[FoldCall] = []

    @property
    def model(self) -> str:
        return str(self._inner.model)

    async def complete(
        self, *, system: str, prompt: str,
        trace_attributes: Mapping[str, Any] | None = None,
    ) -> Completion:
        label = str((trace_attributes or {}).get("nora.debrief.call") or "")
        if not self._call_model:
            self.calls.append(FoldCall(label, system, prompt, None, None))
            # Empty and well-formed: the fold parses it as `malformed` and
            # writes nothing. No provider is reached.
            return Completion(content="", finish_reason="stop")
        try:
            out = await self._inner.complete(
                system=system, prompt=prompt, trace_attributes=trace_attributes,
            )
        except BaseException:
            self.calls.append(FoldCall(label, system, prompt, None, None))
            raise
        self.calls.append(FoldCall(label, system, prompt, out.content, out.finish_reason))
        return out


def _outcome_counts(subject: Subject) -> dict[str, float]:
    counts: dict[str, float] = {}
    for key, value in subject.deps.metrics.snapshot().items():
        if key and key[0] == _OUTCOMES:
            outcome = dict(key[1] or ()).get("outcome", "")
            counts[outcome] = counts.get(outcome, 0.0) + value
    return counts


def _translated(row_id: int | None, rows: Mapping[int | str, int], *, what: str) -> int | None:
    if row_id is None:
        return None
    mapped = rows.get(row_id)
    if mapped is None:
        raise LookupError(
            f"{what} names source row {row_id}, which the sandbox was not "
            "seeded with; the fold would read a different stretch than it did"
        )
    return mapped


async def fold(
    subject: Subject,
    turn: FoldTurn,
    *,
    rows: Mapping[int | str, int],
    call_model: bool = True,
) -> Folded:
    """Seed the prior, run the deployed pipeline's fold on `turn`, read back.

    `rows` is the snapshot's own map from each source row id to the id the
    sandbox gave its copy (`nora.subject.Sandboxed.rows`).
    """
    platform, conversation_id = subject.room
    if (turn.platform, turn.trace.conversation_id) != (platform, conversation_id):
        raise ValueError("the turn is not in the subject's room")
    pipeline = subject.deps.engagement
    wiring = getattr(pipeline, "_debrief_wiring", None)
    if wiring is None:
        raise LookupError(
            "the composed pipeline holds no debrief wiring — [standing] enabled "
            "is false, so there is no fold to measure"
        )

    # Her rows' delivery, as recorded (see the module docstring).
    for source_id, status in turn.delivered.items():
        if status is not DeliveryStatus.PENDING and source_id in rows:
            await subject.deps.audit.set_delivery_outcome(
                row_id=rows[source_id], status=status,
            )

    # The prior, with its watermark in the sandbox's id space. `replace`, so
    # every other field the arm's `Standing` carries rides through unchanged.
    if turn.prior is not None:
        await wiring.store.write(standing=dataclasses.replace(
            turn.prior,
            last_folded_row_id=_translated(
                turn.prior.last_folded_row_id, rows, what="the prior's watermark",
            ),
        ))

    trace = dataclasses.replace(
        turn.trace,
        transcript=tuple(
            dataclasses.replace(
                r, id=_translated(r.id, rows, what="the turn's transcript"),
            )
            for r in turn.trace.transcript
        ),
    )

    recorder = _Recording(wiring.client, call_model=call_model)
    before = _outcome_counts(subject)
    pipeline._debrief_wiring = dataclasses.replace(wiring, client=recorder)
    try:
        await pipeline._debrief(trace=trace, platform=platform)
    finally:
        pipeline._debrief_wiring = wiring
    after = _outcome_counts(subject)
    moved = sorted(k for k, v in after.items() if v > before.get(k, 0.0))
    if len(moved) != 1:
        raise RuntimeError(
            f"the fold moved {moved or 'no'} outcome label(s); exactly one is "
            "every exit's contract, so this reading cannot be attributed"
        )
    [outcome] = moved
    position = None
    if outcome == "written":
        held = await wiring.store.read(platform=platform, conversation_id=conversation_id)
        position = held.body if held is not None else None
    return Folded(outcome=outcome, position=position, calls=tuple(recorder.calls))
