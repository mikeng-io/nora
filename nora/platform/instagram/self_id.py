"""Nora's own Instagram identity, via the bridge's `GET /health`.

**`/health`, not `/self`.** WhatsApp's bridge has a `GET /self`; this one does
not, because the alternative — the adapter carrying `GetOwnFBID()` in config —
is a value whose drift has **no symptom**. R-12
(`nora.engagement.stages.admission`) would simply stop recognising Nora's own
mentions, in every room, forever, with nothing logged. So the id comes from the
live session or it does not come at all.

Two id kinds land, exactly the `instagram` vocabulary `nora/domain/identity.py`
declares:

- **`fbid`** — `interop_messaging_user_fbid`, which is `client.GetOwnFBID()`,
  which is what `mentions[].fbid` carries. It is the PRIMARY, and it is the
  only number "was I tagged?" may compare against. `viewer.GetFBID()` is a
  different number for the same account and matches nothing.
- **`igid`** — the public Instagram id the social surface takes. It
  rides along because an `Identifier` set is how `nora.persons` and
  `PlatformIdentity` both flatten identity, and leaving it out would mean the
  social axis had a second, private answer to "who is Nora".

`username` is NOT recorded as an identity. It is a renameable handle and
therefore never a matchable kind on any platform (`nora.domain.identity`'s
`_ID_KINDS` note) — `Identifier` refuses to carry one, so there is no way to
record it by accident. It IS read, separately and as plain text, by
`read_username`: the name people type to reach her, rendered in `<presence>`.
The two functions share a response shape and nothing else, so the handle cannot drift into
`derive_identity`'s set.

## The session state is part of the measurement, not a detail

A checkpointed bridge still answers `/health` with **200** — that is the
contract's own rule, because an Instagram challenge is a normal operating
state and a non-200 would flap the container healthcheck on an ordinary
captcha. It also still serves a `self` block, because the bridge caches
`self.json` across a resumed boot.

So "200 with an fbid" is NOT evidence that Nora can act. An adapter that
resolved identity from a checkpointed bridge would register as fully healthy
and then 409 every single send. `derive_identity` therefore refuses any
session that is not `authenticated`, and names the state in the error — which
is what `nora.platform.registry` turns into a degraded registration and its
startup log line.
"""

from __future__ import annotations

import aiohttp

from ...domain.identity import Identifier
from .ids import fbid_form, igid_form

#: The one `session` value that means Nora can send, react and hear. The other
#: two the bridge declares (`checkpointed`, `logged_out`) both need a HUMAN —
#: a checkpoint is a 409 and not retryable. Matched positively rather than by
#: excluding the two known bad values: a session state this adapter has never
#: heard of is "cannot act", never "probably fine" (fail-closed).
AUTHENTICATED = "authenticated"


class InstagramIdentityError(Exception):
    """`GET /health` failed, reported a session that cannot act, or returned
    no usable fbid.

    NOT fatal by itself — `nora.platform.registry` catches this one and
    degrades the adapter rather than refusing to start, because on Instagram
    it usually means *a captcha happened overnight* and Signal and Discord
    have done nothing wrong. The message is what reaches the operator's
    startup log and Nora's `/health`, so it names the state rather than
    saying "identity error".
    """


async def derive_identity(
    base_url: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> tuple[str, frozenset[Identifier]]:
    """The bot's own (primary id, typed identifiers): `("<fbid>", {...})`.

    `primary` is the interop fbid. `registry.self_aliases()` flattens the
    values, so `MentionPolicy` and `ReplyToNoraPolicy` membership-test against
    these very strings — the parser's `_mentioned_ids` and this derivation are
    two ends of one contract, and both hold bare digits with no prefix.
    """
    async with (
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
        session.get(f"{base_url.rstrip('/')}/health") as resp,
    ):
        if resp.status != 200:
            raise InstagramIdentityError(f"GET /health returned HTTP {resp.status}")
        body = await resp.json()

    if not isinstance(body, dict):
        raise InstagramIdentityError("GET /health returned a body that is not an object")

    state = body.get("session")
    if state != AUTHENTICATED:
        detail = body.get("detail")
        suffix = f" ({detail})" if isinstance(detail, str) and detail else ""
        raise InstagramIdentityError(
            f"instagram session is {state!r}, not {AUTHENTICATED!r}{suffix} — "
            "a human has to clear it; the bridge cannot"
        )

    raw_self = body.get("self")
    self_block: dict = raw_self if isinstance(raw_self, dict) else {}
    fbid = fbid_form(self_block.get("fbid"))
    if not fbid:
        raise InstagramIdentityError("GET /health returned no usable self.fbid")

    identifiers = {Identifier("instagram", "fbid", fbid)}
    igid = igid_form(self_block.get("igid"))
    if igid:
        identifiers.add(Identifier("instagram", "igid", igid))
    return fbid, frozenset(identifiers)


async def read_username(
    base_url: str,
    *,
    timeout: float = 1.0,  # noqa: ASYNC109 -- caller budget, as derive_identity's
) -> str | None:
    """Her Instagram handle as the live session reports it, or `None`.

    DISPLAY TEXT, never an `Identifier` (the module docstring): it is what
    `<presence>` tells her she is called on Instagram, read from the same
    `GET /health` `derive_identity` reads, because the operator can rename
    the account and a copy in config would go stale in silence.

    `None` on anything short of a 200 carrying a non-empty `self.username`.
    A checkpointed session still answers with its cached `self` block, and the
    handle in it is still her handle — whether she can SEND there is
    `health()`'s question, not this one's.
    """
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(f"{base_url.rstrip('/')}/health") as resp,
        ):
            if resp.status != 200:
                return None
            body = await resp.json()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    raw_self = body.get("self")
    username = raw_self.get("username") if isinstance(raw_self, dict) else None
    if not isinstance(username, str) or not username.strip():
        return None
    return username.strip()
