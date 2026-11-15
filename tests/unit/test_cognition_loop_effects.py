"""A broken lifecycle reader retains the subject trajectory without dispatching judges."""

from types import SimpleNamespace

from tests.unit.test_cognition_loop_run import _runner_subject, _wake_args, _wake_seam


async def test_lifecycle_read_failure_keeps_terminal_evidence_and_names_the_error(
    monkeypatch, tmp_path,
):
    inspection = {"system": "Generated complete subject context", "agenda": "Generated agenda"}
    run = _wake_seam(monkeypatch, tmp_path, _runner_subject(inspection=inspection))
    args, evidence = _wake_args(tmp_path)
    calls = []

    async def unavailable(*args, **kwargs):
        raise RuntimeError("generated canonical history read failure")

    async def judge(*args, **kwargs):
        calls.append(1)
        raise AssertionError("a broken instrument must not dispatch a judge")

    monkeypatch.setattr(run, "read_effects", unavailable)
    monkeypatch.setattr(run, "_judge", judge)
    result = await run._run_wake(args, SimpleNamespace(unit="u", expectations=(), read_probes=()),
        0, 11, 1_800_000_000_000, "postgresql+asyncpg://x", "time", None,
        evidence, tmp_path, ("signal", "r"))
    assert result["status"] == "ERROR" and result["step"] == "report"
    assert "generated canonical history read failure" in result["error"]
    assert result["trajectory"]["blocks"]["system"] == inspection["system"]
    assert calls == []
