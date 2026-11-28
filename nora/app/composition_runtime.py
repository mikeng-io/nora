from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nora.app.runtime import BackgroundTasks
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.tag_debounce import SqlTagDebounceStore
from nora.obs.health import LLMState
from nora.platform.discord.bridge_control import BridgeChannelDirectory
from nora.tag import Debounce

"""Runtime-support wiring steps: tasks and slots."""


@dataclass(frozen=True)
class TasksWiring:
    background: Any
    debounce: Any
    llm_state: Any


async def tasks(
    cfg: Any, channel_directory: Any, engine: Any, reload_files: Any,
    owner: Any = None,
)-> TasksWiring:


    llm_state = LLMState()
    background = BackgroundTasks()
    if owner is not None:
        # Owned before the first spawn — a failing `hydrate` below must
        # still cancel what is already running.
        owner.own_background(background)
    background.spawn(reload_files.run())
    if isinstance(channel_directory, BridgeChannelDirectory):
        # The bridge-mode directory's TTL snapshot only stays live while
        # its refresh loop runs — a real registered loop, not lazy reads.
        background.spawn(channel_directory.run())
    # R-9's rate limiter, with its window persisted so a force-recreate does not empty it (a
    # duplicate tag arriving seconds after a restart would otherwise run a second turn).
    # `spawn` is REQUIRED alongside the store, not optional: `Debounce.allow` is…
    debounce = Debounce(
        cfg.engagement.trigger.min_interval_seconds,
        store=SqlTagDebounceStore(engine),
        spawn=background.spawn,
    )
    # ONCE, here, before any adapter is listening — `allow` cannot do it for
    # itself. It also prunes the rows whose window has already elapsed, which
    # is what bounds the table without a sweep loop of its own.
    await debounce.hydrate()
    return TasksWiring(background=background, debounce=debounce, llm_state=llm_state)


@dataclass(frozen=True)
class SlotsWiring:
    slots: Any


def build_slots(background: Any, cfg: Any, policy: Any) -> SlotsWiring:

    # R-10's per-conversation lock and R-30's FIFO queue, built ONCE here. Every path that
    # may speak into a conversation holds this same instance — two would be two locks and no
    # serialisation at all. Built HERE, above the registry, because the relay below needs…
    slots = ConversationSlots(
        maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn, policy=policy,
    )
    return SlotsWiring(slots=slots)
