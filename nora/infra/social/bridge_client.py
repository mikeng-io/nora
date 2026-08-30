"""`OpenCliBridgeClient` — the `social_fetch` tool's read path to X and
Instagram, over the host-side bridge (DEC-151).

Nora's container cannot run `opencli` (it exists only on the host), so this
client POSTs `{platform, what, target, limit}` to the bridge's `/read` and
maps the envelope back onto R-27's three states. The bridge re-enforces the
read-only allowlist host-side; this client adds NOTHING to that boundary and
cannot widen it — there is no parameter here that names a command.

The discipline every grounding client holds, unchanged from Tavily's:
* NEVER raises — a non-200, a non-JSON body, an envelope whose `status` is not
  one of the three states, or a transport error is a tool STATUS
  (`unavailable`), never an exception into the loop (R-5). An envelope that
  does not match the documented shape is a wiring gap — `unavailable`, NOT
  `nothing_found`, exactly as `TavilySearchProvider.fetch` treats a malformed
  `/extract` envelope.
* Retrieved text is `untrusted`-wrapped (R-13) with the source URL as
  provenance and CLAMPED (`content_max_chars`), because a tool result is
  re-sent on every subsequent model call.
* `target` was already identifier/coordinate-screened at the tool layer
  (`nora.agent.tools`) before it reached here.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from ...domain.ports import SocialProvider
from ..transit.arrivals import _wrap

log = logging.getLogger("nora.social")


class OpenCliBridgeClient(SocialProvider):
    """READ-ONLY X/Instagram reads via the host bridge's `POST /read`.
    Constructed once per process at the composition root, only when
    `[social] enabled` (there is no credential — `enabled` is the whole gate);
    unwired, `social_fetch` answers `unavailable`."""

    def __init__(
        self,
        *,
        bridge_url: str = "http://127.0.0.1:19826",
        timeout_seconds: float = 90.0,
        content_max_chars: int = 4000,
    ) -> None:
        self._bridge_url = bridge_url
        self._timeout_seconds = timeout_seconds
        self._content_max_chars = content_max_chars

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        """One read. NEVER raises. Three states (R-27): `ok` with wrapped,
        clamped `content` (and `url` when the bridge named one),
        `nothing_found` when the read ran and found nothing, `unavailable`
        for any failure of the call itself — «I could not check», never
        «nothing found»."""
        try:
            return await self._read(platform=platform, what=what, target=target, limit=limit)
        except Exception:
            # The never-raises backstop: a contract violation degrades to
            # unavailable, never propagates into the loop (R-5).
            log.exception("social bridge read failed")
            return dict(_UNAVAILABLE)

    async def _read(self, *, platform: str, what: str, target: str, limit: int) -> dict[str, Any]:
        body = {"platform": platform, "what": what, "target": target, "limit": limit}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(f"{self._bridge_url}/read", json=body) as response,
        ):
            text = await response.text()
            if response.status != 200:
                # A 400 is the bridge REFUSING the pair (its allowlist is
                # narrower than the tool's, or drift between the two) — a
                # wiring gap, not «nothing there». 5xx the same.
                log.warning("social bridge answered %s", response.status)
                return dict(_UNAVAILABLE)
        try:
            payload: Any = json.loads(text)
        except ValueError:
            log.warning("social bridge did not return JSON")
            return dict(_UNAVAILABLE)
        if not isinstance(payload, dict):
            log.warning("social bridge returned a non-object envelope")
            return dict(_UNAVAILABLE)
        status = payload.get("status")
        if status not in ("ok", "nothing_found", "unavailable"):
            # An unknown status is the envelope not matching the documented
            # shape — unavailable (R-27), never passed through to the model.
            log.warning("social bridge returned an unknown status: %r", status)
            return dict(_UNAVAILABLE)
        if status != "ok":
            return {"status": status, "note": str(payload.get("note") or "")}
        if "data" not in payload:
            log.warning("social bridge ok envelope carries no data")
            return dict(_UNAVAILABLE)
        content = json.dumps(payload["data"], ensure_ascii=False)
        clamped, truncated = _clamp_chars(content, self._content_max_chars)
        url = payload.get("source_url")
        url = str(url) if url else None
        # The content is stranger-written (R-13): wrapped `untrusted` with the
        # source as provenance. The URL rides PLAIN beside it so the model can
        # cite it in the reply — the same split as a Tavily result.
        out: dict[str, Any] = {
            "status": "ok",
            "content": _wrap(url or f"{platform} {what}", clamped),
            "truncated": truncated,
        }
        if url is not None:
            out["url"] = url
        return out


def _clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp `text` to `max_chars`, stating whether it was truncated."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


_UNAVAILABLE: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't read that just now. Say so — don't answer as if "
            "you'd checked.",
}
