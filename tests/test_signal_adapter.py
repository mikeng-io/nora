from nora.domain.models import ConversationTarget
from nora.platform.base import PlatformAdapter
from nora.platform.signal.adapter import SignalAdapter


def test_adapter_satisfies_the_protocol():
    assert isinstance(SignalAdapter("http://127.0.0.1:18080", "+852111"), PlatformAdapter)


def test_adapter_declares_its_platform():
    assert SignalAdapter("http://x", "+1").platform == "signal"


async def test_parse_filters_our_own_account():
    adapter = SignalAdapter("http://x", "+852999")
    event = {"method": "receive", "params": {"envelope": {
        "source": "+852999", "dataMessage": {"message": "hi",
                                             "groupInfo": {"groupId": "g"}}}}}
    assert await adapter.parse(event) is None


async def test_health_reports_connection_state():
    adapter = SignalAdapter("http://x", "+1")
    health = await adapter.health()
    assert set(health) >= {"connected", "last_event_age_seconds", "account"}
    assert health["account"].endswith("1")


async def test_send_delegates_to_the_sender():
    adapter = SignalAdapter("http://127.0.0.1:1", "+1")
    # unreachable daemon → False, never an exception
    assert await adapter.send(ConversationTarget("signal", "g", "group"), "x") is False
