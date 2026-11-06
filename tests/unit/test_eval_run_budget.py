"""The declared eval seam: `--subject-overrides` and `--run-budget`.

Two contracts, no network, no DB. (1) The budget gate on
`chat.completions.create` reserves BEFORE a request leaves — a refused
call raises `RunBudgetError` and the transport never fires — and
reconciles the measured usage after. (2) `--subject-overrides FILE` is a
declared seam: its content and sha256 land in `run_started` before any
case composes, and an unreadable/invalid file refuses the run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nora.domain.run_budget import (
    RunBudget,
    RunBudgetError,
    active_run_budget,
    gated_create,
)


def _usage(prompt=10, completion=5):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


async def test_gated_create_reserves_before_dispatch_and_reconciles():
    """Two admitted calls, the third refused WITHOUT the call firing —
    reserve happens before the request leaves, reconcile lands usage."""
    budget = RunBudget(max_requests=2, max_input_tokens=10_000,
                       max_output_tokens=10_000, output_reserve=100)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=_usage(prompt=50, completion=20))

    token = active_run_budget.set(budget)
    try:
        await gated_create(budget, create, max_tokens=30)
        await gated_create(budget, create)
        with pytest.raises(RunBudgetError):
            await gated_create(budget, create)
    finally:
        active_run_budget.reset(token)
    assert len(calls) == 2          # the refused third call never fired
    assert budget.requests == 2
    assert budget.input_tokens == 100
    assert budget.output_tokens == 40


async def test_input_crossing_is_terminal_not_post_spend():
    """A request that reconciles over the input cap marks the budget
    spent; the NEXT reserve refuses — and the terminal check makes the
    run ERROR, never a successful final call."""
    budget = RunBudget(max_requests=10, max_input_tokens=60,
                       max_output_tokens=10_000, output_reserve=10)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=_usage(prompt=70, completion=1))

    with pytest.raises(RunBudgetError, match="input tokens"):
        await gated_create(budget, create)      # reconcile itself fails
    with pytest.raises(RunBudgetError):
        await gated_create(budget, create)
    with pytest.raises(RunBudgetError):
        budget.finished()
    assert len(calls) == 1


async def test_input_size_reserve_is_a_pre_dispatch_bound():
    """The serialized request's byte size is the pre-dispatch input bound:
    a request larger than the cap is refused before the call fires."""
    budget = RunBudget(max_requests=10, max_input_tokens=50,
                       max_output_tokens=10_000, output_reserve=10)
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=_usage())
    big = "x" * 200
    with pytest.raises(RunBudgetError, match="input size"):
        await gated_create(budget, create, messages=[{"content": big}])
    assert calls == []


async def test_a_stream_reconciles_from_its_terminal_usage():
    """Streamed calls hold the reservation until the stream's usage chunk
    lands; a stream ending without usage is spent, never a zero."""
    budget = RunBudget(max_requests=5, max_input_tokens=10_000,
                       max_output_tokens=10_000, output_reserve=100)

    async def create(**kwargs):
        async def chunks():
            for i in range(2):
                yield SimpleNamespace(usage=None, delta={"x": i})
            yield SimpleNamespace(usage=_usage(prompt=40, completion=10))
        return chunks()

    stream = await gated_create(budget, create)
    async for _ in stream:
        pass
    assert budget.input_tokens == 40 and budget.output_tokens == 10

    async def no_usage(**kwargs):
        async def chunks():
            yield SimpleNamespace(usage=None)
        return chunks()

    stream = await gated_create(budget, no_usage)
    with pytest.raises(RunBudgetError, match="without usage"):
        async for _ in stream:
            pass
    with pytest.raises(RunBudgetError):
        budget.finished()


async def test_deadline_refuses_dispatch():
    budget = RunBudget(max_requests=10, max_input_tokens=1, max_output_tokens=1,
                       deadline=0.0, monotonic=lambda: 10.0)
    async def create(**kwargs):
        raise AssertionError("must never fire")
    with pytest.raises(RunBudgetError, match="active-time"):
        await gated_create(budget, create)


async def test_create_client_wraps_only_when_a_budget_is_installed():
    """`create_client` gates `chat.completions.create` exactly when the
    contextvar is installed — production clients stay untouched."""
    from nora.agent.factory import create_client

    ungated = create_client(base_url="http://x", api_key="k")
    plain_create = ungated.chat.completions.create
    budget = RunBudget(max_requests=1, max_input_tokens=1, max_output_tokens=1)
    token = active_run_budget.set(budget)
    try:
        gated = create_client(base_url="http://x", api_key="k")
        assert gated.chat.completions.create is not plain_create
    finally:
        active_run_budget.reset(token)
    again = create_client(base_url="http://x", api_key="k")
    assert again.chat.completions.create is not gated.chat.completions.create


# ---- the declared subject-override seam ------------------------------

def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path, runs: list[str]) -> None:
    import evals.__main__ as m
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    monkeypatch.setattr(m, "_sweep_stale_private_blanks", lambda url: None)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: None)
    written = tmp_path / "config.evaluation.toml"
    written.write_text("", encoding="utf-8")
    monkeypatch.setattr(m, "materialise", lambda **k: str(written))
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)
    monkeypatch.setattr(m, "graph_surface_lines", lambda *a: [])
    monkeypatch.setattr(m, "embedder_model_of", lambda text: "m")
    monkeypatch.setattr(
        m, "_run_one",
        lambda *a, **k: runs.append(
            json.dumps(k.get("subject_overrides") or {})) or True)

    async def _close() -> None:
        return None

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    monkeypatch.setattr(m, "assert_shared_databases_unchanged", lambda url, before: None)

    class _Evidence:
        def __init__(self, **kwargs):
            self.metadata = kwargs["metadata"]
            _Evidence.last = self
            self.recorded = []
            self.path = tmp_path / "evidence"
            self.exit_code = None

        def record(self, kind, **payload):
            self.recorded.append((kind, payload))

        def check(self):
            pass

        def capture(self, provider):
            import contextlib
            return contextlib.nullcontext()

    monkeypatch.setattr(m, "RunEvidence", _Evidence)


def test_subject_overrides_reaches_every_case_via_spec(monkeypatch, tmp_path):
    import evals.__main__ as m

    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({
        "computer.scoped.enabled": True,
        "computer.scoped.registry": "fixture-registry"}))
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--case", "route_where_is_now",
        "--subject-overrides", str(fixture)])
    assert m.main() == 0
    assert json.loads(runs[0]) == {
        "computer.scoped.enabled": True,
        "computer.scoped.registry": "fixture-registry"}


def test_subject_overrides_content_and_hash_are_in_run_started(monkeypatch, tmp_path):
    import hashlib

    import evals.__main__ as m

    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    fixture = tmp_path / "fixture.json"
    body = json.dumps({"computer.scoped.enabled": True})
    fixture.write_text(body)
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--case", "route_where_is_now",
        "--subject-overrides", str(fixture), "--run-budget", "5"])
    assert m.main() == 0
    md = m.RunEvidence.last.metadata
    assert md["subject_overrides_file"] == str(fixture)
    assert md["subject_overrides_sha256"] == hashlib.sha256(
        body.encode()).hexdigest()
    assert md["subject_overrides"] == {"computer.scoped.enabled": True}
    assert md["run_budget"]["max_requests"] == 5


def test_an_invalid_subject_overrides_file_is_refused_before_any_case(
        monkeypatch, tmp_path, capsys):
    import evals.__main__ as m

    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2,3]")
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--case", "route_where_is_now",
        "--subject-overrides", str(bad)])
    assert m.main() == 2
    assert "JSON object" in capsys.readouterr().err
    assert runs == []


def test_a_missing_subject_overrides_file_is_refused(monkeypatch, tmp_path):
    import evals.__main__ as m

    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--case", "route_where_is_now",
        "--subject-overrides", str(tmp_path / "nope.json")])
    assert m.main() == 2
    assert runs == []


def test_run_budget_refuses_a_non_integer(monkeypatch, tmp_path, capsys):
    import evals.__main__ as m

    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--case", "route_where_is_now",
        "--run-budget", "not-a-number"])
    assert m.main() == 2
    assert "integers" in capsys.readouterr().err
    assert runs == []
