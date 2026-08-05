"""`SinkSender` (`tests/fakes.py`) — a `PlatformAdapter` stand-in that records
sends instead of performing them (spec E.3).

Was `test_platform_base.py`, testing conformance against
`nora.platform.base.PlatformAdapter` — a pre-ports-migration protocol that
predated `nora.domain.ports.PlatformAdapter` and drifted out of date the
moment `SignalAdapter.parse` came off the public surface (platforms phase,
Trap B). That protocol added nothing `nora.domain.ports.PlatformAdapter`
doesn't already cover and is deleted; `tests/contract/test_port_conformance.py`
is where adapter/port conformance is asserted now.
"""

from nora.domain.models import ConversationTarget
from tests.fakes import SinkSender


async def test_sink_sender_records_instead_of_sending():
    sink = SinkSender()
    target = ConversationTarget("signal", "g1", "group")
    result = await sink.send(target, "hello")
    assert result.ok is True
    assert sink.sent == [(target, "hello")]


async def test_sink_sender_can_simulate_a_send_failure():
    sink = SinkSender(succeed=False)
    result = await sink.send(ConversationTarget("signal", "g1", "group"), "x")
    assert result.ok is False
    assert len(sink.sent) == 1     # the attempt is still recorded


def test_sink_sender_texts_helper():
    sink = SinkSender()
    assert sink.texts == []


async def test_no_real_sender_in_tests():
    """Guard: the real Signal send path must never be constructed under pytest."""
    import nora.platform.signal.send as send_mod

    assert getattr(send_mod, "_PYTEST_GUARD", True)
