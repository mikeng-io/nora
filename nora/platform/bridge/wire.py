"""What the bridges and the app share beside the `platform` contract
(`nora.domain.service_api_platform`; design/platform-bridges/02-wire-and-platforms.md).

The contract's routes, envelopes and outcomes live in the domain and, for the
Python bridges, `nora.platform.bridge.contract`. Here: the blob key grammar
every custody route enforces, and the durable action owner.
"""

from __future__ import annotations

import re

from nora.domain.actions import OWNER_KINDS, ActionOwner

# `ActionOwner`/`OWNER_KINDS` are re-exported: the type is domain vocabulary
# (nora.domain.actions) so layers below platform may share it.
__all__ = ["OWNER_KINDS", "ActionOwner", "attachment_key_ok"]

#: The `{key}` grammar both blob verbs enforce — the SAME charset the
#: whatsapp bridge's `KEY_RE` and the instagram bridge's `custodyKeyRE`
#: implement natively. Inbound keys are sha256 hex; app-minted outbound keys
#: embed the operation binding (`out-<operation_id>-<i>`: colons and hyphens),
#: so an alnum-only rule refuses every key the client can mint. `/` is
#: excluded by the charset and `..` by the check below, so no key can name a
#: path outside the custody dir.
ATTACHMENT_KEY_RE = re.compile(r"[A-Za-z0-9._~:-]{1,128}\Z")


def attachment_key_ok(key: str) -> bool:
    return bool(key) and bool(ATTACHMENT_KEY_RE.fullmatch(key)) \
        and ".." not in key
