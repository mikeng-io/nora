"""The public Instagram summons occasion.

Somebody in `identity.toml` tags her (`@noraexample`) in a comment under an
Instagram post; she reads the post (what each of its items SHOWS is read for
her before the turn, `_read_items`) and the thread around the summons, and
answers in the comments, once, or says nothing.

**How a summons arrives.** Instagram tells her about
the tag as a DM card from the commenter, and the bridge emits a `summons`
event for it on its SSE stream (`nora.platform.instagram.summons` parses it
into a `SummonsCard`; `nora.app.summons_consumer` hands it here). The card
names the post but not the comment, so the occasion reads the post's comments
and answers under the one the card's SENDER wrote that tags her
(`summons_comment`: author igid AND word-bounded `@handle`, newest first). The
ledger row is claimed on the card's message id before anything is read; the
comment id is bound to it once found, and stays unique across rows. It reuses the model loop, the
tool surface and the guards of every other occasion, and it constructs NO
ROOM and writes NO MEMORY: no Honcho, no audit row, no roster, no notes, no
transcript. What it holds is the bridge, the ledger, the person index (for the
summoner gate and nothing else), the doctrine, the tool registry under
`PUBLIC_GRANT`, and a runner.

**A third sending path, and why it is not a third `adapter.send`.** DEC-212
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

**One follow-up when she wrote an answer and did not send it** (a turn can
end `end_turn` with a complete answer as final text and no `send_reply`, which
is recorded `silent`). A turn that ends on its own with nothing
held and non-empty prose is continued ONCE, in the same conversation
(`LiveAgentRunner.invoke`'s `follow_up`: same agent, tools, guard and
budgets, what is left of the loop's caps, the same timeout and trace), by
`UNSENT_REPLY_FOLLOW_UP`. Only a `send_reply` she then makes is held; the
prose itself is never posted, and a follow-up that ends without one is
`silent` as before; one the invocation's timeout or an error cut short is
`silent` with `bridge_code` `follow_up_timed_out` / `follow_up_failed`.
Every follow-up is counted in `nora_summons_follow_ups_total` and logged at
INFO with the ledger uuid; the row records only a follow-up that died.

**Telemetry carries the ledger uuid and nothing else that identifies**: never
the comment id, an igid, a username, a shortcode or any text, in a span
attribute or a log line.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from nora.agent.egress import LeakGuard, scan_reply
from nora.agent.ports import ToolSpec
from nora.agent.punctuation import undash
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import media_look
from nora.doctrine.loader import DoctrineStore
from nora.domain.audience import SUMMONS
from nora.domain.models import ConversationTarget, NormalizedMessage, TagContext
from nora.domain.summons import (
    ALREADY_CLAIMED,
    MAX_ITEMS_SHOWN,
    PUBLIC_REPLY_MAX_CHARS,
    ItemReading,
    SummonsCard,
    SummonsContext,
    SummonsLedger,
    SummonsOutcome,
    SummonsRow,
    build_context,
    render_summons,
    summons_comment,
    with_readings,
)
from nora.engagement.stages import deliver, invoke
from nora.infra.social.readings import read_view
from nora.infra.social.summons_client import BridgeResult, InstagramSummonsClient
from nora.infra.social.views import ItemKind, ItemView, PostView, Reading
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, current_trace_id, span
from nora.persons import Persons

log = logging.getLogger("nora.app.summons")

#: The tools a public summons may call (`PUBLIC_GRANT`, minus
#: `generate_image`: a comment carries no
#: image). Read-only lookups on the open web and X, `media_fetch` (the post's
#: items are already read before the turn, `_read_items`, and the render does
#: not point her at it; it stays granted for anything else, and HER call is
#: an ordinary `caller="model"` read that caches as every model read does),
#: the clock and arithmetic, and `send_reply` into the staging deliverer.
#: NOTHING that reads a room, a roster, memory, notes, persons or places,
#: and no Instagram tool: `instagram_read` reads stories that are
#: roster-scoped, over the same session this occasion writes with.
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

#: The one time budget for reading the post's items before the turn, all of
#: them together (and each on its own). Not shorter: a reel read uncached takes
#: well over a minute (`readings.ITEM_TIMEOUT_SECONDS`), and one look's ceiling
#: is the single-window vision ceiling (120 s) plus the download (25 s) plus
#: ffmpeg (5 s). A summons answers a comment, so two and a half minutes of
#: reading costs nothing a person waits on, and a reel that times out is
#: exactly the failure this read exists to end. Past it, an item is said to be
#: unreadable, never guessed.
MEDIA_READ_SECONDS = 150.0

#: One look's own ceiling inside that budget: the same number, so in
#: production the two bind together and there is ONE budget. Kept a separate
#: knob so a test can tell a look that ran out of its own time from a read
#: that ran out of the whole budget.
MEDIA_ITEM_SECONDS = 150.0

#: `web_search` calls one summons turn may make. `public/conduct.md`: when
#: a post makes several claims, each claim she judges gets its OWN check, not
#: one settled as a side effect of another. A post can name many companies, so
#: the registry's per-turn budget (a room tag's, sized for one question) would
#: leave some unchecked. Raised for THIS occasion only: a room tag keeps the
#: registry's value, and `x_search` is unchanged.
SUMMONS_WEB_SEARCHES = 8

#: The ONE follow-up a summons turn may get (see the module docstring): the
#: next user turn of the same conversation when she ended it on her own with
#: prose and nothing held. It asks, it does not tell: silence stays a valid
#: end, and what she wrote is never posted unless she sends it herself.
UNSENT_REPLY_FOLLOW_UP = (
    "You wrote a reply but did not send it. If you mean to post it, call "
    "send_reply with it. To stay silent, end without sending."
)

#: `invoke.run`'s blank-trace outcomes for an invocation that died, as the
#: follow-up outcome word they become (`nora_summons_follow_ups_total`).
_FOLLOW_UP_FAILURES: Mapping[str, str] = {"timeout": "timed_out", "llm_error": "failed"}

#: The bridge's write refusals, mapped to the ledger state they leave
#: (`errors.go`'s refusal codes). Anything not named
#: here and every no-answer (`timeout`, `unreachable`) is `ambiguous`: the
#: comment may or may not exist, and an ambiguous write is never retried.
_WRITE_STATES: Mapping[str, str] = {
    "writes_disabled": "refused",
    "invalid_text": "refused",
    # The bridge's guard 3: it re-reads the post's comments itself and finds
    # no served summons whose sender wrote `reply_to_comment_id` tagging her.
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
#: mention (the `@` follows an ASCII word character); after a CJK character,
#: or written full-width, it still is.
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.])[@\uff20][A-Za-z0-9._]+")


def without_address(text: str, summoner: str | None) -> str:
    """`text` without an opening `@<summoner>`: the reply is threaded under
    the summoner's comment, so that is its address already, and she may write
    it as one, which the mention check would refuse
    whole. Only the summoner's own handle, only at the start: any other
    `@handle`, anywhere, still reaches the mention check (a stripped
    `@victim you are a fraud` would post the insult at the summoner)."""
    if not summoner:
        return text
    address = rf"^\s*[@\uff20]{re.escape(summoner)}(?:[\s,:\uff0c\uff1a]+|$)"
    return re.sub(address, "", text, flags=re.IGNORECASE)


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


#: A post item's kind as `readings` types it. The client already narrows it
#: to these, with `media` for anything else.
_ITEM_KINDS: Mapping[str, ItemKind] = {"image": "image", "video": "video"}

#: `readings.Reading` states that mean the look did not come back with what
#: the item shows: she is told it could not be read.
_UNREADABLE: frozenset[str] = frozenset({"failed", "timed_out", "not_started"})


def _item_reading(reading: Reading | None) -> ItemReading:
    """The `instagram_read` reading of one item → the summons' own. Every
    look that did not come back seen or withheld is `unreadable`; a video
    not opened by design keeps its reason. `None` (never looked at) is
    `unreadable` too: an item she is shown is one she was meant to see."""
    if reading is None or reading.state in _UNREADABLE:
        return ItemReading("unreadable")
    if reading.state == "seen":
        return ItemReading("seen", reading.text, reading.coverage)
    if reading.state in ("withheld", "too_long", "unknown_length", "over_video_cap"):
        return ItemReading(reading.state)
    return ItemReading("unreadable")


class SummonsOccasion:
    """One summons, from a card to a ledger row. `run` and `flush_staged`
    NEVER raise into the consumer for a reason a bridge or a model can cause;
    an exception there is a bug and the consumer counts it."""

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
        media_read_seconds: float = MEDIA_READ_SECONDS,
        media_item_seconds: float = MEDIA_ITEM_SECONDS,
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
        self._media_read_seconds = media_read_seconds
        self._media_item_seconds = media_item_seconds

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

    async def run(self, card: SummonsCard) -> SummonsOutcome:
        outcome = await self._run(card)
        self._count(outcome)
        return outcome

    async def _run(self, card: SummonsCard) -> SummonsOutcome:
        now = self._clock()
        # Resolved BEFORE the claim, so a stranger's igid is never stored: an
        # ignored row carries `author_igid = ''` (its key is the message id).
        person_id = (
            await self._summoners.person_for_igid(igid=card.sender_igid)
            if card.sender_igid else None
        )
        # Claimed on the CARD's message id, before anything else: of any
        # number of deliveries of one card (an SSE reconnect, two processes
        # during a recreate), exactly one is worked.
        summons_id = await self._ledger.claim(
            message_id=card.message_id, media_id=card.media_id,
            shortcode=card.shortcode,
            author_igid=card.sender_igid if person_id is not None else "",
            mention_at=card.received_at, mode=self._mode, now=now,
        )
        if summons_id is None:
            return SummonsOutcome(ALREADY_CLAIMED)
        # The cheap gates: no bridge call, no model call.
        if not card.resolved:
            # The bridge could not name the post the card points at: there is
            # nothing she could answer under. Recorded, never answered.
            await self._ledger.finish(summons_id, "unresolved", now=now, person_id=person_id)
            return SummonsOutcome("unresolved")
        if person_id is None:
            # Counted and ignored. Nothing is read and
            # nothing is said: a stranger tagging her costs one row.
            await self._ledger.finish(summons_id, "ignored_summoner", now=now)
            return SummonsOutcome("ignored_summoner")
        if now - card.received_at > self._max_age:
            # A card replayed long after it arrived (a bridge buffer, a
            # backlog behind a checkpoint) is filed, never answered late.
            await self._ledger.finish(summons_id, "stale", now=now, person_id=person_id)
            return SummonsOutcome("stale")
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
            outcome = await self._answer(summons_id, card, person_id, now)
            annotate(**{"nora.outcome": outcome.state})
            return outcome

    async def _answer(
        self, summons_id: UUID, card: SummonsCard, person_id: str, now: datetime,
    ) -> SummonsOutcome:
        trace_id = current_trace_id()
        post = await self._client.post(card.shortcode)
        comments = await self._client.comments(card.shortcode) if post.ok else None
        if post.value is None or comments is None or comments.value is None:
            # Either read failing is silence: she never
            # answers a post, or under a thread, she has not seen.
            code = post.code if not post.ok else (comments.code if comments else None)
            await self._ledger.finish(
                summons_id, "unreadable", now=now, person_id=person_id,
                bridge_code=code, trace_id=trace_id,
            )
            return SummonsOutcome("unreadable", bridge_code=code, reached_bridge=True)
        # The card carries no comment id. The comment is the one the card's
        # SENDER wrote that tags her: author igid AND @handle; the one whose
        # text equals the card's first, else the newest.
        # A comment by anybody else is never the one she answers under.
        found = summons_comment(
            comments.value, igid=card.sender_igid, handle=self._handle, text=card.text,
        )
        if found is None:
            await self._ledger.finish(
                summons_id, "unreadable", now=now, person_id=person_id,
                bridge_code="not_on_page", trace_id=trace_id,
            )
            return SummonsOutcome(
                "unreadable", bridge_code="not_on_page", reached_bridge=True,
            )
        if not await self._ledger.bind_comment(summons_id, found.id, now=now):
            # Another row already answers this comment (a second card for one
            # comment): one comment, one reply.
            await self._ledger.finish(
                summons_id, "duplicate", now=now, person_id=person_id,
                bridge_code="comment_claimed", trace_id=trace_id,
            )
            return SummonsOutcome(
                "duplicate", bridge_code="comment_claimed", reached_bridge=True,
            )
        context = build_context(
            summons_id=str(summons_id), now=now, comment_id=found.id,
            summoner=card.sender_username or None, summoned_at=card.received_at,
            handle=self._handle, post=post.value, comments=comments.value,
        )
        if context is None:  # pragma: no cover — `found` is on the page by construction
            await self._ledger.finish(
                summons_id, "unreadable", now=now, person_id=person_id,
                bridge_code="not_on_page", trace_id=trace_id,
            )
            return SummonsOutcome(
                "unreadable", bridge_code="not_on_page", reached_bridge=True,
            )

        held, follow_up_end = await self._turn(summons_id, context=context, now=now)
        # `timed_out` / `failed`: the follow-up began and the invocation died
        # before it ended, which is not her choosing silence.
        broke = follow_up_end if follow_up_end in ("timed_out", "failed") else None
        if follow_up_end is not None:
            # Fixed words and the ledger uuid only (the telemetry rule above).
            after = "replied" if held else (broke or "silent")
            log.info("summons %s: follow_up %s", summons_id, after)
            annotate(**{"nora.follow_up": after})
            self._metrics.inc(
                "nora_summons_follow_ups_total", outcome=after, mode=self._mode,
            )
        if not held:
            # The row's state stays `silent` (its CHECK constraint names no
            # other end for a turn that held nothing); a follow-up that died
            # says so in `bridge_code`, `follow_up_timed_out` / `_failed`.
            code = f"follow_up_{broke}" if broke else None
            await self._ledger.finish(
                summons_id, "silent", now=now, person_id=person_id, trace_id=trace_id,
                bridge_code=code,
            )
            return SummonsOutcome("silent", bridge_code=code, reached_bridge=True)
        return await self._flush(
            summons_id, shortcode=card.shortcode, comment_id=found.id,
            text=held[0], person_id=person_id, trace_id=trace_id, now=now,
            summoner=card.sender_username or None,
        )

    async def _turn(
        self, summons_id: UUID, *, context: SummonsContext, now: datetime,
    ) -> tuple[tuple[str, ...], str | None]:
        """Read the post's items, then one model turn against a staging
        deliverer → `(what it HELD, how its follow-up ended)`: None for no
        follow-up, else `ended`, `timed_out` or `failed`. Nothing it
        does reaches a room, and its `final_text` is discarded: it only
        decides whether the turn gets its one `UNSENT_REPLY_FOLLOW_UP`."""
        question = context.question
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
            budgets={
                **self._registry.budgets(), "send_reply": 1,
                # Each claim its own check (`SUMMONS_WEB_SEARCHES`).
                "web_search": SUMMONS_WEB_SEARCHES,
            },
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
        turn_tools: dict[str, Any] = {
            "store": _NoTranscript(), "conversation_id": conversation_id,
            "conversation_type": CONVERSATION_TYPE, "persons": self._persons,
            "honcho": None, "deliverer": handle.deliverer, "metrics": self._metrics,
            "room": room, "question": question, "transcript": (),
            # Not a tag: `send_reply` asks no reaction picker for a GIF.
            "fire_turn": True, "links": handle.links,
        }
        surface = self._registry.surface(**turn_tools, grant=PUBLIC_GRANT)
        # What the post SHOWS is read before she is, by a `media_fetch` the
        # registry builds from the SAME arguments as the tools she is handed:
        # a second build, so a second closure, with the same no-room
        # collaborators and the same configuration as hers. Her own calls
        # are not this one's (hers are `caller="model"`).
        context = await self._read_items(
            summons_id, context, self._registry.specs(**turn_tools),
        )
        context_text = render_summons(context)
        message = NormalizedMessage(
            platform="instagram", conversation_id=conversation_id,
            conversation_type=CONVERSATION_TYPE, sender_id="", sender_name=None,
            timestamp=int(context.now.timestamp() * 1000), body=question, raw={},
        )
        ctx = TagContext(
            tag_id=str(summons_id), message=message, tagger=None,
            transcript=(), peer_card=(), started_at=time.perf_counter(),
        )
        followed_up = False

        def follow_up(final_text: str) -> str | None:
            # ONCE, and only for prose with nothing held. A turn that CALLED
            # `send_reply` is never continued, whatever the guards made of
            # the call: its budget (1) is spent, and the text the guard ends
            # such a turn with is its own, not her prose.
            nonlocal followed_up
            if (
                followed_up or handle.deliverer.held_texts
                or handle.guard.counts.get("send_reply", 0) > 0
                or not final_text.strip()
            ):
                return None
            followed_up = True
            return UNSENT_REPLY_FOLLOW_UP

        _ok, _elapsed, _stop, _text, trace = await invoke.run(
            runner=self._runner, doctrine=doctrine, tools=surface.tools,
            pre_fill=[], guard=handle.guard, tool_call_log=handle.tool_call_log,
            reasoning_watch=handle.reasoning_watch, tool_hooks=surface.hooks,
            tag_message=context_text, limits=surface.limits(self._limits),
            ctx=ctx, timeout_seconds=self._timeout_seconds,
            metrics=self._metrics, audience=SUMMONS, follow_up=follow_up,
        )
        # How the follow-up ended, when there was one: the invocation's own
        # failure outcome (`invoke.run`'s blank trace) if it died after the
        # follow-up began, so a timeout there is never read as her silence.
        follow_up_end = (
            None if not followed_up
            else _FOLLOW_UP_FAILURES.get(trace.outcome, "ended")
        )
        return handle.deliverer.held_texts, follow_up_end

    # ---- reading the post: what each item SHOWS, before the turn -----------

    async def _read_items(
        self, summons_id: UUID, context: SummonsContext, specs: Iterable[ToolSpec],
    ) -> SummonsContext:
        """`context` with a reading on every item it shows (at most
        `MAX_ITEMS_SHOWN`; the rest are already counted in `elided_items`).

        The `instagram_read` mechanism, not a second one:
        `readings.read_view` over a `PostView` of the items, each
        look `media_fetch(url=...)` itself through `tools.media_look`. So the
        url egress screen, the download ceilings, the image vision call, the
        chunked video read, the identifier screen on what comes back, and the
        per-video length and count caps are the ones every other reading
        has. The caption is not in the view: nothing a stranger wrote can
        reach a vision prompt.

        **The pre-read stores nothing** (`persist=False`, the story frame's
        switch). A url read writes no blob and no `media_objects` row in any
        case (its bytes are never kept, `media_fetch`'s url path); with
        `persist` off these looks write neither content cache either. That
        is a claim about THIS read only: a `media_fetch` she makes herself in
        the turn is an ordinary `caller="model"` read and caches as every one
        does, which is why the render never points her at it.

        Never raises for a reading's sake: a look that fails, times out or
        never started is `unreadable`, and her context says it could not be
        read. `context.now` moves to when the read ended (`with_readings`),
        so every age she is shown is measured after it."""
        if not context.items:
            return context
        fetch = next((spec.call for spec in specs if spec.name == "media_fetch"), None)
        if fetch is None:  # pragma: no cover — media_fetch is always registered
            return with_readings(
                context, tuple(ItemReading("unreadable") for _ in context.items),
                now=self._clock(),
            )
        look = media_look(
            fetch, caller="summons", persist=False,
            max_video_seconds=float(self._registry.video_chunk_seconds),
        )
        # Only what a look needs: kind, url, a video's length. No author, no
        # caption: the view carries nothing a stranger wrote.
        view = PostView(
            author=None, caption=None, taken_at=None,
            items=tuple(
                ItemView(
                    kind=_ITEM_KINDS.get(item.kind, "media"),
                    url=item.url, duration_seconds=item.duration_seconds,
                )
                for item in context.items
            ),
        )
        # Counts and states only: never a url, an id or any text.
        with span("summons.read_items", **{"nora.items": len(view.items)}):
            try:
                read = await read_view(
                    view, look, max_items=MAX_ITEMS_SHOWN,
                    item_timeout=self._media_item_seconds,
                    total_timeout=self._media_read_seconds,
                )
                readings = tuple(_item_reading(item.reading) for item in read.items)
            except Exception as exc:  # noqa: BLE001 — a reading never ends a summons
                # The type only: an exception's message can carry the url.
                log.error(
                    "summons %s: reading the post's items raised %s",
                    summons_id, type(exc).__name__,
                )
                readings = tuple(ItemReading("unreadable") for _ in context.items)
            annotate(**{
                f"nora.items_{state}": count
                for state, count in Counter(r.state for r in readings).items()
            })
        return with_readings(context, readings, now=self._clock())

    # ---- the flush: the ONE place a comment is written ----------------------

    def _disclosure(self, body: str) -> str | None:
        """The kind of disclosure the public body carries, or None: the doctrine
        or the canary (`LeakGuard`, R-6), or somebody's identifier or a
        coordinate (`scan_reply`, R-18). Built per call, from the doctrine the
        summons reads, so a flush sees what its turn saw."""
        doctrine = self._doctrine_store.get(applies_to=SUMMONS)
        return LeakGuard(doctrine, self._canary).check(body) or scan_reply(body)

    async def _flush(
        self, summons_id: UUID, *, shortcode: str, comment_id: str, text: str,
        person_id: str | None, trace_id: str | None, now: datetime,
        prior_code: str | None = None, summoner: str | None = None,
    ) -> SummonsOutcome:
        """Publish `text` (the FIRST held reply, and only it) once, or stage
        it. Dry run stores it and posts nothing. Live stores it `staged`, then
        TAKES the row (`staged` -> `flushing`, atomically) before the POST, so
        of two flushes of one row exactly one writes, and a process that dies
        mid-write leaves `flushing`, which the sweep makes `ambiguous` and
        nothing ever posts again."""
        # The model is never SHOWN an em dash (nora.agent.punctuation), yet
        # some replies still carry one. Room replies let that
        # residue through; a public comment does not. Punctuation only: the
        # same undash the request path uses, no word changes.
        body = without_address(undash(text), summoner).strip()
        # Every check runs on this FINAL body, after each transform above. The
        # guards before the hold measured the text as she wrote it, and a
        # transform can make a clean text a disclosure: the em dash between two
        # decimals becomes the comma of a coordinate pair. A disclosure is never
        # kept in the ledger, even refused.
        disclosure = self._disclosure(body)
        refusal = (
            "empty" if not body
            else "too_long" if len(body) > PUBLIC_REPLY_MAX_CHARS
            else "mention" if _MENTION_RE.search(body)
            else disclosure
        )
        if refusal is not None:
            # A prior 503 may have been claimed and posted (`_RETRY_503`), so a
            # refusal now does not prove it never posted: the outcome stays
            # `ambiguous`, as `_write_state` keeps it after a 503 and a retry.
            state = "ambiguous" if prior_code in _RETRY_503 else "refused"
            code = prior_code if state == "ambiguous" else refusal
            if state == "ambiguous" and disclosure is not None:
                # The kind only, never the text: an operator sees why it was refused.
                log.error(
                    "summons %s: a %s disclosure refused a row whose prior write got a "
                    "503; it stays ambiguous", summons_id, disclosure,
                )
            await self._ledger.finish(
                summons_id, state, now=now, person_id=person_id,
                reply_text=None if disclosure else body, clear_reply=disclosure is not None,
                bridge_code=code, trace_id=trace_id,
            )
            return SummonsOutcome(state, bridge_code=code, reached_bridge=True)
        await self._ledger.finish(
            summons_id, "staged", now=now, person_id=person_id, reply_text=body,
            trace_id=trace_id,
        )
        if self._mode != "live":
            return SummonsOutcome("staged", reached_bridge=True)
        if not await self._ledger.take_for_flush(summons_id, now=now):
            # Another flush holds it, or it is already final: nothing posts.
            return SummonsOutcome(ALREADY_CLAIMED, reached_bridge=True)
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
            state, bridge_code=None if result.ok else result.code, reached_bridge=True,
        )

    async def flush_staged(self, row: SummonsRow) -> SummonsOutcome:
        """Retry the flush of a LIVE row left `staged` (the bridge said
        `too_soon` or answered a 503 before claiming). No model call: the text
        was already guarded and stored. Past `max_age` it is given up as
        `failed` rather than posted late."""
        now = self._clock()
        expired = (
            row.mention_at is None or now - row.mention_at > self._max_age
            or not row.reply_text or not row.comment_id
        )
        if expired or self._mode != "live" or row.comment_id is None:
            # Past its age, or the operator switched back to dry_run while a
            # live reply waited: given up, never posted late or against the
            # mode now in force. (A staged row always has its comment; one
            # without is refused rather than posted under nothing.)
            code = "expired_staged" if expired else "mode_changed"
            await self._ledger.finish(row.id, "failed", now=now, bridge_code=code)
            outcome = SummonsOutcome("failed", bridge_code=code)
        else:
            outcome = await self._flush(
                row.id, shortcode=row.shortcode, comment_id=row.comment_id,
                text=row.reply_text or "", person_id=None, trace_id=None, now=now,
                prior_code=row.bridge_code,
            )
        self._count(outcome)
        return outcome
