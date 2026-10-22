"""Nora's own WhatsApp identity, via the bridge's `GET /self`.

One call, hard-fail — Discord's shape (`nora.platform.discord.self_id`),
not Signal's. Signal's multi-call best-effort derivation with a metrics
parameter exists for R-17's degradation story, which is specific to
signal-cli answering a different question per id form; the bridge answers
`{phone, lid}` in one body or not at all (503 `{"error": "not_connected"}`
until the socket is open), so there is nothing to degrade around — a failed
derivation is a startup error, not a counted partial answer.

The two id forms are exactly the `whatsapp` vocabulary
`nora/domain/identity.py:54` already declares: `phone` in the "+<digits>"
form the parser's `sender_id`/mention values take, and `lid` as bare digits.
`registry.self_aliases()` flattens the values, so `MentionPolicy` and
`ReplyToNoraPolicy` membership-test against these very strings — the
parser's normalization and this derivation are two ends of one contract.
"""

from __future__ import annotations

import aiohttp

from ...domain.identity import Identifier
from ...infra.service_auth import service_auth_headers
from .jid import lid_form, phone_form


class WhatsAppIdentityError(Exception):
    """`GET /self` failed (a non-200 — 503 while the socket is down — or an
    unreachable bridge) or returned a body with no usable phone."""


async def derive_identity(
    base_url: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> tuple[str, frozenset[Identifier]]:
    """The bot's own (primary id, typed identifiers): `("+<phone>", {...})`.

    `primary` is the phone form; the `lid` identifier rides alongside when
    the bridge reports one, because Baileys surfaces many group senders —
    including, potentially, the bot itself — as LIDs, and a self-alias set
    without it would miss a reply-to-Nora addressed through the LID form.
    """
    async with (
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
        session.get(
            f"{base_url.rstrip('/')}/self", headers=service_auth_headers(),
        ) as resp,
    ):
        if resp.status != 200:
            raise WhatsAppIdentityError(f"GET /self returned HTTP {resp.status}")
        body = await resp.json()

    phone = body.get("phone") if isinstance(body, dict) else None
    if not isinstance(phone, str) or not phone:
        raise WhatsAppIdentityError("GET /self returned no usable phone")

    identifiers = {Identifier("whatsapp", "phone", phone_form(phone))}
    lid = body.get("lid")
    if isinstance(lid, str) and lid:
        identifiers.add(Identifier("whatsapp", "lid", lid_form(lid)))
    return phone_form(phone), frozenset(identifiers)
