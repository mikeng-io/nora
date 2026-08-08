"""`FirePath` — one due reminder becomes one actual message.

WHAT THIS MODULE IS RESPONSIBLE FOR
-----------------------------------
The TURN. `nora.app.heartbeat` owns *when* a reminder fires, *whether* policy
still allows it and *what the row then says*; this owns everything between the
two — the synthetic `TagContext`, the prompt, the model call, the delivery, and
the §8.1 outcome string it answers with.

It reuses the tag pipeline's stages where they fit and refuses them where they
do not, and the refusals are the design (design spec §5's table):

* **no `admission.run`** — R-9's debounce would reject a fire turn within five
  seconds of a tag AND consume the tag's window; R-30 sheds the NEWEST arrival,
  which is always the reminder that has waited hours; R-12 tests a synthetic
  sender against Nora's own aliases. The conversation slot is taken by the tick
  instead, through the same `ConversationSlots` the tag path holds.
* **no `audit_stage` and no `memory_feed_stage`** — both would write a message
  the requester never sent, under the requester's peer, at the wrong time.
* **no `indicate`** — `indicate.begin` reacts to `(author, timestamp)` and
  returns `None` when either is falsy, which the synthetic message's blank
  `sender_id`/`timestamp` make true BY CONSTRUCTION. Fabricating them means a
  failed `sendReaction` every tick, or 👀 then ⚠️ on an unrelated old message.
* **no `ContextAssembler.assemble`** — see `build_context`. This is the trap.
* **no `invoke.run` and no `deliver.check_outcome`** — see `run`.

WHAT IT DOES REUSE, AND WHY EACH ONE MATTERS
--------------------------------------------
`MentionRenderer.rehydrate` (or every @-mention in the transcript reaches the
model as U+FFFC), `build_prefill`/`build_tag_message` (or R-13/R-13b escaping
of group-authored text stops being pinned by its two named acceptance tests),
`deliver.build` + `Deliverer` (the ONLY caller of `adapter.send`, so R-2, R-4,
R-6 and R-18 all still apply), and `feedback.run` (a fire turn is still
something she said; omitting it makes her memory of her own speech incomplete).

Doctrine is read at `applies_to="heartbeat"`, which is why that audience had to
exist before this module did: at `"tag"` it would carry the silence rules that
are false here, and an audience that resolved to nothing would deliver with no
persona, no constraints, and a `LeakGuard` corpus shrunk to the capability
block.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from nora.agent.prefill import build_prefill, build_tag_message, escape
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import NormalizedMessage, PersonRef, TagContext, TranscriptRow
from nora.domain.ports import MemoryStore, PlatformAdapter, RecordStore, RosterSource
from nora.domain.reminders import Reminder
from nora.engagement.mentions import MentionRenderer
from nora.engagement.stages import deliver, feedback
from nora.engagement.stages.invoke import AgentRunner
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.obs.tracing import trace_attributes
from nora.persons import Persons

log = logging.getLogger("nora.app.fire")

# The `audience` label every `nora_silent_failures_total` this path raises
# carries — including the five inside `Deliverer`, which is why `deliver.build`
# takes it. A8 (`docs/runbook.md`) filters on `audience="tag"`, so a fire-turn
# failure recorded without this would be charged to a <1% budget whose
# denominator counts only tags.
AUDIENCE = "heartbeat"

# §8.1's four outcomes. The heartbeat derives the ROW's terminal state from the
# string (`_state_for`: only `sent` is DONE), so these are not log text —
# spelling one differently records a delivery that never happened.
SENT = "sent"
FAILED_POLICY = "failed_policy"
FAILED_TIMEOUT = "failed_timeout"
FAILED_ERROR = "failed_error"


class FirePath:
    """One fire turn, end to end. Every collaborator arrives through `__init__`.

    `clock` is here for the same reason it is on `HeartbeatLoop`: the header
    states when the reminder was set and when it came due, and a module that
    read the wall clock itself could not be tested at a fixed instant.
    """

    def __init__(
        self,
        *,
        persons: Persons,
        records: RecordStore,
        memory: MemoryStore,
        doctrine_store: DoctrineStore,
        metrics: Metrics,
        runner: AgentRunner,
        adapter: PlatformAdapter,
        registry: ToolRegistry,
        canary: str,
        display_name: str,
        nora_sender_id: str,
        ai_peer: str,
        context_window: int,
        limits: dict[str, Any],
        timeout_seconds: float,
        clock: Callable[[], datetime],
        roster_source: RosterSource | None = None,
        llm_state: Any = None,
    ) -> None:
        self._persons = persons
        self._records = records
        self._memory = memory
        self._doctrine_store = doctrine_store
        self._metrics = metrics
        self._runner = runner
        self._adapter = adapter
        self._registry = registry
        self._canary = canary
        self._display_name = display_name
        self._nora_sender_id = nora_sender_id
        self._ai_peer = ai_peer
        self._context_window = context_window
        self._limits = limits
        # `[heartbeat] timeout_seconds`, NOT `[llm] timeout_seconds`. A fire
        # turn has nobody waiting on it, so the tag path's 60s — chosen because
        # a human is staring at a group chat — is the wrong bound here.
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        # Optional for the reason it is optional on the pipeline: `Room` reports
        # an absent source as UNAVAILABLE rather than as an empty room, so a
        # harness degrades honestly instead of answering "nobody is here" (R-5).
        self._roster_source = roster_source
        self._llm_state = llm_state

    async def build_context(self, rows: Sequence[Reminder]) -> TagContext:
        """§6.4's table, assembled HERE rather than by `ContextAssembler`.

        **This is the trap the whole section exists for.** The synthetic message
        carries a blank `sender_id` on purpose — that is what makes §5.5's "no
        reactions" hold by construction. But `TranscriptInjection.assemble`
        resolves the tagger as `persons.resolve(platform, msg.sender_id)`, and a
        blank id resolves to `None`. Every one of these then follows, silently,
        with every gate still green:

        * `ctx.tagger is None`, so `peer_card` is `()` — she remembers nothing
          about the person she is answering
        * `Room(tagger=None).scope()` returns UNAVAILABLE, so every room-scoped
          tool goes dark
        * `search_memory` refuses outright
        * §12's `tagger is None -> refuse` gate fires on EVERY fire turn, for a
          reason that has nothing to do with trust

        So the tagger comes from the row's `requester_person_id` — the id the
        tick already screened `policy.is_allowed` against — through
        `Persons.by_person`, and never from the message.

        `sender_name` has the same shape and is the second half of the same
        trap: `Persons.display_name(msg)` reads `msg.sender_id` and would
        return `""` here. It comes off the `PersonRef` too.

        Async because both its sources are: `RecordStore.recent` and
        `MemoryStore.peer_card`. A synchronous signature would push an
        implementer toward an empty transcript and an empty card — a fire turn
        with no context at all, and nothing anywhere saying so.
        """
        row = rows[0]
        # `.get`, not `[...]`: identity.toml loads once at startup and a row may
        # be thirty days old, so the person it names may have left the file. A
        # KeyError here would surface as `failed_error` with a traceback; `run`
        # refuses it as `failed_policy` with a sentence instead.
        ref = self._persons.by_person.get(row.requester_person_id)

        transcript: tuple[TranscriptRow, ...] = ()
        try:
            transcript = tuple(await self._records.recent(
                conversation_id=row.conversation_id, limit=self._context_window,
            ))
        except Exception:
            # `TranscriptInjection`'s behaviour, deliberately copied: a thinner
            # turn beats no turn (R-5-adjacent). The counter is what says the
            # turn ran blind.
            log.exception("fire context read failed; continuing with an empty transcript")
            self._metrics.inc("nora_errors_total", stage="fire_context", platform=row.platform)

        card: tuple[str, ...] = ()
        if ref is not None:
            try:
                card = tuple(await self._memory.peer_card(peer_id=ref.honcho_peer_id))
            except Exception:
                # Guarded where `TranscriptInjection`'s equivalent is not, and
                # the asymmetry is §8.2: at-most-once means a raise here writes
                # `failed_error` and the reminder is GONE. The tag path's
                # equivalent costs somebody a re-tag. The shipped
                # `HonchoClient.peer_card` returns `[]` on failure, so nothing
                # reaches this today — it is here because the cost of the day
                # something does is a reminder nobody can get back.
                # Its OWN stage, not `fire_context`: an operator asking which
                # half of the context went blind must not have to guess.
                log.exception("fire peer_card read failed; continuing without it")
                self._metrics.inc(
                    "nora_errors_total", stage="fire_peer_card", platform=row.platform,
                )

        return TagContext(
            tag_id=uuid.uuid4().hex,
            message=_synthetic_message(rows, ref),
            tagger=ref,
            transcript=transcript,
            peer_card=card,
            started_at=time.perf_counter(),
        )

    async def run(self, rows: Sequence[Reminder]) -> str:
        """One turn, and the §8.1 outcome the heartbeat writes onto the rows.

        **It does not call `invoke.run` or `deliver.check_outcome`** (§6.2).
        Not for metric isolation — that was tried twice and undercounted twice,
        which is why `audience` exists — but because it needs its own timeout
        (`[heartbeat] timeout_seconds`) and its own outcome mapping: both of
        those functions answer `bool`, and a `bool` cannot distinguish
        `failed_timeout` from `failed_error`, which is precisely the
        distinction the row has to record. It reimplements the one side effect
        `check_outcome` has that is not accounting — the `llm_state.last_success_at`
        touch — because a successful fire turn IS a successful invocation, and
        omitting it would let a healthy process report a stale LLM to `/health`.

        Only `sent` is DONE, and `sent` means the `Deliverer` actually
        delivered. Anything else is a reminder that did not arrive.
        """
        ctx = await self.build_context(rows)
        # Every log line for the rest of this turn — across every stage it calls
        # into — carries the tag id without naming it (nora.obs.logging's
        # contextvar bridge), so a fire turn is greppable exactly like a tag.
        bind_tag_id(ctx.tag_id)
        try:
            return await self._turn(rows, ctx)
        finally:
            clear_tag_id()

    async def _turn(self, rows: Sequence[Reminder], ctx: TagContext) -> str:
        """One turn's stages, its last-resort guard, and DEC-42's observable.

        The `except` is the fire path's counterpart to
        `EngagementPipeline._run_admitted`'s outer guard — §6.2 enumerated nine
        sites that must carry `audience`, all of them on the tag path, and this
        is the tenth.
        Without it every stage between `resolve_identity` and
        `build_tag_message` could fault straight past `nora_silent_failures_total`
        into the heartbeat's `failed_error` — the same string an ordinary model
        error writes, so a total wiring fault and a bad model call were one
        reading. `handler_error` is R-5's last-resort signal and this is where
        the fire path raises it.

        `Exception`, not the tag path's `BaseException`, and that is the one
        deliberate divergence. The tag path is a spawned turn nobody awaits, so
        swallowing everything is what makes its silence guarantee unconditional.
        This one is awaited by `HeartbeatLoop`, whose `run()` loop is cancelled
        at shutdown — absorbing a `CancelledError` here would answer
        `failed_error`, write a terminal row, and swallow the cancellation of
        the loop itself.

        The `finally` is DEC-42/R-25 on this path: a turn that sent SOME of its
        messages and then stopped is NOT silent — `delivered` is True, the
        outcome is `sent`, the row is DONE — so nothing R-23 built can see it.
        It runs in a `finally` for the tag path's reason: that is the one point
        every exit path converges on, the cap-cut loop, the timeout, the
        `llm_error` and the guard above alike.
        """
        row = rows[0]
        # The three the `finally` reads. LOCALS, never attributes: `FirePath` is
        # built once per process and outlives every turn, so state parked on
        # `self` would let one turn's stop reason describe the next one's
        # delivery — the shape `Handler._guards` had.
        handle = None
        stop_reason: str | None = None
        completed = False
        try:
            tagger = ctx.tagger
            if tagger is None:
                # §12: only a mapped person may hold a reminder, re-checked here
                # for the same reason policy is (§5.1) — identity.toml is read
                # once at startup and this row may be thirty days old.
                # `failed_policy`, not `failed_error`: nothing went wrong, the
                # grant went away.
                log.error(
                    "reminder %s not fired: %s is no longer in identity.toml",
                    row.id, row.requester_person_id,
                )
                return FAILED_POLICY

            identity = await self._adapter.resolve_identity()
            self_ids = frozenset(getattr(identity, "aliases", ()))
            # Without this every @-mention in the transcript — including mentions
            # of Nora herself — reaches the model as U+FFFC. Same placement as the
            # tag path: on the assembled context, never at ingest (the audit
            # stores what arrived) and never in the prefill (which may not import
            # nora.platform).
            ctx = MentionRenderer(
                persons=self._persons, display_name=self._display_name,
                self_ids=self_ids, metrics=self._metrics,
            ).rehydrate(ctx)

            doctrine = self._doctrine_store.get(applies_to=AUDIENCE)
            log.info(
                "fire turn start (tag_id=%s conversation_id=%s person_id=%s "
                "reminders=%d doctrine_sha256=%s)",
                ctx.tag_id, row.conversation_id, tagger.person_id, len(rows),
                doctrine.sha256,
            )

            handle = deliver.build(
                store=self._records, adapter=self._adapter,
                target=ctx.message.target(),
                doctrine=doctrine, canary=self._canary, tag_id=ctx.tag_id,
                nora_sender_id=self._nora_sender_id,
                conversation_id=row.conversation_id,
                budgets=self._registry.budgets(),
                terminal=self._registry.terminal_tools(), metrics=self._metrics,
                audience=AUDIENCE,
            )
            room = Room(
                platform=row.platform, conversation_id=row.conversation_id,
                roster_source=self._roster_source, persons=self._persons,
                tagger=tagger, self_ids=self_ids,
            )
            tools = self._registry.build(
                store=self._records, conversation_id=row.conversation_id,
                conversation_type=row.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer, room=room,
                question=ctx.message.body, context_window=self._context_window,
                metrics=self._metrics,
                # The one thing that tells a tool this turn was not a tag. Task
                # 6's `remind` refuses on it: a reminder that can set a reminder
                # is an unbounded loop, and the conversation it would fire into
                # is this one.
                fire_turn=True,
            )
            now = self._clock().astimezone()
            pre_fill = build_prefill(
                ctx, display_name=self._display_name,
                context_window=self._context_window,
                now=now, header=_fire_header(rows, tagger, now),
            )
            # `tagger.name`, NOT `Persons.display_name(ctx.message)` — that reads
            # `sender_id`, which is blank here, and would attribute the request to
            # nobody. Same trap as `build_context`'s `sender_name`.
            tag_message = build_tag_message(
                ctx, tagger_name=tagger.name, display_name=self._display_name,
            )

            failure, stop_reason = await self._invoke(
                doctrine=doctrine, tools=tools, pre_fill=pre_fill,
                guard=handle.guard, tag_message=tag_message, ctx=ctx,
                platform=row.platform, deliverer=handle.deliverer,
            )
            # `truncation_reason`'s "the turn reached the end of its try block",
            # decided HERE rather than at the end of it. The tag path can leave
            # it to the last line because a failed invocation returns early
            # there; a fire turn that timed out AFTER delivering still answers
            # `sent` (§8.1: the outcome is `sent` iff the group heard something),
            # so it runs on to the end of this block, and a `completed = True`
            # down there would tell `truncation_reason` the invocation finished
            # when it was abandoned mid-reply — the one case this counter exists
            # for.
            completed = failure is None
            if not handle.deliverer.delivered:
                return failure if failure is not None else FAILED_ERROR

            # A delivered reply implies a successful invocation — the touch
            # `deliver.check_outcome` performs, reimplemented rather than
            # inherited (§6.2). Without it a process whose only recent speech was
            # a reminder reports a stale LLM on `GET /health`.
            if self._llm_state is not None:
                self._llm_state.last_success_at = time.time()
            await self._feed(
                handle.deliverer.sent_texts, row.conversation_id, row.platform,
            )
            return SENT
        except Exception:
            log.exception("fire turn failed (tag=%s); staying silent", ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="handler_error",
                platform=row.platform, audience=AUDIENCE,
            )
            # Whatever the invocation reported, this turn did NOT reach its end —
            # which is what makes `pipeline_error` reachable for a fault raised
            # after some messages had already gone out.
            completed = False
            return FAILED_ERROR
        finally:
            messages = handle.deliverer.delivered_count if handle is not None else 0
            if messages:
                reason = deliver.truncation_reason(
                    stop_reason=stop_reason, completed=completed,
                )
                if reason is not None:
                    log.warning(
                        "partial_reply: %d reminder message(s) sent, then %s "
                        "(tag=%s)", messages, reason, ctx.tag_id,
                    )
                    self._metrics.inc(
                        "nora_partial_replies_total", reason=reason,
                        platform=row.platform,
                    )

    async def _invoke(
        self,
        *,
        doctrine: Any,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: Any,
        tag_message: str,
        ctx: TagContext,
        platform: str,
        deliverer: Any,
    ) -> tuple[str | None, str | None]:
        """The model call, with the heartbeat's own timeout and its own
        accounting. Returns `(failure, stop_reason)`: the §8.1 failure name — or
        None if the loop finished — and how the loop ENDED.

        A `None` failure does NOT mean she spoke — `_turn` asks the `Deliverer`
        that. It means the invocation completed, which is a different fact, and
        conflating them is what `deliver.check_outcome`'s `stop_reason`
        parameter exists to prevent (R-23).

        `stop_reason` is handed back rather than only counted here for the
        reason `invoke.run` hands it back: `truncation_reason` needs it, and
        `None` (the invocation never returned) means something different from
        `"limit_turns"` — a distinction `_turn`'s `finally` cannot recover
        afterwards.
        """
        started = time.perf_counter()
        stop_reason: str | None = None
        failure: str | None = None
        try:
            stop_reason = await asyncio.wait_for(
                self._runner.invoke(
                    doctrine=doctrine, tools=tools, pre_fill=pre_fill, guard=guard,
                    tag_message=tag_message, limits=self._limits, ctx=ctx,
                    trace_attributes=trace_attributes(ctx, doctrine),
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            log.error("fire turn invocation_timeout (tag=%s)", ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="invocation_timeout",
                platform=platform, audience=AUDIENCE,
            )
            failure = FAILED_TIMEOUT
        except Exception:
            log.exception("fire turn llm_error (tag=%s); staying silent", ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="llm_error",
                platform=platform, audience=AUDIENCE,
            )
            failure = FAILED_ERROR
        finally:
            # `audience` on both, and it is not symmetry for its own sake: this
            # turn was bounded by `[heartbeat] timeout_seconds`, not the tag
            # tier's, so an unlabelled sample blends two populations with two
            # different ceilings and describes neither (§13). Same for the stop
            # reason — the `limit_.*` alarm
            # (`2026-08-08-turn-budget-finding.md:224`) has to say which path
            # ran out of budget, because the caps differ.
            self._metrics.observe(
                "nora_llm_latency_seconds", time.perf_counter() - started,
                audience=AUDIENCE,
            )
        if stop_reason is not None:
            self._metrics.inc(
                "nora_llm_stop_reason_total", reason=stop_reason, audience=AUDIENCE,
            )
        if failure is None and not deliverer.delivered and not deliverer.attempted:
            # The loop finished and she never called `send_reply`, or one of
            # R-8's caps cut it off. Labelled the way `check_outcome` labels it
            # (R-23) — `no_send_call` and `limit_total_tokens` are different
            # failures and used to be the same metric. Every OTHER way of not
            # delivering implies `attempted` and has already counted itself
            # inside `Deliverer`.
            reason = (
                stop_reason if stop_reason in deliver.BUDGET_STOP_REASONS else "no_send_call"
            )
            log.warning("fire turn %s (tag=%s); nothing was sent", reason, ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason=reason,
                platform=platform, audience=AUDIENCE,
            )
        return failure, stop_reason

    async def _feed(
        self, sent_texts: Sequence[str], conversation_id: str, platform: str,
    ) -> None:
        """Nora's own messages, under the Nora aiPeer, into this conversation's
        session — the tag path's `feedback` stage, awaited rather than spawned.

        Awaited because nobody is waiting on a fire turn, so there is no reply
        latency to protect; guarded because a Honcho outage must not turn a
        reminder that reached the group into a `failed_error` row that says it
        did not.
        """
        if not sent_texts:
            return
        try:
            await feedback.run(
                memory=self._memory, ai_peer=self._ai_peer, sent_texts=sent_texts,
                conversation_id=conversation_id, metrics=self._metrics,
            )
        except Exception:
            log.exception("fire turn feedback failed; the reminder was still delivered")
            # `platform`, because the series DECLARES `(stage, platform)` and an
            # omitted label records `platform=""` (`Metrics._full_labels`) — a
            # sample invisible to `nora_errors_total{platform="signal"}`, which
            # is the filter an operator reaches for first. `fire_context` above
            # already gets this right; this site did not.
            self._metrics.inc(
                "nora_errors_total", stage="fire_feedback", platform=platform,
            )


def _synthetic_message(
    rows: Sequence[Reminder], ref: PersonRef | None,
) -> NormalizedMessage:
    """§6.4's second table. Built in memory only — nothing writes it.

    `body` is the requester's OWN sentence(s), and that is the premise of the
    whole feature: a reminder is a deferred request, put to her at the due time
    as a request. `build_tag_message` escapes this into `<tag_message>`, which
    is how she receives it at all. The set-at and due-at facts deliberately do
    NOT go here — they belong in the header (`_fire_header`), because a body
    that is anything other than what the person said breaks that premise.

    `sender_id` and `timestamp` are blank/0: falsy, which is what makes
    `indicate.begin` return None and §5.5's "no reactions" hold without a flag
    anywhere. `timestamp` is `0` and not `None` because the field is `int`.
    """
    return NormalizedMessage(
        platform=rows[0].platform,
        conversation_id=rows[0].conversation_id,
        conversation_type=rows[0].conversation_type,
        sender_id="",
        sender_name=ref.name if ref is not None else "",
        timestamp=0,
        body="\n".join(row.text for row in rows),
        raw={},
    )


def _stamp(moment: datetime) -> str:
    """A due/set time in the reader's own timezone. Local, not UTC: the rest of
    the prompt states time locally (`_now_line`), and two clocks in one prompt
    is how she answers with the wrong hour."""
    return moment.astimezone().strftime("%-d %b %H:%M")


def _fire_header(
    rows: Sequence[Reminder], tagger: PersonRef, now: datetime,
) -> str:
    """The situation paragraph for a fire turn — `build_prefill`'s `header`.

    Every clause of the tag header is false here: nobody called her, and that is
    not why this turn is happening. `_context_header` is parameterised rather
    than branched so the block catalogue, the notation and — the part that
    actually matters — R-13/R-13b escaping stay shared (§6.3).

    The name is escaped because everything else this module puts in a prompt is,
    and `identity.toml` is operator-authored text like any other.
    """
    who = escape(tagger.name)
    when = "".join(
        f"- asked {_stamp(row.created_at)}, due {_stamp(row.due_at)}\n" for row in rows
    )
    request = "request" if len(rows) == 1 else "requests"
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        f"just called you. Earlier, {who} asked you to bring something back up "
        "at a particular time, and that time has come — that is why this turn "
        "is happening, and it is the only reason.\n"
        f"When the {request} in tag_message below were made, and when each came "
        f"due (in the same order):\n"
        f"{when}"
        f"Say it to {who} now, in your own words, as the person who was asked to "
        "remember. Do not read the times back at them unless they asked in those "
        "terms."
    )
