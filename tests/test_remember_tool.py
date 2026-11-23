"""The `remember` tool — the ONE write lane for a settled person-fact, as a
Profile `told` line citing the message that raised the turn.

The store's own rules are `tests/unit/test_profile_rules.py` and the real-store
path is `tests/infra/test_remember_profile.py`. What THIS file tests is the tool
layer's side of the three-state honesty contract — the tool exists so that
Nora never says 「記低咗」 and writes nowhere, so `ok` versus `refused` versus
`unavailable` must each be a REAL, pinned outcome (R-27):
- `ok` is the only state in which she may claim to have remembered;
- a missing tagger is identity failure, `refused`, never a guess-subject write;
- a down store is `unavailable`, never a note fallback, never a silent ok.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import RoomFacts
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.profile import (
    LINE_MAX_CHARS,
    AddOp,
    ProfileRefusal,
    ProfileWriteResult,
)
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource
from tests.fakes import SinkSender
from tests.test_room_tools import (
    _UNREACHABLE_TRANSIT,
    GROUP,
    IDENTITY,
    MILES,
    NORA,
    TYLER,
    _call,
    _NullStore,
)

UNIT = "u1"
_FRIENDS = RoomFacts(
    "signal", frozenset({GROUP}), UNIT, frozenset({"miles_novak", "tyler_cortez"}),
    label="Friends",
)
_LINEAGE = LineagePolicy(rooms=(_FRIENDS,), _by_id={("signal", GROUP): _FRIENDS})
_FACT = "屋企有兩隻貓"


class _Profile:
    """The Profile writer as the tool sees it: what it was asked to write, and
    what it answers. `raises` is the down store."""

    def __init__(
        self, *, result: ProfileWriteResult | None = None, raises: bool = False,
    ) -> None:
        self._result = result or ProfileWriteResult(status="ok", revision=1)
        self._raises = raises
        self.writes: list[dict[str, Any]] = []

    async def account_state(self, *, key: str) -> tuple[int, int]:
        return 0, 0

    async def account_heads(self, *, key: str) -> tuple:
        return ()

    async def profile_write(self, **kwargs: Any) -> ProfileWriteResult:
        if self._raises:
            raise RuntimeError("store down")
        self.writes.append(kwargs)
        return self._result


@pytest.fixture
def persons(tmp_path: Any) -> Any:
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _remember(
    persons: Any, *, profile: _Profile | None, tagger: bool = True,
    trigger: tuple[int, ...] = (41,),
) -> Any:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=persons.by_person["miles_novak"] if tagger else None,
        self_ids=NORA, trigger_message_ids=trigger,
    )
    built = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        question="", context_window=40, lineage=_LINEAGE, supplied_unit=UNIT,
        profile=profile,
        transcript=tuple(
            TranscriptRow(
                id=n, ts=1000 * n, person_id="miles_novak", sender_id="sender",
                sender_name="Miles", body=_FACT, is_nora=False,
                delivery_status=DeliveryStatus.SENT, raw_json=None,
            ) for n in trigger
        ),
    )
    return {t.tool_name: t for t in built}["remember"]


async def _say(tool: Any, content: str) -> dict:
    return await _call(
        tool, content=content, fact_key="dietary_restrictions", fact_value=[content],
    )


# ── the table ─────────────────────────────────────────────────────────────────

def test_remember_is_budget_1_and_not_terminal():
    """Sized like `note_create` (one write answers the ask; a second call is a
    loop) and not terminal — she still owes the room 「記低咗」 as her reply."""
    assert NATIVE_TOOLS["remember"].budget == 1
    assert NATIVE_TOOLS["remember"].terminal is False


# ── the three states ──────────────────────────────────────────────────────────

async def test_remember_ok_records_who_told_it(persons):
    profile = _Profile()
    result = await _say(_remember(persons, profile=profile), _FACT)
    assert result == {
        "status": "ok", "person": persons.by_person["miles_novak"].name,
        "note": "It is saved. Only now may you say 記低咗.",
    }
    [write] = profile.writes
    [op] = write["ops"]
    assert isinstance(op, AddOp)
    assert (op.mode, op.told_by, op.citations) == ("told", "miles_novak", ("41",))
    assert write["account"].key == f"{UNIT}#character:miles_novak"


async def test_remember_refuses_without_a_tagger(persons):
    """Fail-closed on identity, the same refusal `note_create` makes: without
    a resolved tagger there is no one who told it."""
    profile = _Profile()
    result = await _say(_remember(persons, profile=profile, tagger=False), _FACT)
    assert result["status"] == "refused"
    assert "don't guess" in result["note"]
    assert profile.writes == []


async def test_remember_refuses_empty_content(persons):
    profile = _Profile()
    result = await _say(_remember(persons, profile=profile), "   ")
    assert result["status"] == "refused"
    assert profile.writes == []


async def test_remember_refuses_content_over_the_line_bound(persons):
    """A Profile line has its own bound — refuse with the fix instead of letting
    the write die as an `unavailable` that reads like memory being down."""
    profile = _Profile()
    result = await _say(_remember(persons, profile=profile), "x" * (LINE_MAX_CHARS + 1))
    assert result["status"] == "refused"
    assert str(LINE_MAX_CHARS) in result["note"]
    assert profile.writes == []


async def test_remember_unavailable_when_the_store_is_down(persons):
    """A down store is `unavailable` — never a silent ok, and never a fallback
    to the note tier (a failed write is a failure; writing it somewhere else
    would teach tomorrow's recall to lie by omission)."""
    profile = _Profile(raises=True)
    result = await _say(_remember(persons, profile=profile), _FACT)
    assert result["status"] == "unavailable"
    # She must not claim to have remembered what was not saved: the note forbids the
    # phrase outright, and never licenses it.
    assert "never say 記低咗" in result["note"]
    assert "may you say" not in result["note"]
    assert "as a note instead" in result["note"]
    assert profile.writes == []


async def test_remember_unavailable_when_no_store_is_wired(persons):
    """No writer, or a turn no Record message raised: «I couldn't», never a
    record."""
    unwired = await _say(_remember(persons, profile=None), _FACT)
    uncited = await _say(_remember(persons, profile=_Profile(), trigger=()), _FACT)
    assert unwired["status"] == uncited["status"] == "unavailable"
    for result in (unwired, uncited):
        assert "never say 記低咗" in result["note"]


async def test_remember_keeps_the_fact_text_verbatim(persons):
    """The content is the settled fact in the speaker's words, not a summary —
    a paraphrase is already one step away from what they said (same rule as
    `note_create` accepting body verbatim)."""
    profile = _Profile()
    await _say(_remember(persons, profile=profile), "  我下個月返新工  ")
    [op] = profile.writes[0]["ops"]
    assert op.text == "我下個月返新工"  # stripped, not wrapped


# ── ADR 0015: how to report a status rides in the result ─────────────────

def _cases(persons: Any) -> dict[str, tuple[Any, str]]:
    store_refused = ProfileWriteResult(
        status="refused", revision=None,
        refusals=(ProfileRefusal(reason="identifier", detail="a number"),),
    )
    return {
        "refused/no_tagger": (_remember(persons, profile=_Profile(), tagger=False), _FACT),
        "refused/empty": (_remember(persons, profile=_Profile()), "   "),
        "refused/too_long": (
            _remember(persons, profile=_Profile()), "x" * (LINE_MAX_CHARS + 1)),
        "refused/store": (
            _remember(persons, profile=_Profile(result=store_refused)), _FACT),
        "unavailable/down": (_remember(persons, profile=_Profile(raises=True)), _FACT),
        "unavailable/unwired": (_remember(persons, profile=None), _FACT),
    }


async def test_every_non_ok_status_carries_a_note_that_nothing_was_saved(persons):
    """The description only NAMES the statuses. «Never claim 記低咗 on a
    refusal or an outage» rides in the result, so every non-ok path must say
    in its own `note` that nothing was saved, dash-free."""
    for case, (tool, content) in _cases(persons).items():
        result = await _say(tool, content)
        assert result["status"] == case.split("/")[0], (case, result)
        note = result.get("note")
        assert isinstance(note, str) and note.strip(), case
        assert "—" not in note and "–" not in note, case
        assert "nothing was saved" in note.lower(), case


async def test_only_the_ok_note_licenses_saying_it_was_remembered(persons):
    """«Say 記低咗 only when it is saved» is an output rule (ADR 0015): it
    rides on `ok`, and no refusal or outage note offers the word as allowed."""
    ok = await _say(_remember(persons, profile=_Profile()), _FACT)
    assert "Only now may you say 記低咗" in ok["note"]
    for case, (tool, content) in _cases(persons).items():
        note = (await _say(tool, content))["note"]
        assert "may you say" not in note, case
        # an outage names the word only to forbid it; a refusal does not need to
        if case.startswith("unavailable/"):
            assert "never say 記低咗" in note, case
        else:
            assert "記低咗" not in note or "don't say 記低咗" in note, case
