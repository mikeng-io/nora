#!/usr/bin/env python3
"""Adjudicate the identifier hook's open findings with a read-only coding agent.

When the pre-commit or commit-msg hook blocks on findings that are neither a
denylist hit nor an ERROR, it leaves them -- masked, with their ids -- in
`$GIT_DIR/nora-pii-findings.json`. This script hands them to a headless
Claude Code agent (Sonnet by default) that may READ the repository and run
read-only git commands, and nothing else. For each finding it answers:

    real    the repository's evidence says the value came from real records
    fake    the evidence says it was invented, generated, or a placeholder
    unsure  the repository does not say

with a one-line reason and the evidence it looked at. The verdicts go to
`$GIT_DIR/nora-pii-adjudication.json`, bound to the staged tree. Re-running
the same `git commit` then turns every `fake` into an acknowledgement,
recorded as a `PII-Ack: <id> (agent <model>: fake, <reason>)` trailer; `real`
and `unsure` stay blocked. Change the staged tree and the verdicts no longer
apply.

It refuses to run while any denylist hit or ERROR is open: a known real value
is removed, not argued about. What the agent does see is the repository, as
any coding agent working here does; it is not given a denylisted value.

Measured (agent-judge spike, 2026-09-26, 24 synthetic cases x 3 runs):
Sonnet 92% correct, 0 real values cleared as fake; Haiku 83%, 2 leaks; local
models 38-67%. Median 20 s per case.

    python3 scripts/pii_adjudicate.py [--model sonnet] [--timeout 900]

Exit 0 when every finding came back fake, 1 when any is real or unsure,
2 when it could not adjudicate (nothing recorded, stale tree, denylist hit
open, the agent failed or answered unreadably).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True

FINDINGS_FILE = "nora-pii-findings.json"
ADJUDICATION_FILE = "nora-pii-adjudication.json"
GIT_READ = [
    "log",
    "show",
    "diff",
    "blame",
    "grep",
    "ls-files",
    "status",
    "rev-parse",
    "shortlog",
    "cat-file",
]

SYSTEM = (
    "You are the provenance check inside a git pre-commit hook.\n"
    "\n"
    "What you are reading: a repository at the point of a commit. A shape detector flagged "
    "values in the STAGED change that look like personal data (phone numbers, email "
    "addresses, chat-platform ids, GPS coordinates). Every flagged value looks realistic -- "
    "that is why it was flagged -- so how a value looks is not evidence either way. What "
    "decides is where the value came from, and the repository usually records that somewhere:"
    " in the file around the line, in a header or README describing the file, in the code or "
    "test that produces or uses it, in identity.example.toml (the placeholder template), and "
    "in the value's history (git log -S<value> --all; git log on the file).\n"
    "\n"
    "Decide each finding:\n"
    "- real: the repository's evidence says the value was copied from real people's records "
    "or from a real running system.\n"
    "- fake: the repository's evidence says the value was invented, generated, or declared a "
    "placeholder or example.\n"
    "- unsure: you looked and the repository does not say. unsure blocks the commit exactly "
    "as real does, so never answer fake without evidence for it.\n"
    "\n"
    "Look before you decide, with your read-only tools; cite what you looked at. Never write "
    "a flagged value into your answer: refer to it by its id. When you are done, answer with "
    "JSON only, no other text:\n"
    '{"verdicts": [{"id": "<finding id>", "at": "<path:line of that id>", "verdict": '
    '"real|fake|unsure", "reason": "<one line>", "evidence": ["<file or git command you '
    'used>"]}]}'
)


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True)  # noqa: S603, S607
    if p.returncode != 0:
        raise SystemExit(f"pii_adjudicate: git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def user_message(findings: list[dict], stat: str, message_file: str | None) -> str:
    lines = [
        f"- id={f['id']} rule={f['rule']} at {f['path']}:{f['line']} value={f['masked']}"
        for f in findings
    ]
    msg = ""
    if any(f["path"] == "<commit-message>" or f["path"] == "<commit>" for f in findings):
        msg = (
            f"\nFindings at <commit-message>:N are lines of the commit message being written, "
            f"in {message_file}.\n"
        )
    return (
        "Repository: the current working directory (tool paths are relative to its root).\n\n"
        f"Staged change:\n{stat}\n{msg}\n"
        "Findings (values masked; read the line to see it):\n" + "\n".join(lines) + "\n\n"
        "Decide every finding id listed."
    )


def parse_verdicts(text: str) -> dict:
    """{id: {verdict, reason, evidence, at}}; anything unreadable is {}."""
    if not text:
        return {}
    stripped = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for c in [text, *re.findall(r"\{.*\}", text, flags=re.S), stripped, stripped + "}"]:
        c = re.sub(r"^```(?:json)?|```$", "", c.strip(), flags=re.M).strip()
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        vs = obj.get("verdicts") if isinstance(obj, dict) else None
        if isinstance(vs, list):
            out = {}
            for v in vs:
                if isinstance(v, dict) and "id" in v:
                    verdict = str(v.get("verdict", "")).lower()
                    out[str(v["id"])] = {
                        "verdict": verdict if verdict in ("real", "fake", "unsure") else "unsure",
                        "reason": re.sub(r"\s+", " ", str(v.get("reason", "")))[:300],
                        "evidence": [str(e)[:200] for e in (v.get("evidence") or [])][:10],
                        "at": str(v.get("at", "")),
                    }
            return out
    return {}


def claude_argv(model: str, prompt: str) -> list[str]:
    """Headless Claude Code, read-only: --restricted drops code-running tools
    and ignores user/project settings; --safe-mode loads no hooks, plugins or
    CLAUDE.md; no MCP servers; Read/Grep/Glob plus Bash pre-approved ONLY for
    read-only git subcommands; every other permission is denied."""
    return [
        os.environ.get("NORA_PII_CLAUDE", "claude"),
        "-p",
        prompt,
        "--model",
        model,
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--tools",
        "Read,Grep,Glob,Bash",
        "--allowedTools",
        *[f"Bash(git {s}:*)" for s in GIT_READ],
        "Read",
        "Grep",
        "Glob",
        "--permission-prompts",
        "none",
        "--append-system-prompt",
        SYSTEM,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        os.environ.get("NORA_PII_ADJUDICATOR_TURNS", "25"),
        "--no-session-persistence",
    ]


def run_agent(model: str, prompt: str, cwd: str, timeout: float) -> tuple[str, dict]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv; the prompt is our own text
            claude_argv(model, prompt),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"pii_adjudicate: the agent did not finish: {exc}") from exc
    final, meta = "", {"tools": 0, "denied": 0}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            meta["tools"] += sum(
                1 for b in ev.get("message", {}).get("content", []) if b.get("type") == "tool_use"
            )
        if ev.get("type") == "result":
            final = ev.get("result") or ""
            meta["denied"] = len(ev.get("permission_denials", []) or [])
            meta["cost_usd"] = ev.get("total_cost_usd")
            if ev.get("is_error"):
                meta["error"] = str(ev.get("subtype"))
    if proc.returncode != 0 and not final:
        raise SystemExit(
            f"pii_adjudicate: the agent exited {proc.returncode}: {proc.stderr[-300:]}"
        )
    return final, meta


def _value_filter():
    """Replace any reason or evidence string that holds something the
    identifier scanner flags (a denylisted value or a phone/id/email shape)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pii_scan

    deny, _err, _ = pii_scan.load_denylist()
    sc = pii_scan.Scanner(deny or pii_scan.Denylist(), pii_scan.Allowlist(), set())

    def clean(text: str) -> str:
        return "[withheld: it quoted a value]" if sc.scan_text(text, "<agent>").findings else text

    return clean


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", default=os.environ.get("NORA_PII_ADJUDICATOR_MODEL", "sonnet"))
    ap.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args(argv)
    top = git("rev-parse", "--show-toplevel").strip()
    gitdir = os.path.abspath(os.path.join(top, git("rev-parse", "--git-dir").strip()))
    path = os.path.join(gitdir, FINDINGS_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        print("pii_adjudicate: no recorded findings; run `git commit` first", file=sys.stderr)
        return 2
    tree = subprocess.run(
        ["git", "write-tree"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=top,
    ).stdout.strip()
    if not tree or data.get("tree") != tree:
        print(
            "pii_adjudicate: the findings describe a different staged tree; re-run `git commit` "
            "to refresh them",
            file=sys.stderr,
        )
        return 2
    if data.get("staged_blocked_otherwise") or data.get("message_blocked_otherwise"):
        print(
            "pii_adjudicate: a denylist hit or an ERROR is open; those are not adjudicated. "
            "Fix them and re-run `git commit`.",
            file=sys.stderr,
        )
        return 2
    findings = {f["id"]: f for f in (data.get("staged") or []) + (data.get("message") or [])}
    if not findings:
        print("pii_adjudicate: nothing to adjudicate", file=sys.stderr)
        return 0
    stat = git("diff", "--cached", "--stat").strip()
    prompt = user_message(list(findings.values()), stat, os.path.join(gitdir, "COMMIT_EDITMSG"))
    t0 = time.monotonic()
    final, meta = run_agent(a.model, prompt, top, a.timeout)
    verdicts = parse_verdicts(final)
    if not verdicts:
        print(
            "pii_adjudicate: the agent's answer could not be read; nothing recorded",
            file=sys.stderr,
        )
        return 2
    kept = {fid: v for fid, v in verdicts.items() if fid in findings}
    # The agent was told never to quote a value; hold it to that before
    # anything it wrote is printed, stored or turned into a trailer.
    clean = _value_filter()
    for v in kept.values():
        v["reason"] = clean(v["reason"])
        v["evidence"] = [clean(e) for e in v["evidence"]]
    for fid in findings:
        kept.setdefault(
            fid,
            {
                "verdict": "unsure",
                "reason": "the agent gave no verdict for it",
                "evidence": [],
                "at": "",
            },
        )
    out = {
        "tree": tree,
        "model": a.model,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seconds": round(time.monotonic() - t0, 1),
        "agent": meta,
        "verdicts": kept,
    }
    with open(os.path.join(gitdir, ADJUDICATION_FILE), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    for fid, v in sorted(kept.items()):
        f = findings[fid]
        print(f"  {v['verdict']:<6} {fid}  {f['rule']:<16} {f['path']}:{f['line']}  {f['masked']}")
        print(f"         reason: {v['reason']}")
        if v["evidence"]:
            print(f"         looked at: {', '.join(v['evidence'][:5])}")
    fakes = sum(1 for v in kept.values() if v["verdict"] == "fake")
    print(
        f"pii_adjudicate: {fakes}/{len(kept)} fake ({a.model}, {out['seconds']}s). Re-run the same "
        "`git commit`: fake verdicts become PII-Ack trailers; real/unsure stay blocked."
    )
    return 0 if fakes == len(kept) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
