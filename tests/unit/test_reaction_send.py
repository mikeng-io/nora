"""Authored reactions use the visible transcript and complete without a text send."""
from types import SimpleNamespace

from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget, DeliveryStatus, TranscriptRow
from nora.engagement.stages import indicate
from nora.obs.metrics import Metrics
from nora.testing import FakeAdapter, FakeRecordStore

TARGET = ConversationTarget('signal', 'room', 'group')
ROW = TranscriptRow(1, 'sender', None, 1000, 'a message', False, DeliveryStatus.SENT, None)


def rig(*, fails=False, permitted=True, observer=None, cap=3, metrics=None):
    adapter, store = FakeAdapter(react_fails=fails), FakeRecordStore()
    metrics = metrics or Metrics()
    doctrine = Doctrine(sections=(), text='', doctrine_text='', sha256='test',
                        runtime_context='', loaded_at=0.0)
    delivery = Deliverer(store=store, adapter=adapter, target=TARGET, doctrine=doctrine,
                         canary='CANARY', tag_id='tag', nora_sender_id='nora',
                         message_cap=cap, audience='tag', metrics=metrics,
                         send_permitted=permitted, on_delivered=observer)
    specs = ToolRegistry().specs(store=store, conversation_id='room',
                                conversation_type='group', persons=None, honcho=None,
                                deliverer=delivery, metrics=metrics,
                                room=SimpleNamespace(platform='signal', conversation_id='room',
                                                     tagger=None), transcript=(ROW,))
    tool = next(s for s in specs if s.name == 'reaction_send')
    return tool, delivery, adapter, store


async def test_reaction_only_is_delivered_and_recorded_without_a_phantom_message():
    tool, delivery, adapter, store = rig()
    assert (await tool.call(emoji='👍🏽', reply_to=1))['status'] == 'ok'
    assert adapter.reactions == [(TARGET, '👍🏽', 'sender', 1000, '', False)]
    assert delivery.delivered and delivery.called
    assert not adapter.sent and delivery.sent_texts == ()
    assert delivery.delivered_count == 0
    assert not store.rows
    assert len(store.reactions) == 1


async def test_invalid_target_and_rejected_reaction_are_not_completion():
    tool, delivery, adapter, _ = rig(fails=True)
    assert (await tool.call(emoji='👍', reply_to=0))['status'] == 'unavailable'
    assert not adapter.reactions
    assert (await tool.call(emoji='👍', reply_to=1))['status'] == 'unavailable'
    assert len(adapter.reactions) == 1
    assert not delivery.delivered


async def test_reaction_respects_send_cooldown():
    tool, delivery, adapter, _ = rig(permitted=False)
    assert (await tool.call(emoji='😂', reply_to=1))['status'] == 'held_for_review'
    assert delivery.held_count == 1
    assert not adapter.reactions and not delivery.delivered


async def test_reaction_retracts_progress_before_sending_and_keeps_authored_emoji():
    import asyncio

    from nora.domain.models import NormalizedMessage

    tasks = []
    adapter = FakeAdapter()
    indicator = indicate.begin(adapter=adapter,
        message=NormalizedMessage('signal', 'room', 'group', 'sender', None, 1000, '', {}),
        tag_id='tag', metrics=Metrics(), spawn=lambda c: tasks.append(asyncio.create_task(c)))
    observer = indicate.DeliverySettler(indicator=indicator,
                                      spawn=lambda c: tasks.append(asyncio.create_task(c)))
    await observer.prepare_reaction()
    await adapter.react(target=TARGET, emoji='❤️', to_author='sender', to_timestamp=1000)
    observer.finish_reaction(ok=True)
    observer.settle()
    indicate.resolve(indicator=indicator, delivered=True,
                     spawn=lambda c: tasks.append(asyncio.create_task(c)))
    await asyncio.gather(*tasks)
    assert [(r[1], r[-1]) for r in adapter.reactions] == [('👀', False), ('👀', True), ('❤️', False)]


def test_reaction_is_terminal_unless_more_work_is_declared():
    landed = {'content': [{'text': '{"status": "ok"}'}]}
    assert ReplyGuard._reply_closes_turn('reaction_send', {}, landed)
    assert not ReplyGuard._reply_closes_turn('reaction_send', {'more': True}, landed)
    refused = {'content': [{'text': '{"status": "unavailable"}'}]}
    assert not ReplyGuard._reply_closes_turn('reaction_send', {}, refused)


async def test_cancel_during_progress_handover_does_not_strand_the_indicator(monkeypatch):
    import asyncio

    import pytest

    from nora.domain.ports import OutboundQuote

    adapter = FakeAdapter()
    indicator = indicate.Indicator(adapter=adapter, target=TARGET, author='sender',
        timestamp=1000, tag_id='tag', metrics=Metrics())
    indicator.placed.set()
    tasks = []
    observer = indicate.DeliverySettler(indicator=indicator,
        spawn=lambda c: tasks.append(asyncio.create_task(c)))
    _, delivery, _, _ = rig(observer=observer)
    entered, release = asyncio.Event(), asyncio.Event()
    original = indicate._react

    async def pause_clear(indicator, emoji, *, phase, remove=False):
        if phase == 'clear':
            entered.set()
            await release.wait()
        await original(indicator, emoji, phase=phase, remove=remove)

    monkeypatch.setattr(indicate, '_react', pause_clear)
    task = asyncio.create_task(delivery.react(emoji='👍', row=ROW,
        quote=OutboundQuote(ts=1000, author_id='sender')))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not indicator.authored_reaction
    release.set()
    indicate.resolve(indicator=indicator, delivered=False,
        spawn=lambda c: tasks.append(asyncio.create_task(c)))
    await asyncio.gather(*tasks)
    assert [(r[1], r[-1]) for r in adapter.reactions] == [('👀', True), ('⚠️', False)]


async def test_a_suppressed_resolution_can_resume_after_reaction_failure():
    import asyncio

    adapter = FakeAdapter()
    indicator = indicate.Indicator(adapter=adapter, target=TARGET, author='sender',
        timestamp=1000, tag_id='tag', metrics=Metrics())
    indicator.placed.set()
    tasks = []
    observer = indicate.DeliverySettler(indicator=indicator,
        spawn=lambda c: tasks.append(asyncio.create_task(c)))
    await observer.prepare_reaction()
    indicate.resolve(indicator=indicator, delivered=True,
        spawn=lambda c: tasks.append(asyncio.create_task(c)))
    await asyncio.gather(*tasks)
    assert not indicator.settled.is_set()
    observer.finish_reaction(ok=False)
    indicate.resolve(indicator=indicator, delivered=False,
        spawn=lambda c: tasks.append(asyncio.create_task(c)))
    await asyncio.gather(*tasks)
    assert adapter.reactions[-1][1] == '⚠️'


async def test_a_reaction_and_a_text_reply_share_the_message_cap():
    metrics = Metrics()
    tool, delivery, adapter, _ = rig(cap=1, metrics=metrics)
    assert (await tool.call(emoji='👍', reply_to=1))['status'] == 'ok'
    assert not await delivery.deliver('another response')
    assert len(adapter.reactions) == 1 and not adapter.sent
    assert metrics.get('nora_guard_vetoes_total', rule='R-2') == 1


async def test_an_unsupported_reaction_is_counted_apart_from_a_failed_send(monkeypatch):
    from unittest.mock import AsyncMock

    from nora.domain.ports import ReactionResult

    metrics = Metrics()
    tool, delivery, adapter, store = rig(metrics=metrics)
    react = AsyncMock(return_value=ReactionResult(ok=False, supported=False))
    monkeypatch.setattr(adapter, 'react', react)
    assert (await tool.call(emoji='👍', reply_to=1))['status'] == 'unavailable'
    react.assert_awaited_once_with(target=TARGET, emoji='👍', to_author='sender',
                                  to_timestamp=1000, to_message_id='')
    assert metrics.get('nora_reactions_total', phase='reply', status='unsupported',
                       platform='signal') == 1
    assert metrics.get('nora_replies_total', status='failed', platform='signal') == 0
    assert not delivery.delivered and not store.reactions
