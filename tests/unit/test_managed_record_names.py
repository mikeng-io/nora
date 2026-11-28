"""Selected managed names reach actual transcript read tools."""

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
from tests.unit.record_query_support import NOW, ROOM, _Record, _stream
from tests.unit.record_query_support import policy as policy

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






