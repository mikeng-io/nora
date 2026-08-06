"""Every tool says that it ran (DEC-28's unfinished half).

**The regression this file exists to catch, verbatim.** On 2026-08-06
`grep lookup_person data/nora.log` returned zero, and that was read as "the
tool was never called". It could not have returned anything either way:
`nora/agent/tools.py` emitted exactly one log line on any success path
(`now`'s), and `lookup_person`, `group_members`, `recall`, `read_buffer`,
`read_peer_card` and `send_reply` emitted none at all. Two architectural
conclusions were drawn from that zero, both wrong, and one nearly retracted a
design document. A silence that reads identically whether the code ran on every
turn or never is not evidence.

Integration-style on purpose, through `setup_logging` and the real structlog
pipeline rather than `caplog`: the claim being made is about what an operator
finds in `data/nora.log`, which is JSON lines produced by a chain of processors
— including the redactor and the `tag_id` contextvar merge — that a caplog
assertion does not exercise at all.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from nora.agent.egress import scan_identifiers
from nora.agent.guards import Deliverer
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import ConversationTarget, DeliveryStatus, TranscriptRow
from nora.obs.logging import bind_tag_id, clear_tag_id, setup_logging
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource
from tests.builders import make_logging_config
from tests.fakes import FakeHoncho, SinkSender
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
    """Two rows, one of them a reply — so `read_buffer`'s `rows` and `threaded`
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


@pytest.fixture
def wiring(tmp_path, restore_logging):
    path = tmp_path / "nora.log"
    cfg = make_logging_config(path=path)
    setup_logging(level=cfg.level, path=cfg.path, format=cfg.format, secrets=[])

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
        canary="C", tag_id="t1", nora_sender_id="nora", metrics=Metrics(),
    )
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, self_ids=NORA,
    )
    tools = build_tools(
        store=_Store(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=FakeHoncho(conclusions={"miles_novak": ["Miles is from Ridgeline"]},
                                           cards={"miles_novak": ["likes espresso"]}),
        deliverer=deliverer, metrics=metrics, room=room, question="邊個係阿豹",
        context_window=40,
    )
    return {"tools": {t.tool_name: t for t in tools}, "path": path, "metrics": metrics,
            "sink": sink}


class _FakeAudit:
    async def append(self, **kwargs) -> int:
        return 1

    async def set_delivery_outcome(self, **kwargs) -> None:
        return None


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


def _lines(path) -> list[dict]:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return [json.loads(line) for line in path.read_text().splitlines()]


def _tool_lines(path) -> list[dict]:
    return [record for record in _lines(path) if record.get("tool")]


# ---- the probe ---------------------------------------------------------------


async def test_a_turn_that_calls_lookup_person_says_so_in_the_log(wiring):
    """THE PROBE, in the exact shape of the grep that misled us.

    Delete `_log_call` from `lookup_person` and this goes red — which is what
    the original `grep lookup_person data/nora.log` could not do.
    """
    await _call(wiring["tools"]["lookup_person"], name="阿豹")
    assert "lookup_person" in wiring["path"].read_text()
    line = _tool_lines(wiring["path"])[-1]
    assert line["tool"] == "lookup_person"
    assert line["status"] == "ok"
    assert "lookup_person" in line["message"]


async def test_every_tool_leaves_exactly_one_line_naming_itself(wiring):
    """All seven, not just the one that was grepped for. The failure was not
    "lookup_person is quiet", it was "a tool is not expected to say anything",
    and a test covering one tool would leave the next six free to regress.
    """
    tools = wiring["tools"]
    await _call(tools["now"])
    await _call(tools["read_buffer"], limit=40)
    await _call(tools["read_peer_card"], sender_id="+85290000101")
    await _call(tools["group_members"])
    await _call(tools["lookup_person"], name="阿豹")
    await _call(tools["recall"], person="俊爺")
    await _call(tools["send_reply"], text="七點半")
    named = [record["tool"] for record in _tool_lines(wiring["path"])]
    assert sorted(named) == sorted([
        "now", "read_buffer", "read_peer_card", "group_members",
        "lookup_person", "recall", "send_reply",
    ])


async def test_a_line_says_how_the_tool_answered_not_just_that_it_ran(wiring):
    """"It ran" is not enough to act on: `not_found` and `unavailable` are the
    distinction R-22 exists for, and a line that only proved the call happened
    would send the next investigation to the same wrong place."""
    tools = wiring["tools"]
    await _call(tools["lookup_person"], name="阿豹")
    await _call(tools["lookup_person"], name="Nobody At All")
    statuses = [record["status"] for record in _tool_lines(wiring["path"])]
    assert statuses == ["ok", "not_found"]


async def test_the_counts_in_a_line_are_the_real_ones(wiring):
    """A line reporting a constant would be as useless as no line. The fixture's
    store holds two rows, exactly one of which is a reply."""
    await _call(wiring["tools"]["read_buffer"], limit=40)
    line = _tool_lines(wiring["path"])[-1]
    assert (line["rows"], line["threaded"], line["limit"]) == (2, 1, 40)


async def test_a_clamped_limit_is_reported_as_the_clamped_value(wiring):
    """`limit` is the one model-supplied number in this module and the clamp is
    the only bound on it — a line reporting what the model ASKED for rather
    than what was served would hide the clamp working."""
    await _call(wiring["tools"]["read_buffer"], limit=10_000)
    assert _tool_lines(wiring["path"])[-1]["limit"] == 60


# ---- what must never be in the file -----------------------------------------


async def test_no_tool_line_carries_a_phone_number_or_an_aci(wiring):
    """`data/nora.log` is a file on disk and nothing scans it on the way out.
    `read_peer_card` takes a raw platform id and `lookup_person` takes a
    model-supplied string that has arrived as a phone number before
    (`_asked_about` exists for that) — so both are called here with one."""
    tools = wiring["tools"]
    await _call(tools["read_peer_card"], sender_id="+85290000101")
    await _call(tools["read_peer_card"], sender_id="+85260000000")
    await _call(tools["lookup_person"], name="+85290000102")
    await _call(tools["recall"], person="a1000001-0000-4000-8000-000000000001")
    # ...and the remaining three, so the scan below covers the whole file
    # rather than the three lines that happened to be written. `now` emits an
    # ISO timestamp with a `+08:00` offset, which is the one detail in here
    # shaped anything like a phone number.
    await _call(tools["now"])
    await _call(tools["read_buffer"], limit=40)
    await _call(tools["group_members"])
    text = wiring["path"].read_text()
    assert scan_identifiers(text) is None, text
    assert "+8529" not in text and "+8526" not in text


async def test_no_tool_line_carries_a_conclusion_or_a_transcript_body(wiring):
    """Counts and statuses only. A conclusion is distilled from what somebody
    actually said about a person, and a transcript body is the group's own
    words — neither becomes safe by being written to a different file."""
    tools = wiring["tools"]
    await _call(tools["recall"], person="俊爺")
    await _call(tools["read_buffer"], limit=40)
    await _call(tools["read_peer_card"], sender_id="+85290000101")
    await _call(tools["send_reply"], text="今晚七點半得")
    text = wiring["path"].read_text()
    for secret in ("Miles is from Ridgeline", "likes espresso", "去邊食", "你屋企",
                   "今晚七點半得"):
        assert secret not in text, secret
    # ...and the reply's LENGTH is there, which is the thing that is safe.
    assert _tool_lines(wiring["path"])[-1]["chars"] == 6


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
    assert _tool_lines(wiring["path"])[-1]["tag_id"] == "tag-42"


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
    assert _tool_lines(wiring["path"])[-1]["tag_id"] == "tag-77"


async def test_a_line_emitted_outside_a_tag_carries_no_tag_id(wiring):
    """The other direction — otherwise a hardcoded default would pass the test
    above while attributing every line to the same phantom tag."""
    await _call(wiring["tools"]["now"])
    assert "tag_id" not in _tool_lines(wiring["path"])[-1]
