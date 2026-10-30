"""`scripts/character_synthesis_measure.py` exit status — no model, no network.

The judge is an inert collaborator keyed by the generated body and the draw
number, never one answer to every request: a body naming "draw-error" fails
its draws, "unstable" spreads its shares past the 0.2 band, anything else is
a stable reading. Credentials are monkeypatched synthetic; the OpenAI client
is constructed but never called.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

import scripts.character_synthesis_measure as csm


def _stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
           accounts: list[dict], *, credential: str = "synthetic-key") -> Path:
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(accounts), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["character_synthesis_measure", str(path)])
    monkeypatch.setattr(csm, "credentials", lambda: {"OMNIROUTE_API_KEY": credential})
    calls: dict[str, int] = defaultdict(int)

    async def judge(client, model, body, effort):
        calls[body] += 1
        if "draw-error" in body:
            return None
        if "unstable" in body:
            return (0.15 * calls[body], calls[body])
        return (0.4, 3)

    monkeypatch.setattr(csm, "judge", judge)
    return path


async def test_all_valid_draws_exit_zero(monkeypatch, tmp_path, capsys):
    _stage(monkeypatch, tmp_path, [
        {"key": "acc-room", "kind": "room", "body": "generated stable account one"},
        {"key": "acc-person", "kind": "person", "body": "generated stable account two"},
    ])
    assert await csm.main() == 0
    out = capsys.readouterr().out
    assert "KIND person:" in out and "KIND room:" in out


async def test_an_error_draw_exits_nonzero_and_keeps_the_valid_kinds(
    monkeypatch, tmp_path, capsys,
):
    _stage(monkeypatch, tmp_path, [
        {"key": "acc-room", "kind": "room", "body": "generated stable account"},
        {"key": "acc-person", "kind": "person", "body": "generated draw-error account"},
    ])
    assert await csm.main() == 1
    out = capsys.readouterr().out
    assert "acc-person" in out and "ERROR" in out
    # The successful population still reports; only the failed account is absent.
    assert "KIND room:" in out and "KIND person:" not in out


async def test_unstable_draws_exit_nonzero(monkeypatch, tmp_path, capsys):
    _stage(monkeypatch, tmp_path, [
        {"key": "acc-room", "kind": "room", "body": "generated unstable account"},
    ])
    assert await csm.main() == 1
    out = capsys.readouterr().out
    assert "UNSTABLE" in out and "KIND" not in out


async def test_no_usable_population_exits_nonzero(monkeypatch, tmp_path, capsys):
    _stage(monkeypatch, tmp_path, [
        {"key": "acc-a", "kind": "room", "body": "generated draw-error a"},
        {"key": "acc-b", "kind": "room", "body": "generated draw-error b"},
    ])
    assert await csm.main() == 1
    assert "KIND" not in capsys.readouterr().out


async def test_a_missing_credential_refuses_before_any_draw(
    monkeypatch, tmp_path, capsys,
):
    _stage(monkeypatch, tmp_path, [
        {"key": "acc-room", "kind": "room", "body": "generated stable account"},
    ], credential="")
    assert await csm.main() == 2
    assert "credential is missing or empty" in capsys.readouterr().err
