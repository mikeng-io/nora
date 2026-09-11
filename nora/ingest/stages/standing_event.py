"""Stage: standing event trigger — the event-driven fold path
(design/standing-watermark/10).

**The problem this stage exists for.** Standing used to fold only when a tag
arrived, reading the LAST 40 rows of the reply path's window. A conversation
that passed 40 messages between tags lost the middle. The fix is a row-id
watermark: every fold reads `since(last_folded_row_id)` capped at
`[standing] fold_batch`, and the fold is triggered EVENT-DRIVEN from the ingest
path — every message, tagged or not, spawns a check that folds when the
accumulator reaches the batch cap (D1: one number, `fold_batch`).

**It is a CHECK, not a loop** (§10). No background tick: the check rides the
ingest path (scheduled off the listener's await path via the injected `spawn`,
exactly like `memory_feed`), reads the conversation's watermark, counts the
rows since it, and enqueues an event fold only when the count reaches
`fold_batch`. Zero work when nothing happened; fresh by construction when
something did.

**The event fold is the SAME fold as the tag path, minus the turn.**
(`nora.engagement.stages.debrief`) — same objective, same store, same two-call
split (standing + verdict via `asyncio.gather`, D4: the verdict reads the same
batch). The differences are deliberate:

- **Serialization is the existing `ConversationSlots`** (D2): the check
  ENQUEUES the fold through the per-conversation FIFO (it never
  `try_acquire`-skips like the heartbeat) — a tag that arrives while an event
  fold is in flight queues behind it, and an event fold arriving while a tag
  runs queues behind the tag. One slot, FIFO order, never dropped.
- **Rollup folds do NOT increment `fold_count`** (D3): that counter stays the
  tag path's ceiling feed (`rebuild_after_folds`); rollup folds count on their
  own series (`nora_standing_rollup_folds_total`).
- **No tag, so no `TurnVerdict` and no notes.** The verdict CALL still runs
  (D4 — the two calls must not disagree about what happened), but its output
  has no `tag_id` to key a `TurnVerdict` on, so it is not persisted.
- **The rollup is bounded** (`[standing] rollup_max_folds`): one spawned event
  fold folds at most that many batches (each `fold_batch` rows), then stops and
  lets the NEXT message's check continue. A cold-start backlog folds
  `ceil(history / fold_batch)` times across messages, never unboundedly in one
  spawn (30-rollup.md).

**Restart safety is by construction** (§10): the queue is in-memory scheduling,
the watermark is Postgres state. A restart loses queued folds; the next
message's check re-reads the watermark, re-counts, and re-triggers. No message
and no backlog is lost.

**Failures are cheap by design.** The check is spawned, so a slow or failing
fold never blocks the message it was triggered by; a fold failure is logged and
counted on `nora_debrief_total{outcome=...}` exactly as the tag path's fold
failure is (10-event-trigger §Failure modes). The message is already recorded;
a missed fold is a stale position, not a lost message.

One public entry point: `run(...)` — everything else is private.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import tzinfo
from typing import Any

from nora.agent.dream_screen import screen
from nora.domain.media_join import MediaJoin
from nora.domain.models import NormalizedMessage, TranscriptRow
from nora.domain.ports import RecordStore, StandingStore
from nora.domain.standing import Standing
from nora.engagement.debrief_render import (
    render_rollup_input,
    render_rollup_verdict_input,
)
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages.debrief import (
    OBJECTIVE,
    VERDICT_OBJECTIVE,
    Completion,
    ModelClient,
    ObjectiveSource,
)
from nora.engagement.stages.debrief import (
    _check as _debrief_check,
)
from nora.engagement.stages.debrief import (
    _complete as _debrief_complete,
)
from nora.engagement.stages.debrief import (
    _prompt as _debrief_prompt,
)
from nora.engagement.stages.debrief import (
    _verdict_prompt as _debrief_verdict_prompt,
)
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, attached, span

log = logging.getLogger("nora.ingest.standing_event")

Key = tuple[str, str]


@dataclass(frozen=True)
class Wiring:
    """Everything the event trigger needs, as ONE object that is either
    present or absent — the same shape `debrief.Wiring` uses, and for the same
    reason: a stage that half-wires its collaborators runs, finds nothing to
    do, and reports nothing (R-27's unavailable-that-looks-like-nothing_found).
    The composition root builds it when `[standing] enabled AND
    [standing] event_enabled`; `None` otherwise leaves the stage inert.

    `store` is the standing store (watermark + position reads/writes);
    `record_store` is the audit store the watermark batch is read from
    (`since_including_nora`); `slots` is the SAME `ConversationSlots` the tag
    path holds — two would be two locks and no serialisation at all. `client`
    and `objective` are the same fold collaborators the debrief uses.
    """

    store: StandingStore
    record_store: RecordStore
    client: ModelClient
    objective: ObjectiveSource
    slots: ConversationSlots
    display_name: str
    fold_batch: int
    rollup_max_folds: int
    timeout_seconds: float
    store_timeout_seconds: float
    max_chars: int
    tz: tzinfo | None = None
    # The shared media join (issue #116). The fold reads the SAME transcript
    # rows the reply path does and renders them for a model, so it needs the
    # same answer to «what did that photo show» — on master the description was
    # in `messages.body` and every consumer had it. `None` is the inert state
    # and renders the bare handles the row actually carries.
    media_join: MediaJoin | None = None


async def run(
    *,
    message: NormalizedMessage,
    wiring: Wiring,
    metrics: Metrics,
    spawn: Callable[[Coroutine[Any, Any, None]], None],
) -> None:
    """Schedule the check; NEVER await it inline.

    The ingest pipeline runs this after the audit write (the row exists) and
    before/around the gate. Like `memory_feed`, this stage spawns the actual
    work — the check reads two stores and may enqueue a fold, so an inline
    await would stall the listener's socket read for every conversation on the
    adapter.
    """
    spawn(_check(message=message, wiring=wiring, metrics=metrics, spawn=spawn))


async def _check(
    *,
    message: NormalizedMessage,
    wiring: Wiring,
    metrics: Metrics,
    spawn: Callable[[Coroutine[Any, Any, None]], None],
) -> None:
    """The accumulator check (D1): read the conversation's watermark, count
    rows since it, and enqueue an event fold only when the count reaches
    `fold_batch`.

    Two store reads, both bounded. A read that fails is R-27's «I could not
    check» — logged, no fold, and the next message retries; the watermark never
    moves on a check, so nothing is lost.
    """
    key: Key = (message.platform, message.conversation_id)
    try:
        prior = await asyncio.wait_for(
            wiring.store.read(
                platform=message.platform, conversation_id=message.conversation_id,
            ),
            timeout=wiring.store_timeout_seconds,
        )
    except Exception:
        log.exception(
            "standing event check: could not read the watermark (%s); "
            "not checking this message", key,
        )
        return
    # D5: NULL watermark = read from the beginning (cold start, no backfill).
    after_id = (
        prior.last_folded_row_id
        if prior is not None and prior.last_folded_row_id is not None else 0
    )
    try:
        rows = await asyncio.wait_for(
            wiring.record_store.since_including_nora(
                conversation_id=message.conversation_id,
                after_id=after_id,
                limit=wiring.fold_batch,
            ),
            timeout=wiring.store_timeout_seconds,
        )
    except Exception:
        log.exception(
            "standing event check: could not count rows since the watermark "
            "(%s); not checking this message", key,
        )
        return
    # The gauge: rows behind the watermark, as the accumulator read measures
    # them (capped at fold_batch — the read's limit — by construction).
    metrics.gauge("nora_standing_watermark_gap", value=len(rows))
    if len(rows) < wiring.fold_batch:
        return  # accumulator below the threshold — wait for the next message
    metrics.inc("nora_standing_event_folds_total")
    # D2: ENQUEUE, never try_acquire-skip. try_acquire succeeds iff the slot is
    # free (acquiring it); when busy, the fold waits in the per-conversation
    # FIFO and is started by the current holder's release_and_dispatch.
    if wiring.slots.try_acquire(key):
        spawn(_event_fold(key=key, wiring=wiring, metrics=metrics))
    else:
        wiring.slots.enqueue(
            key, lambda: _event_fold(key=key, wiring=wiring, metrics=metrics),
        )


async def _event_fold(*, key: Key, wiring: Wiring, metrics: Metrics) -> None:
    """The event fold: loop within `rollup_max_folds` bounds, each iteration
    re-reading the watermark, folding the next batch, and advancing the
    watermark. ALWAYS releases the conversation's slot in its finally — both
    when it acquired the slot itself (via `try_acquire`) and when it was
    started by a previous holder's `release_and_dispatch` (which re-acquires
    before spawning).

    The loop folds ANY non-empty remainder (30-rollup.md's `200+200+100`):
    the trigger only fires at the full batch, but once fired the rollup drains
    until caught up or the bound. A refused fold (malformed, overflow,
    screened, write failure) stops the loop — the watermark did not advance, so
    the next message's check retries the same stretch.

    **Its own ROOT span** (issue #209), opened under `attached(None)` — never
    a child of whatever the caller had current. `spawn` copies the context of
    the task that calls it (`nora.obs.tracing.capture_context`'s docstring),
    and this coroutine is spawned from two different places: the ingest
    path's own `plumbing` root (`_check`, dropped at the sampler — R-76), and
    `ConversationSlots.release_and_dispatch`'s `finally`, which may run with
    no span in context at all or with some unrelated turn's span current.
    Either way the fold's two model calls (`engagement.debrief.fold` and
    `.verdict`, via `debrief._complete`) must reach the collector under a
    trace named for what they are, not vanish under a dropped root or surface
    as an unnamed child of somebody else's turn.
    """
    platform, conversation_id = key
    try:
        with attached(None), span(
            "standing.event_fold",
            **{
                TRACE_KIND: "generation",
                "nora.platform": platform,
                "nora.conversation_id": conversation_id,
            },
        ):
            for _ in range(wiring.rollup_max_folds):
                try:
                    prior = await asyncio.wait_for(
                        wiring.store.read(platform=platform, conversation_id=conversation_id),
                        timeout=wiring.store_timeout_seconds,
                    )
                except Exception:
                    log.exception(
                        "standing event fold: could not read the prior (%s); "
                        "stopping this rollup", key,
                    )
                    break
                after_id = (
                    prior.last_folded_row_id
                    if prior is not None and prior.last_folded_row_id is not None else 0
                )
                try:
                    rows = await asyncio.wait_for(
                        wiring.record_store.since_including_nora(
                            conversation_id=conversation_id,
                            after_id=after_id,
                            limit=wiring.fold_batch,
                        ),
                        timeout=wiring.store_timeout_seconds,
                    )
                except Exception:
                    log.exception(
                        "standing event fold: could not read the batch (%s); "
                        "stopping this rollup", key,
                    )
                    break
                if not rows:
                    break  # caught up — nothing left since the watermark
                if wiring.media_join is not None:
                    # The same join the tag path and the debrief run: this fold
                    # renders these rows for a model, so it must see what a photo
                    # showed or the position it forms is built on a bare handle.
                    #
                    # BOUNDED, like its two siblings above and for a harder reason
                    # than symmetry: this loop holds the conversation's slot until
                    # `finally: slots.release_and_dispatch(key)`, and `enrich`
                    # catches EXCEPTIONS, not hangs. A wedged `media_objects` would
                    # hold that room's rollup slot indefinitely. It was the only
                    # unbounded await on a slot-held path in the repo — the debrief
                    # got this right in the same commit because it has an AST
                    # validator deriving its receiver set from `run`'s parameters,
                    # and this module had none. It has one now
                    # (`tests/architecture/test_slot_held_awaits_are_bounded.py`).
                    try:
                        rows = list(await asyncio.wait_for(
                            wiring.media_join.enrich(rows, path="standing_event"),
                            timeout=wiring.store_timeout_seconds,
                        ))
                    except Exception:
                        log.exception(
                            "standing event fold: media join failed (%s); folding "
                            "on the bare handles", key,
                        )
                if not await _fold_batch(
                    prior=prior, rows=rows, key=key, wiring=wiring, metrics=metrics,
                ):
                    break  # refused — the watermark did not advance
                metrics.inc("nora_standing_rollup_folds_total")
    finally:
        wiring.slots.release_and_dispatch(key)


async def _fold_batch(
    *,
    prior: Standing | None,
    rows: Sequence[TranscriptRow],
    key: Key,
    wiring: Wiring,
    metrics: Metrics,
) -> bool:
    """Fold ONE batch: the same two-call split as the tag path, under ONE
    `wait_for` budget (the slot can afford a total, and two independent
    timeouts would each spend the whole total). Returns True iff the position
    was WRITTEN (and the watermark advanced); False on any refusal, with the
    failure counted on `nora_debrief_total{outcome=...}` exactly as the tag
    path's fold failure is.

    The standing call reads `render_rollup_input` (prior + conversation);
    the verdict call reads `render_rollup_verdict_input` (the SAME batch,
    without the prior — D4). The verdict's output is not persisted: it has no
    tag_id to key a `TurnVerdict` on, and the call exists to keep the two
    calls' shared-batch discipline identical.
    """
    platform, conversation_id = key
    instructions = wiring.objective.objective_body(OBJECTIVE)
    verdict_instructions = wiring.objective.objective_body(VERDICT_OBJECTIVE)
    rendered = render_rollup_input(
        prior=prior, rows=rows, display_name=wiring.display_name, tz=wiring.tz,
    )
    rendered_verdict = render_rollup_verdict_input(
        rows=rows, display_name=wiring.display_name, tz=wiring.tz,
    )

    async def _standing() -> tuple[str | None, str]:
        system, user = _debrief_prompt(
            instructions=instructions, rendered=rendered, max_chars=wiring.max_chars,
        )
        # Through `debrief._complete` (issue #209), the same GENERATION
        # wrapper the tag path's fold call uses, so this renders as
        # `engagement.debrief.fold` with a model, a prompt digest and a
        # `finish_reason` instead of an unwrapped `client.complete`. No tag
        # exists on this path, so `tag_id=""` — the honest value, not a
        # borrowed or fabricated one.
        reply = await _debrief_complete(
            wiring.client, call="fold", tag_id="", conversation_id=conversation_id,
            prompt=(system, user),
        )
        # `conversation_id` stands in for the tag id in the refusal logs: a
        # rollup fold has no tag, and the parse rules are the tag path's.
        return _debrief_check(reply, conversation_id)

    async def _judge() -> Completion:
        system, user = _debrief_verdict_prompt(
            instructions=verdict_instructions, rendered=rendered_verdict,
        )
        return await _debrief_complete(
            wiring.client, call="verdict", tag_id="", conversation_id=conversation_id,
            prompt=(system, user),
        )

    try:
        (body, refusal), _verdict = await asyncio.wait_for(
            asyncio.gather(_standing(), _judge()),
            timeout=wiring.timeout_seconds,
        )
    except TimeoutError:
        metrics.inc("nora_debrief_total", outcome="timeout")
        log.warning(
            "standing event fold: the model call did not finish in time (%s); "
            "the watermark did not advance", key,
        )
        return False
    except Exception:
        metrics.inc("nora_debrief_total", outcome="error")
        log.exception(
            "standing event fold failed (%s); the watermark did not advance",
            key,
        )
        return False
    if body is None:
        metrics.inc("nora_debrief_total", outcome=refusal)
        log.warning(
            "standing event fold refused (%s outcome=%s); the watermark did "
            "not advance", key, refusal,
        )
        return False
    # The SAME overflow refusal the tag path makes: nothing is written and the
    # PRIOR position stands. No consolidate retry here — the tag path's retry
    # exists because a fold failure in the live slot is expensive; a rollup
    # refusal costs the next message's check, which retries the same stretch.
    if len(body) > wiring.max_chars:
        metrics.inc("nora_standing_overflow_refusals_total", stage="rollup")
        metrics.inc("nora_debrief_total", outcome="refused_overflow")
        log.warning(
            "standing event fold: the position would not fit %d characters "
            "(%s); keeping the prior position",
            wiring.max_chars, key,
        )
        return False
    # §4.3's disclosure screen, same as the tag path: this row is injected into
    # every subsequent turn, so a coordinate reaching it persists by design.
    kept, _rejected = screen(
        adaptor="debrief", items=[{"standing": body}], metrics=metrics,
    )
    if not kept:
        metrics.inc("nora_debrief_total", outcome="screened")
        log.warning(
            "standing event fold: the position was screened out (%s); keeping "
            "the prior position", key,
        )
        return False
    revised = Standing(
        platform=platform,
        conversation_id=conversation_id,
        body=body,
        # D3: rollup folds do NOT increment fold_count — that counter stays
        # the tag path's ceiling feed. Carried forward from the prior (0 on a
        # first fold).
        fold_count=prior.fold_count if prior is not None else 0,
        updated_at=int(time.time() * 1000),
        consumed_verdict_tag=(
            prior.consumed_verdict_tag if prior is not None else None
        ),
        # The watermark advances to the LAST row this batch read (the rows are
        # ascending, so `rows[-1]` is the furthest). Written in the SAME
        # statement as the position (20-watermark-read.md).
        last_folded_row_id=rows[-1].id,
    )
    try:
        await asyncio.wait_for(
            wiring.store.write(standing=revised),
            timeout=wiring.store_timeout_seconds,
        )
    except Exception:
        metrics.inc("nora_debrief_total", outcome="error")
        log.exception(
            "standing event fold: could not write the position (%s); the "
            "watermark did not advance", key,
        )
        return False
    return True
