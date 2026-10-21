"""The `<computer>` block: a deterministic
window, rebuilt each turn from the machine and the audit, never a fold.

Pinned: absent while `[computer]` is off and for a room not using the
computer (and that room costs no VM read); each section's cap with a «+N
more» line; the token ceiling drops the OLDEST lines first and says how many;
an age on every line; a lapsed task shows within its window and not after;
the block says what it is and that the computer is its source (ADR 0013).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import tiktoken

from nora.agent.prefill import build_prefill
from nora.domain.computer import StateFile, TmuxSession, computer_unit_scope
from nora.domain.computer_context import (
    ComputerContextCaps,
    ComputerView,
    RunLine,
    SessionLine,
)
from nora.domain.computer_events import ComputerEvent
from nora.domain.computer_sessions import ComputerSession, SessionProgress
from nora.domain.models import NormalizedMessage, TagContext
from nora.engagement.computer_context import ComputerContextReader
from nora.language.computer_render import estimate_tokens, render_computer
from nora.obs.metrics import Metrics
from nora.testing import FakeComputerEventStore, FakeComputerSessionStore, FakeSandboxRuntime

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
ROOM = "g-block"
SCOPE = computer_unit_scope(ROOM)
AGE = re.compile(r"(\d+ (min|h|d)( \d+ min)? ago|just now)")


def _session(i: int, *, status="open", minutes_ago=0, objective=None) -> ComputerSession:
    at = NOW - timedelta(minutes=minutes_ago)
    return ComputerSession(
        id=uuid.uuid4(), platform="signal", conversation_id=ROOM, scope=SCOPE,
        objective=objective if objective is not None else f"task number {i}",
        status=status, created_at=at - timedelta(hours=1), updated_at=at,
        last_activity_at=at, actor="tag", created_by_tag="t",
        expired_at=at if status == "expired" else None,
        expired_reason="idle" if status == "expired" else None,
    )


def _progress() -> SessionProgress:
    return SessionProgress(runs=3, last_command="python fit.py --all", last_exit_code=0,
                           files=("projects/trip/prices.csv",),
                           last_activity_at=NOW - timedelta(minutes=5))


def _view(**fields) -> ComputerView:
    return ComputerView(now=NOW, caps=fields.pop("caps", ComputerContextCaps()), **fields)


def _lines(block) -> list[str]:
    return block.body.splitlines()


# -- the gate ---------------------------------------------------------------


def _ctx(computer):
    msg = NormalizedMessage(platform="signal", conversation_id=ROOM,
                            conversation_type="group", sender_id="+1", sender_name="M",
                            timestamp=1, body="hi", raw={})
    return TagContext(tag_id="t", message=msg, tagger=None, transcript=(), peer_card=(),
                      started_at=0.0, computer=computer)


def _prefill_text(ctx) -> str:
    return "\n".join(str(m["content"]) for m in build_prefill(
        ctx, display_name="Nora", context_window=40, now=NOW))


def test_no_view_renders_no_block_in_the_prompt():
    assert render_computer(None) is None
    assert "<computer" not in _prefill_text(_ctx(None))


def test_a_view_renders_the_block_in_the_prompt_saying_what_it_is():
    view = _view(open_sessions=(SessionLine(_session(1, minutes_ago=3), "active", _progress()),))
    text = _prefill_text(_ctx(view))
    assert "<computer " in text and "task number 1" in text
    block = render_computer(view)
    assert block is not None
    assert "read at the start of this turn from the computer itself" in block.description


class CountingRuntime(FakeSandboxRuntime):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.state_reads = 0

    async def state(self, *, scope):
        self.state_reads += 1
        return await super().state(scope=scope)


def _reader(runtime, sessions, events, caps=None):
    return ComputerContextReader(
        unit_for=lambda platform, cid: cid,
        sessions=sessions, events=events, runtime=runtime,
        caps=caps or ComputerContextCaps(), clock=lambda: NOW, metrics=Metrics(),
    )


def _run_rows(command: str, at: datetime, exit_code: int | None, *, actor="tag"):
    op = uuid.uuid4()
    common = {"operation_id": op, "actor": actor, "requester_person_id": None,
              "platform": "signal", "conversation_id": ROOM, "conversation_type": "group",
              "tag_id": "t", "scope": SCOPE, "tool": "computer_run", "path": "direct",
              "trace_id": "", "span_id": ""}
    rows = [ComputerEvent(id=uuid.uuid4(), phase="started", created_at=at, started_at=at,
                          finished_at=None, status="started",
                          request={"command": command}, **common)]
    if exit_code is not None:
        rows.append(ComputerEvent(id=uuid.uuid4(), phase="finished", created_at=at,
                                  started_at=at, finished_at=at, status="ok",
                                  result={"exit_code": exit_code}, **common))
    return rows


async def test_an_inactive_room_renders_nothing_and_costs_no_vm_read():
    runtime = CountingRuntime(clock=lambda: NOW)
    await runtime.write_text(scope=SCOPE, path="old.txt", content="x")
    events = FakeComputerEventStore()
    for row in _run_rows("ls", NOW - timedelta(days=8), 0):  # outside active_days
        await events.append(row)
    view = await _reader(runtime, FakeComputerSessionStore(), events).read(
        platform="signal", conversation_id=ROOM)
    assert view is None and runtime.state_reads == 0


async def test_an_active_room_renders_recent_files_and_runs_with_ages():
    runtime = FakeSandboxRuntime(clock=lambda: NOW - timedelta(minutes=12))
    await runtime.write_text(scope=SCOPE, path="projects/trip/prices.csv", content="1,2")
    events = FakeComputerEventStore()
    for row in _run_rows("python fit.py", NOW - timedelta(minutes=5), 2):
        await events.append(row)
    view = await _reader(runtime, FakeComputerSessionStore(), events).read(
        platform="signal", conversation_id=ROOM)
    block = render_computer(view)
    assert block is not None
    assert "file projects/trip/prices.csv · 3 B · changed 12 min ago" in block.body
    assert "ran `python fit.py` · exit 2 · 5 min ago" in block.body


async def test_files_older_than_the_recent_window_are_left_out():
    runtime = FakeSandboxRuntime(clock=lambda: NOW - timedelta(days=8))
    await runtime.write_text(scope=SCOPE, path="stale.txt", content="x")
    events = FakeComputerEventStore()
    for row in _run_rows("ls", NOW - timedelta(days=1), 0):
        await events.append(row)
    view = await _reader(runtime, FakeComputerSessionStore(), events).read(
        platform="signal", conversation_id=ROOM)
    assert view is not None and view.files == ()


async def test_a_lapsed_task_shows_within_its_window_and_is_gone_after():
    sessions = FakeComputerSessionStore()
    s, _ = await sessions.continue_or_open(
        platform="signal", conversation_id=ROOM, scope=SCOPE, objective="book a table",
        actor="tag", tag_id="t", now=NOW - timedelta(days=3))
    await sessions.expire(s.id, seen_last_activity_at=s.last_activity_at, reason="idle",
                          now=NOW - timedelta(hours=23))
    runtime, events = FakeSandboxRuntime(), FakeComputerEventStore()
    view = await _reader(runtime, sessions, events).read(platform="signal", conversation_id=ROOM)
    block = render_computer(view)
    assert block is not None
    assert "lapsed task, stopped 23 h ago (no activity; files kept)" in block.body
    assert "book a table" in block.body
    later = ComputerContextReader(
        unit_for=lambda platform, cid: cid, sessions=sessions, events=events, runtime=runtime,
                                  caps=ComputerContextCaps(), metrics=Metrics(),
                                  clock=lambda: NOW + timedelta(hours=2))
    assert await later.read(platform="signal", conversation_id=ROOM) is None


async def test_a_machine_that_cannot_be_read_still_renders_the_database_half():
    sessions = FakeComputerSessionStore()
    await sessions.continue_or_open(platform="signal", conversation_id=ROOM, scope=SCOPE,
                                    objective="fit", actor="tag", tag_id="t", now=NOW)
    runtime = FakeSandboxRuntime()
    runtime.down = True
    view = await _reader(runtime, sessions, FakeComputerEventStore()).read(
        platform="signal", conversation_id=ROOM)
    block = render_computer(view)
    assert block is not None
    assert "open task" in block.body and "could not be read" in block.body


# -- caps ------------------------------------------------------------------


def test_each_section_is_capped_with_a_line_saying_how_many_more():
    view = _view(
        open_sessions=tuple(SessionLine(_session(i, minutes_ago=i), "active", None)
                            for i in range(7)),
        lapsed_sessions=tuple(SessionLine(_session(i, status="expired", minutes_ago=60 + i),
                                          "lapsed", None) for i in range(5)),
        tmux=tuple(TmuxSession(f"job{i}", NOW - timedelta(minutes=i), False, True)
                   for i in range(7)),
        files=tuple(StateFile(f"f{i}.txt", 10, NOW - timedelta(minutes=i)) for i in range(11)),
        runs=tuple(RunLine(f"cmd {i}", 0, True, NOW - timedelta(minutes=i)) for i in range(9)),
        caps=ComputerContextCaps(max_tokens=100_000),
    )
    body = render_computer(view).body
    assert body.count("open task,") == 5 and "+2 more open tasks" in body
    assert body.count("lapsed task,") == 3 and "+2 more lapsed tasks" in body
    assert body.count('tmux "') == 5 and "+2 more tmux sessions" in body
    assert body.count("\nfile ") + body.startswith("file ") == 8
    assert "+3 more recent files" in body
    assert body.count("ran `") == 5 and "+4 more commands" in body


def test_every_line_carries_an_age():
    view = _view(
        open_sessions=tuple(SessionLine(_session(i, minutes_ago=i * 90), "idle", _progress())
                            for i in range(7)),
        tmux=(TmuxSession("scrape", NOW - timedelta(minutes=40), False, True),),
        files=tuple(StateFile(f"f{i}.txt", 2048, NOW - timedelta(hours=i)) for i in range(10)),
        runs=tuple(RunLine("python x.py", None, False, NOW - timedelta(days=1))
                   for _ in range(7)),
        caps=ComputerContextCaps(max_tokens=150),
    )
    lines = _lines(render_computer(view))
    assert lines and all(AGE.search(line) for line in lines), lines


# -- the ceiling -------------------------------------------------------------


def test_the_ceiling_drops_the_oldest_lines_first_and_says_how_many():
    files = tuple(StateFile(f"projects/p/file_{i:02}.csv", 100, NOW - timedelta(minutes=i))
                  for i in range(8))
    runs = tuple(RunLine(f"python step_{i}.py --flag", 0, True,
                         NOW - timedelta(minutes=30 + i)) for i in range(5))
    full = render_computer(_view(files=files, runs=runs,
                                 caps=ComputerContextCaps(max_tokens=100_000)))
    capped = render_computer(_view(files=files, runs=runs,
                                   caps=ComputerContextCaps(max_tokens=120)))
    assert estimate_tokens(capped.body) <= 120 < estimate_tokens(full.body)
    last = _lines(capped)[-1]
    dropped = int(re.match(r"\+(\d+) older lines left out", last).group(1))
    assert dropped == len(_lines(full)) - (len(_lines(capped)) - 1)
    # The oldest go first: every run (30+ min old) before the newest file.
    assert "file_00.csv" in capped.body and "step_4.py" not in capped.body
    assert "the newest of them" in last and AGE.search(last)


def test_a_cjk_objective_cannot_slip_past_the_ceiling():
    long_cjk = "幫我訂枱" * 60
    view = _view(open_sessions=tuple(
        SessionLine(_session(i, minutes_ago=i, objective=long_cjk), "active", None)
        for i in range(3)), caps=ComputerContextCaps(max_tokens=200))
    body = render_computer(view).body
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(body)) <= 200


@pytest.mark.parametrize("n", [1])
def test_a_full_block_is_about_four_hundred_tokens_or_fewer(n, capsys):
    """The size of a full example (every section at its cap)."""
    view = _view(
        open_sessions=tuple(SessionLine(_session(i, minutes_ago=i * 30,
                                                 objective="scrape prices for the Osaka trip "
                                                 "and compare hotels"), "active", _progress())
                            for i in range(6)),
        lapsed_sessions=tuple(SessionLine(_session(i, status="expired", minutes_ago=300 + i),
                                          "lapsed", _progress()) for i in range(3)),
        tmux=tuple(TmuxSession(f"scrape-{i}", NOW - timedelta(minutes=40 + i), False, i == 0)
                   for i in range(5)),
        files=tuple(StateFile(f"projects/trip/prices_{i}.csv", 2100, NOW - timedelta(minutes=i))
                    for i in range(8)),
        runs=tuple(RunLine("python compare.py --city osaka --nights 3 --out report.html", 0,
                           True, NOW - timedelta(minutes=2 + i)) for i in range(5)),
    )
    block = render_computer(view)
    enc = tiktoken.get_encoding("cl100k_base")
    body_tokens, whole_tokens = len(enc.encode(block.body)), len(enc.encode(block.render()))
    print(f"computer block: body {body_tokens} tokens, whole {whole_tokens} (cl100k)")
    assert body_tokens <= 400



# -- the machine read never holds a reply ----------------------------------


class SlowRuntime(FakeSandboxRuntime):
    """A computer that answers the state read after `delay` seconds."""

    def __init__(self, delay: float, **kw) -> None:
        super().__init__(**kw)
        self.delay = delay
        self.state_reads = 0

    async def state(self, *, scope):
        self.state_reads += 1
        await asyncio.sleep(self.delay)
        return await super().state(scope=scope)


async def _open_session(sessions, objective="scrape prices"):
    await sessions.continue_or_open(platform="signal", conversation_id=ROOM, scope=SCOPE,
                                    objective=objective, actor="tag", tag_id="t",
                                    now=NOW - timedelta(hours=3))


async def test_a_slow_machine_does_not_delay_the_block_past_its_budget():
    sessions, metrics = FakeComputerSessionStore(), Metrics()
    await _open_session(sessions)
    reader = ComputerContextReader(
        unit_for=lambda platform, cid: cid,
        sessions=sessions, events=FakeComputerEventStore(), runtime=SlowRuntime(5.0),
        caps=ComputerContextCaps(read_timeout_seconds=0.2), clock=lambda: NOW,
        metrics=metrics,
    )
    started = time.monotonic()
    view = await reader.read(platform="signal", conversation_id=ROOM)
    assert time.monotonic() - started < 1.0
    block = render_computer(view)
    assert block is not None
    assert "scrape prices" in block.body  # the database half still renders
    assert "the computer itself could not be read" in block.body
    assert metrics.get("nora_computer_context_machine_unread_total", reason="timeout") == 1


async def test_an_unreadable_machine_shows_an_open_task_as_running_unknown_never_idle():
    sessions = FakeComputerSessionStore()
    await _open_session(sessions)
    runtime = FakeSandboxRuntime()
    runtime.down = True
    view = await _reader(runtime, sessions, FakeComputerEventStore()).read(
        platform="signal", conversation_id=ROOM)
    body = render_computer(view).body
    assert "open task, running unknown" in body and "idle" not in body


async def test_a_good_machine_read_is_kept_for_the_cache_window():
    sessions = FakeComputerSessionStore()
    await _open_session(sessions)
    runtime, tick = SlowRuntime(0.0), [0.0]
    reader = ComputerContextReader(
        unit_for=lambda platform, cid: cid,
        sessions=sessions, events=FakeComputerEventStore(), runtime=runtime,
        caps=ComputerContextCaps(state_cache_seconds=30), clock=lambda: NOW,
        metrics=Metrics(), monotonic=lambda: tick[0],
    )
    await reader.read(platform="signal", conversation_id=ROOM)
    tick[0] = 29.0
    await reader.read(platform="signal", conversation_id=ROOM)
    assert runtime.state_reads == 1
    tick[0] = 31.0
    await reader.read(platform="signal", conversation_id=ROOM)
    assert runtime.state_reads == 2


def test_a_task_whose_jobs_ended_says_its_files_were_kept():
    view = _view(lapsed_sessions=(SessionLine(_lapsed_jobs_ended(), "lapsed", None),))
    assert "its tmux jobs ended; files kept" in render_computer(view).body


def _lapsed_jobs_ended() -> ComputerSession:
    s = _session(1, status="expired", minutes_ago=10)
    return ComputerSession(
        id=s.id, platform=s.platform, conversation_id=s.conversation_id, scope=s.scope,
        objective=s.objective, status="expired", created_at=s.created_at,
        updated_at=s.updated_at, last_activity_at=s.last_activity_at, actor="tag",
        created_by_tag="t", expired_at=s.expired_at, expired_reason="jobs_ended",
    )


def test_the_ceiling_counts_the_escaped_text():
    """`&` goes out as `&amp;`: five characters, not one."""
    amp = "&" * 200
    view = _view(runs=(RunLine(amp, 0, True, NOW),),
                 files=(StateFile("f.txt", 1, NOW - timedelta(minutes=1)),),
                 caps=ComputerContextCaps(max_tokens=150))
    body = render_computer(view).body
    assert estimate_tokens(body) <= 150


# -- links ---------------------------------------------------------------


def _export_rows(path: str, at: datetime, *, status="ok", artifact_id="art_1",
                 revised=False, title="Trip report"):
    op = uuid.uuid4()
    common = {"operation_id": op, "actor": "tag", "requester_person_id": None,
              "platform": "signal", "conversation_id": ROOM, "conversation_type": "group",
              "tag_id": "t", "scope": SCOPE, "tool": "computer_export", "path": "direct",
              "trace_id": "", "span_id": ""}
    result = {"to": "link", "title": title}
    if status == "ok":
        result |= {"artifact_id": artifact_id, "url": f"https://portal.example.net/artifacts/{artifact_id}",
                   "revised": revised}
    return [
        ComputerEvent(id=uuid.uuid4(), phase="started", created_at=at, started_at=at,
                      finished_at=None, status="started",
                      request={"path": path, "to": "link"}, **common),
        ComputerEvent(id=uuid.uuid4(), phase="finished", created_at=at, started_at=at,
                      finished_at=at, status=status, result=result, **common),
    ]


async def test_a_link_the_room_made_shows_with_the_artifact_id_that_revises_it():
    """A revision in a later turn needs the artifact_id, and the turn keeps
    nothing: the block carries it, from the audit rows, newest per link."""
    events = FakeComputerEventStore()
    for row in (*_export_rows("out/r.html", NOW - timedelta(hours=2)),
                *_export_rows("out/r.html", NOW - timedelta(minutes=10), revised=True),
                *_export_rows("out/x.html", NOW - timedelta(minutes=5), status="refused",
                              artifact_id="art_theirs")):
        await events.append(row)
    view = await _reader(FakeSandboxRuntime(clock=lambda: NOW), FakeComputerSessionStore(),
                         events).read(platform="signal", conversation_id=ROOM)
    body = render_computer(view).body
    assert ('link "Trip report" · artifact_id art_1 · https://portal.example.net/artifacts/art_1 · '
            "from out/r.html · revised 10 min ago") in body
    assert body.count("artifact_id art_1") == 1
    assert "art_theirs" not in body


def test_links_are_capped_with_a_line_saying_how_many_more():
    from nora.domain.computer_context import LinkLine

    view = _view(
        links=tuple(LinkLine(f"art_{i}", f"https://d/{i}", f"page {i}", f"out/{i}.html",
                             False, NOW - timedelta(minutes=i)) for i in range(5)),
        caps=ComputerContextCaps(max_tokens=100_000),
    )
    body = render_computer(view).body
    assert body.count("link \"page") == 3 and "+2 more links" in body


# -- processes outside tmux ----------------------


async def test_a_nohup_job_shows_as_running_and_keeps_its_task_active():
    """FALSIFIER: the block's running section and the session status read
    processes as well as tmux. A `nohup … &` job has no tmux session."""
    runtime = FakeSandboxRuntime(clock=lambda: NOW)
    runtime.start_process(SCOPE, "python tick.py --every 60", pid=159,
                          started=NOW - timedelta(minutes=17))
    sessions = FakeComputerSessionStore()
    await _open_session(sessions)  # last active 3 h ago: idle without a job
    view = await _reader(runtime, sessions, FakeComputerEventStore()).read(
        platform="signal", conversation_id=ROOM)
    body = render_computer(view).body
    assert "process 159 `python tick.py --every 60` · running · started 17 min ago" in body
    assert "open task, active," in body


def test_a_command_is_escaped_exactly_once():
    """`&` and `>` reach the prompt as `&amp;` and `&gt;` once: the renderer
    escapes each line (`render_bullets`), nothing escapes it before."""
    view = _view(runs=(RunLine("python a.py > out.txt && echo ok", 0, True, NOW),),
                 caps=ComputerContextCaps(max_tokens=100_000))
    body = render_computer(view).body
    assert "python a.py &gt; out.txt &amp;&amp; echo ok" in body
    assert "&amp;gt;" not in body and "&amp;amp;" not in body
