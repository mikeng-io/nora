"""The identifier hooks against real git: every attack the gate defends against.

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

from tests.pii_synth import Synth

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SY = Synth(4242)
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
        "scripts/pii_cjk_fold.py",
        "scripts/check_commit_subject.py",
        ".githooks/pre-commit",
        ".githooks/commit-msg",
        ".githooks/pre-push",
        ".githooks/pii-allowlist.txt",
    ):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, work / rel)
    r.git("config", "core.hooksPath", ".githooks")
    # the scaffold is committed around the hooks: fifty fixtures each running
    # every hook over the scanner's own source is CPU the rest of the suite
    # needs. test_the_hook_scripts_pass_their_own_scan checks that source once.
    r.git("add", "-A")
    out = r.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "chore: scaffold the hooks")
    assert out.returncode == 0, out.stderr
    remote = tmp_path / "remote.git"
    r.git("init", "-q", "--bare", "-b", "main", str(remote))
    r.git("remote", "add", "origin", str(remote))
    assert r.git("-c", "core.hooksPath=/dev/null", "push", "-q", "origin", "main").returncode == 0
    return r


def _ids(out: subprocess.CompletedProcess) -> list[str]:
    return re.findall(r"id=([0-9a-f]{12})", out.stderr)


def test_the_hook_scripts_pass_their_own_scan(repo):
    out = subprocess.run(
        [sys.executable, "scripts/pii_scan.py", "commits", "HEAD"],
        cwd=repo.path,
        env=repo.env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr


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
    repo.write("nora/a.py", "x = 1\n")
    assert repo.commit("feat: a").returncode == 0
    out = repo.git("push", "-q", "origin", "main")
    both = out.stdout + out.stderr
    assert out.returncode == 0 and "pii push: PASS" in both
    assert "checked 1 commit subject(s)" in both
    # the identifier scan runs first; while it fails, subjects are not echoed
    repo.write("tests/rows.py", f"ROWS = [{NAME!r}]\n")
    assert repo.commit("test: rows", no_verify=True).returncode == 0
    out = repo.git("push", "-q", "origin", "main")
    both = out.stdout + out.stderr
    assert out.returncode != 0 and "pii push: FAIL" in both
    assert "subjects not checked" in both


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


# ---- what the push gives the TARGET remote


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


# ---- commit-message scissors, amend, merge and published declarations


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


# ---- declarations, acknowledgements and forged local state


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


def test_no_local_file_clears_a_finding(repo):
    """There is no adjudicator verdict file or log, and nothing in the git dir
    can stand in for one: a forged verdict file, a forged adjudication log and a
    forged ack side-file each leave the finding blocking, at commit and at
    push."""
    repo.write("c.txt", f"call {SY.phone('852', 'spaced')} tonight\n")
    out = repo.commit("chore: c")
    ids = _ids(out)
    assert ids
    tree = repo.git("write-tree").stdout.strip()
    gitdir = repo.path / repo.git("rev-parse", "--git-dir").stdout.strip()
    verdicts = {i: {"verdict": "fake", "reason": "fixture"} for i in ids}
    (gitdir / "nora-pii-adjudication.json").write_text(
        json.dumps({"tree": tree, "model": "sonnet", "verdicts": verdicts})
    )
    (gitdir / "nora-pii-adjudications.jsonl").write_text(
        json.dumps({"tree": tree, "model": "sonnet", "verdicts": verdicts}) + "\n"
    )
    (gitdir / "nora-pii-acks").write_text("".join(f"{i}\t\n" for i in ids))
    assert repo.git("commit", "-q", "-m", "chore: c").returncode != 0
    assert repo.commit("chore: c", no_verify=True).returncode == 0
    assert repo.push_scan().returncode == 1


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
    residue, _fresh, _every = _cutover(repo, tmp_path)
    assert repo.push_scan("fresh").returncode == 1
    f = tmp_path / "v1.txt"
    f.write_text(f"# nora-pii-baseline v1 verified-by: test\n{residue}\n")
    import hashlib

    repo.env.update(
        NORA_PII_PUSH_BASELINE=str(f),
        NORA_PII_PUSH_BASELINE_SHA256=hashlib.sha256(f.read_bytes()).hexdigest(),
    )
    out = repo.push_scan("fresh")
    assert out.returncode == 2 and "v1 manifests" in out.stderr  # unbound: refused


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


# ---- a branch behind its remote, and `git rebase`


def _hookless(repo, *args):
    return repo.git("-c", "core.hooksPath=/dev/null", *args)


def _remote_moves_on(repo, tmp_path, rel="other.txt", text="moved\n", branch="main"):
    """Another clone pushes to the remote's `branch`; this clone fetches."""
    n = len(list(tmp_path.glob("clone-*")))
    clone = tmp_path / f"clone-{n}"
    subprocess.run(  # noqa: S603
        ["git", "clone", "-q", "-b", branch, str(tmp_path / "remote.git"), str(clone)],
        env=repo.env,
        check=True,
        capture_output=True,
    )
    other = Repo(clone, repo.env)
    other.write(rel, f"{text}{n}\n")
    assert _hookless(other, "add", "-A").returncode == 0
    assert _hookless(other, "commit", "-q", "-m", "chore: remote moves on").returncode == 0
    assert _hookless(other, "push", "-q", "origin", branch).returncode == 0
    assert repo.git("fetch", "-q", "origin").returncode == 0


def test_a_branch_behind_its_remote_still_honours_a_published_declaration(repo, tmp_path):
    other = SY.phone("44", "spaced")
    _declare(repo, f"value {other} -- test placeholder")
    repo.git("checkout", "-q", "-b", "feature")
    _remote_moves_on(repo, tmp_path)  # the base is now an ANCESTOR of the remote's tip
    repo.write("p.txt", f'P = "{other}"\n')
    out = repo.commit("test: p")
    assert out.returncode == 0, out.stderr


def test_a_branch_behind_its_remote_still_reads_its_published_placeholders(repo, tmp_path):
    """Placeholders (identity.example.toml) come from the published base
    alone, so this is the base itself being recognised as published when it
    is an ancestor of the remote's tip, not equal to it."""
    local = SY.phone("852")
    repo.write("identity.example.toml", f'[[persons.x.signal]]\nphone = "{local}"\n')
    assert repo.commit("docs: example identity").returncode != 0  # a shape, not yet published
    assert repo.commit("docs: example identity", no_verify=True).returncode == 0
    assert _hookless(repo, "push", "-q", "origin", "main").returncode == 0
    repo.git("checkout", "-q", "-b", "feature")
    _remote_moves_on(repo, tmp_path)
    repo.write("p.txt", f'phone = "{local}"\n')
    out = repo.commit("test: p")
    assert out.returncode == 0, out.stderr


def _branch_with(repo, name, payload_rel, payload, env=None):
    """`name` off main: one commit that edits shared.txt and adds a payload."""
    repo.git("checkout", "-q", "-b", name, "main")
    repo.write("shared.txt", f"{name}\n")
    repo.write(payload_rel, payload)
    return repo.commit(f"test: {name}", env=env)


def _rebase_through_a_conflict(repo, tmp_path, branch, env=None):
    _remote_moves_on(repo, tmp_path, "shared.txt", "main\n")
    repo.git("checkout", "-q", "main")
    assert repo.git("merge", "-q", "--ff-only", "origin/main").returncode == 0
    repo.git("checkout", "-q", branch)
    out = repo.git("rebase", "main")
    assert out.returncode != 0 and "CONFLICT" in out.stdout + out.stderr
    assert (repo.path / repo.git("rev-parse", "--git-dir").stdout.strip() / "REBASE_HEAD").exists()
    repo.write("shared.txt", "both\n")
    repo.git("add", "shared.txt")
    # what an author does at a rebase stop: `git commit`, which fires both hooks
    return repo.git("commit", "-q", "-C", "REBASE_HEAD", env=env)


def _with_shared(repo):
    repo.write("shared.txt", "base\n")
    assert repo.commit("chore: shared").returncode == 0
    assert repo.git("push", "-q", "origin", "main").returncode == 0


def test_rebasing_a_branch_keeps_its_published_declaration(repo, tmp_path):
    _with_shared(repo)
    other = SY.phone("44", "spaced")
    repo.git("checkout", "-q", "-b", "feature")
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + f"value {other} -- test placeholder\n")
    assert repo.commit("chore: declare p", ".githooks/pii-allowlist.txt").returncode == 0
    assert repo.git("push", "-q", "origin", "feature").returncode == 0
    repo.write("shared.txt", "feature\n")
    repo.write("p.txt", f'P = "{other}"\n')
    out = repo.commit("test: p")
    assert out.returncode == 0, out.stderr
    assert repo.git("push", "-q", "origin", "feature").returncode == 0
    out = _rebase_through_a_conflict(repo, tmp_path, "feature")
    assert out.returncode == 0, out.stderr
    assert repo.git("-c", "core.editor=true", "rebase", "--continue").returncode == 0
    out = repo.git("push", "-q", "--force", "origin", "feature")
    assert out.returncode == 0, out.stdout + out.stderr


def test_rebasing_a_branch_keeps_an_acknowledged_finding_and_catches_an_unacknowledged_one(
    repo, tmp_path
):
    _with_shared(repo)
    payload = f"call {SY.phone('852', 'spaced')} tonight\n"
    out = _branch_with(repo, "acked", "c.txt", payload)
    (fid,) = _ids(out)
    assert repo.commit("test: acked", env={"NORA_PII_ACK": fid}).returncode == 0
    assert repo.git("push", "-q", "origin", "acked").returncode == 0
    out = _rebase_through_a_conflict(repo, tmp_path, "acked")
    assert out.returncode == 0, out.stderr
    assert f"PII-Ack: {fid} (human)" in repo.git("log", "-1", "--format=%B").stdout
    assert repo.git("-c", "core.editor=true", "rebase", "--continue").returncode == 0
    out = repo.git("push", "-q", "--force", "origin", "acked")
    assert out.returncode == 0, out.stdout + out.stderr
    # a finding nobody acknowledged is still caught at the rebase stop
    repo.git("checkout", "-q", "main")
    _branch_with(repo, "unacked", "d.txt", f"call {SY.phone('852', 'spaced')} later\n")
    assert repo.commit("test: unacked", no_verify=True).returncode == 0
    out = _rebase_through_a_conflict(repo, tmp_path, "unacked")
    assert out.returncode != 0 and "phone" in out.stderr


# ---- every declaration form is capped


def test_a_declaration_cannot_grow_past_the_cap_after_it_is_published(repo):
    _declare(repo, "binary media/later/* -- art, none yet")
    for i in range(201):
        repo.write(f"media/later/f{i}.bin", f"{i}\n")
    out = repo.commit("chore: art")
    assert out.returncode != 0 and "allowlist-too-broad" in out.stderr
    assert repo.commit("chore: art", no_verify=True).returncode == 0
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and "allowlist-too-broad" in out


def test_the_tree_audit_caps_worktree_declarations_too(repo):
    for i in range(201):
        repo.write(f"media/big/f{i}.bin", f"{i}\n")
    assert repo.commit("chore: many", no_verify=True).returncode == 0
    allow = (repo.path / ".githooks/pii-allowlist.txt").read_text()
    repo.write(".githooks/pii-allowlist.txt", allow + "binary media/big/* -- many\n")
    out = subprocess.run(
        [sys.executable, "scripts/pii_scan.py", "tree", "HEAD", "--allowlist-from-worktree"],
        cwd=repo.path,
        env=repo.env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2 and "allowlist-too-broad" in out.stderr


# ---- the repo's scanner, and masking before any output


FAKE_SCANNER = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"


@pytest.mark.parametrize("what", ["scanner", "scripts-dir"])
def test_a_symlinked_scanner_is_refused(repo, tmp_path, what):
    fake = tmp_path / "elsewhere" / "scripts"
    fake.mkdir(parents=True)
    for rel in ("pii_scan.py", "check_commit_subject.py", "pii_cjk_fold.py"):
        (fake / rel).write_text(FAKE_SCANNER)
    if what == "scanner":
        (repo.path / "scripts/pii_scan.py").unlink()
        (repo.path / "scripts/pii_scan.py").symlink_to(fake / "pii_scan.py")
    else:
        shutil.rmtree(repo.path / "scripts")
        (repo.path / "scripts").symlink_to(fake)
    repo.write("rows.txt", f"{NAME}\n")
    repo.git("add", "rows.txt")
    out = repo.git("commit", "-q", "-m", "test: rows")
    assert out.returncode != 0 and "pre-commit:" in out.stderr and "symlink" in out.stderr
    assert repo.git("commit", "-q", "--no-verify", "-m", "test: rows").returncode == 0
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and "pre-push:" in out and "symlink" in out


def test_a_planted_bytecode_cache_is_never_run(repo, tmp_path):
    """The scanner must not load bytecode from a shared
    $TMPDIR/nora-pii-pycache: a .pyc planted there must not run in place of the
    repo's pii_cjk_fold.py."""
    import importlib.util
    import marshal

    src = repo.path / "scripts" / "pii_cjk_fold.py"
    marker = tmp_path / "pwned"
    code = compile(f"open({str(marker)!r}, 'w').write('x')\nFOLD = {{}}\n", str(src), "exec")
    st = src.stat()
    prefix = tmp_path / "tmpdir" / "nora-pii-pycache"
    old = sys.pycache_prefix
    sys.pycache_prefix = str(prefix)
    try:
        pyc = Path(importlib.util.cache_from_source(str(src)))
    finally:
        sys.pycache_prefix = old
    pyc.parent.mkdir(parents=True)
    header = importlib.util.MAGIC_NUMBER + (0).to_bytes(4, "little")
    header += (int(st.st_mtime) & 0xFFFFFFFF).to_bytes(4, "little")
    header += (st.st_size & 0xFFFFFFFF).to_bytes(4, "little")
    pyc.write_bytes(header + marshal.dumps(code))
    repo.write("a.txt", "a\n")
    out = repo.commit("chore: a", env={"TMPDIR": str(tmp_path / "tmpdir")})
    assert out.returncode == 0, out.stderr
    assert not marker.exists()


def test_pythonpath_cannot_substitute_a_module_of_the_scanner(repo, tmp_path):
    """The hooks run `python3 -I -S`: a PYTHONPATH entry shadowing a module
    the scanner imports (here `bisect`) must not run in its place."""
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "pwned"
    (evil / "bisect.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\n"
        "from _bisect import *  # noqa\nbisect = bisect_right  # noqa\n"
    )
    repo.write("a.txt", "a\n")
    out = repo.commit("chore: a", env={"PYTHONPATH": str(evil)})
    assert out.returncode == 0, out.stderr
    assert not marker.exists()


def test_a_bad_subject_never_prints_an_identifier(repo):
    repo.write("a.txt", "a\n")
    out = repo.commit(f"update the notes for {NAME}")
    assert out.returncode != 0 and "denylist:text" in out.stderr
    assert NAME.split()[0] not in out.stderr and NAME.split()[1] not in out.stderr
    assert repo.commit(f"update the notes for {NAME}", no_verify=True).returncode == 0
    rc, out = _push(repo, "origin", "main")
    assert rc != 0 and NAME.split()[0] not in out and NAME.split()[1] not in out


def test_a_ref_name_is_scanned_and_never_printed(repo):
    given, family = (w.lower() for w in NAME.split())
    repo.git("tag", "-a", f"notes-{given}-{family}", "-m", f"call {SY.phone('852', 'spaced')}")
    rc, out = _push(repo, "origin", f"notes-{given}-{family}")
    assert rc != 0 and "denylist:text" in out
    assert given not in out.lower() and family not in out.lower()
    repo.git("branch", f"for-{given}-{family}")
    rc, out = _push(repo, "origin", f"for-{given}-{family}")
    assert rc != 0 and "denylist:text" in out and given not in out.lower()


# ---- a cutover manifest is bound to its commits, its remote
# and a digest pinned outside it


def _cutover(repo, tmp_path):
    repo.write("old.txt", f"{NAME}\n")
    assert repo.commit("chore: old", no_verify=True).returncode == 0
    residue = repo.head()
    repo.write("b.txt", "fine\n")
    assert repo.commit("chore: b").returncode == 0
    fresh = tmp_path / "fresh.git"
    repo.git("init", "-q", "--bare", "-b", "main", str(fresh))
    repo.git("remote", "add", "fresh", str(fresh))
    every = repo.git("rev-list", "HEAD").stdout.split()
    return residue, str(fresh), every


def _manifest(tmp_path, remote, ids, name="m.txt"):
    import hashlib

    f = tmp_path / name
    f.write_text(f"# nora-pii-baseline v2\nremote {remote}\n" + "".join(i + "\n" for i in ids))
    return f, hashlib.sha256(f.read_bytes()).hexdigest()


def test_a_cutover_manifest_counts_only_the_commits_it_lists(repo, tmp_path):
    _residue, fresh, every = _cutover(repo, tmp_path)
    tip = repo.head()
    f, digest = _manifest(tmp_path, fresh, [tip])  # the tip only: its ancestors are NOT clean
    repo.env.update(NORA_PII_PUSH_BASELINE=str(f), NORA_PII_PUSH_BASELINE_SHA256=digest)
    out = repo.push_scan("fresh")
    assert out.returncode == 1 and "old.txt" in out.stderr, out.stderr
    f, digest = _manifest(tmp_path, fresh, every)
    repo.env.update(NORA_PII_PUSH_BASELINE=str(f), NORA_PII_PUSH_BASELINE_SHA256=digest)
    out = repo.push_scan("fresh")
    assert out.returncode == 0 and "CUTOVER BASELINE" in out.stderr, out.stderr


def test_a_cutover_manifest_is_bound_to_its_remote_and_its_pinned_digest(repo, tmp_path):
    _residue, fresh, every = _cutover(repo, tmp_path)
    f, _digest = _manifest(tmp_path, fresh, every)
    for env in (
        {"NORA_PII_PUSH_BASELINE": str(f)},  # no pinned digest
        {"NORA_PII_PUSH_BASELINE": str(f), "NORA_PII_PUSH_BASELINE_SHA256": "0" * 64},
    ):
        repo.env.pop("NORA_PII_PUSH_BASELINE_SHA256", None)
        repo.env.update(env)
        out = repo.push_scan("fresh")
        assert out.returncode == 2 and "NORA_PII_PUSH_BASELINE" in out.stderr, out.stderr
    g, gdigest = _manifest(tmp_path, str(tmp_path / "remote.git"), every, "g.txt")
    repo.env.update(NORA_PII_PUSH_BASELINE=str(g), NORA_PII_PUSH_BASELINE_SHA256=gdigest)
    out = repo.push_scan("fresh")
    assert out.returncode == 2 and "remote" in out.stderr, out.stderr
