
import logging

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import (
    LinkPreview,
    Mention,
    OutboundAttachment,
    SendResult,
    Style,
    TextStyle,
)
from nora.platform.signal.send import SignalSender

TARGET = ConversationTarget("signal", "Z3JvdXA=", "group")
ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"


@pytest.fixture
async def rpc_server(aiohttp_server):
    calls: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        if body.get("_fail"):
            return web.Response(status=500, text="boom")
        return web.json_response({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    server.calls = calls
    return server


async def test_send_posts_the_expected_jsonrpc_shape(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello", request_id="tag-1")
    assert result == SendResult(ok=True)
    body = rpc_server.calls[0]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "send"
    assert body["params"]["groupId"] == "Z3JvdXA="
    assert body["params"]["message"] == "hello"
    assert body["id"] == "tag-1"


async def test_send_encodes_verified_attachment_bytes_as_a_data_uri(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    attachment = OutboundAttachment(b"GIF89a", "image/gif", "reaction.gif")

    result = await sender.send(TARGET, "", attachments=(attachment,))

    assert result.ok is True
    assert rpc_server.calls[0]["params"]["attachments"] == [
        "data:image/gif;filename=reaction.gif;base64,R0lGODlh"
    ]


async def test_send_returns_a_non_retryable_failure_on_a_server_error(aiohttp_server):
    async def handler(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is False
    assert result.retryable is False


async def test_send_maps_http_429_to_a_retryable_failure(aiohttp_server):
    async def handler(request):
        return web.Response(status=429, headers={"Retry-After": "5"})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result == SendResult(ok=False, retryable=True, retry_after=5.0)


async def test_send_maps_http_429_without_retry_after_to_a_retryable_failure(aiohttp_server):
    async def handler(request):
        return web.Response(status=429)

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is False
    assert result.retryable is True
    assert result.retry_after is None


async def test_send_returns_false_on_a_jsonrpc_error_body(aiohttp_server):
    async def handler(request):
        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                  "error": {"code": -1, "message": "no such group"}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


async def test_send_returns_false_when_the_daemon_is_unreachable():
    sender = SignalSender("http://127.0.0.1:1", timeout=0.5)
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


async def test_send_never_raises_on_a_timeout(aiohttp_server):
    import asyncio

    async def handler(request):
        await asyncio.sleep(2)
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"), timeout=0.2)
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


async def test_send_does_not_log_the_message_body(rpc_server, caplog):
    """Group content is private; the send log line carries ids and length only.

    `set_level` is load-bearing, not decoration. The success line is `log.info`,
    and `caplog` defaults to WARNING, so without it the assertion would read a
    stream the line never reaches and stay green even when the body WAS logged.
    It is the only assertion in this module protecting a member's words. The
    positive control below proves the stream is being read at all.
    """
    caplog.set_level(logging.INFO, logger="nora.signal.send")
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    await sender.send(TARGET, "a very private secret")
    assert "a very private secret" not in caplog.text
    assert "sent 21 chars" in caplog.text          # the line IS being captured


async def test_a_dm_to_an_address_not_in_identity_toml_is_refused(rpc_server, caplog):
    """A direct message goes only to somebody in
    identity.toml. A sender handed no addresses refuses every one, and one
    handed some refuses the rest, before any call to signal-cli — and the log
    line does not carry the number it refused."""
    url = str(rpc_server.make_url("")).rstrip("/")
    dm = ConversationTarget("signal", "+85212345678", "dm")
    caplog.set_level(logging.INFO, logger="nora.signal.send")
    assert (await SignalSender(url).send(dm, "hi")).ok is False
    other = SignalSender(url, dm_recipients=frozenset({ACI}))
    assert (await other.send(dm, "hi")).ok is False
    assert rpc_server.calls == []
    assert "85212345678" not in caplog.text


async def test_a_dm_to_a_known_address_goes_to_the_recipient_not_a_group(rpc_server, caplog):
    sender = SignalSender(
        str(rpc_server.make_url("")).rstrip("/"), dm_recipients=frozenset({ACI}),
    )
    caplog.set_level(logging.INFO)
    result = await sender.send(ConversationTarget("signal", ACI, "dm"), "hi")
    assert result.ok is True
    params = rpc_server.calls[0]["params"]
    assert params == {"recipient": [ACI], "message": "hi"}
    assert ACI not in caplog.text            # logged as "dm", not as the ACI


# ── the platform's own sent-timestamp ─────────────────────────────────────────


async def test_send_returns_the_platform_assigned_timestamp(aiohttp_server):
    """`Deliverer` stamps `int(time.time() * 1000)` locally; Signal assigns its
    OWN sent-timestamp, and that is what a reply's `dataMessage.quote.id` points
    at — the two differ by milliseconds, so a join by `ts` would silently never
    match Nora's own rows.

    The response body carries it and `send` returns it. Shape read out of the
    signal-cli jar: `org.asamk.signal.util.SendMessageResultUtils.outputResult`
    writes `Map.of("timestamp", <long>, "results", [...])` to the `JsonWriter`,
    and `SignalJsonRpcCommandHandler.runCommand` returns exactly that object as
    the JSON-RPC `result`.
    """
    async def handler(request):
        return web.json_response({
            "jsonrpc": "2.0", "id": "tag-1",
            "result": {
                "timestamp": 1785950108291,
                "results": [{"recipientAddress": {"number": "+85212345678"},
                             "type": "SUCCESS"}],
            },
        })

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello", request_id="tag-1")
    assert result.ok is True
    assert result.timestamp == 1785950108291


async def test_a_response_without_a_timestamp_leaves_it_none(rpc_server):
    """The fixture server answers `"result": {}`. A missing timestamp must
    degrade to `None` — the row keeps its locally stamped `ts` — never to 0,
    which is a real epoch value and would sort every Nora row to the top."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is True
    assert result.timestamp is None


async def test_a_non_integer_timestamp_is_ignored_rather_than_raising(aiohttp_server):
    async def handler(request):
        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                  "result": {"timestamp": "nonsense"}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is True
    assert result.timestamp is None


# --------------------------------------------------------- decoration on the wire
#
# The parameter NAMES, which nothing else in the suite can check. They were read
# out of the shipped signal-cli 0.14.7 jar rather than inferred from the man
# page, because the man page documents both a camelCase rule AND a pluralising
# example (`--attachment` becoming `attachments`) and does not say which applies
# to a repeatable option. Guessing wrong here is SILENT: signal-cli ignores a
# parameter it never reads, the message goes out unmentioned or unstyled, and no
# error appears anywhere.
#
# The names were also CONFIRMED against the running daemon —
# `nora.platform.signal.send`'s module docstring records the probe and its
# negative controls. That matters for how to read this file: these tests hold
# the names STEADY, and the daemon is what established them. A test whose
# fixture is a local aiohttp server can only ever prove what this code sends,
# never what signal-cli accepts, and misreading one for the other is exactly how
# a wrong name survives a green suite.

async def test_a_mention_is_sent_as_start_length_recipient_under_the_mention_key(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "@Grant hi",
        mentions=(Mention(start=0, length=8, recipient=ACI),),
    )

    assert rpc_server.calls[0]["params"]["mention"] == [f"0:8:{ACI}"]


async def test_several_mentions_ride_one_array(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "@Grant @Miles",
        mentions=(
            Mention(start=0, length=8, recipient=ACI),
            Mention(start=9, length=5, recipient="+85251234567"),
        ),
    )

    assert rpc_server.calls[0]["params"]["mention"] == [
        f"0:8:{ACI}", "9:5:+85251234567",
    ]


async def test_a_plain_message_carries_no_decoration_key_at_all(rpc_server):
    """A plain send's payload is exactly the two keys `groupId` and `message`.

    Not cosmetic: an argparse4j namespace holding an EMPTY list is a different
    object from one holding no key at all, and this is what keeps the shipped
    shape of the overwhelmingly common message from drifting as a side effect
    of a feature nobody used on it."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(TARGET, "hello")

    assert set(rpc_server.calls[0]["params"]) == {"groupId", "message"}


async def test_a_style_is_sent_as_start_length_style_under_the_text_style_key(rpc_server):
    """The name and the encoding together (DEC-82).

    `textStyle` — singular, camelCase. `SendCommand` asks for `text-style`, and
    `JsonRpcNamespace.getList` resolves that through an overridden `get` that
    tries the literal key and then the camelCase form, which is the spelling
    `signal-cli-jsonrpc(5)` documents. Confirmed on the live daemon: this key
    reached `parseTextStyles` (it rejected a deliberately invalid style name),
    while a `textStylez` control fell through to the next validation instead.
    """
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "hello there",
        styles=(TextStyle(start=0, length=5, style=Style.BOLD),),
    )

    assert rpc_server.calls[0]["params"]["textStyle"] == ["0:5:BOLD"]


async def test_the_style_name_is_sent_uppercase_because_the_parser_is_exact(rpc_server):
    """`TextStyle.Style.from` is a chain of `String.equals` against uppercase
    names, so a lowercase value is not merely ignored — it fails the whole send.
    The live daemon answered `0:1:bold` with «Invalid style: bold»."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "hello there",
        styles=(
            TextStyle(start=0, length=5, style=Style.BOLD),
            TextStyle(start=6, length=5, style=Style.ITALIC),
        ),
    )

    assert rpc_server.calls[0]["params"]["textStyle"] == ["0:5:BOLD", "6:5:ITALIC"]


async def test_mentions_and_styles_ride_the_same_message_under_separate_keys(rpc_server):
    """The two range arrays are independent parameters over one body, which is
    what makes them able to disagree — and the reason everything upstream of
    this line exists to stop them."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "@Grant hi",
        mentions=(Mention(start=0, length=6, recipient=ACI),),
        styles=(TextStyle(start=7, length=2, style=Style.BOLD),),
    )

    params = rpc_server.calls[0]["params"]
    assert params["mention"] == [f"0:6:{ACI}"]
    assert params["textStyle"] == ["7:2:BOLD"]


async def test_a_style_range_past_the_end_of_the_text_is_dropped(rpc_server):
    """Same rule as a malformed mention, and the daemon is the reason: it
    answered `-1:3:BOLD` with «Invalid textStyle syntax» and sent nothing at
    all. A range that cannot be expressed costs the STYLING, never the message.

    The over-long range here is the case a bounds check catches and the regex
    would not — `12:9:BOLD` matches `(\\d+):(\\d+):(.+)` perfectly well and would
    be posted, indexing five units of text that do not exist.
    """
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    result = await sender.send(
        TARGET, "hello",
        styles=(
            TextStyle(start=-1, length=3, style=Style.BOLD),
            TextStyle(start=0, length=0, style=Style.BOLD),
            TextStyle(start=12, length=9, style=Style.BOLD),
        ),
    )

    assert result.ok is True
    assert "textStyle" not in rpc_server.calls[0]["params"]


async def test_the_bounds_check_counts_utf16_units_not_characters(rpc_server):
    """A range covering an emoji is LONGER than the character count, so a bounds
    check written in `len()` would drop the one valid range in this message.

    PROBE: change `_text_style_params`' `limit` to `len(text)` and this reds —
    the whole point being that the check must fail in the same units the range
    is expressed in, or it becomes a second source of wrongness rather than a
    guard against the first.
    """
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "😅 好正",                     # 2 + 1 + 2 = 5 UTF-16 units, 4 chars
        styles=(TextStyle(start=3, length=2, style=Style.BOLD),),
    )

    assert rpc_server.calls[0]["params"]["textStyle"] == ["3:2:BOLD"]


async def test_a_preview_is_sent_as_the_four_camelcase_scalars(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "https://links.example.net/a",
        preview=LinkPreview(
            url="https://links.example.net/a", title="A page", description="about it",
            image_data_uri="data:image/png;base64,AAAA",
        ),
    )

    params = rpc_server.calls[0]["params"]
    assert params["previewUrl"] == "https://links.example.net/a"
    assert params["previewTitle"] == "A page"
    assert params["previewDescription"] == "about it"
    assert params["previewImage"] == "data:image/png;base64,AAAA"


async def test_the_optional_preview_scalars_are_omitted_when_empty(rpc_server):
    """`--preview-description`/`--preview-image` are documented optional;
    `--preview-title` is documented MANDATORY and is always sent."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "https://links.example.net/a",
        preview=LinkPreview(url="https://links.example.net/a", title="A page"),
    )

    params = rpc_server.calls[0]["params"]
    assert params["previewTitle"] == "A page"
    assert "previewDescription" not in params
    assert "previewImage" not in params


async def test_a_preview_image_travels_as_a_data_uri_not_a_path(rpc_server):
    """signal-cli runs in a DIFFERENT container from Nora with no shared
    volume (`docker-compose.yml`), so a PATH here would be unreadable in
    production while passing every local test. `AttachmentHelper.
    getAttachmentStream` skips its file-path validation entirely for a `data:`
    string (read out of the 0.14.7 jar), which is what makes this work."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "https://links.example.net/a",
        preview=LinkPreview(
            url="https://links.example.net/a", title="A page",
            image_data_uri="data:image/jpeg;base64,/9j/4AAQ",
        ),
    )

    assert rpc_server.calls[0]["params"]["previewImage"].startswith("data:image/")


async def test_a_malformed_mention_range_is_dropped_rather_than_posted(rpc_server):
    """signal-cli throws `UserErrorException` on a range its
    `(\\d+):(\\d+):(.+)` regex rejects, and that fails the WHOLE send. A bad
    range must therefore cost the CHIP, never the message."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    result = await sender.send(
        TARGET, "hello",
        mentions=(
            Mention(start=-1, length=3, recipient=ACI),
            Mention(start=0, length=0, recipient=ACI),
            Mention(start=0, length=3, recipient=""),
        ),
    )

    assert result.ok is True
    assert "mention" not in rpc_server.calls[0]["params"]


async def test_a_valid_range_survives_alongside_a_malformed_one(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "hello",
        mentions=(
            Mention(start=-5, length=3, recipient=ACI),
            Mention(start=0, length=5, recipient=ACI),
        ),
    )

    assert rpc_server.calls[0]["params"]["mention"] == [f"0:5:{ACI}"]


# ── the undecorated retry ─────────────────────────────────────────────────────


async def test_a_rejected_decorated_send_is_retried_without_the_decoration(aiohttp_server):
    """THE PROBE. Decoration is a nicety; the message is not.

    The four `preview*` params are not covered by the live-daemon probe —
    `getString` has no parse step, so a key that is READ and a key that is
    IGNORED look identical — and `previewImage`'s `data:` URI acceptance rests
    on a jar reading. If any of that is wrong, signal-cli rejects the CALL, not
    the card: `ok=False`, row FAILED, and the group hears NOTHING. DEC-89 makes
    URL-carrying replies the common case rather than a corner.

    R-5 says silence beats a WRONG answer. It does not say silence beats a
    PLAIN one. Delete the retry and this reds."""
    seen: list[dict] = []

    async def handler(request):
        body = await request.json()
        seen.append(body["params"])
        if any(k in body["params"] for k in ("previewUrl", "mention", "textStyle")):
            return web.json_response({"error": {"message": "Invalid preview"}})
        return web.json_response({"result": {"timestamp": 111}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))

    result = await sender.send(
        TARGET, "去呢間 https://links.example.net/9DpAk8g",
        preview=LinkPreview(url="https://links.example.net/9DpAk8g", title="Google Maps"),
        attachments=(OutboundAttachment(b"GIF89a", "image/gif", "reaction.gif"),),
    )

    assert result.ok is True                       # the REPLY got through
    assert len(seen) == 2                          # decorated, then bare
    assert "previewUrl" in seen[0]
    assert not any(k in seen[1] for k in ("previewUrl", "previewTitle", "mention", "textStyle"))
    assert seen[1]["attachments"] == ["data:image/gif;filename=reaction.gif;base64,R0lGODlh"]
    assert seen[1]["message"] == "去呢間 https://links.example.net/9DpAk8g"   # same words


async def test_an_undecorated_rejection_is_not_retried(aiohttp_server):
    """The bound on the retry. A bare send that signal-cli refuses is a REAL
    refusal — blocked, not a member, bad group id — and retrying it would turn
    one honest failure into two identical ones."""
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await request.json()
        return web.json_response({"error": {"message": "Group not found"}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hi")

    assert result.ok is False
    assert calls == 1


# --- the attachment timeout ---------------------------------------------------
#
# The flat 10s budget is sized for a text send; an attachment rides as a base64
# `data:` URI in the JSON-RPC body, so a megabyte-scale picture is two orders of
# magnitude more to serialize and ship. A timeout on a send that still arrives
# is a LIE: `ok=False` reaches `send_reply`, and Nora tells the room the path
# was broken and describes the image in words instead.


async def test_an_attachment_send_gets_a_longer_budget_than_a_text_send():
    """A SLOW send must not be reported as a failed one.

    Asserted on the CLIENT TIMEOUTS rather than by making a server sleep: a
    sleep test would have to outlast the real 60s budget to prove anything, and
    a shorter one would pass whatever the budget was — the silent-match failure
    inside the instrument meant to find it.
    """
    from nora.platform.signal.send import ATTACHMENT_TIMEOUT_SECONDS

    sender = SignalSender("http://signal-cli:18080")

    assert sender._timeout.total == 10.0, "the text budget should be unchanged"
    assert sender._attachment_timeout.total == ATTACHMENT_TIMEOUT_SECONDS
    assert sender._attachment_timeout.total > sender._timeout.total, (
        "an attachment send needs more patience than a sentence — with one flat "
        "budget a picture that takes 12s is reported as a failed send, and the "
        "picture arrives anyway"
    )


async def test_a_configured_timeout_never_shrinks_the_attachment_budget():
    """`max(...)`, not a plain override. An operator raising `timeout` for a
    slow daemon must not accidentally be given a SHORTER attachment budget than
    the default, which a bare assignment would do for any value under 60."""
    from nora.platform.signal.send import ATTACHMENT_TIMEOUT_SECONDS

    assert SignalSender("http://x", timeout=5.0)._attachment_timeout.total == (
        ATTACHMENT_TIMEOUT_SECONDS
    )
    assert SignalSender("http://x", timeout=90.0)._attachment_timeout.total == 90.0


async def test_the_attachment_budget_is_chosen_off_the_payload(
    rpc_server, monkeypatch,
):
    """The budget ACTUALLY HANDED to the session, per send.

    Asserting only that both kinds of send return `ok=True` against the fake
    server would pass with the selection line deleted — the fake answers
    instantly, so every budget works. Asserting an OUTCOME that any timeout
    produces proves nothing about which timeout was used.

    So this records the `timeout=` each `ClientSession` is constructed with,
    which is the only place the choice is observable.
    """
    import nora.platform.signal.send as mod
    from nora.platform.signal.send import ATTACHMENT_TIMEOUT_SECONDS

    seen: list[float | None] = []
    real_session = mod.aiohttp.ClientSession

    def record(*args, **kwargs):
        timeout = kwargs.get("timeout")
        seen.append(timeout.total if timeout is not None else None)
        return real_session(*args, **kwargs)

    monkeypatch.setattr(mod.aiohttp, "ClientSession", record)
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    assert await sender.send(TARGET, "text only") == SendResult(ok=True)
    attachment = OutboundAttachment(b"GIF89a", "image/gif", "pic.gif")
    assert await sender.send(
        TARGET, "with a picture", attachments=(attachment,),
    ) == SendResult(ok=True)

    assert seen == [10.0, ATTACHMENT_TIMEOUT_SECONDS], (
        f"budgets handed to the session were {seen}; a picture sent on the "
        f"text budget is the live defect — it times out at 10s and arrives "
        f"anyway, and the model is told it failed"
    )
    assert "attachments" not in rpc_server.calls[0]["params"]
    assert len(rpc_server.calls[1]["params"]["attachments"]) == 1
