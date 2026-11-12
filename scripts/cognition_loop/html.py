"""The operator's one-file report: `report.html` beside `report.json`.

Reads only the run's frozen files — `report.json`, each
`wake-<i>-<recorded id>.json`, and `run_budget.json` when the budget
was armed — so `python -m scripts.cognition_loop.html <output dir>`
re-renders it with no database and no network. Every value is escaped;
a gap renders as "not recorded", never a blank.
"""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2em auto; max-width: 72em;
       color: #1a1a1a; line-height: 1.45; }
h1, h2, h3, h4 { margin: 1.2em 0 .3em; }
table { border-collapse: collapse; margin: .4em 0; }
td, th { border: 1px solid #ccc; padding: .25em .6em; text-align: left;
         vertical-align: top; font-size: .92em; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: .6em;
      overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
details { margin: .3em 0; }
.status-PASS { color: #1a7a1a; } .status-FAIL { color: #a01010; }
.status-ERROR { color: #a01010; font-weight: bold; }
.status-REVIEW { color: #a06000; }
.not-recorded { color: #777; font-style: italic; }
.wake { border-top: 2px solid #999; margin-top: 1.5em; padding-top: .5em; }
.case { border-top: 4px solid #333; margin-top: 2em; padding-top: .5em; }
"""


def esc(value: Any) -> str:
    return html.escape("not recorded" if value is None else str(value))


def _json(value: Any) -> str:
    return html.escape(json.dumps(value, indent=2, ensure_ascii=False,
                                  default=str))


def _bytes(value: Any) -> str:
    size = len(json.dumps(value, ensure_ascii=False,
                          default=str).encode())
    return f"{size:,} bytes"


def _when(ms: Any) -> str:
    if not isinstance(ms, (int, float)):
        return esc(ms)
    utc = datetime.fromtimestamp(ms / 1000, UTC)
    hkt = utc.astimezone(HKT)
    return (f"{utc.strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"({hkt.strftime('%Y-%m-%d %H:%M:%S')} Asia/Hong_Kong)")


def _block(name: str, value: Any) -> str:
    """One context block, collapsed with its size; absent is said."""
    if value is None:
        return f'<details><summary>{esc(name)} — ' \
               '<span class="not-recorded">not recorded</span></summary></details>'
    body = value if isinstance(value, str) else json.dumps(
        value, indent=2, ensure_ascii=False, default=str)
    return (f"<details><summary>{esc(name)} — {esc(_bytes(value))}"
            f"</summary><pre>{html.escape(body)}</pre></details>")


def _kv(rows: list[tuple[str, Any]]) -> str:
    return ("<table>" + "".join(
        f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)
        + "</table>")


def _run(report: dict[str, Any], budget: dict[str, Any] | None) -> str:
    run = report.get("run") or {}
    ev = report.get("evidence") or {}
    parts = [
        f'<h1>Cognition loop run — '
        f'<span class="status-{esc(report.get("status"))}">'
        f'{esc(report.get("status"))}</span></h1>',
        _kv([
            ("run id", run.get("run_id")),
            ("evidence run id", run.get("evidence_run_id")),
            ("commit", run.get("head")),
            ("diff sha256", run.get("diff_sha256")),
            ("lockfile sha256", run.get("lockfile_sha256")),
            ("mode", run.get("mode")),
            ("config sha256", run.get("config_sha256")),
            ("subject config sha256", run.get("subject_config_sha256")),
        ]),
        "<h3>Token budget</h3>",
        _kv([
            ("stated total", run.get("token_budget")),
            ("per wake", run.get("per_wake_tokens")),
            ("requests used / max", None if budget is None else
             f"{budget.get('requests_used')} / {budget.get('max_requests')}"),
            ("input tokens used / max", None if budget is None else
             f"{budget.get('input_tokens_used')} / "
             f"{budget.get('max_input_tokens')}"),
            ("output tokens used / max", None if budget is None else
             f"{budget.get('output_tokens_used')} / "
             f"{budget.get('max_output_tokens')}"),
            ("stop after total tokens", None if budget is None else
             budget.get("stop_after_total_tokens")),
            ("stop reason", None if budget is None else
             (budget.get("spent_reason") or "none")),
        ]) if budget is not None else
        '<p class="not-recorded">not recorded — no budget was armed</p>',
        "<h3>Evidence receipt</h3>",
        _kv([
            ("status", ev.get("status")),
            ("causes", ", ".join(ev.get("causes") or []) or None),
            ("events file", ev.get("ref")),
        ]),
    ]
    return "".join(parts)


def _arrived(wake: dict[str, Any]) -> str:
    advance = wake.get("advance")
    if advance is None:
        arrived = ('<p class="not-recorded">not recorded — the sandbox '
                   "restore supplies this wake's world</p>")
    elif not advance:
        arrived = "<p>no Record rows arrived in this window</p>"
    else:
        arrived = ("<table><tr><th>table</th><th>rows</th></tr>" + "".join(
            f"<tr><td>{esc(t)}</td><td>{esc(n)}</td></tr>"
            for t, n in sorted(advance.items())) + "</table>")
    return ("<h4>a. What arrived</h4>" + _kv([
        ("recorded reason", wake.get("reason")),
        ("recorded at", _when(wake.get("at_ms"))),
        ("forced", wake.get("forced")),
    ]) + arrived)


def _read(wake: dict[str, Any]) -> str:
    trajectory = wake.get("trajectory") or {}
    blocks = trajectory.get("blocks") or {}
    return ("<h4>b. What Nora read</h4>" + "".join(
        _block(name, blocks.get(name))
        for name in ("system", "instructions", "agenda", "items", "stimuli")))


def _did(wake: dict[str, Any]) -> str:
    trajectory = wake.get("trajectory") or {}
    out = ["<h4>c. What Nora did</h4>"]
    calls = trajectory.get("model_calls")
    out.append("<h5>model exchanges</h5>")
    if not calls:
        out.append('<p class="not-recorded">not recorded</p>')
    for i, call in enumerate(calls or []):
        out.append(
            f"<details><summary>model call {i + 1} — {esc(_bytes(call))}"
            f"</summary><pre>{_json(call)}</pre></details>")
    out.append("<h5>tool calls</h5>")
    tools = trajectory.get("tool_calls") or []
    exchanges = trajectory.get("exchanges") or []
    if not tools and not exchanges:
        out.append('<p class="not-recorded">not recorded</p>')
    for exchange in exchanges:
        refused = ("yes — " + str(exchange["argument_validation"])
                   if exchange.get("argument_validation") else "no")
        out.append(
            f"<details><summary>{esc(exchange.get('name'))} — "
            f"refused: {esc(refused)}</summary>"
            f"<h5>arguments</h5><pre>{_json(exchange.get('input'))}</pre>"
            f"<h5>result</h5><pre>{_json(exchange.get('result'))}</pre>"
            f"<h5>source rows</h5><pre>{_json(exchange.get('source_rows'))}"
            f"</pre></details>")
    for call in tools:
        refused = "yes" if call.get("refusal") else "no"
        out.append(
            f"<details><summary>{esc(call.get('tool'))} — "
            f"refused: {esc(refused)}</summary>"
            f"<h5>arguments</h5><pre>{_json(call.get('arguments'))}</pre>"
            f"<h5>result</h5><pre>{_json(call.get('result'))}</pre>"
            f"<h5>error type</h5><p>{esc(call.get('error_type'))}</p></details>")
    usage = trajectory.get("usage") or {}
    out.append(_kv([
        ("stop reason", trajectory.get("stop_reason")),
        ("tokens", usage.get("tokens")),
    ]))
    return "".join(out)


def _field_diff(before: Any, after: Any) -> str:
    """The changed fields of a revised entry, before → after."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return f"<pre>{_json({'before': before, 'after': after})}</pre>"
    rows = "".join(
        f"<tr><td>{esc(k)}</td><td><pre>{_json(before.get(k))}</pre></td>"
        f"<td><pre>{_json(after.get(k))}</pre></td></tr>"
        for k in sorted(set(before) | set(after))
        if before.get(k) != after.get(k))
    return ("<table><tr><th>field</th><th>before</th><th>after</th></tr>"
            f"{rows}</table>")


def _changed(diff: dict[str, Any] | None) -> str:
    out = ["<h4>d. What changed</h4>"]
    if diff is None:
        out.append('<p class="not-recorded">not recorded</p>')
        return "".join(out)
    for kind in sorted(diff):
        d = diff[kind] or {}
        out.append(f"<h5>{esc(kind)}</h5>")
        created = d.get("created") or []
        revised = d.get("revised") or []
        retired = d.get("retired") or []
        unchanged = d.get("unchanged") or []
        if created:
            out.append("<p>created:</p>")
            for e in created:
                out.append(
                    f"<details><summary>{esc(e.get('entry_id'))}</summary>"
                    f"<pre>{_json(e.get('after'))}</pre></details>")
        if revised:
            out.append("<p>revised:</p>")
            for e in revised:
                out.append(
                    f"<details><summary>{esc(e.get('entry_id'))}</summary>"
                    f"{_field_diff(e.get('before'), e.get('after'))}</details>")
        if retired:
            out.append("<p>retired:</p>")
            for e in retired:
                out.append(
                    f"<details><summary>{esc(e.get('entry_id'))}</summary>"
                    f"<pre>{_json(e.get('before'))}</pre></details>")
        out.append(f'<p class="not-recorded">unchanged: '
                   f"{esc(len(unchanged))}</p>")
        if not (created or revised or retired or unchanged):
            out.append('<p class="not-recorded">not recorded</p>')
    return "".join(out)


def _judgement(expectations: list[dict[str, Any]] | None) -> str:
    out = ["<h4>e. Judgement</h4>"]
    if not expectations:
        out.append('<p class="not-recorded">not recorded</p>')
        return "".join(out)
    for exp in expectations:
        expectation = exp.get("expectation")
        if isinstance(expectation, dict):
            label = (f"{expectation.get('polarity')} — "
                     f"{expectation.get('kind')} — {expectation.get('text')}")
        else:
            label = expectation
        out.append(
            f"<details open><summary><b>{esc(label)}</b> — verdict: "
            f'<span class="status-{esc(exp.get("verdict"))}">'
            f'{esc(exp.get("verdict"))}</span></summary>')
        rows = "".join(
            "<tr>"
            f"<td>{esc(a.get('model') or None)}</td>"
            f"<td>{esc(a.get('verdict'))}</td>"
            f"<td>{esc(a.get('reason') or None)}</td>"
            f"<td>{esc(', '.join(a.get('entry_ids') or []) or None)}</td>"
            f"<td>{esc(a.get('served') or None)}</td>"
            "</tr>"
            for a in (exp.get("answers") or []))
        out.append(
            "<table><tr><th>judge</th><th>answer</th><th>reason</th>"
            f"<th>entry ids</th><th>served model</th></tr>{rows}</table>"
            "</details>")
    return "".join(out)


def _wake(case_index: int, index: int, wake: dict[str, Any],
          frozen: dict[str, Any]) -> str:
    anchor = f"case-{case_index}-wake-{index}"
    status = wake.get("status") or "PASS"
    error = frozen.get("error") or {}
    parts = [
        f'<section class="wake" id="{anchor}">'
        f"<h3>wake {index} (recorded {esc(wake.get('recorded_wake_id'))}) — "
        f'<span class="status-{esc(status)}">{esc(status)}</span></h3>',
    ]
    if error or wake.get("error"):
        parts.append(_kv([
            ("failed step", error.get("step") or wake.get("step")),
            ("error", error.get("message") or wake.get("error")),
        ]))
    parts.append(_arrived({**wake, "advance": wake.get("advance")}))
    parts.append(_read({**wake, "trajectory":
                            wake.get("trajectory") or frozen.get("trajectory")}))
    parts.append(_did({**wake, "trajectory":
                           wake.get("trajectory") or frozen.get("trajectory")}))
    parts.append(_changed(frozen.get("diff")))
    parts.append(_judgement(
        wake.get("expectations") or frozen.get("expectations")))
    parts.append("</section>")
    return "".join(parts)


def _toc(report: dict[str, Any]) -> str:
    items = []
    for ci, case in enumerate(report.get("cases") or []):
        inner = "".join(
            f'<li><a href="#case-{ci}-wake-{wi}">wake {wi}</a> — '
            f'<span class="status-{esc(w.get("status") or "PASS")}">'
            f'{esc(w.get("status") or "PASS")}</span></li>'
            for wi, w in enumerate(case.get("wakes") or []))
        items.append(
            f'<li><a href="#case-{ci}">{esc(case.get("case"))}</a> — '
            f'<span class="status-{esc(case.get("status"))}">'
            f'{esc(case.get("status"))}</span><ul>{inner}</ul></li>')
    return f"<nav><h2>contents</h2><ul>{''.join(items)}</ul></nav>"


def render(output: Path) -> Path:
    """`report.html` beside `report.json`, 0600; re-derivable any time."""
    report_path = output / "report.json"
    report: dict[str, Any] = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file() else
        {"status": "ERROR", "run": {}, "cases": [],
         "evidence": {"status": "ERROR", "causes": ["report.json missing"]}})
    budget_path = output / "run_budget.json"
    budget = (json.loads(budget_path.read_text(encoding="utf-8"))
              if budget_path.is_file() else None)
    parts = [
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>cognition loop report</title>"
        f"<style>{_CSS}</style></head><body>",
        _run(report, budget),
        _toc(report),
    ]
    for ci, case in enumerate(report.get("cases") or []):
        parts.append(
            f'<section class="case" id="case-{ci}">'
            f"<h2>{esc(case.get('case'))} — unit {esc(case.get('unit'))} — "
            f'<span class="status-{esc(case.get("status"))}">'
            f'{esc(case.get("status"))}</span></h2>')
        for error in case.get("errors") or []:
            parts.append(f"<p><b>error:</b> {esc(error)}</p>")
        for wi, wake in enumerate(case.get("wakes") or []):
            frozen_path = output / (
                f"wake-{wi}-{wake.get('recorded_wake_id')}.json")
            frozen = (json.loads(frozen_path.read_text(encoding="utf-8"))
                      if frozen_path.is_file() else {})
            parts.append(_wake(ci, wi, wake, frozen))
        parts.append("</section>")
    parts.append("</body></html>")
    target = output / "report.html"
    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return target


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m scripts.cognition_loop.html <output dir>",
              file=sys.stderr)
        return 2
    target = render(Path(args[0]))
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
