"""The standing fold, driven as the tag path drives it — on ONE real turn.

The seam that measures a fold: a real tag turn's `tag_trace` row, the position as it stood BEFORE
that turn, and the deployed pipeline's own `_debrief` run against them.

**Why `EngagementPipeline._debrief` and not `debrief.run`.** The two arms the
corpus compares are master's fold and the branch's, and `run` has a different
signature on each (master's takes a verdict store and a rebuild ceiling). The
pipeline's `_debrief(trace=, platform=)` is the same on both, and
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

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.tag_trace import TagTrace
from nora.engagement.stages.debrief import Completion
from nora.infra.sql import create_readonly_sql_engine, create_sql_engine
from nora.infra.sql.schema import MESSAGES, STANDING_REVISION, TAG_TRACE, TURN_VERDICT
from nora.infra.sql.snapshot import snapshot
from nora.infra.sql.standing import SqlStandingStore

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
    #: The position the store holds after the fold, when `outcome == "written"`.
    position: str | None
    #: `Standing.working_set` the store holds after the fold — what it found
    #: this turn and has not given the room, read back beside
    #: `position` and by the SAME condition: `()` unless `outcome == "written"`.
    #: Never backfilled and never guessed at — `working_set_from_json`'s own
    #: reading of a row with nothing in the column is `()`, which is exactly
    #: what a fold that found nothing new should read as.
    working_set: tuple[WorkingSetItem, ...] = ()
    calls: tuple[FoldCall, ...] = ()


async def read_fold_turn(
    subject: Subject, *, source_url: str, tag_id: str, prior_absent: bool = False,
) -> FoldTurn:
    """The turn `tag_id` names, in the subject's room, as the source holds it.

    `prior_absent` is the declared counterfactual (module docstring): refused
    with `ValueError` when the chain holds any revision at or before the turn.

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
                    f"the source holds {len(found)} tag_trace rows for tag "
                    f"{tag_id!r}; a case names exactly one real turn"
                )
            [row] = found
            if row.conversation_id != conversation_id:
                raise ValueError(
                    f"tag {tag_id!r} was a turn in another conversation; the "
                    "case's snapshot names the room the fold writes into"
                )
            reply_at = None
            if prior_absent:
                held = (await conn.execute(
                    sa.select(sa.func.count()).select_from(STANDING_REVISION).where(
                        STANDING_REVISION.c.platform == platform,
                        STANDING_REVISION.c.conversation_id == conversation_id,
                        STANDING_REVISION.c.updated_at <= row.created_at,
                    )
                )).scalar()
                if held:
                    raise ValueError(
                        f"the standing chain holds {held} revision(s) at or "
                        f"before tag {tag_id!r}; a counterfactual ABSENT prior "
                        "is refused where a real one exists"
                    )
                reply_at = (await conn.execute(
                    sa.select(sa.func.min(MESSAGES.c.ts)).where(
                        MESSAGES.c.platform == platform,
                        MESSAGES.c.conversation_id == conversation_id,
                        MESSAGES.c.is_nora == 1,
                        MESSAGES.c.ts > row.created_at,
                        MESSAGES.c.ts <= row.created_at + _REPLY_GRACE_MS,
                    )
                )).scalar()
            folded_at = (await conn.execute(
                sa.select(sa.func.min(STANDING_REVISION.c.updated_at)).where(
                    STANDING_REVISION.c.platform == platform,
                    STANDING_REVISION.c.conversation_id == conversation_id,
                    STANDING_REVISION.c.updated_at > row.created_at,
                )
            )).scalar()
            verdicts = tuple(
                dict(v._mapping) for v in (await conn.execute(
                    sa.select(TURN_VERDICT).where(
                        TURN_VERDICT.c.conversation_id == conversation_id,
                        TURN_VERDICT.c.created_at <= row.created_at,
                    )
                )).all()
            )
        trace = TagTrace(
            tag_id=row.tag_id,
            conversation_id=row.conversation_id,
            messages=tuple(row.messages),
            transcript=tuple(_json_to_row(r) for r in row.transcript),
            outcome=row.outcome,
            created_at=row.created_at,
        )
        prior = None
        if prior_absent:
            room_at = int(reply_at) if reply_at is not None else trace.created_at
        else:
            # BEFORE the turn: the chain's newest revision at or before it.
            standing_store = SqlStandingStore(engine)
            prior = await standing_store.read_as_of(
                platform=platform, conversation_id=conversation_id,
                before_ms=trace.created_at,
            )
            room_at = int(folded_at) if folded_at is not None else trace.created_at
        # The ROOM as the fold read it: the same reconstruction the sandbox is
        # seeded from, at the room's instant.
        shot = await snapshot(
            engine,
            platform=platform,
            conversation_id=conversation_id,
            culture_key=subject.culture_key,
            unit=subject.unit,
            before_ms=room_at,
        )
    finally:
        await engine.dispose()
    if prior_absent and shot.standing is not None:
        # The count above covers the turn's own instant; this covers the few
        # seconds up to the reply. A revision there would be seeded into the
        # sandbox and silently become the prior the case says is absent.
        raise ValueError(
            f"a standing revision stands at {room_at}, the room's instant for "
            f"tag {tag_id!r}; a counterfactual ABSENT prior cannot be honoured"
        )
    watermark = (prior.last_folded_row_id if prior is not None else None) or 0
    batch = sorted(
        (r for r in shot.transcript if r.id > watermark), key=lambda r: r.id,
    )[: subject.cfg.standing.fold_batch]
    by_id = {r.id: r for r in batch}
    by_id.update({r.id: r for r in trace.transcript})
    return FoldTurn(
        platform=platform,
        trace=trace,
        prior=prior,
        delivered={r.id: r.delivery_status for r in shot.transcript if r.is_nora},
        folded_at=int(folded_at) if folded_at is not None else None,
        seen=tuple(sorted(by_id.values(), key=lambda r: r.id)),
        verdicts=verdicts,
        room_at=room_at,
        counterfactual_prior=prior_absent,
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


def _translated(row_id: int, rows: Mapping[int | str, int], *, what: str) -> int:
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
    # With NO prior (the counterfactual), the sandbox must hold none either:
    # anything there would be folded as the position the case says is absent.
    if turn.prior is None:
        held = await wiring.store.read(platform=platform, conversation_id=conversation_id)
        if held is not None:
            raise ValueError(
                "the sandbox holds a position and the case declares the prior "
                "absent; folding would read one it says does not exist"
            )
    else:
        watermark = turn.prior.last_folded_row_id
        await wiring.store.write(standing=dataclasses.replace(
            turn.prior,
            last_folded_row_id=(
                None if watermark is None
                else _translated(watermark, rows, what="the prior's watermark")
            ),
        ))

    # The verdict chain as of the turn (see the module docstring). Written
    # through the table rather than a store: the branch has no store for it, and
    # master's arm reads it through its own.
    if turn.verdicts:
        engine = create_sql_engine(subject.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.insert(TURN_VERDICT), [dict(v) for v in turn.verdicts])
        finally:
            await engine.dispose()

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
    working_set: tuple[WorkingSetItem, ...] = ()
    if outcome == "written":
        held = await wiring.store.read(platform=platform, conversation_id=conversation_id)
        position = held.body if held is not None else None
        working_set = held.working_set if held is not None else ()
    return Folded(
        outcome=outcome, position=position, working_set=working_set,
        calls=tuple(recorder.calls),
    )
