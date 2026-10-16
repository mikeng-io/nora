"""A sandbox Signal send lands, and only the wire is replaced.

Without `nora.subject.sandbox_signal`, an eval send would dial a signal-cli
that is not there, so `send_reply` would tell the model `refused` on every
reply.
"""
from __future__ import annotations

import pytest

from nora.domain.models import ConversationTarget
from nora.platform.signal import adapter
from nora.subject.sandbox_signal import LandedSignalSender, landed_signal_sends


def _target(kind: str, cid: str = "g1") -> ConversationTarget:
    return ConversationTarget(platform="signal", conversation_id=cid, conversation_type=kind)


@pytest.mark.asyncio
async def test_a_group_send_lands_without_a_daemon_and_carries_no_platform_stamp():
    sender = LandedSignalSender("http://127.0.0.1:1")
    result = await sender.send(_target("group"), "hi")
    assert result.ok
    assert result.timestamp is None


@pytest.mark.asyncio
async def test_the_real_refusals_above_the_wire_still_refuse():
    sender = LandedSignalSender("http://127.0.0.1:1")
    assert not (await sender.send(_target("dm", "+85291234567"), "hi")).ok
    assert not (await sender.send(_target("channel"), "hi")).ok


def test_adapters_built_inside_the_block_land_and_the_patch_is_gone_after():
    real = adapter.SignalSender
    with landed_signal_sends():
        with landed_signal_sends():
            pass
        assert adapter.SignalSender is not real, "an inner block must not restore it"
        built = adapter.SignalAdapter("http://127.0.0.1:1", "+85200000000", metrics=None)
    assert isinstance(built._sender, LandedSignalSender)
    assert adapter.SignalSender is real
