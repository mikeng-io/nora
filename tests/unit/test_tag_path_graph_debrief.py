"""The tag path runs the graph stage with the conversation slot held."""

from dataclasses import replace

import pytest

from nora.engagement.stages import debrief
from tests.unit.test_engagement_pipeline import (
    ScriptedRunner,
    ScriptedTurn,
    _build_pipeline,
    _drain,
    _held,
    _msg,
    _patch_debrief,
    _reply_turns,
    _request,
    _wiring,
)


@pytest.mark.parametrize("silent", [False, True], ids=["reply", "silence"])
async def test_the_tag_debrief_runs_once_after_reply_settlement_inside_the_slot(
    monkeypatch, silent,
):
    calls = []

    async def record(wiring, authority, message, transcript):
        calls.append(("debrief", _held(pipeline), authority.pass_context.pass_id))

    async def retry(wiring, unit, platform, room):
        calls.append(("retry", _held(pipeline), unit))

    async def legacy(**kwargs):
        calls.append(("legacy",))

    monkeypatch.setattr(debrief, "run", legacy)
    _patch_debrief(monkeypatch, record, retry=retry)
    turns = [ScriptedTurn(final_text="")] if silent else _reply_turns()
    pipeline, _, _, _, background = _build_pipeline(
        runner=ScriptedRunner(turns), debrief_wiring=_wiring(),
    )
    await pipeline.run(_request(replace(_msg(), admitted_unit="fixture-unit")))
    await _drain(background)

    assert calls == [("retry", True, "fixture-unit"), ("debrief", True, "tag-1")]
    assert not _held(pipeline)


async def test_a_command_slot_retries_before_its_command(monkeypatch):
    calls = []

    async def retry(wiring, unit, platform, room):
        calls.append(("retry", _held(pipeline), unit))

    async def command(*args, **kwargs):
        calls.append(("command", _held(pipeline), "fixture-unit"))

    _patch_debrief(monkeypatch, None, retry=retry)
    pipeline, _, _, _, background = _build_pipeline(debrief_wiring=_wiring())
    monkeypatch.setattr(pipeline, "_run_room_command", command)
    request = _request(replace(_msg(), admitted_unit="fixture-unit"))
    await pipeline.run(replace(request, occasion="room_command"))
    await _drain(background)

    assert calls == [("retry", True, "fixture-unit"), ("command", True, "fixture-unit")]


async def test_a_started_pass_debriefs_when_no_trace_can_be_attributed(monkeypatch):
    from nora.engagement.stages import invoke

    passes = []

    async def invocation_fault(**kwargs):
        raise RuntimeError("Generated invocation fault before a trace returned")

    async def record(wiring, authority, message, transcript):
        passes.append(authority.pass_context.pass_id)

    monkeypatch.setattr(invoke, "run", invocation_fault)
    _patch_debrief(monkeypatch, record)
    pipeline, _, _, _, background = _build_pipeline(debrief_wiring=_wiring())
    await pipeline.run(_request())
    await _drain(background)

    assert passes == ["tag-1"]
    assert not pipeline._tag_trace_store.saved
    assert not _held(pipeline)
