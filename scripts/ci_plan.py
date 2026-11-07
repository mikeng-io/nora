"""Hosted CI selection and fail-closed result join (not a backend test selector)."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from scripts import affected_tests, testmon_record

JOBS = (
    "python",
    "contracts",
    "portal",
    "go",
    "site",
    "dependency-review",
    "codeql-python",
    "codeql-javascript",
    "codeql-go",
    "codeql-actions",
)


def classify(paths: list[str], repo: Path = affected_tests.ROOT) -> dict[str, bool]:
    plan = dict.fromkeys(JOBS, False)
    plan["contracts"] = True
    for path in paths:
        # These component contracts read shared generated schemas/fixtures.
        if path.startswith("nora/documents/"):
            plan["portal"] = plan["codeql-javascript"] = True
        if path.startswith("tests/fixtures/instagram_bridge/"):
            plan["go"] = plan["codeql-go"] = True
        name = Path(path).name
        suffix = Path(path).suffix
        # Build and analyzer policy can affect every component.
        if (
            path.startswith(".github/") and not affected_tests._is_doc_or_data(path, repo)
        ) or path in {"Makefile", "scripts/ci_plan.py"}:
            return dict.fromkeys(JOBS, True)
        dependency = name in {
            "pyproject.toml",
            "uv.lock",
            "package.json",
            "package-lock.json",
            "go.mod",
            "go.sum",
        }
        if dependency:
            plan["dependency-review"] = True
        if path.startswith(("site/", "docs-site/", "openwiki/", "assets/")):
            plan["site"] = True
            if (
                suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".astro", ".mdx"}
                or dependency
                or name.endswith("config.json")
            ):
                plan["codeql-javascript"] = True
        elif path.startswith("app/"):
            if suffix != ".md" or dependency:
                plan["portal"] = True
                if (
                    suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".mdx"}
                    or dependency
                    or name.endswith("config.json")
                ):
                    plan["codeql-javascript"] = True
        elif (
            path.startswith(("deploy/instagram-bridge/", "deploy/http-egress/")) and suffix != ".md"
        ):
            plan["go"] = plan["codeql-go"] = True
        elif affected_tests._is_doc_or_data(path, repo):
            continue
        elif path.startswith(("nora/", "evals/", "tests/", "scripts/")) or path in {
            "pyproject.toml",
            "uv.lock",
        }:
            plan["python"] = True
            # Runtime Markdown changes behavior but is not Python source.
            if suffix == ".py" or dependency:
                plan["codeql-python"] = True
            if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".mdx"}:
                plan["codeql-javascript"] = True
        else:
            return dict.fromkeys(JOBS, True)
    return plan


RELEASE_HEAD_REF = "release-please--branches--master--components--nora"
_RELEASE_WAIT_SECONDS = 75 * 60

# The release bypass applies only to a diff release-please could have written
# (checked against the 7.16.0 merge, commit 2cb15623a): these paths only,
# modifications only, and inside each strict file only its release FIELD,
# compared semantically — a branch name alone never earns the bypass.
_RELEASE_FILES = frozenset({
    ".release-please-manifest.json", "CHANGELOG.md",
    "nora/__init__.py", "pyproject.toml", "uv.lock",
})


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git argv, repo-local
        ["git", *args], cwd=repo, capture_output=True, text=True,  # noqa: S607
        check=True,
    ).stdout


def _init_version_only(old: str, new: str) -> bool:
    """Identical ASTs except the module-level `__version__` constant — a
    function-local `__version__` edit changes the tree and is not a release."""
    try:
        ot, nt = ast.parse(old), ast.parse(new)
    except SyntaxError:
        return False
    def scrub(tree):
        hits = 0
        for node in tree.body:
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target] if isinstance(node, ast.AnnAssign) else []
            )
            if any(isinstance(t, ast.Name) and t.id == "__version__" for t in targets):
                node.value = ast.Constant("__release__")
                hits += 1
        return hits
    return scrub(ot) == 1 and scrub(nt) == 1 and ast.dump(ot) == ast.dump(nt)


def _pyproject_version_only(old: str, new: str) -> bool:
    """Identical TOML except `project.version`, which must differ."""
    try:
        o, n = tomllib.loads(old), tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return False
    op, np = o.get("project"), n.get("project")
    if not isinstance(op, dict) or not isinstance(np, dict):
        return False
    ov, nv = op.get("version"), np.get("version")
    if not isinstance(ov, str) or not isinstance(nv, str) or ov == nv:
        return False
    op["version"] = np["version"] = ov
    return o == n


def _uvlock_version_only(old: str, new: str) -> bool:
    """Identical TOML except the `version` of the `[[package]]` named nora."""
    try:
        o, n = tomllib.loads(old), tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return False
    def nora(doc):
        for pkg in doc.get("package", []):
            if isinstance(pkg, dict) and pkg.get("name") == "nora":
                return pkg
        return None
    op, np = nora(o), nora(n)
    if op is None or np is None:
        return False
    ov, nv = op.get("version"), np.get("version")
    if not isinstance(ov, str) or not isinstance(nv, str) or ov == nv:
        return False
    op["version"] = np["version"] = ov
    return o == n


def _manifest_version_only(old: str, new: str) -> bool:
    """Identical JSON object except the nora component version (`.`)."""
    try:
        o, n = json.loads(old), json.loads(new)
    except ValueError:
        return False
    if not isinstance(o, dict) or not isinstance(n, dict) or set(o) != set(n):
        return False
    if not isinstance(o.get("."), str) or not isinstance(n.get("."), str):
        return False
    if o["."] == n["."]:
        return False
    return all(k == "." or o[k] == n[k] for k in o)


# The bounded rule for the strict release files: the whole diff is ONE
# removed line and ONE added line, ASCII, matching the file's version
# pattern — then the parsed-structure compare confirms the line is the
# field it spells (a docstring line or another TOML section cannot qualify).
_STRICT_LINE = {
    "nora/__init__.py": rb'^__version__ = "\d+\.\d+\.\d+"$',
    "pyproject.toml": rb'^version = "\d+\.\d+\.\d+"$',
    "uv.lock": rb'^version = "\d+\.\d+\.\d+"$',
    ".release-please-manifest.json": rb'^\s+"\.": "\d+\.\d+\.\d+"$',
}


def _git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed git argv, repo-local
        ["git", *args], cwd=repo, capture_output=True, check=True,  # noqa: S607
    ).stdout


def _one_line_bump(repo: Path, range_: str, path: str) -> bool:
    """The strict shape, read as BYTES: ASCII only, exactly one removed and
    one added line, both matching the file's full-line version pattern and
    differing. A second hunk, CRLF, BOM or any non-ASCII byte is ineligible."""
    patch = _git_bytes(repo, "diff", "-U0", range_, "--", path)
    try:
        text = patch.decode("ascii")
    except UnicodeDecodeError:
        return False
    # split on \n only: splitlines() would eat the \r of a CRLF line and let
    # it pass the pattern; the trailing \r must fail it instead
    added = [line[1:] for line in text.split("\n")
             if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in text.split("\n")
               if line.startswith("-") and not line.startswith("---")]
    if len(added) != 1 or len(removed) != 1 or added == removed:
        return False
    pattern = re.compile(_STRICT_LINE[path])
    return bool(
        pattern.match(added[0].encode()) and pattern.match(removed[0].encode())
    )


def _additions_only(repo: Path, range_: str, path: str) -> bool:
    """CHANGELOG.md keeps free text but pure additions: no removed lines."""
    patch = _git(repo, "diff", "-U0", range_, "--", path)
    added = [line for line in patch.split("\n")
             if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in patch.split("\n")
               if line.startswith("-") and not line.startswith("---")]
    return bool(added) and not removed


def _release_semantic(repo: Path, range_: str, path: str, old: str, new: str) -> bool:
    if path == "CHANGELOG.md":
        return _additions_only(repo, range_, path)
    if not _one_line_bump(repo, range_, path):
        return False
    if path == "nora/__init__.py":
        return _init_version_only(old, new)
    if path == "pyproject.toml":
        return _pyproject_version_only(old, new)
    if path == "uv.lock":
        return _uvlock_version_only(old, new)
    return _manifest_version_only(old, new)


def _diff_entries(repo: Path, range_: str) -> list[tuple[str, str, str, str]]:
    """(path, src_mode, dst_mode, status) per `git diff --raw` line."""
    entries = []
    for line in _git(repo, "diff", "--raw", range_).splitlines():
        meta, _, path = line.partition("\t")
        fields = meta.lstrip(":").split()
        entries.append((path, fields[0], fields[1], fields[4]))
    return entries


def release_eligible(repo: Path, event: dict) -> bool:
    """The release bypass: same-repository release branch AND a diff that
    release-please could have written — release files only, modifications
    only (a delete, rename or mode change is not), and inside each strict
    file only its release field, checked semantically (AST/TOML/JSON), not
    by line spelling. Anything else — a fork, a borrowed branch name, a
    runtime or content change — runs the normal selected acceptance."""
    pr = event.get("pull_request") or {}
    head, base = pr.get("head") or {}, pr.get("base") or {}
    if head.get("ref") != RELEASE_HEAD_REF:
        return False
    if (head.get("repo") or {}).get("full_name") != os.environ.get(
        "GITHUB_REPOSITORY"
    ):
        return False
    try:
        range_ = f"{base['sha']}...{head['sha']}"
        entries = _diff_entries(repo, range_)
        if not entries or any(
            status != "M" or src_mode != "100644" or dst_mode != "100644"
            for _, src_mode, dst_mode, status in entries
        ):
            return False
        if any(path not in _RELEASE_FILES for path, *_ in entries):
            return False
        for path, *_ in entries:
            old = _git(repo, "show", f"{base['sha']}:{path}")
            new = _git(repo, "show", f"{head['sha']}:{path}")
            if not _release_semantic(repo, range_, path, old, new):
                return False
    except Exception:  # noqa: BLE001 - an unreadable diff is never eligible
        return False
    return True


def base_run_verdict(runs: list[dict]) -> str:
    """The newest CI run on the release PR's base commit.

    `success` only when that run completed `success`; `pending` while it is
    still running; `missing` when no run exists; `failure` for every other
    conclusion — a release never publishes a commit the full suite has not
    passed.
    """
    if not runs:
        return "missing"
    latest = runs[0]
    if latest.get("status") != "completed":
        return "pending"
    return "success" if latest.get("conclusion") == "success" else "failure"


def _base_run(repo: Path, sha: str) -> list[dict]:
    listing = json.loads(testmon_record._gh(
        repo, "api",
        f"repos/{testmon_record._repo_name(repo)}/actions/runs"
        f"?head_sha={sha}&event=push&branch=master&per_page=10",
    ))
    return [
        run for run in listing.get("workflow_runs", [])
        if run.get("path") == ".github/workflows/ci.yml"
    ]


def _covered(selected, node_file: str) -> bool:
    return selected == affected_tests.ALL or any(
        node_file == target or node_file.startswith(target.rstrip("/") + "/")
        for target in selected
    )


def escape_lines(failed: list[str], selected) -> list[str]:
    """Each post-merge failure and whether the merged diff's selection ran it.

    A test red on master that the diff's selection skipped is an escape — the
    counter the design's reversal condition counts.
    """
    escapes = [node for node in failed if not _covered(selected, node.split("::", 1)[0])]
    lines = [
        "### Escape report",
        f"failed tests: {len(failed)}, escapes (red, not selected): {len(escapes)}",
    ]
    for node in failed:
        mark = "ESCAPED" if node in escapes else "selected"
        lines.append(f"- `{node}` — {mark}")
    return lines


def _node_path(classname: str) -> str:
    parts = classname.split(".")
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            return "/".join(parts[: i + 1]) + ".py"
    return "/".join(parts) + ".py"


def _junit_failures(directory: Path) -> list[str]:
    failed = []
    for report in sorted(directory.glob("*.xml")):
        for case in ET.parse(report).iter("testcase"):
            if any(child.tag in ("failure", "error") for child in case):
                file = case.get("file") or _node_path(case.get("classname", ""))
                failed.append(f"{file}::{case.get('name', '')}")
    return failed


def _escape(repo: Path, event: dict) -> list[str]:
    """Selection the merged diff would have had, versus what actually failed.

    The record just written by this run's full suite reflects the MERGED tree;
    the pull request would have used the previous one, so the escape check
    fetches the newest matching artifact (this run's upload happens later) and
    puts the fresh record back afterwards.
    """
    fresh = repo / testmon_record.DB_NAME
    stash = repo / (testmon_record.DB_NAME + ".recorded")
    try:
        if fresh.is_file():
            fresh.rename(stash)
        testmon_record.fetch(repo)
        try:
            paths = changed_paths(event, repo)
            selected = affected_tests.select(paths, repo) if paths else affected_tests.ALL
        except Exception as exc:  # noqa: BLE001 - never report a made-up selection
            return [
                "### Escape report",
                f"unavailable: could not reconstruct the merged diff's selection ({exc})",
            ]
        return escape_lines(_junit_failures(repo / "backend-results"), selected)
    finally:
        (repo / testmon_record.DB_NAME).unlink(missing_ok=True)
        if stash.is_file():
            stash.rename(fresh)


def verify(plan: dict[str, bool], needs: dict) -> bool:
    if set(plan) != set(JOBS) or any(type(v) is not bool for v in plan.values()):
        return False
    if needs.get("classify", {}).get("result") != "success":
        return False
    return all(
        needs.get(job, {}).get("result") == ("success" if selected else "skipped")
        for job, selected in plan.items()
    )


def changed_paths(event: dict, repo: Path) -> list[str]:
    """Full local merge-base diff, no API cap; renames expose both paths."""
    if "pull_request" in event:
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
    else:
        base, head = event["before"], event["after"]
    # SHAs are argv values, never shell or git options.
    if any(len(ref) != 40 or any(c not in "0123456789abcdef" for c in ref) for ref in (base, head)):
        raise ValueError("Expected immutable commit SHAs")
    merge = subprocess.check_output(["git", "merge-base", base, head], cwd=repo, text=True).strip()  # noqa: S603,S607
    data = subprocess.check_output(  # noqa: S603 -- fixed argv, validated refs
        ["git", "diff", "--name-only", "--no-renames", "-z", merge, head],  # noqa: S607
        cwd=repo,
    )
    return [p.decode("utf-8") for p in data.split(b"\0") if p]


def codeql_findings(event: dict, repo: Path, directory: Path) -> int:
    """Count high/critical or error findings on added PR lines, never raw code.

    Analysis still covers the full language. Baseline events upload existing
    findings without blocking on historical alerts; PRs gate newly changed lines.
    Malformed or absent SARIF raises so the scan cannot silently pass.
    """
    files = list(directory.glob("*.sarif"))
    if not files:
        raise ValueError("CodeQL produced no SARIF")
    changed: dict[str, set[int]] = {}
    if "pull_request" in event:
        # CodeQL analyzes the event's merge checkout, so its locations must
        # match that tree rather than the PR head's pre-merge line numbers.
        parents = subprocess.check_output(
            ["git", "rev-list", "--parents", "-n", "1", "HEAD"],  # noqa: S607
            cwd=repo, text=True,
        ).split()
        if len(parents) != 3 or parents[2] != event["pull_request"].get("head", {}).get("sha"):
            raise ValueError("Expected the analyzed PR merge checkout")
        paths = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", "HEAD^1", "HEAD"],  # noqa: S607
            cwd=repo,
        )
        for raw_path in paths.split(b"\0"):
            if not raw_path:
                continue
            path = raw_path.decode("utf-8")
            diff = subprocess.check_output(  # noqa: S603 -- fixed refs, paths after --
                ["git", "diff", "--no-ext-diff", "--unified=0", "HEAD^1", "HEAD", "--", path],  # noqa: S607
                cwd=repo, text=True,
            )
            lines: set[int] = set()
            for start, length in re.findall(r"^@@ .* \+(\d+)(?:,(\d+))? @@", diff, re.MULTILINE):
                lines.update(range(int(start), int(start) + (int(length) if length else 1)))
            changed[path] = lines
    blocked = 0
    for file in files:
        runs = json.loads(file.read_text())["runs"]
        if not runs:
            raise ValueError("CodeQL SARIF contains no analysis runs")
        for run in runs:
            if any(i.get("executionSuccessful") is False for i in run.get("invocations", [])):
                raise ValueError("CodeQL reported an unsuccessful analysis")
            components = [run["tool"]["driver"], *run["tool"].get("extensions", [])]
            rules = {r["id"]: r for component in components for r in component.get("rules", [])}
            for result in run["results"]:
                rule = rules[result["ruleId"]]
                severity = float(rule.get("properties", {}).get("security-severity", 0))
                level = result.get("level", rule.get("defaultConfiguration", {}).get("level"))
                if severity < 7 and level != "error":
                    continue
                for location in result["locations"]:
                    physical = location["physicalLocation"]
                    path = unquote(physical["artifactLocation"]["uri"])
                    region = physical["region"]
                    if changed.get(path, set()).intersection(
                        range(region["startLine"], region.get("endLine", region["startLine"]) + 1),
                    ):
                        blocked += 1
                        break
    return blocked


def main() -> int:
    if sys.argv[1:] == ["verify"]:
        return 0 if verify(json.loads(os.environ["PLAN"]), json.loads(os.environ["NEEDS"])) else 1
    repo = Path.cwd()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if sys.argv[1:] == ["codeql"]:
        blocked = codeql_findings(event, repo, repo / ".codeql-results")
        print(f"CodeQL findings above threshold on added PR lines: {blocked}")
        return 1 if blocked else 0
    if sys.argv[1:] == ["release"]:
        # A release PR does not run the suite: it waits for the full run on its
        # base commit and passes only on `success` — never a skip.
        sha = event["pull_request"]["base"]["sha"]
        deadline = time.monotonic() + _RELEASE_WAIT_SECONDS
        while True:
            verdict = base_run_verdict(_base_run(repo, sha))
            print(f"full run on base {sha[:12]}: {verdict}", flush=True)
            if verdict != "pending" or time.monotonic() >= deadline:
                break
            time.sleep(60)
        return 0 if verdict == "success" else 1
    if sys.argv[1:] == ["escape"]:
        print("\n".join(_escape(repo, event)))
        return 0
    paths: list[str] = []
    docs: list[str] = []
    eligible = False
    try:
        is_pr = "pull_request" in event
        paths = changed_paths(event, repo) if (
            is_pr or ("before" in event and "after" in event)) else []
        plan = classify(paths, repo) if is_pr else dict.fromkeys(JOBS, True)
        docs = [p for p in paths if affected_tests._is_doc_or_data(p, repo)]
        # The record only exists inside the contracts job; classify defers the
        # recorded-map class (testmon=False) and flags it for the job instead.
        testmon = is_pr and any(affected_tests.needs_record(p) for p in paths)
        # Non-PR events record: a push or schedule runs the WHOLE suite so the
        # uploaded record covers every test, and the release check can trust a
        # green run on the base commit as "the full suite passed".
        targets = affected_tests.select(paths, repo, testmon=False) if is_pr else "ALL"
        eligible = is_pr and release_eligible(repo, event)
        error = ""
    except Exception as exc:  # noqa: BLE001 -- every selector failure broadens and fails CI
        plan, targets, testmon, error = dict.fromkeys(JOBS, True), "ALL", True, str(exc)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        print("plan=" + json.dumps(plan), file=output)
        print("readers=" + json.dumps(targets), file=output)
        print("docs_only=" + str(bool(paths) and len(docs) == len(paths)).lower(), file=output)
        print("testmon=" + str(testmon).lower(), file=output)
        print("release_eligible=" + str(eligible).lower(), file=output)
        print("selection_error=" + json.dumps(error), file=output)
    # Broad work still runs; the final join must reject this error.
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
