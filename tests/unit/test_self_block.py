"""The `<self>` block: her own Character · Nora account for the turn's unit
(design/self-growth/20, DEC-291).

What must hold, each with the way it would fail:

- the three occasions read it from the unit's key (`nora_character_key`), and
  only that key: a room's, a unit's or a person's account is not hers;
- a unit with no account renders NO block, on every occasion, not an empty one;
- on the tag and fire turns it is the first block, and on a Cognition turn it
  is the first block of the prompt;
- the retired `<self_card>` is on no prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.lineage import LineagePolicy
from nora.agent.prefill import _BLOCKS as PREFILL_BLOCKS
from nora.app.cognition_grounding import build_grounder
from nora.app.fire import FirePath
from nora.domain.cognition_ground import Grounding
from nora.domain.culture import CultureRevision, nora_character_key
from nora.domain.events import Event
from nora.language.cognition_render import _BLOCKS as COGNITION_BLOCKS
from nora.language.cognition_render import render_agenda
from nora.language.culture_render import render_self
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCultureStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
UNIT = "friends"


def _rev(body: str) -> CultureRevision:
    return CultureRevision(
        id="r-nora", culture_key=nora_character_key(UNIT), body=body,
        written_at=int(NOW.timestamp() * 1000), established_by="character-nora",
    )


class _Policy:
    def conversation(self, platform, conversation_id):
        return type("_Convo", (), {"primary_id": "g1", "unit": UNIT})()


def _fire_path(culture) -> FirePath:
    path = object.__new__(FirePath)
    path._culture = culture
    path._policy = _Policy()
    path._metrics = Metrics()
    path._standing_store_timeout_seconds = 5.0
    return path


def _participation() -> Event:
    return Event(
        kind="participation", platform="signal", conversation_id="g1",
        conversation_type="group", person_id=None, body="",
        header=lambda ref, now: "",
    )


async def test_the_fire_path_reads_her_account_by_the_units_nora_key():
    culture = InMemoryCultureStore()
    await culture.append(
        key=nora_character_key(UNIT), body="she keeps it short here",
        written_at=1, established_by="character-nora")
    # Accounts that are NOT hers: the unit's and a person's.
    await culture.append(
        key=f"{UNIT}#unit", body="UNIT ACCOUNT", written_at=1, established_by="x")
    await culture.append(
        key=f"{UNIT}#character:miles", body="PERSON ACCOUNT", written_at=1,
        established_by="x")
    culture.reads.clear()

    got = await _fire_path(culture)._read_self_account(_participation())

    assert got is not None and got.body == "she keeps it short here"
    assert culture.reads == [nora_character_key(UNIT)]


async def test_a_unit_with_no_account_reads_none_on_the_fire_path():
    assert await _fire_path(InMemoryCultureStore())._read_self_account(
        _participation()) is None
    assert await _fire_path(None)._read_self_account(_participation()) is None


def test_no_account_renders_no_block_not_an_empty_one():
    assert render_self(None) is None
    assert render_self(_rev("  \n ")) is None


def test_the_self_block_is_the_first_row_of_the_tag_and_cognition_tables():
    for table in (PREFILL_BLOCKS, COGNITION_BLOCKS):
        order = {name: o for name, o, _key in table}
        assert order["self"] == min(order.values()), table
        assert list(order.values()).count(order["self"]) == 1, table


async def test_a_failed_read_costs_the_block_not_the_session():
    """Background, never the ground for a claim: a raising store leaves the
    session grounded without it."""

    class _Boom:
        async def head(self, *, key: str):
            raise RuntimeError("postgres is away")

    async def _no_notes(unit):
        return ()

    async def _no_events(unit, conversation_id):
        return ()

    class _Records:
        async def by_refs(self, refs, rooms):
            return ()

        async def own_sends(self, *a, **kw):
            return ()

    ground = build_grounder(
        records=_Records(), persons=None, media_join=None,
        note_index=_no_notes, events_in=_no_events,
        lineage=LineagePolicy(),
        unit_rooms=lambda unit: set(),
        self_account=lambda unit: _Boom().head(key=nora_character_key(unit)),
    )
    grounding = await ground(UNIT, (), ())
    assert grounding.self_account is None


async def test_the_grounder_hands_the_session_her_account():
    culture = InMemoryCultureStore()
    await culture.append(
        key=nora_character_key(UNIT), body="she keeps it short here",
        written_at=1, established_by="character-nora")

    async def _none(*a, **kw):
        return ()

    class _Records:
        async def by_refs(self, refs, rooms):
            return ()

        async def own_sends(self, *a, **kw):
            return ()

    ground = build_grounder(
        records=_Records(), persons=None, media_join=None,
        note_index=_none, events_in=_none, lineage=LineagePolicy(),
        unit_rooms=lambda unit: set(),
        self_account=lambda unit: culture.head(key=nora_character_key(unit)),
    )
    grounding = await ground(UNIT, (), ())
    assert grounding.self_account is not None
    assert grounding.as_dict()["self_revision_id"] == grounding.self_account.id


def _cognition_prompt(grounding: Grounding | None) -> str:
    return render_agenda(
        [], grounding=grounding, now_ms=int(NOW.timestamp() * 1000),
        last_session_ms=None,
    )


def test_a_cognition_prompt_opens_with_her_account_before_the_clock():
    prompt = _cognition_prompt(Grounding(self_account=_rev("she keeps it short here")))
    assert prompt.startswith("<self ")
    assert prompt.index("<self ") < prompt.index("<clock")
    assert "she keeps it short here" in prompt


def test_a_cognition_prompt_has_no_self_block_without_an_account():
    assert "<self" not in _cognition_prompt(Grounding())
    assert "<self" not in _cognition_prompt(None)
