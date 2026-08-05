"""signal-cli JSON-RPC send (spec C.3). Returns SendResult; never raises to the caller."""

from __future__ import annotations

import logging
import os

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import SendResult

log = logging.getLogger("nora.signal.send")


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header as seconds. Only the delta-seconds form is
    handled — signal-cli has no documented use of the HTTP-date form. A missing
    or unparseable header degrades to `None`: the caller still learns the send is
    retryable, just without a hint of how long to wait.
    """
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _sent_timestamp(body: object) -> int | None:
    """The PLATFORM's own sent-timestamp out of a JSON-RPC `send` result.

    signal-cli answers `send` with `{"timestamp": <long>, "results": [...]}` —
    `org.asamk.signal.util.SendMessageResultUtils.outputResult` writes exactly
    that `Map.of` to the JsonWriter, and `SignalJsonRpcCommandHandler.runCommand`
    returns the single written object as the JSON-RPC `result`. (Read out of
    the shipped 0.14.7 jar; no live send was made to confirm it, since the only
    live group is a real one.)

    This body used to be discarded after the `error` check, which is Bug B:
    `Deliverer` stamps `time.time()` locally, Signal assigns this instead, and
    this is what a reply's `dataMessage.quote.id` points at. Measured 8 ms
    apart on live data — close enough to look right, never equal, so a join by
    `ts` silently resolved every sender except Nora herself.

    Anything unparseable degrades to `None` rather than raising: a send that
    reached the group must never be reported as failed over a missing field.
    """
    if not isinstance(body, dict):
        return None
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    value = result.get("timestamp")
    # `bool` is an `int` in Python and `True` would silently become 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def __getattr__(name: str):
    # Task 16 guard (spec E.3): `_PYTEST_GUARD` reads True whenever the caller is
    # currently running under pytest, so tests can assert the real send path is
    # inert. Evaluated live via PEP 562 module __getattr__ (not frozen at import
    # time): PYTEST_CURRENT_TEST is only set during a test's setup/call/teardown,
    # never during collection, so a plain module-level constant would freeze at
    # whatever value happened to hold when some test file first imports this
    # module at collection time — which is order-dependent and wrong.
    if name == "_PYTEST_GUARD":
        return "PYTEST_CURRENT_TEST" in os.environ
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class SignalSender:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def send(
        self, target: ConversationTarget, text: str, *, request_id: str = ""
    ) -> SendResult:
        if target.conversation_type != "group":
            log.error("refusing to send to conversation_type=%r (v2 is groups only)",
                      target.conversation_type)
            return SendResult(ok=False)
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {"groupId": target.conversation_id, "message": text},
            "id": request_id or "nora",
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(f"{self._base_url}/api/v1/rpc", json=payload) as resp,
            ):
                if resp.status == 429:
                    retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                    log.error("send rate-limited: HTTP 429 (conversation=%s, id=%s)",
                              target.conversation_id, request_id)
                    return SendResult(ok=False, retryable=True, retry_after=retry_after)
                if resp.status != 200:
                    log.error("send failed: HTTP %s (conversation=%s, id=%s)",
                              resp.status, target.conversation_id, request_id)
                    return SendResult(ok=False)
                body = await resp.json()
        except Exception as exc:               # noqa: BLE001 — a send failure is never fatal
            log.error("send failed: %s (conversation=%s, id=%s)",
                      type(exc).__name__, target.conversation_id, request_id)
            return SendResult(ok=False)
        if isinstance(body, dict) and body.get("error"):
            log.error("send rejected by signal-cli (conversation=%s, id=%s)",
                      target.conversation_id, request_id)
            return SendResult(ok=False)
        log.info("sent %d chars to %s (id=%s)", len(text), target.conversation_id, request_id)
        return SendResult(ok=True, timestamp=_sent_timestamp(body))
