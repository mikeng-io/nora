"""The `<self>` block: her own Character · Nora account for the turn's unit
(DEC-292).

What must hold, each with the way it would fail:

- the three occasions read it from the unit's key (`nora_character_key`), and
  only that key: a room's, a unit's or a person's account is not hers;
- a unit with no account renders NO block, on every occasion, not an empty one;
- on the tag and fire turns it is the first block, and on a Cognition turn it
  is the first block of the prompt;
- `<self_card>` is on no prompt.
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


_POLICY_TOML = """
default = "deny"

[[conversations]]
platform = "signal"
id = "g1"
label = "On"
persons = "*"
unit = "friends"
location = true

[[conversations]]
platform = "whatsapp"
id = "g2"
label = "Off"
persons = "*"
unit = "friends"

[[conversations]]
platform = "signal"
id = "w1"
label = "Quiet one"
persons = "*"
unit = "quiet"

[[conversations]]
platform = "discord"
id = "100000000000000001/100000000000000002"
label = "Quiet two"
persons = "*"
unit = "quiet"

[[conversations]]
platform = "signal"
id = "o1"
label = "Open one"
persons = "*"
unit = "open"
location = true
"""


def _policy(tmp_path):
    from nora.policy import load_policy

    f = tmp_path / "policy.toml"
    f.write_text(_POLICY_TOML)
    return load_policy(f)


def _fire_path(culture, policy) -> FirePath:
    path = object.__new__(FirePath)
    path._culture = culture
    path._policy = policy
    path._metrics = Metrics()
    path._standing_store_timeout_seconds = 5.0
    # Pre-switch (45 §5): no Profile reader wired, so the culture reads run.
    path._profile_reader = None
    return path


def _participation(platform: str = "signal", conversation_id: str = "g1") -> Event:
    return Event(
        kind="participation", platform=platform, conversation_id=conversation_id,
        conversation_type="group", person_id=None, body="",
        header=lambda ref, now: "",
    )


async def test_the_fire_path_reads_her_account_by_the_units_nora_key(tmp_path):
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

    got = await _fire_path(culture, _policy(tmp_path))._read_self_account(_participation())

    assert got is not None and got.body == "she keeps it short here"
    assert culture.reads == [nora_character_key(UNIT)]


async def test_a_unit_with_no_account_reads_none_on_the_fire_path(tmp_path):
    policy = _policy(tmp_path)
    assert await _fire_path(InMemoryCultureStore(), policy)._read_self_account(
        _participation()) is None
    assert await _fire_path(None, policy)._read_self_account(_participation()) is None


async def test_the_switch_reads_no_self_account_on_the_fire_path(tmp_path):
    """45 §5: a wired Profile reader replaces the read entirely — no culture
    read is made, so a raising store is never even asked."""
    culture = InMemoryCultureStore()
    await culture.append(
        key=nora_character_key(UNIT), body="she keeps it short here",
        written_at=1, established_by="character-nora")
    culture.reads.clear()
    path = _fire_path(culture, _policy(tmp_path))
    path._profile_reader = object()  # wired: the account itself is not needed

    assert await path._read_self_account(_participation()) is None
    assert culture.reads == []


def test_no_account_renders_no_block_not_an_empty_one():
    assert render_self(None) is None
    assert render_self(_rev("  \n ")) is None


def test_the_self_block_is_the_first_row_of_the_tag_and_cognition_tables():
    for table in (PREFILL_BLOCKS, COGNITION_BLOCKS):
        order = {name: o for name, o, _key in table}
        assert order["self"] == min(order.values()), table
        # The switch (45 §5): `<profile of="nora">` replaces `<self>` at the SAME
        # row, and the grounder fills one or the other, never both.
        first = {name for name, o in order.items() if o == order["self"]}
        assert first <= {"self", "profile"}, table


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


# ---- R-31: the account is unit-wide, so a room shows it only where it may -----


def test_a_room_shows_her_account_only_where_it_may(tmp_path):
    p = _policy(tmp_path)
    # mixed unit: the location-ON room shows it, the location-OFF room does not
    assert p.shows_self_in("signal", "g1") is True
    assert p.shows_self_in("whatsapp", "g2") is False
    # a unit with no location room at all shows it everywhere
    assert p.shows_self_in("signal", "w1") is True
    assert p.shows_self_in("discord", "100000000000000001/100000000000000002") is True
    # a unit that is all location-on shows it
    assert p.shows_self_in("signal", "o1") is True
    # an unknown room never does
    assert p.shows_self_in("signal", "nowhere") is False


def test_a_unit_scoped_prompt_shows_it_only_when_every_room_agrees(tmp_path):
    p = _policy(tmp_path)
    assert p.shows_self_to_unit("friends") is False      # mixed
    assert p.shows_self_to_unit("quiet") is True         # all off
    assert p.shows_self_to_unit("open") is True          # all on
    assert p.shows_self_to_unit("no-such-unit") is False


async def test_a_location_off_room_of_a_mixed_unit_reads_no_account_on_fire(tmp_path):
    culture = InMemoryCultureStore()
    await culture.append(
        key=nora_character_key("friends"), body="she keeps it short here",
        written_at=1, established_by="character-nora")
    culture.reads.clear()
    path = _fire_path(culture, _policy(tmp_path))

    off = await path._read_self_account(_participation("whatsapp", "g2"))
    on = await path._read_self_account(_participation("signal", "g1"))

    assert off is None and culture.reads == [nora_character_key("friends")]
    assert on is not None and on.body == "she keeps it short here"


async def test_the_cognition_reader_withholds_a_mixed_units_account(tmp_path):
    from nora.app.cognition_grounding import unit_self_reader

    culture = InMemoryCultureStore()
    for unit in ("friends", "quiet", "open"):
        await culture.append(
            key=nora_character_key(unit), body=f"{unit} account", written_at=1,
            established_by="character-nora")
    read = unit_self_reader(culture, _policy(tmp_path))
    assert read is not None
    assert read("friends") is None
    assert (await read("quiet")).body == "quiet account"
    assert (await read("open")).body == "open account"
    assert unit_self_reader(None, _policy(tmp_path)) is None


async def test_a_location_off_room_of_a_mixed_unit_reads_no_account_on_tag(tmp_path):
    from nora.engagement.context import TranscriptInjection

    culture = InMemoryCultureStore()
    await culture.append(
        key=nora_character_key("friends"), body="she keeps it short here",
        written_at=1, established_by="character-nora")
    assembler = object.__new__(TranscriptInjection)
    assembler._culture_reader = culture
    assembler._policy = _policy(tmp_path)
    assembler._metrics = Metrics()
    assembler._profile_reader = None

    def msg(platform, cid):
        return type("_Msg", (), {"platform": platform, "conversation_id": cid})()

    assert await assembler._self_account(msg("whatsapp", "g2")) is None  # type: ignore[arg-type]
    got = await assembler._self_account(msg("signal", "g1"))  # type: ignore[arg-type]
    assert got is not None and got.body == "she keeps it short here"
