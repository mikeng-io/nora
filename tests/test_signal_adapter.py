from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.models import ConversationTarget
from nora.obs.metrics import Metrics
from nora.platform.signal.adapter import SignalAdapter


def _rpc_app(contact_row: dict | None, status_uuid: str | None) -> web.Application:
    """A daemon answering the batched JSON-RPC `resolve_identity()` sends.

    signal-cli has no `/api/v1/accounts` endpoint (`signal-cli-jsonrpc(5)` lists
    only /api/v1/rpc, /api/v1/events, /api/v1/check), so the fake serves none.
    See `tests/test_signal_self_id.py` for the derivation's own suite.
    """

    async def handler(request: web.Request) -> web.Response:
        batch = await request.json()
        out = []
        for call in batch:
            if call["method"] == "listContacts":
                out.append({"jsonrpc": "2.0", "id": call["id"],
                            "result": [contact_row] if contact_row else []})
            else:
                out.append({"jsonrpc": "2.0", "id": call["id"], "result": (
                    [{"recipient": "+852999", "uuid": status_uuid}] if status_uuid else []
                )})
        return web.json_response(out)

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    return app


def _receive_event(*, source: str, group_id: str | None = "g", text: str = "hi") -> dict:
    data_message: dict = {"message": text}
    if group_id is not None:
        data_message["groupInfo"] = {"groupId": group_id}
    return {"method": "receive", "params": {"envelope": {
        "source": source, "dataMessage": data_message}}}


def test_adapter_declares_its_platform():
    assert SignalAdapter("http://x", "+1", metrics=Metrics()).platform == "signal"


def test_parse_is_not_on_the_public_surface():
    """`PlatformAdapter` deliberately has no `parse()` — every adapter
    parses its own transport internally, inside `listen()`. Keeping `parse`
    around as a public method would let a caller bypass the drop rules
    `listen()` enforces (DMs, sync messages, self-sends, empty bodies)."""
    assert not hasattr(SignalAdapter, "parse")


async def test_listen_parses_and_forwards_only_parseable_events():
    """`listen(*, on_message)` parses internally via `parse_event` and drops
    anything that parses to `None` — R-12's first pass (a DM, a self-send)
    plus an empty body — before `on_message` is ever called. The daemon
    connection itself (`SignalListener`) is swapped for a canned feed here;
    that half is `test_signal_listener.py`'s job.
    """
    adapter = SignalAdapter("http://x", "+852999", metrics=Metrics())
    events = [
        _receive_event(source="+852111", group_id=None),        # DM — dropped
        _receive_event(source="+852999", group_id="g"),         # self-send — dropped (R-12)
        _receive_event(source="+852111", group_id="g", text=""),  # empty body — dropped
        _receive_event(source="+852111", group_id="g", text="hello"),  # kept
    ]

    async def _fake_listen(on_event):
        for event in events:
            await on_event(event)

    adapter._listener.listen = _fake_listen  # white-box: swap the daemon connection only

    received = []

    async def _capture(message):
        received.append(message)

    await adapter.listen(on_message=_capture)

    assert len(received) == 1
    assert received[0].body == "hello"
    assert received[0].sender_id == "+852111"


async def test_listen_forwarding_unparseable_events_would_fail_the_drop_case():
    """PROBE: if the adapter forwarded every event instead of dropping the
    unparseable ones, this same drop-case assertion would fail — this test IS
    that check, the permanent guard against that regression."""
    adapter = SignalAdapter("http://x", "+852999", metrics=Metrics())
    events = [_receive_event(source="+852111", group_id=None)]  # a DM: must be dropped

    async def _fake_listen(on_event):
        for event in events:
            await on_event(event)

    adapter._listener.listen = _fake_listen

    received: list = []

    async def _capture(message):
        received.append(message)

    await adapter.listen(on_message=_capture)

    assert received == []


async def test_health_reports_connection_state():
    adapter = SignalAdapter("http://x", "+1", metrics=Metrics())
    health = await adapter.health()
    assert health.platform == "signal"
    assert health.connected is False
    assert health.last_event_age_seconds is None


async def test_send_delegates_to_the_sender():
    adapter = SignalAdapter("http://127.0.0.1:1", "+1", metrics=Metrics())
    # unreachable daemon → not ok, never an exception
    result = await adapter.send(target=ConversationTarget("signal", "g", "group"), text="x")
    assert result.ok is False


async def test_resolve_identity_always_includes_the_primary(aiohttp_server):
    """Even when the daemon reports nothing at all, `primary` is a member of
    `aliases` — `PlatformIdentity.__post_init__` guarantees it, but the adapter
    must actually reach that constructor."""
    server = await aiohttp_server(_rpc_app(None, None))
    adapter = SignalAdapter(
        str(server.make_url("")).rstrip("/"), "+852999", metrics=Metrics(),
    )
    identity = await adapter.resolve_identity()
    assert identity.platform == "signal"
    assert identity.primary == "+852999"
    assert identity.primary in identity.aliases


async def test_resolve_identity_includes_a_uuid_alias_when_the_daemon_reports_one(
    aiohttp_server,
):
    """signal-cli pairs a phone number with a UUID for the same account — R-12
    must recognize either form as Nora's own (nora/domain/ports.py's module
    docstring), so the UUID has to make it into `aliases`."""
    server = await aiohttp_server(
        _rpc_app({"number": "+852999", "uuid": "uuid-abc"}, None)
    )
    adapter = SignalAdapter(
        str(server.make_url("")).rstrip("/"), "+852999", metrics=Metrics(),
    )
    identity = await adapter.resolve_identity()
    assert "uuid-abc" in identity.aliases
    assert "+852999" in identity.aliases


async def test_resolve_identity_publishes_typed_identifiers_not_just_strings(
    aiohttp_server,
):
    """Nora's ids use the same `Identifier` model as everybody
    else's, and `aliases` is that set flattened — not a parallel source of
    truth an adapter could populate inconsistently."""
    server = await aiohttp_server(
        _rpc_app({"number": "+852999", "uuid": "uuid-abc", "username": "nora.000"}, None)
    )
    adapter = SignalAdapter(
        str(server.make_url("")).rstrip("/"), "+852999", metrics=Metrics(),
    )
    identity = await adapter.resolve_identity()
    assert Identifier("signal", "uuid", "uuid-abc") in identity.identifiers
    # The RPC row above carries `username`, and it is dropped: a renameable
    # handle is never a matchable identity. Asserted on
    # `aliases` too, because that is the flattened set R-12 tests membership in
    # — the one place a stray handle would actually be matched against.
    assert {i.kind for i in identity.identifiers} == {"phone", "uuid"}
    assert "nora.000" not in identity.aliases
    assert identity.aliases == {i.value for i in identity.identifiers} | {"+852999"}


async def test_resolve_identity_is_idempotent(aiohttp_server):
    server = await aiohttp_server(
        _rpc_app({"number": "+852999", "uuid": "uuid-abc"}, None)
    )
    adapter = SignalAdapter(
        str(server.make_url("")).rstrip("/"), "+852999", metrics=Metrics(),
    )
    first = await adapter.resolve_identity()
    second = await adapter.resolve_identity()
    assert first == second
