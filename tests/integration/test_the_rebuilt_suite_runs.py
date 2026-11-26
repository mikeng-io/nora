"""End to end: a case in, a measurement out — with no model reachable.

The reading that matters here is the NEGATIVE one. A run that reached no model
must say so FIRST and must not print a clean bar: an unreachable provider
producing `PASS` is the exact substitution this layer exists to prevent.

One case in three DOES grade without a model, and that is a property rather
than a leak: an untagged, unmentioning line is refused by ingest before any
model call, so "she did not answer" is a complete and correct reading. The
gate and the participation turn cannot say that, and both come back as ERROR.
Two different answers from one runner, each right for its own reason, is what
the fail-closed rule buys.

Every case here is driven the ONE way: a moment in a seeded source
(`tests/sandbox_fixtures.seed_source`), reconstructed into a sandbox of the
case's own by `evals.backbone._drive`, graded by `MeetsGrader`.
"""
from __future__ import annotations

from typing import Any

import pytest
from strands_evals import Case
from strands_evals.types.evaluation import EvaluationData

from evals.backbone import (
    ERROR,
    NORA_ERROR,
    NORA_TOKEN,
    PASS,
    MeetsGrader,
    Spec,
    state_of,
)
from nora.subject import Knowledge, NoteSeed
from tests.sandbox_fixtures import ROOM, embed_graph_source, offline_drive, seed_source

_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}

_HISTORY = Knowledge(
    transcript=("when is the next Raft update?", "anyway 聽日 lunch 食咩"),  # type: ignore[arg-type]
    standing="The room asked about the Raft update and nobody answered.",
    notes=(NoteSeed(
        title="Raft update", description="whether the update has a date",
        body="nobody knows the date yet", uncertainty="open",
        closing_condition="a date is named", anchor_date=20260901,
    ),),
)


def _policy(*, proactive: bool, home_room_here: bool) -> str:
    """The room's own entry, in a unit of its own, plus a SECOND unit whose
    room is the home room — the shape that ships (`policy.toml` names three
    units), and the only shape that makes `no_home_room` reachable: with no
    home room anywhere the adaptor is not built and a different guard fires."""
    here = (
        'default = "deny"\n'
        f'[[conversations]]\nplatform = "{ROOM[0]}"\nid = "{ROOM[1]}"\n'
        f'label = "{ROOM[1]}"\npersons = "*"\nunit = "nora"\n'
        + ("proactive = true\n" if proactive else "")
        + ("home_room = true\n" if home_room_here else "")
    )
    elsewhere = (
        '[[conversations]]\nplatform = "signal"\nid = "elsewhere"\n'
        'label = "elsewhere"\npersons = "*"\nunit = "somewhere-else"\n'
        'proactive = true\nhome_room = true\n'
    )
    return here + elsewhere


async def _graded(
    *, entry: str, expect: str, clean_pg_url: str, pg_template: str, tmp_path: Any,
    policy: str = "", input: str = "", metadata: dict[str, Any] | None = None,
    memory_surface: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    source = await seed_source(
        clean_pg_url, tmp_path, _HISTORY, template=pg_template, policy=policy,
    )
    case = Case(
        name=f"probe/{entry}", input=input,
        metadata={"expect": expect, "snapshot": source.snapshot, **(metadata or {})},
    )
    spec = Spec(
        source_url=source.url, entry=entry, overrides=_NO_PROVIDER,
        policy=source.policy, identity=source.identity, template=source.template,
        memory_surface=memory_surface,
    )
    result = await offline_drive(case, spec)
    data = EvaluationData(
        input=input, actual_output=result["output"],
        actual_environment_state=result["environment_state"], metadata={"expect": expect},
    )
    [out] = MeetsGrader().evaluate(data)
    return out, {
        "error": state_of(data, NORA_ERROR), "token": state_of(data, NORA_TOKEN),
    }


@pytest.mark.asyncio
async def test_a_run_with_no_model_reports_error_not_a_verdict(
    clean_pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The two that need a model: ERROR, and therefore ungradable. Not FAIL —
    the grader returns the third answer rather than scoring an un-run case.
    The one that does not: graded, and graded RIGHT — ingest refused an
    untagged line, so no turn ran, so nothing was counted as silence."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    for entry, expect in (("gate", "no_go"), ("participation", "silence")):
        out, state = await _graded(
            entry=entry, expect=expect, clean_pg_url=clean_pg_url, pg_template=pg_template,
            tmp_path=tmp_path / entry,
            input="" if entry == "gate" else "when is the next Raft update?",
        )
        assert out.label == ERROR, f"{entry} produced {out.label} with no model: {out.reason}"
        assert state["error"], entry

    out, state = await _graded(
        entry="tag", expect="silence", clean_pg_url=clean_pg_url, pg_template=pg_template,
        tmp_path=tmp_path / "tag", input="anyway what time is lunch",
    )
    assert out.label == PASS, f"an untagged line engaged her or errored: {out.reason} {state}"


@pytest.mark.asyncio
async def test_a_room_without_proactive_trust_is_an_error_not_silence(
    clean_pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """INDUCED, not hoped for. The room's policy entry drops `proactive =
    true`, which is exactly the misconfiguration an operator makes. The screen
    then refuses the row and the turn never happens, producing an empty `said`
    byte-identical to a correct silence — and this case expects silence."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    out, state = await _graded(
        entry="proactive_act", expect="silence", clean_pg_url=clean_pg_url, pg_template=pg_template,
        tmp_path=tmp_path, policy=_policy(proactive=False, home_room_here=True),
        metadata={"row": {"note": "Raft update", "intent": "ask whether a date is out"}},
    )
    assert out.label == ERROR, (
        f"a room without proactive trust graded {out.label}: {out.reason}. The "
        "screen refused the row, so nothing was measured."
    )
    # The screen's own name for it, once the row reaches the screen, or the
    # seeded-notes refusal that can precede it. Either refusal is an ERROR;
    # neither is a silence.
    reason = f"{state['token']} {state['error']}"
    assert "failed_policy" in reason or "seeded notes are" in reason, state




@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "expect", "metadata"),
    [
        ("proactive_act", "silence", {"row": {"note": "Raft update", "intent": "nudge"}}),
    ],
)
async def test_a_proactive_case_never_grades_on_a_wiring_refusal(
    entry, expect, metadata, clean_pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The two proactive paths, driven, with no provider reachable and the
    policy CORRECT. Every way these can end WITHOUT running produces an empty
    result byte-identical to a correct "nothing to do", and `none`/`silence`
    is what the cases expect. `cancelled_*` IS a verdict and may grade; the
    unreachable model is not."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    out, state = await _graded(
        entry=entry, expect=expect, clean_pg_url=clean_pg_url, pg_template=pg_template,
        tmp_path=tmp_path, policy=_policy(proactive=True, home_room_here=True),
        metadata=metadata,
    )
    if "cancelled_" in str(state["token"] or ""):
        return
    assert out.label == ERROR, (
        f"{entry} graded {out.label} on token={state['token']!r} "
        f"error={state['error']!r}. Every path that ends without a turn "
        "produces an empty result, so an ungraded ERROR is the only reading "
        "that cannot lie."
    )


@pytest.mark.asyncio
async def test_the_eval_memory_off_arm_composes_without_memory_reads(
    clean_pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """The eval-only control reaches one real draw offline; the harness's
    admitted-surface check, not a passed flag, proves memory_search/memory_read
    are absent. An untagged line needs no model, so nothing paid runs."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    out, state = await _graded(
        entry="tag", expect="silence", clean_pg_url=clean_pg_url,
        pg_template=pg_template, tmp_path=tmp_path, input="anyway what time is lunch",
        memory_surface="off",
    )
    assert out.label == PASS, f"off arm: {out.reason} {state}"


@pytest.mark.asyncio
async def test_a_graph_surface_sandbox_is_indexed_from_the_sources_vectors(
    clean_pg_url, pg_template, tmp_path, monkeypatch, capsys,
) -> None:
    """The graph arm builds each sandbox's index and adopts the source's
    vectors through the SNAPSHOT path, which renumbers every row: matched on
    the embedded input, the sandbox holds every vector it owes. A source
    holding none refuses the draw: ERROR, never a lexical run graded."""
    from nora.subject import GraphIndexRefusedError

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await seed_source(clean_pg_url, tmp_path, _HISTORY, template=pg_template)
    case = Case(name="probe/graph", input="anyway what time is lunch",
                metadata={"expect": "silence", "snapshot": source.snapshot})
    spec = Spec(
        source_url=source.url, entry="tag", overrides=_NO_PROVIDER,
        policy=source.policy, identity=source.identity, template=source.template,
        memory_surface="graph",
    )
    # `_drive` raises; `nora_task` turns it into the draw's ERROR.
    with pytest.raises(GraphIndexRefusedError, match="no graph-search vector of"):
        await offline_drive(case, spec)

    embedded = await embed_graph_source(source.url)
    assert embedded, "positive control: the source owes and holds vectors"
    capsys.readouterr()
    result = await offline_drive(case, spec)
    data = EvaluationData(input="", actual_output=result["output"],
                          actual_environment_state=result["environment_state"])
    assert state_of(data, NORA_ERROR) is None or "memory index" not in str(
        state_of(data, NORA_ERROR))
    line = capsys.readouterr().out
    assert "probe/graph: graph search index:" in line and "0 owed" in line, line
    assert "coverage 1.000" in line, line
    # Not vacuous: nothing owed and nothing held would also read 1.000.
    held = int(line.split("graph search index: ")[1].split(" with a vector")[0])
    assert held >= len(_HISTORY.transcript), line
