"""`deploy/computer/nora-computer`'s egress allowlist, driven through a stub `sbx`.

The operator's ruling (2026-10-01): the VM may fetch from PyPI and the Ubuntu
archive, nothing else. The stub behaves like the real binary on the properties
the script reads: `policy check network` answers `Allowed:`/`Denied:` from a
table that `policy allow network` appends to, and a command it does not know
exits 1, so a call the script should not make is a failure and not a silent no.
The harness is `tests/unit/nora_computer_harness.py`.

What this cannot show is sbx itself: that the real daemon takes these rules and
answers in these words is the README's read-back on norty, UNVERIFIED until run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.nora_computer_harness import HOST_IP, HOST_PORT, NAME, SCRIPT, Host

README = SCRIPT.parent / "README.md"

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


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def test_apply_allows_exactly_the_operators_hosts_and_nothing_else(host: Host) -> None:
    result = host.run("egress", "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    # The set the policy ended up holding, and the set it was asked for, are
    # the operator's list and no wider: no wildcard, no `**`, none of Nora's.
    assert host.allowed() == EXPECTED_EGRESS
    asked = [c.split()[-1] for c in host.allow_calls()]
    assert sorted(asked) == sorted(EXPECTED_EGRESS)
    assert not any("*" in a or HOST_IP in a for a in asked)


def test_every_rule_is_scoped_to_this_sandbox_not_the_whole_host(host: Host) -> None:
    host.run("egress", "apply")
    calls = host.allow_calls()
    assert calls, "no allow call was made, so there is nothing to scope"
    assert all(f"--sandbox {NAME}" in c for c in calls), calls


def test_a_second_apply_adds_nothing(host: Host) -> None:
    assert host.run("egress", "apply").returncode == 0
    first = len(host.allow_calls())
    again = host.run("egress", "apply")
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(host.allow_calls()) == first == len(EXPECTED_EGRESS)


def test_check_passes_after_apply_and_names_each_line(host: Host) -> None:
    host.run("egress", "apply")
    result = host.run("egress", "check")
    assert result.returncode == 0, result.stdout
    for res in EXPECTED_EGRESS:
        assert f"want allowed  {res}  got allowed" in result.stdout
    # The negative half, the part that keeps Nora's own services out of reach.
    assert f"want denied   {HOST_IP}:{HOST_PORT}  got denied" in result.stdout
    assert "want denied   example.com:443  got denied" in result.stdout


def test_check_fails_when_nothing_has_been_applied(host: Host) -> None:
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert "want allowed  pypi.org:443  got denied" in result.stdout


def test_check_fails_when_a_service_of_noras_became_reachable(host: Host) -> None:
    """The negative measurement: an allow rule that reaches computerd's own port
    is a violation however well the package hosts read."""
    host.run("egress", "apply")
    with (host.dir / "allowed").open("a") as fh:
        fh.write(f"{HOST_IP}:{HOST_PORT}\n")
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert f"want denied   {HOST_IP}:{HOST_PORT}  got allowed" in result.stdout


def test_a_verdict_sbx_will_not_give_is_an_error_not_an_allow(host: Host) -> None:
    """Exit 2, never 0: an unreadable policy engine proves nothing about the
    allowlist."""
    result = host.run("egress", "check", STUB_CHECK_BROKEN="1")
    assert result.returncode == 2, result.stdout
    assert "got unknown" in result.stdout


def test_apply_reports_a_rule_it_could_not_add(host: Host) -> None:
    result = host.run("egress", "apply", STUB_ALLOW_FAILS="1")
    assert result.returncode != 0
    assert host.allowed() == set()


def test_egress_without_a_sandbox_refuses(tmp_path: Path) -> None:
    host = Host(tmp_path, sandbox=False)
    result = host.run("egress", "apply")
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
