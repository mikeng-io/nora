"""Generated managed rows and revisions for whole-database snapshot checks."""

import uuid
from datetime import UTC, datetime, timedelta

_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _id(kind: str, i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"managed-snapshot:{kind}:{i}"))


ROWS = {
    "managed_person": lambda i: {
        "person_id": f"generated-person-{i}", "display_name": f"Generated Identity {i}"},
    "managed_account": lambda i: {
        "account_id": _id("account", i), "person_id": "generated-person-0",
        "platform": "signal", "label": f"Generated Account {i}"},
    "managed_identifier": lambda i: {
        "platform": "signal", "kind": "uuid", "value": _id("identifier", i),
        "account_id": _id("account", 0)},
    "managed_alias": lambda i: {
        "former_person_id": f"generated-former-{i}", "person_id": "generated-person-0"},
    "managed_membership": lambda i: {
        "person_id": "generated-person-0", "scope_kind": "unit",
        "scope_id": f"generated-unit-{i}", "state": "member", "source": "generated"},
    "managed_membership_support": lambda i: {
        "person_id": "generated-person-0", "scope_kind": "unit",
        "scope_id": f"generated-unit-{i}", "source": "generated"},
    "managed_operation": lambda i: {
        "operation_id": f"generated-operation-{i}", "kind": "profile",
        "state": "pending", "detail": f"Generated pending {i}"},
    "managed_authority_block": lambda i: {
        "person_id": f"generated-person-{i}", "scope_kind": "unit",
        "scope_id": "generated-unit", "reason": "generated",
        "operation_id": "generated-operation-0"},
    "managed_identity_hold": lambda i: {
        "person_id": "generated-person-0", "operation_id": f"generated-operation-{i}"},
    "managed_link_request": lambda i: {
        "request_id": f"generated-request-{i}", "snapshot": {"generated": i}},
    "managed_observation": lambda i: {
        "service": "generated-service", "collection": f"generated-collection-{i}",
        "scope_kind": "unit", "scope_id": "generated-unit", "observed_ms": i,
        "operation_id": "generated-operation-0", "effect": f"generated-effect-{i}"},
    "managed_scoped_name": lambda i: {
        "person_id": "generated-person-0", "scope_kind": "unit",
        "scope_id": f"generated-unit-{i}", "nickname": f"Generated Scoped {i}",
        "operation_id": "generated-operation-0"},
}

REVISE = {
    "managed_person": ("display_name", lambda i: f"Generated Renamed {i}"),
    "managed_account": ("label", lambda i: f"Generated Relabelled {i}"),
    "managed_identifier": ("created_at", lambda i: _AT + timedelta(seconds=i)),
    "managed_alias": ("created_at", lambda i: _AT + timedelta(seconds=i)),
    "managed_membership": ("state", lambda i: "left"),
    "managed_membership_support": ("created_at", lambda i: _AT + timedelta(seconds=i)),
    "managed_operation": ("detail", lambda i: f"Generated interrupted {i}"),
    "managed_authority_block": ("reason", lambda i: f"Generated blocked {i}"),
    "managed_identity_hold": ("created_at", lambda i: _AT + timedelta(seconds=i)),
    "managed_link_request": ("snapshot", lambda i: {"generated": i, "revised": True}),
    "managed_observation": ("effect", lambda i: f"generated-revised-effect-{i}"),
    "managed_scoped_name": ("nickname", lambda i: f"Generated New Scoped {i}"),
}

APPEND = {
    "managed_link_decision": lambda n: {
        "operation_id": f"generated-decision-{n}", "request_id": "generated-request-0",
        "effect_id": f"generated-effect-{n}", "evidence": {"generated": n}},
    # The first request has two sides; request4 exists before the third append.
    "managed_link_delivery": lambda n: {
        "request_id": f"generated-request-{4 if n == 3 else 0}",
        "side": "receiving" if n == 2 else "initiating", "nonce": f"generated-nonce-{n}"},
}
