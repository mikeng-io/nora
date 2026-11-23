"""Generated SQL composition proves a collective account names people in its own unit."""

import pytest

from nora.app.composition_dream import _character_adaptors
from nora.obs.metrics import Metrics
from tests.integration.test_character_social import _message
from tests.integration.test_character_social import social as _sql_social
from tests.unit.test_managed_background_access import _background_identity_case, _population_rooms

social = _sql_social
pytestmark = pytest.mark.asyncio


def _character_nora(case, engine, config):
    persons = case.access.persons_view()
    policy = _population_rooms()
    rooms = policy.rooms()
    for room in rooms:
        room.absorbed = True
    policy.rooms = lambda: rooms
    character = _character_adaptors(
        allowlist=policy, cfg=config, content_checks=False, engine=engine,
        metrics=Metrics(), persons=persons, access=case.access, note_store=None)
    adaptor = next(a for a in character.adaptors if a.name == "character-nora")
    return adaptor, adaptor.keys()[0]


async def test_composed_character_nora_names_replies_in_its_own_unit(social, example_config):
    from dataclasses import replace

    from nora.domain.culture import Episode
    from nora.infra.sql.character import SqlCharacterEvidence
    from tests.unit.test_scoped_engagement_names import _M_UNIT

    _, engine = social
    case = await _background_identity_case()
    person = case.people[0]
    await _message(engine, person=person, room="g1", body="Generated Episode Reply")
    rows, _ = await SqlCharacterEvidence(engine).room_since(
        rooms=[("signal", "g1")], after=0, limit=20)
    episode = Episode(send_id=99, room="g1", text="Generated Nora Question",
        outcome="responded", reactions=0, edits=0, replies=(rows[0].row,))
    adaptor, key = _character_nora(case, engine, example_config)
    text = await adaptor.inputs(key=key, batch=[episode])
    assert "Generated Unit Name 0" in text and "Generated Episode Reply" in text
    await case.bindings.set_scoped_name(person_id=person, scope=_M_UNIT,
        nickname="Generated Current Episode Name", operation_id="generated-episode-rename")
    assert "Generated Current Episode Name" in await adaptor.inputs(key=key, batch=[episode])
    assert await adaptor.inputs(key=key, batch=[replace(episode, room="g3")]) == ""
    case.bindings.down = True
    with pytest.raises(RuntimeError):
        await adaptor.inputs(key=key, batch=[episode])
    case.bindings.down = False
    assert "Generated Current Episode Name" in await adaptor.inputs(key=key, batch=[episode])
