"""The `<computer>` block (design/sandbox-runtime §15.3): a deterministic
window, rebuilt each turn from the machine and the audit, never a fold.

Pinned: absent while `[computer]` is off and for a room not using the
computer (and that room costs no VM read); each section's cap with a «+N
more» line; the token ceiling drops the OLDEST lines first and says how many;
an age on every line; a lapsed task shows within its window and not after;
the block says what it is and that the computer is its source (ADR 0013).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import tiktoken

from nora.agent.prefill import build_prefill
from nora.domain.computer import StateFile, TmuxSession, computer_scope
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
SCOPE = computer_scope("signal", ROOM)
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
    assert 'lapsed task, stopped 23 h ago (idle)' in block.body and "book a table" in block.body
    later = ComputerContextReader(sessions=sessions, events=events, runtime=runtime,
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
    """The measured size of a full example (every section at its cap)."""
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
