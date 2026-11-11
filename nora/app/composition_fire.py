from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from nora.app.composition_helpers import _reasoning_effort
from nora.app.compute_poller import ComputeJobPoller
from nora.app.compute_poller import recover as compute_poller_recover
from nora.app.fire import FirePath, proactive_occasion, reminder_occasion
from nora.app.heartbeat import HeartbeatLoop, publish_idle_gauge, recover
from nora.app.participation import ParticipationTrigger
from nora.app.proactive_screen import ProactiveScreen
from nora.app.raw_clients import _RelevanceGateModelClient
from nora.domain.video import VIDEO_KIND
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.participation_state import SqlParticipationStateStore
from nora.ingest.relevance_gate import RelevanceGate
from nora.obs.metrics import Metrics
from nora.policy import Policy

from .fire import FAILED_POLICY, lost_job_occasion

"""Fire-path, heartbeat, gate and trigger wiring steps."""


log = logging.getLogger("nora.app.composition")


async def _tell_about_lost_jobs(
    jobs: list[Any], speak: Any, slots: ConversationSlots, metrics: Metrics,
    policy: Policy,
) -> None:
    """Close the promise each lost job left open, one room at a time."""
    for job in jobs:
        if not policy.is_allowed(
            platform=job.platform,
            conversation_id=job.conversation_id,
            person_id=job.requester_person_id,
        ):
            log.warning(
                "lost compute job %s not told: %s is no longer allowed in %s",
                job.id,
                job.requester_person_id,
                job.conversation_id,
            )
            metrics.inc("nora_compute_lost_told_total", outcome=FAILED_POLICY)
            continue
        key = (job.platform, job.conversation_id)
        # `try_acquire`, the heartbeat's rule: a room already mid-turn is not
        # one to interrupt, and this is the least urgent thing the process does.
        if not slots.try_acquire(key):
            log.warning(
                "could not tell %s about a lost job: the room is mid-turn",
                job.conversation_id,
            )
            metrics.inc("nora_compute_lost_told_total", outcome="busy")
            continue
        try:
            outcome = await speak(lost_job_occasion(job))
            metrics.inc("nora_compute_lost_told_total", outcome=outcome)
        except Exception:
            log.exception("failed to tell %s about a lost job", job.conversation_id)
            metrics.inc("nora_compute_lost_told_total", outcome="failed_error")
        finally:
            slots.release_and_dispatch(key)


@dataclass(frozen=True)
class RecoverWiring:
    lost_jobs: Any


async def build_recover(
    clock: Any, compute_backends: Any, compute_jobs: Any, metrics: Any,
    reminders: Any,
) -> RecoverWiring:

    # The Dispatcher is built LATE, below the fire path — the participation
    # trigger it may carry needs `fire_path.speak`, which does not exist yet
    # here. Nothing between here and there consumes it.

    # Boot recovery, UNCONDITIONALLY — deliberately NOT behind `cfg.heartbeat.enabled`. A
    # crash leaves rows FIRING, and a heartbeat that has been switched off is exactly the
    # state in which a stranded row would otherwise sit there forever: at-most-once would…
    await recover(store=reminders, metrics=metrics, now=clock())
    # Compute-job boot recovery, ALSO unconditional, and for the SAME reason — deliberately
    # NOT behind `cfg.compute.enabled`: a deployment that crashes mid-delivery and then
    # flips `[compute] enabled=false` would otherwise leave any `FIRING` `ComputeJob` row…
    lost_jobs = await compute_poller_recover(
        store=compute_jobs,
        metrics=metrics,
        now=clock(),
        # A video row's handle is the provider's task id, not memory: when the video backend
        # is wired its pending rows are polled again rather than swept as lost.
        durable_kinds=(frozenset({VIDEO_KIND}) if VIDEO_KIND in compute_backends else frozenset()),
    )
    # The liveness gauge is seeded HERE, not in `HeartbeatLoop.__init__` alone —
    # otherwise every deployment on the shipped default (`enabled = false`)
    # exports 0.0 forever, which reads identically to "ticked a moment ago".
    publish_idle_gauge(metrics)
    return RecoverWiring(lost_jobs=lost_jobs)


@dataclass(frozen=True)
class PathWiring:
    fire_path: Any


def path(
    allowlist: Any, audit: Any, background: Any, cfg: Any,
    clock: Any, computer_context: Any, culture_reader: Any, decision_log: Any,
    doctrine_store: Any, documents_context: Any, lessons_reader: Any, limits: Any,
    lineage: Any, llm_state: Any, lost_jobs: Any, media_join: Any,
    memo_store: Any, memory: Any, metrics: Any, note_store: Any,
    participation_runner: Any, persons: Any, presence_reader: Any, preview_source: Any,
    proactive_runner: Any, profile_reader: Any, quote_window: Any,
    reasoning_strip_store: Any, registry: Any, rosters: Any, runner: Any,
    shared: Any, slots: Any, tag_trace_store: Any, tool_call_log_store: Any,
    tool_registry: Any,
) -> PathWiring:
    # Three occasions need a fire turn: a reminder coming due, a compute job
    # that has finished and needs delivering, and a participation decision.
    fire_path = _wire_fire_path(
        metrics,
        quote_window,
        reasoning_strip_store,
        llm_state,
        allowlist,
        presence_reader,
        audit,
        lessons_reader,
        profile_reader,
        computer_context,
        registry,
        limits,
        lineage,
        memo_store,
        rosters,
        documents_context,
        note_store,
        doctrine_store,
        participation_runner,
        tag_trace_store,
        cfg,
        media_join,
        decision_log,
        clock,
        tool_registry,
        preview_source,
        shared,
        runner,
        persons,
        proactive_runner,
        memory,
        culture_reader,
        tool_call_log_store,
    )
    _path_lost_jobs(metrics, background, lost_jobs, slots, allowlist, fire_path)
    return PathWiring(fire_path=fire_path)


def _fire_turn_args(
    metrics: Any, quote_window: Any, reasoning_strip_store: Any, llm_state: Any,
    presence_reader: Any, audit: Any, lessons_reader: Any, profile_reader: Any,
    computer_context: Any, registry: Any, limits: Any, lineage: Any,
    memo_store: Any, rosters: Any, documents_context: Any, note_store: Any,
    doctrine_store: Any, participation_runner: Any, tag_trace_store: Any, cfg: Any,
    media_join: Any, decision_log: Any, clock: Any, tool_registry: Any,
    preview_source: Any, shared: Any, runner: Any, persons: Any,
    proactive_runner: Any, memory: Any, culture_reader: Any, tool_call_log_store: Any,
) -> Any:
    """The collaborators a fire turn shares with the tag path — one of each."""
    return {
        "tag_trace_store": tag_trace_store,
        "persons": persons,
        "records": audit,
        "memory": memory,
        "lineage": lineage,
        "shared": shared,
        "self_ids": registry.self_aliases(),
        "media_join": media_join,
        # The shared quote-window resolver (DEC-195). The SAME instance `build_assembler` above
        # holds — a fire turn reads the same `context_window`-sized transcript a tag reply does,
        # and a reply INSIDE that window can still quote a parent outside it, whether or not…
        "quote_window": quote_window,
        "doctrine_store": doctrine_store,
        "metrics": metrics,
        "runner": runner,
        "adapters": dict(registry.adapters),
        "registry": tool_registry,
        "canary": cfg.doctrine.canary,
        "display_name": cfg.doctrine.display_name,
        "ai_peer": cfg.honcho.ai_peer,
        "context_window": cfg.store.context_window,
        "limits": limits,
        "defer_tools": cfg.llm.defer_tools,
        # `[heartbeat]`, not `[llm]`: nobody is waiting on a fire turn, so
        # the 60s chosen for a person staring at a group chat is the wrong
        # bound.
        "timeout_seconds": cfg.heartbeat.timeout_seconds,
        "clock": clock,
        "rosters": rosters,
        # The SAME preview source the tag path holds. A fire turn is a
        # message into the same group, so a link in it earns the same card.
        "preview_source": preview_source,
        "llm_state": llm_state,
        "tool_call_log_store": tool_call_log_store,
        "reasoning_strip_store": reasoning_strip_store,
    }


def _fire_grounding_args(
    allowlist: Any, cfg: Any, clock: Any, computer_context: Any,
    culture_reader: Any, decision_log: Any, documents_context: Any, lessons_reader: Any,
    memo_store: Any, note_store: Any, presence_reader: Any, profile_reader: Any,
) -> Any:
    """The read-side grounding a fire determination grounds on."""
    return {
        # The reminder-derived Event registry (DEC-139): kind -> factory, wired here at the
        # composition root so the domain type stays free of app wiring. The registry holds the
        # kinds whose Events are PRODUCED BY a due Reminder row; compute/relay/lost-job call…
        "event_registry": {
            "reminder": reminder_occasion,
            # The proactive act turn: a row the Cognition session's `remind` writes, built
            # `person_id=None`. Registered unconditionally — a row of this kind can only exis…
            # writer was once wired, and an unregistered kind fails the row `failed_error` wi…
            "proactive": proactive_occasion,
        },
        # `<standing>` renders on the fire path too. A fire turn speaks into a room UNPROMPTED,
        # so «where had this conversation got to» is the context she is least likely to have any
        # other way: there is no tagging message to infer it from. Reads only; the fire path…
        "culture": culture_reader,
        # THE SWITCH (45 §5): the SAME profile reader the tag assembler
        # holds; `None` until `[memory.profile] read` is on.
        "profile": profile_reader,
        "lessons": lessons_reader,
        "standing_store_timeout_seconds": cfg.standing.store_timeout_seconds,
        # The participation turn's memory reads. ONE knob set, read at the composition root
        # exactly as the tag assembler reads `tagger_card_conclusions` — two readers each
        # carrying their own idea of how much memory a prompt may hold is how the fire path…
        "people_in_window_conclusions": cfg.honcho.people_in_window_conclusions,
        "people_in_window_speakers": cfg.honcho.people_in_window_speakers,
        "memory_read_timeout_seconds": cfg.honcho.read_timeout_seconds,
        # The shared decision log, written from `_determined` — the one method all four
        # determination exits already converge on. Participation and proactive act turns only; a
        # reminder firing is not a decision about whether to speak.
        "decisions": decision_log,
        # `<presence>` — the SAME reader the tag path's assembler holds, so a participation turn
        # or a reminder knows where it is the way a tag does. `<computer>`, the reader the tag
        # path's assembler holds; `None` while `[computer]` is off.
        "computer": computer_context,
        "documents": documents_context,
        "presence": presence_reader,
        # The note tier: the participation determination grounds on «transcript +
        # standing/notes». Wired for every fire occasion, gated exactly like the assembler's
        # reads above: the SAME store instance (already `None` while the tier's `enabled =…
        "notes": note_store,
        "memos": memo_store,
        "policy": allowlist,
    }


def _fire_occasion_args(cfg: Any, participation_runner: Any, proactive_runner: Any) -> Any:

    """The runners and shadow-review channels only certain fire occasions use."""
    return {
        # The participation occasion's own runner — None whenever the
        # occasion is disabled, which is also exactly when no participation
        # Event can reach `speak` (no policy marks one, no trigger exists).
        "participation_runner": participation_runner,
        # The proactive act turn's runner: the same auto-runner, under its own name. Gated on a
        # HOME ROOM existing, not on the adaptor being wired: a proactive row written yesterday
        # still comes due after `[dreamer] enabled` is flipped off, the screen below admits it,…
        "proactive_runner": proactive_runner,
        # The shadow mirror: wired only when the operator turned BOTH the occasion AND the
        # review surface on — a mirrored determination always cleared the room opt-in and the
        # enabled gate first. The channel id is validated non-empty by `ParticipationConfig`…
        "shadow_channel": (
            cfg.participation.shadow_channel
            if cfg.participation.enabled and cfg.participation.shadow
            else None
        ),
        "shadow_platform": cfg.participation.shadow_platform,
        # The Relevance Gate's OWN review channel, wired only when the gate is on AND in shadow.
        # Separate from the mirror above because the two report different things: that one
        # carries what she WOULD HAVE SAID, this one carries what the gate ADMITTED with no…
        "gate_shadow_channel": (
            cfg.relevance_gate.shadow_channel
            if cfg.relevance_gate.enabled and cfg.relevance_gate.shadow
            else None
        ),
        "gate_shadow_platform": cfg.relevance_gate.shadow_platform,
    }


def _wire_fire_path(
    metrics: Any, quote_window: Any, reasoning_strip_store: Any, llm_state: Any,
    allowlist: Any, presence_reader: Any, audit: Any, lessons_reader: Any,
    profile_reader: Any, computer_context: Any, registry: Any, limits: Any,
    lineage: Any, memo_store: Any, rosters: Any, documents_context: Any,
    note_store: Any, doctrine_store: Any, participation_runner: Any, tag_trace_store: Any,
    cfg: Any, media_join: Any, decision_log: Any, clock: Any,
    tool_registry: Any, preview_source: Any, shared: Any, runner: Any,
    persons: Any, proactive_runner: Any, memory: Any, culture_reader: Any,
    tool_call_log_store: Any,
) -> Any:
    """The real fire turn, built only when some occasion can fire one."""
    if not (
        cfg.heartbeat.enabled or cfg.compute.enabled or cfg.participation.enabled
    ):
        return None
    shared_kwargs = {
        "metrics": metrics,
        "quote_window": quote_window,
        "reasoning_strip_store": reasoning_strip_store,
        "llm_state": llm_state,
        "presence_reader": presence_reader,
        "audit": audit,
        "lessons_reader": lessons_reader,
        "profile_reader": profile_reader,
        "computer_context": computer_context,
        "registry": registry,
        "limits": limits,
        "lineage": lineage,
        "memo_store": memo_store,
        "rosters": rosters,
        "documents_context": documents_context,
        "note_store": note_store,
        "doctrine_store": doctrine_store,
        "participation_runner": participation_runner,
        "tag_trace_store": tag_trace_store,
        "cfg": cfg,
        "media_join": media_join,
        "decision_log": decision_log,
        "clock": clock,
        "tool_registry": tool_registry,
        "preview_source": preview_source,
        "shared": shared,
        "runner": runner,
        "persons": persons,
        "proactive_runner": proactive_runner,
        "memory": memory,
        "culture_reader": culture_reader,
        "tool_call_log_store": tool_call_log_store,
    }
    return FirePath(
        **_fire_turn_args(**shared_kwargs),
        **_fire_grounding_args(
            allowlist=allowlist,
            cfg=cfg,
            clock=clock,
            computer_context=computer_context,
            culture_reader=culture_reader,
            decision_log=decision_log,
            documents_context=documents_context,
            lessons_reader=lessons_reader,
            memo_store=memo_store,
            note_store=note_store,
            presence_reader=presence_reader,
            profile_reader=profile_reader,
        ),
        **_fire_occasion_args(
            cfg=cfg,
            participation_runner=participation_runner,
            proactive_runner=proactive_runner,
        ),
    )


def _path_lost_jobs(
    metrics: Any, background: Any, lost_jobs: Any, slots: Any,
    allowlist: Any, fire_path: Any,
) -> Any:

    if lost_jobs and fire_path is not None:
        # Spawned, never awaited — a boot must not wait on a model call, and
        # `BackgroundTasks` holds the strong reference, exactly as the
        # heartbeat and the poller do.
        background.spawn(
            _tell_about_lost_jobs(
                jobs=lost_jobs,
                speak=fire_path.speak,
                slots=slots,
                metrics=metrics,
                policy=allowlist,
            )
        )
    elif lost_jobs:
        # No fire path on this deployment, so there is no way to say it. Loud
        # rather than silent: somebody is still waiting on each of these.
        log.error(
            "%d compute job(s) lost across the restart and NOBODY CAN BE TOLD — "
            "this deployment wires no fire path (heartbeat, participation and "
            "compute all off)",
            len(lost_jobs),
        )
    return PathWiring(fire_path=fire_path)


@dataclass(frozen=True)
class HeartbeatWiring:
    proactive_screen: Any


def heartbeat(
    allowlist: Any, background: Any, cfg: Any, clock: Any,
    fire_path: Any, metrics: Any, note_store: Any, reminders: Any,
    slots: Any,
) -> HeartbeatWiring:

    # THE FIRE-TIME SCREEN for a `kind="proactive"` row, built unconditionally. It is a pure
    # value with no side effects. The screen belongs to the ROW, not to the loop, so asking
    # "would this row be refused" should not require turning the loop on.
    proactive_screen = ProactiveScreen(policy=allowlist, notes=note_store)

    if cfg.heartbeat.enabled and fire_path is not None:
        heartbeat = HeartbeatLoop(
            store=reminders,
            # The SAME `slots` the engagement pipeline holds. Two instances
            # would be two locks and no serialisation at all.
            slots=slots,
            # `allowlist` — policy.toml — NOT `policy`, which is the CompositePolicy deciding
            # whether a message is a tag and has no `is_allowed` at all. Re-checking the ALLOWLIST
            # at fire time is the whole point of this parameter: the live policy may change while a…
            policy=allowlist,
            clock=clock,
            metrics=metrics,
            tick_seconds=cfg.heartbeat.tick_seconds,
            fire=fire_path.run,
            # The `kind="proactive"` screen: the room's flag and the note's state, read through the
            # SAME allowlist and the SAME note store every other reader holds. Wired whenever the
            # heartbeat is, not only when the adaptor is: a proactive row written yesterday must be…
            proactive=proactive_screen,
        )
        background.spawn(heartbeat.run())
    return HeartbeatWiring(proactive_screen=proactive_screen)


@dataclass(frozen=True)
class ComputePollerWiring:
    pass


def compute_poller(
    allowlist: Any, background: Any, blobs: Any, cfg: Any,
    clock: Any, compute_backends: Any, compute_jobs: Any, document_service: Any,
    fire_path: Any, media_objects: Any, metrics: Any, slots: Any,
) -> ComputePollerWiring:

    # The compute-job poller: a background loop, gated on BOTH the feature flag and a live
    # `fire_path` (a compute job is delivered as a fire turn into the same conversation,
    # exactly as a reminder is). `compute_backends` is only non-empty when…
    if cfg.compute.enabled and fire_path is not None and compute_backends:
        compute_poller = ComputeJobPoller(
            store=compute_jobs,
            backends=compute_backends,
            documents=document_service if cfg.documents.enabled else None,
            # A finished clip's bytes are kept where a generated image's are (DEC-138): the
            # same blob store and media index, so `media_search` / `media_send` reach it.
            blobs=blobs,
            media=media_objects,
            # `speak`, NOT `run`: `run` is the heartbeat's reminder-shaped
            # adapter and would need rows this occasion does not have.
            speak=fire_path.speak,
            # The SAME `slots` every other path into a conversation holds.
            # Two would be two locks and no serialisation at all.
            slots=slots,
            metrics=metrics,
            tick_seconds=cfg.compute.poll_tick_seconds,
            clock=clock,
            policy=allowlist,
        )
        # Spawned, never awaited — the same reason `heartbeat.run()` above
        # is: a boot must not wait on a background
        # loop, and `BackgroundTasks` holds the strong reference.
        background.spawn(compute_poller.run())
    return ComputePollerWiring()


@dataclass(frozen=True)
class TriggerWiring:
    participation_trigger: Any


def trigger() -> TriggerWiring:

    # The participation occasion: trigger, then dispatcher. Gated on BOTH the flag and a
    # live `fire_path` — the same shape as the poller above. The trigger holds
    # `fire_path.speak` as a BOUND METHOD (`tests/test_composition.py` asserts the same of…
    participation_trigger: ParticipationTrigger | None = None
    return TriggerWiring(participation_trigger=participation_trigger)


@dataclass(frozen=True)
class GateWiring:
    gate: Any
    participation_trigger: Any


def build_gate(
    audit: Any, cfg: Any, doctrine_store: Any, engine: Any,
    fire_path: Any, media_join: Any, metrics: Any, model_client_for: Any,
    persons: Any, route_model: Any, slots: Any, standing_store: Any,
) -> GateWiring:
    # Bound in the branch below, beside the trigger that holds it.
    gate: RelevanceGate | None = None
    participation_trigger: ParticipationTrigger | None = None
    if cfg.relevance_gate.enabled and fire_path is not None:
        # The gate is built HERE and not beside the other model clients, because it is the only
        # one whose absence would change behaviour silently: a trigger with `gate=None` admits
        # every closed window. Constructed in the same branch that constructs the trigger, so…
        gate = RelevanceGate(
            client=_RelevanceGateModelClient(
                # The process-wide `AsyncOpenAI` (DEC-13), not a second transport of its own: the…
                # model is its own combo, but the connection pool, the retry policy and the base…
                # the deployment's one client.
                client=model_client_for("relevance_gate"),
                model=route_model("relevance_gate", cfg.relevance_gate.model),
                max_tokens=cfg.relevance_gate.max_tokens,
                temperature=cfg.relevance_gate.temperature,
                timeout_seconds=cfg.relevance_gate.timeout_seconds,
                reasoning_effort=_reasoning_effort(
                    cfg.relevance_gate.reasoning_effort,
                    source="[relevance_gate] reasoning_effort",
                ),
            ),
            # The SAME store the turn reads, at a DIFFERENT audience. The gate gets
            # `participation.md` and nothing else; Nora never sees that file and the gate never sees
            # hers. Hot-reloads on mtime, which is what makes the shadow soak a tuning loop instead…
            prompts=doctrine_store,
            records=audit,
            metrics=metrics,
            # DEC-80's projection, the SAME `Persons` the fire path and the tag path use. Without it
            # `person_name` is None on every row and the gate labels speakers by their self-set
            # platform profile name — the two-name-space bug, on the one call that reads every…
            persons=persons,
            # The SAME standing store the fire path reads (`None` while
            # `[standing] enabled = false`), so the gate and the turn behind
            # it see one room and not two.
            standing=standing_store if cfg.standing.enabled else None,
            transcript_limit=cfg.relevance_gate.transcript_rows,
            # The SAME number the client above is built with, handed to the gate as well. Not belt-
            # and-braces: the client's `wait_for` is a property of THIS wiring, and the gate's own
            # `decide` promise — never raise, never hang — has to hold against whatever satisfies…
            call_timeout_seconds=cfg.relevance_gate.timeout_seconds,
            # The SAME join the turn behind it runs. Without it the gate reads an image-only message
            # as an EMPTY BODY, `render_context` drops the row, and the window a photo prompted is
            # the one the gate is least able to see — a systematic under-admission exactly where…
            media_join=media_join,
            # NO `capabilities`: giving the gate her derived read-only tool surface made it STRICTER
            # and worse on the eval suite (more false NO_GO, no capability improved) and adds prompt
            # tokens on every quiet window in every opted-in room.…
        )
        participation_trigger = ParticipationTrigger(
            speak=fire_path.speak,
            slots=slots,
            # Still `[participation]`'s: the quiescence window
            # belongs to the TRIGGER, which is shared, and the gate is what
            # runs when a window closes — not what defines one.
            settle_seconds=cfg.participation.settle_seconds,
            max_window_messages=cfg.participation.max_window_messages,
            cooldown_seconds=cfg.participation.cooldown_minutes * 60,
            gate=gate,
            # Shadow is DECIDED here, once: passing the poster IS shadow mode.
            # A boolean flag plus a poster would admit a fourth state (shadow
            # on, nowhere to post) that silently behaves like live.
            gate_shadow=(fire_path.post_gate_verdict if cfg.relevance_gate.shadow else None),
            # The per-room watermarks, persisted so the SEND cooldown survives a force-recreate:
            # otherwise she could speak again at once however recently she had spoken, and the
            # cooldown's whole job is to cap what a room HEARS.
            store=SqlParticipationStateStore(engine),
        )
    elif cfg.participation.enabled:
        # The CONVERSE of `_the_gate_needs_something_behind_it` (nora/config.py), and it cannot
        # live there: that validator refuses a gate with no turn behind it, which is a config
        # error. This is a turn with no gate in FRONT of it, which is a legal config that…
        log.error(
            "participation.enabled = true but relevance_gate.enabled = false — "
            "NOTHING will happen: since design/dreamer-agent-loop/36 the "
            "quiescence trigger and the room policy are the gate's, so with the "
            "gate off no window is ever noted, judged or turned into a turn. "
            "Set [relevance_gate] enabled = true (it ships in shadow mode)."
        )
    elif cfg.relevance_gate.enabled:
        # Reachable only if the fire-path gate above stopped including
        # `cfg.participation.enabled` — loud rather than inert, because a
        # trigger-less participation flag would otherwise look healthy.
        log.error(
            "relevance_gate.enabled = true but no fire path was built — no "
            "window can ever be judged and no turn can ever run. Check the "
            "FirePath gate."
        )
    return GateWiring(gate=gate, participation_trigger=participation_trigger)
