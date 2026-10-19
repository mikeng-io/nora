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
