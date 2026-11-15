"""`evals.journey_cohort` + the journey branches of `evals.scenarios`.

The autonomous_journey corpus loads only its declared private cohort —
never `evals/corpora/` — and its experiment binds each case's declared
finite world instead of a positional recording. Pure loader tests: no DB,
no model dispatch.
"""

from __future__ import annotations

import pytest

from evals.scenarios import census, experiment_for, load_corpus

_V2_PLAN = {
    "version": 2,
    "expected_wakes": 0,
    "steps": [{"send": "the venue is Cedar Hall"}, {"until_delivery": True}],
    "expectations": [{"turn": 0, "polarity": "must", "kind": "behavior",
                      "text": "says where the outing was held",
                      "check": "Cedar Hall"}],
    "judges": ["alpha", "beta"],
}


def _cohort_file(tmp_path, cases=None, judges=None, **overrides):
    import json

    data = {"version": 1,
            "judges": ["alpha", "beta"] if judges is None else judges,
            "cases": ([{"name": "the_outing", "plan": dict(_V2_PLAN)}]
                      if cases is None else cases)}
    data.update(overrides)
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(data))
    return path


def _journey_services(*, judges=("alpha", "beta")):
    from nora.testing.journey_world import JourneyServices

    return JourneyServices(policy="[rooms]\nallow = []",
                           judge_models=judges)


def test_the_journey_corpus_loads_only_its_declared_cohort(tmp_path):
    """The live-clock corpus is not a yaml file: its cases come from the
    declared private cohort, each carrying its journey plan."""
    from evals.journey_cohort import load_cohort

    cohort = load_cohort(_cohort_file(tmp_path), corpus="autonomous_journey")
    [case] = load_corpus("autonomous_journey", cohort=cohort)
    assert case.name == "the_outing"
    assert case.metadata["journey"]["expectations"][0]["kind"] == "behavior"
    with pytest.raises(ValueError, match="declared"):
        load_corpus("autonomous_journey")
    with pytest.raises(ValueError, match="binds"):
        load_corpus("no_trigger", cohort=cohort)


def test_cohort_cases_are_active_but_never_unanchored(tmp_path):
    """A live-clock case has no `snapshot` and needs none — the live clock
    IS the anchor. `census` must not report it as work to do."""
    from evals.journey_cohort import load_cohort

    cohort = load_cohort(_cohort_file(tmp_path), corpus="autonomous_journey")
    result = census("autonomous_journey", cohort=cohort)
    assert result.total == 1
    assert result.active == 1
    assert result.unanchored == 0


def test_selected_cases_names_cohort_cases(tmp_path):
    from evals.journey_cohort import load_cohort
    from evals.scenarios import selected_cases

    cases = [{"name": "first", "plan": dict(_V2_PLAN)},
             {"name": "second", "plan": dict(_V2_PLAN)}]
    cohort = load_cohort(_cohort_file(tmp_path, cases=cases),
                         corpus="autonomous_journey")
    [only] = selected_cases(
        "autonomous_journey", only=frozenset({"second"}), cohort=cohort)
    assert only.name == "second"
    with pytest.raises(ValueError, match="no active case"):
        selected_cases("autonomous_journey",
                       only=frozenset({"not_declared"}), cohort=cohort)


@pytest.mark.parametrize("damage", [
    pytest.param({"cases": []}, id="empty-cohort"),
    pytest.param({"version": 2}, id="unversioned-cohort"),
    pytest.param({"judges": ["alpha"]}, id="one-judge"),
    pytest.param({"judges": ["alpha", "alpha"]}, id="judges-not-distinct"),
    pytest.param({"cases": [{"name": "c", "plan": dict(
        _V2_PLAN, version=1, expected_wakes=1,
        steps=[{"send": "hi"}, {"until_wakes": 1}],
        expectations=[{"wake": 0, "polarity": "must", "kind": "memo",
                       "text": "a storage kind"}])}]}, id="legacy-kind-bound"),
    pytest.param({"cases": [{"name": "c", "plan": dict(
        _V2_PLAN, expectations=[{"turn": 0, "polarity": "must",
                                 "kind": "working_memory",
                                 "text": "a storage kind"}])}]},
        id="behavior-claim-still-kind-bound"),
    pytest.param({"cases": [{"name": "c", "plan": dict(
        _V2_PLAN, judges=["other", "aliases"])}]}, id="undeclared-judges"),
    pytest.param({"cases": [{"name": "c", "plan": dict(
        _V2_PLAN, expected_wakes=1)}]}, id="denominator-unreached"),
    pytest.param({"cases": [{"plan": dict(_V2_PLAN)}]}, id="no-name"),
])
def test_a_cohort_refuses_malformed_legacy_and_undeclared_input(tmp_path, damage):
    """Missing, empty, unversioned, legacy kind-bound, malformed or
    judge-undeclared cohorts refuse before any case composes."""
    from evals.journey_cohort import load_cohort

    with pytest.raises(ValueError):
        load_cohort(_cohort_file(tmp_path, **damage),
                    corpus="autonomous_journey")


def test_experiment_for_the_journey_corpus_constructs_the_live_clock_pair(tmp_path):
    """The native pair: `Experiment` graded by `JourneyGrader`, cases drawn
    from the cohort, `Spec.services` holding each case's declared finite
    world under the live-clock entry."""
    from evals.backbone import JourneyGrader
    from evals.journey_cohort import load_cohort

    cohort = load_cohort(_cohort_file(tmp_path), corpus="autonomous_journey")
    plan = _journey_services()
    experiment, task = experiment_for(
        "autonomous_journey", source_url="postgresql://u@h/eval",
        cohort=cohort, services={"the_outing": plan})
    assert [type(g) for g in experiment.evaluators] == [JourneyGrader]
    assert [c.name for c in experiment.cases] == ["the_outing"]
    assert callable(task)


@pytest.mark.parametrize("services", [
    pytest.param({}, id="no-plan"),
    pytest.param({"other_case": "placeholder"}, id="missing-plan"),
    pytest.param({"the_outing": "a positional recording is not a world"},
                 id="wrong-kind"),
])
def test_the_journey_experiment_refuses_missing_or_wrong_services(tmp_path, services):
    from evals.journey_cohort import load_cohort

    cohort = load_cohort(_cohort_file(tmp_path), corpus="autonomous_journey")
    with pytest.raises(ValueError):
        experiment_for("autonomous_journey", source_url="postgresql://u@h/eval",
                       cohort=cohort, services=services)


def test_the_journey_experiment_refuses_undeclared_judge_aliases(tmp_path):
    from evals.journey_cohort import load_cohort

    cohort = load_cohort(_cohort_file(tmp_path), corpus="autonomous_journey")
    with pytest.raises(ValueError, match="undeclared judge"):
        experiment_for(
            "autonomous_journey", source_url="postgresql://u@h/eval",
            cohort=cohort,
            services={"the_outing": _journey_services(judges=("a", "b"))})


def test_the_journey_experiment_refuses_without_the_cohort():
    with pytest.raises(ValueError):
        experiment_for("autonomous_journey", source_url="postgresql://u@h/eval",
                       services={"the_outing": _journey_services()})


def test_the_journey_experiment_refuses_a_sampled_redraw(tmp_path):
    from evals.journey_cohort import load_cohort

    cohort = load_cohort(_cohort_file(tmp_path), corpus="autonomous_journey")
    with pytest.raises(ValueError, match="sample"):
        experiment_for("autonomous_journey", source_url="postgresql://u@h/eval",
                       sample=2, cohort=cohort,
                       services={"the_outing": _journey_services()})
