"""Every tool says that it ran (DEC-28's unfinished half).

**The failure this file guards.** `grep lookup_person data/nora.log` returning
zero would read as "the tool was never called", yet a tool that logs nothing on
success returns zero either way. A silence that reads identically whether the
code ran on every turn or never is not evidence, so every tool emits a line on
its success path.

Integration-style on purpose, through `setup_logging` and the real structlog
pipeline rather than `caplog`: the claim being made is about what an operator
finds in the log — JSON lines on stdout — produced by a chain of processors
— including the redactor and the `tag_id` contextvar merge — that a caplog
assertion does not exercise at all.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from unittest.mock import patch

import pytest

from nora.agent.egress import scan_identifiers
from nora.agent.guards import Deliverer
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOL_BUDGETS, build_tools
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import ConversationTarget, DeliveryStatus, TranscriptRow
from nora.infra.transit import TransitClient
from nora.obs.logging import bind_tag_id, clear_tag_id, setup_logging
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMemoryStore, FakeRosterSource
from tests.builders import make_logging_config
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
NORA = frozenset({"+85290000100"})
MILES = frozenset({"+85290000101"})
TYLER = frozenset({"+85290000102"})

IDENTITY = """
[persons.miles_novak]
first_name = "Miles"
last_name  = "Novak"
aliases = ["俊爺", "Miles"]
[[persons.miles_novak.signal]]
phone = "+85290000101"

[persons.tyler_cortez]
first_name = "Tyler"
last_name  = "Cortez"
aliases = ["阿豹"]
[[persons.tyler_cortez.signal]]
phone = "+85290000102"
"""


class _Store:
    """Two rows, one of them a reply — so `load_transcript`'s `rows` and `threaded`
    counts are different numbers and a line that reported one as the other
    would be visible. A store that answered every call with `[]` would let the
    counts be anything at all."""

    def __init__(self) -> None:
        self.rows = [
            TranscriptRow(id=1, sender_id="+85290000101", sender_name="Miles", ts=1000,
                          body="去邊食", is_nora=False,
                          delivery_status=DeliveryStatus.PENDING, raw_json=None),
            TranscriptRow(id=2, sender_id="+85290000102", sender_name="Tyler", ts=2000,
                          body="你屋企", is_nora=False,
                          delivery_status=DeliveryStatus.PENDING, raw_json=None,
                          quoted_ts=1000),
        ]

    async def recent(self, *, conversation_id: str, limit: int):
        return self.rows[-limit:]


# `transit` is not what these tests are about — see `UNREACHABLE_TRANSIT` in
# tests/test_tools.py for why this is a real client aimed at a closed port
# rather than a stand-in object.
_UNREACHABLE_TRANSIT = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)


@pytest.fixture
def restore_logging():
    """`setup_logging` replaces the ROOT logger's handlers, so put them back —
    otherwise every later test in the session writes into this test's tmp_path.
    """
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    root.handlers = []
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.handlers = saved
    root.setLevel(level)


class _Stdout:
    """Stands in for the stream `setup_logging` writes to (stdout). It
    keeps `read_text()` so the assertions below read as if the sink were a
    file: what matters is what is true of the lines, not where they go."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def read_text(self) -> str:
        logging.shutdown()
        return self.buffer.getvalue()


@pytest.fixture
def wiring(tmp_path, restore_logging):
    log = _Stdout()
    cfg = make_logging_config()
    # `setup_logging` binds `sys.stdout` at call time, so the patch has to be
    # in place before it runs — and only for that call, since the handler holds
    # the stream afterwards.
    with patch("sys.stdout", log.buffer):
        setup_logging(level=cfg.level, format=cfg.format, secrets=[])

    identity = tmp_path / "identity.toml"
    identity.write_text(IDENTITY)
    persons = load_persons(identity)

    pdir = tmp_path / "doctrine"
    pdir.mkdir()
    write(pdir, "identity.md", name="soul", order=10, body="You are Nora.")
    doctrine = DoctrineStore(pdir, canary="C", metrics=Metrics()).get()

    metrics = Metrics()
    sink = SinkSender()
    deliverer = Deliverer(
        store=_FakeAudit(), adapter=sink,
        target=ConversationTarget("signal", GROUP, "group"), doctrine=doctrine,
        canary="C", tag_id="t1", nora_sender_id="nora",
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag", metrics=Metrics(),
    )
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, self_ids=NORA,
    )
    tools = build_tools(
        store=_Store(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(conclusions={"miles_novak": [
            "Miles is from Ridgeline"
        ]},
                                           cards={"miles_novak": ["likes espresso"]}),
        deliverer=deliverer, metrics=metrics, room=room, question="邊個係阿豹",
        transit_source=_UNREACHABLE_TRANSIT, context_window=40,
    )
    return {"tools": {t.tool_name: t for t in tools}, "log": log, "metrics": metrics,
            "sink": sink}


class _FakeAudit:
    async def append(self, **kwargs) -> int:
        return 1

    async def set_delivery_outcome(self, **kwargs) -> None:
        return None


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


def _lines(log) -> list[dict]:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return [json.loads(line) for line in log.read_text().splitlines()]


def _tool_lines(log) -> list[dict]:
    return [record for record in _lines(log) if record.get("tool")]


# ---- the probe ---------------------------------------------------------------


async def test_a_turn_that_calls_lookup_person_says_so_in_the_log(wiring):
    """THE PROBE, in the shape of `grep lookup_person data/nora.log`.

    Delete `_log_call` from `lookup_person` and this goes red — which that
    grep alone could not do.
    """
    await _call(wiring["tools"]["lookup_person"], name="阿豹")
    assert "lookup_person" in wiring["log"].read_text()
    line = _tool_lines(wiring["log"])[-1]
    assert line["tool"] == "lookup_person"
    assert line["status"] == "ok"
    assert "lookup_person" in line["message"]


async def test_every_tool_leaves_exactly_one_line_naming_itself(wiring):
    """All seven, not just the one that was grepped for. The failure is not
    "lookup_person is quiet", it is "a tool is not expected to say anything",
    and a test covering one tool would leave the others free to regress.
    """
    tools = wiring["tools"]
    await _call(tools["now"])
    await _call(tools["load_transcript"], limit=40)
    await _call(tools["group_members"])
    await _call(tools["lookup_person"], name="阿豹")
    await _call(tools["about"], person="俊爺")
    await _call(tools["calculate"], expression="1485/7")
    await _call(tools["send_reply"], text="七點半")
    named = [record["tool"] for record in _tool_lines(wiring["log"])]
    assert sorted(named) == sorted([
        "now", "load_transcript", "group_members",
        "lookup_person", "about", "calculate", "send_reply",
    ])


async def test_no_calculate_line_carries_the_expression_the_model_wrote(wiring):
    """`_log_call` takes COUNTS and statuses, never the model's own argument,
    and `calculate`'s argument is no different from `lookup_person`'s `name`.

    `+85290000101 + 0` is valid arithmetic, so a model can put a phone number
    in this parameter as easily as in that one — and the log is a
    worse place for one than a tool result is, because nothing scans it on the
    way out. The expression reaches the TRACE through the tool result, which is
    where DEC-31 wanted it; it does not reach the log.
    """
    await _call(wiring["tools"]["calculate"], expression="+85290000101 + 0")
    await _call(wiring["tools"]["calculate"], expression="1485/7")
    lines = [record for record in _tool_lines(wiring["log"])
             if record["tool"] == "calculate"]
    assert len(lines) == 2
    for line in lines:
        rendered = json.dumps(line, ensure_ascii=False)
        assert "85290000101" not in rendered
        assert "1485/7" not in rendered
        assert scan_identifiers(rendered) is None
    # It still says how it answered — silence is what DEC-31 was fixing.
    assert [line["status"] for line in lines] == ["ok", "ok"]


async def test_a_line_says_how_the_tool_answered_not_just_that_it_ran(wiring):
    """"It ran" is not enough to act on: `not_found` and `unavailable` are the
    distinction R-22 exists for, and a line that only proved the call happened
    would send the next investigation to the same wrong place."""
    tools = wiring["tools"]
    await _call(tools["lookup_person"], name="阿豹")
    await _call(tools["lookup_person"], name="Nobody At All")
    statuses = [record["status"] for record in _tool_lines(wiring["log"])]
    assert statuses == ["ok", "not_found"]


async def test_the_counts_in_a_line_are_the_real_ones(wiring):
    """A line reporting a constant would be as useless as no line. The fixture's
    store holds two rows, exactly one of which is a reply."""
    await _call(wiring["tools"]["load_transcript"], limit=40)
    line = _tool_lines(wiring["log"])[-1]
    assert (line["rows"], line["threaded"], line["limit"]) == (2, 1, 40)


async def test_a_clamped_limit_is_reported_as_the_clamped_value(wiring):
    """`limit` is the one model-supplied number in this module and the clamp is
    the only bound on it — a line reporting what the model ASKED for rather
    than what was served would hide the clamp working."""
    await _call(wiring["tools"]["load_transcript"], limit=10_000)
    assert _tool_lines(wiring["log"])[-1]["limit"] == 60


# ---- what must never be in the file -----------------------------------------


async def test_no_tool_line_carries_a_phone_number_or_an_aci(wiring):
    """The log leaves the process unscanned — nothing redacts identifiers on
    the way out.
    `lookup_person` and `about` take a model-supplied string that has arrived
    as a phone number or an ACI before (`_asked_about` exists for that) — so
    both are called here with one."""
    tools = wiring["tools"]
    await _call(tools["lookup_person"], name="+85290000101")
    await _call(tools["lookup_person"], name="+85260000000")
    await _call(tools["lookup_person"], name="+85290000102")
    await _call(tools["about"], person="a1000001-0000-4000-8000-000000000001")
    # ...and the remaining three, so the scan below covers the whole file
    # rather than the three lines that happened to be written. `now` emits an
    # ISO timestamp with a `+08:00` offset, which is the one detail in here
    # shaped anything like a phone number.
    await _call(tools["now"])
    await _call(tools["load_transcript"], limit=40)
    await _call(tools["group_members"])
    text = wiring["log"].read_text()
    assert scan_identifiers(text) is None, text
    assert "+8529" not in text and "+8526" not in text


async def test_no_tool_line_carries_a_conclusion_or_a_transcript_body(wiring):
    """Counts and statuses only. A conclusion is distilled from what somebody
    actually said about a person, and a transcript body is the group's own
    words — neither becomes safe by being written to a different file."""
    tools = wiring["tools"]
    await _call(tools["about"], person="俊爺")
    await _call(tools["load_transcript"], limit=40)
    await _call(tools["send_reply"], text="今晚七點半得")
    text = wiring["log"].read_text()
    for secret in ("Miles is from Ridgeline", "likes espresso", "去邊食", "你屋企",
                   "今晚七點半得"):
        assert secret not in text, secret
    # ...and the reply's LENGTH is there, which is the thing that is safe.
    assert _tool_lines(wiring["log"])[-1]["chars"] == 6


# ---- the tag_id bridge -------------------------------------------------------


async def test_a_tool_line_carries_the_tag_id_without_the_tool_knowing_it(wiring):
    """`nora.obs.logging` binds `tag_id` to a `structlog.contextvars` variable
    for the lifetime of the tag and `merge_contextvars` copies it onto every
    record emitted while it is bound. Nothing in `nora/agent/tools.py` mentions
    `tag_id`; this is what says the bridge reaches inside a tool closure.
    """
    bind_tag_id("tag-42")
    try:
        await _call(wiring["tools"]["group_members"])
    finally:
        clear_tag_id()
    assert _tool_lines(wiring["log"])[-1]["tag_id"] == "tag-42"


async def test_the_tag_id_survives_the_task_hop_strands_makes_per_tool_call(wiring):
    """Strands dispatches each tool use through `asyncio.create_task`
    (`strands.tools.executors.concurrent.ConcurrentToolExecutor._execute`), and
    a `ContextVar` set before task creation is copied into the child context —
    which is the only reason the binding above holds in production. Asserted
    against a real task hop rather than assumed, because if that ever changed
    the lines would keep being written and silently stop being attributable.
    """
    bind_tag_id("tag-77")
    try:
        await asyncio.create_task(_call(wiring["tools"]["now"]))
    finally:
        clear_tag_id()
    assert _tool_lines(wiring["log"])[-1]["tag_id"] == "tag-77"


async def test_a_line_emitted_outside_a_tag_carries_no_tag_id(wiring):
    """The other direction — otherwise a hardcoded default would pass the test
    above while attributing every line to the same phantom tag."""
    await _call(wiring["tools"]["now"])
    assert "tag_id" not in _tool_lines(wiring["log"])[-1]
