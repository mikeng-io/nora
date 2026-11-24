# Copyright (c) 2026 Mike's LLC and contributors
"""Unit tests for ``scripts/confirm_cognition_anchors.py``:

- the boolean answer contract on a shared bound ``Judge`` over the real
  OpenAI SDK and a generated ``MockTransport`` (no HTTP),
- per-wake ``SubjectSpec`` assembly: restored sandbox database, an active
  recorded environment, deployment identity/policy/config, historical
  cognition/memory overrides, and no workspace rewrite,
- the run's evidence/budget outer loop, one restore per wake, and
  named-unavailable semantics for a wake with no recording,
- the private/public boundary: no message text or stack trace may leave
  the machine boundary,
- ``memory_state`` reads a restored sandbox connection."""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from evals.scenarios import COGNITION_SESSION_OVERRIDES, MEMORY_INDEX_OVERRIDES
from evals.subject_config import FULL_RESTORE_OVERRIDES
from nora.subject.obtain import SubjectSpec
from nora.subject.recorded_services import RecordedServices
from scripts import confirm_cognition_anchors as confirm

_RECORDING = {
    "version": 1,
    "bindings": {"llm.base_url": "model"},
    "services": {
        "model": [{"method": "POST", "path": "/v1/chat/completions",
                   "response": {"status": 200, "json": {"usage": {}}}}],
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "want"),
    [
        ("match", "match"),
        ("miss", "no_match"),
        ("junk", "unavailable"),
        ("empty", "unavailable"),
        ("no_choices", "unavailable"),
        ("truncated", "unavailable"),
        ("truncated_empty", "unavailable"),
    ],
)
async def test_boolean_model_contract(
        monkeypatch: Any, mode: str, want: str) -> None:
    """The strict contract runs on the real SDK surface over a generated
    response: match/no_match parse; anything else is unavailable at the
    per-item boundary — never a negative verdict."""
    moment: dict[str, Any] = {"at_ms": 1}
    captured: dict[str, Any] = {}

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        captured["payload"] = payload
        assert payload["model"] == "generated-production-model"
        assert payload["max_tokens"] == 2000
        assert payload["temperature"] == 0
        assert payload["reasoning_effort"] == "low"
        assert payload["messages"] == [
            {"role": "system", "content": "generated-brief"},
            {"role": "user", "content": "generated-moment"},
        ]
        if mode == "no_choices":
            body = {"choices": []}
        elif mode == "truncated_empty":
            body = {"choices": [
                {"finish_reason": "length", "message": {"content": None}}]}
        else:
            body = {"choices": [{"finish_reason": "length" if mode ==
                                 "truncated" else "stop",
                                 "message": {"content": {
                                     "match": '{"match": true}',
                                     "miss": '{"match": false}',
                                     "junk": "not-json",
                                     "empty": None}[mode]}}]}
        return httpx.Response(200, json=body)

    from evals.rebuilt.grading import Judge
    client = AsyncOpenAI(
        base_url="https://generated.invalid/v1", api_key="generated-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(serve)))
    instrument = Judge(base_url="", model="", timeout_seconds=5.0)
    bound = SimpleNamespace(
        _endpoint=lambda: ("https://generated.invalid/v1", "generated-key",
                           "generated-production-model"),
        _client=lambda: client,
        _complete=instrument._complete)
    monkeypatch.setattr(
        "nora.language.confirmation_render.confirmation_prompts",
        lambda scenario, moment_json: ("generated-brief", "generated-moment"))
    try:
        result = await confirm.judge(
            bound, {"max_tokens": 2000, "reasoning_effort": "low"},
            "generated-scenario", moment)
    except Exception:  # noqa: BLE001 -- the run's per-item boundary names it unavailable
        result = "unavailable"
    assert result == want
    assert captured["payload"]["model"] == "generated-production-model"


def test_private_processing_never_prints_text(
        capsys: Any, tmp_path: Any, monkeypatch: Any) -> None:
    private = "generated-private-message-text"

    async def private_processing(source_url: Any, config: Any, rows: Any,
                                 aliases: Any, recordings: Any) -> list[dict]:
        assert source_url.endswith("_eval")
        assert set(recordings) == {9}
        raise RuntimeError(
            f"synthetic failure while handling {private} from room9")

    config = tmp_path / "config.toml"
    config.write_text('[llm]\nmodel = "nora"\n')
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(json.dumps(
        {"room": "room_a", "wake": 9, "scenario": 3}) + "\n")
    services = tmp_path / "services.json"
    services.write_text(json.dumps({"9": _RECORDING}))
    argv = [
        "confirm_cognition_anchors.py",
        "--source-url", "postgresql:///x_eval",
        "--config", str(config),
        "--candidates", str(candidates),
        "--services", str(services),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(confirm, "confirm", private_processing)
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.room_aliases",
        lambda: {"real-room-9": "room_a"})
    rc = confirm.main()
    assert rc == 2
    out, err = capsys.readouterr()
    assert "unavailable" in (out + err).lower()
    assert private not in out
    assert private not in err


def test_load_recordings_validates_the_whole_manifest(tmp_path: Any) -> None:
    path = tmp_path / "wakes.json"
    path.write_text(json.dumps({"9": _RECORDING, "10": _RECORDING}))
    recordings = confirm.load_recordings(path)
    assert set(recordings) == {9, 10}
    assert all(isinstance(r, RecordedServices) for r in recordings.values())

    path.write_text(json.dumps({"nine": _RECORDING}))
    with pytest.raises(ValueError):
        confirm.load_recordings(path)
    # Every alias `int()` would collapse onto a wake is refused before any
    # document is read: zero, leading zeros, signs, whitespace and Unicode
    # digits are not canonical wake ids.
    for alias in ("+9", "0", "09", "007", "٩", "9 ", " 9"):
        path.write_text(json.dumps({alias: _RECORDING}))
        with pytest.raises(ValueError):
            confirm.load_recordings(path)
    # Distinct manifest keys cannot collapse onto one wake's recording.
    path.write_text(json.dumps({"9": _RECORDING, "09": _RECORDING}))
    with pytest.raises(ValueError):
        confirm.load_recordings(path)
    path.write_text(json.dumps(
        {"9": {"version": 1, "bindings": {}, "services": {}}}))
    with pytest.raises(ValueError):
        confirm.load_recordings(path)
    path.write_text('{"9": NaN}')
    with pytest.raises(ValueError):
        confirm.load_recordings(path)
    path.write_text("{}")
    with pytest.raises(ValueError):
        confirm.load_recordings(path)


@pytest.mark.asyncio
async def test_wake_input_composes_the_production_spec(
        monkeypatch: Any, tmp_path: Any) -> None:
    seen: dict[str, SubjectSpec] = {}

    async def obtain(spec: SubjectSpec) -> Any:
        seen["spec"] = spec
        runner = SimpleNamespace(inspect=AsyncMock(return_value={
            "system": "generated-system", "agenda": "generated-agenda"}))
        return SimpleNamespace(
            deps=SimpleNamespace(cognition_runner=runner),
            unit="generated-unit", cfg="generated-cfg")

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "identity.toml").write_text('name = "generated"')
    (cfg_dir / "policy.toml").write_text('default = "deny"')
    environment = object()
    view = SimpleNamespace(
        unit="generated-unit",
        pending=[SimpleNamespace(platform="signal",
                                 conversation_id="generated-room")])
    monkeypatch.setattr("nora.subject.obtain", obtain)
    monkeypatch.setattr("evals.backbone._close", AsyncMock())
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.memory_state",
        AsyncMock(return_value={"open_notes": []}))

    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.create_async_engine",
        lambda url: SimpleNamespace(
            connect=lambda: _Context(object()),
            dispose=AsyncMock()))

    subject_config = tmp_path / "private" / "config.evaluation.toml"
    moment, cfg = await confirm.wake_input(
        "postgresql:///generated_sandbox", 90_000, view,
        "generated-room", cfg_dir, subject_config, True, environment)
    assert cfg == "generated-cfg"
    spec = seen["spec"]
    assert spec.database_url == "postgresql:///generated_sandbox"
    # The deployment's rebuilt config binds through the declared spec field,
    # not a mutated module template.
    assert spec.config == subject_config
    assert spec.environment is environment
    assert spec.room == ("signal", "generated-room")
    assert spec.policy == 'default = "deny"'
    assert spec.identity == 'name = "generated"'
    assert spec.now == datetime.fromtimestamp(90, UTC)
    assert spec.overrides == {**COGNITION_SESSION_OVERRIDES, **FULL_RESTORE_OVERRIDES,
                              **MEMORY_INDEX_OVERRIDES}
    assert spec.overrides["cognition.enabled"] is True
    assert spec.overrides["memory.index.read"] is True
    assert moment == {"at_ms": 90_000, "room": "generated-room",
                      "platform": "signal", "system": "generated-system",
                      "agenda": "generated-agenda",
                      "memory": {"open_notes": []}}


class _Context:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patched_outer_loop(
        monkeypatch: Any, tmp_path: Any) -> tuple[Path, SimpleNamespace]:
    """Every seam of ``confirm`` that touches the outside — except the
    eval lock, the evidence capture and the run budget, which stay REAL."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "identity.toml").write_text('name = "generated"')
    (cfg_dir / "policy.toml").write_text('default = "deny"')
    config = cfg_dir / "config.toml"
    config.write_text('[llm]\nmodel = "nora"\n')
    state = SimpleNamespace(materialise_target=None)

    conn = SimpleNamespace(
        begin=lambda: _Context(None),
        execute=AsyncMock(),
        scalar=AsyncMock(return_value="generated_eval"))
    engine = SimpleNamespace(
        connect=lambda: _Context(conn), dispose=AsyncMock())
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.create_async_engine",
        lambda url: engine)

    def materialise(*, example: Any, deployment: Any, target: Any) -> Path:
        state.materialise_target = Path(target)
        return target

    def load(path: Any, require_secrets: bool = True) -> Any:
        return SimpleNamespace(
            llm=SimpleNamespace(model_dump=lambda: {
                "timeout_seconds": 5.0, "max_tokens": 10,
                "reasoning_effort": "low"}),
            memory=SimpleNamespace(
                index=SimpleNamespace(read=False)))

    async def wake_from_snapshot(url: Any, wake_id: Any) -> tuple[int, Any]:
        return 90_000, SimpleNamespace(
            unit="generated-unit",
            pending=[SimpleNamespace(platform="signal",
                                     conversation_id="generated-room")])

    @asynccontextmanager
    async def sandbox_as_of(url: Any, at: Any):
        yield "postgresql:///generated_sandbox", SimpleNamespace(copied={})

    monkeypatch.setattr("evals.subject_config.materialise", materialise)
    monkeypatch.setattr("nora.config.load", load)
    monkeypatch.setattr(
        "nora.subject.cognition.wake_from_snapshot", wake_from_snapshot)
    monkeypatch.setattr(
        "nora.subject.sandbox.sandbox_as_of", sandbox_as_of)
    return config, state


class _Recording:
    def __init__(self, wake: int) -> None:
        self.wake = wake
        self.opened: list[tuple[Any, bool]] = []

    @asynccontextmanager
    async def open(self, url: Any, redis: bool = False):
        self.opened.append((url, redis))
        yield f"generated-environment-{self.wake}"


@pytest.mark.asyncio
async def test_confirm_restores_once_per_wake(
        monkeypatch: Any, tmp_path: Any) -> None:
    """The outer loop restores once per wake, keeps every item on the one
    wake input, and runs each verdict through the bound ``Judge`` — the
    eval route resolved from the obtained subject config."""
    config, state = _patched_outer_loop(monkeypatch, tmp_path)
    inputs = [{"room": "room_a", "wake": 9, "scenario": s}
              for s in (3, 4)] + [
        {"room": "room_b", "wake": 10, "scenario": 6}]
    checked: list[str] = []

    async def wake_input(sandbox_url: Any, at_ms: Any, view: Any, room: Any,
                         config_dir: Any, subject_config: Any,
                         memory_index: Any,
                         environment: Any) -> tuple[dict, Any]:
        assert environment in {"generated-environment-9",
                               "generated-environment-10"}
        # The subject composes the file this run materialised privately.
        assert subject_config == state.materialise_target
        return {"agenda": "generated"}, "generated-cfg"

    async def judge(bound: Any, llm: Any, scenario: Any, moment: Any) -> str:
        assert bound.config == "generated-cfg"
        checked.append("judge")
        return "match"

    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.wake_input", wake_input)
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.judge", judge)

    recordings = {9: _Recording(9), 10: _Recording(10)}
    result = await confirm.confirm(
        "postgresql:///generated_eval", config,
        inputs, {"realroom-a": "room_a", "realroom-b": "room_b"},
        recordings)
    assert len(checked) == 3
    assert [row["result"] for row in result] == ["match"] * 3
    # The deployment's rebuilt config lands in this run's own directory,
    # never the checkout's shared `config.evaluation.toml`.
    assert state.materialise_target is not None
    assert state.materialise_target.name == "config.evaluation.toml"
    assert confirm.ROOT not in state.materialise_target.parents
    # One environment per wake, opened on the restored sandbox URL with
    # redis for the bridge workers' composition parity.
    for wake_id, recording in recordings.items():
        assert recording.opened == [("postgresql:///generated_sandbox", True)], (
            f"wake {wake_id} environment was not opened on the restored "
            "sandbox with redis")


@pytest.mark.asyncio
async def test_per_item_errors_are_unavailable_never_negative(
        monkeypatch: Any, tmp_path: Any) -> None:
    """A failure composing or judging one candidate names that item
    `unavailable`; the other item on the wake is unaffected."""
    config, _ = _patched_outer_loop(monkeypatch, tmp_path)
    calls: list[str] = []

    async def wake_input(sandbox_url: Any, at_ms: Any, view: Any, room: Any,
                         config_dir: Any, subject_config: Any,
                         memory_index: Any,
                         environment: Any) -> tuple[dict, Any]:
        if calls:
            raise RuntimeError("generated inspection failure")
        calls.append("first")
        return {"agenda": "generated"}, "generated-cfg"

    async def judge(bound: Any, llm: Any, scenario: Any,
                    moment: Any) -> str:
        return "match"

    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.wake_input", wake_input)
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.judge", judge)

    result = await confirm.confirm(
        "postgresql:///generated_eval", config,
        [{"room": "room_a", "wake": 9, "scenario": s} for s in (3, 4)],
        {"realroom-a": "room_a"}, {9: _Recording(9)})
    assert [row["result"] for row in result] == ["match", "unavailable"]


@pytest.mark.asyncio
async def test_a_violated_boundary_makes_judged_items_unavailable(
        monkeypatch: Any, tmp_path: Any) -> None:
    """When the recorded-services environment reports an unrecorded call at
    close, the wake's evidence is incomplete: even an already-judged item on
    that wake is `unavailable`, never `match`."""
    config, _ = _patched_outer_loop(monkeypatch, tmp_path)

    class _Violated(_Recording):
        @asynccontextmanager
        async def open(self, url: Any, redis: bool = False):
            yield f"generated-environment-{self.wake}"
            raise RuntimeError("external_response_not_recorded")

    async def wake_input(*args: Any) -> tuple[dict, Any]:
        return {"agenda": "generated"}, "generated-cfg"

    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.wake_input", wake_input)
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.judge",
        AsyncMock(return_value="match"))

    result = await confirm.confirm(
        "postgresql:///generated_eval", config,
        [{"room": "room_a", "wake": 9, "scenario": s} for s in (3, 4)],
        {"realroom-a": "room_a"}, {9: _Violated(9)})
    assert [row["result"] for row in result] == ["unavailable"] * 2


@pytest.mark.asyncio
async def test_missing_recording_makes_the_whole_wake_unavailable(
        monkeypatch: Any, tmp_path: Any) -> None:
    config, _ = _patched_outer_loop(monkeypatch, tmp_path)
    inputs = [{"room": "room_a", "wake": 7, "scenario": 3},
              {"room": "room_a", "wake": 7, "scenario": 4}]
    wake_input = AsyncMock()
    monkeypatch.setattr(
        "scripts.confirm_cognition_anchors.wake_input", wake_input)

    result = await confirm.confirm(
        "postgresql:///generated_eval", config,
        inputs, {"realroom-a": "room_a"}, {})
    wake_input.assert_not_awaited()
    assert [row["result"] for row in result] == ["unavailable"] * 2


@pytest.mark.asyncio
async def test_memory_state_reads_the_restored_sandbox() -> None:
    captured: list[str] = []

    class _Rows:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def mappings(self) -> Any:
            return iter(self._rows)

        def all(self) -> list[Any]:
            return self._rows

    class _Conn:
        async def execute(self, sql: Any, params: Any = None) -> Any:
            captured.append(str(sql))
            if params is not None:
                return _Rows([{"id": "n1", "body": "generated-note"}])
            return _Rows([{"id": "generated-claim", "part": "fact"}])

    state = await confirm.memory_state(_Conn(), "generated-unit", room="signal:generated")
    assert set(state) == {"open_notes", "open_events", "profile_claims"}
    assert state["profile_claims"] == [{"id": "generated-claim", "part": "fact"}]
    assert any("FROM claim" in sql for sql in captured)
    assert any("FROM note" in sql for sql in captured)


@pytest.mark.asyncio
async def test_memory_state_surfaces_engine_failures() -> None:
    class _Conn:
        async def execute(self, sql: Any, params: Any = None) -> Any:
            raise OSError("generated-restoration-missing")

    with pytest.raises(OSError, match="generated-restoration-missing"):
        await confirm.memory_state(_Conn(), "generated-unit", room="signal:generated")
