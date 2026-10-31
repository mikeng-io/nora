"""Bridge-side action authority — the closed typed owner check EVERY bridge
runtime runs under the same transaction as the reservation CAS, before any
upstream effect.

Conforms to the JS (`deploy/whatsapp-bridge/actions.js`) and Go
(`deploy/instagram-bridge/internal/bridge/actionstore.go`) contracts: an owner
kind outside the closed set is a parse error, a well-formed kind whose
authority reference does not resolve is a deny (4xx-class), and the check
itself binds platform + account + target — never the request_hash alone.

- `work`: a real `bridge_work` row for this platform/account whose claim is
  CURRENT — `state='running'` with an unexpired `leased_until` on the DB
  clock — conversation-scoped to the action's target, generation REQUIRED
  and equal to the row's. `pending`/`retryable_failure` are live-but-
  unclaimed: a lease-dead or re-parked work must not keep signing actions.
- `event_receipt`: bridge-initiated only — the owner names an event the
  producer's own journal holds WITH a frozen receipt intent (matched by
  canonical event id or `source_digest`, both rotation-stable). An HTTP
  caller cannot mint receipt authority for an event the bridge never
  captured or never intended to acknowledge. A `bridge_event` row that
  exists and is no longer `open` is a later-phase revocation → denied.
- `otp_challenge`: the `otp_challenge` row exists for this platform and has
  NOT expired, and the committed pending outbound audit row exists —
  `messages` with `raw_json->>'tag_id' = 'otp-{challenge_id}'`, `is_nora`,
  `delivery_status='pending'`, same platform, same conversation as the
  action target. Its id becomes `bridge_action.message_id`.
- `live_invocation`: the owner names the committed pending outbound audit
  row directly (a `messages.id`): `is_nora`, still `pending`, same
  platform + conversation as the target. The unbound `{"id": "app"}`
  fallback resolves to no row → denied, fail-closed.

`stored_request` is the OTHER half of the same authority: what the ledger
persists. For `otp_challenge` the outbound text carries a live one-time
code — a credential — so the JSONB keeps the redacted form while
`request_hash` is still computed over the FULL request by the caller: two
requests that differ only in the code hash differently and stay fenced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from nora.domain.actions import ActionOwner
from nora.infra.sql.schema import (
    BRIDGE_EVENT,
    BRIDGE_EVENT_ALIAS,
    BRIDGE_WORK,
    MESSAGES,
    OTP_CHALLENGE,
)

#: `bridge_work` states in which a work owner may still ACT — the JS
#: contract (`store.js`: `wrow.state === 'running'`). A claimed row is
#: `running`; `pending`/`retryable_failure` are claimable-again states, not
#: acting ones — the claim that moves a row through them bumps
#: `generation`, and only the generation holder may act anyway.
LIVE_WORK_STATES = frozenset({"running"})

OTP_REDACTED = "[redacted]"


@dataclass(frozen=True)
class OwnerVerdict:
    """The authority answer: `ok` plus the pending outbound audit row the
    action links (`bridge_action.message_id`), or `reason` when denied."""
    ok: bool
    reason: str = ""
    message_id: int | None = None


def stored_request(owner: ActionOwner, request: dict[str, Any]) -> dict[str, Any]:
    """What `bridge_action.request` persists — the FULL request stays in
    flight (the upstream body) and in `request_hash` (idempotence); the
    JSONB is the safe form. Only `otp_challenge` redacts: its `text` carries
    a live code, which is a credential that must never rest."""
    if owner.kind == "otp_challenge" and "text" in request:
        return {**request, "text": OTP_REDACTED}
    return request


async def _pending_outbound(
    conn: Any, *, platform: str, conversation_id: str,
    tag_id: str | None = None, row_id: int | None = None,
) -> int | None:
    """The committed pending outbound audit row this action confirms — by
    tag (otp/work linkage) or by direct id (live_invocation). Scoped to the
    same platform + conversation the action targets."""
    pred = sa.and_(
        MESSAGES.c.is_nora == 1,
        MESSAGES.c.delivery_status == "pending",
        MESSAGES.c.platform == platform,
        MESSAGES.c.conversation_id == conversation_id)
    if tag_id is not None:
        pred &= sa.text("raw_json::jsonb ->> 'tag_id' = :tag")
    if row_id is not None:
        pred &= MESSAGES.c.id == row_id
    row = (await conn.execute(
        sa.select(MESSAGES.c.id).where(pred),
        {"tag": tag_id} if tag_id is not None else {})).one_or_none()
    return int(row[0]) if row is not None else None


async def validate_owner(
    conn: Any, *, platform: str, account_key: str, owner: ActionOwner,
    target_id: str, receipt_intents: frozenset[str] = frozenset(),
) -> OwnerVerdict:
    """Closed typed authority for `POST /v1/actions`, under the reservation's
    own transaction. `receipt_intents` is the bridge's journal-derived set of
    ids (canonical event ids AND source digests) whose envelopes carry a
    frozen receipt — the producer evidence an `event_receipt` owner must
    name. A denied owner answers `ok=False`, never an exception — store
    failures still raise so the route can answer unavailable."""
    oid = owner.id
    if owner.kind == "work":
        if not (oid.startswith("w-") and oid[2:].isdigit()):
            return OwnerVerdict(False, "malformed work owner id")
        if owner.generation is None:
            return OwnerVerdict(False, "work owner must declare a generation")
        row = (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == int(oid[2:])))).mappings().one_or_none()
        if row is None:
            return OwnerVerdict(False, "unknown work owner")
        if row["platform"] != platform or row["account_key"] != account_key:
            return OwnerVerdict(False, "owner platform/account mismatch")
        if row["conversation_id"] != target_id:
            return OwnerVerdict(False, "owner conversation mismatch")
        if row["state"] not in LIVE_WORK_STATES:
            return OwnerVerdict(False, "owner work is not running")
        if int(owner.generation) != row["generation"]:
            return OwnerVerdict(False, "stale owner generation")
        # The claim fence's second half: `generation` says WHICH claim, the
        # DB-clock lease says it is still ALIVE. A lease that lapsed without
        # `expire_leases` having swept yet is still a dead owner — and a
        # re-parked/re-claimed row fails the generation or state check above
        # either way.
        lease_live = (await conn.execute(sa.select(
            BRIDGE_WORK.c.id).where(
            BRIDGE_WORK.c.id == row["id"],
            BRIDGE_WORK.c.leased_until > sa.func.now()))).scalar_one_or_none()
        if lease_live is None:
            return OwnerVerdict(False, "owner lease expired")
        tag = (row["payload"] or {}).get("tag_id")
        message_id = (await _pending_outbound(
            conn, platform=platform, conversation_id=target_id, tag_id=tag)
            if isinstance(tag, str) and tag else None)
        return OwnerVerdict(True, message_id=message_id)

    if owner.kind == "event_receipt":
        if oid not in receipt_intents:
            # Not in this bridge's journal with a frozen receipt intent — an
            # HTTP caller cannot mint receipt authority from nothing.
            return OwnerVerdict(False, "no journaled receipt intent for owner")
        # Later-phase revocation: an event row that already closed cannot
        # collect another receipt (IG's client-facing gate).
        ev = (await conn.execute(sa.select(BRIDGE_EVENT.c.state).select_from(
            BRIDGE_EVENT.outerjoin(
                BRIDGE_EVENT_ALIAS,
                BRIDGE_EVENT_ALIAS.c.event_ref == BRIDGE_EVENT.c.id))
            .where(sa.or_(
                BRIDGE_EVENT.c.source_digest == oid,
                BRIDGE_EVENT.c.id == oid,
                BRIDGE_EVENT_ALIAS.c.event_id == oid),
                BRIDGE_EVENT.c.platform == platform,
                BRIDGE_EVENT.c.account_key == account_key))
        ).mappings().one_or_none()
        if ev is not None and ev["state"] != "open":
            return OwnerVerdict(False, "receipt event is terminal")
        return OwnerVerdict(True)

    if owner.kind == "otp_challenge":
        try:
            challenge = uuid.UUID(oid)
        except (ValueError, AttributeError, TypeError):
            return OwnerVerdict(False, "malformed otp challenge owner id")
        row = (await conn.execute(sa.select(OTP_CHALLENGE).where(
            OTP_CHALLENGE.c.challenge_id == challenge,
            OTP_CHALLENGE.c.expires_at > sa.func.now()))).mappings().one_or_none()
        if row is None or row["platform"] != platform:
            return OwnerVerdict(False, "unknown or expired otp challenge owner")
        message_id = await _pending_outbound(
            conn, platform=platform, conversation_id=target_id,
            tag_id=f"otp-{oid}")
        if message_id is None:
            return OwnerVerdict(
                False, "no pending outbound row for otp challenge")
        return OwnerVerdict(True, message_id=message_id)

    if owner.kind == "live_invocation":
        if not oid.isdigit():
            return OwnerVerdict(False, "malformed live invocation owner id")
        message_id = await _pending_outbound(
            conn, platform=platform, conversation_id=target_id,
            row_id=int(oid))
        if message_id is None:
            return OwnerVerdict(
                False, "no pending outbound row for live invocation")
        return OwnerVerdict(True, message_id=message_id)

    return OwnerVerdict(False, f"unknown owner kind {owner.kind!r}")
