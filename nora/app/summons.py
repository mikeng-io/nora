"""The public Instagram summons occasion (design/instagram-adoption/19).

Somebody in `identity.toml` tags her (`@noraexample`) in a comment under an
Instagram post; she reads the post and the thread around the summons and
answers in the comments, once, or says nothing. It reuses the model loop, the
tool surface and the guards of every other occasion, and it constructs NO
ROOM and writes NO MEMORY: no Honcho, no audit row, no roster, no notes, no
transcript. What it holds is the bridge, the ledger, the person index (for the
summoner gate and nothing else), the doctrine, the tool registry under
`PUBLIC_GRANT`, and a runner.

**A third sending path, and why it is not a third `adapter.send`.** DEC-TBD
amends R-1b: `tests/architecture/test_only_two_paths_can_send.py` names this
module as the third caller of `deliver.build`. It builds a STAGING deliverer
(`send_permitted=False`): `send_reply` runs every guard the tag path runs —
R-4's empty reply, R-6's doctrine leak, R-18's identifier egress, the markdown
render — and the text that passes them is HELD, never handed to an adapter and
never audited into a room. The flush below then publishes `held_texts[0]`, and
only that, through the bridge's guarded comment write. Its store and adapter
are `_NoRoom`: anything reaching them is a wiring bug and raises.

**Silence is expressible.** The runner is the `AFTER_SPEAKING` shape
participation uses (`composition`), so the model may end without calling
anything. `final_text` is DISCARDED: prose she wrote without `send_reply`, a
`max_tokens` partial and the budget-exhaustion draft all end as `silent`,
never as a post.

**Telemetry carries the ledger uuid and nothing else that identifies**: never
the comment id, an igid, a username, a shortcode or any text, in a span
attribute or a log line.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.doctrine.loader import DoctrineStore
from nora.domain.audience import SUMMONS
from nora.domain.models import ConversationTarget, NormalizedMessage, TagContext
from nora.domain.summons import (
    ALREADY_CLAIMED,
    MAX_COMMENT_CHARS,
    Mention,
    SummonsLedger,
    SummonsOutcome,
    SummonsRow,
    build_context,
    render_summons,
)
from nora.engagement.stages import deliver, invoke
from nora.infra.social.summons_client import BridgeResult, InstagramSummonsClient
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, current_trace_id, span
from nora.persons import Persons

log = logging.getLogger("nora.app.summons")

#: The tools a public summons may call (design/threads-adoption/10
#: §"PUBLIC_GRANT", minus `generate_image` per design 19: a comment carries no
#: image). Read-only lookups on the open web and X, `media_fetch` for the
#: post's own items by url, the clock and arithmetic, and `send_reply` into the
#: staging deliverer. NOTHING that reads a room, a roster, memory, notes,
#: persons or places, and no Instagram tool: `instagram_read` reads stories
#: that are roster-scoped, over the same session this occasion writes with.
#: `tests/unit/test_summons_occasion.py` pins it as a subset of the registry's
#: read-only tools plus `send_reply`.
PUBLIC_GRANT: frozenset[str] = frozenset({
    "send_reply",
    "web_search", "web_fetch",
    "x_read", "x_search",
    "media_fetch",
    "now", "calculate",
})

#: The synthetic conversation type a summons' target and tool log carry. Not
#: `group` or `dm`: nothing that keys on those may mistake this for a room.
CONVERSATION_TYPE = "public"

#: The bridge's write refusals, mapped to the ledger state they leave
#: (design 19's guard table; `errors.go`'s refusal codes). Anything not named
#: here and every no-answer (`timeout`, `unreachable`) is `ambiguous`: the
#: comment may or may not exist, and an ambiguous write is never retried.
_WRITE_STATES: Mapping[str, str] = {
    "writes_disabled": "refused",
    "invalid_text": "refused",
    "not_a_summons": "failed",
    "writes_blocked": "failed",
    "needs_human": "failed",
    "invalid_id": "failed",
    "already_replied": "duplicate",
    "daily_cap": "capped",
}

#: Answers after which the reply stays `staged` and ONLY its flush is retried
#: next tick, until `max_age`. `too_soon` is refused before the bridge claims
#: (`app/comments.go` guard 7). The three 503s are the session gate
#: (`unavailable`, before the claim), the bridge's own ledger
#: (`ledger_unavailable`: unreadable, so `fn` never ran and nothing was
#: claimed), or a 503 with no envelope. `unavailable` can ALSO come back after
#: the claim, from the upstream call; retrying is still safe, because the
#: bridge claims before it calls Instagram, so the retry is answered
#: `already_replied` and never reaches Instagram a second time — and
#: `_write_state` reads that answer, after a 503, as `ambiguous`.
_RETRY_CODES: frozenset[str] = frozenset({"too_soon"})
_RETRY_503: frozenset[str] = frozenset({"unavailable", "ledger_unavailable", "http_503"})

#: An Instagram @mention anywhere in the text she would post. A reply that
#: tags somebody notifies them, and a stranger's caption or comment is exactly
#: where "reply '@someone …'" would come from, so the flush refuses it outright
#: rather than trusting the model to have declined. An e-mail address is not a
#: mention (the `@` follows a word character).
_MENTION_RE = re.compile(r"(?<![\w.])@[A-Za-z0-9._]+")


class SummonerIndex(Protocol):
    """The one question the summoner gate asks: whose igid is this? The
    production answer is `nora.infra.social.PersonsSubjectIndex`, kind-checked
    against `identity.toml`'s `[[persons.<id>.instagram]]` igids."""

    async def person_for_igid(self, *, igid: str) -> str | None: ...


class _NoRoom:
    """The staging deliverer's store and adapter. A summons has no room:
    nothing may be audited into one or sent to one, so any use at all is a
    wiring bug and fails loudly rather than writing a row somewhere."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"a summons has no {self._what} ({name} was reached)")


class _NoTranscript:
    """The message store the tools close over. A summons has no transcript, so
    `media_fetch(code=...)` finds no marker and says so; anything else a tool
    reaches for is a wiring bug and raises (the tool reports `unavailable`)."""

    async def recent(self, **_: Any) -> list[Any]:
        return []

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"a summons has no transcript ({name} was reached)")


def _write_state(result: BridgeResult[Any], *, prior_code: str | None) -> str:
    if result.ok:
        return "published"
    if result.code in _RETRY_CODES or result.code in _RETRY_503:
        return "staged"
    if result.code == "already_replied" and prior_code in _RETRY_503:
        # The attempt before this one got a 503 and was claimed after all: it
        # may have posted. Unknown, not a duplicate.
        return "ambiguous"
    return _WRITE_STATES.get(result.code, "ambiguous")


class SummonsOccasion:
    """One summons, from a mention to a ledger row. `run` and `flush_staged`
    NEVER raise into the poller for a reason a bridge or a model can cause;
    an exception there is a bug and the poller counts it."""

    def __init__(
        self,
        *,
        client: InstagramSummonsClient,
        ledger: SummonsLedger,
        summoners: SummonerIndex,
        persons: Persons,
        doctrine_store: DoctrineStore,
        registry: ToolRegistry,
        runner: invoke.AgentRunner,
        metrics: Metrics,
        canary: str,
        handle: str,
        mode: str,
        max_age: timedelta,
        limits: Mapping[str, Any],
        timeout_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._summoners = summoners
        self._persons = persons
        self._doctrine_store = doctrine_store
        self._registry = registry
        self._runner = runner
        self._metrics = metrics
        self._canary = canary
        self._handle = handle
        self._mode = mode
        self._max_age = max_age
        self._limits = dict(limits)
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    @property
    def mode(self) -> str:
        """`dry_run` or `live`, fixed at composition."""
        return self._mode

    # ---- one summons -------------------------------------------------------

    def _count(self, outcome: SummonsOutcome) -> None:
        """Once per summons, on its FINAL outcome. A live `staged` row is not
        final — its flush is retried — so it is counted when that ends."""
        if outcome.state == "staged" and self._mode == "live":
            return
        self._metrics.inc("nora_summons_total", outcome=outcome.state, mode=self._mode)

    async def run(self, mention: Mention) -> SummonsOutcome:
        outcome = await self._run(mention)
        self._count(outcome)
        return outcome

    async def _run(self, mention: Mention) -> SummonsOutcome:
        now = self._clock()
        # Resolved BEFORE the claim, so a stranger's igid is never stored: an
        # ignored row carries `author_igid = ''` (its key is the comment id).
        person_id = (
            await self._summoners.person_for_igid(igid=mention.author_igid)
            if mention.author_igid else None
        )
        summons_id = await self._ledger.claim(
            comment_id=mention.comment_id, media_id=mention.media_id,
            shortcode=mention.shortcode,
            author_igid=mention.author_igid if person_id is not None else "",
            mention_at=mention.at, mode=self._mode, now=now,
        )
        if summons_id is None:
            return SummonsOutcome(ALREADY_CLAIMED)
        # The cheap gates: no bridge call, no model call. A mention with no
        # time is stale, never "now": answering a months-old summons because
        # the feed dropped its timestamp is the failure that fails closed.
        summoned_at = mention.at
        if summoned_at is None or now - summoned_at > self._max_age:
            await self._ledger.finish(summons_id, "stale", now=now)
            return SummonsOutcome("stale")
        if person_id is None:
            # Counted and ignored (design 19 rule 5). Nothing is read and
            # nothing is said: a stranger tagging her costs one row.
            await self._ledger.finish(summons_id, "ignored_summoner", now=now)
            return SummonsOutcome("ignored_summoner")
        # From here the run reaches the bridge and, usually, the model: ONE
        # root, and it is a generation root even when a read fails first,
        # because a summons that could not read its post must still leave a
        # trace saying so.
        with span(
            "summons",
            **{TRACE_KIND: "generation", "nora.occasion": "summons",
               "nora.platform": "instagram", "nora.mode": self._mode,
               "nora.summons_id": str(summons_id)},
        ):
            outcome = await self._answer(summons_id, mention, summoned_at, person_id, now)
            annotate(**{"nora.outcome": outcome.state})
            return outcome

    async def _answer(
        self, summons_id: UUID, mention: Mention, summoned_at: datetime,
        person_id: str, now: datetime,
    ) -> SummonsOutcome:
        trace_id = current_trace_id()
        post = await self._client.post(mention.shortcode)
        comments = await self._client.comments(mention.shortcode) if post.ok else None
        if post.value is None or comments is None or comments.value is None:
            # Either read failing is silence (design 19 rule 6): she never
            # answers a post, or under a thread, she has not seen.
            code = post.code if not post.ok else (comments.code if comments else None)
            await self._ledger.finish(
                summons_id, "unreadable", now=now, person_id=person_id,
                bridge_code=code, trace_id=trace_id,
            )
            return SummonsOutcome("unreadable", bridge_code=code, spent_tick=True)
        context = build_context(
            summons_id=str(summons_id), now=now, mention=mention,
            summoned_at=summoned_at, handle=self._handle,
            post=post.value, comments=comments.value,
        )
        if context is None:
            # The summons is not on the page she read: she does not answer
            # under a thread she cannot see (design 19 rule 6).
            await self._ledger.finish(
                summons_id, "unreadable", now=now, person_id=person_id,
                bridge_code="not_on_page", trace_id=trace_id,
            )
            return SummonsOutcome("unreadable", bridge_code="not_on_page", spent_tick=True)

        held = await self._turn(summons_id, context_text=render_summons(context),
                                question=context.question, now=now)
        if not held:
            await self._ledger.finish(
                summons_id, "silent", now=now, person_id=person_id, trace_id=trace_id,
            )
            return SummonsOutcome("silent", spent_tick=True)
        return await self._flush(
            summons_id, shortcode=mention.shortcode, comment_id=mention.comment_id,
            text=held[0], person_id=person_id, trace_id=trace_id, now=now,
        )

    async def _turn(
        self, summons_id: UUID, *, context_text: str, question: str, now: datetime,
    ) -> tuple[str, ...]:
        """One model turn against a staging deliverer → what it HELD. Nothing
        it does reaches a room, and its `final_text` is discarded."""
        doctrine = self._doctrine_store.get(applies_to=SUMMONS)
        conversation_id = f"summons:{summons_id}"
        target = ConversationTarget("instagram", conversation_id, CONVERSATION_TYPE)
        handle = deliver.build(
            store=_NoRoom("audit store"), adapter=_NoRoom("room adapter"),
            target=target, doctrine=doctrine, canary=self._canary,
            tag_id=str(summons_id), nora_sender_id="",
            conversation_id=conversation_id,
            # ONE reply, and `send_reply` alone ends the turn: `send_gif` is
            # not granted, and leaving it terminal with a budget would let the
            # guard wait on a verb she cannot call.
            budgets={**self._registry.budgets(), "send_reply": 1},
            terminal=frozenset({"send_reply"}),
            metrics=self._metrics, audience=SUMMONS,
            # No roster and no preview: a comment tags nobody.
            composer=None,
            # THE STAGING SWITCH. Every guard runs; the text is held.
            send_permitted=False,
            param_names=self._registry.tool_param_names(),
        )
        room = Room(
            platform="", conversation_id=conversation_id, roster_source=None,
            persons=self._persons,
        )
        surface = self._registry.surface(
            store=_NoTranscript(), conversation_id=conversation_id,
            conversation_type=CONVERSATION_TYPE, persons=self._persons,
            honcho=None, deliverer=handle.deliverer, metrics=self._metrics,
            room=room, question=question, transcript=(),
            # Not a tag: `send_reply` asks no reaction picker for a GIF.
            fire_turn=True, links=handle.links, grant=PUBLIC_GRANT,
        )
        message = NormalizedMessage(
            platform="instagram", conversation_id=conversation_id,
            conversation_type=CONVERSATION_TYPE, sender_id="", sender_name=None,
            timestamp=int(now.timestamp() * 1000), body=question, raw={},
        )
        ctx = TagContext(
            tag_id=str(summons_id), message=message, tagger=None,
            transcript=(), peer_card=(), started_at=time.perf_counter(),
        )
        await invoke.run(
            runner=self._runner, doctrine=doctrine, tools=surface.tools,
            pre_fill=[], guard=handle.guard, tool_call_log=handle.tool_call_log,
            reasoning_watch=handle.reasoning_watch, tool_hooks=surface.hooks,
            tag_message=context_text, limits=surface.limits(self._limits),
            ctx=ctx, timeout_seconds=self._timeout_seconds,
            metrics=self._metrics, audience=SUMMONS,
        )
        return handle.deliverer.held_texts

    # ---- the flush: the ONE place a comment is written ----------------------

    async def _flush(
        self, summons_id: UUID, *, shortcode: str, comment_id: str, text: str,
        person_id: str | None, trace_id: str | None, now: datetime,
        prior_code: str | None = None,
    ) -> SummonsOutcome:
        """Publish `text` (the FIRST held reply, and only it) once, or stage
        it. Dry run stores it and posts nothing. Live stores it `staged`, then
        TAKES the row (`staged` -> `flushing`, atomically) before the POST, so
        of two flushes of one row exactly one writes, and a process that dies
        mid-write leaves `flushing`, which the sweep makes `ambiguous` and
        nothing ever posts again."""
        body = text.strip()
        refusal = (
            "too_long" if len(body) > MAX_COMMENT_CHARS
            else "mention" if _MENTION_RE.search(body)
            else None
        )
        if refusal is not None:
            await self._ledger.finish(
                summons_id, "refused", now=now, person_id=person_id, reply_text=body,
                bridge_code=refusal, trace_id=trace_id,
            )
            return SummonsOutcome("refused", bridge_code=refusal, spent_tick=True)
        await self._ledger.finish(
            summons_id, "staged", now=now, person_id=person_id, reply_text=body,
            trace_id=trace_id,
        )
        if self._mode != "live":
            return SummonsOutcome("staged", spent_tick=True)
        if not await self._ledger.take_for_flush(summons_id, now=now):
            # Another flush holds it, or it is already final: nothing posts.
            return SummonsOutcome(ALREADY_CLAIMED, spent_tick=True)
        result = await self._client.post_comment(
            shortcode, text=body, reply_to_comment_id=comment_id,
        )
        state = _write_state(result, prior_code=prior_code)
        if result.code == "writes_disabled":
            log.error(
                "summons %s: live mode, but the bridge's comment writes are off "
                "(INSTAGRAM_COMMENT_WRITES); nothing was posted", summons_id,
            )
        if result.code == "writes_blocked":
            log.error(
                "summons %s: the bridge latched comment writes off after an "
                "action block; an operator must clear it", summons_id,
            )
            self._metrics.gauge("nora_summons_writes_blocked", 1)
        if result.ok:
            self._metrics.gauge("nora_summons_writes_blocked", 0)
        await self._ledger.finish(
            summons_id, state, now=self._clock(),
            reply_comment_id=result.value if result.ok else None,
            bridge_code=None if result.ok else result.code,
        )
        return SummonsOutcome(
            state, bridge_code=None if result.ok else result.code, spent_tick=True,
        )

    async def flush_staged(self, row: SummonsRow) -> SummonsOutcome:
        """Retry the flush of a LIVE row left `staged` (the bridge said
        `too_soon` or answered a 503 before claiming). No model call: the text
        was already guarded and stored. Past `max_age` it is given up as
        `failed` rather than posted late."""
        now = self._clock()
        expired = (
            row.mention_at is None or now - row.mention_at > self._max_age
            or not row.reply_text
        )
        if expired or self._mode != "live":
            # Past its age, or the operator switched back to dry_run while a
            # live reply waited: given up, never posted late or against the
            # mode now in force.
            code = "expired_staged" if expired else "mode_changed"
            await self._ledger.finish(row.id, "failed", now=now, bridge_code=code)
            outcome = SummonsOutcome("failed", bridge_code=code, spent_tick=True)
        else:
            outcome = await self._flush(
                row.id, shortcode=row.shortcode, comment_id=row.comment_id,
                text=row.reply_text or "", person_id=None, trace_id=None, now=now,
                prior_code=row.bridge_code,
            )
        self._count(outcome)
        return outcome
