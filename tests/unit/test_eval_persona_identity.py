"""Acceptance checks reject plausible wrong writer output, without a model judge."""
from copy import deepcopy

import pytest
import yaml

from evals.persona_cases import case_file
from evals.rebuilt.domain import Case, Outcome
from evals.rebuilt.persona_identity import check_identity, persona_identity
from evals.scenarios import census, load_corpus


def receipt():
    h = {
        "fact_key": "birthday", "fact_value": {"month": 6, "day": 8},
        "part": "identity", "state": "current", "citations": ("101",), "as_of": 1,
        "support_count": 1, "last_supported_at": 1, "told_by": "p", "mode": "told",
        "valid_from": None, "valid_until": None,
    }
    return {"heads": [h], "held": [deepcopy(h)], "rows": [101, 102], "timestamps": [1, 2]}


def test_birthday_receipt_checks_value_assertion_support_and_actual_source():
    good = receipt()
    assert check_identity("birthday_retained", good) == ()
    for field, value in (("fact_value", {"month": 6, "day": 9}), ("as_of", 2),
                         ("support_count", 2), ("last_supported_at", 2),
                         ("citations", ("101", "102")), ("valid_until", 5)):
        wrong = deepcopy(good)
        wrong["heads"][0][field] = value
        assert check_identity("birthday_retained", wrong), field
    corrected = deepcopy(good)
    corrected["heads"][0].update(fact_value={"month": 6, "day": 9}, as_of=2, citations=("102",))
    assert check_identity("birthday_corrected", corrected) == ()


def test_negative_and_positive_controls_do_not_pass_identical_empty_output():
    empty = {"heads": [], "held": [], "rows": [1], "timestamps": [1]}
    assert check_identity("residence_unknown", empty) == ()
    assert check_identity("marriage_unknown", empty) == ()
    assert check_identity("explicit_home_spouse", empty)
    assert check_identity("partner_unspecified", empty)
    wrong = receipt()
    wrong["heads"][0].update(fact_key="residence", fact_value="Kowloon")
    assert check_identity("residence_unknown", wrong)
    wrong["heads"][0].update(fact_key="relationship", fact_value={
        "relation": "wife", "unresolved_name": "Jo"})
    assert check_identity("partner_unspecified", wrong)
    with pytest.raises(ValueError, match="unknown persona vector"):
        check_identity("typo", empty)


def test_private_loader_preserves_other_corpora(tmp_path, monkeypatch):
    monkeypatch.delenv("NORA_EVAL_PERSONA_CASES", raising=False)
    assert census("persona_identity").unanchored == 8
    ordinary = load_corpus("tool_selection")
    path = tmp_path / "private.yaml"
    path.write_text(yaml.safe_dump(case_file()))
    monkeypatch.setenv("NORA_EVAL_PERSONA_CASES", str(path))
    loaded = load_corpus("persona_identity")
    assert len(loaded) == 8 and census("persona_identity").unanchored == 0
    assert census("persona_identity").counterfactual == 8
    assert all(c.metadata["synthetic_evidence"]
               and c.metadata["private_persona_file"] for c in loaded)
    assert [(c.name, c.metadata) for c in load_corpus("tool_selection")] == [
        (c.name, c.metadata) for c in ordinary]
    path.write_text("{}")
    with pytest.raises(ValueError, match="registered"):
        load_corpus("persona_identity")


async def test_missing_private_input_and_unreadable_receipt_are_errors(monkeypatch):
    case = Case(name="residence_unknown", entry="persona_identity", expect="identity")
    assert (await persona_identity(None, case, now_ms=1)).meets("identity") is None
    assert Outcome(admitted=None).meets("identity") is None

    async def wrong_shape(*args, **kwargs):
        return {"status": "nothing_found"}

    monkeypatch.setattr("evals.rebuilt.persona_identity.run_writer", wrong_shape)
    from dataclasses import replace
    case = replace(case, metadata={"persona_input": {"sources": []}, "private_persona_file": True})
    outcome = await persona_identity(None, case, now_ms=1)
    assert outcome.error and outcome.meets("identity") is None


def test_height_age_grader_requires_both_values_source_time_and_no_birthday():
    good = receipt()
    good["heads"] = [{**good["heads"][0], "fact_key": "attribute", "fact_value": value}
                     for value in ({"name": "height", "value": 178, "unit": "cm"},
                                   {"name": "age", "value": 34, "unit": "years"})]
    assert check_identity("height_age_stated", good) == ()
    empty = {**good, "heads": []}
    assert check_identity("height_age_stated", empty)
    assert check_identity("activity_not_ownership", empty) == ()
    assert check_identity("activity_not_ownership", good)
    for field, value in (("as_of", 99), ("citations", ("102",)),
                         ("fact_value", {"name": "age", "value": 35, "unit": "years"})):
        wrong = deepcopy(good)
        wrong["heads"][1][field] = value
        assert check_identity("height_age_stated", wrong)
    wrong = deepcopy(good)
    wrong["heads"].append(receipt()["heads"][0])
    assert check_identity("height_age_stated", wrong)


@pytest.mark.parametrize(("change", "passes"), [
    ({}, True),
    ({"empty": True}, True),
    ({"fact_key": "attribute", "fact_value": {"name": "car", "value": "owned"}}, False),
    ({"fact_key": "attribute", "fact_value": {"name": "dog", "value": "owned"}}, False),
    ({"as_of": 2}, False),
    ({"citations": ("102",)}, False),
    ({"fact_value": {"relation": "spouse", "unresolved_name": "Jo"}}, False),
    ({"fact_value": {"relation": "friend", "unresolved_name": "Sam"}}, False),
    ({"mode": "inferred"}, False),
    ({"duplicate": True}, False),
    ({"valid_from": 1}, False),
    ({"valid_until": 2}, False),
])
def test_activity_grader_allows_only_source_grounded_friend(change, passes):
    observed = receipt()
    head = observed["heads"][0]
    head.update(fact_key="relationship", fact_value={"relation": "friend", "unresolved_name": "Jo"})
    change = dict(change)
    if change.pop("empty", False):
        observed["heads"] = []
    if change.pop("duplicate", False):
        observed["heads"].append(deepcopy(head))
    head.update(change)
    assert (check_identity("activity_not_ownership", observed) == ()) is passes


def test_source_lane_loader_is_additive_and_rejects_malformed_seed(tmp_path, monkeypatch):
    from nora.subject.persona_writer import validate_sources

    path = tmp_path / "sources.yaml"
    seeds = case_file(source_lanes=True)
    path.write_text(yaml.safe_dump(seeds))
    monkeypatch.setenv("NORA_EVAL_PERSONA_CASES", str(path))
    assert len(load_corpus("persona_identity")) == 11
    for bad in (
        [{"body": "caption", "ts": True}],
        [{"body": "caption", "ts": 1, "person": "somebody_else"}],
        [{"body": "caption", "ts": 1, "attachment": {"reading": "", "late": False}}],
        [{"body": "caption", "ts": 1, "attachment": {"reading": None, "late": True}}],
        [{"body": "caption", "ts": 1, "attachment": {"reading": "x", "late": False,
                                                     "source_id": "real-id"}}],
    ):
        with pytest.raises(ValueError):
            validate_sources(bad, 2)
    seeds["profile_cached"]["sources"][0]["attachment"]["wrong"] = True
    path.write_text(yaml.safe_dump(seeds))
    with pytest.raises(ValueError, match="attachment"):
        load_corpus("persona_identity")


def test_source_storage_grader_requires_qualified_value_and_exact_provenance():
    from copy import deepcopy

    good = receipt()
    good["heads"][0].update(fact_key="food_preferences", fact_value=["vegetarian ramen"],
                            mode="deduced", told_by=None, as_of=2, citations=("101", "102"))
    assert check_identity("profile_cached", good) == ()
    assert check_identity("profile_late", good) == ()
    assert check_identity("profile_missing", good)
    for field, value in (("fact_value", ["ramen"]), ("mode", "told"),
                         ("as_of", 1), ("citations", ("101",)), ("told_by", "other")):
        wrong = deepcopy(good)
        wrong["heads"][0][field] = value
        assert check_identity("profile_cached", wrong), field
    good["heads"] = []
    assert check_identity("profile_missing", good) == ()
    assert check_identity("profile_cached", good)


def test_v2_provenance_contract_keeps_endorsement_separate_from_media_only():
    good = receipt()
    good["person"] = "p"
    good["heads"][0].update(fact_key="food_preferences", fact_value=["vegetarian ramen"],
                            mode="deduced", told_by=None, as_of=2, citations=("101", "102"))
    for vector in ("profile_endorsed_late", "profile_media_only_late"):
        assert check_identity(vector, good) == ()
        for field, value in (("fact_value", ["ramen"]), ("mode", "induced"),
                             ("as_of", 1), ("citations", ("101",)),
                             ("told_by", "other"), ("valid_from", 1)):
            wrong = deepcopy(good)
            wrong["heads"][0][field] = value
            assert check_identity(vector, wrong), (vector, field)
    good["heads"][0].update(mode="told", told_by="p")
    assert check_identity("profile_endorsed_late", good) == ()
    assert check_identity("profile_media_only_late", good)
    good["heads"][0]["told_by"] = "other"
    assert check_identity("profile_endorsed_late", good)
    assert check_identity("profile_media_only_missing", good)
    good["heads"] = []
    assert check_identity("profile_media_only_missing", good) == ()
    assert check_identity("profile_media_only_late", good)


def test_v2_closed_bundle_and_missing_either_reading(tmp_path, monkeypatch):
    from nora.subject.persona_writer import validate_sources

    seeds = case_file(source_lanes_v2=True)
    path = tmp_path / "v2.yaml"
    monkeypatch.setenv("NORA_EVAL_PERSONA_CASES", str(path))
    path.write_text(yaml.safe_dump(seeds))
    assert len(load_corpus("persona_identity")) == 11
    for omitted in (0, 1):
        sources = deepcopy(seeds["profile_media_only_late"]["sources"])
        for source in sources:
            source["attachment"]["late"] = False
        sources[omitted]["attachment"]["reading"] = None
        validate_sources(sources, seeds["profile_media_only_missing"]["clock_ms"],
                         vector="profile_media_only_missing")
    for extra in ({"typo": seeds["profile_endorsed_late"]},
                  {"profile_cached": case_file(source_lanes=True)["profile_cached"]}):
        path.write_text(yaml.safe_dump({**seeds, **extra}))
        with pytest.raises(ValueError, match="registered"):
            load_corpus("persona_identity")
    del seeds["profile_endorsed_late"]
    path.write_text(yaml.safe_dump(seeds))
    with pytest.raises(ValueError, match="registered"):
        load_corpus("persona_identity")
