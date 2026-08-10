"""signal-cli JSON-RPC send (spec C.3). Returns SendResult; never raises to the caller.

THE WIRE NAMES, AND HOW THEY WERE ESTABLISHED
----------------------------------------------
`nora.platform.signal.react` set the precedent: a JSON-RPC method IS the CLI
command name and "parameter names are provided in camelCase format instead of
the hyphen format on the cli" (`signal-cli-jsonrpc(5)`). That sentence is
enough for `sendReaction`'s five scalars, and it is NOT enough here — the same
man page also says a repeatable option "can be provided as single json value or
as json array" and gives `--attachment` becoming **`attachments`**, plural. So
for `--mention` the documentation admits both `mention` and `mentions` without
saying which, and picking by eye is how a silently-ignored parameter ships (the
message goes out, unmentioned, with no error anywhere — R-5's shape).

So the names below were read out of the shipped **0.14.7 jar**, not inferred:

* `org.asamk.signal.commands.JsonRpcNamespace` is the adapter between the JSON
  `params` object and argparse4j's `Namespace`. `get(name)` tries the literal
  key, then `Util.dashSeparatedToCamelCaseString(name)`. `getList(name)` tries
  the literal key as a LIST first, falls back to wrapping a scalar under the
  same key, and only then tries `name + "s"` (the constant-pool string is
  `\\u0001s`, a `makeConcatWithConstants` recipe — plural is the LAST resort,
  not the first).
* `org.asamk.signal.commands.SendCommand` reads `namespace.getList("mention")`
  and `namespace.getString("preview-url" | "preview-title" |
  "preview-description" | "preview-image")`.

Combining those two: **`"mention"` as a JSON array is the FIRST lookup** and
needs no fallback, which is why it is the name used here rather than the
plural the `--attachment` example would suggest. The four preview scalars are
sent camelCased (`previewUrl`, …) because that is the form the man page
documents as public; the dash form resolves too, via `get`'s literal-key
attempt, so both are correct and only one is documented.

A mention element is `"start:length:recipient"` — `SendCommand.parseMentions`
compiles `(\\d+):(\\d+):(.+)` and passes group 1 as start, group 2 as length and
group 3 to `CommandUtil.getSingleRecipientIdentifier`. Units are UTF-16 code
units (`nora.domain.ports.Mention`).

**NOT VERIFIED AGAINST A LIVE DAEMON.** Every name above is read from the jar
this deployment runs and from its own man pages; no send carrying a mention or
a preview has been made, because the only live conversation is a real one. The
failure mode of a wrong name here is a message that goes out plain — signal-cli
ignores parameters it does not read — so this degrades the way the rest of the
feature does rather than costing the message. A live smoke test against the
operator's daemon is the confirmation this cannot supply.
"""

from __future__ import annotations

import logging
import os

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import LinkPreview, Mention, SendResult

log = logging.getLogger("nora.signal.send")


def _mention_params(mentions: tuple[Mention, ...]) -> list[str]:
    """`Mention`s as signal-cli's `start:length:recipient` strings.

    Malformed ranges are dropped HERE as well as refused upstream, and that
    duplication is deliberate rather than defensive clutter: `SendCommand`
    throws `UserErrorException` on a value its regex does not match, which
    fails the WHOLE send — so a negative start or an empty recipient reaching
    the daemon does not cost a mention, it costs the message. `nora.agent.
    outbound` cannot construct one of these; a future second producer might.
    """
    out = []
    for m in mentions:
        if m.start < 0 or m.length <= 0 or not m.recipient or ":" in m.recipient[:1]:
            log.error("dropping a malformed mention range (start=%s, length=%s)",
                      m.start, m.length)
            continue
        out.append(f"{m.start}:{m.length}:{m.recipient}")
    return out


def _preview_params(preview: LinkPreview) -> dict[str, str]:
    """The four `preview*` scalars, omitting the two that are optional-and-empty.

    `previewTitle` is sent even when empty only because signal-cli calls it
    mandatory and would `Optional.orElse("")` it anyway; `nora.infra.preview`
    refuses to produce a titleless preview in the first place, so this is the
    belt to that braces.
    """
    params = {"previewUrl": preview.url, "previewTitle": preview.title}
    if preview.description:
        params["previewDescription"] = preview.description
    if preview.image_data_uri:
        params["previewImage"] = preview.image_data_uri
    return params


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
        self,
        target: ConversationTarget,
        text: str,
        *,
        mentions: tuple[Mention, ...] = (),
        preview: LinkPreview | None = None,
        request_id: str = "",
    ) -> SendResult:
        if target.conversation_type != "group":
            log.error("refusing to send to conversation_type=%r (v2 is groups only)",
                      target.conversation_type)
            return SendResult(ok=False)
        params: dict[str, object] = {
            "groupId": target.conversation_id, "message": text,
        }
        # Both keys are ABSENT when there is nothing to say, never present and
        # empty. An empty `mention: []` is harmless today, but the whole point
        # of the previous release's payload being two keys is that a reader can
        # see what a plain message looks like on the wire — and an argparse4j
        # namespace with an empty list in it is a different object from one
        # without the key, which is the kind of difference that turns out to
        # matter on the version nobody tested against.
        if ranges := _mention_params(mentions):
            params["mention"] = ranges
        if preview is not None:
            params.update(_preview_params(preview))
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": params,
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
        # A COUNT of mentions, never the recipients — the same rule
        # `Deliverer` follows for the reply text. An ACI in a log line is an
        # identifier at rest in a file the operator greps, which R-18 is about
        # as much as it is about the group.
        log.info("sent %d chars to %s (id=%s, mentions=%d, preview=%s)",
                 len(text), target.conversation_id, request_id,
                 len(mentions), preview is not None)
        return SendResult(ok=True, timestamp=_sent_timestamp(body))
