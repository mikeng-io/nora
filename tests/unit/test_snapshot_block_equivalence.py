# Copyright (c) 2026 Mike's LLC and contributors
"""Unit tests for ``scripts/snapshot_block_equivalence.py``:

the fail-closed half of the live comparison — a required historical load
(`config.toml`, `policy.toml`, `identity.toml`) that `materialise` could not
establish is a named exit-2 before any recording is opened or subject
composed, and unhandled execution/evidence errors are exit 2 rather than a
traceback exit."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scripts import snapshot_block_equivalence as snap


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


@pytest.mark.asyncio
async def test_a_missing_required_load_refuses_before_any_recording(
        monkeypatch: Any, tmp_path: Any, capsys: Any) -> None:
    """`config.toml`/`policy.toml`/`identity.toml` absent at `--at` is a named
    error, never a template fallback: the run stops before
    `RecordedServices.load` could even read `--services`."""
    engine = SimpleNamespace(dispose=AsyncMock())
    forced = SimpleNamespace(
        missing=lambda: ["config.toml", "policy.toml", "identity.toml"])

    async def materialise(source: Any, at: Any, target: Any) -> Any:
        return forced, object()

    monkeypatch.setattr(snap, "create_readonly_sql_engine", lambda url: engine)
    monkeypatch.setattr(snap, "materialise", materialise)

    rc = await snap._main(_args(tmp_path))
    assert rc == 2
    engine.dispose.assert_awaited_once()
    _, err = capsys.readouterr()
    for name in ("config.toml", "policy.toml", "identity.toml"):
        assert name in err


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
