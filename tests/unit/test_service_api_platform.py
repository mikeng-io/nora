"""The committed contract document lists the `platform` wire the domain types declare."""

from __future__ import annotations

import json
from typing import get_args

from nora.domain.service_api import path_conforms
from nora.domain.service_api_platform import PlatformOperationKind, PlatformQueryKind
from scripts.service_api_contract import DOCUMENT

THE_WIRE = {
    ("post", "/v1/platform/operations"),
    ("get", "/v1/platform/operations/{operation_id}"),
    ("post", "/v1/platform/queries"),
    ("get", "/v1/platform/accounts/{account_id}"),
    ("put", "/v1/platform/blobs/{key}"),
    ("get", "/v1/platform/blobs/{key}"),
    ("get", "/v1/operations/status"),
}
OPERATION_KINDS = {"send", "react", "edit", "typing"}
QUERY_KINDS = {
    "roster", "members", "channels", "channel",
    "member_presence", "voice_presence", "groups",
}


def _document() -> dict:
    return json.loads(DOCUMENT.read_text(encoding="utf-8"))


def test_every_documented_path_conforms():
    paths = _document()["paths"]
    assert {(m, p) for p, methods in paths.items() for m in methods} == THE_WIRE
    for path in paths:
        assert path_conforms(path), path


def test_the_operation_and_query_kinds_are_closed():
    schemas = _document()["components"]["schemas"]
    documented = {
        "operation": {
            *schemas["PlatformOperationKind"]["enum"],
            *schemas["PlatformOperationRequest"]["properties"]["kind"]["enum"],
        },
        "query": {
            *schemas["PlatformQueryKind"]["enum"],
            *schemas["PlatformQuery"]["properties"]["kind"]["enum"],
        },
    }
    declared = {
        "operation": set(get_args(PlatformOperationKind)),
        "query": set(get_args(PlatformQueryKind)),
    }
    assert declared == documented, "a kind is declared but not documented: regenerate"
    assert declared == {"operation": OPERATION_KINDS, "query": QUERY_KINDS}
