"""computerd's browsers: a room's Chrome is
reached only through computerd's authenticated CDP websocket, relayed over
Chrome's `--remote-debugging-pipe`; there is never a debugging port.

A fake Chrome stands in for the real one: it speaks the pipe protocol (NUL-
terminated JSON in on fd 3, out on fd 4), models the startup TLS protocol and
reports its argv/profile/display for Browser.getVersion. Unknown calls fail.
A fake Xvfb creates its display's socket file, which is
what computerd waits for."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from nora.domain.computer import computer_scope
from tests.contract.test_sandbox_runtime_contract import TOKEN, load_computerd, short_tmux_root

SCOPE = computer_scope("signal", "room-a")
OTHER = computer_scope("signal", "room-b")
THIRD = computer_scope("signal", "room-c")
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FAKE_CHROME = """#!{python}
import json, os, sys, time
argv = sys.argv[1:]
mode = os.environ.get("FAKE_TLS_MODE", "ready")
updated, probes = False, 0
history = []
log = os.path.join(os.environ["FAKE_X11_DIR"], "chrome-%d.jsonl" % os.getpid())
inbox, outbox = os.fdopen(3, "rb", buffering=0), os.fdopen(4, "wb", buffering=0)
buffer = b""
while True:
    chunk = inbox.read(65536)
    if not chunk:
        break
    buffer += chunk
    while b"\\0" in buffer:
        raw, buffer = buffer.split(b"\\0", 1)
        message = json.loads(raw)
        with open(log, "a") as f:
            f.write(json.dumps(message) + "\\n")
        method, p = message["method"], message.get("params", {{}})
        history.append(method)
        result = None
        if method == "Target.createBrowserContext":
            result = {{"browserContextId": "probe-context"}}
        elif method == "Target.createTarget":
            if p["url"] == "about:blank":
                assert p["browserContextId"] == "probe-context"
            else:
                assert p["url"] == "chrome://components/" and "browserContextId" not in p
            result = {{"targetId": "components" if p["url"] == "chrome://components/" else "probe"}}
        elif method == "Target.attachToTarget":
            result = {{"sessionId": p["targetId"]}}
        elif method == "Page.getFrameTree":
            result = {{"frameTree": {{"frame": {{"id": "probe-frame"}}}}}}
        elif method == "Network.loadNetworkResource":
            assert message["sessionId"] == "probe" and p["frameId"] == "probe-frame"
            assert p["options"] == {{"disableCache": True, "includeCredentials": False}}
            if mode == "timeout":
                time.sleep(3600)
            if p["url"] == "https://dv-g3b-emudhra.we-have-a-problem.com/":
                probes += 1
                if mode in ("dns", "authority"):
                    resource = {{"success": False, "netError": -105 if mode == "dns" else -202,
                                "netErrorName": "net::ERR_NAME_NOT_RESOLVED" if mode == "dns"
                                                else "net::ERR_CERT_AUTHORITY_INVALID"}}
                elif mode in ("warming", "never", "update_error") and (not updated or probes < 3):
                    resource = {{"success": True, "httpStatusCode": 200, "stream": "negative-body"}}
                else:
                    resource = {{"success": False, "netError": -206,
                                "netErrorName": "net::ERR_CERT_REVOKED"}}
            elif p["url"] == "https://pypi.org/":
                resource = {{"success": mode != "positive_error", "httpStatusCode": 200,
                            "stream": "positive-body"}}
            else:
                raise AssertionError(p)
            result = {{"resource": resource}} if mode != "malformed" else {{}}
        elif method == "Runtime.evaluate":
            assert message["sessionId"] == "components"
            assert "button.button-check-update#hfnkpimlhhgieaddgfemjhofmfblmnib" in p["expression"]
            updated = mode != "never"
            result = ({{"exceptionDetails": {{"text": "update error"}}}} if mode == "update_error"
                      else {{"result": {{"value": True}}}})
        elif method in ("IO.close", "Target.disposeBrowserContext", "x"):
            result = {{}}
        elif method == "Target.closeTarget":
            result = {{"success": True}}
        elif method == "Browser.getVersion":
            result = {{"argv": argv, "display": os.environ.get("DISPLAY"),
                      "xauthority": os.environ.get("XAUTHORITY"), "history": history,
                      "has_token": "COMPUTERD_TOKEN" in os.environ, "pid": os.getpid()}}
        reply = {{"id": message["id"]}}
        if "sessionId" in message:
            reply["sessionId"] = message["sessionId"]
        reply.update({{"error": {{"code": -32601}}}} if result is None else {{"result": result}})
        outbox.write(json.dumps(reply).encode() + b"\\0")
"""

FAKE_XVFB = """#!{python}
import os, socket, sys, time
argv = sys.argv[1:]
here = os.environ["FAKE_X11_DIR"]
report = int(argv[argv.index("-displayfd") + 1])
n = 0
while os.path.lexists(os.path.join(here, "X%d" % n)):
    n += 1  # like Xvfb: a taken number is skipped
open(os.path.join(here, "argv-%d" % n), "w").write(" ".join(argv))
open(os.path.join(here, "pid-%d" % n), "w").write(str(os.getpid()))
sock = socket.socket(socket.AF_UNIX)
sock.bind(os.path.join(here, "X%d" % n))
sock.listen()
os.write(report, b"%d\\n" % n)
time.sleep(3600)
"""


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    x11 = Path(short_tmux_root())
    monkeypatch.setenv("FAKE_X11_DIR", str(x11))
    monkeypatch.setenv("COMPUTERD_TOKEN", TOKEN)
    monkeypatch.setenv("https_proxy", "http://gateway.docker.internal:3128")
    return {
        "chrome": _executable(bin_dir / "chrome", FAKE_CHROME.format(python=sys.executable)),
        "xvfb": _executable(bin_dir / "xvfb", FAKE_XVFB.format(python=sys.executable)),
        "x11": x11,
        "root": tmp_path / "workspace",
    }


async def _serve(fakes: dict[str, Any], **kwargs: Any) -> TestServer:
    fakes["root"].mkdir(exist_ok=True)
    app = load_computerd().create_app(
        root=fakes["root"], token=TOKEN, tmux_root=short_tmux_root(),
        chrome_bin=str(fakes["chrome"]), xvfb_bin=str(fakes["xvfb"]), x11_dir=fakes["x11"],
        **kwargs,
    )
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.fixture
async def server(fakes: dict[str, Any]) -> AsyncIterator[TestServer]:
    srv = await _serve(fakes)
    try:
        yield srv
    finally:
        await srv.close()


async def _ask(session: ClientSession, server: TestServer, scope: str) -> dict[str, Any]:
    """Open the room's CDP websocket, send one command, return its answer."""
    async with session.ws_connect(server.make_url(f"/v1/browser?scope={scope}"),
                                  headers=AUTH) as ws:
        await ws.send_str(json.dumps({"id": 7, "method": "Browser.getVersion"}))
        msg = await ws.receive(timeout=10)
        assert msg.type is WSMsgType.TEXT, msg
        return json.loads(msg.data)


async def test_a_cdp_command_round_trips_through_the_pipe(server, fakes):
    async with ClientSession() as session:
        answer = await _ask(session, server, SCOPE)
    assert answer["id"] == 7
    result = answer["result"]
    assert "--remote-debugging-pipe" in result["argv"]
    assert result["display"] is not None and result["display"].startswith(":")


async def test_tls_activation_precedes_cdp_on_fresh_and_restarted_profile(
    server, fakes, monkeypatch,
):
    """An updated component can still precede verifier activation on restart."""
    monkeypatch.setenv("FAKE_TLS_MODE", "warming")
    async with ClientSession() as session:
        for _ in range(2):
            result = (await _ask(session, server, SCOPE))["result"]
            history = result["history"]
            assert history.count("Network.loadNetworkResource") == 4
            assert history.count("Target.createBrowserContext") == 4
            assert history.count("Target.disposeBrowserContext") == 4
            assert history.count("Runtime.evaluate") == 1
            client_index = history.index("Browser.getVersion")
            assert history.index("Target.disposeBrowserContext") < client_index
            assert history.index("Target.closeTarget") < client_index
            commands = [json.loads(s) for s in
                        (fakes["x11"] / f"chrome-{result['pid']}.jsonl").read_text().splitlines()]
            urls = [c["params"]["url"] for c in commands
                    if c["method"] == "Network.loadNetworkResource"]
            assert urls == ["https://dv-g3b-emudhra.we-have-a-problem.com/"] * 3 + ["https://pypi.org/"]
            assert [c["params"]["handle"] for c in commands if c["method"] == "IO.close"] == [
                "negative-body", "negative-body", "positive-body",
            ]
            stopped = await session.post(server.make_url("/v1/browser_stop"),
                                         headers=AUTH, json={"scope": SCOPE})
            assert (await stopped.json())["status"] == "ok"


@pytest.mark.parametrize("mode", [
    "dns", "authority", "positive_error", "update_error", "malformed", "timeout", "never",
])
async def test_tls_activation_failure_exposes_no_cdp_and_reaps_processes(
    server, fakes, monkeypatch, mode,
):
    monkeypatch.setenv("FAKE_TLS_MODE", mode)
    if mode in ("timeout", "never"):
        monkeypatch.setattr(sys.modules["computerd_under_test"], "CHROME_TLS_SECONDS", 0.4)
    async with ClientSession() as session:
        response = await session.get(server.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH)
        assert response.status == 200  # A successful start would attempt a websocket upgrade.
        assert (await response.json())["status"] == "unavailable"
    assert server.app["browsers"].running == {}
    (chrome_log,) = fakes["x11"].glob("chrome-*.jsonl")
    assert "Target.createBrowserContext" in chrome_log.read_text()  # the startup actually ran
    (xvfb_pid,) = fakes["x11"].glob("pid-*")
    for pid in (int(chrome_log.stem.split("-")[1]), int(xvfb_pid.read_text())):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


async def test_chrome_never_gets_a_debugging_port(server, fakes):
    """No flag opens a CDP port, so nothing in the VM can attach but
    computerd, which holds the pipe."""
    async with ClientSession() as session:
        argv = (await _ask(session, server, SCOPE))["result"]["argv"]
    assert not [a for a in argv if a.startswith("--remote-debugging-port")]
    assert not [a for a in argv if a.startswith("--remote-debugging-address")]
    assert "--headless" not in " ".join(argv)


async def test_each_room_gets_its_own_chrome_profile_and_display(server, fakes):
    async with ClientSession() as session:
        a = (await _ask(session, server, SCOPE))["result"]
        b = (await _ask(session, server, OTHER))["result"]
    assert a["pid"] != b["pid"]
    assert a["display"] != b["display"]
    root = fakes["root"]
    assert f"--user-data-dir={root / SCOPE / 'browser' / 'default'}" in a["argv"]
    assert f"--user-data-dir={root / OTHER / 'browser' / 'default'}" in b["argv"]
    assert a["xauthority"] == str(root / SCOPE / "browser" / ".Xauthority")


async def test_a_room_reconnecting_reaches_the_same_chrome(server, fakes):
    async with ClientSession() as session:
        first = (await _ask(session, server, SCOPE))["result"]["pid"]
        second = (await _ask(session, server, SCOPE))["result"]["pid"]
    assert first == second


async def test_the_display_cookie_is_the_rooms_alone(server, fakes):
    async with ClientSession() as session:
        await _ask(session, server, SCOPE)
    auth = fakes["root"] / SCOPE / "browser" / ".Xauthority"
    assert stat.S_IMODE(os.lstat(auth).st_mode) == 0o600
    data = auth.read_bytes()
    assert b"MIT-MAGIC-COOKIE-1" in data and len(data) > 30


async def test_xvfb_is_started_without_tcp_and_with_the_cookie(server, fakes):
    async with ClientSession() as session:
        display = (await _ask(session, server, SCOPE))["result"]["display"]
    argv = (fakes["x11"] / f"argv-{display.lstrip(':')}").read_text().split()
    assert argv[argv.index("-nolisten") + 1] == "tcp"
    assert argv[argv.index("-auth") + 1].endswith(f"{SCOPE}/browser/.Xauthority")


async def test_chrome_uses_the_vms_proxy_and_never_sees_the_token(server, fakes):
    async with ClientSession() as session:
        result = (await _ask(session, server, SCOPE))["result"]
    assert "--proxy-server=http://gateway.docker.internal:3128" in result["argv"]
    assert result["has_token"] is False


async def test_a_bad_token_is_refused_before_any_browser_starts(server, fakes):
    async with ClientSession() as session:
        resp = await session.get(server.make_url(f"/v1/browser?scope={SCOPE}"),
                                 headers={"Authorization": "Bearer wrong"})
        assert resp.status == 401
    assert not (fakes["root"] / SCOPE / "browser").exists()


async def test_a_bad_scope_is_denied(server, fakes):
    async with ClientSession() as session:
        resp = await session.get(server.make_url("/v1/browser?scope=../etc"), headers=AUTH)
        assert (await resp.json())["status"] == "denied"


async def test_a_second_client_for_the_same_room_is_denied(server, fakes):
    async with ClientSession() as session:
        url = server.make_url(f"/v1/browser?scope={SCOPE}")
        async with session.ws_connect(url, headers=AUTH) as ws:
            await ws.send_str(json.dumps({"id": 1, "method": "x"}))
            await ws.receive(timeout=10)
            resp = await session.get(url, headers=AUTH)
            body = await resp.json()
    assert body["status"] == "denied" and "already" in body["reason"]


async def test_stop_kills_the_rooms_chrome_and_state_says_so(server, fakes):
    async with ClientSession() as session:
        pid = (await _ask(session, server, SCOPE))["result"]["pid"]
        state = await (await session.post(server.make_url("/v1/state"), headers=AUTH,
                                          json={"scope": SCOPE})).json()
        assert state["browser"]["running"] is True
        stopped = await (await session.post(server.make_url("/v1/browser_stop"),
                                            headers=AUTH, json={"scope": SCOPE})).json()
        assert stopped["status"] == "ok"
        state = await (await session.post(server.make_url("/v1/state"), headers=AUTH,
                                          json={"scope": SCOPE})).json()
    assert state["browser"]["running"] is False
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_past_the_cap_the_least_recent_idle_browser_is_stopped(fakes):
    """SCOPE starts first but is used again after OTHER, so OTHER is the least
    recently used: eviction by start order would stop the wrong one."""
    srv = await _serve(fakes, max_browsers=2)
    try:
        async with ClientSession() as session:
            await _ask(session, srv, SCOPE)
            other = (await _ask(session, srv, OTHER))["result"]["pid"]
            await _ask(session, srv, SCOPE)
            await _ask(session, srv, THIRD)
            running = set(srv.app["browsers"].running)
    finally:
        await srv.close()
    assert running == {SCOPE, THIRD}
    with pytest.raises(ProcessLookupError):
        os.kill(other, 0)


async def test_past_the_cap_with_every_browser_attached_the_new_room_is_denied(fakes):
    srv = await _serve(fakes, max_browsers=1)
    try:
        async with ClientSession() as session:
            url = srv.make_url(f"/v1/browser?scope={SCOPE}")
            async with session.ws_connect(url, headers=AUTH) as ws:
                await ws.send_str(json.dumps({"id": 1, "method": "x"}))
                await ws.receive(timeout=10)
                resp = await session.get(srv.make_url(f"/v1/browser?scope={OTHER}"), headers=AUTH)
                body = await resp.json()
    finally:
        await srv.close()
    assert body["status"] == "denied"


async def test_an_xvfb_that_never_comes_up_is_unavailable(fakes, monkeypatch):
    fakes["xvfb"].write_text("#!/bin/sh\nsleep 1\nexit 1\n")
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session:
            resp = await session.get(srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH)
            body = await resp.json()
    finally:
        await srv.close()
    assert body["status"] == "unavailable"


def test_the_xauthority_record_has_the_xau_layout():
    """FamilyWild, no address, NO display number (libXau matches an empty
    number to any display, which Xvfb chooses after the file is written)."""
    computerd = load_computerd()
    cookie = bytes(range(16))
    record = computerd.xauthority_entry(cookie)
    assert record[:2] == b"\xff\xff"  # FamilyWild
    assert record[2:4] == b"\x00\x00"  # no address
    assert record[4:6] == b"\x00\x00"  # no display number
    assert record[6:8] == b"\x00\x12" and record[8:26] == b"MIT-MAGIC-COOKIE-1"
    assert record[26:28] == b"\x00\x10" and record[28:] == cookie


async def test_xvfb_picks_the_display_and_computerd_uses_the_one_it_reports(server, fakes):
    """computerd never chooses a number a room could predict and squat: Xvfb
    is started with `-displayfd`, skips taken numbers, and holds the one it
    reports. Chrome is pointed at that one."""
    for n in range(0, 5):
        (fakes["x11"] / f"X{n}").touch()
    async with ClientSession() as session:
        display = (await _ask(session, server, SCOPE))["result"]["display"]
    assert display == ":5"
    argv = (fakes["x11"] / "argv-5").read_text().split()
    assert "-displayfd" in argv
    assert not [a for a in argv if a.startswith(":")]


async def test_something_at_the_socket_path_that_is_not_a_socket_is_not_our_display(fakes):
    binds = ('sock = socket.socket(socket.AF_UNIX)\n'
             'sock.bind(os.path.join(here, "X%d" % n))\nsock.listen()')
    plain_file = 'open(os.path.join(here, "X%d" % n), "w").close()'
    fake = FAKE_XVFB.format(python=sys.executable)
    assert binds in fake
    fakes["xvfb"].write_text(fake.replace(binds, plain_file))
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session:
            resp = await session.get(srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH)
            body = await resp.json()
        assert body["status"] == "unavailable"
        assert srv.app["browsers"].running == {}
    finally:
        await srv.close()


async def test_chromes_stderr_lands_in_the_rooms_own_log(server, fakes):
    fakes["chrome"].write_text(
        FAKE_CHROME.format(python=sys.executable).replace(
            "argv = sys.argv[1:]", "argv = sys.argv[1:]\nsys.stderr.write('chrome said hello\\n')"
            "\nsys.stderr.flush()"))
    async with ClientSession() as session:
        await _ask(session, server, SCOPE)
    log = fakes["root"] / SCOPE / "browser" / "chrome.log"
    assert "chrome said hello" in log.read_text()
    assert stat.S_IMODE(os.lstat(log).st_mode) == 0o600


async def test_chrome_is_started_without_the_automation_flag_pages_can_read(server, fakes):
    """Driven over the pipe, Chrome reports `navigator.webdriver = true`
    unless AutomationControlled is disabled."""
    async with ClientSession() as session:
        argv = (await _ask(session, server, SCOPE))["result"]["argv"]
    assert "--disable-blink-features=AutomationControlled" in argv
    assert "--enable-automation" not in argv


async def test_two_clients_connecting_at_once_get_one_browser_between_them(
    server, fakes, monkeypatch,
):
    """The slot is claimed before the upgrade's first await: two connects in
    the same instant cannot both pass the check."""
    url = server.make_url(f"/v1/browser?scope={SCOPE}")
    # Widen the window between the check and the upgrade finishing, so a
    # claim made only after `prepare` would let both through.
    original = web.WebSocketResponse.prepare

    async def slow_prepare(self, request):
        await asyncio.sleep(0.2)
        return await original(self, request)

    monkeypatch.setattr(web.WebSocketResponse, "prepare", slow_prepare)
    async with ClientSession() as session:
        await _ask(session, server, SCOPE)  # the browser is running

        async def connect() -> str:
            try:
                ws = await session.ws_connect(url, headers=AUTH)
            except Exception:  # noqa: BLE001 - a refused upgrade is the outcome under test
                return "refused"
            await asyncio.sleep(0.3)
            await ws.close()
            return "connected"

        outcomes = await asyncio.gather(connect(), connect())
    assert sorted(outcomes) == ["connected", "refused"]


async def test_a_binary_frame_closes_the_socket_instead_of_hanging(server, fakes):
    url = server.make_url(f"/v1/browser?scope={SCOPE}")
    async with ClientSession() as session, session.ws_connect(url, headers=AUTH) as ws:
        await ws.send_bytes(b"\x00\x01")
        msg = await ws.receive(timeout=10)
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)


async def test_a_fifo_planted_at_the_log_cannot_hang_computerd(server, fakes):
    """A room owns its browser/ directory: a FIFO at chrome.log would block a
    plain open forever, under the lock every room's browser needs."""
    browser_dir = fakes["root"] / SCOPE / "browser"
    browser_dir.mkdir(parents=True)
    os.mkfifo(browser_dir / "chrome.log")
    async with ClientSession() as session:
        answer = await asyncio.wait_for(_ask(session, server, SCOPE), 10)
        await asyncio.wait_for(_ask(session, server, OTHER), 10)
    assert answer["id"] == 7
    assert stat.S_ISREG(os.lstat(browser_dir / "chrome.log").st_mode)


async def test_a_link_planted_at_the_log_is_replaced_not_written_through(server, fakes, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    browser_dir = fakes["root"] / SCOPE / "browser"
    browser_dir.mkdir(parents=True)
    (browser_dir / "chrome.log").symlink_to(victim)
    async with ClientSession() as session:
        await _ask(session, server, SCOPE)
    assert victim.read_text() == "keep me"
    assert not (browser_dir / "chrome.log").is_symlink()


async def test_a_linked_x11_directory_is_refused_and_its_target_untouched(
    fakes, tmp_path, monkeypatch,
):
    """The X directory is made and checked without following a link: a room
    that points it at /etc must not get root to open /etc to everyone."""
    target = tmp_path / "etc"
    target.mkdir(mode=0o755)
    linked = Path(short_tmux_root()) / "x11"
    linked.symlink_to(target)
    fakes["x11"] = linked
    # The fake Xvfb binds where computerd looks, so only the link refusal can
    # make this unavailable (not a display that never appears).
    monkeypatch.setenv("FAKE_X11_DIR", str(linked))
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session:
            resp = await session.get(srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH)
            body = await resp.json()
    finally:
        await srv.close()
    assert body["status"] == "unavailable"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


async def test_a_start_that_fails_after_xvfb_leaves_no_xvfb_or_slot_behind(fakes):
    """A directory planted at browser/chrome.log makes the start fail after
    Xvfb is already running: Xvfb must be stopped and no slot kept."""
    log = fakes["root"] / SCOPE / "browser" / "chrome.log"
    log.mkdir(parents=True)
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session:
            resp = await session.get(srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH)
            body = await resp.json()
        assert body["status"] == "unavailable", body
        assert srv.app["browsers"].running == {}
    finally:
        await srv.close()
    (pid_file,) = fakes["x11"].glob("pid-*")
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


async def test_an_unframed_flood_from_the_pipe_kills_that_browser_not_computerd(fakes):
    """A room's command runs as its Chrome's user and could write to Chrome's
    output pipe itself: bytes without a NUL must not grow root's memory."""
    computerd = load_computerd()
    computerd.MAX_TRANSFER_BYTES = 10_000
    fakes["chrome"].write_text(FAKE_CHROME.format(python=sys.executable).replace(
        "buffer = b\"\"", "outbox.write(b\"x\" * 50_000)\nbuffer = b\"\""))
    fakes["root"].mkdir(exist_ok=True)
    app = computerd.create_app(
        root=fakes["root"], token=TOKEN, tmux_root=short_tmux_root(),
        chrome_bin=str(fakes["chrome"]), xvfb_bin=str(fakes["xvfb"]), x11_dir=fakes["x11"])
    srv = TestServer(app)
    await srv.start_server()
    try:
        async with ClientSession() as session:
            response = await session.get(srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH)
            assert (await response.json())["status"] == "unavailable"
        assert srv.app["browsers"].running == {}
    finally:
        await srv.close()
