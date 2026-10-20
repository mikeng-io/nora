"""Owning-store revision delivery with indexing off, through real Postgres."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona_inputs import SqlPersonaInputs
from nora.infra.sql.schema import (
    EVENTS,
    MEDIA_OBJECTS,
    MEMO,
    MEMO_REVISION,
    MESSAGE_EDIT,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    ROLLUP_BUILD,
    ROLLUP_TIE,
    SOCIAL_ITEM,
    SOCIAL_ITEM_COUNT,
    SOCIAL_ITEM_STATE,
)

T0 = 1_790_000_000_000
AT = datetime.fromtimestamp(T0 / 1000, UTC)
ROOMS = [('signal', 'room-a')]
KEY = 'character:unit:person-a'
PERSON = 'person-a'


@pytest.fixture
async def env(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def source_inputs(engine, *, opted_out=()):
    from nora.domain.models import PersonRef
    from nora.infra.sql.social_stream import readable_social_store
    from nora.persons import Persons
    persons = Persons(by_person={PERSON: PersonRef(person_id=PERSON, name='Synthetic',
        honcho_peer_id='synthetic')}, index={}, _social_off=frozenset(opted_out))
    return SqlPersonaInputs(engine, social=readable_social_store(engine, persons))


async def message(engine, *, ident=10, person=PERSON, room='room-a', body='a qualified intention',
                  media=None, own=False, ts=T0, quoted_ts=None):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGES).values(
            id=ident, platform='signal', conversation_id=room, conversation_type='group',
            sender_id='synthetic-sender',
            sender_name='Synthetic', person_id=person, ts=ts, body=body, quoted_ts=quoted_ts,
            is_nora=int(own), delivery_status='sent', media_json=media,
        ))


@pytest.mark.parametrize('kind', ['image', 'video', 'sticker'])
async def test_late_cached_reading_after_cursor_and_restart_is_pending_once(env, kind):
    sha = 'a' * 64
    await message(env, body=f'[{kind} aaaaaa]', media=json.dumps([{'sha256': sha}]))
    source = source_inputs(env)
    first = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10)
    assert len(first) == 1 and first[0].roots[0].row.id == 10
    await source.settle(key=KEY, revisions=first)
    assert await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10) == []
    async with env.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(
            sha256=sha, media_type='image/png', size=1, source='inbound', created_at_ms=T0,
            description='synthetic cached scene',
        ))
    restarted = source_inputs(env)
    late = await restarted.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10)
    assert len(late) == 1 and late[0].revision != first[0].revision
    assert late[0].roots[0].row.ts == T0
    # No acknowledgement: another restart must retain the same work.
    assert await source_inputs(env).messages(
        key=KEY, rooms=ROOMS, person=PERSON, limit=10) == late
    await restarted.settle(key=KEY, revisions=late)
    assert await restarted.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10) == []
    async with env.begin() as conn:
        await conn.execute(sa.insert(MESSAGE_EDIT).values(
            message_id=10, ts=T0 + 1, body='revised intention',
        ))
    [edited] = await restarted.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10)
    assert edited.roots[0].row.body == 'revised intention'
    assert edited.roots[0].row.ts == T0


@pytest.mark.parametrize('kind', ['post', 'reel', 'story_frame'])
async def test_social_late_media_counts_and_restrictions_are_rechecked(env, kind):
    sha = 'b' * 64
    async with env.begin() as conn:
        await conn.execute(sa.insert(SOCIAL_ITEM).values(
            network='synthetic', item_id='item-a', person=PERSON, account='author',
            kind=kind, taken_at=AT, expires_at=AT, seen_at=AT, origin='poll', media_ref=sha,
            caption='synthetic caption',
        ))
    source = source_inputs(env)
    first = await source.social(key=KEY, persons=[PERSON], limit=6)
    assert first[0].social.kind == kind and not first[0].social.readings[0].description
    await source.settle(key=KEY, revisions=first)
    async with env.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(
            sha256=sha, media_type='image/png', size=1, source='inbound', created_at_ms=T0,
            description='late social scene',
        ))
        await conn.execute(sa.insert(SOCIAL_ITEM_COUNT).values(
            network='synthetic', item_id='item-a', likes=3, comments=None,
            reposts=None, likes_hidden=False,
        ))
    [late] = await source.social(key=KEY, persons=[PERSON], limit=6)
    assert late.social.readings[0].description == 'late social scene'
    assert late.counts.likes == 3 and late.counts.comments is None
    assert late.social.taken_at == AT
    await source.settle(key=KEY, revisions=[late])
    [opted] = await source_inputs(env, opted_out=[PERSON]).social(
        key=KEY, persons=[PERSON], limit=6, citations=['social:synthetic:item-a'])
    assert opted.state == 'withheld' and opted.social is None and opted.counts is None
    async with env.begin() as conn:
        await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
            network='synthetic', item_id='item-a', state='hidden',
        ))
    [hidden] = await source.social(key=KEY, persons=[PERSON], limit=6,
                                   citations=['social:synthetic:item-a'])
    assert hidden.social is None and hidden.counts is None
    assert 'late social scene' not in repr(hidden)


async def test_register_states_use_actual_human_roots_without_notebook_prose(env):
    await message(env)
    await message(env, ident=11, own=True, body='her repeated conclusion')
    await message(env, ident=12, room='other-room', body='withheld other-room root')
    note, head, memo, memo_head, event = [str(uuid.uuid4()) for _ in range(5)]
    async with env.begin() as conn:
        await conn.execute(sa.insert(NOTE).values(
            id=note, unit='unit', title='untyped location prose', description='do not read prose',
            uncertainty='?', closing_condition='settled', anchor_entity_ids=[PERSON],
            established_in='room-a', created_at=T0, head_revision_id=head,
        ))
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=head, note_id=note, body='a held Persona repeated here is not evidence',
            written_at=T0 + 1, established_by='synthetic', citations=['10', '11', '12', 'P1'],
        ))
        await conn.execute(sa.insert(MEMO).values(
            id=memo, unit='unit', platform='signal', conversation_id='room-a', created_at=T0,
        ))
        await conn.execute(sa.insert(MEMO_REVISION).values(
            id=memo_head, memo_id=memo, seq=1, title='a settled thing', body='do not trust prose',
            mode='deduced', state='kept', citations=['10'], written_at=T0, established_by='test',
        ))
        await conn.execute(sa.insert(EVENTS).values(
            id=uuid.UUID(event), unit_id='unit', conversation_id='room-a', title='private prose',
            created_by='test', created_at=AT, status='cancelled', people=[PERSON],
            when=AT, as_of=T0, field_meta={'title': {'reasoning': 'told', 'as_of': T0}},
        ))
    source = source_inputs(env)
    batch = await source.register(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=6)
    assert {r.state for r in batch} == {'open', 'kept', 'cancelled'}
    assert next(r for r in batch if r.state == 'cancelled').roots == ()
    assert {x.row.id for r in batch for x in r.roots} == {10}
    assert 'private prose' not in repr(batch) and 'untyped location prose' not in repr(batch)
    await source.settle(key=KEY, revisions=batch)
    assert await source.register(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=6) == []
    async with env.begin() as conn:
        await conn.execute(sa.update(NOTE).where(NOTE.c.id == note).values(retired_at=T0 + 2))
    [retired] = await source.register(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=6)
    assert retired.state == 'retired'


async def test_rollup_rebuild_same_roots_is_unchanged_but_changed_bounds_are_pending(env):
    await message(env, ident=9, person='person-b', ts=T0 - 1)
    await message(env, quoted_ts=T0 - 1)
    async def build(ident, until):
        async with env.begin() as conn:
            await conn.execute(sa.update(ROLLUP_BUILD).values(status='superseded'))
            await conn.execute(sa.insert(ROLLUP_BUILD).values(
                id=ident, status='current', code_version='synthetic', params_sha256='a' * 64,
                through_ms=until,
            ))
            await conn.execute(sa.insert(ROLLUP_TIE).values(
                build_id=ident, unit='unit', valid_from_ms=T0, valid_to_ms=until,
                as_of_ms=T0, lineage=[['message', None, 'signal:room-a']],
                person_a=PERSON, person_b='person-b', iso_week='2026-W38',
                replies_ab=1, replies_ba=0, mentions_ab=0, mentions_ba=0,
                reactions_ab=0, reactions_ba=0, colocation_ms=999,
            ))
    await build(1, T0 + 10)
    source = source_inputs(env)
    [first] = await source.rollup(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=6)
    assert dict(first.properties)['replies_ab'] == '1'
    assert 'colocation' not in repr(first) and first.roots[0].row.id == 10
    await source.settle(key=KEY, revisions=[first])
    await build(2, T0 + 10)
    assert await source.rollup(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=6) == []
    await build(3, T0 + 20)
    [changed] = await source.rollup(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=6)
    assert changed.effective_until == T0 + 20 and changed.roots == first.roots
    # Unit membership alone does not grant another room's Record roots.
    assert await source.rollup(key=KEY, unit='unit', rooms=[('signal', 'other-room')],
                               person=PERSON, limit=6) == []


@pytest.mark.parametrize('kind', ['image', 'video', 'sticker'])
@pytest.mark.parametrize('legacy_body', [None, '', 'original synthetic caption'])
async def test_initial_cached_recovery_past_old_cursor_is_bounded_and_durable(
    env, kind, legacy_body,
):
    from nora.agent.egress import scan_identifiers
    from nora.domain.media_join import MediaJoin
    from nora.domain.models import PersonRef
    from nora.domain.ports import Proposal
    from nora.infra.sql.character import SqlCharacterEvidence
    from nora.infra.sql.media_objects import SqlMediaObjectStore
    from nora.infra.sql.persona import SqlPersonaStore
    from nora.language.character import CharacterRoom, PersonKey
    from nora.language.persona import PersonaEvidence, PersonaPersonAdaptor
    from nora.obs.metrics import Metrics
    from nora.testing import InMemoryCultureStore

    sha = 'c' * 64
    body = f'[{kind} cccccc]' if legacy_body is None else legacy_body
    await message(env, body=body, media=json.dumps([{'sha256': sha,
        'media_type': {'image': 'image/png', 'video': 'video/mp4',
                       'sticker': 'image/webp'}[kind]}]))
    await message(env, ident=20, body='an old plain message')
    await message(env, ident=30, room='other-room', body='private other-room reading',
                  media=json.dumps([{'sha256': sha}]))
    # Cache PRE-DATES the first boot of the new revision mechanism.
    async with env.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(
            sha256=sha, media_type={'image': 'image/png', 'video': 'video/mp4',
                                   'sticker': 'image/webp'}[kind],
            size=1, source='inbound', created_at_ms=T0, description='existing synthetic scene',
        ))
    reads = SqlCharacterEvidence(env)
    boundary, _ = await reads.history_state(key=KEY)
    await reads.advance_history(key=KEY, cursor=boundary)  # old message history already completed
    pk = PersonKey(unit='unit', person=PersonRef(person_id=PERSON, name='Synthetic',
        honcho_peer_id='synthetic'), rooms=(CharacterRoom(key='room-a', platform='signal',
        label='Allowed'),), home='room-a', listed=('room-a',))
    persona = SqlPersonaStore(env, screen=scan_identifiers)
    source = source_inputs(env)
    # Read-only receipt neither freezes nor acknowledges source recovery.
    dry = await source.media_recovery(key=KEY, rooms=ROOMS, person=PERSON)
    assert dry['eligible_cached'] == 1 and dry['pending_cached'] == 1 and not dry['frozen']
    def evidence():
        return PersonaEvidence(store=reads, persona=persona, keys={KEY: pk},
                               wake_batch=100, source_inputs=source_inputs(env))
    first = evidence()
    assert await first.count(key=KEY, after=boundary) == 100
    batch, cursor = await first.newest(key=KEY, after=boundary, limit=1)
    assert cursor == boundary and [r.roots[0].row.id for r in batch if r.roots] == [10]
    adaptor = PersonaPersonAdaptor(
        persona=persona, culture=InMemoryCultureStore(), evidence=first, reads=reads,
        keys={KEY: pk}, name_of=lambda _: 'Synthetic', clock_ms=lambda: T0 + 1000,
        clock=lambda: datetime.fromtimestamp((T0 + 1000) / 1000, UTC),
        media_join=MediaJoin(store=SqlMediaObjectStore(env), screen=scan_identifiers,
                             metrics=Metrics()),
    )
    rendered = await adaptor.inputs(key=KEY, batch=batch)
    assert 'existing synthetic scene' in rendered and 'n="10"' in rendered
    assert 'old plain message' not in rendered and 'other-room reading' not in rendered
    assert adaptor._runs[KEY].citable.messages[10].ts == T0
    assert batch[0].roots[0].row.body == body
    from xml.etree import ElementTree
    root = ElementTree.fromstring('<input>' + rendered + '</input>')  # noqa: S314 - synthetic
    shown = json.loads(root.find('.//*[@n="10"]').text)
    assert shown['raw_caption'] == body
    # Before a durable account settlement, a new process still sees this work.
    again, _ = await evidence().newest(key=KEY, after=boundary, limit=1)
    assert again == batch
    _, no_change = adaptor.verbs(key=KEY)
    assert (await no_change(why='synthetic unchanged'))['status'] == 'ok'
    staged = await adaptor.accept(key=KEY, items=())
    await adaptor.commit(key=KEY, proposals=[Proposal(payload=p, instruction_sha256='a' * 64,
        model='synthetic', established_by='synthetic-session') for p in staged], now_ms=T0 + 1000)
    await first.settle(key=KEY)
    done = await source.media_recovery(key=KEY, rooms=ROOMS, person=PERSON)
    assert done['frozen'] and done['complete'] and done['pending_cached'] == 0
    assert await reads.history_state(key=KEY) == (boundary, boundary)
    after_restart, _ = await evidence().newest(key=KEY, after=boundary, limit=1)
    assert after_restart == []


def wired(engine, *, media_join=None):
    from nora.domain.models import PersonRef
    from nora.infra.sql.character import SqlCharacterEvidence
    from nora.infra.sql.persona import SqlPersonaStore
    from nora.language.character import CharacterRoom, PersonKey
    from nora.language.persona import PersonaEvidence, PersonaPersonAdaptor
    from nora.testing import InMemoryCultureStore

    reads = SqlCharacterEvidence(engine)
    persona = SqlPersonaStore(engine, screen=lambda _: None)
    source = source_inputs(engine)
    pk = PersonKey(unit='unit', person=PersonRef(person_id=PERSON, name='Synthetic',
        honcho_peer_id='synthetic'), rooms=(CharacterRoom(key='room-a', platform='signal',
        label='Allowed'),), home='room-a', listed=('room-a',))
    evidence = PersonaEvidence(store=reads, persona=persona, keys={KEY: pk},
                              source_inputs=source, wake_batch=100)
    adaptor = PersonaPersonAdaptor(persona=persona, culture=InMemoryCultureStore(),
        evidence=evidence, reads=reads, keys={KEY: pk}, name_of=lambda _: 'Synthetic',
        clock_ms=lambda: T0 + 1000, clock=lambda: AT, media_join=media_join)
    return reads, persona, source, evidence, adaptor


async def settle_account(adaptor, evidence):
    from nora.domain.ports import Proposal
    _, no_change = adaptor.verbs(key=KEY)
    assert (await no_change(why='synthetic unchanged'))['status'] == 'ok'
    staged = await adaptor.accept(key=KEY, items=())
    await adaptor.commit(key=KEY, proposals=[Proposal(payload=p, instruction_sha256='a' * 64,
        model='synthetic', established_by='synthetic') for p in staged], now_ms=T0 + 1000)
    await evidence.settle(key=KEY)


async def test_budget_trim_and_write_conflict_never_acknowledge_unsettled_source(env):
    from nora.domain.errors import AccountNotRecordableError
    from nora.domain.persona import Account, AddOp, PersonaCitable, Said

    await message(env, ident=10, body='large ' * 10_000)
    await message(env, ident=11, body='small visible intention')
    _, persona, source, evidence, adaptor = wired(env)
    batch = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=100)
    rendered = await adaptor.inputs(key=KEY, batch=batch)
    assert 'small visible intention' in rendered and 'large large' not in rendered
    assert set(adaptor._runs[KEY].citable.messages) == {11}
    await settle_account(adaptor, evidence)
    [remaining] = await source_inputs(env).messages(
        key=KEY, rooms=ROOMS, person=PERSON, limit=100)
    assert remaining.roots[0].row.id == 10
    # A later account revision invalidates the staged commit; source 11's
    # changed content remains pending through that conflict and a restart.
    async with env.begin() as conn:
        await conn.execute(sa.insert(MESSAGE_EDIT).values(message_id=11, ts=T0 + 1,
                                                         body='new qualified intention'))
    batch = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=100)
    await adaptor.inputs(key=KEY, batch=batch)
    result = await persona.persona_write(account=Account(key=KEY, entity_kind='human',
        unit='unit', subject=PERSON), ops=(AddOp(part='identity', kind='attribute',
            text='synthetic role', mode='told', citations=('11',), told_by=PERSON,
            fact_key='occupation', fact_value='synthetic role'),),
        citable=PersonaCitable(messages={11: Said(T0, PERSON, 'room-a')},
                               listed_rooms=frozenset({'room-a'})), now_ms=T0 + 2,
        written_by_session='concurrent', apply_decay=False)
    assert result.status == 'ok'
    with pytest.raises(AccountNotRecordableError):
        await settle_account(adaptor, evidence)
    pending = await source_inputs(env).messages(key=KEY, rooms=ROOMS, person=PERSON, limit=100)
    assert {r.roots[0].row.id for r in pending} == {10, 11}


async def test_unavailable_social_is_explicit_and_does_not_block_or_ack_other_domains(env):
    from nora.infra.sql.schema import CONSOLIDATION_WATERMARK as W

    reads, _, source, evidence, adaptor = wired(env)
    boundary, _ = await reads.history_state(key=KEY)
    await reads.advance_history(key=KEY, cursor=boundary)
    await message(env)  # a new arrival after the frozen history was completed
    # Real failed read, rather than a fake returning [] for every call.
    async with env.begin() as conn:
        await conn.execute(sa.text('ALTER TABLE social_item RENAME TO unavailable_social_item'))
    batch, _ = await evidence.newest(key=KEY, after=0, limit=100)
    rendered = await adaptor.inputs(key=KEY, batch=batch)
    assert 'a qualified intention' in rendered and 'state="unavailable"' in rendered
    await settle_account(adaptor, evidence)
    assert await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=100) == []
    async with env.begin() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(W).where(
            W.c.consolidator_name == 'persona-person:source:social'))).scalar_one() == 0
        await conn.execute(sa.text('ALTER TABLE unavailable_social_item RENAME TO social_item'))
        await conn.execute(sa.insert(SOCIAL_ITEM).values(
            network='synthetic', item_id='retry', person=PERSON, account='author', kind='post',
            taken_at=AT, seen_at=AT, origin='poll', caption='recovered source',
        ))
    [retry] = await source_inputs(env).social(key=KEY, persons=[PERSON], limit=6)
    assert retry.social.caption == 'recovered source'


async def test_removed_mention_and_pruned_record_reconsider_without_rendering_content(env):
    await message(env, person='person-b', body='original eligible mention')
    async with env.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == 10)
                           .values(mention_persons_json=json.dumps([PERSON])))
    source = source_inputs(env)
    [original] = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10)
    assert original.roots[0].row.body == 'original eligible mention'
    await source.settle(key=KEY, revisions=[original])
    async with env.begin() as conn:
        await conn.execute(sa.insert(MESSAGE_EDIT).values(message_id=10, ts=T0 + 1,
            body='no longer mentions the subject', mention_persons_json='[]'))
    [changed] = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10)
    assert changed.state == 'ineligible' and changed.roots == ()
    await source.settle(key=KEY, revisions=[changed])
    async with env.begin() as conn:
        await conn.execute(sa.delete(MESSAGES).where(MESSAGES.c.id == 10))
    [gone] = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10)
    assert gone.state == 'absent' and gone.roots == ()
    assert 'original eligible mention' not in repr(gone)
    await source.settle(key=KEY, revisions=[gone])
    assert await source.messages(key=KEY, rooms=ROOMS, person=PERSON, limit=10) == []


async def test_attempted_unreadable_cache_stays_pending_without_blocking_recovery(env):
    from nora.infra.sql.schema import CONSOLIDATION_WATERMARK as W

    sha = 'd' * 64
    await message(env, body='[image dddddd]', media=json.dumps([{'sha256': sha}]))
    async with env.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(sha256=sha, media_type='image/png',
            size=1, source='inbound', created_at_ms=T0, description='a cached scene'))
    reads, _, source, evidence, adaptor = wired(env)  # no join: unreadable in this consumer
    boundary, _ = await reads.history_state(key=KEY)
    await reads.advance_history(key=KEY, cursor=boundary)
    assert await evidence.count(key=KEY, after=boundary) == 100
    batch, _ = await evidence.newest(key=KEY, after=boundary, limit=1)
    assert 'unavailable' in await adaptor.inputs(key=KEY, batch=batch)
    await settle_account(adaptor, evidence)
    status = await source.media_recovery(key=KEY, rooms=ROOMS, person=PERSON)
    assert status['pending_cached'] == 1 and status['unattempted_cached'] == 0
    assert not status['complete']
    assert 0 < await evidence.count(key=KEY, after=boundary) < 100
    async with env.begin() as conn:
        row = (await conn.execute(sa.select(W).where(
            W.c.consolidator_name == 'persona-person:source:message'))).one()
        assert row.source_revision is None and row.source_read_revision == batch[0].revision
        # New cached content is new delivery work, not swallowed by the attempt marker.
        await conn.execute(sa.update(MEDIA_OBJECTS).where(MEDIA_OBJECTS.c.sha256 == sha)
                           .values(description='a revised cached scene'))
    assert 0 < await evidence.count(key=KEY, after=boundary) < 100
    assert (await source.media_recovery(key=KEY, rooms=ROOMS,
                                       person=PERSON))['initial_sweep_complete']


async def test_historical_page_cannot_reintroduce_unrooted_withdrawn_rollup_support(env):
    await message(env, ident=9, person='person-b', ts=T0 - 1)
    await message(env, ident=10, body='allowed current statement')
    await message(env, ident=11, body='withdrawn reply', quoted_ts=T0 - 1)
    async with env.begin() as conn:
        await conn.execute(sa.insert(ROLLUP_BUILD).values(id=1, status='current',
            code_version='synthetic', params_sha256='a' * 64, through_ms=T0 + 10))
        await conn.execute(sa.insert(ROLLUP_TIE).values(build_id=1, unit='unit',
            valid_from_ms=T0, valid_to_ms=T0 + 10, as_of_ms=T0,
            lineage=[['message', None, 'signal:room-a']], person_a=PERSON,
            person_b='person-b', iso_week='2026-W38', replies_ab=1, replies_ba=0,
            mentions_ab=0, mentions_ba=0, reactions_ab=0, reactions_ba=0, colocation_ms=0))
        # A withdrawal after the build leaves the derived count in place.
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == 11)
                           .values(hidden_at=T0 + 1))
    reads, _, _, evidence, adaptor = wired(env)
    batch, _ = await evidence.newest(key=KEY, after=0, limit=100)
    assert evidence.historical(KEY)
    rendered = await adaptor.inputs(key=KEY, batch=batch)
    assert 'allowed current statement' in rendered and 'withdrawn reply' not in rendered
    assert '<persona_ties ' not in rendered
    assert adaptor._runs[KEY].citable.rollup_keys == frozenset()
    await settle_account(adaptor, evidence)
    assert await reads.history_state(key=KEY) == (11, 10)


async def test_completed_history_bounds_recent_scan_without_hiding_old_cached_readings(env):
    sha = 'e' * 64
    await message(env, body='[image eeeeee]', media=json.dumps([{'sha256': sha}]))
    await message(env, ident=20, body='already settled historical plain text')
    async with env.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(sha256=sha, media_type='image/png',
            size=1, source='inbound', created_at_ms=T0, description='existing cached scene'))
    reads, _, _, evidence, _ = wired(env)
    boundary, _ = await reads.history_state(key=KEY)
    await reads.advance_history(key=KEY, cursor=boundary)
    # The recent watermark may still lag after a completed historical pass.
    batch, cursor = await evidence.newest(key=KEY, after=0, limit=100)
    assert cursor == boundary
    assert {m.row.id for r in batch for m in r.roots} == {10}
    assert batch[0].cache_ready
    assert await reads.history_state(key=KEY) == (boundary, boundary)


async def test_response_projection_keeps_question_context_and_only_subject_reply_roots(env):
    from nora.domain.media_join import MediaJoin
    from nora.infra.sql.media_objects import SqlMediaObjectStore
    from nora.infra.sql.schema import ROLLUP_RESPONSE
    from nora.obs.metrics import Metrics

    sha = 'f' * 64
    await message(env, ident=9, person='nora', own=True, ts=T0 - 1,
                  body='Which format works for you?')
    await message(env, ident=10, body='[image ffffff]', ts=T0 + 1,
                  quoted_ts=T0 - 1, media=json.dumps([{'sha256': sha}]))
    await message(env, ident=11, person='person-b', ts=T0 + 2,
                  quoted_ts=T0 - 1, body='another responder\'s unrelated words')
    async with env.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(sha256=sha, media_type='image/png',
            size=1, source='inbound', created_at_ms=T0, description='target cached scene'))
        await conn.execute(sa.insert(ROLLUP_BUILD).values(id=1, status='current',
            code_version='synthetic', params_sha256='a' * 64, through_ms=T0 + 10))
        await conn.execute(sa.insert(ROLLUP_RESPONSE).values(build_id=1, unit='unit',
            valid_from_ms=T0 - 1, valid_to_ms=T0 + 10, as_of_ms=T0 + 2,
            lineage=[['message', None, 'signal:room-a']], message_id=9, platform='signal',
            conversation_id='room-a', sent_ts=T0 - 1, replies=2, reactions=0, edits=0,
            responders=[PERSON, 'person-b'], outcome='responded'))
    source = source_inputs(env)
    [reply] = await source.rollup(key=KEY, unit='unit', rooms=ROOMS, person=PERSON, limit=12)
    assert {r.row.id for r in reply.roots} == {10}
    assert dict(reply.properties)['quoted_replies_by_subject'] == '1'
    _, _, _, _, adaptor = wired(env, media_join=MediaJoin(
        store=SqlMediaObjectStore(env), screen=lambda _: None, metrics=Metrics()))
    rendered = await adaptor.inputs(key=KEY, batch=[reply])
    assert 'Which format works for you?' in rendered and 'target cached scene' in rendered
    assert 'another responder' not in rendered
    citable = adaptor._runs[KEY].citable
    assert set(citable.messages) == {10} and citable.episodes[9].ts == T0 + 1


@pytest.mark.parametrize('invalid_roots', [0, 101])
async def test_rejected_rollup_page_cannot_hide_readable_revision(env, invalid_roots):
    from nora.infra.sql.schema import CONSOLIDATION_WATERMARK as W
    from nora.infra.sql.schema import ROLLUP_DAYBOOK

    # More than two production pages of permanently invalid candidates.
    for i in range(invalid_roots):
        await message(env, ident=i + 1, ts=T0 + i)
    await message(env, ident=200, ts=T0 + 26 * 86400000 + 1000)
    async with env.begin() as conn:
        await conn.execute(sa.insert(ROLLUP_BUILD).values(id=1, status='current',
            code_version='synthetic', params_sha256='a' * 64, through_ms=T0))
        for i in range(27):
            await conn.execute(sa.insert(ROLLUP_DAYBOOK).values(build_id=1, unit='unit',
                valid_from_ms=T0 + i * 86400000, valid_to_ms=T0 + (i + 1) * 86400000,
                as_of_ms=T0, lineage=[['message', None, 'signal:room-a']], person_id=PERSON,
                local_date=f'2026-01-{i + 1:02}', stays=[], stay_ms=0, home_ms=0,
                colocation_ms=0, outings=0, rooms={}, messages=invalid_roots if i < 26 else 1,
                reactions=0))
    reads, _, _, evidence, adaptor = wired(env)
    await reads.history_state(key=KEY)
    await reads.advance_history(key=KEY, cursor=200)
    assert await evidence.count(key=KEY, after=200) > 0
    batch, cursor = await evidence.newest(key=KEY, after=200, limit=100)
    assert cursor == 200
    assert [r.source for r in batch] == ['daybook:2026-01-27']
    rendered = await adaptor.inputs(key=KEY, batch=batch)
    assert 'n="200"' in rendered and 'daybook:2026-01-27' in rendered
    await settle_account(adaptor, evidence)
    async with env.connect() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(W).where(
            W.c.consolidator_name == 'persona-person:source:rollup'))).scalar_one() == 1
    _, _, _, restarted, _ = wired(env)
    assert await restarted.count(key=KEY, after=200) == 0
    assert (await restarted.newest(key=KEY, after=200, limit=100))[0] == []
    # A formerly empty/incomplete candidate becomes readable without a reset.
    await message(env, ident=201, ts=T0 + 25 * 86400000 + 1000)
    async with env.begin() as conn:
        await conn.execute(sa.update(ROLLUP_DAYBOOK).where(
            ROLLUP_DAYBOOK.c.local_date == '2026-01-26').values(messages=1))
    [revised] = await source_inputs(env).rollup(key=KEY, unit='unit', rooms=ROOMS,
                                              person=PERSON, limit=12)
    assert revised.source == 'daybook:2026-01-26'
    assert [r.row.id for r in revised.roots] == [201]


@pytest.mark.parametrize('mention', [False, True])
@pytest.mark.parametrize('before_read', [False, True])
async def test_preupgrade_plain_edit_discovery_is_independent_of_cache(env, mention, before_read):
    await message(env, person='other' if mention else PERSON)
    # Unchanged consumed plaintext is not replayed to establish new progress.
    source = source_inputs(env)
    assert await source.messages(key=KEY, rooms=ROOMS, person=PERSON, after=20, limit=12) == []

    async def edit():
        async with env.begin() as conn:
            await conn.execute(sa.insert(MESSAGE_EDIT).values(message_id=10, ts=T0 + 500,
                body='I prefer written instructions',
                mention_persons_json=json.dumps([PERSON] if mention else [])))

    if before_read:
        await edit()
    # Simulate initialization/read between revisions with no source watermark.
    await source.media_recovery(key=KEY, rooms=ROOMS, person=PERSON, initialize=True)
    if not before_read:
        await edit()
    [revised] = await source_inputs(env).messages(key=KEY, rooms=ROOMS, person=PERSON,
                                                after=20, limit=12)
    root = revised.roots[0]
    assert root.row.id == 10 and root.row.ts == T0 and root.room == 'room-a'
    assert root.row.person_id == ('other' if mention else PERSON)
    assert revised.as_of == T0 + 500 and root.row.body == 'I prefer written instructions'
    assert not revised.cache_ready
    # Crash/restart repeats; durable exact settlement prevents unchanged replay.
    assert await source_inputs(env).messages(key=KEY, rooms=ROOMS, person=PERSON,
                                             after=20, limit=12) == [revised]
    await source.settle(key=KEY, revisions=[revised])
    assert await source_inputs(env).messages(key=KEY, rooms=ROOMS, person=PERSON,
                                             after=20, limit=12) == []
    assert await source.messages(key=KEY, rooms=[('signal', 'other-room')],
                                 person=PERSON, after=20, limit=12) == []


@pytest.mark.parametrize('withdrawal', ['removed_mention', 'hidden', 'deleted', 'pruned'])
async def test_preupgrade_withdrawal_does_not_require_cached_source_progress(env, withdrawal):
    from nora.infra.sql.schema import MESSAGE_DELETE

    await message(env, person='other' if withdrawal == 'removed_mention' else PERSON)
    async with env.begin() as conn:
        if withdrawal == 'removed_mention':
            await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == 10).values(
                mention_persons_json=json.dumps([PERSON])))
            await conn.execute(sa.insert(MESSAGE_EDIT).values(message_id=10, ts=T0 + 1,
                body='withdrawn mention content', mention_persons_json='[]'))
        elif withdrawal == 'hidden':
            await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == 10).values(
                hidden_at=T0 + 1))
        elif withdrawal == 'deleted':
            await conn.execute(sa.insert(MESSAGE_DELETE).values(message_id=10, ts=T0 + 1,
                sender_id='synthetic-sender'))
        else:
            await conn.execute(sa.delete(MESSAGES).where(MESSAGES.c.id == 10))
    source = source_inputs(env)
    [row] = await source.messages(key=KEY, rooms=ROOMS, person=PERSON, after=20, limit=12)
    assert not row.roots and row.state in ('ineligible', 'hidden', 'absent')
    assert 'withdrawn mention content' not in repr(row)
    await source.settle(key=KEY, revisions=[row])
    assert await source_inputs(env).messages(key=KEY, rooms=ROOMS, person=PERSON,
                                             after=20, limit=12) == []
