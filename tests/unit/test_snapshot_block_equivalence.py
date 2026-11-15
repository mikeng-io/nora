# Copyright (c) 2026 Mike's LLC and contributors
"""Unit tests for ``scripts/snapshot_block_equivalence.py``:

the fail-closed half of the live comparison — a required historical load
(`config.toml`, `policy.toml`, `identity.toml`) that `materialise` could not
establish is a named exit-2 before any recording is opened or subject
composed, and unhandled execution/evidence errors are exit 2 rather than a
traceback exit."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nora.infra.sql.snapshot_at import CONFIG_FILES, InForce, InstructionLoad
from nora.subject.recorded_services import RecordedServices
from scripts import snapshot_block_equivalence as snap


class _RecordingOpened(RuntimeError):
    """The probe's `RecordedServices.load` ran; raised to stop the run before
    the recorded environment, subject or render would need real services."""


def _args(tmp_path: Any, **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "source": "postgresql:///generated_eval",
        "at": "2026-01-01T00:00:00+00:00",
        "message_id": 1,
        "prefill_text": "generated prefill",
        "services": str(tmp_path / "services.json"),
        "workdir": str(tmp_path),
        "set": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _instruction_load(name: str) -> InstructionLoad:
    """One recorded load, shaped the way `in_force` builds it."""
    return InstructionLoad(
        name=name, loaded_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_sha256="a" * 64, files={name: "a" * 64})


def _in_force(config: dict[str, InstructionLoad | None]) -> InForce:
    """A complete `materialise` result for `config`'s per-file loads."""
    return InForce(
        doctrine=_instruction_load("doctrine"),
        dreamer=_instruction_load("dreamer"),
        config=config,
        deploy={"version": "v2026.11.1", "git_sha": "b" * 40,
                "image_id": "sha256:" + "c" * 64})


def _recording_observer(reached: list[str]) -> Any:
    """A `RecordedServices.load` that records the read, then refuses."""
    def load(path: str | Path) -> RecordedServices:
        reached.append(str(path))
        raise _RecordingOpened(str(path))
    return load


@pytest.mark.asyncio
async def test_a_missing_required_load_refuses_before_any_recording(
        monkeypatch: Any, tmp_path: Any, capsys: Any) -> None:
    """`config.toml`/`policy.toml`/`identity.toml` absent at `--at` is a named
    error, never a template fallback: the run stops before
    `RecordedServices.load` could even read `--services`."""
    engine = SimpleNamespace(dispose=AsyncMock())
    forced = _in_force(config=dict.fromkeys(CONFIG_FILES))
    reached: list[str] = []

    async def materialise(source: Any, at: Any, target: Any) -> Any:
        return forced, ()

    monkeypatch.setattr(snap, "create_readonly_sql_engine", lambda url: engine)
    monkeypatch.setattr(snap, "materialise", materialise)
    monkeypatch.setattr(RecordedServices, "load", _recording_observer(reached))

    try:
        rc = await snap._main(_args(tmp_path))
    except _RecordingOpened as exc:
        pytest.fail(
            f"required loads absent at --at yet the recording was read: {exc}")
    assert rc == 2
    engine.dispose.assert_awaited_once()
    _, err = capsys.readouterr()
    assert "required load(s) not recorded by --at" in err
    for name in ("config.toml", "policy.toml", "identity.toml"):
        assert name in err
    assert reached == []


@pytest.mark.asyncio
async def test_recorded_required_loads_reach_the_recording(
        monkeypatch: Any, tmp_path: Any) -> None:
    """Positive control for the refusal probe: every required load recorded
    means the same observer sees `RecordedServices.load` reached, so the
    refusal test's `reached == []` measures the guard, not a dead probe."""
    engine = SimpleNamespace(dispose=AsyncMock())
    forced = _in_force(
        config={name: _instruction_load(name) for name in CONFIG_FILES})
    reached: list[str] = []

    async def materialise(source: Any, at: Any, target: Any) -> Any:
        return forced, ()

    monkeypatch.setattr(snap, "create_readonly_sql_engine", lambda url: engine)
    monkeypatch.setattr(snap, "materialise", materialise)
    monkeypatch.setattr(RecordedServices, "load", _recording_observer(reached))

    with pytest.raises(_RecordingOpened):
        await snap._main(_args(tmp_path))
    assert reached == [str(tmp_path / "services.json")]


@pytest.mark.asyncio
async def test_an_at_without_an_offset_is_rejected(
        tmp_path: Any, capsys: Any) -> None:
    """A naive `--at` cannot name the historical instant to restore to."""
    rc = await snap._main(_args(tmp_path, at="2026-01-01T00:00:00"))
    assert rc == 2
    assert "--at" in capsys.readouterr().err


def test_an_execution_error_exits_2_not_a_traceback(
        monkeypatch: Any, capsys: Any) -> None:
    async def boom(args: Any) -> int:
        raise RuntimeError("generated evidence failure")

    monkeypatch.setattr(snap, "_main", boom)
    monkeypatch.setattr(
        "sys.argv",
        ["snapshot_block_equivalence.py", "--source", "x", "--at", "x",
         "--message-id", "1", "--prefill", "/dev/null", "--services", "x",
         "--workdir", "x"])
    assert snap.main() == 2
    out, err = capsys.readouterr()
    assert "unavailable" in err.lower()
    assert "generated evidence failure" not in out + err
