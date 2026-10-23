"""The job checkpoint: a typed place-keeper the worker writes, and the
hard facts code keeps outside it.

Two guards are load-bearing: OUTPUTS COME FROM CODE (`SubagentProgress` records
a document id only from an `ok` `document_publish` result, and the renderer's
Outputs section is built from those alone, so a checkpoint that names a document
nobody published yields no output), and the RE-ANCHORING
(`SubagentProgress._anchor`, which puts the latest checkpoint and outputs back into the pinned
first message once the sliding window has trimmed).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from nora.agent import factory
from nora.agent.subagent_desk import SubagentDesk
from nora.agent.subagent_progress import ANCHOR_MARK, SubagentProgress
from nora.app.compute_poller import ComputeJobPoller
from nora.app.subagent_backend import SubagentBackend
from nora.domain.checkpoint import (
    MAX_ITEM_CHARS,
    MAX_ITEMS,
    Checkpoint,
    Outputs,
    parse_lists,
    read_row,
    row_payload,
)
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.ports import JobResult, JobStatus
from nora.domain.subagent import SUBAGENT_KIND
from nora.domain.subagents_context import SubagentLine, SubagentsView
from nora.language.checkpoint_render import render_checkpoint
from nora.language.subagents_render import render_subagents
from nora.policy import permissive_policy
from nora.testing import FakeComputeJobStore
from tests.unit.test_subagent_tools import _Desk, _surface_names
from tests.unit.test_subagent_worker import (
    PLATFORM,
    ROOM,
    _doc,
    _request,
    _Rig,
    _Script,
    _until_done,
)
from tests.unit.test_subagents_block import _Documents

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
FAKE_DOC = "00000000-0000-4000-8000-000000000000"
LISTS = {
    "done": ["read the 8 sheets"], "doing": ["publishing sheet 3"],
    "next": ["sheets 4 to 8", "read each back"], "decisions": ["kept the layout"],
}


def _cp(**over: Any) -> Checkpoint:
    return Checkpoint(**{**{k: tuple(v) for k, v in LISTS.items()}, "at": NOW, **over})


# -- the tool: validates, replaces -----------------------------------------------------------


async def test_checkpoint_update_validates_and_replaces(tmp_path):
    rig, progress = _Rig(tmp_path), SubagentProgress()
    tool = rig.specs(job="j1", checkpoint_sink=progress)["checkpoint_update"]
    assert (await tool(**LISTS))["status"] == "ok"
    assert progress.checkpoint.doing == ("publishing sheet 3",)
    # a second call REPLACES: nothing of the first survives, there is no merge
    again = {"done": ["x"], "doing": [], "next": [], "decisions": []}
    assert (await tool(**again))["status"] == "ok"
    assert (progress.checkpoint.done, progress.checkpoint.doing, progress.checkpoint.next,
            progress.checkpoint.decisions) == (("x",), (), (), ())
    # a refused call changes nothing
    too_many = {**again, "next": [f"step {i}" for i in range(MAX_ITEMS + 1)]}
    refused = await tool(**too_many)
    assert refused["status"] == "bad_arguments" and "`next`" in refused["note"]
    long = await tool(**{**again, "done": ["y" * (MAX_ITEM_CHARS + 1)]})
    assert long["status"] == "bad_arguments"
    bare = await tool(**{**again, "done": "one string"})
    assert bare["status"] == "bad_arguments"
    assert progress.checkpoint.done == ("x",)
    # the caps are inclusive
    ok = await tool(**{**again, "next": [f"s{i}" for i in range(MAX_ITEMS)],
                       "done": ["y" * MAX_ITEM_CHARS]})
    assert ok["status"] == "ok"


async def test_a_checkpoint_update_outside_a_worker_is_unavailable_and_saves_nothing(tmp_path):
    rig, progress = _Rig(tmp_path), SubagentProgress()
    out = await rig.specs(checkpoint_sink=progress)["checkpoint_update"](**LISTS)
    assert out["status"] == "unavailable" and progress.checkpoint is None
    out = await rig.specs(job="j1")["checkpoint_update"](**LISTS)       # no sink
    assert out["status"] == "unavailable"


def test_parse_lists_drops_blanks_and_collapses_lines():
    lists, problem = parse_lists(done=["a\n  b", "  ", ""], doing=[], next=[], decisions=[])
    assert problem == "" and lists["done"] == ("a b",)


# -- the tool is the worker's alone ----------------------------------------------------------


def test_the_tool_is_on_the_workers_list_and_on_no_other_surface(tmp_path):
    rig = _Rig(tmp_path)
    held = {t.tool_name for t in rig.source.build(
        job_id="j1", platform=PLATFORM, conversation_id=ROOM, conversation_type="group",
        requester_person_id="miles", checkpoint_sink=SubagentProgress(),
    )}
    assert "checkpoint_update" in held
    desk = _Desk()
    assert "subagent_create" in _surface_names(desk.registry)      # the control: a tag surface
    for kw in ({}, {"fire_turn": True}, {"dream_adaptor": "proactive"},
               {"dream_adaptor": "cognition"}):
        assert "checkpoint_update" not in _surface_names(desk.registry, **kw), kw
    assert "checkpoint_update" in _surface_names(desk.registry, subagent_job="j1")


# -- persisted on the row ---------------------------------------------------------------------


async def test_the_checkpoint_is_persisted_on_the_row_and_in_memory(tmp_path):
    rig = _Rig(tmp_path)
    store = FakeComputeJobStore()
    model = _Script([
        ("tool", "checkpoint_update", LISTS),
        ("tool", "computer_write", {"path": "out.txt", "content": "hi"}),
        ("tool", "computer_export", {"path": "out.txt", "to": "keep"}),
        ("text", "Done: all of it."),
    ])
    backend = SubagentBackend(
        runner=rig.runner(model), metrics=rig.metrics, clock=lambda: NOW,
        requester_name=lambda _p: "Miles", jobs=store,
    )
    job = await backend.launch(request=_request())
    # the row is written after launch returns, exactly as the desk does
    row_id = uuid4()
    await store.create(_row(row_id, job))
    result = await _until_done(backend, job)
    assert result.status is JobStatus.SUCCEEDED
    row = await store.get(row_id)
    assert row.checkpoint["doing"] == ["publishing sheet 3"]
    assert row.checkpoint["kept_files"] == ["out.txt"]        # recorded by code, from a result
    assert row.checkpoint_at is not None
    status = backend.status(job)
    assert status.checkpoint.next == ("sheets 4 to 8", "read each back")
    assert status.kept_files == ("out.txt",)
    # and the delivery's result carries the same, from memory
    assert result.checkpoint["done"] == ["read the 8 sheets"]


def _row(row_id, backend_job_id, **over: Any) -> ComputeJob:
    base: dict[str, Any] = {
        "id": row_id, "backend_job_id": backend_job_id, "task": "merge", "artifact_id": None,
        "platform": PLATFORM, "conversation_id": ROOM, "conversation_type": "group",
        "requester_person_id": "miles", "created_at": NOW - timedelta(minutes=5),
        "state": ComputeJobState.PENDING, "completed_at": None, "result_url": None,
        "result_summary": None, "error": None, "task_sha256": "a", "context_sha256": "b",
        "kind": SUBAGENT_KIND, "payload": {"title": "Sheet merge"},
    }
    base.update(over)
    return ComputeJob(**base)


async def test_an_update_before_the_row_exists_stays_in_memory_and_the_next_one_lands(tmp_path):
    store = FakeComputeJobStore()
    progress = SubagentProgress(persist=lambda p, at: store.set_checkpoint("bj", p, at))
    await progress.update_checkpoint(_cp())                 # no row yet: nothing to write to
    assert progress.checkpoint is not None
    row = _row(uuid4(), "bj")
    await store.create(row)
    await progress.update_checkpoint(_cp(doing=("later",)))
    assert (await store.get(row.id)).checkpoint["doing"] == ["later"]


async def test_a_persist_that_raises_is_logged_and_never_fails_the_update():
    async def boom(_p, _at):
        raise RuntimeError("db down")

    progress = SubagentProgress(persist=boom)
    await progress.update_checkpoint(_cp())
    assert progress.checkpoint is not None


# -- outputs come from code -------------------------------------------------------------------


async def test_a_checkpoint_that_names_a_fake_document_yields_no_output(tmp_path):
    """The load-bearing one. The worker's words claim a document; code recorded none."""
    rig = _Rig(tmp_path)
    claim = {**LISTS, "done": [f"published document {FAKE_DOC}"]}
    backend = rig.backend(_Script([
        ("tool", "checkpoint_update", claim), ("text", "Done."),
    ]))
    job = await backend.launch(request=_request())
    result = await _until_done(backend, job)
    assert result.document_ids == ()
    status = backend.status(job)
    assert status.checkpoint is not None and FAKE_DOC in status.checkpoint.done[0]
    assert status.document_ids == ()
    outputs = Outputs(documents=tuple((d, "") for d in status.document_ids))
    text = render_checkpoint(status.checkpoint, outputs, title="t", now=NOW, mode="read")
    assert FAKE_DOC in text                       # the worker's own words are still shown
    assert "**Outputs**" not in text              # and nothing is recorded as made


async def test_a_real_publish_is_the_only_output_and_a_refused_one_adds_none(tmp_path):
    rig = _Rig(tmp_path)
    claim = {**LISTS, "done": [f"also made {FAKE_DOC}"]}
    backend = rig.backend(_Script([
        ("tool", "document_publish", {"spec": _doc()}),
        ("tool", "document_publish", {"spec": {"version": 1}}),     # refused
        ("tool", "checkpoint_update", claim),
        ("text", "Done."),
    ]))
    job = await backend.launch(request=_request())
    await _until_done(backend, job)
    status = backend.status(job)
    assert len(status.document_ids) == 1 and FAKE_DOC not in status.document_ids
    text = render_checkpoint(
        status.checkpoint, Outputs(documents=((status.document_ids[0], "Sheet 1"),)),
        title="t", now=NOW, mode="read",
    )
    assert f"document Sheet 1 (id {status.document_ids[0]})" in text
    outputs_section = text.split("**Outputs**", 1)[1]
    assert FAKE_DOC not in outputs_section


async def test_a_conflict_result_that_carries_a_document_id_is_not_an_output():
    """`document_publish` answers a conflict with `status: bad_arguments` AND a `document_id`:
    the id exists, the call did not succeed. Only `ok` is recorded."""
    progress = SubagentProgress()
    event = _Event(
        "document_publish", {"status": "bad_arguments", "document_id": FAKE_DOC},
    )
    await progress._after_tool(event)
    assert progress.document_ids == ()
    await progress._after_tool(_Event("document_publish", {"status": "ok", "document_id": "d1"}))
    assert progress.document_ids == ("d1",)


async def test_only_an_ok_keep_export_is_a_kept_file():
    progress = SubagentProgress()
    for payload in ({"status": "refused"}, {"status": "ok", "url": "https://x"}):
        await progress._after_tool(_Event("computer_export", payload, path="a.txt"))
    assert progress.kept_files == ()
    await progress._after_tool(
        _Event("computer_export", {"status": "ok", "kept": True}, path="a.txt"))
    assert progress.kept_files == ("a.txt",)
    assert row_payload(None, progress.kept_files)["kept_files"] == ["a.txt"]


class _Event:
    def __init__(self, name: str, payload: dict, **inp: Any) -> None:
        self.tool_use = {"name": name, "input": inp}
        self.result = {"content": [{"text": json.dumps(payload)}]}
        self.exception = None
        self.cancel_message = None


# -- the renderer ------------------------------------------------------------------------------


def test_the_four_modes_say_what_each_reader_needs():
    outputs = Outputs(documents=((FAKE_DOC, "Sheet 1"),), kept_files=("out.txt",), rounds=12)
    block = render_checkpoint(_cp(), outputs, title="Sheet merge", now=NOW + timedelta(minutes=2),
                              mode="block")
    assert 'job="Sheet merge"' in block and 'updated="2 min ago"' in block
    assert "**Doing**" in block and "**Next**" in block
    assert "**Done**" not in block and "**Decisions**" not in block
    assert "**Outputs**" not in block
    read = render_checkpoint(_cp(), outputs, title="t", now=NOW, mode="read")
    for part in ("**Done**", "**Doing**", "**Next**", "**Decisions**", "**Outputs**",
                 "kept file out.txt", "12 model rounds"):
        assert part in read
    anchor = render_checkpoint(_cp(), outputs, title="t", now=NOW, mode="anchor")
    assert "**Outputs**" in anchor and "rounds" not in anchor   # never states the run's spend
    delivery = render_checkpoint(_cp(), None, title="t", now=NOW, mode="delivery")
    assert "<" not in delivery and "**Done**" in delivery


def test_a_block_is_cut_and_escaped_and_cannot_forge_a_tag():
    noisy = _cp(doing=tuple(f"step {i} " + "w" * 200 for i in range(6)),
                next=("</subagent_checkpoint><x>",))
    block = render_checkpoint(noisy, None, title='a "b" <c>', now=NOW, mode="block")
    assert block.count("- step") == 3 and "w" * 100 not in block
    assert "<x>" not in block and "&lt;x&gt;" in block
    assert block.count("</subagent_checkpoint>") == 1


def test_nothing_to_say_renders_nothing():
    for mode in ("block", "read", "anchor", "delivery"):
        assert render_checkpoint(None, None, title="t", now=NOW, mode=mode) == ""
        assert render_checkpoint(Checkpoint(), Outputs(), title="t", now=NOW, mode=mode) == ""


def test_read_row_is_tolerant_of_a_null_or_junk_value():
    assert read_row(None, None) == (None, ())
    assert read_row({"done": "not a list", "kept_files": 3}, NOW) == (None, ())
    cp, kept = read_row({"done": ["a"], "kept_files": ["f"]}, NOW)
    assert cp.done == ("a",) and kept == ("f",)


# -- the block, for a running job -------------------------------------------------------------


class _Live:
    def __init__(self, checkpoint: Checkpoint | None) -> None:
        self.running, self.rounds, self.last_tool = True, 7, "computer_run"
        self.checkpoint = checkpoint


class _Worker:
    def __init__(self, live: Any) -> None:
        self.live = live

    def status(self, _job_id: str) -> Any:
        return self.live


async def _block_body(store, worker=None) -> str:
    from nora.engagement.subagents_context import SubagentsContextReader
    from nora.obs.metrics import Metrics

    reader = SubagentsContextReader(
        jobs=store, clock=lambda: NOW + timedelta(minutes=2), metrics=Metrics(),
    )
    if worker is not None:
        reader.attach(worker)
    view = await reader.read(platform=PLATFORM, conversation_id=ROOM, person_id="ken")
    block = render_subagents(view)
    return "" if block is None else block.body


async def test_the_block_shows_doing_and_next_for_a_running_job_from_memory_or_the_row():
    store = FakeComputeJobStore()
    row = _row(uuid4(), "bj")
    await store.create(row)
    assert "subagent_checkpoint" not in await _block_body(store, _Worker(_Live(None)))
    memory = await _block_body(store, _Worker(_Live(_cp())))
    assert "running" in memory and "publishing sheet 3" in memory and "sheets 4 to 8" in memory
    assert "read the 8 sheets" not in memory and "kept the layout" not in memory
    # the row's copy is used when the worker holds none (progress known: false) ...
    await store.set_checkpoint("bj", row_payload(_cp(doing=("from the row",)), ()), NOW)
    from_row = await _block_body(store)
    assert "from the row" in from_row and "progress unknown" in from_row
    # ... and memory wins when both exist
    both = await _block_body(store, _Worker(_Live(_cp(doing=("from memory",)))))
    assert "from memory" in both and "from the row" not in both


async def test_a_finished_job_in_the_block_shows_no_checkpoint():
    store = FakeComputeJobStore()
    await store.create(_row(
        uuid4(), "bj", state=ComputeJobState.DONE, completed_at=NOW,
        result_report="all done", checkpoint=row_payload(_cp(), ()), checkpoint_at=NOW,
    ))
    body = await _block_body(store)
    assert "all done" in body and "subagent_checkpoint" not in body


def test_a_running_line_without_a_checkpoint_is_unchanged():
    line = SubagentLine(id="1", state="running", title="T", objective="o", created_at=NOW,
                        progress_known=True, rounds=3, last_tool="x")
    body = render_subagents(SubagentsView(lines=(line,), now=NOW)).body
    assert "subagent_checkpoint" not in body and "3 rounds used" in body


# -- subagent_read: full ----------------------------------------------------------------------


async def test_subagent_read_shows_the_full_checkpoint_with_outputs_from_code():
    rig = _Desk(documents=_Documents(creator="miles", title="Sheet 1"))
    row = _row(uuid4(), "bj1", checkpoint=row_payload(_cp(), ("out.txt",)), checkpoint_at=NOW,
               result_document_ids=(FAKE_DOC,))
    await rig.store.create(row)
    rig.worker.live["bj1"] = None
    out = await rig.tools()["subagent_read"](id=str(row.id))
    assert out["status"] == "ok"
    text = out["checkpoint"]
    for part in ("read the 8 sheets", "publishing sheet 3", "sheets 4 to 8", "kept the layout",
                 "**Outputs**", "kept file out.txt", f"document Sheet 1 (id {FAKE_DOC})"):
        assert part in text
    # a running job's read uses memory, and says its rounds
    rig.worker.live["bj1"] = _Live(_cp(doing=("fresh",)))
    rig.worker.live["bj1"].kept_files = ()
    rig.worker.live["bj1"].document_ids = ()
    live = await rig.tools()["subagent_read"](id=str(row.id))
    assert "fresh" in live["checkpoint"] and "7 model rounds" in live["checkpoint"]


async def test_a_job_with_no_checkpoint_reads_and_lists_as_before():
    rig = _Desk()
    row = _row(uuid4(), "bj1")
    await rig.store.create(row)
    out = await rig.tools()["subagent_read"](id=str(row.id))
    assert out["status"] == "ok" and "checkpoint" not in out
    listed = await rig.tools()["subagent_list"]()
    assert all("checkpoint" not in j for j in listed["jobs"])
    assert isinstance(SubagentDesk, type)


# -- compaction: the window trims and the anchor comes back ---------------------------------------


class _Recording(_Script):
    """A scripted model that records the messages each call was handed."""

    def __init__(self, script):
        super().__init__(script)
        self.seen: list[list[dict]] = []

    async def stream(self, messages, *args: Any, **kwargs: Any):
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        async for event in super().stream(messages, *args, **kwargs):
            yield event


def _first_texts(call: list[dict]) -> list[str]:
    return [b["text"] for b in call[0]["content"] if isinstance(b, dict) and "text" in b]


async def test_the_anchor_is_written_after_the_window_trims_and_not_before(tmp_path):
    rig = _Rig(tmp_path)
    early = {**LISTS, "doing": ["FIRST-CHECKPOINT"]}
    late = {**LISTS, "doing": ["LATEST-CHECKPOINT"]}
    steps = [("tool", "checkpoint_update", early)]
    steps += [("tool", "computer_run", {"command": f"echo {i}"}) for i in range(30)]
    steps += [("tool", "document_publish", {"spec": _doc()}),
              ("tool", "checkpoint_update", late)]
    steps += [("tool", "computer_run", {"command": "echo more"}) for _ in range(4)]
    steps += [("text", "Done.")]
    model = _Recording(steps)
    backend = rig.backend(model, turns=80)
    job = await backend.launch(request=_request())
    result = await _until_done(backend, job)
    assert result.status is JobStatus.SUCCEEDED
    assert max(len(c) for c in model.seen) <= factory.SUBAGENT_WINDOW_MESSAGES + 2
    near = factory.SUBAGENT_WINDOW_MESSAGES - 4
    first = next(i for i, c in enumerate(model.seen) if len(c) > near)
    # nothing is anchored while the whole history is still in the window
    assert all(not any(t.startswith(ANCHOR_MARK) for t in _first_texts(c))
               for c in model.seen[:first])
    last = model.seen[-1]
    texts = _first_texts(last)
    assert texts[0].startswith("<") or "do the thing" in "".join(texts)     # the brief is kept
    anchor = next(t for t in texts if t.startswith(ANCHOR_MARK))
    assert "LATEST-CHECKPOINT" in anchor and "FIRST-CHECKPOINT" not in anchor
    # and code's outputs ride with it: the document the worker really published
    assert backend.status(job).document_ids[0] in anchor
    assert "rounds" not in anchor


async def test_without_a_checkpoint_the_trimmed_worker_is_anchored_on_outputs_alone(tmp_path):
    rig = _Rig(tmp_path)
    steps = [("tool", "document_publish", {"spec": _doc()})]
    steps += [("tool", "computer_run", {"command": "true"}) for _ in range(30)]
    steps += [("text", "Done.")]
    model = _Recording(steps)
    backend = rig.backend(model, turns=80)
    job = await backend.launch(request=_request())
    await _until_done(backend, job)
    anchor = next(t for t in _first_texts(model.seen[-1]) if t.startswith(ANCHOR_MARK))
    assert backend.status(job).document_ids[0] in anchor and "**Done**" not in anchor


# -- delivery ----------------------------------------------------------------------------------


async def _deliver(result: JobResult, row: ComputeJob):
    from nora.engagement.slots import ConversationSlots
    from nora.obs.metrics import Metrics

    spoken: list[Any] = []

    class _Backend:
        async def poll(self, *, job_id: str) -> JobResult:
            return result

    async def speak(event):
        spoken.append(event)
        return "sent"

    store = FakeComputeJobStore()
    await store.create(row)
    poller = ComputeJobPoller(
        policy=permissive_policy(),
        store=store, backends={SUBAGENT_KIND: _Backend()}, speak=speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda c: None), metrics=Metrics(),
        tick_seconds=1.0, clock=lambda: NOW,
    )
    await poller.tick()
    return spoken[0].body


async def test_a_job_that_stopped_is_delivered_with_where_it_had_got_to():
    payload = row_payload(_cp(), ("out.txt",))
    body = await _deliver(JobResult(
        status=JobStatus.FAILED, error="it ran out of rounds before it finished",
        failure_reason="limit_turns", checkpoint=payload,
    ), _row(uuid4(), "bj"))
    assert "It failed: it ran out of rounds" in body
    assert "What it had recorded when it stopped" in body and "sheets 4 to 8" in body
    assert "Files it kept in the workspace: out.txt" in body


async def test_a_finished_job_is_delivered_with_its_report_and_no_second_copy():
    body = await _deliver(JobResult(
        status=JobStatus.SUCCEEDED, report="Done: 8 of 8.\nNot done: none.\nDecisions: none.",
        checkpoint=row_payload(_cp(), ("out.txt",)),
    ), _row(uuid4(), "bj"))
    assert "Done: 8 of 8." in body and "Files it kept in the workspace: out.txt" in body
    assert "What it had recorded" not in body and "sheets 4 to 8" not in body


async def test_a_job_with_no_checkpoint_is_delivered_as_before():
    body = await _deliver(JobResult(status=JobStatus.FAILED, error="boom",
                                    failure_reason="error"), _row(uuid4(), "bj"))
    assert body.endswith("It failed: boom")


def test_the_constants_the_doctrine_leans_on_are_the_ones_the_tool_enforces():
    assert (MAX_ITEMS, MAX_ITEM_CHARS) == (8, 160)
    assert factory.SUBAGENT_WINDOW_MESSAGES == 40
    with pytest.raises(TypeError):
        Checkpoint(kept_files=())            # the worker's value has no field for code's files


# -- the terminal write, the pinned message on the wire, the lead ----------------


async def test_a_checkpoint_written_before_the_row_exists_is_on_the_row_after_delivery(tmp_path):
    rig = _Rig(tmp_path)
    store = FakeComputeJobStore()
    backend = SubagentBackend(
        runner=rig.runner(_Script([("tool", "checkpoint_update", LISTS), ("text", "Done.")])),
        metrics=rig.metrics, clock=lambda: NOW, requester_name=lambda _p: "Miles", jobs=store,
    )
    job = await backend.launch(request=_request())
    # let the worker run to the end BEFORE the row is written: its only update finds no row
    await backend._jobs[job].task
    row = _row(uuid4(), job)
    await store.create(row)
    assert (await store.get(row.id)).checkpoint is None
    assert rig.metrics.get("nora_subagent_checkpoint_unwritten_total") == 1
    from nora.engagement.slots import ConversationSlots

    async def speak(_event):
        return "sent"

    poller = ComputeJobPoller(
        policy=permissive_policy(),
        store=store, backends={SUBAGENT_KIND: backend}, speak=speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda c: None), metrics=rig.metrics,
        tick_seconds=1.0, clock=lambda: NOW,
    )
    await poller.tick()
    done = await store.get(row.id)
    assert done.state is ComputeJobState.DONE
    assert done.checkpoint["doing"] == ["publishing sheet 3"] and done.checkpoint_at is not None


def test_the_pinned_and_anchored_first_message_reaches_the_api_with_only_api_fields():
    from nora.agent.subagent_progress import ANCHOR_LEAD

    model = factory.create_model_provider(
        model="m", max_tokens=100, temperature=0.0,
        client=factory.create_client(base_url="http://localhost:1/v1", api_key="k"),
    )
    messages = [{"role": "user", "content": [{"text": "brief"}, {"text": ANCHOR_LEAD + "x"}],
                 "metadata": {"custom": {"pinned": True}}},
                {"role": "assistant", "content": [{"text": "ok"}],
                 "metadata": {"usage": {"inputTokens": 1, "outputTokens": 1}}}]
    sent = model.format_request_messages(messages, "system")
    assert {k for m in sent for k in m} <= {"role", "content", "tool_calls", "tool_call_id"}
    assert "pinned" not in json.dumps(sent) and "metadata" not in json.dumps(sent)


async def test_the_lead_is_written_after_a_trim_even_with_nothing_recorded(tmp_path):
    rig = _Rig(tmp_path)
    steps = [("tool", "computer_run", {"command": "true"}) for _ in range(30)] + [("text", "ok")]
    model = _Recording(steps)
    backend = rig.backend(model, turns=80)
    await _until_done(backend, await backend.launch(request=_request()))
    texts = _first_texts(model.seen[-1])
    assert any(t.startswith(ANCHOR_MARK) for t in texts)


def test_a_refused_list_note_carries_no_numbers():
    _, problem = parse_lists(done=["x"] * 9, doing=[], next=[], decisions=[])
    assert not any(ch.isdigit() for ch in problem)
    _, problem = parse_lists(done=["x" * 999], doing=[], next=[], decisions=[])
    assert not any(ch.isdigit() for ch in problem)
