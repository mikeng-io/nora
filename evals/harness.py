"""Eval harness: drives the real `Handler` (real guards, real db) against a scripted
turn sequence and reports what happened as a `Trajectory`, for the deterministic
evaluators in `evals.evaluators` to grade.

Design note (this file's `run_case`/`Case` are design guidance, not pinned by
`tests/test_evals_gate.py` — that test only exercises `Trajectory` + the evaluators):
rather than reimplementing Handler wiring, this reuses the already-reviewed
integration-test machinery in `tests.integration.harness` (`build_handler`, `FakeLLM`,
`ScriptedTurn`, `group_event`). That machinery deliberately makes the real,
live-network branch of `Handler._invoke` unreachable by construction (see
`build_handler`'s docstring) — it always wraps whatever `script` you pass into a
`FakeLLM`. So today, `run_case` only supports the offline/scripted path: `script=None`
degrades to a zero-turn `FakeLLM` (a harmless silent run), it does NOT reach out to a
real model. Wiring an actual live-model code path (for the M7 stage's
`--suite all` network-requiring verification command) is out of scope for this task
and is left for later work.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tests.integration.harness import ScriptedTurn, build_handler, group_event

# Kept in sync with config.example.toml's engagement.trigger.text — the single real
# trigger string every case file and the harness itself must agree on.
TRIGGER_TEXT = "nora.000"


@dataclass
class Trajectory:
    tool_calls: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    reply: str | None = None
    honcho_feeds: int = 0
    prefill_sufficient: bool = True


@dataclass
class Case:
    name: str
    input: str
    expected_output: str | None = None
    trigger: bool = True
    prefill_sufficient: bool = True

    @classmethod
    def from_dict(cls, raw: dict) -> Case:
        meta = raw.get("metadata") or {}
        return cls(
            name=raw["name"],
            input=raw["input"],
            expected_output=raw.get("expected_output"),
            trigger=bool(meta.get("trigger", True)),
            prefill_sufficient=bool(meta.get("prefill_sufficient", True)),
        )


def load_cases(path: Path | str) -> list[Case]:
    """`yaml.safe_load` a case file into `Case`s. An empty/missing file yields []."""
    raw = yaml.safe_load(Path(path).read_text()) or []
    return [Case.from_dict(item) for item in raw]


def _build_body(case: Case) -> str:
    """Prefix `case.input` with the trigger text for trigger cases (unless the author
    already embedded it plausibly in the message), pass no-trigger cases through as-is.
    """
    if not case.trigger:
        return case.input
    if TRIGGER_TEXT.lower() in case.input.lower():
        return case.input
    return f"{TRIGGER_TEXT} {case.input}"


async def run_case(case: Case, *, script: list[ScriptedTurn] | None = None) -> Trajectory:
    """Run one eval `Case` through a real `Handler` in an isolated tmp directory and
    return the resulting `Trajectory`.

    `script` is the scripted model turn sequence (offline mode — see module docstring).
    The tool names it *attempts* become `Trajectory.tool_calls`: in scripted mode we
    already know exactly which tools the script tries to call, whether or not
    `ReplyGuard` ends up vetoing any given call, and that's the simplest, most direct
    signal of "what did the model try to do" for the evaluators to grade.
    """
    turns = script or []
    tool_calls = [name for turn in turns for name, _ in turn.tool_calls]

    tmp_dir = Path(tempfile.mkdtemp(prefix="nora-eval-"))
    try:
        h = await build_handler(tmp_dir, script=turns, debounce=0)
        body = _build_body(case)
        await h.handler.on_event(group_event(body=body))
        await h.drain()

        feeds = len([f for f in h.honcho.fed if "[Nora]" in f[1]])
        sent = list(h.sink.texts)
        return Trajectory(
            tool_calls=tool_calls,
            sent=sent,
            reply=sent[-1] if sent else None,
            honcho_feeds=feeds,
            prefill_sufficient=case.prefill_sufficient,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
