"""`deploy/alert-bridge/bridge.py` — the last hop, and the one that decides
whether any of the monitoring stack is real.

This file's subject is not "does it format nicely" but "does a failure anywhere on
this path stay visible instead of turning into silence".

Imported by PATH rather than as a package: the bridge is a deployment artifact
with no dependencies on `nora`, deliberately (see its docstring), and importing
it as one would create the coupling it exists to avoid.

No test here reaches Discord. `urlopen` is replaced and the REQUEST is
inspected — the properties worth guarding (which channel, which credential
shape, what happens on a refusal) are all decided before the socket opens, and
a test that needed the network would be one nobody runs.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parents[2] / "deploy" / "alert-bridge" / "bridge.py"


@pytest.fixture
def bridge():
    spec = importlib.util.spec_from_file_location("alert_bridge", _BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(*alerts: dict, status: str = "firing") -> dict:
    return {"status": status, "alerts": list(alerts)}


def _alert(name: str, **annotations: str) -> dict:
    return {
        "labels": {"alertname": name, "severity": "critical", "job": "nora"},
        "annotations": annotations,
    }


def test_the_message_leads_with_firing_or_resolved(bridge):
    """On a phone screen the only thing that matters first is which of the two
    this is. A message that buries it is one the operator has to read twice at
    3am."""
    text = bridge.render(_payload(_alert("TargetDown", summary="nora is down")))
    assert text.startswith("[FIRING]")
    resolved = bridge.render(
        _payload(_alert("TargetDown", summary="nora is down"), status="resolved"),
    )
    assert resolved.startswith("[RESOLVED]")


def test_it_carries_the_alert_name_and_summary(bridge):
    text = bridge.render(_payload(_alert("HeartbeatStalled", summary="no tick for 6m")))
    assert "HeartbeatStalled" in text
    assert "no tick for 6m" in text


def test_a_description_identical_to_the_summary_is_not_repeated(bridge):
    """Half the rules set both and some set them the same. Printing it twice
    makes every message look padded, which is how people stop reading them."""
    text = bridge.render(
        _payload(_alert("X", summary="the same words", description="the same words")),
    )
    assert text.count("the same words") == 1


def test_a_long_burst_is_truncated_and_says_so(bridge):
    """A silently clipped list is a message that lies about how bad it is."""
    alerts = [_alert(f"Alert{i}", summary=f"s{i}") for i in range(25)]
    text = bridge.render(_payload(*alerts))
    assert "… and 15 more" in text
    assert "Alert0" in text and "Alert24" not in text


def test_a_missing_channel_or_token_is_reported_as_a_failure_not_a_success(
    bridge, monkeypatch, caplog,
):
    """An unset destination must not read as delivered. This is the difference
    between "you have alerting" and "you believe you have alerting", which is
    the state this whole stack exists to end.

    Both halves are checked because they fail for different reasons an operator
    fixes in different places: a channel id is compose configuration, a bot
    token is a credential.
    """
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")
    monkeypatch.setattr(bridge, "CHANNEL", "")
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    assert any("ALERT_DISCORD_CHANNEL" in r.message for r in caplog.records)

    caplog.clear()
    monkeypatch.setattr(bridge, "CHANNEL", "123")
    monkeypatch.setattr(bridge, "TOKEN", "")
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    assert any("DISCORD_BOT_TOKEN" in r.message for r in caplog.records)


def _wired(bridge, monkeypatch) -> list[object]:
    """Configure the bridge and capture the request it builds."""
    monkeypatch.setattr(bridge, "CHANNEL", "100000000000000207")
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")
    seen: list[object] = []

    class _Response:
        def read(self) -> bytes: return b"{}"
        def __enter__(self): return self
        def __exit__(self, *_a): return False

    def fake_urlopen(request: object, **_k: object) -> object:
        seen.append(request)
        return _Response()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_a_clean_post_is_a_delivery_and_says_so(bridge, monkeypatch, caplog):
    """The POSITIVE CONTROL. Every assertion in this file is satisfied by a
    `send` that always returns False; this is the one that proves it can
    succeed.

    It also asserts the success LINE: if only failures logged, an empty log would mean both
    "delivered" and "the webhook never arrived".
    """
    _wired(bridge, monkeypatch)
    with caplog.at_level("INFO"):
        assert bridge.send("anything") is True
    assert any("delivered" in r.getMessage() for r in caplog.records), (
        "a successful delivery is silent, so the log cannot distinguish it "
        "from an alert that never arrived"
    )


def test_it_posts_to_the_configured_channel_as_the_bot(bridge, monkeypatch):
    """The token rides an `Authorization: Bot` header and the channel id is in
    the path. Asserted because both are silent when wrong: a bad channel gives
    a 404 that looks like an outage, and a missing `Bot ` prefix gives a 401
    that looks like a bad token."""
    seen = _wired(bridge, monkeypatch)
    assert bridge.send("hello") is True
    request = seen[0]
    assert request.full_url.endswith("/channels/100000000000000207/messages")
    assert request.get_header("Authorization") == "Bot bot-token"
    # Discord blocks urllib's default agent outright.
    assert request.get_header("User-agent")
    assert json.loads(request.data)["content"] == "hello"


def test_an_over_long_message_is_truncated_and_says_so(bridge, monkeypatch):
    """Discord rejects a body over 2000 characters OUTRIGHT. A burst of alerts
    can exceed it, and a 400 would turn "many things broke" into "you were told
    nothing" — at the worst possible moment to lose the message."""
    seen = _wired(bridge, monkeypatch)
    assert bridge.send("x" * 5000) is True
    content = json.loads(seen[0].data)["content"]
    assert len(content) < 2000
    assert content.endswith("… truncated")


def test_a_refusal_from_discord_is_logged_with_discord_s_own_reason(
    bridge, monkeypatch, caplog,
):
    """Discord answers 403 for a bot without permission in that channel, 404
    for a wrong id, 429 when rate-limited — and the BODY carries which.

    **Asserted on the log, not on the return value.** `HTTPError` is a subclass of `URLError`, so
    deleting the dedicated branch entirely still returns `False` through the
    next handler: a `is False` assertion here passes either way and guards
    nothing. What the branch buys is the reason, and without it every one of
    403/404/429 looks identical to an operator — "discord unreachable" for a
    bot that simply lacks permission in the channel.
    """
    import io

    monkeypatch.setattr(bridge, "CHANNEL", "123")
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")

    def refuse(*_a: object, **_k: object) -> None:
        raise bridge.urllib.error.HTTPError(
            "https://discord.com", 403, "Forbidden", {},
            io.BytesIO(b'{"message":"Missing Permissions","code":50013}'),
        )

    monkeypatch.setattr(bridge.urllib.request, "urlopen", refuse)
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "403" in messages and "Missing Permissions" in messages, (
        f"the refusal was logged without Discord's reason: {messages!r}"
    )


def test_an_unreachable_discord_returns_false_and_never_raises(bridge, monkeypatch):
    """A bridge that dies on a bad response stops delivering every LATER alert
    too — the failure would spread from one notification to all of them."""
    monkeypatch.setattr(bridge, "CHANNEL", "123")
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(bridge.urllib.request, "urlopen", boom)
    assert bridge.send("anything") is False


# --- the inbound side: who may post, how much, how long, how many at once -----
#
# Every test below drives the REAL server over a socket, with `send` replaced so
# nothing reaches Discord. A test of a Handler method alone would not see a
# refused connection, a stalled body or a slot that never came back.

_TOKEN = "synthetic-bridge-token"  # noqa: S105 - a fake


@contextmanager
def _serving(bridge, monkeypatch, *, token: str = _TOKEN):
    """The bridge's real server on an ephemeral port, `send` recorded not sent."""
    monkeypatch.setattr(bridge, "BRIDGE_TOKEN", token)
    sent: list[str] = []
    monkeypatch.setattr(bridge, "send", lambda message: sent.append(message) or True)
    server = bridge.Server(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], sent
    finally:
        server.shutdown()
        server.server_close()


def _post(port: int, body: bytes, auth: str | None, timeout: float = 5.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if auth is not None:
        headers["Authorization"] = auth
    conn.request("POST", "/alert", body=body, headers=headers)
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.status


_GOOD_BODY = json.dumps(_payload(_alert("TargetDown", summary="nora is down"))).encode()


def test_the_alert_endpoint_refuses_a_post_without_the_bearer(bridge, monkeypatch):
    """Any co-networked container can reach this port, so membership in the
    network is not authority to post in the Discord channel. The bearer is the
    same shared secret every other bridge in the stack already checks."""
    with _serving(bridge, monkeypatch) as (port, sent):
        assert _post(port, _GOOD_BODY, auth=None) == 401
        assert _post(port, _GOOD_BODY, auth="Bearer wrong") == 401
        assert _post(port, _GOOD_BODY, auth=f"Basic {_TOKEN}") == 401
        assert _post(port, _GOOD_BODY, auth="Bearer ünïcode") == 401
    assert sent == [], "a refused POST still reached the send path"


def test_the_alert_endpoint_accepts_the_exact_bearer(bridge, monkeypatch):
    """The positive control for the refusal above: the right credential is the
    one thing that gets through, so the 401s are not a broken server."""
    with _serving(bridge, monkeypatch) as (port, sent):
        assert _post(port, _GOOD_BODY, auth=f"Bearer {_TOKEN}") == 200
    assert len(sent) == 1 and "TargetDown" in sent[0]


def test_an_empty_configured_token_fails_closed(bridge, monkeypatch):
    """A bridge with no token must not become an open one."""
    with _serving(bridge, monkeypatch, token="") as (port, sent):
        assert _post(port, _GOOD_BODY, auth="Bearer ") == 401
    assert sent == []


def test_the_bridge_refuses_to_start_without_a_token():
    """Started as the container starts it, with no token in the environment, it
    must exit at once. A server that starts open and warns is the failure this
    rule exists to prevent; the timeout turns that failure into a test failure
    rather than a hang."""
    env = {k: v for k, v in os.environ.items() if k != "NORA_BRIDGE_TOKEN"}
    bot = "synthetic-bot-credential"
    env.update(ALERT_DISCORD_CHANNEL="123", DISCORD_BOT_TOKEN=bot, ALERT_BRIDGE_PORT="0")
    done = subprocess.run(  # noqa: S603 - this repo's own script, fixed argv
        [sys.executable, str(_BRIDGE)], env=env, capture_output=True, text=True, timeout=15,
        check=False,
    )
    assert done.returncode != 0
    assert "NORA_BRIDGE_TOKEN" in done.stderr


def test_a_declared_length_over_the_limit_is_refused_before_the_body_is_read(
    bridge, monkeypatch,
):
    """413 is answered from the header alone. Reading first would let one request
    make the bridge buffer whatever size it declared."""
    with _serving(bridge, monkeypatch) as (port, sent):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.putrequest("POST", "/alert")
        conn.putheader("Authorization", f"Bearer {_TOKEN}")
        conn.putheader("Content-Length", str(bridge.MAX_BODY + 1))
        conn.endheaders()  # no body follows: a read would block until the timeout
        assert conn.getresponse().status == 413
        conn.close()
    assert sent == []


def test_a_non_numeric_length_is_a_400_not_a_dropped_connection(bridge, monkeypatch):
    with _serving(bridge, monkeypatch) as (port, _sent):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.putrequest("POST", "/alert")
        conn.putheader("Authorization", f"Bearer {_TOKEN}")
        conn.putheader("Content-Length", "twelve")
        conn.endheaders()
        assert conn.getresponse().status == 400
        conn.close()


def test_a_body_that_is_not_an_object_is_a_400(bridge, monkeypatch):
    """A JSON list parses, then `render` would call `.get` on it and the handler
    would die with no answer at all."""
    with _serving(bridge, monkeypatch) as (port, sent):
        assert _post(port, b"[1, 2]", auth=f"Bearer {_TOKEN}") == 400
    assert sent == []


def test_a_client_that_stalls_mid_body_is_disconnected(bridge, monkeypatch):
    """Headers arrive and the body never does. Without an inbound socket timeout
    the handler thread waits forever, and a few of these hold every slot."""
    monkeypatch.setattr(bridge.Handler, "timeout", 0.5)
    with _serving(bridge, monkeypatch) as (port, sent):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.putrequest("POST", "/alert")
        conn.putheader("Authorization", f"Bearer {_TOKEN}")
        conn.putheader("Content-Length", "100")
        conn.endheaders()
        # The server gives up well inside the client's own 5s; a stall would
        # surface here as socket.timeout instead.
        with pytest.raises((http.client.HTTPException, ConnectionError)):
            conn.getresponse()
        conn.close()
    assert sent == []


def test_a_burst_past_the_concurrency_bound_is_refused_not_queued(bridge, monkeypatch):
    """Each connection is a thread. With one slot held by a stalled client, the
    next well-formed POST must be turned away at once, not admitted to grow the
    thread count."""
    monkeypatch.setattr(bridge, "MAX_CONCURRENT", 1)
    monkeypatch.setattr(bridge.Handler, "timeout", 5.0)
    with _serving(bridge, monkeypatch) as (port, sent):
        stalled = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        stalled.sendall(
            b"POST /alert HTTP/1.1\r\nHost: x\r\n"
            + f"Authorization: Bearer {_TOKEN}\r\nContent-Length: 100\r\n\r\n".encode(),
        )
        try:
            time.sleep(0.2)  # let the stalled connection take its slot
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            conn.request(
                "POST", "/alert", body=_GOOD_BODY,
                headers={
                    "Authorization": f"Bearer {_TOKEN}",
                    "Content-Length": str(len(_GOOD_BODY)),
                },
            )
            assert conn.getresponse().status == 503
            conn.close()
        finally:
            stalled.close()
    assert sent == []


def test_a_body_cut_short_by_the_client_is_refused_not_delivered(bridge, monkeypatch):
    """A client that disconnects after part of its declared body must not have
    what arrived delivered as a complete alert. An empty read would otherwise
    parse as `{}` and go to Discord as "[UNKNOWN] 0 alert(s)"."""
    monkeypatch.setattr(bridge.Handler, "timeout", 5.0)
    with _serving(bridge, monkeypatch) as (port, sent):
        stalled = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        stalled.sendall(
            b"POST /alert HTTP/1.1\r\nHost: x\r\n"
            + f"Authorization: Bearer {_TOKEN}\r\nContent-Length: 100\r\n\r\n".encode()
            + b'{"alerts": ',
        )
        stalled.shutdown(socket.SHUT_WR)
        reply = stalled.recv(1024)
        stalled.close()
    assert reply.split(b" ")[1] == b"400", reply[:40]
    assert sent == []


def test_a_mention_in_an_alert_cannot_ping_anyone(bridge, monkeypatch):
    """Alert text is copied from rule annotations and label values, some of which
    echo request input. A bare `@everyone` in that text must not become a
    notification, so Discord is told to parse no mention at all."""
    seen = _wired(bridge, monkeypatch)
    assert bridge.send("@everyone <@123> @here") is True
    assert json.loads(seen[0].data)["allowed_mentions"] == {"parse": []}


def test_a_label_or_annotation_is_capped_before_it_is_rendered(bridge):
    """A runaway annotation must not become the message. The cap is applied to
    each field as it is read, so one field cannot push the rest out of the
    2000-character limit."""
    text = bridge.render(_payload(_alert("Big", summary="x" * 50_000, description="y" * 50_000)))
    assert "x" * 50_000 not in text and "y" * 50_000 not in text
    assert "x" * (bridge.MAX_FIELD + 1) not in text
    assert "y" * (bridge.MAX_FIELD + 1) not in text


def test_the_inbound_socket_has_a_timeout(bridge):
    """A client that stops sending must be disconnected. The stall test below
    shortens this to 0.5s to run fast, so it cannot see a removed default; this
    is the one that reads the real value."""
    assert 0 < bridge.Handler.timeout <= 30


def test_the_bridge_token_reaches_the_bridge_and_alertmanager():
    """Both ends of the bearer, read from the committed config. If either is
    dropped, every alert is refused with a 401: Alertmanager logs it, nothing
    pages anyone, and the alert channel goes silent without a single error in
    the bridge's own log. So this is checked as configuration, not assumed."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    services = compose["services"]
    # Required (`:?`), so an unset token fails the compose project rather than
    # starting a bridge or an Alertmanager that sends nothing.
    for name in ("alert-bridge", "alertmanager"):
        env = services[name].get("environment") or []
        assert any(e.startswith("NORA_BRIDGE_TOKEN=${NORA_BRIDGE_TOKEN:?") for e in env), name

    am = yaml.safe_load((root / "deploy" / "alertmanager" / "alertmanager.yml").read_text())
    hook = am["receivers"][0]["webhook_configs"][0]
    assert hook["url"] == "http://alert-bridge:9110/alert"
    auth = hook["http_config"]["authorization"]
    assert auth == {"type": "Bearer", "credentials_file": "/run/alertmanager/bridge-token"}
