"""`deploy/computer/nora-computer`'s egress policy, driven through a stub `sbx`.

The operator's ruling (ADR 0022 Decision 2): the VM may reach the
public web (`**` over TCP) with the private ranges denied. The stub behaves like
the real binary on the properties the script reads: `policy check network`
answers `Allowed:`/`Denied:` from the allow and deny rules that `policy allow
network` and `policy deny network` append to (a CIDR deny binds IP literals
only, as sbx does), and a command it does not know exits 1, so a call the
script should not make is a failure and not a silent no.
The harness is `tests/unit/nora_computer_harness.py`.

The stub's `policy check network` reproduces the real tool's exit status, not
only its words: 0 and `Allowed:` for an allowed host, 1 and `Denied:` for a
denied one. A stub that exited 0 for a denial would let the script read every
denial as unknown and stay green.

What this cannot show is sbx itself: that the real daemon takes these rules and
answers in these words is the README's read-back on norty, UNVERIFIED until run.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.unit.nora_computer_harness import HOST_IP, HOST_PORT, NAME, SCRIPT, Host

README = SCRIPT.parent / "README.md"

#: Written out here, not read from the script: the test must be able to fail
#: when the script's lists change. The operator's ruling.
EXPECTED_EGRESS = {"**"}
EXPECTED_DENY = {
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
}


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def test_apply_allows_the_public_web_and_denies_exactly_the_private_ranges(host: Host) -> None:
    result = host.run("egress", "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    # The sets the policy ended up holding, and the sets it was asked for, are
    # the operator's ruling and no wider: one allow (`**`), six denies.
    assert host.allowed() == EXPECTED_EGRESS
    assert host.denied() == EXPECTED_DENY
    asked_allow = [c.split()[-1] for c in host.allow_calls()]
    asked_deny = [c.split()[-1] for c in host.deny_calls()]
    assert sorted(asked_allow) == sorted(EXPECTED_EGRESS)
    assert sorted(asked_deny) == sorted(EXPECTED_DENY)


def test_every_rule_is_scoped_to_this_sandbox_not_the_whole_host(host: Host) -> None:
    host.run("egress", "apply")
    calls = host.allow_calls() + host.deny_calls()
    assert len(calls) == len(EXPECTED_EGRESS) + len(EXPECTED_DENY), calls
    assert all(f"--sandbox {NAME}" in c for c in calls), calls


def test_a_second_apply_adds_nothing(host: Host) -> None:
    assert host.run("egress", "apply").returncode == 0
    first = (len(host.allow_calls()), len(host.deny_calls()))
    again = host.run("egress", "apply")
    assert again.returncode == 0, again.stdout + again.stderr
    assert (len(host.allow_calls()), len(host.deny_calls())) == first
    assert first == (len(EXPECTED_EGRESS), len(EXPECTED_DENY))


def test_the_wildcard_reaches_sbx_as_one_literal_argument_never_a_glob(host: Host) -> None:
    """`**` is a shell glob too: run from a directory holding files, an unquoted
    expansion would hand sbx their names instead."""
    (host.root / "decoy-file").write_text("x")
    assert host.run("egress", "apply", cwd=str(host.root)).returncode == 0
    assert [c.split()[-1] for c in host.allow_calls()] == ["**"], host.allow_calls()


def test_check_passes_after_apply_and_names_each_line(host: Host) -> None:
    host.run("egress", "apply")
    result = host.run("egress", "check")
    assert result.returncode == 0, result.stdout
    for res in ALLOWED_PROBES:
        assert f"want allowed  {res}  got allowed" in result.stdout
    # The negative half, the part that keeps Nora's own services out of reach.
    for probe in DENIED_PROBES:
        assert f"want denied   {probe}  got denied" in result.stdout


def test_check_fails_when_nothing_has_been_applied(host: Host) -> None:
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert "want allowed  pypi.org:443  got denied" in result.stdout


def test_check_fails_when_a_private_range_is_no_longer_denied(host: Host) -> None:
    """The realistic failure: a deny rule is lost (a policy reset, a hand edit).
    With `**` allowed, the docker0 range then reaches computerd and OmniRoute, a
    violation however well the public hosts read."""
    host.run("egress", "apply")
    denied = host.dir / "denied"
    denied.write_text("".join(f"{c}\n" for c in EXPECTED_DENY - {"172.16.0.0/12"}))
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert f"want denied   {HOST_IP}:{HOST_PORT}  got allowed" in result.stdout
    assert f"want denied   {HOST_IP}:20128  got allowed" in result.stdout
    assert "want denied   192.168.1.1:80  got denied" in result.stdout


def test_check_fails_when_the_wildcard_allow_is_gone(host: Host) -> None:
    host.run("egress", "apply")
    (host.dir / "allowed").write_text("")
    result = host.run("egress", "check")
    assert result.returncode == 1, result.stdout
    assert "want allowed  example.com:443  got denied" in result.stdout


def test_a_name_resolving_to_a_private_address_is_not_caught_the_accepted_limit(
    host: Host,
) -> None:
    """KNOWN, ACCEPTED RISK (operator-accepted; sbx 0.46.0): a CIDR deny binds
    IP literals only, so a public NAME that resolves to 127.0.0.1 is Allowed.
    This pins that the policy does not claim otherwise; if sbx ever closes the
    gap this test goes red and the README's limit paragraph is to be
    rewritten."""
    host.run("egress", "apply")
    literal = host.run("egress", "check")
    assert literal.returncode == 0, literal.stdout
    sbx = str(host.bin / "sbx")
    env = {"STUB_DIR": str(host.dir), "PATH": "/usr/bin:/bin"}
    named = subprocess.run(  # noqa: S603 - the stub, on a private dir
        [sbx, "policy", "check", "network", "--sandbox", NAME, "127-0-0-1.sslip.io:11434"],
        env=env, capture_output=True, text=True, check=False)
    assert named.returncode == 0
    assert named.stdout.splitlines()[0] == "Allowed: 127-0-0-1.sslip.io:11434"


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


def test_apply_reports_a_deny_rule_it_could_not_add(host: Host) -> None:
    """A failed deny is the dangerous one. It must be exit 1, never a quiet 0,
    and `**` must NOT be added: the web allowed with a private range not shut
    would reach Nora's own services by IP."""
    result = host.run("egress", "apply", STUB_DENY_FAILS="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert host.denied() == set()
    assert "denying 10.0.0.0/8 failed" in result.stderr
    assert host.allowed() == set()
    assert host.allow_calls() == []


def test_every_deny_is_added_before_the_wildcard_allow(host: Host) -> None:
    """Never a moment with `**` allowed and a private range still open."""
    host.run("egress", "apply")
    calls = host.sbx_calls("policy ")
    first_allow = next(i for i, c in enumerate(calls) if c.startswith("policy allow"))
    last_deny = max(i for i, c in enumerate(calls) if c.startswith("policy deny"))
    assert last_deny < first_allow, calls


def test_egress_without_a_sandbox_refuses(tmp_path: Path) -> None:
    host = Host(tmp_path, sandbox=False)
    result = host.run("egress", "apply")
    assert result.returncode == 6, result.stdout + result.stderr
    assert host.allow_calls() == [] and host.deny_calls() == []


def test_serve_prepares_the_policy_on_every_start_not_only_on_a_create() -> None:
    """`prepare` is what `serve` runs each time, and a policy rule lives in
    sbx's daemon, not in the VM, so applying it only after a create would lose
    it the day the policy store is reset while the VM keeps running."""
    text = SCRIPT.read_text()
    start = text.index("\nprepare() {")
    body = text[start:text.index("\n}\n", start)]
    assert "egress_apply" in body
    # And outside the create branch: after the sandbox-absent block closed.
    assert body.index("egress_apply") > body.index("published")


def _readme_egress_section() -> str:
    text = README.read_text()
    start = text.index("\n## Network egress")
    return text[start:text.index("\n## ", start + 1)]


def test_the_readme_documents_the_same_egress_policy_as_the_script() -> None:
    """The README's egress section names the allow rule and every denied CIDR in
    its table, and no CIDR the script does not deny: neither a range the script
    denies without saying so, nor one it documents and drops."""
    section = _readme_egress_section()
    for rule in EXPECTED_EGRESS:
        assert f"`{rule}`" in section, rule
    documented: set[str] = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells and re.fullmatch(r"`\d+\.\d+\.\d+\.\d+/\d+`", cells[0]):
            documented.add(cells[0].strip("`"))
    assert documented == EXPECTED_DENY
    # The accepted limit is stated where the policy is, with its measurement.
    assert "04-s1-runbook.md" in section and "IP literals" in section


def test_the_script_parses_and_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
    parsed = subprocess.run(["/bin/bash", "-n", str(SCRIPT)], capture_output=True,  # noqa: S603
                            text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr


# -- idempotence that does not trust sbx to de-duplicate; the exit codes ----


def test_apply_reads_the_rule_list_first_and_never_adds_a_listed_rule_again(host: Host) -> None:
    """If `sbx policy allow` does not de-duplicate, a second add is a second rule.
    The authorizer is made blind here (it denies everything), so only the rule
    LIST can say the rules are already there."""
    (host.dir / "allowed").write_text("\n".join(sorted(EXPECTED_EGRESS)) + "\n")
    (host.dir / "denied").write_text("\n".join(sorted(EXPECTED_DENY)) + "\n")
    result = host.run("egress", "apply", STUB_CHECK_DENIES_ALL="1")
    assert host.allow_calls() == [], host.allow_calls()
    assert host.deny_calls() == [], host.deny_calls()
    # Listed but not reading back as allowed is a failure to say so, not a pass.
    assert result.returncode == 1, result.stdout + result.stderr


def test_an_unreadable_rule_list_withholds_the_web(host: Host) -> None:
    """With sbx's rule list unreadable the denies cannot be confirmed, so `**`
    is not added: fail closed. The denies are still attempted (a duplicate
    deny is harmless, a missing one is not)."""
    result = host.run("egress", "apply", STUB_LS_BROKEN="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert host.allow_calls() == []
    assert len(host.deny_calls()) == len(EXPECTED_DENY)


def test_a_rule_scoped_to_another_sandbox_does_not_count_as_ours(host: Host) -> None:
    """Denies listed for `nora-computer-eval` (a name that merely starts with
    ours) are not ours: every range is still denied for this sandbox before
    `**` is allowed. A substring match would skip them and leave the ranges
    open."""
    (host.dir / "foreign_denied").write_text("".join(f"{c}\n" for c in EXPECTED_DENY))
    result = host.run("egress", "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    asked = {c.split()[-1] for c in host.deny_calls()}
    assert asked == EXPECTED_DENY, asked


def test_a_deny_that_reports_success_but_did_not_land_withholds_the_web(host: Host) -> None:
    """The adds' exit codes are not the evidence: the rule list is read back,
    and `**` is not added while a range is missing from it."""
    result = host.run("egress", "apply", STUB_DENY_SILENT="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert host.allow_calls() == []


@pytest.mark.parametrize("cidr", sorted(EXPECTED_DENY))
def test_check_sees_each_missing_deny(host: Host, cidr: str) -> None:
    """One probe per range: losing any single deny is a violation."""
    host.run("egress", "apply")
    (host.dir / "denied").write_text("".join(f"{c}\n" for c in EXPECTED_DENY - {cidr}))
    result = host.run("egress", "check")
    assert result.returncode == 1, (cidr, result.stdout)
    assert "got allowed" in result.stdout


def _fresh(tmp_path: Path, name: str) -> Host:
    root = tmp_path / name
    root.mkdir()
    return Host(root)


def test_apply_exit_codes_are_the_checks(tmp_path: Path) -> None:
    """0 in place and read back, 1 a rule could not be added or reads denied, 2 a
    verdict sbx would not give: what the README says."""
    ok = _fresh(tmp_path, "ok")
    assert ok.run("egress", "apply").returncode == 0
    # Everything is already there, so a store that refuses writes is never asked.
    assert ok.run("egress", "apply", STUB_ALLOW_FAILS="1").returncode == 0
    assert _fresh(tmp_path, "ro").run("egress", "apply", STUB_ALLOW_FAILS="1").returncode == 1
    assert _fresh(tmp_path, "blind").run("egress", "apply", STUB_CHECK_BROKEN="1").returncode == 2


def test_the_readme_documents_the_apply_and_check_exit_codes() -> None:
    readme = " ".join(README.read_text().split())
    assert "egress apply" in readme and "egress check" in readme
    assert "exits 0 when every rule is in place and reads allowed, 1 when a rule" in readme


# -- the verdict is the first line AND the exit status, never the exit alone --

ALLOWED_PROBES = ("pypi.org:443", "archive.ubuntu.com:80", "example.com:443")
DENIED_PROBES = (
    f"{HOST_IP}:{HOST_PORT}", f"{HOST_IP}:20128", "127.0.0.1:11434", "10.0.0.1:80",
    "192.168.1.1:80", "100.64.0.1:80", "169.254.169.254:80",
)


def _status_env() -> dict[str, str]:
    """`status` also curls computerd: a closed loopback port refuses at once."""
    return {"NORA_COMPUTER_HOST_IP": "127.0.0.1", "NORA_COMPUTER_PORT": "1"}


def _egress_field(status_stdout: str) -> str:
    return next(w for w in status_stdout.split() if w.startswith("egress=")).removeprefix("egress=")


def test_the_fake_sbx_exits_like_the_real_one(host: Host) -> None:
    """The harness itself: a denial is exit 1 with the verdict first and the
    Governance/Context/Reason lines after it, an allow is exit 0. Without this the
    fake can drift back to exit 0 for a denial and every other test goes blind."""
    (host.dir / "allowed").write_text("**\n")
    (host.dir / "denied").write_text("\n".join(sorted(EXPECTED_DENY)) + "\n")
    sbx = str(host.bin / "sbx")
    env = {"STUB_DIR": str(host.dir), "PATH": "/usr/bin:/bin"}

    def check(target: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - the stub, on a private dir
            [sbx, "policy", "check", "network", "--sandbox", NAME, target],
            env=env, capture_output=True, text=True, check=False)

    denied, allowed = check("172.17.0.1:18765"), check("pypi.org:443")
    assert denied.returncode == 1 and allowed.returncode == 0
    assert denied.stdout.splitlines()[0] == "Denied: 172.17.0.1:18765"
    assert allowed.stdout.splitlines()[0] == "Allowed: pypi.org:443"
    # sbx 0.46.0: a CIDR deny names a local rule,
    # an allowed host carries no Reason line.
    assert re.search(r'^Reason: denied by local rule rule "local:[0-9a-f-]+"$',
                     denied.stdout, re.M), denied.stdout
    assert "Reason:" not in allowed.stdout
    assert "Context: sandbox:nora-computer" in denied.stdout
    # No allow rule at all: the default deny, in its own words.
    (host.dir / "allowed").write_text("")
    bare = check("example.com:443")
    assert bare.returncode == 1
    assert "Reason: no matching allow rule (default deny)" in bare.stdout


def test_a_denial_that_exits_1_reads_denied_not_unknown(host: Host) -> None:
    """The real sbx exits 1 for a denied host; reading any non-zero exit as
    `unknown` would mean `egress check` could never pass."""
    assert host.run("egress", "apply").returncode == 0
    result = host.run("egress", "check")
    assert result.returncode == 0, result.stdout
    for probe in DENIED_PROBES:
        assert f"want denied   {probe}  got denied" in result.stdout, result.stdout
    assert "unknown" not in result.stdout


def test_an_allow_that_exits_0_reads_allowed(host: Host) -> None:
    assert host.run("egress", "apply").returncode == 0
    result = host.run("egress", "check")
    for res in ALLOWED_PROBES:
        assert f"want allowed  {res}  got allowed" in result.stdout, result.stdout


def test_an_allowed_line_with_a_failing_exit_is_unknown_never_allowed(host: Host) -> None:
    """Only `Allowed` with exit 0 is an allow: a tool that said Allowed and then
    failed has not given an answer to act on."""
    assert host.run("egress", "apply").returncode == 0
    result = host.run("egress", "check", STUB_CHECK_SHAPE="allowed-exit-1")
    assert result.returncode == 2, result.stdout
    assert "got allowed" not in result.stdout, result.stdout
    assert "want allowed  pypi.org:443  got unknown" in result.stdout


@pytest.mark.parametrize("shape", ["empty", "empty-exit-1", "crash", "denied-exit-2"])
def test_an_unreadable_answer_is_unknown_never_a_verdict(host: Host, shape: str) -> None:
    """Empty output, a crashed sbx, and a Denied line that came with an exit
    status the tool does not use for a denial are all unknown (exit 2)."""
    assert host.run("egress", "apply").returncode == 0
    result = host.run("egress", "check", STUB_CHECK_SHAPE=shape)
    assert result.returncode == 2, result.stdout
    assert "got allowed" not in result.stdout and "got denied" not in result.stdout, result.stdout
    assert result.stdout.count("got unknown") == len(ALLOWED_PROBES) + len(DENIED_PROBES)


def test_a_denied_line_with_exit_0_is_still_denied(host: Host) -> None:
    """A tool version that exits 0 for a denial must not turn a denial unknown:
    Denied is read at exit 0 or 1."""
    assert host.run("egress", "apply").returncode == 0
    result = host.run("egress", "check", STUB_CHECK_SHAPE="denied-exit-0")
    assert "want denied   192.168.1.1:80  got denied" in result.stdout
    assert "want allowed  pypi.org:443  got denied" in result.stdout
    assert result.returncode == 1, result.stdout


def test_check_exit_codes_0_when_all_match_1_on_a_violation_2_only_when_unknown(
    tmp_path: Path,
) -> None:
    ok = _fresh(tmp_path, "ok")
    ok.run("egress", "apply")
    assert ok.run("egress", "check").returncode == 0

    # Nothing applied: the allowlist reads denied, a violation (1). The denials,
    # which exit 1, are not what would make it 2.
    nothing = _fresh(tmp_path, "bare").run("egress", "check")
    assert nothing.returncode == 1, nothing.stdout
    assert "unknown" not in nothing.stdout

    # A lost deny on the docker0 range: a violation, again 1, with every verdict read.
    leak = _fresh(tmp_path, "leak")
    leak.run("egress", "apply")
    (leak.dir / "denied").write_text("".join(f"{c}\n" for c in EXPECTED_DENY - {"172.16.0.0/12"}))
    leaked = leak.run("egress", "check")
    assert leaked.returncode == 1, leaked.stdout
    assert "unknown" not in leaked.stdout

    # A verdict the tool will not give is the only thing that is 2.
    blind = _fresh(tmp_path, "blind")
    blind.run("egress", "apply")
    assert blind.run("egress", "check", STUB_CHECK_SHAPE="empty").returncode == 2


def test_status_reads_egress_ok_when_the_policy_is_as_required(host: Host) -> None:
    host.run("egress", "apply")
    result = host.run("status", **_status_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert _egress_field(result.stdout) == "ok", result.stdout


def test_status_reads_egress_wrong_on_a_violation(host: Host) -> None:
    host.run("egress", "apply")
    # status probes 127.0.0.1:1 as computerd's own port: lose the loopback deny.
    (host.dir / "denied").write_text("".join(f"{c}\n" for c in EXPECTED_DENY - {"127.0.0.0/8"}))
    result = host.run("status", **_status_env())
    assert _egress_field(result.stdout) == "WRONG", result.stdout


def test_status_reads_egress_wrong_when_nothing_is_applied(tmp_path: Path) -> None:
    result = _fresh(tmp_path, "bare").run("status", **_status_env())
    assert _egress_field(result.stdout) == "WRONG", result.stdout


def test_status_reads_egress_unknown_when_sbx_will_not_say(host: Host) -> None:
    host.run("egress", "apply")
    for shape in ("empty", "allowed-exit-1", "crash"):
        result = host.run("status", STUB_CHECK_SHAPE=shape, **_status_env())
        assert _egress_field(result.stdout) == "unknown", (shape, result.stdout)
