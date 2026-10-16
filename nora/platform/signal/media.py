"""`SignalMediaSource` — attachment bytes out of signal-cli, over the RPC it
already answers.

**Why RPC and not the filesystem.** signal-cli writes attachments into its own
store (`$XDG_DATA_HOME/signal-cli/attachments/`, bind-mounted at
`./state/signal-cli`). Reading that directory would need a read-only bind mount
into Nora's container beside the account store she is explicitly kept out of
("she has no business reading Signal identity keys" — `docker-compose.yml`), and
a trap under it: Docker creates a missing mount point ROOT-owned, and signal-cli
runs as uid 1000, so it could no longer write attachments at all.

`getAttachment` avoids the question: it is the same daemon, the same
`POST /api/v1/rpc`, the same loopback port Nora already uses for `send`,
`listGroups` and `listContacts`. No mount, no ownership, no second view of
somebody else's filesystem.

`id` alone is accepted, so no `recipient`/`groupId` is sent. A miss answers
`{"code":-1,"message":"Could not find attachment with ID: …"}`, distinct from an
unknown method (`{"code":-32601,"message":"Method not implemented"}`).

The shape of a SUCCESSFUL `getAttachment` result is not pinned. `_decode`
accepts the two shapes signal-cli plausibly returns — a bare base64 string, or
an object carrying it under `data` — and anything else becomes
`available=False, reason="bad_payload"` with the result's KEYS (never its
values) in the log line, so a new shape says exactly what to change in one line.
Accepting one shape and silently dropping the other would be a decorative
implementation.

`getSticker`'s success shape is `{"result":{"data":"<base64>"}}`, the
object-with-`data` branch `_decode` already has.

Never raises: `nora.domain.ports.MediaSource`'s contract, for `RosterSource`'s
reason. The caller is the ingest path, and an exception there costs the whole
MESSAGE — the record — to save bytes that are only ever a bonus.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

import aiohttp

from nora.domain.media import MediaBytes, MediaRef

log = logging.getLogger("nora.platform.signal.media")

# Lower than `directory`'s 10 s and matching `SignalRoster`'s reasoning: this
# runs on the ingest path, which the SSE reader awaits. A wedged daemon must
# cost seconds and an honest miss, never the message.
DEFAULT_TIMEOUT_SECONDS = 8.0

# 100 MiB — Signal's own attachment ceiling. A refusal here is a real answer
# ("too big to hold"), counted like any other custody outcome, not a crash and
# not a silent truncation.
DEFAULT_MAX_BYTES = 100 * 1024 * 1024

# The token prefix this adapter writes in `nora.platform.signal.parser`. It is
# private to Signal: the domain moves the string and never reads it.
_STICKER_PREFIX = "sticker:"


def _sticker_params(token: str) -> dict[str, object] | None:
    """`getSticker`'s params out of this adapter's own token, or None.

    `packId` is a string,
    `stickerId` an int, and passing either under another name returns a
    NullPointerException rather than a result — so the names are load-bearing
    and are written once, here.
    """
    rest = token[len(_STICKER_PREFIX):]
    pack, sep, index = rest.rpartition(":")
    if not sep or not pack or not index.isdigit():
        return None
    return {"packId": pack, "stickerId": int(index)}


class SignalMediaSource:
    """Satisfies `nora.domain.ports.MediaSource` for Signal."""

    platform = "signal"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    async def fetch(self, *, ref: MediaRef) -> MediaBytes:
        """The bytes for one attachment, or an unavailable answer saying why.

        `reason` is a small closed vocabulary because it becomes a metric
        label: `no_id`, `unreachable`, `not_found`, `bad_payload`, `too_large`,
        `bad_token`. Each names a different thing to go fix, which is the
        entire reason this is not a `bytes | None`.
        """
        if ref.fetch_token.startswith(_STICKER_PREFIX):
            params = _sticker_params(ref.fetch_token)
            if params is None:
                # This adapter wrote the token, so a token it cannot read is a
                # bug here, not a daemon failure. Distinct reason so the metric
                # says which.
                log.warning("sticker token this adapter cannot read")
                return MediaBytes(available=False, reason="bad_token")
            method, params_out = "getSticker", params
        elif not ref.platform_id:
            # An attachment signal-cli described without an id. There is
            # nothing to ask for; saying so is not a failure of the daemon and
            # must not be counted as one. (Stickers carry a token and take the branch
            # above.)
            return MediaBytes(available=False, reason="no_id")
        else:
            method, params_out = "getAttachment", {"id": ref.platform_id}

        if ref.size is not None and ref.size > self._max_bytes:
            # Refused on the platform's CLAIM, before transferring anything.
            # The claim can be wrong, which is why `_decode` checks the real
            # length too — but when it is right this saves moving 100 MiB to
            # then throw it away.
            return MediaBytes(available=False, reason="too_large")

        url = f"{self._base_url.rstrip('/')}/api/v1/rpc"
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params_out,
            "id": f"nora-{method}",
        }
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as session,
                session.post(url, json=payload) as resp,
            ):
                if resp.status != 200:
                    log.warning("%s: HTTP %s from %s", method, resp.status, url)
                    return MediaBytes(available=False, reason="unreachable")
                body = await resp.json()
        except Exception as exc:                                   # noqa: BLE001
            # Blind, on purpose and narrowly scoped: every transport failure is
            # the same answer to this caller, and the port forbids raising.
            log.warning("%s unreachable at %s: %s", method, url, type(exc).__name__)
            return MediaBytes(available=False, reason="unreachable")

        if not isinstance(body, dict):
            return MediaBytes(available=False, reason="bad_payload")
        if body.get("error"):
            # The miss shape: `{"code": -1, "message": "Could not find
            # attachment/sticker with ID: …"}`. The handle is signal-cli's own,
            # not a person's identifier, but the message is echoed at debug
            # rather than warning because a miss is already counted and the
            # log line adds nothing an operator alerts on. Never the token —
            # only the platform id, or a fixed placeholder for a sticker.
            log.info(
                "%s: signal-cli did not produce %s",
                method, ref.platform_id or "<sticker>",
            )
            return MediaBytes(available=False, reason="not_found")
        return self._decode(body.get("result"))

    def _decode(self, result: Any) -> MediaBytes:
        """Base64 out of whichever shape the daemon used. See module docstring
        for why two shapes are accepted and why an unknown one is loud."""
        encoded: Any = None
        if isinstance(result, str):
            encoded = result
        elif isinstance(result, dict):
            for key in ("data", "base64", "attachment"):
                if isinstance(result.get(key), str):
                    encoded = result[key]
                    break
        if not isinstance(encoded, str) or not encoded:
            log.warning(
                "getAttachment returned a shape this adapter does not read: %s "
                "(keys, never values) — see nora/platform/signal/media.py",
                sorted(result) if isinstance(result, dict) else type(result).__name__,
            )
            return MediaBytes(available=False, reason="bad_payload")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            log.warning("getAttachment returned %d chars that are not base64", len(encoded))
            return MediaBytes(available=False, reason="bad_payload")
        if len(data) > self._max_bytes:
            return MediaBytes(available=False, reason="too_large")
        return MediaBytes(available=True, data=data)
