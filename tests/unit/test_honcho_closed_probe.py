"""`deploy/honcho-closed-probe.sh` reads the outcomes apart (DEC-259).

The probe is the read-back for the one property that matters here: Honcho
answers with no authentication, so what protects it is where it listens. A probe
that reads a dead Honcho, a dropped packet, an empty result or its own blind eye
as "closed" would certify the exposure it exists to find, so each case below is
one that tells PASS from FAIL from ERROR from WARN, driven through stub
`docker`, `curl`, `hostname`, `ss` and `tailscale` binaries that behave like the
real ones on the properties the probe reads: an HTTP status and a curl exit code
per URL (000 with 7 is a refusal, 000 with 28 a timeout), a docker failure with
no output, and the listening sockets.

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
V6 = "2001:db8::9"
TAILNET = "100.64.0.9"

# Each stub looks its answer up in a file named for the URL, so a case states
# only the URLs it cares about. A bridge URL it does not state is a REFUSAL
# ("000 7"); a file holding EMPTY makes docker itself fail with no output.
_DOCKER = r"""#!/bin/bash
key() { printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'; }
case "$1" in
  network) echo "${STUB_GATEWAY:-172.17.0.1}"; exit 0 ;;
  version) echo "${STUB_DOCKER_VERSION-29.5.2}"; exit 0 ;;
  run)
      url=""; for a in "$@"; do url="$a"; done
      f="$STUB_DIR/bridge.$(key "$url")"
      if [ -f "$f" ]; then body=$(cat "$f"); else body="000 7"; fi
      [ "$body" = EMPTY ] && exit 125
      printf '%s' "${body%% *}"; exit "${body##* }" ;;
  exec)
      url=""; for a in "$@"; do url="$a"; done
      f="$STUB_DIR/exec.$(key "$url")"
      if [ -f "$f" ]; then cat "$f"; else printf 000; fi
      exit 0 ;;
esac
exit 1
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
_SS = r"""#!/bin/bash
[ -f "$STUB_DIR/ss.out" ] && cat "$STUB_DIR/ss.out"
exit 0
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
        for name, body in (("docker", _DOCKER), ("curl", _CURL), ("hostname", _HOSTNAME),
                           ("ss", _SS), ("tailscale", _TAILSCALE)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.addrs = [GATEWAY, LAN]
        self.tailnet: str | None = TAILNET
        self.version = "29.5.2"
        self.has_ss = True

    def bridge(self, url: str, body: str) -> None:
        (self.dir / f"bridge.{_key(url)}").write_text(body)

    def answers(self, kind: str, url: str, status: int = 200) -> None:
        (self.dir / f"{kind}.{_key(url)}").write_text(str(status))

    def drop(self, kind: str, url: str) -> None:
        (self.dir / f"{kind}.{_key(url)}").unlink()

    def listeners(self, *lines: str) -> None:
        (self.dir / "ss.out").write_text("".join(f"LISTEN 0 4096 {ln} 0.0.0.0:*\n" for ln in lines))

    def healthy(self) -> _World:
        """The healthy state: the control port answers at every address,
        the port is refused (the default), Honcho answers on loopback and to nora
        and over the tailnet, every listener is loopback, the engine is new."""
        for ip in (GATEWAY, LAN):
            self.bridge(f"http://{ip}:3100/", "200 0")
        self.answers("host", "http://127.0.0.1:8000/health")
        self.answers("exec", "http://honcho:8000/health")
        self.answers("host", f"http://{TAILNET}:18000/health")
        self.listeners("127.0.0.1:8000")
        return self

    def run(self) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_DIR": str(self.dir), "STUB_GATEWAY": GATEWAY,
            "STUB_ADDRS": " ".join(self.addrs), "STUB_DOCKER_VERSION": self.version,
            "STUB_TAILNET_IP": self.tailnet or "", "HOME": str(self.dir),
        }
        if self.tailnet is None:
            (self.bin / "tailscale").unlink()
        if not self.has_ss:
            (self.bin / "ss").unlink()
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
    assert "PASS   LISTEN" in result.stdout
    assert "PASS   CALLER" in result.stdout
    assert "PASS   TAILNET" in result.stdout
    assert "PASS   ENGINE" in result.stdout


def test_an_address_that_answers_is_a_fail_naming_it(world: _World) -> None:
    """The exposure itself: the LAN address answers a default-bridge container."""
    world.healthy().bridge(f"http://{LAN}:8000/health", "200 0")
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert f"FAIL   CLOSED: {LAN}:8000 ANSWERED" in result.stdout
    assert f"PASS   CLOSED: {GATEWAY}:8000" in result.stdout


def test_any_http_status_from_the_port_counts_as_open(world: _World) -> None:
    """A 401 or a 404 is an answer: the door is open and something is behind it."""
    world.healthy().bridge(f"http://{GATEWAY}:8000/health", "404 0")
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert f"FAIL   CLOSED: {GATEWAY}:8000 ANSWERED" in result.stdout


def test_a_timeout_is_not_a_closed_port(world: _World) -> None:
    """curl exit 28: nothing answered, and nothing refused either. A firewall drop
    and a closed port are different claims; only exit 7 is the second."""
    world.healthy().bridge(f"http://{LAN}:8000/health", "000 28")
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert f"ERROR  CLOSED: {LAN}:8000 gave no HTTP answer but curl exited 28" in result.stdout
    assert f"PASS   CLOSED: {LAN}:8000" not in result.stdout


def test_a_reset_is_not_a_closed_port_either(world: _World) -> None:
    world.healthy().bridge(f"http://{LAN}:8000/health", "000 56")
    result = world.run()
    assert result.returncode == 2, result.stdout


def test_an_empty_result_is_an_error_and_never_a_pass(world: _World) -> None:
    """docker itself failed for that address (no output): an empty code must not
    read as a refusal."""
    world.healthy().bridge(f"http://{LAN}:8000/health", "EMPTY")
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert f"ERROR  CLOSED: no result at all for {LAN}:8000" in result.stdout
    assert f"PASS   CLOSED: {LAN}:8000" not in result.stdout


def test_an_address_the_control_cannot_reach_is_an_error_not_a_refusal(world: _World) -> None:
    """The control port does not answer at that address, so the refusal at :8000
    there proves nothing."""
    world.healthy().bridge(f"http://{LAN}:3100/", "000 7")
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert f"ERROR  CLOSED: the control port does not answer at {LAN} either" in result.stdout


def test_a_probe_that_cannot_see_out_is_an_error_not_a_pass(world: _World) -> None:
    world.healthy().bridge(f"http://{GATEWAY}:3100/", "000 7")
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  CONTROL" in result.stdout


def test_ipv6_addresses_are_probed_too(world: _World) -> None:
    """Honcho published `[::]:8000` as well as `0.0.0.0:8000`: an IPv4-only probe
    certified half the door."""
    world.addrs.append(V6)
    world.healthy().bridge(f"http://[{V6}]:3100/", "200 0")
    world.bridge(f"http://[{V6}]:8000/health", "200 0")
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert f"FAIL   CLOSED: {V6}:8000 ANSWERED" in result.stdout


def test_an_ipv6_address_with_no_route_is_covered_by_the_listener_check(world: _World) -> None:
    world.addrs.append(V6)
    result = world.healthy().run()
    assert result.returncode == 0, result.stdout
    assert f"SKIP   CLOSED: {V6}" in result.stdout


def test_a_listener_on_a_wildcard_address_is_a_fail_in_either_family(world: _World) -> None:
    """The sockets themselves, which need no route to read: `[::]:8000` is the
    IPv6 half of the publish."""
    world.healthy().listeners("127.0.0.1:8000", "[::]:8000")
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "FAIL   LISTEN" in result.stdout and "[::]:8000" in result.stdout


def test_a_loopback_ipv6_listener_is_fine(world: _World) -> None:
    world.healthy().listeners("127.0.0.1:8000", "[::1]:8000")
    result = world.run()
    assert result.returncode == 0, result.stdout


def test_no_listener_at_all_is_an_error_not_closed(world: _World) -> None:
    world.healthy().listeners()
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  LISTEN" in result.stdout


def test_a_host_without_ss_cannot_vouch_for_ipv6(world: _World) -> None:
    world.has_ss = False
    result = world.healthy().run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  LISTEN: ss is not installed" in result.stdout


def test_a_dead_honcho_reads_as_an_error_not_as_closed(world: _World) -> None:
    world.healthy().drop("host", "http://127.0.0.1:8000/health")
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  ALIVE" in result.stdout


def test_a_caller_shut_out_is_a_fail(world: _World) -> None:
    world.healthy().drop("exec", "http://honcho:8000/health")
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "FAIL   CALLER" in result.stdout


def test_a_lost_tailnet_forward_is_a_fail(world: _World) -> None:
    world.healthy().drop("host", f"http://{TAILNET}:18000/health")
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "FAIL   TAILNET" in result.stdout


def test_a_host_with_no_tailscale_skips_that_check_and_still_passes(world: _World) -> None:
    world.healthy().tailnet = None
    result = world.run()
    assert result.returncode == 0, result.stdout
    assert "SKIP   TAILNET: no tailscale here" in result.stdout


@pytest.mark.parametrize("version", ["27.5.1", "20.10.24", "24.0.7"])
def test_an_engine_older_than_28_is_a_loud_non_zero_warn(world: _World, version: str) -> None:
    """Older engines let a loopback-published port be reached from the LAN, which a
    probe run on the host cannot see. Everything else is green and the exit is
    still not 0."""
    world.healthy().version = version
    result = world.run()
    assert result.returncode == 3, result.stdout
    assert f"WARN   ENGINE: Docker Engine {version} is older than 28" in result.stdout
    assert "From a machine on the LAN" in result.stdout


def test_an_engine_version_that_cannot_be_read_is_an_error(world: _World) -> None:
    world.healthy().version = ""
    result = world.run()
    assert result.returncode == 2, result.stdout
    assert "ERROR  ENGINE" in result.stdout


def test_an_open_door_outranks_an_unreadable_control_and_a_warn(world: _World) -> None:
    world.healthy().bridge(f"http://{LAN}:8000/health", "200 0")
    world.bridge(f"http://{GATEWAY}:3100/", "000 7")
    world.version = "24.0.7"
    result = world.run()
    assert result.returncode == 1, result.stdout
    assert "ERROR  CONTROL" in result.stdout and "FAIL   CLOSED" in result.stdout
    assert "WARN   ENGINE" in result.stdout


def test_an_error_outranks_a_warn(world: _World) -> None:
    world.healthy().version = "24.0.7"
    world.listeners()
    result = world.run()
    assert result.returncode == 2, result.stdout


def test_the_script_is_executable_and_parses() -> None:
    assert os.access(SCRIPT, os.X_OK)
    parsed = subprocess.run(["/bin/bash", "-n", str(SCRIPT)], capture_output=True,  # noqa: S603
                            text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr
