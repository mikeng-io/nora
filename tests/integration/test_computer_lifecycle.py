"""Generated host/controller/Unix client checks; no host sbx or production data."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from nora.infra.computer.lifecycle import HostLifecycleClient

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "lifecycle_controller", ROOT / "deploy/computer/lifecycle_controller.py")
assert spec and spec.loader
controller_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller_module)
BEARER = "synthetic-bearer"
SCOPE = "1234567890abcdef"
OTHER = "abcdef1234567890"


@pytest.fixture
def host(tmp_path, monkeypatch):
    """Strict subprocess seam: only the installed wrapper verbs are accepted."""
    monkeypatch.setenv("TEST_HOST_ROOT", str(tmp_path))
    registry = tmp_path / "registry"
    registry.mkdir()
    wrapper = tmp_path / "wrapper"
    wrapper.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["TEST_HOST_ROOT"])
args = sys.argv[1:]
with (root / "calls").open("a") as f: f.write(json.dumps(args) + "\\n")
p = root / "boxes.json"
boxes = json.loads(p.read_text()) if p.exists() else {}
if args == ["inventory"]:
    print(json.dumps({"sandboxes": [{"name": n, "status": s} for n,s in boxes.items()]}))
    sys.exit(0)
if len(args) != 3 or args[0] != "scope": sys.exit(7)
verb, scope = args[1:]
name = "nora-computer-" + scope[:12]
record = root / "registry" / (scope + ".json")
if verb == "provision":
    if name in boxes: sys.exit(9)
    boxes[name] = "running"
    record.write_text(json.dumps({"scope": scope, "name": name,
        "endpoint": "http://172.17.0.1:" + str(24000 + int(scope[:8],16)%20000),
        "instance": ""}))
elif verb == "start":
    if boxes.get(name) != "stopped": sys.exit(9)
    boxes[name] = "running"
elif verb == "stop":
    if boxes.get(name) != "running": sys.exit(9)
    boxes[name] = "stopped"
elif verb == "observe":
    r = json.loads(record.read_text())
    r["instance"] = "boot-" + str(len((root / "calls").read_text()))
    record.write_text(json.dumps(r))
elif verb == "service-start":
    if boxes.get(name) != "running": sys.exit(9)
else: sys.exit(7)
p.write_text(json.dumps(boxes))
''')
    wrapper.chmod(0o755)
    ctl = controller_module.Controller(wrapper=wrapper, state=tmp_path / "state",
        registry=registry, token=BEARER, max_running=1,
        guest_bytes=1, headroom_bytes=1, idle_seconds=1)
    # Guest HTTP behavior varies by route/identity and work state. The actual
    # authenticated guest transport remains a separate installed-host boundary.
    guest_state = {"status": "ok", "processes": [], "tmux": [], "runs": 0,
                   "browser": {"running": False}}
    def guest(scope, endpoint, route):
        if route == "identity":
            if not endpoint.get("instance"):
                raise controller_module.UnavailableError("empty boot")
            return {"scope": scope, "instance": endpoint["instance"]}
        if route == "state":
            return guest_state
        raise AssertionError(route)
    monkeypatch.setattr(ctl, "guest", guest)
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == "/proc/meminfo":
            return "MemAvailable: 8388608 kB\n"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    socket_dir = tempfile.TemporaryDirectory(dir="/tmp", prefix="ncl-")
    path = Path(socket_dir.name) / "control.sock"
    server = controller_module.Server(str(path), ctl)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield ctl, HostLifecycleClient(socket=str(path), token=BEARER), guest_state
    server.shutdown()
    server.server_close()
    path.unlink(missing_ok=True)
    socket_dir.cleanup()
    thread.join()
    ctl.process_lock.close()


async def test_first_use_serialized_reuse_capacity_and_retained_wake(host):
    ctl, client, state = host
    operation = str(uuid.uuid4())
    one, two = await asyncio.gather(*[
        client.request("acquire", SCOPE, operation) for _ in range(2)])
    assert one["status"] == two["status"] == "ok"
    assert one["instance"] == two["instance"]
    calls = [json.loads(line) for line in (ctl.state.parent / "calls").read_text().splitlines()]
    assert calls.count(["scope", "provision", SCOPE]) == 1
    denied = await client.request("acquire", OTHER, str(uuid.uuid4()))
    assert denied["status"] == "unavailable" and "capacity" in denied["reason"]
    state["runs"] = 1
    assert (await client.request("release", SCOPE, operation))["status"] == "unavailable"
    path, owned = ctl.record(SCOPE)
    owned["last_activity"] = 0
    controller_module.atomic(path, owned)
    assert not (await client.request("idle-stop", SCOPE, str(uuid.uuid4()))).get("stopped")
    state["runs"] = 0
    assert (await client.request("release", SCOPE, operation))["status"] == "ok"
    _, owned = ctl.record(SCOPE)
    owned["last_activity"] = 0
    controller_module.atomic(path, owned)
    assert (await client.request("idle-stop", SCOPE, str(uuid.uuid4()))).get("stopped") is True
    stopped = ctl.endpoint(SCOPE)
    assert stopped["instance"] == ""
    wake = await client.request("acquire", SCOPE, str(uuid.uuid4()))
    assert wake["status"] == "ok" and wake["instance"] != one["instance"]
    calls = [json.loads(line) for line in (ctl.state.parent / "calls").read_text().splitlines()]
    assert calls.count(["scope", "provision", SCOPE]) == 1
    assert ["scope", "start", SCOPE] in calls


async def test_auth_scope_corruption_and_missing_controller_fail_closed(host, tmp_path):
    ctl, client, _ = host
    bad = HostLifecycleClient(socket=client._socket, token=BEARER + "-wrong")
    assert (await bad.request("acquire", SCOPE, str(uuid.uuid4())))["status"] == "unavailable"
    assert not (tmp_path / "calls").exists()
    invalid = await client.request("acquire", "../foreign", str(uuid.uuid4()))
    assert invalid["status"] == "unavailable"
    assert not (tmp_path / "calls").exists()
    (ctl.state / f"{SCOPE}.json").write_text("broken")
    assert (await client.request("acquire", SCOPE, str(uuid.uuid4())))["status"] == "unavailable"
    missing = HostLifecycleClient(socket=str(tmp_path / "absent.sock"), token=BEARER)
    assert (await missing.request("acquire", SCOPE, str(uuid.uuid4())))["status"] == "unavailable"


async def test_elected_execution_acquires_but_maintenance_is_passive(host):
    from datetime import UTC, datetime

    from nora.app.computer_queue import ComputerAdmission
    from nora.testing import FakeComputerTaskStore, FakeStreamRedis

    ctl, client, _ = host
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
        clock=lambda: datetime.now(UTC), lifecycle=client,
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    async def work():
        assert ctl.endpoint(SCOPE)["instance"]
        return {"status": "ok"}
    row = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert row.granted
    assert (await admission.run_segment(row.task, work()))["status"] == "ok"
    row2 = await admission.submit(scope=OTHER, kind="maintenance", payload={}, origin={})
    async def passive():
        return {"status": "ok", "no_guest_effect": True}
    assert (await admission.run_segment(row2.task, passive()))["status"] == "ok"
    assert not (ctl.state / f"{OTHER}.json").exists()

async def test_failed_acquire_never_executes_and_passive_binding_never_requests(host):
    from datetime import UTC, datetime

    from nora.app.computer_queue import ComputerAdmission
    from nora.domain.computer_vms import FileScopeEndpoints
    from nora.infra.computer.scoped import ScopedComputerdRuntime
    from nora.testing import FakeComputerTaskStore, FakeStreamRedis

    ctl, client, _ = host
    assert (await client.request("acquire", SCOPE, str(uuid.uuid4())))["status"] == "ok"
    admission = ComputerAdmission(tasks=FakeComputerTaskStore(), redis=FakeStreamRedis(),
        clock=lambda: datetime.now(UTC), lifecycle=client)
    called = []
    async def work():
        called.append(True)
        return {"status": "ok"}
    row = await admission.submit(scope=OTHER, kind="native", payload={}, origin={})
    reply = await admission.run_segment(row.task, work())
    assert reply["status"] == "unavailable" and reply["no_guest_effect"]
    assert not called
    endpoint = ctl.endpoint(SCOPE)
    endpoint["instance"] = ""
    controller_module.atomic(ctl.registry / f"{SCOPE}.json", endpoint)
    runtime = ScopedComputerdRuntime(endpoints=FileScopeEndpoints(ctl.registry), token=BEARER)
    before = (ctl.state.parent / "calls").read_text()
    assert await runtime.binding(SCOPE) is None
    assert await runtime.instance(SCOPE) is None
    assert (await runtime.state(scope=SCOPE)).status.value == "unavailable"
    assert SCOPE in await runtime.registered()
    assert (ctl.state.parent / "calls").read_text() == before

async def test_restart_reconciles_owned_disk_and_rejects_missing_or_foreign(host, monkeypatch):
    ctl, client, _ = host
    op = str(uuid.uuid4())
    assert (await client.request("acquire", SCOPE, op))["status"] == "ok"
    ctl.process_lock.close()
    restarted = controller_module.Controller(wrapper=ctl.wrapper, state=ctl.state,
        registry=ctl.registry, token=BEARER, max_running=1,
        guest_bytes=1, headroom_bytes=1, idle_seconds=1)
    monkeypatch.setattr(restarted, "guest", ctl.guest)
    try:
        reply = await asyncio.to_thread(restarted.dispatch,
            {"action": "acquire", "scope": SCOPE, "operation": str(uuid.uuid4())})
        assert reply["status"] == "ok"
        boxes = ctl.state.parent / "boxes.json"
        boxes.write_text("{}")
        with pytest.raises(controller_module.UnavailableError, match="disk missing"):
            restarted.dispatch({"action": "acquire", "scope": SCOPE,
                                "operation": str(uuid.uuid4())})
        boxes.write_text(json.dumps({"nora-computer-" + OTHER[:12]: "running"}))
        with pytest.raises(controller_module.UnavailableError, match="unowned"):
            restarted.dispatch({"action": "acquire", "scope": OTHER,
                                "operation": str(uuid.uuid4())})
    finally:
        restarted.process_lock.close()


async def test_unknown_inventory_and_guest_read_never_mean_capacity_or_quiet(host, monkeypatch):
    ctl, client, state = host
    operation = str(uuid.uuid4())
    assert (await client.request("acquire", SCOPE, operation))["status"] == "ok"
    state.pop("runs")
    assert (await client.request("release", SCOPE, operation))["status"] == "unavailable"
    real_command = ctl.command
    def broken(*args):
        return "{}" if args == ("inventory",) else real_command(*args)
    monkeypatch.setattr(ctl, "command", broken)
    refused = await client.request("acquire", OTHER, str(uuid.uuid4()))
    assert refused["status"] == "unavailable" and "inventory" in refused["reason"]
    assert not (ctl.state / f"{OTHER}.json").exists()


async def test_idle_stop_and_incoming_acquire_share_real_controller_lock(host, monkeypatch):
    ctl, client, _ = host
    op = str(uuid.uuid4())
    assert (await client.request("acquire", SCOPE, op))["status"] == "ok"
    assert (await client.request("release", SCOPE, op))["status"] == "ok"
    path, owned = ctl.record(SCOPE)
    owned["last_activity"] = 0
    controller_module.atomic(path, owned)
    stopping, proceed = threading.Event(), threading.Event()
    real_command = ctl.command
    def held_stop(*args):
        if args == ("scope", "stop", SCOPE):
            stopping.set()
            assert proceed.wait(5)
        return real_command(*args)
    monkeypatch.setattr(ctl, "command", held_stop)
    stop = asyncio.create_task(client.request("idle-stop", SCOPE, str(uuid.uuid4())))
    assert await asyncio.to_thread(stopping.wait, 5)
    assert ctl.lock.locked()
    acquire = asyncio.create_task(client.request("acquire", SCOPE, str(uuid.uuid4())))
    try:
        await asyncio.sleep(0.05)  # let the second HTTP handler reach the held transition
        assert not acquire.done()
        assert ctl.endpoint(SCOPE)["instance"] == ""
    finally:
        proceed.set()
    assert (await stop)["stopped"]
    assert (await acquire)["status"] == "ok"


def test_guest_authentication_scope_and_boot_are_verified(host):
    import http.server

    ctl, _, _ = host
    answer = {"scope": SCOPE, "instance": "boot-synthetic"}
    seen = []
    class Guest(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            data = json.dumps(answer).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *_args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Guest)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = {"endpoint": f"http://127.0.0.1:{server.server_port}",
                "instance": "boot-synthetic"}
    try:
        guest = controller_module.Controller.guest
        assert guest(ctl, SCOPE, endpoint, "identity") == answer
        assert seen == [("/v1/identity", "Bearer " + BEARER)]
        answer["scope"] = OTHER
        with pytest.raises(controller_module.UnavailableError, match="identity mismatch"):
            guest(ctl, SCOPE, endpoint, "identity")
        answer.update(scope=SCOPE, instance="")
        with pytest.raises(controller_module.UnavailableError, match="identity mismatch"):
            guest(ctl, SCOPE, endpoint, "identity")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

async def test_durable_queue_executor_acquires_before_dispatch(host):
    from datetime import UTC, datetime

    from nora.app.computer_queue import ComputerAdmission, ComputerQueueRunner
    from nora.testing import FakeComputerTaskStore, FakeStreamRedis

    ctl, client, _ = host
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    def clock():
        return datetime.now(UTC)
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock)
    submitted = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    seen = []
    async def dispatch(row):
        seen.append(ctl.endpoint(row.scope)["instance"])
        return {"status": "ok"}
    runner = ComputerQueueRunner(tasks=store, redis=redis, clock=clock,
        dispatch={"native": dispatch}, lifecycle=client,
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    await runner._execute("synthetic-stream", "1-0", submitted.task)
    assert seen and seen[0]
    assert (await store.get(submitted.task.id)).status == "done"
    _, owned = ctl.record(SCOPE)
    assert owned["lease"] == ""

async def test_partial_ready_publication_recovers_without_another_create(host):
    ctl, client, _ = host
    op = str(uuid.uuid4())
    assert (await client.request("acquire", SCOPE, op))["status"] == "ok"
    path, owned = ctl.record(SCOPE)
    owned["phase"] = "provisioned"
    controller_module.atomic(path, owned)
    endpoint = ctl.endpoint(SCOPE)
    endpoint["instance"] = ""
    controller_module.atomic(ctl.registry / f"{SCOPE}.json", endpoint)
    recovered = await client.request("acquire", SCOPE, str(uuid.uuid4()))
    assert recovered["status"] == "ok" and recovered["instance"]
    calls = [json.loads(line) for line in (ctl.state.parent / "calls").read_text().splitlines()]
    assert calls.count(["scope", "provision", SCOPE]) == 1


async def test_registry_only_scopes_are_observed_without_adoption_and_idle_stopped(host):
    ctl, client, _ = host
    operation = str(uuid.uuid4())
    assert (await client.request("acquire", SCOPE, operation))["status"] == "ok"
    assert (await client.request("release", SCOPE, operation))["status"] == "ok"
    path, _ = ctl.record(SCOPE)
    path.unlink()  # pre-controller registry remains the existing host authority
    registry = ctl.registry / f"{SCOPE}.json"
    import os
    os.utime(registry, (0, 0))
    observed = await client.request("observe", SCOPE, str(uuid.uuid4()))
    assert observed["status"] == "ok" and observed["idle_eligible"] is True
    assert not path.exists()  # a passive read neither adopts nor wakes
    assert (await client.request("idle-stop", SCOPE, str(uuid.uuid4()))).get("stopped") is True
    path.unlink()
    stopped = await client.request("observe", SCOPE, str(uuid.uuid4()))
    assert stopped["status"] == "ok" and stopped["state"] == "stopped"
    assert not path.exists()
    assert (await client.request("acquire", SCOPE, str(uuid.uuid4())))["status"] == "ok"


@pytest.mark.parametrize("browser, expected", [
    ({"running": False}, True),
    ({"running": True, "pending_actions": 0}, True),
    ({"running": True, "pending_actions": 1}, False),
    ({"running": True}, False),
    ({"pending_actions": 0}, False),
])
def test_quiet_matches_guest_browser_payload_shapes(host, browser, expected):
    ctl, _, state = host
    state["browser"] = browser
    endpoint = {"instance": "boot-synthetic"}
    assert ctl.quiet(SCOPE, endpoint) is expected
    assert ctl.quiet(SCOPE, endpoint, browser=True) is (
        expected and browser.get("running") is False)


async def test_empty_sweeps_claim_only_when_host_idle_is_eligible(host, monkeypatch):
    from datetime import UTC, datetime

    from nora.app.computer_queue import ComputerAdmission
    from nora.testing import FakeComputerTaskStore, FakeStreamRedis
    from tests.unit.test_scoped_computer import _register, _wired

    ctl, client, _ = host
    operation = str(uuid.uuid4())
    assert (await client.request("acquire", SCOPE, operation))["status"] == "ok"
    assert (await client.request("release", SCOPE, operation))["status"] == "ok"
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
        clock=lambda: datetime.now(UTC), lifecycle=client,
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    rig = _wired(monkeypatch, tasks=store, segments=admission, lifecycle=client)
    _register(rig.runtime, scope=SCOPE)
    assert await rig.manager.sweep() == 0
    assert not store.rows
    path, owned = ctl.record(SCOPE)
    owned["last_activity"] = 0
    controller_module.atomic(path, owned)
    assert await rig.manager.sweep() == 0
    assert len(store.rows) == 1
    row = next(iter(store.rows.values()))
    assert row.kind == "maintenance" and row.status == "done"
    assert ctl.inventory()["nora-computer-" + SCOPE[:12]] == "stopped"
