"""Narrow host lifecycle client; only elected execution acquires a VM."""
from __future__ import annotations

import logging

import aiohttp

from nora.memory import ActAuthority, EffectRecorder, Outcome

log = logging.getLogger("nora.computer.lifecycle")


class HostLifecycleClient:
    def __init__(
        self, *, socket: str, token: str, timeout_seconds: float = 240,
        effects: EffectRecorder | None = None,
    ) -> None:
        self._socket = socket
        self._token = token
        self._timeout = timeout_seconds
        self._effects = effects

    async def request(self, action: str, scope: str, operation: str) -> dict[str, object]:
        if self._effects is None or action == "observe":
            return await self._request(action, scope, operation)
        return await self._effects.invoke(
            "computer.lifecycle", f"computerd-lifecycle:{scope}", f"{operation}:{action}",
            ActAuthority(None), lambda: self._request(action, scope, operation),
            judge=lambda value: Outcome("done" if value.get("status") == "ok" else "uncertain"),
        )

    async def _request(self, action: str, scope: str, operation: str) -> dict[str, object]:
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=self._socket),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as session, session.post(
                "http://localhost/lifecycle",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"action": action, "scope": scope, "operation": operation},
            ) as response:
                body = await response.json()
                if response.status == 200 and isinstance(body, dict):
                    return body
                return {"status": "unavailable", "reason": "controller refused request"}
        except (OSError, ValueError, aiohttp.ClientError, TimeoutError):
            log.warning("computer lifecycle controller unavailable", exc_info=True)
            return {"status": "unavailable", "reason": "lifecycle controller unreachable"}
