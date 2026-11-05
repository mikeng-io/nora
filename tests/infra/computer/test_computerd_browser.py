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
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from nora.domain.computer import SandboxStatus, StateResult, anything_running, computer_scope
from tests.contract.test_sandbox_runtime_contract import TOKEN, load_computerd, short_tmux_root

SCOPE = computer_scope("signal", "room-a")
OTHER = computer_scope("signal", "room-b")
THIRD = computer_scope("signal", "room-c")
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FAKE_CHROME = """#!{python}
import json, os, sys, threading, time
argv = sys.argv[1:]
mode = os.environ.get("FAKE_TLS_MODE", "ready")
updated, probes = False, 0
history = []
log = os.path.join(os.environ["FAKE_X11_DIR"], "chrome-%d.jsonl" % os.getpid())
inbox, outbox = os.fdopen(3, "rb", buffering=0), os.fdopen(4, "wb", buffering=0)
pages = {{"initial": True}}

def emit(event):
    outbox.write(json.dumps(event).encode() + b"\\0")
    outbox.flush()

# Page targets a page script/timer creates on a clock — FAKE_POPUPS is a
# comma list of targetIds (or `type:id` for a non-page target), fired
# FAKE_POPUP_DELAY seconds after Chrome starts: the popup that lands while
# NO websocket client is attached.
popups = os.environ.get("FAKE_POPUPS", "")
if popups:
    def _fire():
        time.sleep(float(os.environ.get("FAKE_POPUP_DELAY", "0.5")))
        for raw in popups.split(","):
            if not raw:
                continue
            kind, _, tid = raw.partition(":")
            if not tid:
                kind, tid = "page", kind
            if kind == "page":
                pages[tid] = True
            emit({{"method": "Target.targetCreated",
                  "params": {{"targetInfo": {{"targetId": tid, "type": kind}}}}}})
    threading.Thread(target=_fire, daemon=True).start()
# Chrome's crash reporter double-forks out of the browser process tree.
# Model that real failure shape: stopping the browser group cannot kill it.
if os.environ.get("FAKE_DETACHED_HELPER") == "1":
    helper_path = os.path.join(os.environ["FAKE_X11_DIR"], "helper-%d.pid" % os.getpid())
    child = os.fork()
    if child == 0:
        os.setsid()
        if os.fork():
            os._exit(0)
        os.close(3)
        os.close(4)
        with open(helper_path, "w") as marker:
            marker.write(str(os.getpid()))
        time.sleep(3600)
        os._exit(0)
    os.waitpid(child, 0)
    while not os.path.exists(helper_path):
        time.sleep(0.001)
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
        elif method == "Target.getTargets":
            result = {{"targetInfos": [{{"targetId": tid, "type": "page"}}
                                      for tid in pages]}}
        elif method == "Target.setDiscoverTargets":
            result = {{}}
        elif method == "Target.closeTarget":
            pages.pop(p["targetId"], None)
            result = {{"success": True}}
        elif method == "Generated.block":
            time.sleep(3600)
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
    # A fresh anonymous profile per browser lifetime: `browser/profile-*`,
    # never a resurrected `default`.
    a_profile = next(a for a in a["argv"] if a.startswith("--user-data-dir="))
    b_profile = next(a for a in b["argv"] if a.startswith("--user-data-dir="))
    assert a_profile.startswith(
        f"--user-data-dir={root / SCOPE / 'browser' / 'profile-'}")
    assert b_profile.startswith(
        f"--user-data-dir={root / OTHER / 'browser' / 'profile-'}")
    assert a_profile != b_profile
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


def _chrome_commands(fakes: dict[str, Any]) -> list[dict[str, Any]]:
    """Every CDP message computerd sent Chrome, from the fake's own log."""
    return [json.loads(line) for path in fakes["x11"].glob("chrome-*.jsonl")
            for line in path.read_text().splitlines()]


async def test_a_page_target_born_while_no_client_is_attached_is_closed(fakes, monkeypatch):
    """Chrome outlives its websocket: a page target a timer creates AFTER
    the client disconnects is supervised inside the browser's own
    lifetime — computerd saw the target event on the pipe and closed the
    orphan, so the four-target cap holds between objectives, never only
    inside `acquire` or on the 30s sweep."""
    monkeypatch.setenv("FAKE_POPUPS", "popup-1,popup-2,popup-3,popup-4,popup-5")
    monkeypatch.setenv("FAKE_POPUP_DELAY", "1.0")
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session:
            async with session.ws_connect(
                    srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH) as ws:
                await ws.send_str(json.dumps({"id": 7, "method": "Browser.getVersion"}))
                assert (await ws.receive(timeout=10)).type is WSMsgType.TEXT
            # Past the popup delay, with the client detached: five new page
            # targets — MORE than the cap — were born ownerless.
            await asyncio.sleep(2.0)
    finally:
        await srv.close()
    closes = [c["params"]["targetId"] for c in _chrome_commands(fakes)
              if c["method"] == "Target.closeTarget"]
    for n in range(1, 6):
        assert f"popup-{n}" in closes, closes


async def test_a_page_target_born_while_attached_is_the_clients_to_own(
        fakes, monkeypatch):
    """Attached, the one client's own watch owns popups: the event is
    relayed to it, and computerd does NOT close behind its back."""
    monkeypatch.setenv("FAKE_POPUPS", "popup-a")
    monkeypatch.setenv("FAKE_POPUP_DELAY", "1.0")
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session, session.ws_connect(
                srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH) as ws:
            await ws.send_str(json.dumps({"id": 7, "method": "Browser.getVersion"}))
            assert (await ws.receive(timeout=10)).type is WSMsgType.TEXT
            seen = None
            for _ in range(25):
                try:
                    msg = await ws.receive(timeout=1)
                except TimeoutError:
                    continue
                if msg.type is not WSMsgType.TEXT:
                    continue
                frame = json.loads(msg.data)
                if frame.get("method") == "Target.targetCreated":
                    seen = frame
                    break
            assert seen is not None  # the event WAS relayed to the client
            assert seen["params"]["targetInfo"]["targetId"] == "popup-a"
    finally:
        await srv.close()
    closes = [c["params"]["targetId"] for c in _chrome_commands(fakes)
              if c["method"] == "Target.closeTarget"]
    assert "popup-a" not in closes


async def test_a_non_page_target_born_clientless_is_not_a_popup(fakes, monkeypatch):
    """The cap counts PAGE targets: a worker/other target born clientless
    is nobody's tab and is left alone."""
    monkeypatch.setenv("FAKE_POPUPS", "worker:worker-1")
    monkeypatch.setenv("FAKE_POPUP_DELAY", "1.0")
    srv = await _serve(fakes)
    try:
        async with ClientSession() as session:
            async with session.ws_connect(
                    srv.make_url(f"/v1/browser?scope={SCOPE}"), headers=AUTH) as ws:
                await ws.send_str(json.dumps({"id": 7, "method": "Browser.getVersion"}))
                assert (await ws.receive(timeout=10)).type is WSMsgType.TEXT
            await asyncio.sleep(2.0)
    finally:
        await srv.close()
    closes = [c["params"]["targetId"] for c in _chrome_commands(fakes)
              if c["method"] == "Target.closeTarget"]
    assert "worker-1" not in closes


class _FakeBrowser:
    """A stopped-or-alive browser shape `Browsers._reap` reads."""

    def __init__(self, *, alive: bool, last_used: float) -> None:
        self.client = None
        self.last_used = last_used
        self.chrome = None
        self.xvfb = None
        self._alive = alive
        self.stops = 0

    @property
    def alive(self) -> bool:
        return self._alive

    async def stop(self) -> None:
        self.stops += 1


class _FakeComputer:
    """The one `Computerd` method `reap` reads: is a `run` in flight."""

    def __init__(self, busy: bool = False) -> None:
        self._busy = busy

    def busy(self, scope: str) -> bool:
        return self._busy


async def test_scoped_idle_reap_belongs_to_the_app_not_the_guest() -> None:
    """On a scoped VM the guest's idle policy is OFF: an idle browser with
    no client is never closed by `reap` — the app's maintenance segment
    (through `browser_stop`) is the ONE lifecycle owner. Dead processes
    are still recovered, and a shared VM's idle policy still holds."""
    import time

    from tests.contract.test_sandbox_runtime_contract import load_computerd

    computerd = load_computerd()
    browsers = computerd.Browsers(
        _FakeComputer(), idle_seconds=0.0, idle_reap=False)
    idle = _FakeBrowser(alive=True, last_used=time.monotonic() - 1000)
    dead = _FakeBrowser(alive=False, last_used=time.monotonic())
    browsers.running[SCOPE] = idle
    browsers.running[OTHER] = dead
    await browsers.reap()
    # Dead recovery still happens; the live idle browser is SPARED.
    assert browsers.running.get(SCOPE) is idle and idle.stops == 0
    assert browsers.running.get(OTHER) is None and dead.stops == 1

    # Positive control: the same idle browser on a SHARED VM reaps.
    shared = computerd.Browsers(
        _FakeComputer(), idle_seconds=0.0, idle_reap=True)
    gone = _FakeBrowser(alive=True, last_used=time.monotonic() - 1000)
    shared.running[SCOPE] = gone
    await shared.reap()
    assert shared.running.get(SCOPE) is None and gone.stops == 1


async def test_an_idle_reap_never_closes_beside_a_running_run() -> None:
    """A `run` call in flight IS an owner: even on a shared VM, `reap`
    spares an idle browser while its scope reports busy, and only closes
    it once the work is gone (positive control)."""
    import time

    computerd = load_computerd()
    computer = _FakeComputer(busy=True)
    browsers = computerd.Browsers(
        computer, idle_seconds=0.0, idle_reap=True)
    spared = _FakeBrowser(alive=True, last_used=time.monotonic() - 1000)
    browsers.running[SCOPE] = spared
    await browsers.reap()
    assert browsers.running.get(SCOPE) is spared and spared.stops == 0
    computer._busy = False
    await browsers.reap()
    assert browsers.running.get(SCOPE) is None and spared.stops == 1


def test_a_scoped_computerd_disables_its_own_idle_reap(tmp_path):
    """Wiring, not just the flag: a bound-scope computerd builds `Browsers`
    with the guest idle policy OFF (the app's maintenance segment is the
    one owner), while an unscoped VM keeps its reaper."""
    computerd = load_computerd()
    scoped = computerd.create_app(
        root=tmp_path / "s", token=TOKEN, isolate_scopes=False,
        tmux_root=tmp_path / "s-tmux", bound_scope=SCOPE, boot_id="b1")
    shared = computerd.create_app(
        root=tmp_path / "h", token=TOKEN, isolate_scopes=False,
        tmux_root=tmp_path / "h-tmux")
    assert scoped["browsers"].idle_reap is False
    assert shared["browsers"].idle_reap is True


async def test_lost_browser_command_transport_drains_before_quiet(server, fakes):
    """A real websocket/pipe carries an unanswered fake-Chrome command.
    Disconnect stops that guest process before its pending work is quiet.
    A completed command is the positive control: it retains the browser.
    """
    async with ClientSession() as session:
        version = await _ask(session, server, SCOPE)
        pid = version["result"]["pid"]
        os.kill(pid, 0)
        async with session.post(server.make_url("/v1/state"),
                                headers=AUTH, json={"scope": SCOPE}) as response:
            state = await response.json()
        assert state["browser"]["running"] and state["browser"]["pending_actions"] == 0
        ws = await session.ws_connect(server.make_url(f"/v1/browser?scope={SCOPE}"),
                                      headers=AUTH)
        await ws.send_json({"id": 42, "method": "Generated.block"})
        log = fakes["x11"] / f"chrome-{pid}.jsonl"
        async with asyncio.timeout(5):
            while "Generated.block" not in log.read_text():  # noqa: ASYNC110 - child process log
                await asyncio.sleep(0.01)
        async with session.post(server.make_url("/v1/state"),
                                headers=AUTH, json={"scope": SCOPE}) as response:
            state = await response.json()
        assert state["browser"]["pending_actions"] == 1
        assert anything_running(StateResult(
            status=SandboxStatus.OK, tmux=(), processes=(), runs=0,
            browser=state["browser"])) is True
        await ws.close()
        for _ in range(100):
            async with session.post(server.make_url("/v1/state"),
                                    headers=AUTH, json={"scope": SCOPE}) as response:
                state = await response.json()
            if not state["browser"]["running"]:
                break
            await asyncio.sleep(0.01)
        assert state["browser"]["running"] is False
        assert anything_running(StateResult(
            status=SandboxStatus.OK, tmux=(), processes=(), runs=0,
            browser=state["browser"])) is False
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper/process ownership")
async def test_browser_retirement_leaves_no_detached_crash_reporter(server, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_DETACHED_HELPER", "1")
    helper_pid = None
    try:
        async with ClientSession() as session:
            pid = (await _ask(session, server, SCOPE))["result"]["pid"]
            os.kill(pid, 0)  # positive control: the browser really started
            marker = fakes["x11"] / f"helper-{pid}.pid"
            assert marker.exists(), "the detached-helper positive control did not run"
            helper_pid = int(marker.read_text())
            os.kill(helper_pid, 0)
            raw = await asyncio.to_thread(Path(f"/proc/{helper_pid}/stat").read_text)
            parent = int(raw.rsplit(")", 1)[1].split()[1])
            browser = server.app["browsers"].running[SCOPE]
            assert parent == browser.chrome.pid, "Chrome's helper escaped its lifetime owner"
            stopped = await (await session.post(
                server.make_url("/v1/browser_stop"), headers=AUTH,
                json={"scope": SCOPE})).json()
            assert stopped["status"] == "ok"
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            detached_alive = False
            if helper_pid is not None:
                try:
                    os.kill(helper_pid, 0)
                    detached_alive = True
                except ProcessLookupError:
                    pass
            assert not detached_alive, "a detached Chrome helper survives browser retirement"
    finally:
        if helper_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(helper_pid, 9)  # only this generated fixture's recorded process
