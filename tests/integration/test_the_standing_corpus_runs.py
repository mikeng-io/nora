"""The standing corpus's harness, end to end, with no model reachable.

`design/standing-compact/20-verification.md` §1. Three readings the harness
must give before a single model call is spent on it:

- **the arm is measurable** — the fold's rendered input, printed without a
  model, carries what this branch changed: `<standing_prior written=…>`,
  `<steps>`, a tool result past the old 600-character clip. And it reads the
  RIGHT stretch: the sandbox renumbers every row, so the prior's watermark and
  the trace's own rows have to be translated or the fold reads a different
  batch than production's did;
- **no model is ERROR, never a verdict** — a fold that could not run wrote
  nothing, and «nothing was written» must not grade as «nothing stale was
  re-affirmed»;
- **no prior is refused, never faked** — a turn with no standing revision
  before it is exactly what an instant before the 0059 chain reads as, and
  folding it against «no position yet» would grade a fold on an input it
  never had.

Every case runs the ONE path (`evals.backbone._drive`): a room seeded into a
source, a moment named in it, a sandbox of the case's own.
"""
from __future__ import annotations

from typing import Any

import pytest
from strands_evals import Case
from strands_evals.types.evaluation import EvaluationData

from evals.backbone import (
    ERROR,
    NORA_CALLS,
    NORA_ERROR,
    NORA_SHOWN,
    MeetsGrader,
    Spec,
    _drive,
    state_of,
)
from evals.rebuilt.grading import Judge
from nora.domain.models import DeliveryStatus
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.infra.sql import create_sql_engine
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.subject import Knowledge, Message
from tests.sandbox_fixtures import BEFORE_MS, ROOM, STEP_MS, seed_source

_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}
_TAG = "t-fold-probe"
_PRIOR = "I owe the room an answer about the venue; nobody has confirmed the date."
#: Placed PAST the old 600-character clip and inside the new 4000 bound, so
#: its presence in the fold's input is the branch's arm and its absence is
#: master's.
_FINDING = "FINDING-PAST-THE-OLD-CLIP"
_RESULT = ("r" * 3000) + _FINDING + ("r" * 500)


async def _seed(pg_url: str, pg_template: str, tmp_path: Any, *, prior: bool) -> Any:
    # ANOTHER ROOM FIRST, so the probe room's rows start at a source id the
    # sandbox will not reuse. Without the offset, source and sandbox ids agree
    # by accident and a missing translation would pass.
    await seed_source(
        pg_url, tmp_path / "other", Knowledge(transcript=("a", "b", "c")),  # type: ignore[arg-type]
        template=pg_template, room=("signal", "elsewhere"),
    )
    rows = tuple(
        Message(body=f"row-{i}", name="Nora" if i == 4 else "Miles", is_nora=i == 4,
                at_ms=BEFORE_MS - (6 - i) * STEP_MS)
        for i in range(1, 7)
    )
    source = await seed_source(
        pg_url, tmp_path / "room", Knowledge(transcript=rows),
        template=pg_template,
    )
    engine = create_sql_engine(pg_url)
    try:
        records = SqlRecordStore(engine)
        seeded = await records.recent(conversation_id=ROOM[1], limit=10)
        by_body = {r.body: r for r in seeded}
        assert by_body["row-1"].id > 3, "the offset room did not take the low ids"
        # Hers was DELIVERED in the source; `know()` writes every row PENDING.
        await records.set_delivery_outcome(
            row_id=by_body["row-4"].id, status=DeliveryStatus.SENT,
        )
        if prior:
            await SqlStandingStore(engine).write(standing=Standing(
                platform=ROOM[0], conversation_id=ROOM[1], body=_PRIOR,
                fold_count=3, updated_at=BEFORE_MS - 26 * 3_600_000,
                # The last fold read up to row-3: this turn's batch is row-4 on.
                last_folded_row_id=by_body["row-3"].id,
            ))
        turn_rows = tuple(
            r for r in (await records.recent(conversation_id=ROOM[1], limit=10))
            if r.body in ("row-5", "row-6")
        )
        await SqlTagTraceStore(engine).save(TagTrace(
            tag_id=_TAG, conversation_id=ROOM[1],
            messages=(
                {"role": "user", "content": [{"text": "row-6"}]},
                {"role": "assistant", "content": [{"toolUse": {
                    "toolUseId": "u1", "name": "web_search", "input": {"query": "venue"},
                }}]},
                {"role": "user", "content": [{"toolResult": {
                    "toolUseId": "u1", "content": [{"text": _RESULT}],
                }}]},
                {"role": "assistant", "content": [{"toolUse": {
                    "toolUseId": "u2", "name": "send_reply", "input": {"text": "on it"},
                }}]},
            ),
            transcript=turn_rows, outcome="send_reply", created_at=BEFORE_MS,
        ))
    finally:
        await engine.dispose()
    return source


async def _run(
    source: Any, *, metadata: dict[str, Any], judge: Any = None,
    before_ms: int = BEFORE_MS,
) -> tuple[Any, EvaluationData]:
    case = Case(
        name="probe/standing", input="", expected_output="the owed answer is still held",
        metadata={
            "expect": "fold", "kind": "carry", "tag_id": _TAG,
            "snapshot": {**source.snapshot, "before_ms": before_ms}, **metadata,
        },
    )
    spec = Spec(
        source_url=source.url, entry="standing", overrides=_NO_PROVIDER,
        policy=source.policy, identity=source.identity, template=source.template,
        judge=judge,
    )
    result = await _drive(case, spec)
    data = EvaluationData(
        input="", actual_output=result["output"],
        actual_environment_state=result["environment_state"], metadata={"expect": "fold"},
    )
    [out] = MeetsGrader().evaluate(data)
    return out, data


@pytest.mark.asyncio
async def test_the_rendered_fold_input_is_measurable(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """`--print-input`'s path: the fold's whole input, no model called."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True)
    out, data = await _run(source, metadata={"print_input": True})

    shown = state_of(data, NORA_SHOWN) or ""
    assert "<standing_prior written=" in shown, shown[:2000]
    assert _PRIOR in shown
    assert "<steps" in shown
    assert _FINDING in shown, "a tool result past 600 characters did not reach the fold"
    # THE STRETCH: the batch after the translated watermark plus the turn's
    # own rows — and nothing the last fold already read.
    for body in ("row-4", "row-5", "row-6"):
        assert body in shown, f"{body} is missing from the fold's batch"
    for body in ("row-1", "row-2", "row-3"):
        assert body not in shown, f"{body} was folded twice: the watermark was not translated"
    # The turn's own rows are merged into the batch BY ID; in the source's id
    # space they would not match their sandbox copies and render twice.
    for body in ("row-5", "row-6"):
        assert shown.count(f">{body}</m>") == 1, f"{body} rendered twice: trace ids untranslated"
    # Her row carries the source's delivery, not the sandbox copy's PENDING.
    assert "not delivered" not in shown
    assert "delivered" in shown

    # A dry run measured nothing, and says so.
    assert out.label == ERROR, out.reason
    assert "no model was called" in str(state_of(data, NORA_ERROR))
    assert state_of(data, NORA_CALLS) == 0


@pytest.mark.asyncio
async def test_a_standing_case_with_no_model_reports_error_not_a_verdict(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The fold's provider is unreachable and so is the judge's. The fold
    writes nothing, and nothing is exactly what a `stale` case would pass on."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True)
    out, data = await _run(
        source, metadata={},
        judge=Judge(base_url="http://127.0.0.1:9/v1", samples=1, timeout_seconds=5),
    )
    assert out.label == ERROR, f"a fold with no model graded {out.label}: {out.reason}"
    assert "wrote no position" in str(state_of(data, NORA_ERROR)), state_of(data, NORA_ERROR)


@pytest.mark.asyncio
async def test_a_turn_with_no_prior_before_it_is_refused_not_folded_against_a_blank(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """No revision before the instant — what every turn before the 0059 chain
    reads as. Refused BEFORE the fold: no model call is made, and nothing
    renders «no position yet» for a room whose position simply went unrecorded."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=False)
    out, data = await _run(source, metadata={"print_input": True})
    assert out.label == ERROR, out.reason
    assert "no standing revision stood before this turn" in str(state_of(data, NORA_ERROR))
    assert not state_of(data, NORA_SHOWN), "the fold rendered an input against a blank prior"


@pytest.mark.asyncio
async def test_an_anchor_that_is_not_the_turns_own_instant_is_refused(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The sandbox holds the room up to `before_ms`; the fold reads the turn
    that finished at `created_at`. Two different instants is two rooms."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True)
    out, data = await _run(
        source, metadata={"print_input": True}, before_ms=BEFORE_MS - STEP_MS,
    )
    assert out.label == ERROR, out.reason
    assert "not tag" in str(state_of(data, NORA_ERROR)), state_of(data, NORA_ERROR)
