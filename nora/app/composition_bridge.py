from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

from nora.app.bridge_queue import BridgeRuntime, DbSpeakingLocks, StreamReader, WorkRunner
from nora.infra.sql.bridge_store import WORK_KINDS, SqlBridgeStore
from nora.infra.sql.participation_state import SqlParticipationStateStore
from nora.platform.bridge.inspection import QueueInspector
from nora.platform.bridge.inspection import prime as prime_queue_metrics
from nora.platform.registry import bridge_platforms

"""Bridge runtime wiring step."""


@dataclass(frozen=True)
class RuntimeWiring:
    bridge_runtime: Any


async def runtime(
    allowlist: Any, cfg: Any, engagement: Any, engine: Any,
    fire_path: Any, ingest: Any, memory: Any, metrics: Any,
    observation_store: Any, onboarding: Any, participation_trigger: Any,
    persons: Any, policy: Any, registry: Any, slots: Any, stimulus_wiring: Any,
    access: Any = None, room_command: Any = None, owner: Any = None,
) -> RuntimeWiring:

    # ---- platform bridges (design/platform-bridges) ------------------------- Any platform
    # with `mode = "bridge"` consumes inbound over Redis Streams instead of its live
    # listener, and its adapter is a BridgeAdapter client of that bridge's action API. The…
    bridged = bridge_platforms(cfg.platforms)
    bridge_runtime: BridgeRuntime | None = None
    if bridged:
        # Present by construction: `composition_platforms.adapters` refused
        # startup before the registry was built if it was not.
        redis_url = cfg.credentials.get(cfg.bridges.redis_url_env, "")
        bridge_store = SqlBridgeStore(engine)
        bridge_redis = aioredis.from_url(redis_url)
        if owner is not None:
            # Owned before the runtime pieces below can fail — the client
            # exists from here on.
            owner.own_bridge(redis=bridge_redis)
        bridge_locks = DbSpeakingLocks(bridge_store)
        if participation_trigger is not None:
            # The durable window close lives in the same work ledger as every other obligation:
            # watermark + candidate folds + fire row in ONE commit, so a force-recreate never finds
            # a judged window whose turn nobody owns.
            async def close_participation_window(
                platform: str, conversation_id: str, window_end_ms: int,
                conversation_type: str,
            ) -> None:
                await bridge_store.close_participation_window(
                    platform, conversation_id, window_end_ms, conversation_type
                )

            participation_trigger.set_close_handoff(close_participation_window)
        bridge_runner = WorkRunner(
            store=bridge_store,
            redis=bridge_redis,
            ingest=ingest,
            engagement=engagement,
            slots=slots,
            db_locks=bridge_locks,
            adapters=dict(registry.adapters),
            # Claims only rows of LIVE platforms: a degraded platform's work
            # stays pending until its identity is registered.
            live_platforms=registry.live_platforms,
            gate=policy,
            metrics=metrics,
            participation=participation_trigger,
            # Absorption is a PER-MESSAGE admission fact, never a wiring constant: each obligation
            # carries the value canonical policy computed at ingest, and the deferred executor
            # rechecks the allowlist as it stands when the work actually runs.
            memory_feed={"memory": memory},
            stimulus=stimulus_wiring,
            lease_seconds=cfg.bridges.lease_seconds,
            allowlist=allowlist,
            persons=persons,
            access=access,
            fire_speak=fire_path.speak if fire_path else None,
            participation_state=SqlParticipationStateStore(engine),
            observations=observation_store,
            # The SAME predicate the live dispatcher holds — one command rule.
            room_command=room_command,

            onboarding=onboarding,
        )
        # The inspector reads the REAL redis client + engine the runtime
        # runs on — a second connection would measure a different world.
        queue_inspector = QueueInspector(bridge_redis, engine=engine, metrics=metrics)
        bridge_runtime = BridgeRuntime(
            redis=bridge_redis,
            store=bridge_store,
            readers=[
                StreamReader(
                    platform=p,
                    account_key=getattr(cfg.platforms, p).account_key,
                    redis=bridge_redis,
                    store=bridge_store,
                    metrics=metrics,
                    # The first stream call waits for the platform to be live.
                    wait_live=functools.partial(registry.wait_live, p),
                )
                for p in bridged
            ],
            runner=bridge_runner,
            inspector=queue_inspector,
        )
        if owner is not None:
            owner.own_bridge(redis=bridge_runtime.redis, stop=bridge_runtime.stop)
        # Zero-series for the counters the runtime increments — the REAL
        # work-kind and action-op tuples, so `increase()` over a series the
        # first event creates does not lose it.
        prime_queue_metrics(metrics, bridged, work_kinds=sorted(WORK_KINDS))
    return RuntimeWiring(bridge_runtime=bridge_runtime)
