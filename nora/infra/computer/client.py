"""`ComputerdRuntime`: Nora's side of the computerd protocol
(`deploy/computer/computerd.py`, design/sandbox-runtime §2, §3).

It is a plain HTTP client with a bearer token. It knows nothing about sbx: the
same client works against computerd in any VM or container, which is what
keeps the runtime replaceable (the lifecycle unit changes, this does not).

**Never raises.** A refused connection, a timeout, a non-JSON body or an
unexpected status are all `SandboxStatus.UNAVAILABLE` with a reason, so a tool
maps the result to R-27 without a try/except. The computer being down is an
ordinary answer ("I couldn't reach my computer"), never a crash in a turn.

`fetch` hashes the bytes it RECEIVED. computerd sends its own digest in a
header, and the two are compared: a mismatch is `unavailable`, not a success
(design/sandbox-runtime §6: the VM's claim is never the provenance).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

from nora.domain.computer import (
    BytesResult,
    FileEntry,
    FileResult,
    ListResult,
    OpResult,
    RunResult,
    SandboxStatus,
)

log = logging.getLogger("nora.computer")

__all__ = ["ComputerdRuntime"]

# Slack on top of a command's own timeout, so the HTTP call outlives the
# command it waits for and computerd, not this client, reports the timeout.
_RUN_SLACK_SECONDS = 15.0


def _status(raw: Any) -> SandboxStatus:
    try:
        return SandboxStatus(raw)
    except ValueError:
        return SandboxStatus.UNAVAILABLE


class ComputerdRuntime:
    """Satisfies `nora.domain.ports.SandboxRuntime`."""

    def __init__(
        self, *, base_url: str, token: str, timeout_seconds: float = 30.0,
        max_output_chars: int = 8000,
    ) -> None:
        self._base = base_url.rstrip("/") + "/v1"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_seconds
        self._max_output_chars = max_output_chars

    async def run(self, *, scope: str, command: str, timeout_s: float) -> RunResult:
        body, reason = await self._post_json(
            "run", {"scope": scope, "command": command, "timeout_s": timeout_s,
                    "max_output_chars": self._max_output_chars},
            deadline_s=timeout_s + _RUN_SLACK_SECONDS,
        )
        if body is None:
            return RunResult(status=SandboxStatus.UNAVAILABLE, reason=reason)
        return RunResult(
            status=_status(body.get("status")), exit_code=body.get("exit_code"),
            stdout=str(body.get("stdout", "")), stderr=str(body.get("stderr", "")),
            truncated=bool(body.get("truncated")), duration_ms=body.get("duration_ms"),
            reason=body.get("reason"),
        )

    async def read(
        self, *, scope: str, path: str, offset: int = 0, limit: int | None = None,
    ) -> FileResult:
        payload: dict[str, Any] = {"scope": scope, "path": path, "offset": offset}
        if limit is not None:
            payload["limit"] = limit
        body, reason = await self._post_json("read", payload)
        if body is None:
            return FileResult(status=SandboxStatus.UNAVAILABLE, reason=reason)
        return FileResult(
            status=_status(body.get("status")), content=str(body.get("content", "")),
            binary=bool(body.get("binary")), size=body.get("size"),
            sha256=body.get("sha256"), truncated=bool(body.get("truncated")),
            lines_total=body.get("lines_total"), reason=body.get("reason"),
        )

    async def write_text(self, *, scope: str, path: str, content: str) -> OpResult:
        return await self._op("write", {"scope": scope, "path": path, "content": content})

    async def edit(self, *, scope: str, path: str, old: str, new: str) -> OpResult:
        return await self._op(
            "edit", {"scope": scope, "path": path, "old": old, "new": new},
        )

    async def files(self, *, scope: str, pattern: str = "**/*") -> ListResult:
        body, reason = await self._post_json("files", {"scope": scope, "pattern": pattern})
        if body is None:
            return ListResult(status=SandboxStatus.UNAVAILABLE, reason=reason)
        entries = tuple(
            FileEntry(path=str(e["path"]), size=int(e["size"]))
            for e in body.get("entries") or () if isinstance(e, dict) and "path" in e
        )
        return ListResult(
            status=_status(body.get("status")), entries=entries,
            truncated=bool(body.get("truncated")), reason=body.get("reason"),
        )

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as session,
                session.post(
                    f"{self._base}/fetch", headers=self._headers,
                    json={"scope": scope, "path": path, "max_bytes": max_bytes},
                ) as response,
            ):
                if response.content_type == "application/json":
                    body = await response.json()
                    return BytesResult(
                        status=_status(body.get("status")), reason=body.get("reason"),
                    )
                if response.status != 200:
                    return BytesResult(
                        status=SandboxStatus.UNAVAILABLE,
                        reason=f"computerd answered HTTP {response.status}",
                    )
                data = await response.content.read(max_bytes + 1)
                claimed = response.headers.get("X-Computerd-Sha256")
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            return BytesResult(status=SandboxStatus.UNAVAILABLE, reason=_unreachable(exc))
        if len(data) > max_bytes:
            return BytesResult(status=SandboxStatus.DENIED, reason="file is over the cap")
        digest = hashlib.sha256(data).hexdigest()
        if claimed != digest:
            return BytesResult(
                status=SandboxStatus.UNAVAILABLE,
                reason="the bytes received do not match the digest computerd sent",
            )
        return BytesResult(status=SandboxStatus.OK, data=data, sha256=digest)

    async def put(self, *, scope: str, path: str, data: bytes) -> OpResult:
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as session,
                session.post(
                    f"{self._base}/put", headers=self._headers,
                    params={"scope": scope, "path": path}, data=data,
                ) as response,
            ):
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            return OpResult(status=SandboxStatus.UNAVAILABLE, reason=_unreachable(exc))
        return _op_result(body)

    async def _op(self, op: str, payload: dict[str, Any]) -> OpResult:
        body, reason = await self._post_json(op, payload)
        if body is None:
            return OpResult(status=SandboxStatus.UNAVAILABLE, reason=reason)
        return _op_result(body)

    async def _post_json(
        self, op: str, payload: dict[str, Any], *, deadline_s: float | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=deadline_s or self._timeout),
                ) as session,
                session.post(
                    f"{self._base}/{op}", headers=self._headers, json=payload,
                ) as response,
            ):
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            return None, _unreachable(exc)
        if not isinstance(body, dict):
            return None, "computerd returned something that is not a JSON object"
        return body, ""


def _op_result(body: Any) -> OpResult:
    if not isinstance(body, dict):
        return OpResult(status=SandboxStatus.UNAVAILABLE, reason="unexpected reply")
    return OpResult(
        status=_status(body.get("status")), size=body.get("size"),
        sha256=body.get("sha256"), reason=body.get("reason"),
    )


def _unreachable(exc: BaseException) -> str:
    log.info("computerd unreachable: %s: %s", type(exc).__name__, exc)
    return f"the computer is unreachable ({type(exc).__name__})"
