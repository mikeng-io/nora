"""Selected managed names reach actual transcript read tools."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import DeliveryStatus, NormalizedMessage
from nora.engagement.self_presence import PresenceReader
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.people.app.managed import ManagedServices
from nora.testing import FakeRecordStore, FakeRosterSource
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from tests.fakes import SinkSender
from tests.unit.test_cognition_record_query_ids import NOW, ROOM, _Record, _stream
from tests.unit.test_cognition_record_query_ids import policy as policy

PERSON = 'p1'
SCOPE = Scope(ScopeKind.ROOM, 'signal/' + ROOM)
NICK = 'Generated Scoped Author'


async def _services(bindings=None):
    bindings, backend = bindings or FakeBindingStore(), FakeAuthorityBackend()
    services = ManagedServices(bindings, backend, FakeTopologySource(
        rooms={SCOPE.id: ('u1', False), 'signal/room-b': ('u1', False),
               'signal/room-c': ('u1', False)}, units={'u1': 'default'}, workspaces={'default'}),
        FakeIdentityRegistry(), FakeLinkRequests())
    account = Identifier('signal', 'phone', '+85200000001')
    assert (await bindings.register(
        person=ManagedPerson(PERSON, PersonLifecycle.ACTIVE, display_name='Generated Global'),
        account=ManagedAccount('generated-binding', PERSON, 'signal', frozenset({account}),
                               AccountState.BOUND),
        membership=ManagedMembership(PERSON, SCOPE, MembershipState.MEMBER, 'generated'),
    )).status is OutcomeStatus.COMPLETE
    await bindings.set_scoped_name(person_id=PERSON, scope=SCOPE, nickname=NICK,
                                   operation_id='generated-name')
    return services


async def _tools(policy, *, on_rows=None, services=None, record=None):
    services = services or await _services()
    room = Room(platform='signal', conversation_id=ROOM,
                roster_source=FakeRosterSource(groups={ROOM: (frozenset({'+85200000001'}),)}),
                access=services.access)
    store = FakeRecordStore()
    await store.append(message=NormalizedMessage(
        'signal', ROOM, 'group', '+85200000001', 'Archived Platform Name',
        int(NOW.timestamp() * 1000) - 3600000, 'we are going', {}),
        person_id=PERSON, delivery_status=DeliveryStatus.SENT)
    tools = build_tools(
        store=store, conversation_id=ROOM, conversation_type='group',
        persons=services.access.persons_view(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
        transit_source=TransitClient(kmb_base_url='http://127.0.0.1:1',
            mtr_schedule_url='http://127.0.0.1:1/s', mtr_routes_url='http://127.0.0.1:1/r.csv',
            timeout_seconds=0.5),
        clock=lambda: NOW, record=record or _Record(),
        rooms=PresenceReader(policy=policy, adapters={}), lineage=LineagePolicy.from_policy(policy),
        on_rows=on_rows)
    return services, {t.tool_name: t for t in tools}, room


async def test_record_query_renders_the_selected_room_name_and_refreshes_after_rename(policy):
    services, tools, _ = await _tools(policy)
    answer = await _stream(
        tools['record_query'], start=(NOW - timedelta(days=1)).date().isoformat())
    assert answer['status'] == 'ok'
    assert answer['messages'][0]['sender'] == NICK
    await services.bindings.set_scoped_name(person_id=PERSON, scope=SCOPE,
        nickname='Generated Renamed Author', operation_id='generated-rename')
    renamed = await _stream(tools['record_query'], start='2026-09-28')
    assert renamed['messages'][0]['sender'] == 'Generated Renamed Author'


async def test_record_query_does_not_emit_a_stale_author_during_identity_outage(policy):
    services, tools, _ = await _tools(policy)
    positive = await _stream(tools['record_query'], start='2026-09-28')
    assert positive['status'] == 'ok'
    services.bindings.down = True
    refused = await _stream(tools['record_query'], start='2026-09-28')
    assert refused['status'] == 'unavailable'
    assert 'messages' not in refused or not refused['messages']


async def test_a_name_actually_handed_out_remains_private_after_rename(policy, example_config):
    from tests.unit.test_managed_memory_scope import _screened_lookup

    services = await _services()
    call, calls, state = await _screened_lookup(
        example_config, services.access, persons=(PERSON,))
    observed = []

    def on_rows(rows):
        observed.extend(rows)
        state.remember_rows(rows)

    services, tools, _ = await _tools(policy, on_rows=on_rows, services=services)
    read = await _stream(tools['record_query'], start='2026-09-28')
    assert read['messages'][0]['sender'] == NICK
    assert observed[0].person_name == NICK and observed[0].person_id == PERSON
    await services.bindings.set_scoped_name(person_id=PERSON, scope=SCOPE,
        nickname='Generated Later Author', operation_id='generated-later-name')
    assert (await call(NICK + ' news'))['status'] == 'refused'
    assert calls == []
    assert (await call('volleyball news'))['status'] == 'ok'
    assert calls == ['volleyball news']


async def test_a_deleted_rows_shown_name_is_private_without_making_its_body_citable(
    policy, example_config,
):
    from tests.unit.test_managed_memory_scope import _screened_lookup

    class DeletedRecord(_Record):
        async def query(self, *args, **kwargs):
            hits, more, near = await super().query(*args, **kwargs)
            return tuple(replace(hit, deleted=True) for hit in hits), more, near

    services = await _services()
    call, calls, state = await _screened_lookup(
        example_config, services.access, persons=())
    observed = []

    def on_rows(rows):
        observed.extend(rows)
        state.remember_rows(rows)

    _, tools, _ = await _tools(
        policy, on_rows=on_rows, services=services, record=DeletedRecord())
    read = await _stream(tools['record_query'], start='2026-09-28')
    assert read['messages'][0]['sender'] == NICK
    assert read['messages'][0]['body'] is None
    await services.bindings.set_scoped_name(person_id=PERSON, scope=SCOPE,
        nickname='Generated Changed Deleted Author', operation_id='generated-deleted-rename')
    assert (await call(NICK + ' news'))['status'] == 'refused'
    assert observed and all(row.body is None for row in observed)
    assert PERSON in state.persons and not state.fetched.sources
    assert calls == []
    assert (await call('volleyball news'))['status'] == 'ok'


@pytest.mark.parametrize('tool_name,arguments', [
    ('load_transcript', {}), ('room_read', {'room': 'A room'}),
])
async def test_transcript_tools_use_current_selected_names_and_refuse_outages(
    policy, tool_name, arguments,
):
    services, tools, _ = await _tools(policy)
    answer = await _stream(tools[tool_name], **arguments)
    assert answer.get('status', 'ok') == 'ok', answer
    assert answer['messages'][0]['sender'] == NICK
    services.bindings.down = True
    refused = await _stream(tools[tool_name], **arguments)
    assert refused['status'] == 'unavailable'
    assert 'messages' not in refused or not refused['messages']


async def test_room_author_lookup_uses_the_current_scoped_name_on_each_read(policy):
    services, tools, _ = await _tools(policy)
    positive = await _stream(tools['record_query'], start='2026-09-28', author=NICK)
    assert positive['status'] == 'ok', positive
    await services.bindings.set_scoped_name(person_id=PERSON, scope=SCOPE,
        nickname='Generated Current Alias', operation_id='generated-author-rename')
    renamed = await _stream(
        tools['record_query'], start='2026-09-28', author='Generated Current Alias')
    assert renamed['status'] == 'ok', renamed


async def test_a_same_person_name_added_during_identity_reads_refuses_before_egress(
    policy, example_config,
):
    from tests.unit.test_managed_memory_scope import _screened_lookup

    class PausingStore(FakeBindingStore):
        def __init__(self):
            super().__init__()
            self.pause = False
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def person(self, person_id):
            if self.pause:
                self.pause = False
                self.entered.set()
                await self.release.wait()
            return await super().person(person_id)

    store = PausingStore()
    services = await _services(store)
    observed = []
    _, tools, _ = await _tools(policy, services=services, on_rows=observed.extend)
    assert (await _stream(tools['record_query'], start='2026-09-28'))['status'] == 'ok'
    call, calls, state = await _screened_lookup(
        example_config, services.access, persons=(PERSON,))
    store.pause = True
    pending = asyncio.create_task(call('volleyball news'))
    try:
        await asyncio.wait_for(store.entered.wait(), timeout=2)
        state.remember_rows((replace(observed[0], person_name='Generated Concurrent Alias'),))
    finally:
        store.release.set()
    assert (await asyncio.wait_for(pending, timeout=2))['status'] == 'unavailable'
    assert calls == []
    assert (await call('volleyball news'))['status'] == 'ok'
    assert calls == ['volleyball news']


async def test_a_later_read_uses_its_actual_room_and_only_shown_names_stay_private(
    tmp_path, example_config,
):
    from nora.policy import load_policy
    from tests.unit.test_cognition_record_query_ids import _POLICY
    from tests.unit.test_managed_memory_scope import _screened_lookup

    path = tmp_path / 'policy.toml'
    path.write_text(_POLICY + '''
[[conversations]]
platform = "signal"
id = "room-b"
unit = "u1"
label = "B room"
persons = ["p1"]
[[conversations]]
platform = "signal"
id = "room-c"
unit = "u2"
label = "C room"
persons = ["p1"]
''')
    policy = load_policy(path)

    class OtherRoomRecord(_Record):
        async def query(self, q, **kwargs):
            assert ('signal', 'room-b') in q.rooms
            assert ('signal', 'room-c') not in q.rooms
            hits, more, near = await super().query(q, **kwargs)
            return tuple(replace(hit, conversation_id='room-b') for hit in hits), more, near

    services = await _services()
    other = Scope(ScopeKind.ROOM, 'signal/room-b')
    await services.bindings.set_scoped_name(person_id=PERSON, scope=other,
        nickname='Generated Other Room Author', operation_id='generated-other-room')
    await services.bindings.set_scoped_name(person_id=PERSON,
        scope=Scope(ScopeKind.ROOM, 'signal/room-c'), nickname='Generated Unread Author',
        operation_id='generated-unread-room')
    call, calls, state = await _screened_lookup(
        example_config, services.access, persons=(PERSON,), scopes=(SCOPE,))
    _, tools, _ = await _tools(
        policy, services=services, record=OtherRoomRecord(), on_rows=state.remember_rows)
    read = await _stream(tools['record_query'], start='2026-09-28', scope='all_rooms')
    assert read['status'] == 'ok', read
    assert read['messages'][0]['sender'] == 'Generated Other Room Author'
    await services.bindings.set_scoped_name(person_id=PERSON, scope=other,
        nickname='Generated New Other Room Author', operation_id='generated-other-room-rename')
    assert (await call('Generated Other Room Author news'))['status'] == 'refused'
    assert calls == []
    assert (await call('Generated Unread Author news'))['status'] == 'ok'
    assert calls == ['Generated Unread Author news']


@pytest.mark.parametrize(("person_name", "sender_name", "expected"), [
    ("\u00a0", "Generated\nAccount", {"Generated Account"}),
    (None, None, set()),
    (" \n", "\t", set()),
    ("unknown", None, {"unknown"}),
    ("Generated Canonical", "Other Label", {"Generated Canonical"}),
])
def test_name_custody_uses_supplied_canonical_speaker_labels(person_name, sender_name, expected):
    from types import SimpleNamespace

    from nora.agent.comprehend_tools import LookupState
    from nora.agent.fetch_watch import GraphFetched

    state = LookupState(GraphFetched(), "generated-unit", 1, None, None, ())
    state.remember_rows((SimpleNamespace(person_id="generated-source", is_nora=False,
                                       person_name=person_name, sender_name=sender_name),))
    assert state.seen_names == expected
    assert state.persons == ("generated-source",)
