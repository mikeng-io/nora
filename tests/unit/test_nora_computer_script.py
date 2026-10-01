"""`deploy/computer/nora-computer`'s host-side behaviour, driven through a stub `sbx`.

The script is the ONLY thing that runs `sbx`, so what it asks `sbx` to do is
the contract: the egress allowlist (operator ruling 2026-10-01: PyPI and the
Ubuntu archive, nothing else), and the lifecycle audit, backup and restore added
beside it. The stub behaves like the real binary on the properties the script
reads: `policy check network` answers `Allowed:`/`Denied:` from a table that
`policy allow network` appends to, and a command it does not know exits 1, so a
call the script should not make shows up as a failure and not as a silent no.

What this cannot show is sbx itself: that the real daemon takes these rules and
answers in these words is the README's read-back on norty, UNVERIFIED until run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "computer" / "nora-computer"
README = ROOT / "deploy" / "computer" / "README.md"

NAME = "nora-computer"
HOST_IP = "172.17.0.1"
HOST_PORT = "18765"

#: Written out here, not read from the script: the test must be able to fail
#: when the script's list changes. The operator's ruling, 2026-10-01.
EXPECTED_EGRESS = {
    "pypi.org:443",
    "files.pythonhosted.org:443",
    "archive.ubuntu.com:80",
    "archive.ubuntu.com:443",
    "security.ubuntu.com:80",
    "security.ubuntu.com:443",
}

_SBX = r"""#!/bin/bash
# A stub sbx. State lives in $STUB_DIR; every call is logged to calls.log.
echo "$*" >> "$STUB_DIR/calls.log"
allowed="$STUB_DIR/allowed"
touch "$allowed"
case "$1 $2 $3" in
  "policy check network")
      shift 3
      [ "${STUB_CHECK_BROKEN:-}" = 1 ] && { echo "policy engine unavailable"; exit 0; }
      target=""; for a in "$@"; do target="$a"; done
      if grep -qxF "$target" "$allowed"; then
          echo "Allowed: $target"; echo "Governance: Local policy only"
      else
          echo "Denied: $target"; echo "Reason: no matching allow rule (default deny)"
      fi
      exit 0 ;;
  "policy allow network")
      shift 3
      [ "${STUB_ALLOW_FAILS:-}" = 1 ] && { echo "policy store is read-only" >&2; exit 1; }
      res=""; for a in "$@"; do res="$a"; done
      echo "$res" >> "$allowed"
      exit 0 ;;
esac
case "$1" in
  ls)
      if [ "${STUB_NO_SANDBOX:-}" = 1 ]; then echo '{"sandboxes": []}'
      else echo '{"sandboxes": [{"name": "nora-computer", "status": "running"}]}'; fi
      exit 0 ;;
esac
echo "stub sbx: unexpected call: $*" >&2
exit 1
"""


class _Host:
    def __init__(self, root: Path) -> None:
        self.dir = root / "state"
        self.bin = root / "bin"
        self.home = root / "home"
        for d in (self.dir, self.bin, self.home):
            d.mkdir()
        sbx = self.bin / "sbx"
        sbx.write_text(_SBX)
        sbx.chmod(0o755)
        self.env_file = root / "env"
        self.env_file.write_text("NORA_COMPUTER_TOKEN=tok-test\n")

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        full = {
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/local/bin",
            "HOME": str(self.home), "STUB_DIR": str(self.dir),
            "NORA_ENV_FILE": str(self.env_file), "XDG_RUNTIME_DIR": str(self.dir),
            **env,
        }
        return subprocess.run(  # noqa: S603 - a fixed local script, stubbed PATH
            ["/bin/bash", str(SCRIPT), *args], env=full, capture_output=True,
            text=True, check=False, timeout=120,
        )

    def calls(self) -> list[str]:
        path = self.dir / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

    def allowed(self) -> set[str]:
        path = self.dir / "allowed"
        return set(path.read_text().split()) if path.exists() else set()

    def allow_calls(self) -> list[str]:
        return [c for c in self.calls() if c.startswith("policy allow network")]


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    return _Host(tmp_path)


# -- egress ---------------------------------------------------------------


def test_apply_allows_exactly_the_operators_hosts_and_nothing_else(host: _Host) -> None:
    result = host.run("egress", "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    # The set the policy ended up holding, and the set it was asked for, are
    # the operator's list and no wider: no wildcard, no `**`, none of Nora's.
    assert host.allowed() == EXPECTED_EGRESS
    asked = [c.split()[-1] for c in host.allow_calls()]
    assert sorted(asked) == sorted(EXPECTED_EGRESS)
    assert not any("*" in a or HOST_IP in a for a in asked)


def test_every_rule_is_scoped_to_this_sandbox_not_the_whole_host(host: _Host) -> None:
    host.run("egress", "apply")
    calls = host.allow_calls()
    assert calls, "no allow call was made, so there is nothing to scope"
    assert all(f"--sandbox {NAME}" in c for c in calls), calls


def test_a_second_apply_adds_nothing(host: _Host) -> None:
    assert host.run("egress", "apply").returncode == 0
    first = len(host.allow_calls())
    again = host.run("egress", "apply")
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(host.allow_calls()) == first == len(EXPECTED_EGRESS)


def test_check_passes_after_apply_and_names_each_line(host: _Host) -> None:
    host.run("egress", "apply")
    result = host.run("egress", "check")
    assert result.returncode == 0, result.stdout
    for res in EXPECTED_EGRESS:
        assert f"want allowed  {res}  got allowed" in result.stdout
    # The negative half, the part that keeps Nora's own services out of reach.
    assert f"want denied   {HOST_IP}:{HOST_PORT}  got denied" in result.stdout
    assert "want denied   example.com:443  got denied" in result.stdout


def test_check_fails_when_nothing_has_been_applied(host: _Host) -> None:
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert "want allowed  pypi.org:443  got denied" in result.stdout


def test_check_fails_when_a_service_of_noras_became_reachable(host: _Host) -> None:
    """The negative measurement: an allow rule that reaches computerd's own port
    is a violation however well the package hosts read."""
    host.run("egress", "apply")
    with (host.dir / "allowed").open("a") as fh:
        fh.write(f"{HOST_IP}:{HOST_PORT}\n")
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert f"want denied   {HOST_IP}:{HOST_PORT}  got allowed" in result.stdout


def test_a_verdict_sbx_will_not_give_is_an_error_not_an_allow(host: _Host) -> None:
    """Exit 2, never 0: an unreadable policy engine proves nothing about the
    allowlist."""
    result = host.run("egress", "check", STUB_CHECK_BROKEN="1")
    assert result.returncode == 2, result.stdout
    assert "got unknown" in result.stdout


def test_apply_reports_a_rule_it_could_not_add(host: _Host) -> None:
    result = host.run("egress", "apply", STUB_ALLOW_FAILS="1")
    assert result.returncode != 0
    assert host.allowed() == set()


def test_egress_without_a_sandbox_refuses(host: _Host) -> None:
    result = host.run("egress", "apply", STUB_NO_SANDBOX="1")
    assert result.returncode == 6, result.stdout + result.stderr
    assert host.allow_calls() == []


def test_serve_prepares_the_allowlist_on_every_start_not_only_on_a_create() -> None:
    """`prepare` is what `serve` runs each time, and a policy rule lives in
    sbx's daemon, not in the VM, so applying it only after a create would lose
    it the day the policy store is reset while the VM keeps running."""
    text = SCRIPT.read_text()
    start = text.index("\nprepare() {")
    body = text[start:text.index("\n}\n", start)]
    assert "egress_apply" in body
    # And outside the create branch: after the sandbox-absent block closed.
    assert body.index("egress_apply") > body.index("published")


def test_the_readme_lists_the_same_egress_hosts_as_the_script() -> None:
    """The README table, read as `host:port` pairs, is the allowlist: neither a
    host the script allows without saying so, nor one it documents and drops."""
    documented: set[str] = set()
    for line in README.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].startswith("`") and "." in cells[0]:
            host = cells[0].strip("`")
            for port in cells[1].replace(" ", "").split(","):
                if port.isdigit():
                    documented.add(f"{host}:{port}")
    assert documented == EXPECTED_EGRESS


def test_the_script_parses_and_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
    parsed = subprocess.run(["/bin/bash", "-n", str(SCRIPT)], capture_output=True,  # noqa: S603
                            text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr


def test_json_shape_the_stub_returns_is_what_the_script_parses(host: _Host) -> None:
    """The stub's `ls --json` is the shape the script's own parser reads
    (`sandbox_state`); if the two drifted, every egress test above would pass on
    a stub the script could not read."""
    body = json.loads(
        subprocess.run(  # noqa: S603
            [str(host.bin / "sbx"), "ls", "--json"], capture_output=True, text=True,
            env={"STUB_DIR": str(host.dir), "PATH": "/usr/bin:/bin"}, check=True,
        ).stdout
    )
    assert body["sandboxes"][0] == {"name": NAME, "status": "running"}
    assert 'for s in json.load(sys.stdin)["sandboxes"]' in SCRIPT.read_text()
