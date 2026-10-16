"""signal-cli JSON-RPC send. Returns SendResult; never raises to the caller.

THE WIRE NAMES
--------------
`nora.platform.signal.react` set the precedent: a JSON-RPC method IS the CLI
command name and "parameter names are provided in camelCase format instead of
the hyphen format on the cli" (`signal-cli-jsonrpc(5)`). That sentence is
enough for `sendReaction`'s five scalars, and it is NOT enough here — the same
man page also says a repeatable option "can be provided as single json value or
as json array" and gives `--attachment` becoming **`attachments`**, plural. So
for `--mention` the documentation admits both `mention` and `mentions` without
saying which, and picking by eye is how a silently-ignored parameter ships (the
message goes out, unmentioned, with no error anywhere — R-5's shape).

So the names below are read out of the **0.14.7 jar**, not inferred:

* `org.asamk.signal.commands.JsonRpcNamespace` is the adapter between the JSON
  `params` object and argparse4j's `Namespace`. It overrides `get(name)` to try
  the literal key and then `Util.dashSeparatedToCamelCaseString(name)`, and
  `getList(name)` to try `super.getList(name)` and then `name + "s"` (the
  constant-pool string is `\\u0001s`, a `makeConcatWithConstants` recipe).
  **The subtlety that decides everything: `Namespace.getList` is
  `checkcast(List) this.get(dest)`, and `this.get` is a VIRTUAL call** — so it
  dispatches back into the override, and the camelCase attempt applies to list
  parameters too. Four spellings therefore resolve to one option:
  `text-style`, `textStyle`, `text-styles`, `textStyles`.
* `org.asamk.signal.commands.SendCommand` reads `namespace.getList("mention")`,
  `namespace.getList("text-style")` and `namespace.getString("preview-url" |
  "preview-title" | "preview-description" | "preview-image")`.

`mention` and `textStyle` are the spellings used below: the singular because
that is the literal key `SendCommand` asks for and it needs no fallback at all,
the camelCase because that is the form `signal-cli-jsonrpc(5)` documents as
public. Both resolve on the first or second lookup, and the live probe below
confirms them.

**Verified against the live daemon** (signal-cli 0.14.7). The parameter names
were probed on the running container without sending anything,
by exploiting the ORDER in which `SendCommand.handleCommand` validates: text
styles are parsed at bytecode offset 428 and the quote author is checked at
464, so a request carrying a deliberately invalid style AND a quote timestamp
with no author fails at one of two named errors and reaches no `sendMessage`:

    textStyle  / text-style / textStyles → "Invalid style: NOTASTYLE"
    textStylez (the negative control)    → "Quote author parameter is missing"
    mention                              → "Invalid phone number 'NOT-A-...'"
    mentionz   (the negative control)    → "Quote author parameter is missing"

The controls are what make the positives readable: the daemon answers a key it
does not know by falling through to the NEXT validation, so an instrument that
returned the same error for both would have measured nothing. A valid
`0:1:BOLD` also falls through to the quote error, and a lowercase `0:1:bold`
comes back «Invalid style: bold» — the style names are case-sensitive.

**What that probe does NOT cover:**

* **The four `preview*` scalars.** They are read with `getString`, which takes
  the `JsonRpcNamespace.get` path (literal key, then camelCase) and has no
  parse step to fail on — so there is no error to distinguish a key that was
  read from one that was ignored, and the same trick cannot be played. They
  remain a bytecode-and-man-page claim, exactly as `[preview] enabled = false`
  shipped (DEC-87), and a live smoke test is still the confirmation this cannot
  supply.
* **That a range which parses also RENDERS where it should** on a recipient's
  device. Nothing short of a real send into the real group can show that, and
  the failure mode there is cosmetic rather than silent — signal-cli rejects a
  malformed range loudly (see `_text_style_params`), so the untested half is
  «right bytes, wrong-looking», not «right-looking, wrong bytes».

A mention element is `"start:length:recipient"` — `SendCommand.parseMentions`
compiles `(\\d+):(\\d+):(.+)` and passes group 1 as start, group 2 as length and
group 3 to `CommandUtil.getSingleRecipientIdentifier`. A text style is
`"start:length:STYLE"` through the same regex in `parseTextStyles`, with group 3
handed to `TextStyle.Style.from`. Units are UTF-16 code units for both
(`nora.domain.utf16`).
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Mapping

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import (
    LinkPreview,
    Mention,
    OutboundAttachment,
    OutboundQuote,
    SendResult,
    TextStyle,
)
from ...domain.utf16 import utf16_len

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


def _text_style_params(styles: tuple[TextStyle, ...], text: str) -> list[str]:
    """`TextStyle`s as signal-cli's `start:length:STYLE` strings.

    The same shape as `_mention_params` above, for the same reason: a
    range signal-cli's `(\\d+):(\\d+):(.+)` regex rejects throws
    `UserErrorException` and fails the WHOLE send — the daemon answers
    `-1:3:BOLD` with «Invalid textStyle syntax (-1:3:BOLD) expected
    'start:length:STYLE'» and nothing is delivered.
    So a range that cannot be expressed is DROPPED here: the group gets the
    sentence without the bold, which is the whole point of R-5's ordering.

    **The bounds check is the part `_mention_params` has no equivalent of**, and
    it is here rather than upstream because this is the last place that holds
    both the ranges and the exact string they index. `Styled.__post_init__`
    already refuses an out-of-range style at construction, and
    `nora.agent.markdown` computes them from the very text it is returning — so
    a drop here means one of those two broke, which is exactly when a belt is
    worth its braces. It is logged at ERROR for that reason: unlike a dropped
    mention (an ordinary consequence of a stale address book), this one has no
    innocent cause.
    """
    limit = utf16_len(text)
    out = []
    for style in styles:
        if style.start < 0 or style.length <= 0 or style.start + style.length > limit:
            log.error(
                "dropping a malformed text style (start=%s, length=%s, limit=%s)",
                style.start, style.length, limit,
            )
            continue
        out.append(f"{style.start}:{style.length}:{style.style.value}")
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
    the shipped 0.14.7 jar; not confirmed by a live send.)

    This is what a reply's `dataMessage.quote.id` points at: `Deliverer`
    stamps `time.time()` locally and Signal assigns this instead, milliseconds
    apart — close enough to look right, never equal, so a join by `ts` would
    silently resolve every sender except Nora herself.

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
    # Test guard: `_PYTEST_GUARD` reads True whenever the caller is
    # currently running under pytest, so tests can assert the real send path is
    # inert. Evaluated live via PEP 562 module __getattr__ (not frozen at import
    # time): PYTEST_CURRENT_TEST is only set during a test's setup/call/teardown,
    # never during collection, so a plain module-level constant would freeze at
    # whatever value happened to hold when some test file first imports this
    # module at collection time — which is order-dependent and wrong.
    if name == "_PYTEST_GUARD":
        return "PYTEST_CURRENT_TEST" in os.environ
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: Every params key that carries DECORATION rather than the message. Named so
#: the undecorated retry below cannot drift from the block that sets them: a new
#: decoration key added to `send` without an entry here would be retried WITH the
#: thing that may have caused the rejection.
_DECORATION_KEYS = ("mention", "textStyle", "previewUrl", "previewTitle",
                    "previewDescription", "previewImage")


#: Seconds allowed for a send that CARRIES BYTES, as opposed to one that
#: carries a sentence.
#:
#: The flat 10 s below is sized for a text send, which returns in well under a
#: second; an attachment rides as a base64 `data:` URI in the JSON-RPC body, so
#: a megabyte-scale picture is two orders of magnitude more to serialize, ship
#: and have signal-cli hand to the service. It regularly needs longer than ten
#: seconds and it is not stuck. A send that times out while the image still
#: arrives makes `send` return `ok=False`, `send_reply` report a failure, and
#: Nora tell the room the path is broken when it is not.
#:
#: 60 s is the wall for "signal-cli is genuinely wedged", not a budget anybody
#: is waiting against: nothing blocks on this send, and a picture that takes 40
#: seconds is still a picture that arrives.
ATTACHMENT_TIMEOUT_SECONDS = 60.0


class SignalSender:
    def __init__(
        self, base_url: str, *, timeout: float = 10.0,
        dm_recipients: frozenset[str] = frozenset(),
    ) -> None:
        # The direct-message addresses this sender may reach: every Signal id
        # in `identity.toml` (`Persons.direct_addresses`). Empty by default, so
        # a sender nobody handed the set to refuses every direct message.
        self._dm_recipients = dm_recipients
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        # A SECOND budget rather than one raised for everything: a text send
        # that has not answered in ten seconds IS wedged, and waiting a minute
        # on it would hold a reply the room is actually waiting for. The two
        # sends have different shapes and deserve different patience.
        self._attachment_timeout = aiohttp.ClientTimeout(
            total=max(timeout, ATTACHMENT_TIMEOUT_SECONDS),
        )

    async def _rpc(
        self, payload: Mapping[str, object], client_timeout: aiohttp.ClientTimeout | None,
    ) -> tuple[int, str | None, object]:
        """One POST to signal-cli's JSON-RPC endpoint: the HTTP status, its
        `Retry-After`, and the body (read only on a 200).

        The wire and nothing else, so every refusal and decoration rule in
        `send` stays real wherever this is replaced. The eval sandbox replaces
        it (`nora.subject.sandbox_signal`): it has no signal-cli, and a send
        that always failed told the model `refused` on every reply.
        """
        async with (
            aiohttp.ClientSession(timeout=client_timeout) as session,
            session.post(f"{self._base_url}/api/v1/rpc", json=payload) as resp,
        ):
            if resp.status != 200:
                return resp.status, resp.headers.get("Retry-After"), None
            return resp.status, None, await resp.json()

    async def send(
        self,
        target: ConversationTarget,
        text: str,
        *,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
        request_id: str = "",
    ) -> SendResult:
        # A direct message goes only to somebody in `identity.toml`: any
        # other address is refused here, before a byte
        # leaves, whatever path asked. Its logs name it "dm", never the
        # number or ACI, which would be an identifier at rest in a log file.
        where = target.conversation_id
        if target.conversation_type == "dm":
            where = "dm"
            if target.conversation_id not in self._dm_recipients:
                log.error("refusing a direct message to an address not in identity.toml")
                return SendResult(ok=False)
            params: dict[str, object] = {
                "recipient": [target.conversation_id], "message": text,
            }
        elif target.conversation_type == "group":
            params = {"groupId": target.conversation_id, "message": text}
        else:
            log.error("refusing to send to conversation_type=%r", target.conversation_type)
            return SendResult(ok=False)
        # Every decoration key is ABSENT when there is nothing to say, never
        # present and empty. An empty `mention: []` is harmless today, but the
        # whole point of a plain message's payload being exactly two keys is
        # that a reader can see what one looks like on the wire — and an
        # argparse4j namespace with an empty list in it is a different object
        # from one without the key, which is the kind of difference that turns
        # out to matter on the version nobody tested against.
        if ranges := _mention_params(mentions):
            params["mention"] = ranges
        if spans := _text_style_params(styles, text):
            params["textStyle"] = spans
        if preview is not None:
            params.update(_preview_params(preview))
        if quote is not None:
            # `quoteTimestamp` + `quoteAuthor`, and BOTH are required: the
            # daemon checks the author at bytecode offset 464 — a request
            # carrying a timestamp and no author fails with "Quote author
            # parameter is missing". Sending one without the other is a
            # refused send, not an unthreaded one.
            params["quoteTimestamp"] = quote.ts
            params["quoteAuthor"] = quote.author_id
        if attachments:
            params["attachments"] = [
                f"data:{a.media_type};filename={a.filename};base64,"
                f"{base64.b64encode(a.content).decode('ascii')}"
                for a in attachments
            ]
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": params,
            "id": request_id or "nora",
        }
        # Chosen off the payload actually being sent, not off the caller's
        # intent: a retry that has DROPPED its decoration still carries its
        # attachments, and picking the budget here means the two can never
        # disagree about which kind of send this is.
        timeout = self._attachment_timeout if attachments else self._timeout
        try:
            status, retry_after_header, body = await self._rpc(payload, timeout)
            if status == 429:
                retry_after = _retry_after_seconds(retry_after_header)
                log.error("send rate-limited: HTTP 429 (conversation=%s, id=%s)",
                          where, request_id)
                return SendResult(ok=False, retryable=True, retry_after=retry_after)
            if status != 200:
                log.error("send failed: HTTP %s (conversation=%s, id=%s)",
                          status, where, request_id)
                return SendResult(ok=False)
        except TimeoutError:
            # ITS OWN BRANCH, and the log line says what a timeout means: «I
            # stopped waiting», never «it did not arrive» (a timed-out send
            # may have landed). `SendResult` carries no third state, so this
            # still returns `ok=False` and the model is told it failed. The
            # honest fix is a `confirmed` axis on `SendResult`, which every
            # caller on four platforms would have to learn, and which must NOT
            # be `retryable`, because retrying a send that already landed posts
            # the picture twice.
            #
            # The wider budget above keeps this branch from firing for the
            # ordinary case.
            log.error(
                "send timed out after %ss — the message may STILL have arrived; "
                "this is not evidence it did not (conversation=%s, id=%s, "
                "attachments=%d)",
                (timeout.total if timeout else None),
                where, request_id, len(attachments),
            )
            return SendResult(ok=False)
        except Exception as exc:               # noqa: BLE001 — a send failure is never fatal
            log.error("send failed: %s (conversation=%s, id=%s)",
                      type(exc).__name__, where, request_id)
            return SendResult(ok=False)
        if isinstance(body, dict) and body.get("error"):
            # RETRY ONCE, UNDECORATED, and this is the one retry in the send path.
            # Decoration is a nicety; the message is not. Four of the params
            # here — the `preview*` scalars — are not covered by the live-daemon
            # probe, because `getString` has no parse step and a read key is
            # indistinguishable from an ignored one; and `previewImage`'s `data:`
            # URI acceptance rests on a jar reading. If any of that is wrong,
            # signal-cli rejects the CALL, not the card, so `ok=False` here means
            # the group hears NOTHING — and URL-carrying replies are common
            # (DEC-89), so that would be the common case rather than a corner.
            #
            # R-5 says silence beats a wrong answer; it does not say silence beats
            # a PLAIN answer. Losing a preview card is invisible to the group;
            # losing the reply is the failure this whole codebase is built to
            # avoid. So a decorated send that is rejected is retried once with the
            # decoration dropped, and only a bare send that ALSO fails is a real
            # failure. Guarded on `params` having had decoration at all, so an
            # undecorated rejection is not retried twice — a genuine refusal
            # (blocked, not a member, bad group id) still fails on the first call.
            decorated = any(k in params for k in _DECORATION_KEYS)
            log.error(
                "send rejected by signal-cli (conversation=%s, id=%s, decorated=%s)",
                where, request_id, decorated,
            )
            if not decorated:
                return SendResult(ok=False)
            log.warning(
                "retrying the send UNDECORATED (conversation=%s, id=%s) — the "
                "reply matters, the card does not", where, request_id,
            )
            return await self.send(target, text, attachments=attachments, request_id=request_id)
        # A COUNT of mentions, never the recipients — the same rule
        # `Deliverer` follows for the reply text. An ACI in a log line is an
        # identifier at rest in a file the operator greps, which R-18 is about
        # as much as it is about the group.
        log.info("sent %d chars to %s (id=%s, mentions=%d, styles=%d, preview=%s)",
                 len(text), where, request_id,
                 len(mentions), len(styles), preview is not None)
        return SendResult(ok=True, timestamp=_sent_timestamp(body))
