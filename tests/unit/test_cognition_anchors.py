"""Generated search shapes and public-output boundary, no private clone access."""
import json

import pytest

from scripts.find_cognition_anchors import Moment, candidates, clone_url


def test_candidate_shapes_emit_only_explicit_room_alias_time_scenario_counts():
    rows = [Moment("generated-private-room", 1000, "generated-author", "k"),
            Moment("generated-private-room", 2000, "generated-author", "Dinner at 7?"),
            Moment("generated-private-room", 3000, "generated-other", "k"),
            Moment("unmapped-generated-room", 4000, "generated-author", "bought ticket")]
    found = candidates(rows, {"generated-private-room": "room_generated"})
    assert any(r["scenario"] == 2 and r["before_ms"] == 1001 for r in found)
    assert any(r["scenario"] == 3 and r["before_ms"] == 3001 for r in found)
    assert all(set(r) == {"room", "before_ms", "scenario", "counts"} for r in found)
    encoded = json.dumps(found)
    for private in ("generated-private-room", "generated-author", "Dinner", "ticket"):
        assert private not in encoded
    assert candidates(rows, {}) == []


@pytest.mark.parametrize("url", [
    "postgresql://localhost/nora", "postgresql://localhost/nora_eval?database=nora",
    "postgresql://localhost/nora_eval?host=elsewhere", "sqlite:///nora_eval",
])
def test_finder_refuses_nonclone_and_routing_overrides(url):
    with pytest.raises(ValueError):
        clone_url(url)


def test_finder_accepts_eval_clone():
    assert clone_url("postgresql://localhost/generated_eval").endswith("/generated_eval")


def test_cognition_corpus_census_and_held_out():
    from evals.scenarios import census, load_all, load_corpus

    count = census("cognition_session")
    assert (count.total, count.active, count.parked, count.unanchored) == (11, 0, 11, 0)
    cases = load_all("cognition_session")
    assert {c.metadata["scenario"] for c in cases} == {1, 2, 3, 4, 5, 6, 8, 10, 11, 12, 13}
    assert sum(c.metadata["held_out"] for c in cases) >= 3
    assert not load_corpus("cognition_session")


def test_ambient_generated_messages_do_not_emit_broad_scenario_noise():
    messages = ["hello", "how?", "why?", "nice weather", "racing is interesting"]
    found = candidates([Moment("generated-room", 1000 + i, "author", body)
                        for i, body in enumerate(messages)], {"generated-room": "room_generated"})
    assert found == []


def test_corpus_uses_existing_trajectory_grader_fail_closed():
    from strands_evals.types.evaluation import EnvironmentState, EvaluationData

    from evals.backbone import NORA_ERROR, NORA_TOOLS, ToolRouteGrader
    from evals.scenarios import load_all

    for case in load_all("cognition_session"):
        data = EvaluationData(input="generated", metadata=case.metadata,
                              actual_environment_state=[
                                  EnvironmentState(name=NORA_TOOLS, state=[]),
                                  EnvironmentState(name=NORA_ERROR, state=None),
                              ])
        assert ToolRouteGrader().evaluate(data)[0].label == "FAIL"
