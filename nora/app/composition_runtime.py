from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nora.app.composition_helpers import _note_unit_of
from nora.app.runtime import BackgroundTasks
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.infra.sql.tag_debounce import SqlTagDebounceStore
from nora.ingest.stages import stimulus as stimulus_stage
from nora.obs.health import LLMState
from nora.platform.discord.bridge_control import BridgeChannelDirectory
from nora.tag import Debounce

"""Runtime-support wiring steps: tasks, slots, stimulus."""


@dataclass(frozen=True)
class TasksWiring:
    background: Any
    debounce: Any
    llm_state: Any


async def tasks(cfg: Any, channel_directory: Any, engine: Any, reload_files: Any)-> TasksWiring:


    llm_state = LLMState()
    background = BackgroundTasks()
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


def build_slots(background: Any, cfg: Any)-> SlotsWiring:

    # R-10's per-conversation lock and R-30's FIFO queue, built ONCE here. Every path that
    # may speak into a conversation holds this same instance — two would be two locks and no
    # serialisation at all. Built HERE, above the registry, because the relay below needs…
    slots = ConversationSlots(maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn)
    return SlotsWiring(slots=slots)


@dataclass(frozen=True)
class StimulusWiring:
    stimulus_store: Any
    stimulus_wiring: Any


def stimulus(allowlist: Any, cfg: Any, engine: Any, registry: Any)-> StimulusWiring:


    # The cognition loop's stimuli: OFF under `[cognition] enabled = false`, the shipped
    # default. On, the ingest pipeline appends one stimulus per recorded person-authored
    # item, and the evaluator reads them and starts sessions. The evaluator is built after…
    stimulus_wiring: stimulus_stage.Wiring | None = None
    stimulus_store: SqlStimulusStore | None = None
    if cfg.cognition.enabled:
        stimulus_store = SqlStimulusStore(engine)
        stimulus_wiring = stimulus_stage.Wiring(
            store=stimulus_store,
            unit_of=_note_unit_of(allowlist),
            self_ids=registry.self_aliases(),
            reply_quiet_seconds=cfg.cognition.reply_quiet_seconds,
            timeout_seconds=cfg.cognition.store_timeout_seconds,
        )
    return StimulusWiring(stimulus_store=stimulus_store, stimulus_wiring=stimulus_wiring)
