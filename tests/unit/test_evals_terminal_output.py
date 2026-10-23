"""What `python -m evals` prints to a terminal. A case's input, a judge's
reason and a draw's position are text a model or a room wrote; a raw escape in
one would move the cursor, clear the screen or retitle the window of whoever
reads the output. They are printed with their control characters shown, never
interpreted, and the line structure is kept.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import evals.__main__ as m
from evals.terminal import terminal_safe


def test_control_characters_and_escapes_are_shown_not_interpreted() -> None:
    raw = "a\x1b[2Jb\x07c\rd\x9b31me\x08"
    assert terminal_safe(raw) == "a\\x1b[2Jb\\x07c\\x0dd\\x9b31me\\x08"
    assert "\x1b" not in terminal_safe(raw)


def test_newlines_and_tabs_are_kept_so_the_layout_survives() -> None:
    assert terminal_safe("one\n\ttwo\n") == "one\n\ttwo\n"


class _Case:
    name = "c1"
    metadata = None

    def model_copy(self, update: dict) -> _Case:
        return self


class _Shown:
    def __call__(self, _case: object) -> dict:
        shown = SimpleNamespace(name=m.NORA_SHOWN, state="room says\x1b[2J\x1b]0;pwned\x07")
        return {"environment_state": [shown]}


def test_print_input_shows_the_rendered_input_without_escapes(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    monkeypatch.setattr(m, "load_corpus", lambda name: [_Case()])
    monkeypatch.setattr(m, "nora_task", lambda _spec: _Shown())
    assert m._print_input("standing", "c1", source_url="") == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "room says\\x1b[2J\\x1b]0;pwned\\x07" in out


def test_print_input_error_is_also_terminal_safe(monkeypatch, capsys) -> None:
    monkeypatch.setattr(m, "load_corpus", lambda name: [_Case()])
    error = SimpleNamespace(name=m.NORA_ERROR, state="failure\x1b[2J\x07")
    monkeypatch.setattr(m, "nora_task", lambda _: lambda _: {"environment_state": [error]})
    assert m._print_input("standing", "c1", source_url="") == 1
    err = capsys.readouterr().err
    assert "failure\\x1b[2J\\x07" in err
