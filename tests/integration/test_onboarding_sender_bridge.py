"""Resolved bridged senders still deliver grouped identity observations."""

from dataclasses import replace

from nora.domain.identity import Identifier
from nora.platform.bridge.envelope import canonical_source_key, event_id
from nora.platform.bridge.queue import CONSUMER_GROUP
from nora.platform.signal.discovery import SignalDiscovery
from tests.integration.test_bridge_queue import (
    ACCOUNT,
    KEY,
    PLATFORM,
    _envelope,
    _produce,
    _reader,
    _runner,
)
from tests.integration.test_bridge_queue import bridge as bridge
from tests.unit.test_onboarding_sender_enrichment import _bind
from tests.unit.test_platform_onboarding import UNIT, _graph, _onboarding, _rows, _rule


async def test_a_resolved_bridged_sender_enriches_its_later_identifier_group(
    bridge, harness_factory,
):
    services, provisioning = _graph()
    phone = Identifier("signal", "phone", "+00000000002")
    aci = Identifier("signal", "uuid", "00000000-0000-4abc-8def-000000000001")
    bound = await _bind(services.bindings, "generated-person", {phone})
    rows = []

    async def fetch(collection):
        return _rows(list(rows))

    runtime = _onboarding(services, provisioning, _rule(
        SignalDiscovery(fetch=fetch, own_account=""),
        collection="g1", platform="signal"))
    await runtime._drain()
    harness = await harness_factory(identity_toml=(
        '[persons.generated]\nname = "Generated"\n'
        '[[persons.generated.signal]]\nphone = "+00000000002"\n'))
    store, redis, stream = bridge
    runner = _runner(store, redis, harness, onboarding=runtime)
    for number in (1, 2):
        rows[:] = [{"number": phone.value}] if number == 1 else [
            {"number": phone.value, "uuid": aci.value}]
        env = _envelope("plain sender note", 2000 + number)
        source_key = canonical_source_key(PLATFORM, ACCOUNT, "g1", "message", str(number))
        env = replace(env, conversation_id="g1", source_key=source_key,
            event_id=event_id(source_key, key=KEY, key_version=1),
            message={**env.message, "conversation_id": "g1"})
        await _produce(redis, stream, env)
        batch = await redis.xreadgroup(CONSUMER_GROUP, "sender-reader", {stream: ">"}, count=1)
        sid, fields = batch[0][1][0]
        await _reader(redis, store, stream)._register(sid, fields)
        work = await store.claim_next(owner="sender-worker", lease_seconds=60)
        assert work is not None and work.kind == "ingest"
        await runner._execute(work)
        recorded = await store.event(env.event_id)
        assert recorded["payload"]["handoff"]["person"] is not None
        assert recorded["state"] == "done"
        await runtime._drain()
    enriched = await services.bindings.record_for(account=aci)
    assert enriched is not None
    assert enriched.account_id == bound.account_id
    assert enriched.person_id == bound.person_id
    assert len(services.bindings.people) == len(services.bindings.accounts) == 1
    assert not await services.bindings.members_of(UNIT)
    assert not services.backend.tuples
