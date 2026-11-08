"""Synthetic claim contracts: citations, subjects and three separate clocks."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from nora.domain.extraction import event_time, partition_claims, validate_claims

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
    stored = validate_claims(
        {"claims": [claim()]}, {"messages": [source()], "context": []},
    )[0]
    assert stored["happened_at"]["start"] == "2030-04-03"  # source is April 2 in HKT
    assert stored["happened_at"]["reference_asserted_at_ms"] == ASSERTED
    assert stored["how"] == "planned"
    assert stored["assertions"] == [
        {"source_id": 1, "asserted_at_ms": ASSERTED, "person_id": "subject-a"}
    ]


def test_claim_cannot_cite_context_or_a_non_member():
    for bad in (2, 999):
        with pytest.raises(ValueError, match="outside batch"):
            validate_claims(
                {"claims": [claim(bad)]}, {"messages": [source()], "context": [source(2)]},
            )
    assert (
        len(
            validate_claims(
                {"claims": [claim()]},
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
        validate_claims({"claims": [raw]}, {"messages": [source()], "context": []})


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
    stored = validate_claims({"claims": [raw]}, {"messages": [source()], "context": []})[0]
    assert stored["about"][0]["person_id"] is None
    assert stored["happened_at"]["start"] is None
    assert stored["where"] is None


def test_resolved_subject_keeps_required_message_reference():
    raw = claim(body="he plans tomorrow visit Workshop")
    raw["about"] = [{"person_id": "subject-a", "reference": "he"}]
    stored = validate_claims(
        {"claims": [raw]},
        {"messages": [source(body="he plans tomorrow visit Workshop")], "context": []},
    )[0]
    assert stored["about"] == [{"person_id": "subject-a", "reference": "he"}]


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
        {"claims": [raw]}, {"messages": [source(body=body)], "context": []},
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
            {"claims": [raw]}, {"messages": [source(body=body)], "context": []},
        )


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
        {"claims": [raw]}, {"messages": [source(body=body)], "context": []},
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
        {"claims": [raw]}, {"messages": [source(body=body)], "context": []},
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
    output = {"claims": [claim(), {**claim(), **change}, claim()]}
    valid, dropped = partition_claims(output, {"messages": [source()], "context": [source(2)]})
    assert len(valid) == 2
    assert all(c["assertions"][0]["source_id"] == 1 for c in valid)
    assert dropped == [reason]


def test_partition_does_not_disguise_unparsed_output_as_a_drop():
    with pytest.raises(KeyError):
        partition_claims({"claims": [{}]}, {"messages": [source()], "context": []})


def test_drop_vocabulary_is_derived_and_runtime_checked():
    from typing import get_args

    from nora.domain.extraction import CLAIM_DROP_REASONS, ClaimDropReason, ClaimProvenanceError

    assert get_args(ClaimDropReason) == CLAIM_DROP_REASONS
    assert set(CLAIM_DROP_REASONS) == {
        "citation_outside_batch", "duplicate_citation", "evidence_not_covering_citations",
        "evidence_absent", "unknown_subject", "event_reference_outside_citations",
        "event_expression_absent", "place_absent",
    }
    with pytest.raises(ValueError, match="unknown claim drop reason"):
        ClaimProvenanceError("private-or-unbounded-text", "private text")
    assert ClaimProvenanceError("place_absent", "place absent").reason == "place_absent"
