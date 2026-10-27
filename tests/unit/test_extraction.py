"""Synthetic claim contracts: citations, subjects and three separate clocks."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from nora.app.extraction import ExtractionOutput
from nora.domain.extraction import event_time, validate_claims


def parsed(payload):
    return ExtractionOutput.model_validate(payload).model_dump()


ASSERTED = int(datetime(2030, 4, 1, 16, 30, tzinfo=UTC).timestamp() * 1000)


def source(row_id=1, body="tomorrow visit Workshop"):
    return {
        "id": row_id,
        "person_id": "subject-a",
        "ts": ASSERTED,
        "body": body,
        "mentions": [],
        "revision": None,
        "is_nora": False,
    }


def claim(row_id=1, *, body="tomorrow visit Workshop"):
    return {
        "about": [{"person_id": "subject-a", "reference": "the speaker"}],
        "what": "The speaker plans a visit",
        "how": "planned",
        "where": "Workshop",
        "happened_at": {"source_id": row_id, "expression": "tomorrow"},
        "source_ids": [row_id],
        "evidence": [{"source_id": row_id, "quote": body}],
    }


def test_claim_is_bound_to_its_own_sources_and_assertion_time():
    output = parsed({"claims": [claim()]})
    stored = validate_claims(output, {"messages": [source()], "context": []})[0]
    assert stored["happened_at"]["start"] == "2030-04-03"  # source is April 2 in HKT
    assert stored["happened_at"]["reference_asserted_at_ms"] == ASSERTED
    assert stored["how"] == "planned"
    assert stored["assertions"] == [
        {"source_id": 1, "asserted_at_ms": ASSERTED, "person_id": "subject-a"}
    ]


def test_claim_cannot_cite_context_or_a_non_member():
    for bad in (2, 999):
        output = parsed({"claims": [claim(bad)]})
        with pytest.raises(ValueError, match="outside batch"):
            validate_claims(output, {"messages": [source()], "context": [source(2)]})
    assert (
        len(
            validate_claims(
                parsed({"claims": [claim()]}),
                {"messages": [source()], "context": []},
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"evidence": [{"source_id": 1, "quote": "invented evidence"}]}, "absent from source"),
        ({"evidence": [{"source_id": 2, "quote": "tomorrow"}]}, "cover citations"),
        ({"where": "Imagined Hall"}, "place absent"),
        ({"about": [{"person_id": "invented-person", "reference": "someone"}]}, "unknown resolved"),
        ({"happened_at": {"source_id": 1, "expression": "2030-04-09"}}, "expression absent"),
    ],
)
def test_unsupported_slots_are_refused(change, reason):
    raw = {**claim(), **change}
    with pytest.raises(ValueError, match=reason):
        validate_claims(parsed({"claims": [raw]}), {"messages": [source()], "context": []})


@pytest.mark.parametrize("expression", [None, "soon", "next festival", "2030-02-30"])
def test_unknown_event_time_stays_unknown(expression):
    resolved = event_time(expression, ASSERTED)
    assert resolved == {"start": None, "end": None, "expression": expression, "status": "unknown"}
    assert event_time("2030-04-01/2030-04-03", ASSERTED)["end"] == "2030-04-03"


def test_unknown_reference_and_time_are_explicit_not_invented():
    raw = copy.deepcopy(claim())
    raw["about"] = [{"person_id": None, "reference": "an unresolved colleague"}]
    raw["happened_at"]["expression"] = None
    raw["where"] = None
    stored = validate_claims(parsed({"claims": [raw]}), {"messages": [source()], "context": []})[0]
    assert stored["about"][0]["person_id"] is None
    assert stored["happened_at"]["start"] is None
    assert stored["where"] is None


@pytest.mark.parametrize("model", ["other-provider/other-model", "ollamacloud/deepseek-v4-pro", ""])
def test_enabled_extraction_refuses_an_unapproved_route(example_config_path, monkeypatch, model):
    import tomlkit

    from nora.config import ConfigError, load

    monkeypatch.setenv("OMNIROUTE_API_KEY", "synthetic-key")
    doc = tomlkit.parse(example_config_path.read_text())
    doc["memory"]["extraction"]["enabled"] = True
    doc["memory"]["extraction"]["model"] = model
    example_config_path.write_text(tomlkit.dumps(doc))
    with pytest.raises(ConfigError, match=r"DeepSeek V4\.1 Flash"):
        load(example_config_path)
