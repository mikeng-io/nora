"""The timed probe of computerd (DEC-261).

`ComputerdRuntime` already tells its observer after every call whether computerd
answered, but a computer nobody is using says nothing, and a computer that has
been down for an hour with no turn that wanted it reads as healthy right up to
the moment somebody asks. This is the call that is made on a clock so the answer
never goes stale.

**It is authenticated on purpose.** `/v1/health` is open and proves only that
the process answers; after a token rotation that did not reach Nora's container
(the README's two-step procedure) health stays green while every real call is a
401, and that is exactly the failure worth an alert. So the probe asks computerd
something that needs the token: a listing of a scope that never exists. It is
read-only (computerd answers `nothing_found` from one `lstat` and creates
nothing, no per-room user included), the same call `deploy/computer/
nora-computer status` makes to read "holding the current token".

**Never raises.** An unreachable computerd, a non-200 or a body it cannot read
is `answered=False`; a probe that threw would be a background loop dying
silently, the failure the alert exists to catch.
"""

from __future__ import annotations

import logging

import aiohttp

from nora.domain.computer import ComputerCallObserver

log = logging.getLogger("nora.computer")

__all__ = ["PROBE_SCOPE", "ComputerdProbe"]

#: A valid scope key (16 lowercase hex) that no room maps to: a room's scope is
#: the first 32 hex of a sha256, so a run of zeros is a scope nothing creates.
PROBE_SCOPE = "0" * 16


class ComputerdProbe:
    def __init__(
        self, *, base_url: str, token: str, observer: ComputerCallObserver,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/files"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._observer = observer
        self._timeout = timeout_seconds

    async def probe_once(self) -> bool:
        """One authenticated, read-only call; reports the outcome to the
        observer and returns it."""
        answered = await self._answered()
        try:
            self._observer.probe(answered)
        except Exception:
            log.exception("computer probe observer failed")
        return answered

    async def _answered(self) -> bool:
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as session,
                session.post(
                    self._url, headers=self._headers,
                    json={"scope": PROBE_SCOPE, "pattern": "*"},
                ) as response,
            ):
                if response.status != 200:
                    log.info("computerd probe: HTTP %s", response.status)
                    return False
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            log.info("computerd probe failed: %s: %s", type(exc).__name__, exc)
            return False
        except Exception:
            log.exception("computerd probe failed unexpectedly")
            return False
        # A 200 whose body is not computerd's answer is not computerd (a
        # captive page, a proxy): an answer has a status in the port's words.
        return isinstance(body, dict) and body.get("status") in ("ok", "nothing_found")
