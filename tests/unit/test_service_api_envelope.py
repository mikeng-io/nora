"""The HTTP status follows from the outcome, and a response is read by its body."""

from __future__ import annotations

from typing import get_args

import pytest

from nora.domain.service_api import (
    OperationOutcome,
    ReadOutcome,
    parse_response,
    status_for,
)

# The outcome tables of design/service-api/conventions.md «Outcomes».
READ_STATUS = {"ok": 200, "nothing_found": 200, "unavailable": 503}
OPERATION_STATUS = {
    "complete": 200, "pending": 202, "denied": 403, "rejected": 200,
    "unsupported": 200, "incomplete": 200, "uncertain": 200, "unavailable": 503,
}
FAMILIES = [("read", o, s) for o, s in READ_STATUS.items()] + [
    ("operation", o, s) for o, s in OPERATION_STATUS.items()
]


def test_status_is_derived_from_the_outcome():
    assert set(READ_STATUS) == set(get_args(ReadOutcome))
    assert set(OPERATION_STATUS) == set(get_args(OperationOutcome))
    assert {o: status_for(o) for o in READ_STATUS} == READ_STATUS
    assert {o: status_for(o) for o in OPERATION_STATUS} == OPERATION_STATUS


@pytest.mark.parametrize(("family", "outcome", "status"), FAMILIES)
def test_a_conforming_response_parses_to_its_outcome(family, outcome, status):
    parsed = parse_response(family, status, {"outcome": outcome, "payload": None})
    assert parsed.outcome == outcome
    assert parsed.caller_fault is None


@pytest.mark.parametrize(
    ("family", "status", "body"),
    [
        ("read", 503, {"outcome": "ok"}),
        ("read", 200, {"outcome": "unavailable"}),
        ("read", 500, {"outcome": "ok"}),
        ("operation", 200, {"outcome": "pending"}),
        ("operation", 202, {"outcome": "complete"}),
        ("operation", 200, {"outcome": "denied"}),
        ("operation", 200, {"outcome": "ok"}),
        ("read", 200, {"outcome": "complete"}),
        ("read", 200, {"payload": {}}),
        ("read", 200, None),
    ],
)
def test_a_status_that_disagrees_with_its_body_is_unavailable(family, status, body):
    assert parse_response(family, status, body).outcome == "unavailable"


@pytest.mark.parametrize("status", [301, 302, 303, 304, 307, 308])
@pytest.mark.parametrize("family", ["read", "operation"])
def test_a_redirect_response_is_read_as_unavailable(family, status):
    body = {"outcome": "ok" if family == "read" else "complete"}
    assert parse_response(family, status, body).outcome == "unavailable"


@pytest.mark.parametrize("status", [400, 401, 404, 409, 422])
@pytest.mark.parametrize("family", ["read", "operation"])
def test_the_five_no_envelope_statuses_carry_no_outcome(family, status):
    parsed = parse_response(family, status, {"outcome": "nothing_found"})
    assert parsed.outcome is None
    assert parsed.caller_fault == status
