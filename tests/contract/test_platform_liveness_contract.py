"""The liveness half of the `PlatformAdapter` contract, over EVERY adapter the
platform table builds (docs/design/platform-bridges/platform-liveness.md «One
contract for every platform»).

Each table row is built in both modes — direct and bridge — so a new row (a
new platform) is covered the moment it is added, and an adapter that does
not join `DegradeLatch` fails here, in CI, rather than sending past a
degraded platform in production. The shared test fakes are held to the same
contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nora.domain.latch import DegradeLatch
from nora.domain.models import ConversationTarget
from nora.domain.ports import AdapterHealth, ReactionResult, SendResult
from nora.obs.metrics import Metrics
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint
from nora.platform.discord.bridge_control import BridgeControlClient, BridgeDiscordAdapter
from nora.platform.registry import PLATFORMS, BuildContext
from nora.testing import FakeAdapter, FakePlatformAdapter


def _section(mode: str) -> SimpleNamespace:
    return SimpleNamespace(enabled=True, mode=mode, base_url="http://127.0.0.1:1",
                           account="+852999", token_env="DISCORD_BOT_TOKEN")  # noqa: S106


def _cases():
    return [(entry.name, mode) for entry in PLATFORMS for mode in ("direct", "bridge")]


async def _adapter_for(name: str, mode: str):
    ctx = BuildContext(metrics=Metrics(), discord_token="tok",  # noqa: S106 — test value
                       bridge_token="t")  # noqa: S106 — test value
    entry = next(e for e in PLATFORMS if e.name == name)
    return (await entry.build(_section(mode), ctx)).adapter


def _probe(adapter):
    # A subclass joins the latch exactly as the real class does, so a count
    # means «the adapter's own code ran»: zero while degraded, one once live.
    calls = {"send": 0, "react": 0, "health": 0}

    async def send(self, **kwargs):
        calls["send"] += 1
        return SendResult(ok=True)

    async def react(self, **kwargs):
        calls["react"] += 1
        return ReactionResult(ok=True)

    async def health(self):
        calls["health"] += 1
        return AdapterHealth(platform=self.platform, connected=True)

    adapter.__class__ = type(f"Probe{type(adapter).__name__}", (type(adapter),),
                             {"send": send, "react": react, "health": health})
    return calls


@pytest.mark.parametrize(("name", "mode"), _cases(),
                         ids=[f"{n}-{m}" for n, m in _cases()])
async def test_every_table_adapter_honours_the_liveness_contract(name, mode):
    adapter = await _adapter_for(name, mode)
    cls = type(adapter)
    assert isinstance(adapter, DegradeLatch), f"{cls.__name__} does not join DegradeLatch"
    for method in ("send", "react", "health"):
        assert getattr(getattr(cls, method), "__latch_gated__", False), (
            f"{cls.__name__}.{method} is not gated by the latch")

    calls = _probe(adapter)
    target = ConversationTarget(platform=name, conversation_id="g",
                                conversation_type="group")
    adapter.degrade("identity unavailable")
    assert not (await adapter.send(target=target, text="hi")).ok
    reacted = await adapter.react(target=target, emoji="👀", to_author="a",
                                  to_timestamp=1)
    assert not reacted.ok and not reacted.supported
    health = await adapter.health()
    assert health.degraded and not health.connected
    assert health.platform == name and "identity unavailable" in health.detail
    assert calls == {"send": 0, "react": 0, "health": 0}, "the adapter's code ran"

    adapter.promote()
    assert (await adapter.send(target=target, text="hi")).ok
    assert (await adapter.react(target=target, emoji="👀", to_author="a",
                                to_timestamp=1)).ok
    assert not (await adapter.health()).degraded
    assert calls == {"send": 1, "react": 1, "health": 1}
    close = getattr(adapter, "close", None)
    if close is not None:
        await close()


@pytest.mark.parametrize("fake", [FakeAdapter, FakePlatformAdapter])
def test_the_shared_fakes_join_the_latch(fake):
    assert issubclass(fake, DegradeLatch)
    assert getattr(fake.send, "__latch_gated__", False)


async def test_a_derived_facade_shares_the_root_latch():
    endpoint = BridgeEndpoint("http://127.0.0.1:1", "t")
    root = BridgeAdapter("whatsapp", endpoint)
    facades = [root.bind_owner(None), root.bind_owner(None).bind_owner(None)]
    discord = BridgeDiscordAdapter(delegate=BridgeAdapter("discord", endpoint),
                                   control=BridgeControlClient(endpoint))
    discord_facade = discord.bind_owner(None)

    root.degrade("logged out")
    discord.degrade("logged out")
    assert all(f.degraded for f in facades)
    assert discord_facade.degraded and discord._delegate.degraded
    # The text body the voice front delegates to is gated as well.
    target = ConversationTarget(platform="whatsapp", conversation_id="g",
                                conversation_type="group")
    assert not (await facades[0].send_via_bridge(target, "hi")).ok

    root.promote()
    discord.promote()
    assert not any(f.degraded for f in facades)
    assert not discord_facade.degraded
    for adapter in (root, discord):
        await adapter.close()
