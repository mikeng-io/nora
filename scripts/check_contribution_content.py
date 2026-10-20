"""Validate live contribution metadata using trusted default-branch code only.

PR text is required-check input, never executable code. Issue feedback and local
spelling/style suggestions are advisory. No model or language service is called.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts.check_commit_subject import SUBJECT_RE, check_subject

ROOT = Path(__file__).resolve().parents[1]
PR_HEADINGS = re.findall(
    r"^## (.+)$", (ROOT / ".github/pull_request_template.md").read_text(), re.M
)
FREEFORM = [
    "What",
    "Why the current shape blocks it",
    "Shape",
    "Decisions to record",
    "Not in this issue",
]
DESIGN = ["The gap", "What's specified (decided, not open)", "Not built", "Depends on / feeds"]
PUBLIC_ISSUES = {
    "bug": [
        "Description",
        "Steps to reproduce",
        "Expected behavior",
        "Actual behavior",
        "Version and environment",
    ],
    "enhancement": ["Problem", "Proposed solution"],
}
MARKER = "<!-- nora-contribution-feedback -->"
PLACEHOLDER = re.compile(
    r"^(?:TODO|TBD|PENDING(?:_[A-Z]+)*|\.{3}|"
    r"<(?:what|why|how|describe|insert|fill|gate result|test result)\b[^>]*>)$",
    re.I,
)


def prose(text: str, *, include_code: bool = False) -> list[str]:
    """Ignore comments and fenced examples when interpreting headings/prompts."""
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.S)
    lines, fence = [], ""
    for line in text.splitlines():
        mark = re.match(r"^\s*(`{3,}|~{3,})", line)
        if mark:
            if not fence:
                fence = mark[1]
            elif mark[1][0] == fence[0] and len(mark[1]) >= len(fence):
                fence = ""
            continue
        if not fence:
            lines.append(line)
        elif include_code:
            # Keep code as content without promoting its headings to form fields.
            lines.append("    " + line)
    return lines


def sections(text: str, levels=(2,), *, include_code: bool = False) -> list[tuple[str, str]]:
    result = []
    heading, lines = None, []
    for line in prose(text, include_code=include_code):
        match = re.match(r"^(#{2,3})\s+(.+?)\s*#*\s*$", line)
        if match and len(match[1]) in levels:
            if heading is not None:
                result.append((heading, "\n".join(lines)))
            heading, lines = match[2], []
        else:
            lines.append(line)
    if heading is not None:
        result.append((heading, "\n".join(lines)))
    return result


def has_placeholders(text: str) -> bool:
    for line in prose(text):
        value = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?", "", line)
        if PLACEHOLDER.fullmatch(value.strip(" *_`~#>")):
            return True
        line = re.sub(r"`[^`]*`", "", line)
        if PLACEHOLDER.fullmatch(line.strip()) or re.search(
            r"<(?:what|why|how|describe|insert|fill|gate result|test result)\b[^>]*>", line, re.I
        ):
            return True
    return False


def substantive(text: str) -> bool:
    clean = " ".join(prose(text)).strip()
    return (
        sum(ch.isalnum() for ch in clean) >= 4
        and clean.lower() not in {"none", "n/a", "not applicable", "_no response_"}
        and not PLACEHOLDER.fullmatch(clean)
    )


def pr_problems(item: dict, repository: str, release_login: str = "") -> list[str]:
    title, body = item["title"].strip(), item.get("body") or ""
    errors = []
    if not SUBJECT_RE.fullmatch(title) or check_subject(title):
        errors.append("Use a declared Conventional Commit title, e.g. `docs: clarify setup`.")
    login = item["user"]["login"]
    owned_bot = (
        item["user"]["type"] == "Bot"
        and (item["head"].get("repo") or {}).get("full_name") == repository
    )
    release = (
        owned_bot
        and login in {"github-actions[bot]", release_login}
        and item["head"]["ref"].startswith("release-please--branches--master")
        and any(x["name"] == "autorelease: pending" for x in item["labels"])
    )
    if release:
        if not (
            re.fullmatch(r"chore\(master\): release v?\d+\.\d+\.\d+(?:-[\w.-]+)?", title)
            and body.startswith(":robot: I have created a release")
            and re.search(r"^## \[v?\d+\.\d+\.\d+", body, re.M)
        ):
            errors.append(
                "The release PR must contain Release Please's generated title and changelog."
            )
        return errors
    if owned_bot and login == "dependabot[bot]" and item["head"]["ref"].startswith("dependabot/"):
        if not re.search(r"\b(?:Bumps|Updates)\s", body):
            errors.append(
                "The dependency PR must contain Dependabot's generated update description."
            )
        return errors
    found = sections(body)
    names = [name for name, _ in found]
    if len(set(names)) != len(names):
        errors.append("Remove duplicate PR sections.")
    if any(name not in PR_HEADINGS for name in names):
        errors.append("Use the PR template headings; `Summary` and `Test plan` are retired.")
    elif names != sorted(names, key=PR_HEADINGS.index):
        errors.append("Keep PR sections in the order shown in the template.")
    mapping = dict(found)
    for name in ("What", "Gate on the merged tree"):
        if not substantive(mapping.get(name, "")):
            errors.append(f"Fill in `## {name}` with actual information/results.")
    if has_placeholders(title + "\n" + body) or PLACEHOLDER.fullmatch(
        title.partition(":")[2].strip()
    ):
        errors.append("Replace unfilled TODO/TBD/PENDING or angle-bracket prompts.")
    return errors


def issue_problems(item: dict) -> list[str]:
    errors = []
    labels = {label["name"] for label in item["labels"]} & (
        {"freeform", "design-anchored"} | PUBLIC_ISSUES.keys()
    )
    public = labels & PUBLIC_ISSUES.keys()
    if public:
        if not substantive(item["title"]):
            errors.append("Use a descriptive issue title.")
    elif not re.fullmatch(r"[^:\n]+: \S[^\n]*", item["title"].strip()):
        errors.append("Use an issue title shaped `<Area>: <the gap>`.")
    if len(labels) != 1:
        errors.append(
            "Choose exactly one issue form: bug, enhancement, freeform or design-anchored."
        )
    body = item.get("body") or ""
    mapping = dict(sections(body, levels=(2, 3), include_code=bool(public)))
    design = "design-anchored" in labels
    required = PUBLIC_ISSUES[sorted(public)[0]] if public else (DESIGN if design else FREEFORM)
    if design:
        anchor = re.search(r"^\*\*Design:\*\* (.+)$", body, re.M)
        if not anchor or not substantive(anchor[1]):
            required = ["Design anchor", "What it covers", *required]
    # Area is already supplied by the title; CLI/API users need not duplicate it.
    for name in required:
        text = mapping.get(name, "").strip()
        uncertain = name in {
            "Decisions to record",
            "Not built",
            "Depends on / feeds",
            "Not in this issue",
        } and text.lower() in {"#tbd", "tbd", "none", "nothing", "undecided", "n/a"}
        if not uncertain and not substantive(text):
            errors.append(f"Complete the `{name}` section using the matching issue form.")
    return errors


def style_advice(body: str) -> list[str]:
    advice = []
    if any(len(line) > 160 for line in prose(body)):
        advice.append("Consider wrapping long prose lines for easier review.")
    if any(line.endswith((" ", "\t")) for line in body.splitlines()):
        advice.append("Consider removing trailing whitespace.")
    return advice


def api(method: str, path: str, body=None):
    # GitHub supplies the API origin; contribution text never controls request URLs.
    origin = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(  # noqa: S310 -- trusted GitHub API origin
        origin + path,
        method=method,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- trusted GitHub origin
        data = response.read()
    return json.loads(data) if data else None


def save_text(item: dict) -> None:
    if directory := os.environ.get("RUNNER_TEMP"):
        Path(directory, "nora-contribution.md").write_text(
            item["title"] + "\n\n" + (item.get("body") or ""), encoding="utf-8"
        )


def check_pr(github, repository: str, number: int, event_head: str = "") -> int:
    try:
        item = github("GET", f"/repos/{repository}/pulls/{number}")
    except Exception:
        # An edit can invalidate metadata without changing SHA. Replace any old green result.
        if event_head:
            github(
                "POST",
                f"/repos/{repository}/check-runs",
                {
                    "name": "PR content",
                    "head_sha": event_head,
                    "status": "completed",
                    "conclusion": "failure",
                    "output": {
                        "title": "PR content unavailable",
                        "summary": "Current metadata could not be read. Re-run this check.",
                    },
                },
            )
        raise
    save_text(item)
    run = github(
        "POST",
        f"/repos/{repository}/check-runs",
        {"name": "PR content", "head_sha": item["head"]["sha"], "status": "in_progress"},
    )
    try:
        # Private Apps cannot be looked up with this repository's GITHUB_TOKEN.
        # Pin GitHub's immutable bot user ID in trusted repository settings instead.
        user_id = os.environ.get("RELEASE_APP_BOT_USER_ID", "")
        author = item["user"]["login"]
        login = author if user_id.isdecimal() and str(item["user"].get("id")) == user_id else ""
        errors = pr_problems(item, repository, release_login=login)
        summary = (
            "\n".join(f"- {error}" for error in errors)
            or "Title and body follow the contribution rules."
        )
        advice = style_advice(item.get("body") or "")
        if advice:
            summary += "\n\nAdvisory style suggestions:\n" + "\n".join(f"- {x}" for x in advice)
        github(
            "PATCH",
            f"/repos/{repository}/check-runs/{run['id']}",
            {
                "status": "completed",
                "conclusion": "failure" if errors else "success",
                "completed_at": datetime.now(UTC).isoformat(),
                "output": {
                    "title": "PR content needs changes"
                    if errors
                    else "PR content follows the rules",
                    "summary": summary,
                },
            },
        )
        print(summary)
        return int(bool(errors))
    except Exception:
        github(
            "PATCH",
            f"/repos/{repository}/check-runs/{run['id']}",
            {
                "status": "completed",
                "conclusion": "failure",
                "output": {
                    "title": "Metadata validation unavailable",
                    "summary": "Validation failed to complete. Resolve the API error and rerun.",
                },
            },
        )
        raise


def check_issue(github, repository: str, number: int) -> int:
    item = github("GET", f"/repos/{repository}/issues/{number}")
    if "pull_request" in item:
        raise ValueError("Issue feedback cannot be used for pull requests")
    save_text(item)
    errors = issue_problems(item)
    comments, page = [], 1
    while True:
        batch = github(
            "GET", f"/repos/{repository}/issues/{number}/comments?per_page=100&page={page}"
        )
        comments.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    own = next(
        (
            x
            for x in comments
            if x["user"]["login"] == "github-actions[bot]" and x["body"].startswith(MARKER)
        ),
        None,
    )
    if errors:
        body = (
            MARKER
            + "\nPlease complete the issue information:\n\n"
            + "\n".join(f"- {x}" for x in errors)
        )
        body += (
            "\n\nThis is advisory; the issue remains open. See CONTRIBUTING.md and the issue forms."
        )
        if own and own["body"] != body:
            github("PATCH", f"/repos/{repository}/issues/comments/{own['id']}", {"body": body})
        elif not own:
            github("POST", f"/repos/{repository}/issues/{number}/comments", {"body": body})
    elif own:
        github("DELETE", f"/repos/{repository}/issues/comments/{own['id']}")
    print("\n".join(errors) or "Issue information is complete.")
    return 0  # Missing issue information is advisory; no close/lock action exists.


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    name = os.environ["GITHUB_EVENT_NAME"]
    if name == "workflow_dispatch":
        kind, number = event["inputs"]["kind"], int(event["inputs"]["number"])
    else:
        kind = "pr" if name == "pull_request_target" else "issue"
        number = event["pull_request" if kind == "pr" else "issue"]["number"]
    if number < 1 or kind not in {"pr", "issue"}:
        raise ValueError("Expected a positive contribution number and pr/issue kind")
    repository = os.environ["GITHUB_REPOSITORY"]
    return (
        check_pr(
            api, repository, number, event.get("pull_request", {}).get("head", {}).get("sha", "")
        )
        if kind == "pr"
        else check_issue(api, repository, number)
    )


def spelling() -> int:
    path = Path(os.environ["RUNNER_TEMP"], "nora-contribution.md")
    if not path.exists():
        return 0
    try:
        result = subprocess.run(  # noqa: S603 -- fixed tool, version and trusted temp filename
            [
                shutil.which("uvx") or "/usr/bin/uvx",
                "--from",
                "codespell==2.4.3",
                "codespell",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            print(
                "::warning::Spelling suggestions or tool errors are advisory; see the job summary."
            )
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write(
                "\n<details><summary>Advisory spelling output</summary><pre>"
                + html.escape(result.stdout + result.stderr)
                + "</pre></details>\n"
            )
    except OSError:
        print("::warning::Advisory spelling tool unavailable.")
    return 0


if __name__ == "__main__":
    sys.exit(spelling() if sys.argv[1:] == ["--spelling"] else main())
