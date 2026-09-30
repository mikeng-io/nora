"""The one subprocess seam: docker and opencli both go through it, so tests
substitute a fake and no test spawns a real process."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RunResult:
    rc: int
    stdout: str = ""
    stderr: str = ""


#: (argv, timeout_seconds, stdin_text) -> RunResult. Never raises: a spawn
#: failure or timeout is rc 127 / 124, which every caller treats as ERROR.
Runner = Callable[[Sequence[str], float, str | None], RunResult]


def subprocess_runner(argv: Sequence[str], timeout: float, stdin: str | None) -> RunResult:
    try:
        proc = subprocess.run(  # noqa: S603 — argv is built from constants and config
            list(argv), input=stdin, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return RunResult(124, "", "timeout")
    except OSError as exc:
        return RunResult(127, "", type(exc).__name__)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)
