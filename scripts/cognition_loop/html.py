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

from scripts.cognition_loop.trajectory import tool_outcome

HKT = ZoneInfo("Asia/Hong_Kong")

_CSS = """
html { color-scheme: light; }
html, body { background: #ffffff; color: #1a1a1a; }
body { font-family: system-ui, sans-serif; margin: 2em auto; max-width: 72em;
       line-height: 1.45; }
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
.status-no_session { color: #7a6a2f; font-weight: bold; }
.summary { color: #333; }
.not-recorded { color: #777; font-style: italic; }
.tool-ok { color: #1a7a1a; } .tool-refused, .tool-unavailable { color: #a01010; }
.tool-nothing_found { color: #a06000; }
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
    errors = report.get("errors") or []
    if errors:
        parts.append("<h3>Run errors</h3>" + "".join(
            "<p><b>"
            f"{esc(e.get('step') if isinstance(e, dict) else 'run')}:"
            f"</b> "
            f"{esc(e.get('message') if isinstance(e, dict) else e)}</p>"
            for e in errors))
    return "".join(parts)


def _ids(ids: Any) -> str:
    if ids is None:
        return "not recorded"
    if not ids:
        return "none"
    return "[" + ", ".join(str(i) for i in ids) + "]"


def _self_difference(recorded: list[int], offered: list[int]) -> str:
    """The self-wake difference: the candidate's own schedule vs production's."""
    if recorded and not offered:
        return "production's self-wake was not scheduled by the candidate"
    if offered and not recorded:
        return f"the candidate scheduled its own self-wake {_ids(offered)}"
    return (f"the candidate's self-wake {_ids(offered)} stands in for "
            f"production's {_ids(recorded)}")


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
    batch = wake.get("batch") or {}
    batching: list[tuple[str, Any]] = []
    if batch:
        batching = [
            ("recorded batch (production)", _ids(batch.get("recorded"))),
            ("offered batch (sandbox)", _ids(batch.get("offered"))),
            ("production session", (
                "ran one" if batch.get("production_session") is True else
                "ran none" if batch.get("production_session") is False else
                "not recorded")),
        ]
        recorded = batch.get("recorded")
        offered = batch.get("offered")
        recorded_self = batch.get("recorded_self") or []
        offered_self = batch.get("offered_self") or []
        if recorded is not None and offered is not None:
            batching.append(("batching", (
                "matches production"
                if sorted(i for i in recorded if i not in set(recorded_self))
                == sorted(i for i in offered if i not in set(offered_self))
                else "differs from production")))
            if sorted(recorded_self) != sorted(offered_self):
                batching.append((
                    "self stimuli",
                    _self_difference(recorded_self, offered_self)))
    return ("<h4>a. What arrived</h4>" + _kv([
        ("recorded reason", wake.get("reason")),
        ("recorded at", _when(wake.get("at_ms"))),
        ("forced", wake.get("forced")),
        *batching,
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
    tools = [_with_outcome(c) for c in trajectory.get("tool_calls") or []]
    if not tools:
        out.append('<p class="not-recorded">not recorded</p>')
    else:
        rows = "".join(
            f"<tr><td>{i + 1}</td><td>{esc(c.get('tool'))}</td>"
            f"<td class=\"tool-{esc(c.get('status') or 'unknown')}\">"
            f"{esc(c.get('status') or 'not recorded')}</td>"
            f"<td>{esc(_detail(c))}</td></tr>"
            for i, c in enumerate(tools))
        out.append("<table><tr><th>#</th><th>tool</th><th>outcome</th>"
                   f"<th>reason</th></tr>{rows}</table>")
    for i, call in enumerate(tools):
        out.append(
            f"<details><summary>{i + 1}. {esc(call.get('tool'))} — "
            f"{esc(call.get('status') or 'not recorded')}</summary>"
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
        if not (created or revised or retired or unchanged):
            # An empty store was captured and held nothing: "none".
            out.append("<p>none</p>")
        else:
            out.append(f'<p class="not-recorded">unchanged: '
                       f"{esc(len(unchanged))}</p>")
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


def _summary(wake: dict[str, Any], frozen: dict[str, Any]) -> str:
    """The plain line above the wake's sections: what it did, and the production comparison."""
    no_session = wake.get("no_session") or frozen.get("no_session")
    batch = wake.get("batch") or frozen.get("batch") or {}
    error = frozen.get("error") or (
        {"step": wake.get("step"), "message": wake.get("error")}
        if wake.get("error") else {})
    suffix = ""
    recorded = batch.get("recorded")
    if batch.get("production_session") is True:
        suffix = (f" (production ran one on {len(recorded)} stimuli)"
                  if recorded else " (production ran one)")
    elif batch.get("production_session") is False:
        suffix = " (production ran none)"
    if no_session:
        return (f'<p class="summary">'
                f'<span class="status-no_session">No session</span>: '
                f"{esc(no_session)}{esc(suffix)}</p>")
    if error:
        return (f'<p class="summary">Failed at '
                f"{esc(error.get('step') or 'wake')} — "
                f"{esc(error.get('message'))}</p>")
    trajectory = wake.get("trajectory") or frozen.get("trajectory") or {}
    blocks = trajectory.get("blocks") or {}
    diff = frozen.get("diff") or {}
    items = blocks.get("items")
    offered = batch.get("offered")
    seen = blocks.get("stimuli")
    stimuli = (len(offered) if isinstance(offered, list)
               else len(seen) if isinstance(seen, list)
               else "unknown")
    verb = "Session ran" if wake.get("model_calls") else "Session assembled"
    return (
        f'<p class="summary">{esc(verb)}: '
        f"{len(items) if isinstance(items, list) else 'unknown'} "
        f"agenda items from {stimuli} stimuli; "
        f"{sum(len((d or {}).get('created') or []) for d in diff.values())} "
        f"created, "
        f"{sum(len((d or {}).get('revised') or []) for d in diff.values())} "
        f"revised, "
        f"{sum(len((d or {}).get('retired') or []) for d in diff.values())} "
        f"retired; "
        f"{_tool_tally([_with_outcome(c) for c in trajectory.get('tool_calls') or []])}"
        f"{esc(suffix)}</p>")


def _with_outcome(call: dict[str, Any]) -> dict[str, Any]:
    if call.get("status"):
        return call
    outcome = tool_outcome(call.get("result"))
    return {**call, "status": outcome.get("status"), "reason": outcome.get("reason")}


def _detail(call: dict[str, Any]) -> str:
    """The tool's own reason, short: what the reader needs in the table."""
    if call.get("status") in (None, "ok"):
        return ""
    return str(call.get("reason") or call.get("error_type") or "")[:80]


def _tool_tally(tools: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for call in tools:
        key = call.get("status") or "not recorded"
        counts[key] = counts.get(key, 0) + 1
    tally = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    return f"{len(tools)} tool calls" + (f" ({tally})" if tally else "")


def _wake(case_index: int, index: int, wake: dict[str, Any],
          frozen: dict[str, Any]) -> str:
    anchor = f"case-{case_index}-wake-{index}"
    status = wake.get("status") or "ERROR"
    error = frozen.get("error") or {}
    parts = [
        f'<section class="wake" id="{anchor}">'
        f"<h3>wake {index} (recorded {esc(wake.get('recorded_wake_id'))}) — "
        f'<span class="status-{esc(status)}">{esc(status)}</span></h3>',
        _summary(wake, frozen),
    ]
    if error or wake.get("error"):
        parts.append(_kv([
            ("failed step", error.get("step") or wake.get("step")),
            ("error", error.get("message") or wake.get("error")),
        ]))
    parts.append(_arrived({**wake, "advance": wake.get("advance"),
                           "batch": wake.get("batch")
                                    or frozen.get("batch")}))
    parts.append(_read({**wake, "trajectory":
                            wake.get("trajectory") or frozen.get("trajectory")}))
    parts.append(_did({**wake, "trajectory":
                           wake.get("trajectory") or frozen.get("trajectory")}))
    parts.append(_changed(frozen.get("diff")))
    parts.append(_judgement(
        wake.get("expectations") or frozen.get("expectations")))
    parts.append("</section>")
    return "".join(parts)


def _toc(report: dict[str, Any], orphans: list[Path]) -> str:
    items = []
    for ci, case in enumerate(report.get("cases") or []):
        inner = "".join(
            f'<li><a href="#case-{ci}-wake-{wi}">wake {wi}</a> — '
            f'<span class="status-{esc(w.get("status") or "ERROR")}">'
            f'{esc(w.get("status") or "ERROR")}</span></li>'
            for wi, w in enumerate(case.get("wakes") or []))
        items.append(
            f'<li><a href="#case-{ci}">{esc(case.get("case"))}</a> — '
            f'<span class="status-{esc(case.get("status"))}">'
            f'{esc(case.get("status"))}</span><ul>{inner}</ul></li>')
    if orphans:
        inner = "".join(
            f'<li><a href="#unlisted-{i}">{esc(p.name)}</a></li>'
            for i, p in enumerate(orphans))
        items.append(
            f'<li>frozen wakes absent from report.json<ul>{inner}</ul></li>')
    return f"<nav><h2>contents</h2><ul>{''.join(items)}</ul></nav>"


def _availability(output: Path) -> str:
    """The first frozen wake's availability, at the top: a closed read
    is seen before any verdict."""
    for path in sorted(output.glob("wake-*.json")):
        frozen = json.loads(path.read_text(encoding="utf-8"))
        avail = (frozen.get("trajectory") or {}).get("availability")
        if avail:
            break
    else:
        return ('<section><h2>availability</h2>'
                '<p class="not-recorded">not recorded</p></section>')
    rows = "".join(
        f"<tr><td>{esc(k)}</td><td class=\"tool-{'ok' if v else 'unavailable'}\">"
        f"{esc('on' if v else ('off' if v is False else 'not set'))}</td></tr>"
        for k, v in (avail.get("switches") or {}).items())
    offered = avail.get("offered")
    offered_html = (f"<p>{len(offered)} tools offered: {esc(', '.join(offered))}</p>"
                    if offered else '<p class="not-recorded">tools offered: '
                    'not recorded (no model call)</p>')
    closed = avail.get("unavailable") or []
    closed_html = ("<p>calls that answered unavailable: " + esc(", ".join(
        f"{c.get('tool')} ({c.get('reason') or 'no reason'})" for c in closed))
        + "</p>") if closed else "<p>no call answered unavailable</p>"
    return ("<section><h2>availability</h2><table><tr><th>switch</th>"
            f"<th>value</th></tr>{rows}</table>{offered_html}{closed_html}</section>")


def render(output: Path) -> Path:
    """`report.html` beside `report.json`, 0600; re-derivable any time.

    Every `wake-*.json` the run froze renders — a wake the report does
    not list (a run that died mid-wake after freezing) still shows its
    trajectory under "frozen wakes absent from report.json", never an
    empty page."""
    report_path = output / "report.json"
    report: dict[str, Any] = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file() else
        {"status": "ERROR", "run": {}, "cases": [],
         "evidence": {"status": "ERROR", "causes": ["report.json missing"]}})
    budget_path = output / "run_budget.json"
    budget = (json.loads(budget_path.read_text(encoding="utf-8"))
              if budget_path.is_file() else None)
    expected = {
        f"wake-{wi}-{wake.get('recorded_wake_id')}.json"
        for case in report.get("cases") or []
        for wi, wake in enumerate(case.get("wakes") or [])
    }
    orphans = sorted(
        p for p in output.glob("wake-*.json") if p.name not in expected)
    parts = [
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>cognition loop report</title>"
        f"<style>{_CSS}</style></head><body>",
        _run(report, budget),
        _availability(output),
        _toc(report, orphans),
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
    if orphans:
        parts.append('<section class="case">'
                     "<h2>frozen wakes absent from report.json</h2>")
        for i, path in enumerate(orphans):
            try:
                frozen = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                frozen = {}
            # `wake-<index>-<recorded id>.json`; an unparseable name still
            # renders, index -1.
            stem = path.name[:-len(".json")].split("-", 2)
            index = int(stem[1]) if len(stem) > 1 and stem[1].isdigit() else -1
            recorded = stem[2] if len(stem) > 2 else ""
            recorded_id = int(recorded) if recorded.isdigit() else recorded or None
            parts.append(
                f'<div id="unlisted-{i}">'
                + _wake(-1, index, {"recorded_wake_id": recorded_id}, frozen)
                + "</div>")
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
