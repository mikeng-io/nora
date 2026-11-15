"""Decode an untrusted management request under an authenticated actor.

The private route supplies the actor after authentication; JSON supplies only
the intended operation. Domain constructors own effect and scope invariants.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from nora.domain.management import OPERATIONS, Actor, Operation, Target


class ManagementRequestError(ValueError):
    """Malformed or incoherent input, without echoing private request values."""


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    operation_id: str
    kind: str
    target: JsonValue
    payload: JsonValue
    observed_revisions: dict[str, str]


def decode_management_request(raw: bytes, *, actor: Actor) -> Operation:
    """Interpret one JSON body using the closed domain operation vocabulary.

    A body cannot select its authenticated actor, silently add fields to a
    target/payload, or coerce strings/numbers into boolean permissions.
    This validates the request, not the actor's current permission: the service
    still revalidates authority, ownership and revisions at commitment.
    """
    try:
        request = _Request.model_validate_json(raw)
        spec = OPERATIONS.get(request.kind)
        if spec is None:
            raise ValueError("unknown operation")
        target = TypeAdapter(Target).validate_json(
            json.dumps(request.target), strict=True, extra="forbid",
        )
        payload: object = TypeAdapter(spec.payload).validate_json(
            json.dumps(request.payload), strict=True, extra="forbid",
        )
        return Operation(
            operation_id=request.operation_id,
            kind=request.kind,
            actor=actor,
            target=target,
            payload=payload,
            observed_revisions=request.observed_revisions,
        )
    except (ValueError, TypeError):
        raise ManagementRequestError("invalid management request") from None
