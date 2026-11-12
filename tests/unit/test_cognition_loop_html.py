"""`scripts.cognition_loop.html` — the one-file operator report, rendered
purely from a run's frozen files.
"""

from __future__ import annotations

import json
import os
from typing import Any

from scripts.cognition_loop.html import render


def _run_dir(tmp_path, *, cases: list[dict[str, Any]],
             wakes: dict[str, dict[str, Any]] | None = None,
             status: str = "PASS") -> Any:
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_text(json.dumps({
        "status": status,
        "run": {
            "run_id": "cl-test", "evidence_run_id": "cognition-loop-x",
            "commit": "abc123", "mode": "inspect-only",
            "token_budget": 400_000, "per_wake_tokens": 200_000,
            "config_sha256": "cf", "subject_config_sha256": "sf",
        },
        "evidence": {"status": "PASS", "causes": [], "ref": "ev"},
        "cases": cases,
    }, ensure_ascii=False))
    for name, payload in (wakes or {}).items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False))
    (out / "run_budget.json").write_text(json.dumps({
        "max_requests": 0, "requests_used": 0,
        "input_tokens_used": 0, "output_tokens_used": 0,
        "max_output_tokens": 600_000, "spent_reason": None,
    }))
    return out


def _wake_report(**kw) -> dict[str, Any]:
    return {
        "recorded_wake_id": 7, "wake_id": 42, "at_ms": 1_800_000_000_000,
        "reason": "time", "forced": False, "status": "PASS",
        "advance": {"messages": 3, "row_history": 4},
        "trajectory": {
            "blocks": {"system": "SYSTEM", "agenda": "AGENDA",
                       "items": [], "stimuli": [], "instructions": {}},
            "model_calls": [], "exchanges": [], "tool_calls": [],
            "stop_reason": "session_end", "usage": {"tokens": 5},
        },
        "expectations": [],
        **kw,
    }


def test_the_sections_appear_in_order(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "generated case", "unit": "u", "status": "PASS",
        "errors": [], "wakes": [_wake_report()],
    }], wakes={"wake-0-7.json": {
        "trajectory": {"blocks": {"system": "SYSTEM"}},
        "advance": {"messages": 3},
        "diff": {"note": {"created": [], "revised": [], "retired": [],
                          "unchanged": [{"entry_id": "u1"}]}},
        "expectations": [],
    }})
    html = render(out).read_text(encoding="utf-8")
    heads = [html.index(s) for s in (
        "a. What arrived", "b. What Nora read", "c. What Nora did",
        "d. What changed", "e. Judgement")]
    assert heads == sorted(heads)
    # The table of contents links the case and its wake, each with status.
    assert 'href="#case-0"' in html and 'href="#case-0-wake-0"' in html
    assert "SYSTEM" in html and "messages" in html


def test_unchanged_entries_are_counted_never_listed(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report()],
    }], wakes={"wake-0-7.json": {
        "diff": {"note": {
            "created": [{"entry_id": "n9", "after": {"title": "new"}}],
            "revised": [],
            "retired": [],
            "unchanged": [{"entry_id": "hidden-id-1"},
                          {"entry_id": "hidden-id-2"}],
        }},
    }})
    html = render(out).read_text(encoding="utf-8")
    assert "unchanged: 2" in html
    assert "hidden-id-1" not in html and "hidden-id-2" not in html
    assert "n9" in html


def test_every_value_is_escaped(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "<script>alert(1)</script>", "unit": "u",
        "status": "PASS", "errors": [],
        "wakes": [_wake_report(reason="<script>")],
    }], wakes={"wake-0-7.json": {
        "trajectory": {"blocks": {"system": "<script>x</script>"}},
    }})
    html = render(out).read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # Self-contained: one file, 0600, no external fetches.
    assert "http://" not in html.replace("<!DOCTYPE", "")
    assert os.stat(out / "report.html").st_mode & 0o777 == 0o600


def test_an_error_run_still_renders_with_its_named_step(tmp_path):
    out = _run_dir(tmp_path, status="ERROR", cases=[{
        "case": "c", "unit": "u", "status": "ERROR",
        "errors": ["advance before wake 1 (recorded 7) failed: boom"],
        "wakes": [_wake_report(
            status="ERROR", step="advance",
            error='UniqueViolationError("uq_row_history_open")')],
    }], wakes={"wake-0-7.json": {
        "error": {"step": "advance",
                  "message": 'UniqueViolationError("uq_row_history_open")'},
    }})
    html = render(out).read_text(encoding="utf-8")
    assert "ERROR" in html
    assert "advance" in html and "uq_row_history_open" in html
    assert "advance before wake 1" in html


def test_missing_data_says_not_recorded(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report(trajectory=None)],
    }])
    html = render(out).read_text(encoding="utf-8")
    assert "not recorded" in html


def test_the_report_owns_a_light_colour_scheme(tmp_path):
    out = _run_dir(tmp_path, cases=[])
    html = render(out).read_text(encoding="utf-8")
    assert "color-scheme: light" in html
    assert "background: #ffffff" in html


def test_a_wake_without_a_session_says_so_and_compares_batches(tmp_path):
    batch = {"recorded": [3, 5, 7], "offered": [],
             "production_session": True}
    out = _run_dir(tmp_path, status="ERROR", cases=[{
        "case": "c", "unit": "u", "status": "ERROR", "errors": [],
        "wakes": [_wake_report(
            status="ERROR", step="wake",
            error="the sandbox ran no session: empty agenda "
                  "(production ran one)",
            no_session="empty agenda", batch=batch)],
    }], wakes={"wake-0-7.json": {
        "error": {"step": "wake",
                  "message": "the sandbox ran no session: empty agenda "
                             "(production ran one)"},
        "no_session": "empty agenda",
        "batch": batch,
    }})
    html = render(out).read_text(encoding="utf-8")
    assert "No session" in html and "empty agenda" in html
    assert "production ran one on 3 stimuli" in html
    assert "recorded batch (production)" in html and "[3, 5, 7]" in html
    assert "offered batch (sandbox)" in html
    assert "differs from production" in html


def test_matching_batches_say_so(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report(batch={
            "recorded": [3, 5], "offered": [5, 3],
            "production_session": True})],
    }], wakes={"wake-0-7.json": {}})
    html = render(out).read_text(encoding="utf-8")
    assert "matches production" in html


def test_self_stimuli_are_compared_beside_the_non_self_batch(tmp_path):
    """The batch verdict is over the non-self ids; a self-wake the
    candidate stood in for production's is its own line."""
    batch = {"recorded": [3, 5, 7], "offered": [3, 5, 42],
             "recorded_self": [7], "offered_self": [42],
             "production_session": True}
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report(batch=batch)],
    }], wakes={"wake-0-7.json": {"batch": batch}})
    html = render(out).read_text(encoding="utf-8")
    assert "matches production" in html
    assert "self stimuli" in html
    assert "[42]" in html and "[7]" in html


def test_a_self_wake_the_candidate_never_scheduled_is_named(tmp_path):
    batch = {"recorded": [9], "offered": [],
             "recorded_self": [9], "offered_self": [],
             "production_session": True}
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report(batch=batch)],
    }], wakes={"wake-0-7.json": {"batch": batch}})
    html = render(out).read_text(encoding="utf-8")
    assert "matches production" in html
    assert "self-wake was not scheduled by the candidate" in html


def test_an_empty_store_reads_none_never_not_recorded(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report()],
    }], wakes={"wake-0-7.json": {
        "diff": {"note": {"created": [], "revised": [], "retired": [],
                          "unchanged": []}},
    }})
    html = render(out).read_text(encoding="utf-8")
    assert "<p>none</p>" in html
    assert "unchanged: 0" not in html


def test_the_summary_line_counts_the_session(tmp_path):
    out = _run_dir(tmp_path, cases=[{
        "case": "c", "unit": "u", "status": "PASS", "errors": [],
        "wakes": [_wake_report(
            model_calls=True,
            batch={"recorded": [1, 2], "offered": [1, 2],
                   "production_session": True},
            trajectory={
                "blocks": {"items": [{"i": 1}, {"i": 2}, {"i": 3}],
                           "stimuli": [{}] * 19},
                "tool_calls": []})],
    }], wakes={"wake-0-7.json": {
        "diff": {"note": {"created": [{"entry_id": "n1", "after": {}}],
                          "revised": [], "retired": [], "unchanged": []}},
    }})
    html = render(out).read_text(encoding="utf-8")
    assert ("Session ran: 3 agenda items from 2 stimuli; "
            "1 created, 0 revised, 0 retired; 0 tool calls") in html


def test_a_frozen_wake_absent_from_report_json_still_renders(tmp_path):
    """A run that died mid-wake froze `wake-0-7.json` but never listed it:
    the HTML still renders its trajectory under its own heading."""
    out = _run_dir(tmp_path, status="ERROR", cases=[], wakes={
        "wake-0-7.json": {
            "trajectory": {
                "blocks": {"system": "ORPHANED-SYSTEM"},
                "tool_calls": [{"tool": "note_create", "arguments": {"x": 1},
                                "result": {"status": "success"},
                                "refusal": None, "error_type": None}],
            },
            "diff": {"note": {"created": [
                {"entry_id": "orphan-note", "after": {"title": "t"}}],
                "revised": [], "retired": [], "unchanged": []}},
        },
    })
    html = render(out).read_text(encoding="utf-8")
    assert "frozen wakes absent from report.json" in html
    assert "ORPHANED-SYSTEM" in html
    assert "orphan-note" in html
    assert "note_create" in html
    assert "wake 0" in html
