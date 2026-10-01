"""`deploy/honcho-closed-probe.sh` reads the three outcomes apart (#633, DEC-255).

The probe is the read-back for the one property that matters here: Honcho
answers with no authentication, so what protects it is where it listens. A
probe that reads a dead Honcho, or its own blind eye, as "closed" would certify
the exposure it exists to find, so the cases below are the ones that tell PASS
from FAIL from ERROR, each driven through stub `docker`, `curl`, `hostname`
and `tailscale` binaries that behave like the real ones on the two properties
the probe reads: an HTTP status per URL, and 000 for "no answer".

What these cannot show is the host: that a throwaway container on the default
bridge is in fact refused by a real Honcho is the runbook's read-back, run on
norty, and is UNVERIFIED until it is.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "honcho-closed-probe.sh"

GATEWAY = "172.17.0.1"
LAN = "192.168.1.10"
TAILNET = "100.64.0.9"

# Each stub looks its answer up in a file named for the URL, so a case states
# only the URLs it cares about; any URL it does not state is 000, a refusal.
_DOCKER = r"""#!/bin/bash
key() { printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'; }
case "$1" in
  network) echo "${STUB_GATEWAY:-172.17.0.1}"; exit 0 ;;
  run)  kind=bridge ;;
  exec) kind=exec ;;
  *) exit 1 ;;
esac
url=""; for a in "$@"; do url="$a"; done
f="$STUB_DIR/$kind.$(key "$url")"
if [ -f "$f" ]; then cat "$f"; else printf 000; fi
"""
_CURL = r"""#!/bin/bash
key() { printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'; }
url=""; for a in "$@"; do url="$a"; done
f="$STUB_DIR/host.$(key "$url")"
if [ -f "$f" ]; then cat "$f"; else printf 000; fi
"""
_HOSTNAME = r"""#!/bin/bash
echo "$STUB_ADDRS "
"""
_TAILSCALE = r"""#!/bin/bash
[ "$1" = ip ] && echo "$STUB_TAILNET_IP"
"""


def _key(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", url)


class _World:
    """One stubbed host: which URLs answer, from where, with what status."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "state"
        self.bin = root / "bin"
        self.dir.mkdir()
        self.bin.mkdir()
        for name, body in (("docker", _DOCKER), ("curl", _CURL),
                           ("hostname", _HOSTNAME), ("tailscale", _TAILSCALE)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.addrs = [GATEWAY, LAN]
        self.tailnet: str | None = TAILNET

    def answers(self, kind: str, url: str, status: int = 200) -> None:
        (self.dir / f"{kind}.{_key(url)}").write_text(str(status))

    def healthy(self) -> _World:
        """The state the fix produces: the control port answers, Honcho
        answers on loopback and to nora and over the tailnet, and the port is
        refused at every address from a default-bridge container."""
        self.answers("bridge", f"http://{GATEWAY}:3100/", 200)
        self.answers("host", "http://127.0.0.1:8000/health")
        self.answers("exec", "http://honcho:8000/health")
        self.answers("host", f"http://{TAILNET}:18000/health")
        return self

    def run(self) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_DIR": str(self.dir), "STUB_GATEWAY": GATEWAY,
            "STUB_ADDRS": " ".join(self.addrs),
            "STUB_TAILNET_IP": self.tailnet or "",
            "HOME": str(self.dir),
        }
        if self.tailnet is None:
            (self.bin / "tailscale").unlink()
        return subprocess.run(  # noqa: S603 - a fixed local script, stubbed PATH
            ["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True,
            check=False, timeout=60,
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_closed_honcho_with_its_caller_and_the_tailnet_intact_passes(world: _World) -> None:
    result = world.healthy().run()
    assert result.returncode == 0, result.stdout
    # A positive control: it did read the addresses, not skip them.
    assert f"PASS   CLOSED: {GATEWAY}:8000" in result.stdout
    assert f"PASS   CLOSED: {LAN}:8000" in result.stdout
    assert "PASS   CALLER" in result.stdout
    assert "PASS   TAILNET" in result.stdout


def test_an_address_that_answers_is_a_fail_naming_it(world: _World) -> None:
    """The exposure itself: the LAN address answers a default-bridge
    container. Everything else is healthy, so only that line can be the
    reason for exit 1."""
    world.healthy().answers("bridge", f"http://{LAN}:8000/health", 200)
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert f"FAIL   CLOSED: {LAN}:8000 ANSWERED" in result.stdout
    assert f"PASS   CLOSED: {GATEWAY}:8000" in result.stdout


def test_any_http_status_from_the_port_counts_as_open(world: _World) -> None:
    """A 401 or a 404 is an answer: the door is open and something is
    behind it. Only no answer at all is closed."""
    world.healthy().answers("bridge", f"http://{GATEWAY}:8000/health", 404)
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert f"FAIL   CLOSED: {GATEWAY}:8000 ANSWERED" in result.stdout


def test_a_probe_that_cannot_see_out_is_an_error_not_a_pass(world: _World) -> None:
    """The control port does not answer, so every refusal that follows is
    the probe's blindness. Exit 2, never 0."""
    world.healthy()
    (world.dir / f"bridge.{_key(f'http://{GATEWAY}:3100/')}").unlink()
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  CONTROL" in result.stdout


def test_a_dead_honcho_reads_as_an_error_not_as_closed(world: _World) -> None:
    """Refused everywhere because Honcho is down looks identical to refused
    everywhere because it is closed. The loopback check is what tells them
    apart."""
    world.healthy()
    (world.dir / f"host.{_key('http://127.0.0.1:8000/health')}").unlink()
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  ALIVE" in result.stdout


def test_a_caller_shut_out_is_a_fail(world: _World) -> None:
    """Closing the door on nora is not a fix."""
    world.healthy()
    (world.dir / f"exec.{_key('http://honcho:8000/health')}").unlink()
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "FAIL   CALLER" in result.stdout


def test_a_lost_tailnet_forward_is_a_fail(world: _World) -> None:
    world.healthy()
    (world.dir / f"host.{_key(f'http://{TAILNET}:18000/health')}").unlink()
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "FAIL   TAILNET" in result.stdout


def test_a_host_with_no_tailscale_skips_that_check_and_still_passes(world: _World) -> None:
    world.healthy().tailnet = None
    result = world.run()
    assert result.returncode == 0, result.stdout
    assert "SKIP   TAILNET: no tailscale here" in result.stdout


def test_an_open_door_outranks_an_unreadable_control(world: _World) -> None:
    """Both an open address and a blind control: the finding is the open
    door, so exit 1, not 2."""
    world.healthy().answers("bridge", f"http://{LAN}:8000/health", 200)
    (world.dir / f"bridge.{_key(f'http://{GATEWAY}:3100/')}").unlink()
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "ERROR  CONTROL" in result.stdout
    assert "FAIL   CLOSED" in result.stdout


def test_the_script_is_executable_and_parses() -> None:
    assert os.access(SCRIPT, os.X_OK)
    parsed = subprocess.run(["/bin/bash", "-n", str(SCRIPT)], capture_output=True,  # noqa: S603
                            text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr
