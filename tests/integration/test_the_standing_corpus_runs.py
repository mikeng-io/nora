"""The standing corpus's harness, end to end, with no model reachable.

Three readings the harness must give before a single model call is spent on it:

- **the arm is measurable** — the fold's rendered input, printed without a
  model, carries what this branch changed: `<standing_prior written=…>`,
  `<steps>`, a tool result past the 600-character clip. And it reads the
  RIGHT stretch: the sandbox renumbers every row, so the prior's watermark and
  the trace's own rows have to be translated or the fold reads a different
  batch than production's did;
- **no model is ERROR, never a verdict** — a fold that could not run wrote
  nothing, and «nothing was written» must not grade as «nothing stale was
  re-affirmed»;
- **no prior is refused, never faked** — a turn with no standing revision
  before it is exactly what an instant before the 0059 chain reads as, and
  folding it against «no position yet» would grade a fold on an input it
  never had;
- **two instants** — the room is read at production's FOLD instant (her
  `recovered` reply lands after the turn's record closes), the prior and the
  verdict chain as of the TURN.

Every case runs the ONE path (`evals.backbone._drive`): a room seeded into a
source, a moment named in it, a sandbox of the case's own.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from strands_evals import Case
from strands_evals.types.evaluation import EvaluationData

from evals.backbone import (
    ERROR,
    NORA_CALLS,
    NORA_ERROR,
    NORA_SHOWN,
    PASS,
    MeetsGrader,
    Spec,
    _close,
    _drive,
    state_of,
)
from evals.rebuilt.grading import Judge, Judgement
from evals.rebuilt.judge import POSITION_CRITERION
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.infra.sql import create_sql_engine
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.schema import TURN_VERDICT
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.subject import (
    Knowledge,
    Message,
    SubjectSpec,
    obtain,
    sandbox_database,
    sandbox_from_snapshot,
)
from nora.subject.standing_fold import FoldCall, Folded, fold, read_fold_turn
from tests.sandbox_fixtures import BEFORE_MS, ROOM, STEP_MS, seed_source

#: Every draw creates a Honcho workspace of its own.
pytestmark = pytest.mark.usefixtures("draw_honcho")

_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}
_TAG = "t-fold-probe"
_PRIOR = "I owe the room an answer about the venue; nobody has confirmed the date."
#: What production's fold wrote AFTER the turn — must never be read as the prior.
_PRODUCTION_FOLD = "PRODUCTION-FOLD-OUTPUT"
#: Placed PAST the old 600-character clip and inside the new 4000 bound, so
#: its presence in the fold's input is the branch's arm and its absence is
#: master's.
_FINDING = "FINDING-PAST-THE-OLD-CLIP"
_RESULT = ("r" * 3000) + _FINDING + ("r" * 500)
#: Her `recovered` reply: written 300 ms AFTER the turn's record closed, and
#: production's fold ran at 800 ms and read it.
_REPLY_AT = BEFORE_MS + 300
#: A revision written long after the turn — where the real chain begins.
_LATER_CHAIN = "LATER-CHAIN-POSITION"
_COUNTERFACTUAL = {"counterfactual": {"varies": "prior", "why": "predates the chain"}}
_FOLDED = BEFORE_MS + 800


async def _seed(
    pg_url: str, pg_template: str, tmp_path: Any, *, prior: bool, folded: bool = True,
    phantom_row: bool = False, later_chain: bool = False,
) -> Any:
    # ANOTHER ROOM FIRST, so the probe room's rows start at a source id the
    # sandbox will not reuse. Without the offset, source and sandbox ids agree
    # by accident and a missing translation would pass.
    await seed_source(
        pg_url, tmp_path / "other", Knowledge(transcript=("a", "b", "c")),  # type: ignore[arg-type]
        template=pg_template, room=("signal", "elsewhere"),
    )
    rows = (
        *(
            Message(body=f"row-{i}", name="Nora" if i == 4 else "Miles", is_nora=i == 4,
                    at_ms=BEFORE_MS - (6 - i) * STEP_MS)
            for i in range(1, 7)
        ),
        Message(body="row-7", name="Nora", is_nora=True, at_ms=_REPLY_AT),
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
        # Hers were DELIVERED in the source; `know()` writes every row PENDING.
        for body in ("row-4", "row-7"):
            await records.set_delivery_outcome(
                row_id=by_body[body].id, status=DeliveryStatus.SENT,
            )
        standing = SqlStandingStore(engine)
        if prior:
            await standing.write(standing=Standing(
                platform=ROOM[0], conversation_id=ROOM[1], body=_PRIOR,
                fold_count=3, updated_at=BEFORE_MS - 26 * 3_600_000,
                # The last fold read up to row-3: this turn's batch is row-4 on.
                last_folded_row_id=by_body["row-3"].id,
            ))
        if folded:
            # Production's own fold of this turn — the revision whose instant
            # the case anchors on, and which is AFTER the turn.
            await standing.write(standing=Standing(
                platform=ROOM[0], conversation_id=ROOM[1], body=_PRODUCTION_FOLD,
                fold_count=4, updated_at=_FOLDED, last_folded_row_id=by_body["row-7"].id,
            ))
        if later_chain:
            # The chain BEGINS long after the turn — the shape of a moment from
            # before the chain existed. Its first revision is today's position,
            # never the one the turn had.
            await standing.write(standing=Standing(
                platform=ROOM[0], conversation_id=ROOM[1], body=_LATER_CHAIN,
                fold_count=1, updated_at=BEFORE_MS + 16 * 86_400_000,
            ))
        async with engine.begin() as conn:
            await conn.execute(sa.insert(TURN_VERDICT), [
                # An earlier turn's, as of this turn: seeded.
                {"tag_id": "t-earlier", "conversation_id": ROOM[1], "grounded": False,
                 "unsupported": ["x"], "created_at": BEFORE_MS - 3_600_000},
                # THIS turn's, written by production's fold: not seeded.
                {"tag_id": _TAG, "conversation_id": ROOM[1], "grounded": True,
                 "unsupported": [], "created_at": _FOLDED},
            ])
        turn_rows = tuple(
            r for r in seeded if r.body in ("row-5", "row-6")
        )
        if phantom_row:
            turn_rows += (TranscriptRow(
                id=999_999, sender_id="+1", sender_name="Miles", ts=BEFORE_MS,
                body="phantom", is_nora=False,
                delivery_status=DeliveryStatus.SENT, raw_json=None,
            ),)
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
                {"role": "assistant", "content": [{"text": "on it"}]},
            ),
            transcript=turn_rows, outcome="recovered", created_at=BEFORE_MS,
        ))
    finally:
        await engine.dispose()
    return source


async def _run(
    source: Any, *, metadata: dict[str, Any], judge: Any = None,
    before_ms: int = _FOLDED,
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
    assert _PRODUCTION_FOLD not in shown, "the prior was read at the fold's instant, not the turn's"
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
    # Her `recovered` reply, written after the turn's record closed, is in the
    # room the fold read — production's fold saw it.
    assert shown.count(">row-7</m>") == 1, "her post-turn reply is missing: the room was cut early"
    # Her rows carry the source's delivery mark, not the sandbox copy's PENDING.
    for body in ("row-4", "row-7"):
        assert f'delivery="delivered">{body}</m>' in shown, f"{body}'s delivery mark"

    # A dry run measured nothing, and says so.
    assert out.label == ERROR, out.reason
    assert "no model was called" in str(state_of(data, NORA_ERROR))
    assert state_of(data, NORA_CALLS) == 0


@pytest.mark.asyncio
async def test_the_judge_reads_the_record_not_the_folds_input(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The judge is handed `Outcome.evidence` — the prior, the rows the fold
    read and every step at full size — never `Outcome.shown`, which differs
    between arms by design. A fold that DID write is graded on the judge's
    verdict, and a PASS carries its reason."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True)

    async def written(subject: Any, turn: Any, *, rows: Any, call_model: bool = True) -> Folded:
        return Folded(
            outcome="written", position="P-WRITTEN",
            calls=(FoldCall("fold", "SYS", "SHOWN-ONLY-PROMPT", "{}", "stop"),),
        )

    monkeypatch.setattr("evals.rebuilt.entry.fold", written)
    graded: dict[str, Any] = {}

    class Recording:
        async def grade_position(self, **kwargs: Any) -> Judgement:
            graded.update(kwargs)
            raw = json.dumps([{"criterion": POSITION_CRITERION, "score": 1.0,
                               "confidence": 0.9, "rationale": "held"}])
            return Judgement(criterion=POSITION_CRITERION, verdict="PASS", samples=(raw,))

    out, _data = await _run(source, metadata={}, judge=Recording())
    assert out.label == PASS, out.reason
    assert "held" in out.reason
    evidence = graded["evidence"]
    assert "SHOWN-ONLY-PROMPT" not in evidence
    for part in (_PRIOR, "row-4", "row-7", _FINDING):
        assert part in evidence, part
    assert _PRODUCTION_FOLD not in evidence
    assert graded["position"] == "P-WRITTEN"
    assert graded["kind"] == "carry"
    assert graded["prop"] == "the owed answer is still held"


@pytest.mark.asyncio
async def test_the_verdict_chain_is_seeded_as_of_the_turn(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The fold re-derives off `turn_verdict`; its arm reproduces
    production only if the sandbox holds the verdicts that stood BEFORE the
    turn — and not the one this turn's own fold wrote."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True)
    async with sandbox_database(source.url, template=source.template) as sandbox:
        subject = await obtain(SubjectSpec(
            database_url=sandbox, workdir=tmp_path / "subject", room=ROOM,
            overrides={**_NO_PROVIDER, "standing.enabled": True},
            policy=source.policy, identity=source.identity,
            now=datetime.fromtimestamp(_FOLDED / 1000, tz=UTC),
        ))
        try:
            seeded = await sandbox_from_snapshot(
                subject, source_url=source.url, before_ms=_FOLDED,
                leave_room_for_arrival=False,
            )
            turn = await read_fold_turn(subject, source_url=source.url, tag_id=_TAG)
            assert turn.folded_at == _FOLDED
            assert turn.prior is not None and turn.prior.body == _PRIOR
            assert "row-7" in [r.body for r in turn.seen]
            folded = await fold(subject, turn, rows=seeded.rows, call_model=False)
            assert folded.outcome == "malformed"
            engine = create_sql_engine(sandbox)
            try:
                async with engine.connect() as conn:
                    tags = {r.tag_id for r in (await conn.execute(
                        sa.select(TURN_VERDICT.c.tag_id))).all()}
            finally:
                await engine.dispose()
        finally:
            await _close(subject)
    assert tags == {"t-earlier"}


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
    """No revision before the turn — what every turn before the 0059 chain
    reads as. Refused BEFORE the fold: no model call is made, and nothing
    renders «no position yet» for a room whose position simply went unrecorded."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=False)
    out, data = await _run(source, metadata={"print_input": True})
    assert out.label == ERROR, out.reason
    assert "no standing revision stood before this turn" in str(state_of(data, NORA_ERROR))
    assert not state_of(data, NORA_SHOWN), "the fold rendered an input against a blank prior"


@pytest.mark.asyncio
async def test_an_anchor_at_the_turns_own_instant_is_refused(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """Anchored at `created_at`, the sandbox holds a room without her reply —
    not the room production's fold read. Only the fold's instant is accepted."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True)
    out, data = await _run(source, metadata={"print_input": True}, before_ms=BEFORE_MS)
    assert out.label == ERROR, out.reason
    assert "not the instant production folded" in str(state_of(data, NORA_ERROR))


@pytest.mark.asyncio
async def test_a_turn_production_never_folded_is_refused(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """No revision after the turn: the instant its fold read the room at is
    unrecorded, and a guessed cut is a guessed room."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True, folded=False)
    out, data = await _run(source, metadata={"print_input": True}, before_ms=BEFORE_MS)
    assert out.label == ERROR, out.reason
    assert "wrote no standing revision after" in str(state_of(data, NORA_ERROR))


@pytest.mark.asyncio
async def test_a_row_id_the_sandbox_cannot_translate_is_an_error(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """A trace row the snapshot did not seed has no sandbox id; guessing one
    folds a different stretch than production read."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True, phantom_row=True)
    out, data = await _run(source, metadata={"print_input": True})
    assert out.label == ERROR, out.reason
    assert "was not seeded with" in str(state_of(data, NORA_ERROR)), state_of(data, NORA_ERROR)
    assert not state_of(data, NORA_SHOWN)


# --- the counterfactual: a prior declared ABSENT ----------------


@pytest.mark.asyncio
async def test_an_absent_prior_renders_no_position_yet_and_reads_no_revision(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The declared counterfactual: the fold is given no prior, and no revision
    — not even the chain's first, written long after — reaches it. Every
    as-of read the path makes comes back empty; the room is cut where her
    reply landed, so the fold still reads the reply and the search."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(
        pg_url, pg_template, tmp_path, prior=False, folded=False, later_chain=True,
    )
    read: list[Any] = []
    real_read_as_of = SqlStandingStore.read_as_of

    async def spy(self: Any, **kwargs: Any) -> Any:
        found = await real_read_as_of(self, **kwargs)
        read.append(found)
        return found

    monkeypatch.setattr(SqlStandingStore, "read_as_of", spy)
    out, data = await _run(source, metadata={"print_input": True, **_COUNTERFACTUAL},
                           before_ms=_REPLY_AT)
    shown = state_of(data, NORA_SHOWN) or ""
    assert "no position yet" in shown, shown[:2000]
    assert _LATER_CHAIN not in shown
    assert all(found is None for found in read), "a revision was read on the absent-prior path"
    assert shown.count(">row-7</m>") == 1, "her delivered reply is missing from the room"
    assert _FINDING in shown
    assert out.label == ERROR and "no model was called" in str(state_of(data, NORA_ERROR))


@pytest.mark.asyncio
async def test_a_counterfactual_prior_where_the_chain_holds_one_is_refused(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The counterfactual never stands in for a real prior: the chain holds a
    revision before this turn, so declaring it absent is refused — before the
    fold renders anything."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(pg_url, pg_template, tmp_path, prior=True, folded=False)
    out, data = await _run(source, metadata={"print_input": True, **_COUNTERFACTUAL},
                           before_ms=_REPLY_AT)
    assert out.label == ERROR, out.reason
    assert "refused where a real one exists" in str(state_of(data, NORA_ERROR))
    assert not state_of(data, NORA_SHOWN)


@pytest.mark.asyncio
async def test_a_counterfactual_is_anchored_where_her_reply_landed(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """No revision dates a pre-chain fold, so the anchor is her reply's
    instant. Cut at the turn's own `created_at`, the room would lack the reply
    the property is graded against."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(
        pg_url, pg_template, tmp_path, prior=False, folded=False, later_chain=True,
    )
    out, data = await _run(source, metadata={"print_input": True, **_COUNTERFACTUAL},
                           before_ms=BEFORE_MS)
    assert out.label == ERROR, out.reason
    assert "not the instant her reply" in str(state_of(data, NORA_ERROR))


@pytest.mark.asyncio
async def test_the_counterfactuals_judge_sees_the_search_and_the_delivered_reply(
    pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """To tell SENT from UNSENT the judge needs both the search result and the
    reply the room received — which, on a `recovered` turn, lands after the
    turn's record closes — and it is told the prior was withheld by design."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await _seed(
        pg_url, pg_template, tmp_path, prior=False, folded=False, later_chain=True,
    )

    async def written(subject: Any, turn: Any, *, rows: Any, call_model: bool = True) -> Folded:
        return Folded(outcome="written", position="P-WRITTEN", calls=())

    monkeypatch.setattr("evals.rebuilt.entry.fold", written)
    graded: dict[str, Any] = {}

    class Recording:
        async def grade_position(self, **kwargs: Any) -> Judgement:
            graded.update(kwargs)
            raw = json.dumps([{"criterion": POSITION_CRITERION, "score": 1.0,
                               "confidence": 0.9, "rationale": "kept an unsent one"}])
            return Judgement(criterion=POSITION_CRITERION, verdict="PASS", samples=(raw,))

    out, _data = await _run(source, metadata=dict(_COUNTERFACTUAL), judge=Recording(),
                            before_ms=_REPLY_AT)
    assert out.label == PASS, out.reason
    evidence = graded["evidence"]
    assert _FINDING in evidence, "the search result is missing from the judge's evidence"
    assert "Nora: row-7" in evidence, "the delivered reply is missing from the judge's evidence"
    assert "COUNTERFACTUAL" in evidence
    assert _LATER_CHAIN not in evidence
