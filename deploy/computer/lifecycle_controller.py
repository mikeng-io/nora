#!/usr/bin/env python3
"""Authenticated Unix lifecycle service. sbx is invoked only by nora-computer.

A single serialized transition protects capacity and acquire/stop. Durable leases
have no expiry: controller/app loss cannot make a long guest call safe to stop.
"""
from __future__ import annotations

import argparse
import fcntl
import hmac
import http.client
import http.server
import json
import os
import re
import socketserver
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


class UnavailableError(Exception):
    pass


def atomic(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        json.dump(body, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Controller:
    def __init__(self, *, wrapper: Path, state: Path, registry: Path,
                 token: str, max_running: int, guest_bytes: int,
                 headroom_bytes: int, idle_seconds: float) -> None:
        if not token or min(max_running, guest_bytes, headroom_bytes, idle_seconds) <= 0:
            raise ValueError("explicit positive capacity, headroom, idle and bearer required")
        self.wrapper, self.state, self.registry = wrapper, state, registry
        self.token = token
        self.max_running, self.guest_bytes = max_running, guest_bytes
        self.headroom_bytes, self.idle_seconds = headroom_bytes, idle_seconds
        self.lock = threading.Lock()
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process_lock = (state / "controller.lock").open("a")
        fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def command(self, *args: str) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed wrapper verbs and validated hex scope
                [str(self.wrapper), *args], capture_output=True,
                                    text=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnavailableError("wrapper unavailable or timed out") from exc
        if result.returncode:
            raise UnavailableError(f"wrapper {args[0]} failed ({result.returncode})")
        return result.stdout

    def inventory(self) -> dict[str, str]:
        try:
            raw = json.loads(self.command("inventory"))["sandboxes"]
            if not isinstance(raw, list):
                raise ValueError()
            boxes = {}
            for item in raw:
                name, status = item["name"], item["status"]
                if (not isinstance(name, str) or not name or name in boxes
                        or status not in {"running", "stopped"}):
                    raise ValueError()
                boxes[name] = status
            return boxes
        except (KeyError, TypeError, ValueError) as exc:
            raise UnavailableError("unreadable host inventory") from exc

    def capacity(self, boxes: dict[str, str], scope: str) -> None:
        # Count ALL running VMs, including unmanaged/eval VMs. The lock covers
        # this measurement through readiness; no parallel start spends it twice.
        reservations = 0
        for record in self.state.glob("*.json"):
            try:
                owned = json.loads(record.read_text())
                if (owned["scope"] != scope and boxes.get(owned["name"]) != "running"
                        and owned["phase"] in {"creating", "provisioned", "starting"}):
                    reservations += 1
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise UnavailableError("unreadable capacity reservation") from exc
        if sum(s == "running" for s in boxes.values()) + reservations >= self.max_running:
            raise UnavailableError("host VM capacity reached")
        try:
            available = next(int(line.split()[1]) * 1024 for line in
                             Path("/proc/meminfo").read_text().splitlines()
                             if line.startswith("MemAvailable:"))
        except (OSError, ValueError, StopIteration, IndexError) as exc:
            raise UnavailableError("host memory headroom unreadable") from exc
        if available < self.guest_bytes * (1 + reservations) + self.headroom_bytes:
            raise UnavailableError("host memory headroom exhausted")

    def record(self, scope: str) -> tuple[Path, dict | None]:
        path = self.state / f"{scope}.json"
        try:
            body = json.loads(path.read_text())
        except FileNotFoundError:
            return path, None
        except (OSError, ValueError) as exc:
            raise UnavailableError("corrupt ownership record") from exc
        if (not isinstance(body, dict) or body.get("scope") != scope
                or body.get("name") != "nora-computer-" + scope[:12]
                or not isinstance(body.get("operation"), str)
                or not isinstance(body.get("lease"), str)
                or body.get("phase") not in {
                    "reserved", "creating", "provisioned", "starting",
                    "ready", "stopping", "stopped"}
                or type(body.get("last_activity")) not in {int, float}):
            raise UnavailableError("conflicting ownership record")
        return path, body

    def endpoint(self, scope: str) -> dict:
        try:
            body = json.loads((self.registry / f"{scope}.json").read_text())
        except (OSError, ValueError) as exc:
            raise UnavailableError("scope registry unavailable") from exc
        host = os.environ.get("NORA_COMPUTER_HOST_IP", "172.17.0.1")
        expected = f"http://{host}:{24000 + int(scope[:8], 16) % 20000}"
        if (not isinstance(body, dict) or body.get("scope") != scope
                or body.get("name") != "nora-computer-" + scope[:12]
                or body.get("endpoint") != expected):
            raise UnavailableError("conflicting scope registry")
        return body

    def guest(self, scope: str, record: dict, route: str) -> dict:
        if not record.get("instance"):
            raise UnavailableError("scope has no authenticated boot")
        data = None if route == "identity" else json.dumps({"scope": scope}).encode()
        endpoint = urlsplit(record["endpoint"])
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=10)
        try:
            connection.request("GET" if route == "identity" else "POST", "/v1/" + route,
                body=data, headers={"Authorization": "Bearer " + self.token,
                    "X-Computerd-Instance": record["instance"],
                    "Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise UnavailableError("guest refused authenticated read")
            body = json.loads(response.read())
        except Exception as exc:
            raise UnavailableError("authenticated guest read failed") from exc
        finally:
            connection.close()
        if not isinstance(body, dict):
            raise UnavailableError("unreadable guest state")
        if route == "identity" and (body.get("scope") != scope or
                                     body.get("instance") != record["instance"]):
            raise UnavailableError("guest identity mismatch")
        return body

    def quiet(self, scope: str, endpoint: dict, *, browser: bool = False) -> bool:
        self.guest(scope, endpoint, "identity")
        state = self.guest(scope, endpoint, "state")
        # Explicit presence/type is required; omitted fields never mean quiet.
        return (state.get("status") == "ok" and state.get("processes") == []
                and state.get("tmux") == [] and type(state.get("runs")) is int
                and state["runs"] == 0 and isinstance(state.get("browser"), dict)
                and type(state["browser"].get("pending_actions")) is int
                and state["browser"]["pending_actions"] == 0
                and (not browser or state["browser"].get("running") is False))

    def dispatch(self, body: dict) -> dict:
        if (not isinstance(body, dict) or set(body) != {"action", "scope", "operation"}
                or body.get("action") not in {"acquire", "release", "idle-stop", "observe"}
                or not isinstance(body.get("scope"), str)
                or re.fullmatch(r"[0-9a-f]{16,64}", body["scope"]) is None):
            raise ValueError("invalid lifecycle request")
        try:
            uuid.UUID(body["operation"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid operation") from exc
        scope, operation, action = body["scope"], body["operation"], body["action"]
        with self.lock:
            path, owned = self.record(scope)
            boxes = self.inventory()
            name = "nora-computer-" + scope[:12]
            status = boxes.get(name, "absent")
            if action == "observe":
                if owned is None or status not in {"running", "stopped"}:
                    raise UnavailableError("owned VM state unavailable")
                return {"status": "ok", "state": status}
            if action == "acquire":
                if owned is None:
                    registered = (self.registry / f"{scope}.json").exists()
                    if registered:
                        endpoint = self.endpoint(scope)  # existing host ownership authority
                        if status not in {"running", "stopped"}:
                            raise UnavailableError("registered disk missing or unknown")
                        if status == "running" and not self.quiet(scope, endpoint):
                            raise UnavailableError("registered guest has not drained")
                        phase = "ready"
                    else:
                        if status != "absent":
                            raise UnavailableError("unowned sandbox conflict")
                        self.capacity(boxes, scope)
                        phase = "reserved"
                    owned = {"scope": scope, "name": name, "operation": operation,
                             "lease": "", "phase": phase, "last_activity": time.time()}
                    atomic(path, owned)
                if status == "absent" and owned.get("phase") != "reserved":
                    raise UnavailableError("owned disk missing; refusing recreation")
                if (owned.get("lease") not in (None, "", operation)
                        and owned.get("phase") not in {
                            "reserved", "creating", "provisioned", "starting"}
                        and status != "stopped"
                        and (status != "running" or not self.quiet(scope, self.endpoint(scope)))):
                    raise UnavailableError("previous operation has not drained")
                if owned.get("phase") == "stopping":
                    if status != "stopped":
                        raise UnavailableError("uncertain stop requires reconciliation")
                    owned.update(phase="stopped", lease="")
                    atomic(path, owned)
                if status != "running":
                    self.capacity(boxes, scope)
                # Persist exclusion before any start. An ambiguous failure leaves
                # this record for reconciliation, never deletes the sandbox.
                owned["lease"] = operation
                atomic(path, owned)
                if status == "absent" or owned.get("phase") in {"reserved", "creating"}:
                    owned["phase"] = "creating"
                    atomic(path, owned)
                    self.command("scope", "provision", scope)
                    owned["phase"] = "provisioned"
                    atomic(path, owned)
                elif status == "stopped":
                    owned["phase"] = "starting"
                    atomic(path, owned)
                    endpoint = self.endpoint(scope)
                    endpoint["instance"] = ""
                    atomic(self.registry / f"{scope}.json", endpoint)
                    self.command("scope", "start", scope)
                endpoint = self.endpoint(scope)
                if status != "running" or not endpoint.get("instance"):
                    self.command("scope", "service-start", scope)
                    until = time.monotonic() + 45
                    while True:
                        try:
                            self.command("scope", "observe", scope)
                            break
                        except UnavailableError:
                            if time.monotonic() >= until:
                                raise UnavailableError("guest startup timed out") from None
                            time.sleep(0.5)
                    endpoint = self.endpoint(scope)
                self.guest(scope, endpoint, "identity")
                owned.update(phase="ready", last_activity=time.time())
                atomic(path, owned)
                return {"status": "ok", "instance": endpoint["instance"]}
            if owned is None:
                raise UnavailableError("no owned scope")
            if action == "release":
                if owned.get("lease") != operation:
                    raise UnavailableError("operation does not own lease")
                if not self.quiet(scope, self.endpoint(scope)):
                    raise UnavailableError("guest has not drained")
                owned.update(lease="", last_activity=time.time())
                atomic(path, owned)
                return {"status": "ok"}
            if status == "stopped":
                return {"status": "ok", "stopped": True}
            if (status != "running" or time.time() - owned["last_activity"] < self.idle_seconds
                    or not self.quiet(scope, self.endpoint(scope), browser=True)):
                return {"status": "nothing_found", "stopped": False}
            # App holds the elected SQL maintenance owner. A previous abandoned
            # lease is cleared only with this election AND positive guest quiet.
            endpoint = self.endpoint(scope)
            endpoint["instance"] = ""
            atomic(self.registry / f"{scope}.json", endpoint)
            owned.update(phase="stopping", lease="")
            atomic(path, owned)
            self.command("scope", "stop", scope)
            if self.inventory().get(name) != "stopped":
                raise UnavailableError("VM stop unconfirmed")
            owned["phase"] = "stopped"
            atomic(path, owned)
            return {"status": "ok", "stopped": True}


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, controller: Controller):
        self.controller = controller
        super().__init__(path, Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        ctl = self.server.controller
        code = 200
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + ctl.token):
            code, result = 401, {"status": "unavailable", "reason": "authentication failed"}
        else:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/lifecycle" or not 0 < length <= 1024:
                    raise ValueError("invalid request")
                result = ctl.dispatch(json.loads(self.rfile.read(length)))
            except (ValueError, TypeError):
                code, result = 400, {"status": "unavailable", "reason": "invalid request"}
            except UnavailableError as exc:
                result = {"status": "unavailable", "reason": str(exc)}
        data = json.dumps(result).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    import tomllib
    settings = tomllib.loads(Path(args.settings).read_text())
    token = os.environ.get("COMPUTERD_TOKEN", "")
    controller = Controller(wrapper=Path(settings["wrapper"]), state=Path(settings["state"]),
        registry=Path(settings["registry"]), token=token, max_running=settings["max_running"],
        guest_bytes=settings["guest_bytes"], headroom_bytes=settings["headroom_bytes"],
        idle_seconds=settings["idle_seconds"])
    path = Path(settings["socket"])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    path.unlink(missing_ok=True)  # exclusive controller lock acquired first
    with Server(str(path), controller) as server:
        path.chmod(0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
