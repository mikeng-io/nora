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


def rig(*, fails=False, permitted=True, observer=None):
    adapter, store, metrics = FakeAdapter(react_fails=fails), FakeRecordStore(), Metrics()
    doctrine = Doctrine(sections=(), text='', doctrine_text='', sha256='test',
                        runtime_context='', loaded_at=0.0)
    delivery = Deliverer(store=store, adapter=adapter, target=TARGET, doctrine=doctrine,
                         canary='CANARY', tag_id='tag', nora_sender_id='nora',
                         message_cap=3, audience='tag', metrics=metrics,
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
