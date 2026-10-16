"""The `remember` tool — the ONE write lane for a settled person-fact, as a
Honcho conclusion under (observer=nora, observed=tagger).

The store contract (`tests/contract/test_memory_store_contract.py`) already
covers the memory port; fakes mirror it. What THIS file tests is the tool
layer's side of the three-state honesty contract — the tool exists so that
Nora never says 「記低咗」 and writes nowhere, so `ok` versus
`refused` versus `unavailable` must each be a REAL, pinned outcome (R-27):
- `ok` is the only state in which she may claim to have remembered;
- a missing tagger is identity failure, `refused`, never a guess-peer write;
- a down store is `unavailable`, never a note fallback, never a silent ok.

Mirrors `tests/test_note_tools.py`'s structure (same fake store/persons/room
shape, same `_tools` builder pattern).
"""

from __future__ import annotations

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRosterSource
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
GROUP = "signal:group-1"


class _NullStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


def _room(*, tagger: PersonRef | None = MILES, conversation_id: str = GROUP) -> Room:
    source = FakeRosterSource(groups={conversation_id: (frozenset({"miles"}),)})
    return Room(
        platform="signal", conversation_id=conversation_id, roster_source=source,
        persons=_FakePersons(MILES), tagger=tagger,
    )


def _tools(
    *, honcho: FakeMemoryStore | None = None, tagger: PersonRef | None = MILES,
    metrics: Metrics | None = None, conversation_id: str = GROUP,
) -> dict:
    registry = ToolRegistry()
    specs = registry.specs(
        store=_NullStore(), conversation_id=conversation_id,
        conversation_type="group",
        persons=_FakePersons(MILES), honcho=honcho or FakeMemoryStore(),
        deliverer=SinkSender(), metrics=metrics or Metrics(),
        room=_room(tagger=tagger, conversation_id=conversation_id),
    )
    return {spec.name: spec.call for spec in specs}


# ── the table ─────────────────────────────────────────────────────────────────

def test_remember_is_budget_1_and_not_terminal():
    """Sized like `note_create` (one write answers the ask; a second call is a
    loop) and not terminal — she still owes the room 「記低咗」 as her reply."""
    assert NATIVE_TOOLS["remember"].budget == 1
    assert NATIVE_TOOLS["remember"].terminal is False


# ── the three states ──────────────────────────────────────────────────────────

async def test_remember_ok_lands_the_pair_and_reads_back():
    """`ok` ONLY when the write actually landed: the (observer, observed,
    content) tuple is recorded under the TAGGER's peer id — a write under a
    guessed peer would be a memory about nobody `about` can never find."""
    honcho = FakeMemoryStore()
    tools = _tools(honcho=honcho)
    result = await tools["remember"](content="屋企有兩隻貓")
    assert result == {
        "status": "ok", "person": MILES.name,
        "note": "It is saved. Only now may you say 記低咗.",
    }
    assert honcho.writes == [("nora", "miles", "屋企有兩隻貓")]
    # the read lane serves it back — remember→about closed in memory
    assert honcho.conclusions["miles"] == ["屋企有兩隻貓"]


async def test_remember_refuses_without_a_tagger():
    """Fail-closed on identity, the same refusal `note_create` makes: without
    a resolved tagger there is no observed peer to attach the fact to."""
    tools = _tools(tagger=None)
    result = await tools["remember"](content="屋企有兩隻貓")
    assert result["status"] == "refused"
    assert "don't guess" in result["note"]


async def test_remember_refuses_empty_content():
    tools = _tools()
    result = await tools["remember"](content="   ")
    assert result["status"] == "refused"


async def test_remember_refuses_content_over_the_endpoint_bound():
    """The endpoint's own bound (ConclusionCreate.content max 65535) — refuse
    with the fix instead of letting the write die as an `unavailable` that
    reads like memory being down."""
    tools = _tools()
    result = await tools["remember"](content="x" * 65536)
    assert result["status"] == "refused"
    assert "65535" in result["note"]


async def test_remember_unavailable_when_the_store_is_down():
    """A down memory store is `unavailable` — never a silent ok, and never a
    fallback to the note tier (a failed conclusion is a failure; writing it
    somewhere else would teach tomorrow's `about` to lie by omission)."""
    honcho = FakeMemoryStore(fail=True)
    tools = _tools(honcho=honcho)
    result = await tools["remember"](content="屋企有兩隻貓")
    assert result["status"] == "unavailable"
    assert honcho.writes == []


async def test_remember_keeps_the_fact_text_verbatim():
    """The content is the settled fact in the speaker's words, not a summary —
    a paraphrase is already one step away from what they said (same rule as
    `note_create` accepting body verbatim)."""
    honcho = FakeMemoryStore()
    tools = _tools(honcho=honcho)
    await tools["remember"](content="  我下個月返新工  ")
    assert honcho.writes[0][2] == "我下個月返新工"  # stripped, not wrapped


# ── ADR 0015: how to report a status rides in the result ─────────────────

async def test_every_non_ok_status_carries_a_note_that_nothing_was_saved():
    """The description only NAMES the statuses. «Never claim 記低咗 on a
    refusal or an outage» rides in the result, so every non-ok path must say
    in its own `note` that nothing was saved, dash-free."""
    cases = {
        "refused/no_tagger": (_tools(tagger=None), "屋企有兩隻貓"),
        "refused/empty": (_tools(), "   "),
        "refused/too_long": (_tools(), "x" * 65536),
        "unavailable/down": (_tools(honcho=FakeMemoryStore(fail=True)), "屋企有兩隻貓"),
    }
    for case, (tools, content) in cases.items():
        result = await tools["remember"](content=content)
        assert result["status"] == case.split("/")[0], (case, result)
        note = result.get("note")
        assert isinstance(note, str) and note.strip(), case
        assert "—" not in note and "–" not in note, case
        assert "nothing was saved" in note.lower(), case


async def test_only_the_ok_note_licenses_saying_it_was_remembered():
    """«Say 記低咗 only when it is saved» is an output rule (ADR 0015): it
    rides on `ok`, and no refusal or outage note offers the word as allowed."""
    ok = await _tools()["remember"](content="屋企有兩隻貓")
    assert "Only now may you say 記低咗" in ok["note"]
    down = await _tools(honcho=FakeMemoryStore(fail=True))["remember"](content="貓")
    assert "never say 記低咗" in down["note"]
    refused = await _tools(tagger=None)["remember"](content="貓")
    assert "may you say" not in refused["note"]
