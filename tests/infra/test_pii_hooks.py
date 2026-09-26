"""The identifier hooks against real git: every attack #166 and #283 name.

Each test builds a throwaway repository with this branch's `.githooks/` and
scanner installed (`core.hooksPath`), a denylist of GENERATED values in
`tmp_path`, and then does what an author would: `git add`, `git commit`,
`git commit --no-verify`, `git rm`, a merge, a push range. The verdict is
read off git's own exit code and the hook's output, so what is proven is the
wiring, not a function.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


E = _load("pii_judge_eval")
SY = E.Synth(4242)
NAME = "Quorvel Tamriswix"  # invented; in the test denylist only
PHONE = SY.phone("81")
UUID = SY.uuid()


class Repo:
    def __init__(self, path: Path, env: dict):
        self.path, self.env = path, env

    def git(self, *args: str, env: dict | None = None, input: str | None = None):
        return subprocess.run(  # noqa: S603 - fixed argv against a throwaway repo
            ["git", *args],
            cwd=self.path,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
            input=input,
        )

    def write(self, rel: str, data: str | bytes) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())

    def commit(self, msg: str, *paths: str, env: dict | None = None, no_verify: bool = False):
        self.git("add", *(paths or ["-A"]))
        args = ["commit", "-q", "-m", msg] + (["--no-verify"] if no_verify else [])
        return self.git(*args, env=env)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def push_scan(self, remote: str = "origin") -> subprocess.CompletedProcess:
        """The scanner exactly as pre-push runs it: ref lines on stdin, the
        remote's name and url as arguments."""
        url = self.git("remote", "get-url", remote).stdout.strip()
        have = self.git("ls-remote", url, "refs/heads/main").stdout.split()
        line = f"refs/heads/main {self.head()} refs/heads/main {have[0] if have else '0' * 40}\n"
        return subprocess.run(  # noqa: S603 - this repo's own script
            [sys.executable, "scripts/pii_scan.py", "push", remote, url],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            input=line,
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    work = tmp_path / "repo"
    work.mkdir()
    deny = tmp_path / "deny.txt"
    deny.write_text(f"text\t{NAME}\nphone\t{PHONE}\nuuid\t{UUID}\n")
    deny.chmod(0o600)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("NORA_PII_", "GIT_")) and k not in ("HOME",)
    }
    env.update(
        HOME=str(tmp_path),
        NORA_PII_DENYLIST=str(deny),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Test Author",
        GIT_AUTHOR_EMAIL="author@example.com",
        GIT_COMMITTER_NAME="Test Author",
        GIT_COMMITTER_EMAIL="author@example.com",
    )
    r = Repo(work, env)
    r.git("init", "-q", "-b", "main")
    for rel in (
        "scripts/pii_scan.py",
        "scripts/pii_judge.py",
        "scripts/pii_cjk_fold.py",
        "scripts/pii_adjudicate.py",
        "scripts/pii_readonly.py",
        "scripts/check_commit_subject.py",
        ".githooks/pre-commit",
        ".githooks/commit-msg",
        ".githooks/pre-push",
        ".githooks/pii-allowlist.txt",
    ):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, work / rel)
    r.git("config", "core.hooksPath", ".githooks")
    out = r.commit("chore: scaffold the hooks")
    assert out.returncode == 0, out.stderr
    remote = tmp_path / "remote.git"
    r.git("init", "-q", "--bare", "-b", "main", str(remote))
    r.git("remote", "add", "origin", str(remote))
    assert r.git("-c", "core.hooksPath=/dev/null", "push", "-q", "origin", "main").returncode == 0
    return r


def _ids(out: subprocess.CompletedProcess) -> list[str]:
    return re.findall(r"id=([0-9a-f]{12})", out.stderr)


def test_clean_commit_passes(repo):
    repo.write("nora/a.py", "def f():\n    return 1\n")
    out = repo.commit("feat: add f")
    assert out.returncode == 0, out.stderr
    assert "pii staged: PASS" in out.stderr and "pii message: PASS" in out.stderr


def test_payload_in_the_commit_message_is_refused(repo):
    repo.write("nora/a.py", "x = 1\n")
    out = repo.commit(f"fix: reported by {NAME}")
    assert out.returncode != 0 and "denylist:text" in out.stderr
    assert "<commit-message>" in out.stderr
    assert NAME not in out.stderr


def test_a_denylisted_author_is_refused(repo):
    repo.write("nora/a.py", "x = 1\n")
    out = repo.commit("feat: x", env={"GIT_AUTHOR_NAME": NAME})
    assert out.returncode != 0 and "<author>" in out.stderr


def test_add_then_rm_is_caught_by_pre_push(repo):
    repo.write("tests/fixtures/rows.py", f'ROWS = [("{NAME}", "{PHONE}")]\n')
    assert repo.commit("test: rows", no_verify=True).returncode == 0
    bad = repo.head()
    repo.git("rm", "-q", "tests/fixtures/rows.py")
    assert repo.commit("test: drop rows").returncode == 0
    out = repo.push_scan()
    assert out.returncode == 1, out.stderr
    assert f"{bad[:10]} tests/fixtures/rows.py" in out.stderr


def test_a_message_committed_around_the_hooks_is_caught_by_pre_push(repo):
    repo.write("nora/a.py", "x = 1\n")
    assert repo.commit(f"fix: as {NAME} asked", no_verify=True).returncode == 0
    out = repo.push_scan()
    assert out.returncode == 1 and "<commit-message>" in out.stderr


def test_git_push_runs_both_checks_through_the_real_hook(repo, tmp_path):
    repo.write("tests/rows.py", f"ROWS = [{NAME!r}]\n")
    assert repo.commit("test: rows", no_verify=True).returncode == 0
    out = repo.git("push", "-q", "origin", "main")
    assert out.returncode != 0
    both = out.stdout + out.stderr
    assert "checked 1 commit subject(s)" in both and "pii push: FAIL" in both


def test_an_appended_non_utf8_byte_is_an_error(repo):
    repo.write("tests/fixtures/rows.py", f"ROWS = {NAME!r}\n".encode() + b"\xff")
    out = repo.commit("test: rows")
    assert out.returncode != 0 and "undecodable" in out.stderr


def test_utf16_is_an_error(repo):
    repo.write("tests/fixtures/rows.txt", f"{NAME}\n".encode("utf-16"))
    out = repo.commit("test: rows")
    assert out.returncode != 0 and "utf16" in out.stderr


def test_a_payload_renamed_svg_is_still_read(repo):
    repo.write("assets/diagram.svg", f"<svg><text>{PHONE}</text></svg>\n")
    out = repo.commit("docs: diagram")
    assert out.returncode != 0 and "denylist:number" in out.stderr


def test_a_symlink_is_an_error(repo):
    (repo.path / "link").symlink_to("scripts/pii_scan.py")
    out = repo.commit("chore: link")
    assert out.returncode != 0 and "symlink" in out.stderr


def test_a_declaration_beside_its_payload_does_not_exempt_it(repo):
    other = SY.phone("44", "spaced")
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + f"value {other} -- test placeholder\n")
    repo.write("tests/fixtures/p.py", f'P = "{other}"\n')
    out = repo.commit("test: p")
    assert out.returncode != 0 and "phone" in out.stderr
    # committed first and PUSHED, it is honoured by the next commit
    repo.git("reset", "-q")
    assert repo.commit("chore: declare p", ".githooks/pii-allowlist.txt").returncode == 0
    out = repo.commit("test: p", "tests/fixtures/p.py")
    assert out.returncode != 0, "a declaration nobody has pushed yet exempts nothing"
    assert repo.git("push", "-q", "origin", "main").returncode == 0
    out = repo.commit("test: p", "tests/fixtures/p.py")
    assert out.returncode == 0, out.stderr


def test_a_phone_with_spaces_and_dashes_is_caught(repo):
    nsn = PHONE[3:]
    repo.write("docs/a.md", f"call +81 {nsn[:2]}-{nsn[2:6]}-{nsn[6:]} or +81 {nsn[:5]} {nsn[5:]}\n")
    out = repo.commit("docs: a")
    assert out.returncode != 0 and out.stderr.count("denylist:number") == 2


def test_a_dashless_upper_case_uuid_is_caught(repo):
    repo.write("nora/ids.py", f'ID = "{UUID.replace("-", "").upper()}"\n')
    out = repo.commit("feat: ids")
    assert out.returncode != 0 and "denylist:uuid" in out.stderr


def test_an_int_microdegree_table_is_caught(repo):
    base = SY.base_point()
    rows = "".join(
        f"{int(la * 1e6)},{int(ln * 1e6)},{i}\n"
        for i, (la, ln) in enumerate(SY.near(base) for _ in range(6))
    )
    repo.write("tests/fixtures/fixes.csv", "lat_e6,lng_e6,seq\n" + rows)
    out = repo.commit("test: fixes")
    assert out.returncode != 0 and "coords" in out.stderr


def test_2000_rows_across_2_locations_is_caught(repo):
    a, b = SY.base_point(), SY.base_point()
    rows = "".join(
        f"({la:.6f}, {ln:.6f}),\n" for la, ln in (SY.near(a if i % 2 else b) for i in range(2000))
    )
    repo.write("tests/fixtures/trace.py", "TRACE = [\n" + rows + "]\n")
    out = repo.commit("test: trace")
    assert out.returncode != 0 and "rows=2000 clusters=2" in out.stderr


def test_residue_already_in_a_file_does_not_block_an_unrelated_edit(repo):
    repo.write("tests/old.py", f"WHO = {NAME!r}\nX = 1\n")
    assert repo.commit("test: old", no_verify=True).returncode == 0
    repo.write("tests/old.py", f"WHO = {NAME!r}\nX = 2\n")
    out = repo.commit("test: bump x")
    assert out.returncode == 0, out.stderr


def test_an_acknowledged_finding_is_recorded_and_pre_push_honours_it(repo):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    out = repo.commit("test: p")
    assert out.returncode != 0
    (fid,) = _ids(out)
    out = repo.commit("test: p", env={"NORA_PII_ACK": fid})
    assert out.returncode == 0, out.stderr
    assert f"PII-Ack: {fid}" in repo.git("log", "-1", "--format=%B").stdout
    assert repo.push_scan().returncode == 0
    # the same kind of value committed around the hooks, with no trailer
    repo.write("tests/q.py", f'Q = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: q", no_verify=True).returncode == 0
    assert repo.push_scan().returncode == 1


def test_a_denylist_hit_cannot_be_acknowledged(repo):
    repo.write("tests/p.py", f'P = "{PHONE}"\n')
    out = repo.commit("test: p")
    assert "not acknowledgeable" in out.stderr and _ids(out) == []


def test_a_missing_denylist_blocks_the_commit(repo):
    repo.write("nora/a.py", "x = 1\n")
    out = repo.commit("feat: x", env={"NORA_PII_DENYLIST": str(repo.path / "nope.txt")})
    assert out.returncode != 0 and "denylist-missing" in out.stderr
    out = repo.commit(
        "feat: x",
        env={
            "NORA_PII_DENYLIST": str(repo.path / "nope.txt"),
            "NORA_PII_DENYLIST_ABSENT": "throwaway test clone",
        },
    )
    assert out.returncode == 0 and "throwaway test clone" in out.stderr


def test_a_merge_does_not_re_judge_what_a_parent_already_had(repo):
    repo.git("checkout", "-q", "-b", "side")
    repo.write("tests/p.py", f'P = "{SY.phone("49", "spaced")}"\n')
    out = repo.commit("test: p")
    (fid,) = _ids(out)
    assert repo.commit("test: p", env={"NORA_PII_ACK": fid}).returncode == 0
    repo.git("checkout", "-q", "main")
    repo.write("nora/b.py", "y = 2\n")
    assert repo.commit("feat: b").returncode == 0
    out = repo.git("merge", "--no-ff", "-q", "side", "-m", "merge: side into main")
    assert out.returncode == 0, out.stderr
    assert repo.push_scan().returncode == 0


# ---- review of #591 (finding 1): what the push gives the TARGET remote


def _push(repo, *args):
    out = repo.git("push", "-q", *args)
    return out.returncode, out.stdout + out.stderr


def test_an_annotated_tag_message_and_its_tagger_are_scanned(repo):
    assert repo.git("tag", "-a", "v1", "-m", f"release for {NAME}").returncode == 0
    rc, out = _push(repo, "origin", "v1")
    assert rc != 0 and "denylist:text" in out and "<commit-message>" in out
    repo.git("tag", "-a", "v2", "-m", "release", env={"GIT_COMMITTER_NAME": NAME})
    rc, out = _push(repo, "origin", "v2")
    assert rc != 0 and "<tagger>" in out


def test_a_lightweight_tag_on_a_blob_or_a_tree_is_scanned(repo, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text(f"{NAME}\n")
    blob = repo.git("hash-object", "-w", str(f)).stdout.strip()
    repo.git("tag", "t-blob", blob)
    rc, out = _push(repo, "origin", "t-blob")
    assert rc != 0 and "denylist:text" in out
    repo.write("leak.txt", f"{NAME}\n")
    repo.git("add", "leak.txt")
    tree = repo.git("write-tree").stdout.strip()
    repo.git("reset", "-q")
    repo.git("tag", "t-tree", tree)
    rc, out = _push(repo, "origin", "t-tree")
    assert rc != 0 and "leak.txt" in out


def test_a_stale_remote_tracking_ref_is_not_trusted(repo, tmp_path):
    repo.git("checkout", "-q", "-b", "leak")
    repo.write("leak.txt", f"{NAME}\n")
    assert repo.commit("test: leak", no_verify=True).returncode == 0
    assert repo.git("push", "-q", "--no-verify", "origin", "leak").returncode == 0
    subprocess.run(
        ["git", "branch", "-D", "leak"],
        cwd=tmp_path / "remote.git",
        capture_output=True,
    )
    assert "origin/leak" in repo.git("branch", "-r").stdout  # still there locally
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "--no-verify", "origin/leak", "-m", "merge: leak")
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and "leak.txt" in out


def test_a_forged_remote_tracking_ref_is_not_trusted(repo):
    repo.write("leak.txt", f"{NAME}\n")
    assert repo.commit("test: leak", no_verify=True).returncode == 0
    repo.git("update-ref", "refs/remotes/origin/wip", "HEAD")
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and "leak.txt" in out


def test_pushing_to_another_remote_asks_that_remote(repo, tmp_path):
    second = tmp_path / "second.git"
    repo.git("init", "-q", "--bare", "-b", "main", str(second))
    repo.git("remote", "add", "second", str(second))
    repo.write("leak.txt", f"{NAME}\n")
    assert repo.commit("test: leak", no_verify=True).returncode == 0
    assert repo.git("push", "-q", "--no-verify", "origin", "main").returncode == 0
    rc, out = _push(repo, "second", "main")
    assert rc != 0 and "leak.txt" in out


def test_an_unreachable_remote_is_an_error(repo, tmp_path):
    repo.git("remote", "add", "gone", str(tmp_path / "nowhere.git"))
    out = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/pii_scan.py", "push", "gone", str(tmp_path / "nowhere.git")],
        cwd=repo.path,
        env=repo.env,
        capture_output=True,
        text=True,
        input=f"refs/heads/main {repo.head()} refs/heads/main {'0' * 40}\n",
    )
    assert out.returncode == 2 and "remote-unreachable" in out.stderr


# ---- findings 8, 9, 6


def test_a_hash_line_or_text_past_the_scissors_is_scanned(repo):
    repo.write("a.txt", "a\n")
    repo.git("add", "a.txt")
    out = repo.git("commit", "-q", "--cleanup=verbatim", "-m", "chore: a", "-m", f"# {NAME}")
    assert out.returncode != 0 and "<commit-message>" in out.stderr
    scissors = "# " + "-" * 24 + " >8 " + "-" * 24
    out = repo.git(
        "commit", "-q", "--cleanup=verbatim", "-m", "chore: a", "-m", f"{scissors}\n{PHONE}"
    )
    assert out.returncode != 0 and "denylist:number" in out.stderr


def test_amend_cannot_let_a_declaration_exempt_its_own_payload(repo):
    other = SY.phone("44", "spaced")
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + f"value {other} -- probe\n")
    assert repo.commit("chore: declare").returncode == 0
    repo.write("secret.txt", f'P = "{other}"\n')
    repo.git("add", "secret.txt")
    out = repo.git("commit", "-q", "--amend", "-m", "chore: declare")
    assert out.returncode != 0 and "secret.txt" in out.stderr


def test_a_merge_that_assembles_a_coordinate_table_is_caught_at_push(repo):
    base = SY.base_point()
    repo.write("fixes.csv", "lat,lng\n")
    assert repo.commit("test: base").returncode == 0
    rows = [f"{la:.6f},{ln:.6f}\n" for la, ln in (SY.near(base) for _ in range(4))]
    repo.git("checkout", "-q", "-b", "b1")
    repo.write("fixes.csv", "lat,lng\n" + "".join(rows[:2]))
    assert repo.commit("test: two").returncode == 0
    repo.git("checkout", "-q", "main")
    repo.git("checkout", "-q", "-b", "b2")
    repo.write("fixes.csv", "lat,lng\n" + "".join(rows[2:]))
    assert repo.commit("test: two more").returncode == 0
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "--no-verify", "b1", "-m", "merge: b1")
    if repo.git("merge", "-q", "--no-ff", "--no-verify", "b2", "-m", "merge: b2").returncode != 0:
        repo.write("fixes.csv", "lat,lng\n" + "".join(rows))
        repo.git("add", "fixes.csv")
        assert repo.git("commit", "-q", "--no-verify", "--no-edit").returncode == 0
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and "coords" in out


def test_a_declaration_pushed_together_with_its_payload_exempts_nothing(repo):
    other = SY.phone("44", "spaced")
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + f"value {other} -- test placeholder\n")
    assert repo.commit("chore: declare p", ".githooks/pii-allowlist.txt").returncode == 0
    repo.write("tests/fixtures/p.py", f'P = "{other}"\n')
    assert repo.commit("test: p", "tests/fixtures/p.py", no_verify=True).returncode == 0
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and "phone" in out


# ---- the agent adjudicator, with a fake `claude` on the command line


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, re, sys
argv = sys.argv[1:]
open(os.environ["FAKE_CLAUDE_ARGV"], "w").write(json.dumps({
    "argv": argv, "cwd": os.getcwd(), "staged_file_in_cwd": os.path.exists("tests/p.py"),
    "branches": os.popen("git for-each-ref --format=%(refname)").read().split()}))
prompt = argv[argv.index("-p") + 1]
ids = re.findall(r"id=([0-9a-f]{12})", prompt)
verdict = os.environ.get("FAKE_VERDICT", "fake")
reason = os.environ.get("FAKE_REASON", "generated by the fixture factory in this test")
body = {"verdicts": [{"id": i, "verdict": verdict, "reason": reason, "evidence": ["log"]}
                     for i in ids]}
print(json.dumps({"type": "result", "result": "done", "structured_output": body,
                  "permission_denials": []}))
"""


def _adjudicate(repo, tmp_path, **env):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    e = {"NORA_PII_CLAUDE": str(fake), "FAKE_CLAUDE_ARGV": str(tmp_path / "argv.json"), **env}
    out = repo.git("rev-parse", "--show-toplevel")
    assert out.returncode == 0
    return subprocess.run(
        [sys.executable, "scripts/pii_adjudicate.py"],
        cwd=repo.path,
        env={**repo.env, **e},
        capture_output=True,
        text=True,
    )


def test_a_fake_verdict_becomes_a_trailer_and_the_push_honours_it(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: p").returncode != 0
    out = _adjudicate(repo, tmp_path)
    assert out.returncode == 0, out.stderr
    seen = json.loads((tmp_path / "argv.json").read_text())
    argv = seen["argv"]
    for flag in ("--restricted", "--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "Bash"
    allowed = argv[argv.index("--allowedTools") + 1 :]
    allowed = allowed[: next(i for i, a in enumerate(allowed) if a.startswith("--"))]
    assert allowed == [f"Bash(python3 {repo.path}/scripts/pii_readonly.py:*)"] or all(
        a.startswith("Bash(python3 ") and a.endswith("pii_readonly.py:*)") for a in allowed
    )
    # the agent ran in a clone of what the remote has, not in the worktree
    assert os.path.realpath(seen["cwd"]) != os.path.realpath(repo.path)
    out = repo.git("commit", "-q", "-m", "test: p")
    assert out.returncode == 0, out.stderr
    body = repo.git("log", "-1", "--format=%B").stdout
    assert re.search(r"PII-Ack: [0-9a-f]{12} \(agent sonnet: fake, generated by the fixture", body)
    assert repo.push_scan().returncode == 0


def test_unsure_and_real_stay_blocked(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: p").returncode != 0
    for verdict in ("unsure", "real"):
        assert _adjudicate(repo, tmp_path, FAKE_VERDICT=verdict).returncode == 1
        assert repo.git("commit", "-q", "-m", "test: p").returncode != 0


def test_a_verdict_for_another_staged_tree_counts_for_nothing(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: p").returncode != 0
    assert _adjudicate(repo, tmp_path).returncode == 0
    repo.write("tests/q.py", "q = 1\n")
    repo.git("add", "tests/q.py")
    assert repo.git("commit", "-q", "-m", "test: p").returncode != 0


def test_the_adjudicator_refuses_while_a_denylist_hit_is_open(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\nQ = {NAME!r}\n')
    assert repo.commit("test: p").returncode != 0
    out = _adjudicate(repo, tmp_path)
    assert out.returncode == 2 and "denylist" in out.stderr
    assert not (tmp_path / "argv.json").exists()


def test_a_reason_that_quotes_a_value_is_withheld(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: p").returncode != 0
    quoted = f"it is {PHONE}, a fixture"
    out = _adjudicate(repo, tmp_path, FAKE_REASON=quoted)
    assert out.returncode == 0 and PHONE not in out.stdout + out.stderr
    assert repo.git("commit", "-q", "-m", "test: p").returncode == 0
    body = repo.git("log", "-1", "--format=%B").stdout
    assert PHONE not in body and "withheld" in body


def test_a_trailer_reason_is_scanned_even_if_the_verdict_file_was_written_by_hand(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    out = repo.commit("test: p")
    (fid,) = _ids(out)
    tree = repo.git("write-tree").stdout.strip()
    gitdir = repo.path / repo.git("rev-parse", "--git-dir").stdout.strip()
    (gitdir / "nora-pii-adjudication.json").write_text(
        json.dumps(
            {
                "tree": tree,
                "model": "sonnet",
                "verdicts": {fid: {"verdict": "fake", "reason": f"it is {PHONE}"}},
            }
        )
    )
    assert repo.git("commit", "-q", "-m", "test: p").returncode == 0
    body = repo.git("log", "-1", "--format=%B").stdout
    assert PHONE not in body and "withheld" in body


FAKE_LAYA = """#!/usr/bin/env python3
import json, sys
req = json.load(sys.stdin)
open(sys.argv[0] + ".seen", "w").write(json.dumps(req))
first = next((r[0] for f in req["files"] for r in f["rows"] if r[1] == "+"), None)
print(json.dumps({"score": 0.9, "ranked": [first] if first else [], "windows": 1}))
"""


def test_the_commit_judge_runs_at_commit_msg_and_raises_at_its_pick(repo, tmp_path):
    fake = tmp_path / "laya-python"
    fake.write_text(FAKE_LAYA)
    fake.chmod(0o755)
    env = {"NORA_PII_JUDGE": "laya", "NORA_PII_LAYA_PYTHON": str(fake)}
    repo.write("nora/a.py", "def f():\n    return compute(x)\n")
    out = repo.commit("feat: add f", env=env)
    assert out.returncode != 0
    assert re.search(r"judge:laya +nora/a.py:1", out.stderr), out.stderr
    seen = json.loads((tmp_path / "laya-python.seen").read_text())
    assert seen["message"][0][1] == "feat: add f"
    assert [f["path"] for f in seen["files"]] == ["nora/a.py"]


# ---- second review of #591


def test_a_denylisted_name_in_a_new_directory_is_refused_and_not_printed(repo):
    repo.write(f"people/{NAME}/notes.md", "hello\n")
    out = repo.commit("docs: notes")
    assert out.returncode != 0 and "in the file's path" in out.stderr
    assert NAME.split()[0] not in out.stderr and NAME.split()[1] not in out.stderr


def _declare(repo, line):
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + line + "\n")
    repo.git("add", ".githooks/pii-allowlist.txt")
    repo.git("commit", "-q", "--no-verify", "-m", "chore: declare")
    assert repo.git("push", "-q", "--no-verify", "origin", "main").returncode == 0


def test_a_star_declaration_is_an_error_not_an_off_switch(repo):
    _declare(repo, "path-exempt * -- vendored")
    repo.write("d/people.txt", f"{NAME}\n")
    out = repo.commit("chore: people")
    assert out.returncode != 0 and "allowlist-invalid" in out.stderr


def test_a_path_exempt_declaration_never_exempts_the_denylist(repo):
    _declare(repo, "path-exempt vendor/lib/data.txt -- vendored")
    repo.write("vendor/lib/data.txt", f"{NAME}\n")
    out = repo.commit("chore: vendor")
    assert out.returncode != 0 and "denylist:text" in out.stderr


def test_a_declaration_matching_too_many_files_is_an_error(repo):
    for i in range(201):
        repo.write(f"media/big/f{i}.bin", f"{i}\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "--no-verify", "-m", "chore: many")
    _declare(repo, "binary media/big/* -- many")
    repo.write("x.txt", "x\n")
    out = repo.commit("chore: x")
    assert out.returncode != 0 and "allowlist-too-broad" in out.stderr


def test_a_hand_written_adjudication_does_not_get_past_the_push(repo):
    """The pre-commit can be fooled by a forged verdict file; the trailer it
    writes then says `agent`, and pre-push finds no record for that tree."""
    repo.write("c.txt", f"call {SY.phone('852', 'spaced')} tonight\n")
    out = repo.commit("chore: c")
    ids = _ids(out)
    tree = repo.git("write-tree").stdout.strip()
    gitdir = repo.path / repo.git("rev-parse", "--git-dir").stdout.strip()
    verdicts = {i: {"verdict": "fake", "reason": "fixture"} for i in ids}
    (gitdir / "nora-pii-adjudication.json").write_text(
        json.dumps({"tree": tree, "model": "sonnet", "verdicts": verdicts})
    )
    assert repo.git("commit", "-q", "-m", "chore: c").returncode == 0
    assert "(agent sonnet" in repo.git("log", "-1", "--format=%B").stdout
    out = repo.push_scan()
    assert out.returncode == 1 and "no matching adjudication record" in out.stderr


def test_a_hand_typed_agent_trailer_is_refused_and_a_human_one_is_a_claim(repo):
    repo.write("c.txt", f"call {SY.phone('852', 'spaced')} tonight\n")
    (fid,) = _ids(repo.commit("chore: c"))
    repo.git(
        "commit", "-q", "--no-verify", "-m", f"chore: c\n\nPII-Ack: {fid} (agent opus: fake, ok)"
    )
    assert repo.push_scan().returncode == 1
    repo.git("commit", "-q", "--amend", "--no-verify", "-m", f"chore: c\n\nPII-Ack: {fid} (human)")
    assert repo.push_scan().returncode == 0


def test_a_human_ack_is_recorded_as_human(repo):
    repo.write("c.txt", f"call {SY.phone('852', 'spaced')} tonight\n")
    (fid,) = _ids(repo.commit("chore: c"))
    assert repo.commit("chore: c", env={"NORA_PII_ACK": fid}).returncode == 0
    assert f"PII-Ack: {fid} (human)" in repo.git("log", "-1", "--format=%B").stdout


def _raw_commit(repo, extra: str) -> None:
    head = repo.head()
    tree = repo.git("rev-parse", "HEAD^{tree}").stdout.strip()
    raw = (
        f"tree {tree}\nparent {head}\nauthor A <a@example.com> 1790000000 +0000\n"
        f"committer A <a@example.com> 1790000000 +0000\n{extra}\n\nchore: probe\n"
    )
    sha = repo.git("hash-object", "-t", "commit", "-w", "--stdin", "--literally", input=raw)
    repo.git("update-ref", "refs/heads/main", sha.stdout.strip())


def test_an_extra_commit_header_is_scanned(repo):
    _raw_commit(repo, f"x-owner {NAME}")
    out = repo.push_scan()
    assert out.returncode == 1 and "<commit-header>" in out.stderr


def test_a_mergetag_message_is_scanned(repo):
    _raw_commit(repo, "mergetag object " + "a" * 40 + f"\n type commit\n tag v1\n \n for {NAME}")
    assert repo.push_scan().returncode == 1


def test_a_cutover_to_an_empty_remote_needs_a_baseline_manifest(repo, tmp_path):
    repo.write("old.txt", f"{NAME}\n")
    assert repo.commit("chore: old", no_verify=True).returncode == 0
    residue = repo.head()
    repo.write("b.txt", "fine\n")
    assert repo.commit("chore: b").returncode == 0
    fresh = tmp_path / "fresh.git"
    repo.git("init", "-q", "--bare", "-b", "main", str(fresh))
    repo.git("remote", "add", "fresh", str(fresh))
    assert repo.push_scan("fresh").returncode == 1
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(f"# nora-pii-baseline v1 verified-by: test\n{residue}\n")
    repo.env["NORA_PII_PUSH_BASELINE"] = str(manifest)
    out = repo.push_scan("fresh")
    assert out.returncode == 0 and "CUTOVER BASELINE" in out.stderr


def test_pre_commit_does_not_trust_a_forged_tracking_ref_for_declarations(repo):
    other = SY.phone("44", "spaced")
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + f"value {other} -- forged\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "--no-verify", "-m", "chore: declare")
    repo.git("update-ref", "refs/remotes/origin/main", "HEAD")  # never pushed
    repo.write("p.txt", f'P = "{other}"\n')
    out = repo.commit("test: p")
    assert out.returncode != 0 and "declarations NOT honoured" in out.stderr


def test_the_adjudicator_without_its_agent_is_rc_2(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: p").returncode != 0
    out = _adjudicate(repo, tmp_path, NORA_PII_CLAUDE=str(tmp_path / "no-such-claude"))
    assert out.returncode == 2 and "not installed" in out.stderr


def test_the_adjudicator_works_on_what_is_published_not_on_the_change(repo, tmp_path):
    repo.write("tests/p.py", f'P = "{SY.phone("65", "spaced")}"\n')
    assert repo.commit("test: p").returncode != 0
    assert _adjudicate(repo, tmp_path).returncode == 0
    seen = json.loads((tmp_path / "argv.json").read_text())
    assert seen["staged_file_in_cwd"] is False  # the change is not in the evidence
    assert all(not b.startswith("refs/heads/main") for b in seen["branches"])  # no local branches
    prompt = seen["argv"][seen["argv"].index("-p") + 1]
    assert "tests/p.py" in prompt and "not evidence" in prompt
