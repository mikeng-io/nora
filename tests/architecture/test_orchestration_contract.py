"""Engineering policy/interchange shapes, not proof of a live dispatcher or run.

Exercise the actual documented examples; vocabularies come from the schema,
capability/evidence definitions from the root policy. Invalid cases describe
the ownership/state mistakes this contract must detect.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = (ROOT / "ORCHESTRATION.md").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / ".agents/orchestration.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
FRONTMATTER = re.match(r"\A---\n(.*?)\n---\n", DOCUMENT, re.DOTALL)
assert FRONTMATTER, "ORCHESTRATION.md needs policy YAML frontmatter"
POLICY = yaml.safe_load(FRONTMATTER.group(1))
EXAMPLE_BLOCK = re.search(r"```yaml\n(.*?)\n```", DOCUMENT, re.DOTALL)
assert EXAMPLE_BLOCK, "ORCHESTRATION.md needs synthetic interchange examples"
EXAMPLES = yaml.safe_load(EXAMPLE_BLOCK.group(1))


def test_schema_and_canonical_policy() -> None:
    Draft202012Validator.check_schema(SCHEMA)
    VALIDATOR.validate(POLICY)
    for catalogue, vocabulary in (
        ("execution_tiers", "execution_tier"),
        ("review_tiers", "review_tier"),
    ):
        assert [tier["id"] for tier in POLICY[catalogue]] == SCHEMA["$defs"][vocabulary]["enum"]


def test_documented_examples_are_valid_and_cover_every_state() -> None:
    """Positive control: actual examples exist, including a resolved worker."""
    assert EXAMPLES
    for record in EXAMPLES.values():
        VALIDATOR.validate(record)
    states = {r["status"] for r in EXAMPLES.values() if r["kind"] == "task"}
    assert states == set(SCHEMA["$defs"]["status"]["enum"])
    assert EXAMPLES["resolved_run"]["selection"]["options"]


@pytest.mark.parametrize(
    ("example", "missing"),
    [
        pytest.param("ready", "issue_ref", id="ready-without-issue"),
        pytest.param("ready", "acceptance_ref", id="ready-without-acceptance-link"),
        pytest.param("ready", "priority", id="ready-without-priority"),
        pytest.param("ready", "execution_tier", id="ready-without-execution-requirement"),
        pytest.param("ready", "review_tier", id="ready-unclassified-review"),
        pytest.param("running", "run_ref", id="running-without-real-run-link"),
        pytest.param("review", "run_ref", id="review-without-run-link"),
        pytest.param("review", "evidence_refs", id="review-without-inspectable-result"),
        pytest.param("done", "evidence_refs", id="done-without-acceptance-evidence"),
        pytest.param("blocked", "blocked_ref", id="blocked-without-unblock-record"),
        pytest.param("resolved_run", "selection", id="run-without-actual-selection"),
        pytest.param("resolved_run", "selection_reason", id="run-without-fit-justification"),
        pytest.param("resolved_run", "input_revision", id="run-without-input-revision"),
        pytest.param("resolved_run", "evidence_refs", id="run-without-evidence"),
    ],
)
def test_missing_admission_or_receipt_fields_are_rejected(example: str, missing: str) -> None:
    record = deepcopy(EXAMPLES[example])
    del record[missing]
    assert not VALIDATOR.is_valid(record)


@pytest.mark.parametrize(
    "field",
    [
        "provider",
        "model",
        "toolset",
        "options",
        "selection",
        "worker_lane",
        "spec",
        "problem",
        "acceptance",
        "body",
        "assignees",
        "milestone",
        "relationships",
        "development",
    ],
)
def test_board_rejects_worker_pins_copied_prose_and_native_fields(field: str) -> None:
    record = deepcopy(EXAMPLES["ready"])
    record[field] = EXAMPLES["resolved_run"]["selection"].get(field, "copied task body")
    assert not VALIDATOR.is_valid(record), f"board accepted {field}"


@pytest.mark.parametrize(
    ("example", "field", "value"),
    [
        pytest.param("ready", "acceptance_ref", "copied acceptance body", id="prose-as-link"),
        pytest.param("running", "run_ref", "", id="empty-run-link"),
        pytest.param("done", "evidence_refs", [], id="empty-acceptance-evidence"),
        pytest.param("done", "status", "Cancelled", id="cancellation-is-not-done"),
        pytest.param("done", "status", "Superseded", id="supersession-is-not-done"),
        pytest.param("ready", "review_tier", "", id="blank-is-not-review-none"),
        pytest.param("ready", "execution_tier", "example-model", id="model-is-not-a-tier"),
    ],
)
def test_invalid_state_metadata_is_rejected(example: str, field: str, value: object) -> None:
    record = deepcopy(EXAMPLES[example])
    record[field] = value
    assert not VALIDATOR.is_valid(record)


def test_policy_requirements_and_run_selection_have_strict_boundaries() -> None:
    pinned = deepcopy(POLICY)
    pinned["execution_tiers"][0]["model"] = "example-model"
    assert not VALIDATOR.is_valid(pinned)
    empty_evidence = deepcopy(POLICY)
    empty_evidence["execution_tiers"][0]["required_evidence"] = []
    assert not VALIDATOR.is_valid(empty_evidence)
    unknown_selection = deepcopy(EXAMPLES["resolved_run"])
    unknown_selection["selection"]["spec"] = "copied specification"
    assert not VALIDATOR.is_valid(unknown_selection)
