"""`ComputerdRuntime`: Nora's side of the computerd protocol
(`deploy/computer/computerd.py`).

It is a plain HTTP client with a bearer token. It knows nothing about sbx: the
same client works against computerd in any VM or container, which is what
keeps the runtime replaceable (the lifecycle unit changes, this does not).

**Never raises.** Every public method is a thin guard around its body: a
refused connection, a timeout, a malformed body or any unexpected exception
becomes `SandboxStatus.UNAVAILABLE` with a reason. So a tool maps the result to
R-27 without a try/except, and the computer being down is an ordinary answer
("I couldn't reach my computer"), never a crash in a turn. **Any non-200 reply
is `unavailable`**: a bad token (401) is a configuration fault, not a refusal
the model should read as "refused as asked".

Transfers are checked end to end. `fetch` reads until EOF under a byte cap and
hashes what it RECEIVED, which must equal the digest computerd sent. `put`
and `write_text` compare the digest computerd reports against the bytes they
sent. A mismatch is `unavailable`, never a success.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

import aiohttp

from nora.domain.computer import (
    BytesResult,
    ComputerCallObserver,
    FileEntry,
    FileResult,
    ListResult,
    OpResult,
    RunResult,
    SandboxStatus,
    ScopeProcess,
    StateFile,
    StateResult,
    TmuxSession,
)

log = logging.getLogger("nora.computer")

__all__ = ["ComputerdRuntime"]

# Slack on top of a command's own timeout, so the HTTP call outlives the
# command it waits for and computerd, not this client, reports the timeout.
_RUN_SLACK_SECONDS = 15.0

R = TypeVar("R")


class _UnavailableError(Exception):
    """Internal: a reply that cannot be trusted as an answer."""


class _NoAnswerError(_UnavailableError):
    """Internal: computerd gave no answer at all: an HTTP status other than 200
    (a 401 after a token rotation, a 5xx, a 404 from something that is not
    computerd). Distinct from `_UnavailableError`, which includes a reply that
    DID arrive and could not be trusted (a digest mismatch): that one is
    computerd answering badly, this is computerd not answering."""


def _status(raw: Any) -> SandboxStatus:
    try:
        return SandboxStatus(raw)
    except ValueError:
        return SandboxStatus.UNAVAILABLE


def _text(raw: Any) -> str:
    return raw if isinstance(raw, str) else ""


def _opt_int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _opt_str(raw: Any) -> str | None:
    return raw if isinstance(raw, str) else None


class ComputerdRuntime:
    """Satisfies `nora.domain.ports.SandboxRuntime`."""

    def __init__(
        self, *, base_url: str, token: str, timeout_seconds: float = 30.0,
        max_output_chars: int = 8000, observer: ComputerCallObserver | None = None,
    ) -> None:
        self._base = base_url.rstrip("/") + "/v1"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_seconds
        self._max_output_chars = max_output_chars
        # Told, after every call, which operation ran, the status it answered
        # and whether computerd answered at all. Never raises into a call: a
        # broken observer must not turn a computer answer into a failure.
        self._observer = observer

    # -- the port ---------------------------------------------------------

    async def run(
        self, *, scope: str, command: str, timeout_s: float,
        limits: Mapping[str, int] | None = None,
    ) -> RunResult:
        payload: dict[str, Any] = {
            "scope": scope, "command": command, "timeout_s": timeout_s,
            "max_output_chars": self._max_output_chars,
        }
        # The reserved slot goes on the wire only when it names a limit, so
        # an unconfigured `[computer] limits` sends no slot.
        if limits:
            payload["limits"] = dict(limits)

        async def body() -> RunResult:
            reply = await self._post_json(
                "run", payload, deadline_s=timeout_s + _RUN_SLACK_SECONDS,
            )
            return RunResult(
                status=_status(reply.get("status")),
                exit_code=_opt_int(reply.get("exit_code")),
                stdout=_text(reply.get("stdout")), stderr=_text(reply.get("stderr")),
                truncated=reply.get("truncated") is True,
                duration_ms=_opt_int(reply.get("duration_ms")),
                reason=_opt_str(reply.get("reason")),
            )
        return await self._guarded(
            "run", body, lambda why: RunResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    async def read(
        self, *, scope: str, path: str, offset: int = 0, limit: int | None = None,
    ) -> FileResult:
        async def body() -> FileResult:
            payload: dict[str, Any] = {"scope": scope, "path": path, "offset": offset}
            if limit is not None:
                payload["limit"] = limit
            reply = await self._post_json("read", payload)
            return FileResult(
                status=_status(reply.get("status")), content=_text(reply.get("content")),
                binary=reply.get("binary") is True, size=_opt_int(reply.get("size")),
                sha256=_opt_str(reply.get("sha256")),
                truncated=reply.get("truncated") is True,
                lines_total=_opt_int(reply.get("lines_total")),
                reason=_opt_str(reply.get("reason")),
            )
        return await self._guarded(
            "read", body, lambda why: FileResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    async def write_text(self, *, scope: str, path: str, content: str) -> OpResult:
        sent = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return await self._op("write", {"scope": scope, "path": path, "content": content}, sent)

    async def edit(self, *, scope: str, path: str, old: str, new: str) -> OpResult:
        return await self._op(
            "edit", {"scope": scope, "path": path, "old": old, "new": new}, None,
        )

    async def files(self, *, scope: str, pattern: str = "**/*") -> ListResult:
        async def body() -> ListResult:
            reply = await self._post_json("files", {"scope": scope, "pattern": pattern})
            raw = reply.get("entries")
            entries = tuple(
                FileEntry(path=e["path"], size=e["size"])
                for e in (raw if isinstance(raw, list) else [])
                if isinstance(e, dict) and isinstance(e.get("path"), str)
                and _opt_int(e.get("size")) is not None
            )
            return ListResult(
                status=_status(reply.get("status")), entries=entries,
                truncated=reply.get("truncated") is True,
                reason=_opt_str(reply.get("reason")),
            )
        return await self._guarded(
            "files", body, lambda why: ListResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        async def body() -> BytesResult:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as session,
                session.post(
                    f"{self._base}/fetch", headers=self._headers,
                    json={"scope": scope, "path": path, "max_bytes": max_bytes},
                ) as response,
            ):
                if response.status != 200:
                    raise _NoAnswerError(f"computerd answered HTTP {response.status}")
                if response.content_type == "application/json":
                    reply = await response.json()
                    if not isinstance(reply, dict):
                        raise _UnavailableError("computerd returned a non-object JSON body")
                    return BytesResult(
                        status=_status(reply.get("status")),
                        reason=_opt_str(reply.get("reason")),
                    )
                claimed = response.headers.get("X-Computerd-Sha256")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.content.iter_chunked(65536):
                    received += len(chunk)
                    if received > max_bytes:
                        return BytesResult(SandboxStatus.DENIED, reason="file is over the cap")
                    chunks.append(chunk)
            data = b"".join(chunks)
            digest = hashlib.sha256(data).hexdigest()
            if claimed != digest:
                raise _UnavailableError("the bytes received do not match the digest computerd sent")
            return BytesResult(status=SandboxStatus.OK, data=data, sha256=digest)
        return await self._guarded(
            "fetch", body, lambda why: BytesResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    async def put(self, *, scope: str, path: str, data: bytes) -> OpResult:
        sent = hashlib.sha256(data).hexdigest()

        async def body() -> OpResult:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as session,
                session.post(
                    f"{self._base}/put", headers=self._headers,
                    params={"scope": scope, "path": path}, data=data,
                ) as response,
            ):
                if response.status != 200:
                    raise _NoAnswerError(f"computerd answered HTTP {response.status}")
                reply = await response.json(content_type=None)
            return _op_result(reply, sent)
        return await self._guarded(
            "put", body, lambda why: OpResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    async def state(self, *, scope: str) -> StateResult:
        async def body() -> StateResult:
            reply = await self._post_json("state", {"scope": scope})
            raw_files = reply.get("files")
            files = tuple(
                StateFile(path=f["path"], size=f["size"], mtime=_epoch(f.get("mtime")))
                for f in (raw_files if isinstance(raw_files, list) else [])
                if isinstance(f, dict) and isinstance(f.get("path"), str)
                and _opt_int(f.get("size")) is not None
                and isinstance(f.get("mtime"), (int, float))
            )
            raw_tmux = reply.get("tmux")
            # None stays None: «could not list» is not «nothing running».
            tmux = None if not isinstance(raw_tmux, list) else tuple(
                TmuxSession(
                    name=t["name"], created=_epoch(t.get("created")),
                    attached=t.get("attached") is True, running=t.get("running") is True,
                )
                for t in raw_tmux
                if isinstance(t, dict) and isinstance(t.get("name"), str)
            )
            raw_procs = reply.get("processes")
            # None stays None here too: an old computerd that sends no
            # `processes`, or one that could not read /proc, is UNKNOWN.
            processes = None if not isinstance(raw_procs, list) else tuple(
                ScopeProcess(
                    pid=p["pid"], command=_text(p.get("command")),
                    started=_epoch(p.get("started")),
                    elapsed_seconds=_opt_int(p.get("elapsed")) or 0,
                )
                for p in raw_procs
                if isinstance(p, dict) and _opt_int(p.get("pid")) is not None
            )
            return StateResult(
                status=_status(reply.get("status")), files=files,
                files_truncated=reply.get("files_truncated") is True, tmux=tmux,
                tmux_reason=_opt_str(reply.get("tmux_reason")),
                reason=_opt_str(reply.get("reason")),
                processes=processes,
                processes_truncated=reply.get("processes_truncated") is True,
                processes_reason=(_opt_str(reply.get("processes_reason"))
                                  or (None if isinstance(raw_procs, list)
                                      else "computerd did not report processes")),
            )
        return await self._guarded(
            "state", body, lambda why: StateResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    # -- plumbing ---------------------------------------------------------

    async def _op(self, op: str, payload: dict[str, Any], sent: str | None) -> OpResult:
        async def body() -> OpResult:
            return _op_result(await self._post_json(op, payload), sent)
        return await self._guarded(
            op, body, lambda why: OpResult(SandboxStatus.UNAVAILABLE, reason=why),
        )

    async def _post_json(
        self, op: str, payload: dict[str, Any], *, deadline_s: float | None = None,
    ) -> dict[str, Any]:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=deadline_s or self._timeout),
            ) as session,
            session.post(f"{self._base}/{op}", headers=self._headers, json=payload) as response,
        ):
            if response.status != 200:
                raise _NoAnswerError(f"computerd answered HTTP {response.status}")
            reply = await response.json(content_type=None)
        if not isinstance(reply, dict):
            raise _UnavailableError("computerd returned a non-object JSON body")
        return reply

    async def _guarded(
        self, op: str, body: Callable[[], Awaitable[R]], unavailable: Callable[[str], R],
    ) -> R:
        """The never-raise barrier every public method goes through, and the one
        place a call is counted: `op`, the status it answered (the result's own
        `status`) and whether computerd answered at all.

        `answered` is False only for a refused or timed-out connection and for
        a non-200 reply. Any other failure happened AFTER a reply arrived
        (parsing it, a digest that did not match), so computerd is up and the
        status says what was wrong."""
        answered = True
        try:
            result = await body()
        except _NoAnswerError as exc:
            answered, result = False, unavailable(str(exc))
        except _UnavailableError as exc:
            result = unavailable(str(exc))
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.info("computerd unreachable: %s: %s", type(exc).__name__, exc)
            answered = False
            result = unavailable(f"the computer is unreachable ({type(exc).__name__})")
        except Exception as exc:  # a raise out of the port breaks every tool on it
            log.exception("computerd call failed unexpectedly")
            result = unavailable(f"the computer call failed ({type(exc).__name__})")
        self._observe(op, str(getattr(result, "status", SandboxStatus.UNAVAILABLE)), answered)
        return result

    def _observe(self, op: str, status: str, answered: bool) -> None:
        if self._observer is None:
            return
        try:
            self._observer.call(op, status, answered)
        except Exception:
            log.exception("computer call observer failed (op=%s)", op)


def _epoch(raw: Any) -> datetime:
    """Epoch seconds from computerd as a UTC-aware datetime (0 when absent)."""
    seconds = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
    return datetime.fromtimestamp(seconds, tz=UTC)


def _op_result(reply: Any, sent: str | None) -> OpResult:
    if not isinstance(reply, dict):
        raise _UnavailableError("computerd returned a non-object JSON body")
    status = _status(reply.get("status"))
    stored = _opt_str(reply.get("sha256"))
    if status is SandboxStatus.OK and sent is not None and stored != sent:
        raise _UnavailableError("computerd stored different bytes from the ones sent")
    return OpResult(
        status=status, size=_opt_int(reply.get("size")), sha256=stored,
        reason=_opt_str(reply.get("reason")),
        previous_sha256=_opt_str(reply.get("previous_sha256")),
    )
