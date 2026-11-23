"""A handoff queued before a `PersonRef` field was removed is still resumed."""
# ruff: noqa: F401, F811 - pytest resolves imported fixtures by name.

import sqlalchemy as sa

from nora.infra.sql.schema import BRIDGE_EVENT
from tests.integration.test_bridge_deferred import (
    MAPPED_SENDER,
    _envelope,
    _finish_state,
    _register,
    _runner,
    _stimulus_wiring,
    _work_rows,
    bridge,
)

_REMOVED_KEY = "honcho_peer_id"


async def test_reclaim_resumes_from_a_handoff_whose_person_carries_a_removed_key(
        bridge, redis_client, harness, monkeypatch):
    store, _, stream = bridge
    env = _envelope("hello room", 5100, sender=MAPPED_SENDER)
    await _register(redis_client, store, stream, env)
    runner = _runner(store, redis_client, harness, stimulus=_stimulus_wiring(store))

    async def killed(conn, **kw):
        raise RuntimeError("killed after audit commit")

    monkeypatch.setattr(store, "enqueue_work", killed)
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    assert (await _finish_state(store, work.id))["state"] == "retryable_failure"

    # The row an older release wrote: the committed snapshot's person carries the key.
    payload = (await store.event(env.event_id))["payload"]
    assert payload["handoff"]["person"]["person_id"] == "miles"
    payload["handoff"]["person"][_REMOVED_KEY] = "miles"
    async with store._engine.begin() as conn:
        await conn.execute(sa.update(BRIDGE_EVENT).where(
            BRIDGE_EVENT.c.id == env.event_id).values(payload=payload))
    stored = (await store.event(env.event_id))["payload"]["handoff"]["person"]
    assert stored[_REMOVED_KEY] == "miles"

    monkeypatch.undo()
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    assert (await _finish_state(store, work.id))["state"] == "done"
    stimulus = {r["kind"]: r for r in await _work_rows(store)}["stimulus"]
    assert stimulus["payload"]["person_id"] == "miles"
