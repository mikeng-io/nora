"""Generated-only confirmation and stdout/stderr custody checks."""
import json
import logging
import sys

import httpx
import pytest

from scripts import confirm_cognition_anchors as confirm


@pytest.mark.parametrize(("content", "finish", "expected"), [
    ('{"match":true}', "stop", "match"),
    ('{"match":false}', "stop", "no_match"),
    ('{"match":"false"}', "stop", "unavailable"),
    ('{"match":true,"reason":"generated secret"}', "stop", "unavailable"),
    ('{"match":true}', "length", "unavailable"),
    ('[]', "stop", "unavailable"),
    ('{"ok":true}', "stop", "unavailable"),
])
async def test_boolean_model_contract(content, finish, expected, monkeypatch):
    monkeypatch.setenv("GENERATED_MODEL_KEY", "generated-key")

    def serve(request):
        payload = json.loads(request.content)
        if "response_format" in payload:  # what the deployment's route answers
            return httpx.Response(503, json={"error": {"code": "ALL_TARGETS_SKIPPED"}})
        assert payload["model"] == "generated-production-model"
        system = payload["messages"][0]["content"]
        assert '{"match": true}' in system and '{"match": false}' in system
        assert "Must not:" in system
        assert payload["temperature"] == 0
        # The moment reaches the model as ONE typed evidence block: read it
        # back through the tag boundary and the escapes it was wrapped in.
        user = payload["messages"][1]["content"]
        assert user.startswith("<wake_moment") and user.endswith("</wake_moment>")
        from xml.sax.saxutils import unescape
        moment = json.loads(unescape(user.split(">\n", 1)[1].rsplit("\n</wake_moment>", 1)[0]))
        assert moment["messages"][0] == {"at_ms": 1000, "speaker": "speaker_1", "text": "k"}
        return httpx.Response(200, json={"choices": [{"finish_reason": finish,
                                                    "message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        result = await confirm.judge(client, {
            "base_url": "https://generated.invalid/v1", "api_key_env": "GENERATED_MODEL_KEY",
            "model": "generated-production-model", "max_tokens": 2000, "temperature": 0.7,
        }, confirm.scenarios()[2], {"messages": [
            {"at_ms": 1000, "speaker": "speaker_1", "text": "k"}]}, "generated-model-key")
    assert result == expected


@pytest.mark.parametrize("fails", [False, True])
def test_private_processing_never_prints_text(tmp_path, monkeypatch, capsys, fails):
    inputs = tmp_path / "candidates.jsonl"
    inputs.write_text(json.dumps({"room": "room_generated", "wake": 1001,
                                 "scenario": 2, "counts": {"prior": 0}}))
    monkeypatch.setattr(sys, "argv", ["confirm", "--source-url", "generated",
                                     "--config", "generated", "--candidates", str(inputs)])
    monkeypatch.setattr(confirm, "room_aliases", lambda: {"generated-real": "room_generated"})

    async def private_processing(source_url, config, rows, aliases):
        print("GENERATED PRIVATE MESSAGE")
        print("GENERATED PRIVATE MESSAGE", file=sys.stderr)
        logging.getLogger("generated").critical("GENERATED PRIVATE MESSAGE")
        if fails:
            raise RuntimeError("GENERATED PRIVATE MESSAGE")
        return [{**rows[0], "result": "match"}]

    monkeypatch.setattr(confirm, "confirm", private_processing)
    previous = logging.root.manager.disable
    assert confirm.main() == (2 if fails else 0)
    assert logging.root.manager.disable == previous
    output = capsys.readouterr()
    assert "GENERATED PRIVATE MESSAGE" not in output.out + output.err
    if fails:
        assert output.err.startswith("anchor confirmation unavailable;")
        assert not output.out
    else:
        assert json.loads(output.out) == {"room": "room_generated", "wake": 1001,
                                         "scenario": 2, "result": "match"}
        assert not output.err


@pytest.mark.parametrize("value", [
    {"room": "generated-real", "wake": 1001, "scenario": 2},
    {"room": "room_other", "wake": 1001, "scenario": 2},
    {"room": "room_generated", "wake": True, "scenario": 2},
    {"room": "room_generated", "wake": 1001, "scenario": 7},
])
def test_output_projection_refuses_unmapped_or_invalid_candidates(value):
    with pytest.raises(ValueError):
        confirm.candidate(value, {"generated-real": "room_generated"})


async def test_wake_input_composes_the_corpus_subject_and_carries_open_state_and_room(
        tmp_path, monkeypatch):
    from contextlib import asynccontextmanager, nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import evals.backbone
    import nora.subject
    import nora.subject.sandbox_roster
    from nora.domain.stimuli import Stimulus, UnitView

    view = UnitView(unit="generated-unit", pending=(Stimulus(
        id=1, unit="generated-unit", kind="message", item="message", ref="messages:1",
        at_ms=4000, recorded_at_ms=4000, platform="signal",
        conversation_id="generated-room"),), last_activity_ms=None, last_wake=None)
    monkeypatch.setattr(nora.subject.sandbox_roster, "recorded_rosters", lambda **kw: nullcontext())
    for name in ("config.toml", "identity.toml", "policy.toml"):
        (tmp_path / name).write_text("generated")
    inspection = {"system": "generated doctrine", "agenda": "generated assembled agenda"}
    runner = SimpleNamespace(inspect=AsyncMock(return_value=inspection))
    subject = SimpleNamespace(deps=SimpleNamespace(cognition_runner=runner), unit=view.unit)
    obtained = AsyncMock(return_value=subject)
    monkeypatch.setattr(nora.subject, "obtain", obtained)
    monkeypatch.setattr(evals.backbone, "_close", AsyncMock())
    @asynccontextmanager
    async def connection():
        yield object()
    engine = SimpleNamespace(connect=lambda *a, **k: connection(), dispose=AsyncMock())
    monkeypatch.setattr(confirm, "create_async_engine", lambda url: engine)
    memory = {"open_notes": [{"body": "generated open gift handover"}],
              "open_events": [{"title": "generated dinner", "status": "proposed"}],
              "profile_lines": [{"text": "generated prior visit"}]}
    monkeypatch.setattr(confirm, "memory_state", AsyncMock(return_value=memory))
    result = await confirm.wake_input("generated-sandbox", 5000, view, "generated-room",
                                      tmp_path / "config.toml", "generated-eval", False)
    assert result == {"at_ms": 5000, "room": "generated-room", "platform": "signal",
                      "system": "generated doctrine",
                      "agenda": "generated assembled agenda", "memory": memory}
    spec = obtained.await_args.args[0]
    assert spec.database_url == "generated-sandbox"
    assert spec.room == ("signal", "generated-room")
    assert spec.overrides["cognition.enabled"] is True
    assert spec.overrides["honcho.workspace_id"] == "generated-eval"
    runner.inspect.assert_awaited_once_with(view=view, now_ms=5000)


async def test_confirm_restores_once_per_wake(tmp_path, monkeypatch):
    import sys
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import evals.subject_config
    import nora.config
    import nora.subject.cognition
    import nora.subject.memory_workspace
    import nora.subject.obtain
    import nora.subject.sandbox

    (tmp_path / "config.toml").write_text("generated")
    monkeypatch.setattr(evals.subject_config, "materialise", lambda **kw: None)
    cfg = SimpleNamespace(
        llm=SimpleNamespace(model_dump=lambda: {"timeout_seconds": 5, "max_tokens": 10}),
        honcho=SimpleNamespace(workspace_id="generated"),
        memory=SimpleNamespace(index=SimpleNamespace(read=False)))
    monkeypatch.setattr(nora.config, "load", lambda *a, **k: cfg)
    monkeypatch.setattr(sys.modules["nora.subject.obtain"], "model_endpoint",
                        lambda *a, **k: ("https://generated.invalid", "k", "m"))
    monkeypatch.setattr(nora.subject.memory_workspace, "shared_eval_workspace",
                        lambda ws: f"{ws}-eval")
    wakes = {9: (5000, "view-nine"), 10: (6000, "view-ten")}
    monkeypatch.setattr(nora.subject.cognition, "wake_from_snapshot",
                        AsyncMock(side_effect=lambda url, wake: wakes[wake]))
    restored = []
    @asynccontextmanager
    async def sandbox(url, now):
        restored.append(now)
        yield "generated-sandbox", None
    monkeypatch.setattr(nora.subject.sandbox, "sandbox_as_of", sandbox)
    conn = SimpleNamespace(begin=lambda: connection(),
                           execute=AsyncMock(),
                           scalar=AsyncMock(return_value="generated_eval"))
    @asynccontextmanager
    async def connection():
        yield conn
    engine = SimpleNamespace(connect=lambda *a, **k: connection(), dispose=AsyncMock())
    monkeypatch.setattr(confirm, "create_async_engine", lambda url: engine)
    monkeypatch.setattr(confirm, "wake_input",
                        AsyncMock(return_value={"agenda": "generated"}))
    judged = AsyncMock(return_value="match")
    monkeypatch.setattr(confirm, "judge", judged)
    inputs = [{"room": "room_generated", "wake": 9, "scenario": 2},
              {"room": "room_generated", "wake": 9, "scenario": 3},
              {"room": "room_generated", "wake": 10, "scenario": 6}]
    out = await confirm.confirm("postgresql://localhost/generated_eval",
                                tmp_path / "config.toml", inputs,
                                {"generated-real": "room_generated"})
    assert len(restored) == 2 and judged.await_count == 3
    assert [r["result"] for r in out] == ["match", "match", "match"]


def _profile_row(**over):
    """One `profile_line` row as the select returns it: every column
    `row_to_line` reads, generated values only."""
    from types import SimpleNamespace

    fields = {
        "id": "generated-line", "profile_key": "generated-account",
        "part": "facts", "kind": "fact", "text": "generated", "mode": "told",
        "citations": (), "state": "current", "unit": "generated-unit",
        "written_at": 1, "written_by_session": "generated", "draws_on": (),
        "told_by": None, "prediction": None, "prediction_observable": None,
        "prediction_window_end": None, "verdict": None, "verdict_by": None,
        "verdict_at": None, "supersedes": None, "support_count": 1,
        "last_supported_at": None, "exclude_persons": (), "as_of": None,
        "fact_key": None, "fact_value": None, "valid_from": None,
        "valid_until": None, "content": {}, "location": False,
        "source_medium": (), "strength": {},
    }
    fields.update(over)
    return SimpleNamespace(**fields, _mapping=fields)


def _conn_with_profile_rows(profile_rows):
    """A conn answering `memory_state`'s three reads: the two text queries by
    `.mappings()`, the `profile_line` select by `.all()`."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def execute(query, params=None):
        sql = str(query)
        if "FROM note n" in sql:
            assert "r.id = n.head_revision_id" in sql and "retired_at IS NULL" in sql
            return SimpleNamespace(mappings=lambda: [])
        if "FROM events" in sql:
            assert set(params["open_statuses"]) == {"staged", "proposed", "confirmed"}
            return SimpleNamespace(mappings=lambda: [])
        if "profile_line" in sql:
            return SimpleNamespace(all=lambda: profile_rows)
        raise AssertionError("unexpected private read")
    return SimpleNamespace(execute=AsyncMock(side_effect=execute))


async def test_memory_state_reads_open_plans_and_current_note_body_in_unit():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def execute(query, params=None):
        sql = str(query)
        if "FROM note n" in sql:
            assert "r.id = n.head_revision_id" in sql and "retired_at IS NULL" in sql
            rows = [{"body": "generated current open note"}]
            return SimpleNamespace(mappings=lambda: rows)
        if "FROM events" in sql:
            assert "status = ANY(:open_statuses)" in sql
            assert set(params["open_statuses"]) == {"staged", "proposed", "confirmed"}
            rows = [{"title": "generated agreed dinner", "status": "confirmed"}]
            return SimpleNamespace(mappings=lambda: rows)
        if "profile_line" in sql:
            rows = [_profile_row(text="generated relevant profile line")]
            return SimpleNamespace(all=lambda: rows)
        raise AssertionError("unexpected private read")
    result = await confirm.memory_state(SimpleNamespace(execute=AsyncMock(side_effect=execute)),
                                        "generated-unit")
    assert result["open_events"][0]["status"] == "confirmed"
    assert result["open_notes"][0]["body"] == "generated current open note"
    assert result["profile_lines"][0]["text"] == "generated relevant profile line"


async def test_memory_state_reduces_profile_lines_to_chain_heads_before_state():
    """`profile_line` is append-only: a withdrawal inserts a successor and
    leaves the predecessor's `state` untouched. A line withdrawn BEFORE the
    wake is superseded in the restored sandbox and must be absent even though
    its row still reads `current`; a line never superseded stands, and a
    successor head is filtered by ITS own state."""
    rows = [
        # Withdrawn before the wake: `current` on its face, superseded by a
        # retired successor. Raw-state filtering would still show it.
        _profile_row(id="generated-withdrawn", state="current",
                     text="generated withdrawn preference"),
        _profile_row(id="generated-withdrawal", state="retired",
                     supersedes="generated-withdrawn", text="generated withdrawal"),
        # Replaced before the wake: same shape, the successor standing.
        _profile_row(id="generated-old", state="current", text="generated old fact"),
        _profile_row(id="generated-new", state="disputed",
                     supersedes="generated-old", text="generated new fact"),
        # Never superseded: a current head and a disputed head, both shown.
        _profile_row(id="generated-standing", state="current",
                     text="generated standing preference"),
        _profile_row(id="generated-contested", state="disputed",
                     text="generated contested line"),
    ]
    result = await confirm.memory_state(_conn_with_profile_rows(rows), "generated-unit")
    texts = {row["text"] for row in result["profile_lines"]}
    assert "generated withdrawn preference" not in texts
    assert "generated withdrawal" not in texts
    assert "generated old fact" not in texts
    assert texts == {"generated standing preference", "generated contested line",
                     "generated new fact"}
