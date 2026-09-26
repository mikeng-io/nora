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

    def push_scan(self) -> subprocess.CompletedProcess:
        line = f"refs/heads/main {self.head()} refs/heads/main {'0' * 40}\n"
        return subprocess.run(
            [sys.executable, "scripts/pii_scan.py", "push"],
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
    # the same declaration committed FIRST is honoured by the next commit
    repo.git("reset", "-q")
    assert repo.commit("chore: declare p", ".githooks/pii-allowlist.txt").returncode == 0
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
