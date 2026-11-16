"""The `!!!member` room-command occasion — `EngagementPipeline`'s command
branch, kept at the stage seam like `admission`/`indicate`: every
collaborator arrives explicitly (the pipeline's own fields), so this is the
pipeline's body split along a responsibility boundary, not a second
command path or a new port.

The occasion's contract is `_run_turn`'s own. ONE owner releases the
conversation slot on every exit — complete, refused, malformed, faulted
or cancelled. On the direct path that owner is this stage's `finally`:
the caller acquired the slot via admission's RUN_NOW acquire or a queued
dispatch's re-acquire inside `release_and_dispatch`. On the durable path
(`disposition` set) the WORKER owns it: `WorkRunner._dispatch` acquired
via `try_acquire` and its `finally` releases — a second release here
would discard the slot a just-dispatched queued turn believes it holds
and pop a SECOND factory behind it, overlapping two follow-ups. A
release that skips any exit strands whatever queued behind the command
on a held slot, which is exactly the failure the first review found.

`disposition` is the durable worker's terminal record. Delivered and
refused answers map onto the existing done vocabulary; a
`send_uncertain`/`send_failed`/`audit_write_failed` deliverer outcome
retries through the same claim/fence path a tag turn does — an uncertain
send's action row is what the started-action fence reads, so the next
attempt answers `uncertain` instead of either claiming completion over a
send that may have landed or blindly repeating it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nora.agent.outbound import OutboundComposer
from nora.domain import audience as audience_names
from nora.domain.ports import EngagementRequest
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.tracing import annotate

from . import deliver, indicate
from .indicate import Indicator

log = logging.getLogger("nora.engagement.stages.command")

# `last_outcome` values that are NOT a deterministic refusal — they retry
# (`send_uncertain` through the started-action fence). Guard refusals are
# terminal `vetoed`, like the tag path's own.
_RETRYABLE_OUTCOMES = frozenset(
    {"send_uncertain", "send_failed", "audit_write_failed"})


async def run(
    *, request: EngagementRequest, identity: Any, indicator: Indicator | None,
    adapter: Any = None, disposition: dict[str, str] | None = None,
    commands: Any, policy: Any, audit: Any, adapter_for: Any,
    doctrine_store: Any, canary: str, tool_registry: Any, metrics: Any,
    access: Any, rosters: Any, preview_source: Any, spawn: Any,
    reasoning_strip_store: Any, tool_call_log_store: Any, slots: Any,
) -> None:
    """Parse, invoke, reply — no model, no tools, the same audit-first
    `Deliverer` a tag's reply takes."""
    msg = request.message
    key = (msg.platform, msg.conversation_id)
    started = time.perf_counter()
    delivered = False
    outcome = "unavailable"
    bind_tag_id(request.tag_id)
    try:
        # A `None` command object is a composition that never wired the
        # people area; the message was already ingested, so the honest thing
        # is silence, not a wrong reply — and the finally still releases.
        if commands is None:
            return
        parsed = commands.parse(msg)
        if parsed is None:
            outcome = "silent"
            return
        convo = policy.conversation(msg.platform, msg.conversation_id)
        allowed_mcp = convo.tools if convo is not None else frozenset()
        handle = deliver.build(
            store=audit, adapter=adapter or adapter_for(msg.platform),
            target=msg.target(),
            doctrine=doctrine_store.get(applies_to="tag"),
            canary=canary, tag_id=request.tag_id,
            nora_sender_id=identity.primary,
            conversation_id=msg.conversation_id,
            budgets=tool_registry.budgets(allowed_mcp=allowed_mcp),
            terminal=tool_registry.terminal_tools(), metrics=metrics,
            param_names=tool_registry.tool_param_names(),
            audience=audience_names.TAG,
            cacheable_tools=tool_registry.cacheable_tools(),
            tool_call_log_store=tool_call_log_store,
            reasoning_strip_store=reasoning_strip_store,
            composer=OutboundComposer(
                platform=msg.platform, conversation_id=msg.conversation_id,
                metrics=metrics, access=access,
                roster_source=rosters.get(msg.platform),
                preview_source=preview_source,
                content_checks=tool_registry.content_checks,
            ),
            on_delivered=indicate.DeliverySettler(
                indicator=indicator, spawn=spawn,
            ),
            content_checks=tool_registry.content_checks,
        )
        reply = parsed if isinstance(parsed, str) else commands.render(
            await commands.invoke(msg, parsed)
        )
        delivered = await handle.deliverer.deliver(reply, source="command")
        if delivered:
            outcome = "send_reply"
        else:
            last = handle.deliverer.last_outcome
            outcome = last if last in _RETRYABLE_OUTCOMES else "vetoed"
    except BaseException:
        outcome = "handler_error"
        log.exception(
            "room command failed (tag=%s); staying silent", request.tag_id,
        )
        # `audience="tag"` as a literal — this pipeline's only audience;
        # the same label the tag path's own outer handler emits, so the
        # command's silent fault lands on the same alarm surface.
        metrics.inc(
            "nora_silent_failures_total", reason="handler_error",
            platform=msg.platform, audience="tag",
        )
    finally:
        if disposition is not None:
            disposition["value"] = outcome
        # Resolved on every exit, exactly as the tag path's `finally` does:
        # a faulted command must not strand its 👀, and a delivered reply
        # settles ✅ through the same latch.
        indicate.resolve(
            indicator=indicator, delivered=delivered, spawn=spawn,
        )
        # Released on every DIRECT-path exit for the reason the module
        # docstring gives — the same ONE call the tag path's `finally`
        # makes: the release and the queued request's dispatch are not
        # separable. The durable caller (`disposition`) already acquired
        # the slot its own `finally` releases; releasing here too is the
        # double-release the second review found.
        if disposition is None:
            slots.release_and_dispatch(key)
        annotate(**{
            "nora.outcome": outcome,
            "nora.delivered": delivered,
            "nora.latency_seconds": time.perf_counter() - started,
        })
        clear_tag_id()
