"""Synthetic claim contracts: citations, subjects and three separate clocks."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from nora.app.extraction import SYSTEM, ExtractionOutput
from nora.domain.extraction import event_time, partition_claims, validate_claims


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


def test_system_requires_an_object_and_only_expression_may_be_null():
    from nora.app.extraction import SYSTEM

    text = " ".join(SYSTEM.split())
    assert "happened_at (ALWAYS an object {source_id, expression}, never null" in text
    assert "source_id is the new message the claim was asserted in" in text
    assert "expression is the verbatim date phrase, or null when the message has none" in text


def test_failure_stage_vocabulary_is_derived_and_checked_at_runtime():
    from typing import get_args

    from nora.app.extraction import _ExtractionError
    from nora.domain.extraction import EXTRACTION_FAILURE_STAGES, ExtractionFailureStage
    from nora.obs.metric_specs import _COUNTERS

    assert get_args(ExtractionFailureStage) == EXTRACTION_FAILURE_STAGES
    assert set(EXTRACTION_FAILURE_STAGES) == {
        "input", "request", "usage", "provider_model", "incomplete", "schema",
        "claims", "publish", "cancelled", "storage",
    }
    spec = next(s for s in _COUNTERS if s.name == "nora_extraction_failures_total")
    assert spec.labelnames == ("stage",)
    assert " / ".join(EXTRACTION_FAILURE_STAGES) in spec.help_text
    for stage in EXTRACTION_FAILURE_STAGES:
        assert _ExtractionError(stage).stage == stage
    with pytest.raises(ValueError, match="unknown extraction failure stage"):
        _ExtractionError("private-or-unbounded-text")


@pytest.mark.parametrize("null_object", [True, False])
async def test_null_happened_at_fails_at_schema_stage(example_config, caplog, null_object):
    import json
    from unittest.mock import AsyncMock
    from xml.etree import ElementTree

    import httpx
    from openai import AsyncOpenAI

    from nora.app.extraction import ExtractionWorker
    from nora.domain.extraction import VERSION
    from nora.infra.sql.extraction import SqlExtraction
    from nora.obs.metrics import Metrics
    from tests.builders import override_config

    calls = []

    def respond(request):
        # Exercise the real SDK response parsing and refuse wrong request shapes.
        assert request.method == "POST" and request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "ollamacloud/deepseek-v4.1-flash"
        assert payload["response_format"] == {"type": "json_object"}
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]
        block = ElementTree.fromstring(payload["messages"][1]["content"])  # noqa: S314
        assert block.tag == "cited_sources"
        snapshot = json.loads(block.text)
        claims = []
        for row in snapshot["messages"]:
            item = claim(row["id"], body=row["body"])
            item["happened_at"] = None if null_object else {
                "source_id": row["id"], "expression": None,
            }
            claims.append(item)
        calls.append(payload)
        return httpx.Response(200, json={
            "id": "synthetic-completion", "object": "chat.completion", "created": 1,
            "model": "deepseek-v4.1-flash",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"claims": claims}),
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        })

    job = {
        "state": "pending", "version": VERSION, "model": "ollamacloud/deepseek-v4.1-flash",
        "snapshot": {"messages": [source(body="visit Workshop")], "context": []},
    }
    store = AsyncMock(spec=SqlExtraction)
    store.take_extraction_batch.return_value = job
    store.publish_extraction_batch.return_value = 1
    metrics = Metrics()
    config = override_config(example_config.memory.extraction, {"enabled": True})
    async with AsyncOpenAI(
        api_key="synthetic-key", base_url="https://gateway.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        worker = ExtractionWorker(store=store, client=client, config=config, metrics=metrics)
        assert await worker.run_once() == int(not null_object)
    assert len(calls) == 1
    assert metrics.get("nora_extraction_calls_total") == 1
    assert metrics.get("nora_extraction_input_tokens_total") == 100
    assert metrics.get("nora_extraction_failures_total", stage="schema") == int(null_object)
    assert metrics.get("nora_extraction_failures_total") == int(null_object)
    store.record_usage.assert_awaited_once()
    if null_object:
        store.publish_extraction_batch.assert_not_awaited()
        store.fail.assert_awaited_once_with(job, stage="schema")
    else:
        store.fail.assert_not_awaited()
        stored = store.publish_extraction_batch.call_args.kwargs["claims"][0]
        assert stored["happened_at"]["expression"] is None
        assert metrics.get("nora_extraction_claims_written_total") == 1
    assert "visit Workshop" not in caplog.text


def test_resolved_subject_keeps_required_message_reference():
    from pydantic import ValidationError

    raw = claim(body="he plans tomorrow visit Workshop")
    raw["about"] = [{"person_id": "subject-a", "reference": "he"}]
    stored = validate_claims(
        parsed({"claims": [raw]}),
        {"messages": [source(body="he plans tomorrow visit Workshop")], "context": []},
    )[0]
    assert stored["about"] == [{"person_id": "subject-a", "reference": "he"}]
    text = " ".join(SYSTEM.split())
    assert "reference is ALWAYS required: the subject as written in the message" in text
    assert "whether or not person_id is filled" in text
    for reference in (None, ""):
        raw["about"][0]["reference"] = reference
        with pytest.raises(ValidationError):
            parsed({"claims": [raw]})
    del raw["about"][0]["reference"]
    with pytest.raises(ValidationError):
        parsed({"claims": [raw]})


@pytest.mark.parametrize("slot", ["evidence", "expression", "where"])
def test_xml_escaped_source_spans_match_after_decoding(slot):
    body = 'tomorrow visit Workshop & <studio> "early"'
    escaped = "Workshop &amp; &lt;studio&gt; &quot;early&quot;"
    raw = claim(body=body)
    if slot == "evidence":
        raw["evidence"][0]["quote"] = escaped
    elif slot == "expression":
        raw["happened_at"]["expression"] = escaped
    else:
        raw["where"] = escaped
    stored = validate_claims(
        parsed({"claims": [raw]}), {"messages": [source(body=body)], "context": []},
    )
    assert len(stored) == 1
    assert stored[0]["about"][0]["person_id"] == "subject-a"


@pytest.mark.parametrize("slot", ["evidence", "expression", "where"])
def test_xml_decoding_still_requires_an_exact_source_span(slot):
    body = 'tomorrow visit Workshop & <studio> "early"'
    mismatch = "Workshop &amp; &lt;studio&gt; &quot;late&quot;"
    raw = claim(body=body)
    if slot == "evidence":
        raw["evidence"][0]["quote"] = mismatch
        reason = "evidence absent from source"
    elif slot == "expression":
        raw["happened_at"]["expression"] = mismatch
        reason = "event expression absent from source"
    else:
        raw["where"] = mismatch
        reason = "place absent from source"
    with pytest.raises(ValueError, match=reason):
        validate_claims(
            parsed({"claims": [raw]}), {"messages": [source(body=body)], "context": []},
        )


def test_system_defines_empty_person_subjects_and_reply_citation_boundary():
    text = " ".join(SYSTEM.split())
    assert "about lists the persons the claim is about" in text
    assert "EMPTY list when no person is the subject (an event, place, group plan or thing)" in text
    assert "Never put the speaker in about just because they said it" in text
    assert "use it to interpret the reply, cite only the new message" in text


@pytest.mark.parametrize("slot", ["evidence", "expression", "where"])
def test_entity_shaped_source_spans_match_verbatim(slot):
    body = "tomorrow visit Workshop; Tom said &quot;hi&quot;"
    span = "&quot;hi&quot;"
    raw = claim(body="tomorrow visit Workshop")
    if slot == "evidence":
        raw["evidence"][0]["quote"] = span
    elif slot == "expression":
        raw["happened_at"]["expression"] = span
    else:
        raw["where"] = span
    stored = validate_claims(
        parsed({"claims": [raw]}), {"messages": [source(body=body)], "context": []},
    )
    assert len(stored) == 1
    assert stored[0]["evidence"] == raw["evidence"]
    assert stored[0]["happened_at"]["expression"] == raw["happened_at"]["expression"]
    assert stored[0]["where"] == raw["where"]


@pytest.mark.parametrize(
    "place",
    ['"The Loft"', "&quot;The Loft&quot;", "Boiler & Co", "Boiler &amp; Co"],
)
def test_place_source_spans_match_verbatim_or_decoded(place):
    body = 'tomorrow visit Workshop, "The Loft" and Boiler & Co'
    raw = claim(body=body)
    raw["where"] = place
    stored = validate_claims(
        parsed({"claims": [raw]}), {"messages": [source(body=body)], "context": []},
    )
    assert len(stored) == 1
    assert stored[0]["where"] == place


@pytest.mark.parametrize("change,reason", [
    ({"source_ids": [999]}, "citation_outside_batch"),
    ({"source_ids": [1, 1]}, "duplicate_citation"),
    ({"evidence": [{"source_id": 2, "quote": "tomorrow"}]}, "evidence_not_covering_citations"),
    ({"evidence": [{"source_id": 1, "quote": "invented"}]}, "evidence_absent"),
    ({"about": [{"person_id": "unknown-person", "reference": "someone"}]}, "unknown_subject"),
    ({"happened_at": {"source_id": 2, "expression": None}}, "event_reference_outside_citations"),
    ({"happened_at": {"source_id": 1, "expression": "next year"}}, "event_expression_absent"),
    ({"where": "Invented Venue"}, "place_absent"),
])
def test_partition_keeps_valid_claims_and_maps_each_provenance_check(change, reason):
    output = parsed({"claims": [claim(), {**claim(), **change}, claim()]})
    valid, dropped = partition_claims(output, {"messages": [source()], "context": [source(2)]})
    assert len(valid) == 2
    assert all(c["assertions"][0]["source_id"] == 1 for c in valid)
    assert dropped == [reason]


def test_partition_does_not_disguise_unparsed_output_as_a_drop():
    with pytest.raises(KeyError):
        partition_claims({"claims": [{}]}, {"messages": [source()], "context": []})


def test_drop_vocabulary_is_derived_declared_and_runtime_checked():
    from typing import get_args

    from nora.domain.extraction import CLAIM_DROP_REASONS, ClaimDropReason, ClaimProvenanceError
    from nora.obs.metric_specs import _COUNTERS

    assert get_args(ClaimDropReason) == CLAIM_DROP_REASONS
    assert set(CLAIM_DROP_REASONS) == {
        "citation_outside_batch", "duplicate_citation", "evidence_not_covering_citations",
        "evidence_absent", "unknown_subject", "event_reference_outside_citations",
        "event_expression_absent", "place_absent",
    }
    spec = next(s for s in _COUNTERS if s.name == "nora_extraction_dropped_claims_total")
    assert spec.labelnames == ("reason",)
    assert " / ".join(CLAIM_DROP_REASONS) in spec.help_text
    with pytest.raises(ValueError, match="unknown claim drop reason"):
        ClaimProvenanceError("private-or-unbounded-text", "private text")
    assert ClaimProvenanceError("place_absent", "place absent").reason == "place_absent"


def test_system_binds_places_times_and_persons_to_supplied_new_sources():
    text = " ".join(SYSTEM.split())
    assert (
        "where and happened_at.expression must be copied verbatim from a NEW message "
        "the claim cites"
        in text
    )
    assert "never from context rows or a reply's parent. Cite only new messages" in text
    assert (
        "person_id must come only from the supplied persons; otherwise use null with reference"
        in text
    )
