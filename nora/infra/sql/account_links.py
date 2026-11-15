"""Durable pending link consent; ownership publication belongs to the service.

The request row serializes decisions from both holders. Operation keys are
serialized separately so a retry cannot become a different request or effect.
This store never transfers accounts or treats an approval as published access.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast

import sqlalchemy as sa
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.account_linking import ConsentProof, LinkState, LinkStateError, PendingLink
from nora.domain.management import LinkApproval, LinkRefusal, Operation
from nora.infra.sql.schema import MANAGED_LINK_DECISION, MANAGED_LINK_REQUEST

_LINK = TypeAdapter(PendingLink)


def _json(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key.value if isinstance(key, Enum) else key: item for key, item in value.items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, frozenset):
        return sorted(value)
    raise TypeError("unsupported link evidence value")


def _snapshot(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(json.dumps(value, default=_json, sort_keys=True)))


def _decode(value: object) -> PendingLink:
    return _LINK.validate_json(json.dumps(value), strict=True, extra="forbid")


async def _operation_lock(connection: AsyncConnection, operation_id: str) -> None:
    await connection.execute(sa.select(sa.func.pg_advisory_xact_lock(
        sa.func.hashtextextended("managed-link-operation:" + operation_id, 0),
    )))


def _evidence(operation: Operation, proof: ConsentProof, now: datetime) -> dict[str, Any]:
    # Persist authenticated provenance, never the transferable private nonce.
    return _snapshot({
        "operation": operation,
        "proof": {"account": proof.account, "issuer": proof.issuer,
                  "surface": proof.surface, "authenticated_at": proof.authenticated_at,
                  "request_id": proof.request_id,
                  "nonce_hash": hashlib.sha256(proof.nonce.encode()).hexdigest()},
        "recorded_at": now,
    })


def _effect_id(evidence: dict[str, Any]) -> str:
    # A fresh authentication for the same retry does not change its effect;
    # the first accepted proof remains the durable evidence for that decision.
    effect = {"operation": evidence["operation"],
              "proof": {key: value for key, value in evidence["proof"].items()
                        if key != "authenticated_at"}}
    return hashlib.sha256(json.dumps(effect, sort_keys=True).encode()).hexdigest()


class SqlLinkRequests:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, link: PendingLink) -> PendingLink:
        if link.state is not LinkState.PENDING or link.approved:
            raise LinkStateError("create requires a new undecided request")
        async with self._engine.begin() as connection:
            await _operation_lock(connection, link.request_id)
            decision = await connection.scalar(sa.select(MANAGED_LINK_DECISION.c.operation_id)
                .where(MANAGED_LINK_DECISION.c.operation_id == link.request_id))
            if decision is not None:
                raise LinkStateError("operation identity already names a decision")
            existing = await connection.scalar(sa.select(MANAGED_LINK_REQUEST.c.snapshot)
                .where(MANAGED_LINK_REQUEST.c.request_id == link.request_id).with_for_update())
            if existing is not None:
                recorded = _decode(existing)
                if recorded.effect_id != link.effect_id:
                    raise LinkStateError("request identity already names a different effect")
                return recorded
            await connection.execute(sa.insert(MANAGED_LINK_REQUEST).values(
                request_id=link.request_id, snapshot=_snapshot(link),
            ))
            return link

    async def get(self, request_id: str) -> PendingLink | None:
        async with self._engine.connect() as connection:
            value = await connection.scalar(sa.select(MANAGED_LINK_REQUEST.c.snapshot)
                .where(MANAGED_LINK_REQUEST.c.request_id == request_id))
            return None if value is None else _decode(value)

    async def decide(
        self, operation: Operation, proof: ConsentProof, *, now: datetime,
    ) -> PendingLink:
        payload = operation.payload
        if not isinstance(payload, LinkApproval | LinkRefusal):
            raise LinkStateError("expected a private holder decision")
        evidence = _evidence(operation, proof, now)
        effect_id = _effect_id(evidence)
        async with self._engine.begin() as connection:
            await _operation_lock(connection, operation.operation_id)
            named_request = await connection.scalar(sa.select(MANAGED_LINK_REQUEST.c.request_id)
                .where(MANAGED_LINK_REQUEST.c.request_id == operation.operation_id))
            if named_request is not None:
                raise LinkStateError("operation identity already names a request")
            previous = (await connection.execute(sa.select(MANAGED_LINK_DECISION).where(
                MANAGED_LINK_DECISION.c.operation_id == operation.operation_id,
            ))).first()
            if previous is not None and (
                previous.request_id != payload.request_id or previous.effect_id != effect_id
            ):
                raise LinkStateError("decision identity already names a different effect")
            value = await connection.scalar(sa.select(MANAGED_LINK_REQUEST.c.snapshot)
                .where(MANAGED_LINK_REQUEST.c.request_id == payload.request_id).with_for_update())
            if value is None:
                raise LinkStateError("unknown private link request")
            link = _decode(value)
            if previous is not None:
                return link
            changed = link.decide(operation, proof, now=now)
            await connection.execute(sa.insert(MANAGED_LINK_DECISION).values(
                operation_id=operation.operation_id, request_id=payload.request_id,
                effect_id=effect_id, evidence=evidence,
            ))
            await connection.execute(sa.update(MANAGED_LINK_REQUEST)
                .where(MANAGED_LINK_REQUEST.c.request_id == payload.request_id)
                .values(snapshot=_snapshot(changed)))
            return changed
