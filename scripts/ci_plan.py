"""Hosted CI selection and fail-closed result join (not a backend test selector)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

from scripts import affected_tests

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


def classify(paths: list[str]) -> dict[str, bool]:
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
        if (path.startswith(".github/") and not affected_tests._is_doc_or_data(path)) or path in {
            "Makefile",
            "scripts/ci_plan.py",
        }:
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
        elif path.startswith("deploy/instagram-bridge/") and suffix != ".md":
            plan["go"] = plan["codeql-go"] = True
        elif affected_tests._is_doc_or_data(path):
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
        for path in changed_paths(event, repo):
            base = event["pull_request"]["base"]["sha"]
            head = event["pull_request"]["head"]["sha"]
            diff = subprocess.check_output(  # noqa: S603 -- fixed argv, validated SHAs
                ["git", "diff", "--no-ext-diff", "--unified=0", f"{base}...{head}", "--", path],  # noqa: S607
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
            rules = {r["id"]: r for r in run["tool"]["driver"]["rules"]}
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
    paths: list[str] = []
    docs: list[str] = []
    try:
        has_delta = "pull_request" in event or ("before" in event and "after" in event)
        paths = changed_paths(event, repo) if has_delta else []
        plan = classify(paths) if has_delta else dict.fromkeys(JOBS, True)
        docs = [p for p in paths if affected_tests._is_doc_or_data(p)]
        targets = affected_tests.select(docs, repo)
        # A shared helper may read the document: retain the selector's ALL
        # fallback and let testcontainers provision the full suite's services.
        error = ""
    except Exception as exc:  # noqa: BLE001 -- every selector failure broadens and fails CI
        plan, targets, error = dict.fromkeys(JOBS, True), [], str(exc)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        print("plan=" + json.dumps(plan), file=output)
        print("readers=" + json.dumps(targets), file=output)
        print("docs_only=" + str(bool(paths) and len(docs) == len(paths)).lower(), file=output)
        print("selection_error=" + json.dumps(error), file=output)
    # Broad work still runs; the final join must reject this error.
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
