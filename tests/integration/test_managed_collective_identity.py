"""Generated SQL composition proves selected collective membership, names and consent."""

from datetime import UTC, datetime

import pytest

from nora.app.composition_dream import _character_adaptors, _profile_adaptors
from nora.domain.profile_inputs import PersonSourceRevision
from nora.obs.metrics import Metrics
from tests.infra.test_profile_source_inputs import message
from tests.integration.test_character_social import _record
from tests.integration.test_character_social import social as _sql_social
from tests.unit.test_managed_background_access import _background_identity_case, _population_rooms
from tests.unit.test_scoped_engagement_names import _M_ROOM, _M_SIBLING

social = _sql_social
pytestmark = pytest.mark.asyncio


def _collective(case, engine, config, kind, media_join=None, policy=None):
    config = config.model_copy(deep=True)
    config.memory.profile.write = True
    persons = case.access.persons_view()
    policy = policy or _population_rooms()
    rooms = policy.rooms()
    for room in rooms:
        room.absorbed = True
    policy.rooms = lambda: rooms
    args = {"allowlist": policy, "cfg": config, "content_checks": False,
            "engine": engine, "metrics": Metrics(), "persons": persons, "access": case.access}
    character = _character_adaptors(**args, note_store=None)
    if kind == "character-nora":
        adaptor = next(a for a in character.adaptors if a.name == "character-nora")
        return adaptor, adaptor.keys()[0]
    wiring = _profile_adaptors(**args, character=character,
        clock=lambda: datetime.now(UTC), media_join=media_join, profile_index=False)
    adaptor = next(a for a in wiring.adaptors if a.name == "profile-" + kind)
    key = "g1" if kind == "room" else adaptor.keys()[0]
    return adaptor, key


def _social(batch):
    return {r.social.item_id for r in batch
            if isinstance(r, PersonSourceRevision) and r.social is not None}


@pytest.mark.parametrize("kind", ["room", "unit", "nora"])
async def test_composed_collective_reads_current_members_and_consent(social, example_config, kind):
    store, engine = social
    case = await _background_identity_case()
    person, peer, outsider = case.people
    items = ("generated-member", "generated-peer", "generated-sibling")
    for pid, item in zip(case.people, items, strict=True):
        await _record(store, item, person=pid, caption=item)
    case.registry.identities[peer]["traits"]["social"] = False
    adaptor, key = _collective(case, engine, example_config, kind)
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    assert _social(batch) == {"generated-member"}
    case.registry.identities[peer]["traits"]["social"] = True
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    assert _social(batch) == {"generated-member", "generated-peer"}
    await case.bindings.set_membership(person_id=person, scope=_M_ROOM,
                                      member=False, source="generated")
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    assert _social(batch) == {"generated-peer"}
    case.bindings.down = True
    with pytest.raises(RuntimeError):
        await adaptor.evidence.newest(key=key, after=0, limit=20)
    case.bindings.down = False
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    assert _social(batch) == {"generated-peer"}
    assert outsider not in {r.subject for r in batch if isinstance(r, PersonSourceRevision)}


@pytest.mark.parametrize("kind", ["room", "unit", "nora"])
async def test_composed_collective_names_and_held_social_use_current_scope(
    social, example_config, kind,
):
    store, engine = social
    case = await _background_identity_case()
    person = case.people[0]
    await _record(store, "generated-private-post", person=person,
                  caption="Generated Collective Private Caption")
    await message(engine, person=person, room="g1", body="Generated Collective Chat")
    await case.bindings.set_scoped_name(person_id=person, scope=_M_ROOM,
        nickname="Generated Collective Room Name", operation_id="generated-collective-room")
    await case.bindings.set_scoped_name(person_id=person, scope=_M_SIBLING,
        nickname="Generated Collective Sibling Secret", operation_id="generated-collective-sibling")
    adaptor, key = _collective(case, engine, example_config, kind)
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    # Use an actual room read as shared context for all three collective kinds.
    from nora.infra.sql.character import SqlCharacterEvidence
    chat, _ = await SqlCharacterEvidence(engine).room_since(
        rooms=[("signal", "g1")], after=0, limit=20)
    text = await adaptor.inputs(key=key, batch=[*chat, *batch])
    expected = "Generated Collective Room Name" if kind == "room" else "Generated Unit Name 0"
    assert expected in text
    assert "Generated Collective Private Caption" in text
    assert "Generated Collective Sibling Secret" not in text
    case.registry.identities[person]["traits"]["social"] = False
    hidden = await adaptor.inputs(key=key, batch=[*chat, *batch])
    assert "Generated Collective Chat" in hidden
    assert "Generated Collective Private Caption" not in hidden
    case.registry.identities[person]["traits"]["social"] = True
    await case.bindings.set_membership(person_id=person, scope=_M_ROOM,
                                      member=False, source="generated")
    departed = await adaptor.inputs(key=key, batch=[*chat, *batch])
    assert "Generated Collective Chat" in departed
    assert "Generated Collective Private Caption" not in departed
    case.bindings.down = True
    with pytest.raises(RuntimeError):
        await adaptor.inputs(key=key, batch=chat)
    case.bindings.down = False
    assert expected in await adaptor.inputs(key=key, batch=chat)


@pytest.mark.parametrize("change", ["consent", "membership"])
@pytest.mark.parametrize("kind", ["room", "unit", "nora"])
async def test_collective_rechecks_consent_after_awaited_enrichment(
    social, example_config, kind, change,
):
    store, engine = social
    case = await _background_identity_case()
    person = case.people[0]
    await _record(store, "generated-late-private", person=person, caption="Generated Late Private")
    await message(engine, person=person, room="g1", body="Generated Late Chat")

    class RevokeOnRead:
        async def enrich(self, rows, **kwargs):
            if change == "consent":
                case.registry.identities[person]["traits"]["social"] = False
            else:
                await case.bindings.set_membership(person_id=person, scope=_M_ROOM,
                                                  member=False, source="generated")
            return rows

    adaptor, key = _collective(case, engine, example_config, kind, RevokeOnRead())
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    assert "generated-late-private" in _social(batch)
    with pytest.raises(RuntimeError, match="social eligibility changed"):
        await adaptor.inputs(key=key, batch=batch)


async def test_composed_character_nora_names_replies_in_its_own_unit(social, example_config):
    from dataclasses import replace

    from nora.domain.culture import Episode
    from nora.infra.sql.character import SqlCharacterEvidence
    from tests.unit.test_scoped_engagement_names import _M_UNIT

    _, engine = social
    case = await _background_identity_case()
    person = case.people[0]
    await message(engine, person=person, room="g1", body="Generated Episode Reply")
    rows, _ = await SqlCharacterEvidence(engine).room_since(
        rooms=[("signal", "g1")], after=0, limit=20)
    episode = Episode(send_id=99, room="g1", text="Generated Nora Question",
        outcome="responded", reactions=0, edits=0, replies=(rows[0].row,))
    adaptor, key = _collective(case, engine, example_config, "character-nora")
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


async def test_selected_file_collective_uses_declared_members_without_managed_services(
    social, example_config,
):
    from nora.access import IdentityAccess
    from nora.domain.models import PersonRef
    from nora.persons import Persons

    store, engine = social
    case = await _background_identity_case()
    person = case.people[0]
    persons = Persons(by_person={person: PersonRef(
        person_id=person, name="Generated File Collective Name", honcho_peer_id=person)}, index={})
    case.access = IdentityAccess.for_files(persons, None)
    case.bindings.down = True
    policy = _population_rooms()
    rooms = policy.rooms()
    rooms[0].persons = frozenset({person})
    policy.rooms = lambda: rooms
    await _record(store, "generated-file-collective", person=person,
                  caption="Generated File Collective Caption")
    await message(engine, person=person, room="g1", body="Generated File Collective Chat")
    adaptor, key = _collective(case, engine, example_config, "room", policy=policy)
    batch, _ = await adaptor.evidence.newest(key=key, after=0, limit=20)
    assert _social(batch) == {"generated-file-collective"}
    text = await adaptor.inputs(key=key, batch=batch)
    assert "Generated File Collective Caption" in text
    assert "Generated File Collective Name" in text
    assert "Generated File Collective Chat" in text
