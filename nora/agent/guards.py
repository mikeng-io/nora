"""Guardrail enforcement (spec B). ReplyGuard is constructed fresh per tag, so counters
are per-invocation by construction rather than by bookkeeping.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Collection, Mapping
from datetime import datetime
from typing import Any, Protocol

from pydantic import TypeAdapter
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from ..doctrine.loader import Doctrine
from ..domain.actions import ActionOwner
from ..domain.media import MediaBlob
from ..domain.models import (
    ConversationTarget,
    DeliveryStatus,
    NormalizedMessage,
)
from ..domain.ports import OutboundAttachment, OutboundQuote, SendResult, Styled
from ..obs.metrics import Metrics
from . import tool_choice
from .call_ledger import CallLedger
from .egress import LeakGuard, scan_reply
from .links import LinkCarry
from .markdown import render_markdown
from .outbound import Outbound, OutboundComposer
from .tools import NATIVE_TOOLS, note_handle, utc_now

log = logging.getLogger("nora.guards")

#: `send_reply`'s `more`, validated as Strands validates the tool's own input.
_MORE: TypeAdapter[bool | None] = TypeAdapter(bool | None)


def _flag(tool_input: Mapping[str, Any], key: str) -> bool:
    """A `send_reply` flag read the way the tool reads it (pydantic's lax
    bool); unreadable is False."""
    try:
        return bool(_MORE.validate_python(tool_input.get(key)))
    except ValueError:
        return False


class DeliveredObserver(Protocol):
    """Told once a reply has actually reached the group (DEC-83).

    A one-method port so `Deliverer` (in `nora.agent`) never imports the
    reaction stage in `nora.engagement`, which the layered import contract
    forbids in that direction. `nora.engagement.stages.indicate.DeliverySettler`
    is the implementation; `_NoDeliveredObserver` below is the default.

    `settle()` returns None and must never raise: it is decoration on the far
    side of a message the group already has, and R-5's rule is that decoration
    failing is never allowed to become the turn's problem.
    """

    def settle(self) -> None: ...


class _NoDeliveredObserver:
    """The default. Delivery still happens; nothing is told about it.

    Constructed PER `Deliverer` rather than shared as a module-level instance:
    `tests/architecture/test_no_module_level_mutable_singletons.py` refuses one,
    even for a stateless object, so nobody has to re-audit statelessness on later
    edits. A `Deliverer` lives and dies with one tag, so this costs one empty
    object per tag."""

    def settle(self) -> None:
        return None





class Deliverer:
    """Bounded delivery: audit-first, then send. The ONLY caller of adapter.send().

    The latch counts *attempts*, not successes (R-2): a failed send consumes a
    message, because v2 has no auto-retry and a second attempt would be an
    unbounded loop.

    `message_cap` is REQUIRED (DEC-42). It is the same number `ReplyGuard`
    enforces as `send_reply`'s R-7 budget, threaded from the same
    `ToolRegistry.budgets()` mapping through `nora.engagement.stages.deliver.build`,
    because the cap must be ONE number: R-7's budget check runs BEFORE R-2's
    latch, so two numbers that disagree resolve silently in favour of the smaller
    and report the refusal under the other one's name. There is no default, so
    "raised the budget and forgot the deliverer" cannot construct cleanly.

    `audience` is REQUIRED for the same reason. It labels every
    `nora_silent_failures_total` this class raises with which path was speaking
    (`"tag"` or `"heartbeat"`). A default would let the heartbeat's fire path
    report its failures as conversation failures, spending the A8 budget
    (`docs/runbook.md`) that is filtered to `audience="tag"`. A missing keyword
    is a `TypeError` at construction.

    `sent_texts` is a list: every delivered message is appended, and the feedback
    stage feeds each under the Nora aiPeer into the conversation's session. A
    scalar would drop the earlier messages of a multi-message turn.
    """

    def __init__(
        self,
        *,
        store,
        adapter,
        target: ConversationTarget,
        doctrine: Doctrine,
        canary: str,
        tag_id: str,
        nora_sender_id: str,
        message_cap: int,
        audience: str,
        metrics: Metrics,
        on_delivered: DeliveredObserver | None = None,
        composer: OutboundComposer | None = None,
        links: LinkCarry | None = None,
        send_permitted: bool = True,
        intent: str | None = None,
        content_checks: bool = True,
    ) -> None:
        # The brief's intent (`tell`/`ask`) of a fire turn, written into the
        # raw_json of every message this Deliverer audits (key `intent`). None is
        # the tag path: a reply has no brief.
        self._content_checks = content_checks
        self._intent = intent
        self._store = store
        self._adapter = adapter
        self._target = target
        self._tag_id = tag_id
        self._sent_message_ids: list[int] = []
        self._nora_sender_id = nora_sender_id
        # Which path is speaking: `"tag"` or `"heartbeat"`. Carried only onto
        # `nora_silent_failures_total`; see the class docstring.
        self._audience = audience
        self._metrics = metrics
        self._leak = LeakGuard(doctrine, canary)
        self._doctrine_sha256 = doctrine.sha256
        self._message_cap = message_cap
        # Told when a reply actually reaches the group (DEC-83). Defaults to a
        # no-op so `Deliverer` never branches on None at the call site.
        self._on_delivered = on_delivered or _NoDeliveredObserver()
        # What decoration rides with the text (`nora.agent.outbound`). Unlike
        # `message_cap` and `audience`, this defaults to None: a missing value
        # for those would be wrong while looking right (capping her at one
        # message, or charging a heartbeat's failures to the tag budget), whereas
        # a missing composer produces a plain message, with no wrong claim
        # available. `deliver.build` takes it with no default, so the production
        # callers still have to decide.
        self._composer = composer
        # DEC-89: the links `place_info` minted this turn, shared with the tools
        # (`nora.agent.links.LinkCarry`). Defaults to a fresh empty carrier, which
        # appends nothing. Whether to SHARE one with the tools is `deliver.build`'s
        # decision; a `Deliverer` holding its own private carrier is the inert
        # case.
        self._links = links if links is not None else LinkCarry()
        # The participation occasion's SEND cooldown, enforced at the last inch:
        # `False` means "evaluate, but if she concludes 'speak', hold the
        # message". Defaults True (the tag path and the heartbeat have no
        # cooldown); only `FirePath.speak(occasion, send_permitted=...)` passes
        # False. The suppression point is in `deliver`, after every guard has
        # measured the text and before the audit write: `called` still reads True
        # (the cooldown, not a malfunction, held it), so the turn never lands in
        # `no_send_call`.
        self._send_permitted = send_permitted
        # The held-text seam: a message the cooldown held is retained here, so
        # the shadow mirror can answer "what she WOULD have said" for a held
        # send. Appended only after every guard has measured the text, exposed
        # read-only, and never fed anywhere: the room never heard it.
        self._held_texts: list[str] = []
        self._attempts = 0
        # Every `deliver()` ENTRY, whether or not it survived the guards. Distinct
        # from `_attempts`, which counts the ones that reached the platform: R-2's
        # latch counts attempts (an empty reply does not consume a message,
        # `test_an_empty_reply_does_not_consume_the_latch`), while `check_outcome`
        # asks whether the model called `send_reply` AT ALL. A turn whose every
        # reply was guard-refused must not read as `no_send_call`; she did try to
        # speak, and a guard stopped her.
        self._send_calls = 0
        self._delivered_count = 0
        # Every message actually SENT this turn (Honcho v2): the feedback stage
        # feeds each under the Nora aiPeer. Populated only on a successful send,
        # so a refused/failed message is never fed.
        self._sent_texts: list[str] = []
        # WHY the last `deliver` answered as it did. `deliver` returns a bool,
        # which cannot tell a guard refusal from a send that reached the wire and
        # failed there; both are False. A field rather than a richer return type,
        # because `deliver`'s bool is what every tool call site reads.
        self._last_outcome = ""
    @property
    def last_outcome(self) -> str:
        """How the last `deliver` ended: `"sent"`, or the reason it did not.

        The refusal names are the ones the metrics use (`empty_reply`,
        `doctrine_leak`, `egress_block`, ...). `"send_failed"` is not a refusal:
        the text passed every guard and the platform rejected it. `""` before the
        first call.
        """
        return self._last_outcome

    @property
    def sent_texts(self) -> tuple[str, ...]:
        """Every message actually sent this turn, in order (Honcho v2). A tuple
        so a caller cannot mutate the Deliverer's record; the feedback stage
        feeds each under the Nora aiPeer."""
        return tuple(self._sent_texts)

    @property
    def held_texts(self) -> tuple[str, ...]:
        """Every message the participation cooldown HELD this turn (the
        shadow-mode seam), in order. Read-only like `sent_texts`; empty on every
        path that has no cooldown."""
        return tuple(self._held_texts)

    @property
    def held_count(self) -> int:
        """How many sends the cooldown or the shadow gate HELD this turn.

        The four model-facing send tools read this across one `deliver` call to
        tell a HOLD from a REFUSAL: `deliver` answers `False` to both, and a model
        that cannot tell them apart rewrites and calls again, costing a model
        call per retry and leaving near-identical drafts on `held_texts`.
        """
        return len(self._held_texts)

    @property
    def delivered(self) -> bool:
        return self._delivered_count > 0

    @property
    def sent_message_ids(self) -> tuple[int, ...]:
        return tuple(self._sent_message_ids)

    @property
    def delivered_count(self) -> int:
        """How many messages actually reached the group this tag.

        `delivered` is derived from this counter, so the two cannot disagree.
        `nora_messages_per_tag` reads it.
        """
        return self._delivered_count

    @property
    def attempts(self) -> int:
        """Messages this tag has SPENT: sent, refused, or failed in flight."""
        return self._attempts

    @property
    def attempted(self) -> bool:
        return self._attempts > 0

    @property
    def called(self) -> bool:
        """Did the model call `send_reply` at all, however it went?

        `attempted` asks whether a message reached the platform; this asks
        whether she TRIED. A guard-refused turn is False for the first and True
        for the second; conflating them would put `no_send_call` on a blocked
        turn."""
        return self._send_calls > 0

    @property
    def exhausted(self) -> bool:
        """No message left: R-2's latch, generalised to `message_cap`.

        `ReplyGuard` asks this rather than `attempted`: with the cap at 1 the two
        are the same predicate, and above 1 `attempted` would refuse the second
        message.
        """
        return self._attempts >= self._message_cap

    async def _compose(self, styled: Styled) -> Outbound:
        """`styled` as an `Outbound`: decorated if a composer is wired, bare if not.

        The `except` is not redundant with `compose`'s own never-raise contract.
        A mention is decoration on a message a human is waiting for, and she must
        not go silent because a name lookup failed. Degrading to
        `Outbound(styled)` sends the message the composer-less path sends.

        The degraded answer keeps the text and its style ranges, which live in
        the `Styled` this was handed. A failing composer costs the @-mention and
        the preview card, not the bold.
        """
        if self._composer is None:
            return Outbound(styled)
        try:
            return await self._composer.compose(styled)
        except Exception:                  # decoration is never fatal
            log.exception("compose raised (tag=%s); sending plain text", self._tag_id)
            return Outbound(styled)

    async def deliver(
        self, text: str, *, source: str = "tool",
        attachments: tuple[OutboundAttachment, ...] = (),
        record_text: str | None = None,
        record_metadata: Mapping[str, Any] | None = None,
        media_blobs: tuple[MediaBlob, ...] = (),
        quote: OutboundQuote | None = None,
    ) -> bool:
        self._send_calls += 1
        if self.exhausted:                                     # R-2
            log.warning(
                "delivery refused: %d message(s) already spent of %d (tag=%s)",
                self._attempts, self._message_cap, self._tag_id,
            )
            self._metrics.inc("nora_guard_vetoes_total", rule="R-2")
            self._last_outcome = "cap_spent"
            return False

        # DEC-82: Markdown is converted ONCE, below, before the remaining guards,
        # so each one measures the bytes that actually LEAVE rather than a draft
        # that no longer exists, and the audit row, `sent_texts` and the Honcho
        # feed all carry the one canonical form. `styled` is the only model of
        # this reply from there on (comprehend-once): `render_markdown` both
        # deletes the asterisks and produces the ranges that index what is left,
        # so a second call would be a second interpretation of one text. The value
        # is passed whole to `_compose` (see `nora.agent.markdown`).
        drafted = text

        if (not text or not text.strip()) and not attachments:  # R-4
            log.warning("delivery refused: empty reply (tag=%s)", self._tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="empty_reply",
                platform=self._target.platform, audience=self._audience,
            )
            self._last_outcome = "empty_reply"
            return False

        # DEC-89: put back the links this reply EARNED and dropped. The position
        # is pinned by three constraints:
        #   - AFTER R-4, so a link can never rescue an empty reply into a sent
        #     one (R-4 reads the DRAFT: the authored reply must be non-empty).
        #   - BEFORE the render, so `render_markdown` is called ONCE
        #     (`test_the_composer_is_handed_the_rendered_value`); a second call is
        #     a second model of one message.
        #   - BEFORE R-6/R-18, so those guards measure the bytes that actually
        #     LEAVE. A text transformation in front of a fail-closed guard that
        #     does not see its output is a hole.
        if record_text is not None:
            # R-100: the recorded copy of a GIF caption never keeps a link the
            # room did not see.
            record_text = self._links.scrub(record_text)
        stripped_before, documents_before = self._links.stripped, self._links.documents_added
        # A message the turn ends on may carry the not-saved note after a catalog
        # read alone; a `send_reply` may be a progress message, so it does not
        # (`LinkCarry.apply`).
        text, carried = self._links.apply(text, final=source != "tool")
        documents = self._links.documents_added - documents_before
        if carried - documents > 0:
            self._metrics.inc("nora_reply_links_carried_total", outcome="appended")
        if documents:
            self._metrics.inc("nora_reply_links_carried_total", outcome="document_added")
        if self._links.stripped > stripped_before:
            # R-100: a link to one of our documents that no tool handed her this
            # turn.
            self._metrics.inc("nora_reply_links_carried_total", outcome="stripped")
            if not text.strip() and not attachments:
                # R-4 again, on what is left: a reply that was only a made-up link
                # is now empty, and a blank message must not go out.
                log.warning("delivery refused: empty after removing a made-up link (tag=%s)",
                            self._tag_id)
                self._metrics.inc(
                    "nora_silent_failures_total", reason="empty_reply",
                    platform=self._target.platform, audience=self._audience,
                )
                self._last_outcome = "empty_reply"
                return False

        # Media code carries for this turn (a finished video clip) rides on the
        # message, after R-4 for the links' reason: it never makes an empty reply
        # sendable. Merged with the message's own attachments, so whichever
        # message is sent first carries it.
        carried_media, carried_blobs = self._links.pending_media
        if carried_media:
            attachments = (*attachments, *carried_media)
            media_blobs = (*media_blobs, *carried_blobs)

        # DEC-82: the one interpretation of this reply. `render_markdown` removes
        # the emphasis delimiters and produces the offsets into what is left, so
        # the text and its ranges are never separated.
        styled = render_markdown(text)

        # R-6 against BOTH forms, because the Markdown rewrite cuts both ways.
        # Removing the delimiters reveals a doctrine span broken up by emphasis,
        # so the draft alone would miss it. But `_MD_HEADING` also deletes the
        # `# ` that the raw doctrine haystack still contains, so a window
        # straddling a heading stops matching in the rendered form. Neither form
        # is a superset of the other: check both and refuse on either (a false
        # block costs one silent reply, a leak is not recoverable). Converting
        # emphasis to a style does not weaken this: `styled.text` is the string
        # the guard measures, and the ranges ride in a parallel field.
        reason = self._leak.check(styled.text) or self._leak.check(drafted)  # R-6
        if reason:
            log.error("doctrine_leak_blocked: %s (tag=%s)", reason, self._tag_id)
            self._metrics.inc("nora_leak_blocks_total")
            self._metrics.inc(
                "nora_silent_failures_total", reason="doctrine_leak",
                platform=self._target.platform, audience=self._audience,
            )
            self._last_outcome = "doctrine_leak"
            return False

        # R-18: see `nora.agent.egress` for what it deliberately does not match.
        # This is the one caller of `scan_reply`: `text` here is addressed to the
        # group, so the identifier floor applies at its strictest. The model-name
        # veto is disabled (`x_read` relays third-party content that names
        # models).
        #
        # BOTH forms, as with R-6, and this is the sharper case. Every identifier
        # pattern is `\b`-anchored, and removing an asterisk BETWEEN two word
        # characters joins them and destroys the boundary:
        #
        #   "*a*deadbeef-1234-5678-9abc-def012345678"  raw->uuid   converted->None
        #   "*+85298765432*a"                          raw->phone  converted->None
        #
        # so a member's real number wrapped in emphasis would otherwise be sent
        # (in bold). A text transformation must never sit in front of a
        # fail-closed guard without the guard seeing both sides.
        kind = scan_reply(styled.text, content_checks=self._content_checks) or scan_reply(
            drafted, content_checks=self._content_checks
        )
        if kind:
            log.error("egress_blocked: %s (tag=%s)", kind, self._tag_id)
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            self._metrics.inc(
                "nora_silent_failures_total", reason="egress_block",
                platform=self._target.platform, audience=self._audience,
            )
            self._last_outcome = "egress_block"
            return False

        # The participation SEND cooldown, placed AFTER every guard above has
        # measured the text (the guards' accounting of a held message stays true)
        # and BEFORE `_attempts`, the audit row and the wire: a held message
        # leaves no trace in the room's history, because the room never heard it.
        # Not a failure: no counter moves, the held text is retained on
        # `held_texts` for the shadow-mode mirror, and `called` stays True, so the
        # turn is never read as `no_send_call`.
        if not self._send_permitted:
            log.info(
                "send held by the participation cooldown (tag=%s, %d chars)",
                self._tag_id, len(styled.text),
            )
            # Retained for the shadow mirror ("what she WOULD have said") after
            # every guard above has passed on the text.
            self._held_texts.append(styled.text)
            self._last_outcome = "cooldown_held"
            return False

        self._attempts += 1
        # The last thing that may look at the text. `outbound.text` is the string
        # that goes on the wire and into the audit row, and neither `text` nor
        # `styled` is read again below (see `nora.agent.outbound`'s module
        # docstring). A transformation added after this line cannot corrupt a
        # mention offset, because it cannot reach the wire; one that is needed
        # belongs in `nora.agent.markdown.render_markdown`, where the text and
        # every range over it are produced together. Pinned on the AST by
        # `tests/architecture/test_outbound_offsets_are_computed_last.py`.
        #
        # `styled` and not `styled.text` goes to the composer: handing it the
        # string alone would strand the style ranges.
        outbound = await self._compose(styled)
        row = NormalizedMessage(
            platform=self._target.platform,
            conversation_id=self._target.conversation_id,
            conversation_type=self._target.conversation_type,
            sender_id=self._nora_sender_id,
            sender_name=None,
            timestamp=int(time.time() * 1000),
            # The audited body is the text as written, `@grant` and all, which is
            # also what goes on the wire, because a mention replaces no characters
            # (`nora.agent.outbound`). That equality keeps her own rows readable
            # in the transcript: Signal's inbound convention is a U+FFFC
            # placeholder, and `MentionRenderer` cannot rehydrate one in a row of
            # hers.
            body=outbound.text if record_text is None else record_text,
            raw={"tag_id": self._tag_id, "source": source,
                 "doctrine_sha256": self._doctrine_sha256,
                 **({} if self._intent is None else {"intent": self._intent}),
                 **({} if record_metadata is None else dict(record_metadata))},
            media_blobs=media_blobs,
        )
        try:                                                   # record-first
            row_id = await self._store.append(
                message=row, is_nora=True, delivery_status=DeliveryStatus.PENDING,
            )
        except Exception:
            log.exception("audit_write_failed (tag=%s); staying silent", self._tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="audit_write_failed",
                platform=self._target.platform, audience=self._audience,
            )
            self._last_outcome = "audit_write_failed"
            return False

        # Durable-owner binding for bridge adapters: the committed pending
        # audit row above IS the live_invocation authority — its id is bound
        # HERE, the only place the row exists yet (bind_owner before
        # `deliver.build` would bind an id that does not exist). An adapter
        # already carrying a work/otp_challenge owner is never rebound; an
        # adapter without bind_owner is not a bridge adapter at all.
        adapter = self._adapter
        bind_owner = getattr(adapter, "bind_owner", None)
        if (bind_owner is not None
                and getattr(adapter, "action_owner", None) is None):
            adapter = bind_owner(
                ActionOwner(kind="live_invocation", id=str(row_id)))
        try:
            result = await adapter.send(
                target=self._target, text=outbound.text,
                mentions=outbound.mentions, styles=outbound.styles,
                preview=outbound.preview,
                attachments=attachments,
                # Threading rides on the send but is not part of what the guards
                # measured: `outbound` is the screened text, and a quote points at
                # a row Nora already audited. It carries no model-supplied string,
                # so there is nothing for R-6 or R-18 to scan.
                quote=quote,
            )
        except Exception:
            log.exception("send raised (tag=%s)", self._tag_id)
            result = SendResult(ok=False)

        try:
            await self._store.set_delivery_outcome(
                row_id=row_id,
                status=(DeliveryStatus.SENT if result.ok
                        else DeliveryStatus.UNCERTAIN
                        if getattr(result, "uncertain", False)
                        else DeliveryStatus.FAILED),
                ts=result.timestamp,          # the platform's stamp, not ours
            )
        except Exception:
            log.exception("delivery_status update failed (tag=%s)", self._tag_id)

        if result.ok:
            self._sent_message_ids.append(row_id)
            self._delivered_count += 1
            # The never-silent fallback (`source="fallback"`) is delivered to the
            # group, so the turn counts as non-silent (`delivered`), but it is a
            # canned apology, not Nora's authored words, so it stays out of
            # `sent_texts` and never feeds her Honcho self-model card.
            if source != "fallback" and (not attachments or outbound.text.strip()):
                self._sent_texts.append(outbound.text)
            self._metrics.inc("nora_replies_total", status="sent", platform=self._target.platform)
            # DEC-83: the group can see the reply now, so mark the turn done now
            # rather than when the agent loop drains, a whole extra model round
            # trip later. Told to a collaborator, not a callback (the
            # `__init__`-only seam rule); the default is a no-op.
            #
            # `source != "fallback"` is deliberately not applied here: the
            # fallback is delivered, and leaving the 👀 up through a fallback turn
            # would misreport the case where a human is most likely watching.
            self._on_delivered.settle()
        elif getattr(result, "uncertain", False):
            # Not a proven non-send: its own status and reason, never folded
            # into `failed` — and still a possible silent failure, so the
            # counter keeps it visible under the honest reason.
            self._metrics.inc("nora_replies_total", status="uncertain",
                              platform=self._target.platform)
            self._metrics.inc(
                "nora_silent_failures_total", reason="send_uncertain",
                platform=self._target.platform, audience=self._audience,
            )
        else:
            self._metrics.inc("nora_replies_total", status="failed", platform=self._target.platform)
            self._metrics.inc(
                "nora_silent_failures_total", reason="send_failed",
                platform=self._target.platform, audience=self._audience,
            )
        if result.ok:
            # R-100: a document link that went out in this message is not added
            # to the next one.
            self._links.mark_sent(text)
            if carried_media:
                self._links.media_sent()
                self._metrics.inc("nora_reply_links_carried_total", outcome="media_attached")
        # The row still RESOLVES on an uncertain send — as `uncertain`, not
        # `failed` (a pending row would keep the live_invocation owner valid
        # and invite a blind second send; calling it failed misreports an
        # outcome nobody disproved). The durable truth is the action row's,
        # and the outcome name keeps the in-process view equally honest.
        self._last_outcome = (
            "sent" if result.ok
            else "send_uncertain" if getattr(result, "uncertain", False)
            else "send_failed")
        return result.ok


def _list_tool_names(names: Any) -> str:
    """`"now, load_transcript or send_reply"`, sorted so the R-7 unknown-tool
    message is deterministic."""
    ordered = sorted(names)
    if len(ordered) <= 1:
        return ", ".join(ordered)
    return ", ".join(ordered[:-1]) + " or " + ordered[-1]


def _unknown_argument_result(
    tool_name: str, *, unknown: frozenset[str], accepted: frozenset[str],
) -> str:
    """R-99's veto text, in Nora's own status-result shape (R-27): a JSON object
    carrying `status`/`note`, the same two keys every dict-returning tool's own
    bad-input answer carries (see `instagram_read`'s "Give exactly one of person,
    handle or link" in `nora.agent.tools`). Serialised once, so a model reading
    this veto and one reading a real tool's status-shaped answer see identical
    shapes.

    `status` is `bad_arguments` (`nora.agent.ports.DECLARED_STATUS_EXTENSIONS`),
    not `unavailable`. `unavailable` means "I could not check" and, per
    `conduct.md`, stays true of the whole turn afterwards; this veto's text also
    reaches the debrief/Standing fold unfiltered
    (`nora.engagement.debrief_render._block`'s `toolResult` branch), so a
    malformed call misreported as a broken tool could fold a false "I can't check
    that" into her self-knowledge. `bad_arguments` says the narrower, correctable
    truth: this call, as written, could not run.

    `note` names both axes the model needs to correct the call: what it sent that
    does not exist, and what the tool actually takes.
    """
    what_it_takes = (
        f"It accepts {_list_tool_names(accepted)}." if accepted
        else "It takes no arguments."
    )
    note = (
        f"{tool_name} does not declare {_list_tool_names(unknown)}. "
        f"{what_it_takes} Call it again with only the arguments it accepts."
    )
    return json.dumps({"status": "bad_arguments", "note": note})


def unknown_arguments_veto(
    tool_name: str, tool_input: dict, param_names: Mapping[str, frozenset[str]] | None,
) -> str | None:
    """R-99's whole check, shared by `ReplyGuard.evaluate` (the tag/participation
    path) and `DreamGuard.evaluate` (`nora.agent.dream_guard`, a Dreamer pass) so
    both use one implementation of the exemption and the message shape. The
    Dreamer runs native tools built by the same `build_tool_specs`, so the same
    `file=`-shaped defect is reachable from a background pass as from a tag.

    `param_names` absent (`None`, the rule off) or missing this tool's own entry
    (a plugin tool like `skills`, or an MCP tool folded in only through
    `allowed_mcp`; neither is built by `build_tool_specs`, so
    `ToolRegistry.tool_param_names()` has no signature for them) both mean
    "cannot verify", never "reject everything": a guard with no schema data must
    not invent a veto it cannot ground (R-27's fail-closed direction applied to
    the check itself).

    `conversation_id` is exempted: `ReplyGuard`'s R-3 is the deliberate DEC-24
    decision that an invented one is counted and never vetoed, and the Dreamer
    holds no per-conversation scope to invent one about. Excluding it keeps both
    callers' behaviour identical to R-3 on the tag path.

    Pure and SDK-free, like both callers' `evaluate`: no budget is touched and no
    metric is incremented. That stays with each guard's own `_veto`, so each
    guard's rule label and counter is its own
    (`nora_guard_vetoes_total{rule="R-99"}` on the tag path,
    `nora_dream_guard_vetoes_total{adaptor, rule="R-99"}` on a Dreamer pass).
    """
    if param_names is None:
        return None
    accepted = param_names.get(tool_name)
    if accepted is None:
        return None
    unknown = frozenset(tool_input or {}) - accepted - {"conversation_id"}
    if not unknown:
        return None
    return _unknown_argument_result(tool_name, unknown=unknown, accepted=accepted)


def _unwrap_tool_status(result: Any) -> str | None:
    """The tool's OWN status (`ok`/`refused`/`unavailable`/...), unwrapped from
    Strands' success/error envelope.

    `AfterToolCallEvent.result` is a `strands.types.tools.ToolResult`
    (`{"status": "success"/"error", "content": [...], "toolUseId": ...}`), which
    is Strands' verdict on whether the call raised, not the tool's semantic
    answer. Every native tool returns a plain dict with no `content` key, so
    `DecoratedFunctionTool._wrap_tool_result` takes its else branch: `content`
    becomes `[{"text": json.dumps(the_tools_own_dict)}]`. This is the one place
    that parses that text back out, shared by `ToolCallLog` and `ReplyGuard` so
    the unwrap logic exists once.

    Returns `None` for a result this cannot parse (a tool that carries no
    `status` key: `now` and `send_reply` return a bare value, `load_transcript` a
    status-free envelope). Callers treat `None` as "not `ok`", never as "assume
    ok"."""
    try:
        content = result["content"]
        payload = json.loads(content[0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None
    return payload.get("status") if isinstance(payload, dict) else None


class ToolCallLog(HookProvider):
    """Write path for the tool-recall log (R-48). Constructed fresh per tag, like
    `ReplyGuard`.

    A different responsibility from `ReplyGuard` (veto/budget logic): this is
    persistence, so it is its own `HookProvider`. `store=None` is the inert
    default (offline/eval compositions): every write is a no-op, never a raised
    error and never a fallback that pretends to remember (R-5's shape, applied
    to a write path).
    """

    def __init__(
        self, *, conversation_id: str, conversation_type: str, tag_id: str,
        cacheable: frozenset[str], store: Any, metrics: Metrics,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._conversation_id = conversation_id
        self._conversation_type = conversation_type
        self._tag_id = tag_id
        self._cacheable = cacheable
        self._store = store
        self._metrics = metrics
        self._clock = clock

    async def _after_tool_async(self, event: Any) -> None:
        try:
            if getattr(event, "cancel_message", None) is not None:
                return
            name = (getattr(event, "tool_use", None) or {}).get("name", "")
            if name not in self._cacheable:
                return
            if _unwrap_tool_status(event.result) != "ok":
                return
            if self._store is None:
                return
            tool_input = (getattr(event, "tool_use", None) or {}).get("input") or {}
            # `content[0]["text"]` is `json.dumps(the tool's own dict)` (see
            # `_unwrap_tool_status`), so this second `json.loads` is the tool's
            # real return value. Guarded like `_unwrap_tool_status`; a second parse
            # of the same string should not fail, but is not assumed to succeed.
            try:
                tool_result = json.loads(event.result["content"][0]["text"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                return
            from uuid import uuid4

            from nora.domain.tool_call_log import ToolCallLogEntry

            entry = ToolCallLogEntry(
                id=uuid4(), conversation_id=self._conversation_id,
                conversation_type=self._conversation_type, tag_id=self._tag_id,
                tool_name=name, tool_input=tool_input, tool_result=tool_result,
                created_at=self._clock(),
            )
            await self._store.append(entry)
            self._metrics.inc("nora_tool_call_log_writes_total", tool=name, outcome="ok")
        except Exception:
            # Decoration on a turn that has already completed correctly without
            # it: a log write must never cost the reply that already went out.
            log.exception("ToolCallLog write failed; ignoring")
            self._metrics.inc(
                "nora_tool_call_log_writes_total",
                tool=name if "name" in locals() else "unknown",
                outcome="failed",
            )

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool_async)


class ReplyGuard(HookProvider):
    """Model-facing veto logic (R-2/R-3/R-4/R-7). Constructed fresh per tag.

    `evaluate` is pure and SDK-free so every rule is testable without strands; the
    hook adapter in _before_tool is a thin translation layer (RK-2).

    `budgets` is the per-tool call-count ceiling R-7 enforces, injected (DEC-10)
    via `nora.agent.registry.ToolRegistry.budgets()` and
    `nora.engagement.stages.deliver.build`, so the number a tool costs lives with
    the tool. R-7's other half, unknown-tool default-deny, holds: a tool absent
    from `budgets` is vetoed.

    `terminal` (DEC-32) is which verbs end a turn. It comes from
    `ToolRegistry.terminal_tools()` along the same path and has NO DEFAULT on
    purpose: a guard constructed without it would end no turn at all, since
    `tool_choice="required"` means the loop cannot stop by itself (the runaway
    DEC-21 addressed), and a default of `()` would hide that behind a
    working-looking constructor.
    """

    def __init__(
        self, *, conversation_id: str, budgets: Mapping[str, int],
        terminal: Collection[str], deliverer=None, metrics: Metrics,
        forced_first_tool: str | None = None,
        lookups: Collection[str] | None = None,
        param_names: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._budgets = dict(budgets)
        self._terminal = frozenset(terminal)
        self._deliverer = deliverer
        self._metrics = metrics
        # R-97's set, or None. `None` is the rule OFF and the default, because
        # the rule is false of the tag path: there she was called, and "I looked
        # and there is nothing" is an answer she owes the person who asked. A
        # collection turns it on and names the lookups it watches
        # (`ToolRegistry.lookup_tools()`, threaded from `deliver.build`, so the set
        # is derived from the tool table). Off is distinct from on-and-empty,
        # which is a wiring fault: a rule armed with nothing can never fire
        # (DEC-42's inert feature). `tests/unit/test_participation_false_speak.py`
        # pins the live path's set as non-empty.
        self._lookups = frozenset(lookups) if lookups is not None else None
        # R-97's two counters: lookups that RAN this turn, and how many answered
        # `ok`. Two numbers, because the rule turns on the difference between
        # "she never looked" (speak from what is already in front of her) and "she
        # looked and everything came back empty" (nothing to say).
        self._lookups_made = 0
        self._lookups_found = 0
        # Completed calls and admitted-but-unfinished ones, in one object
        # (`nora.agent.call_ledger`): DEC-33's reservation, held here so
        # `DreamGuard` enforces the identical ceiling with the identical code.
        self._ledger = CallLedger(self._budgets)
        # Set by `_after_tool` when a terminal verb has actually RUN, not when
        # one was merely attempted. See `_before_model`.
        self._spoke = False
        self._turn_is_over = False
        # A heads-up that still owes its answer: the last landed `send_reply`
        # carried `heads_up=true`. Read by `reply_still_owed`.
        self._heads_up_owed = False
        # Did THIS guard cancel a model call? Public because
        # `nora.engagement.pipeline` and `nora.app.fire` read it to refuse DEC-93's
        # recovery: the text such a turn ends with is this class's veto string,
        # not anything the model authored.
        self.cancelled_model_call = False
        # Routing (`nora.agent.intent_router`): a tool to FORCE via `tool_choice`
        # on the turn's FIRST model call, or None to abstain. The counter lets
        # `_before_model` force on call 1 and release the pin on call 2; a pin
        # left in place would make her call it every pass and never `send_reply`.
        self._forced_first_tool = forced_first_tool
        # Model calls that COMPLETED, not attempts. `BeforeModelCallEvent` fires
        # inside the retry loop
        # (`strands/event_loop/event_loop.py::_handle_model_execution`: the
        # `while True:` invokes it at the top of every pass, and both the
        # exception path and the hook-requested path `continue` back to it), so
        # counting there would let a throttle-retry of the first call release the
        # `forced_first_tool` pin before any successful call had used it. Counted
        # in `_after_model`, where "the model answered" and "the attempt raised"
        # are distinguishable.
        self._model_calls = 0
        # A real (not cancelled) model call is admitted and not yet counted.
        # Distinguishes the retry pass, which must not advance the counter, from
        # a fresh call, and keeps a call this guard cancelled out of the count:
        # Strands fires `AfterModelCallEvent` with a synthesised stop response for
        # a cancelled call, indistinguishable from a real answer at that event
        # alone.
        self._model_call_in_flight = False
        # R-55, the read-before-write latch: the notes a `note_read` SUCCEEDED on
        # this turn. Per-tag by construction; a latch that outlived the turn would
        # let a read in one tag license a revision in the next, against a body
        # that has moved.
        #
        # Keyed on the note's TITLE, normalised by `nora.agent.tools.note_handle`,
        # not on an id: the injected index carries title + description and no id,
        # so the title is the only handle the model can name and the only one that
        # reaches this guard. `evaluate` sees the raw tool_use the model emitted;
        # `note_revise`'s own tool closure holds the id->head map the
        # compare-and-swap needs, and refuses separately when the title resolved
        # to a note this turn never read. Two keys for one fact: this one is what
        # the model is told, that one is what the write is bound to, and they
        # disagree exactly when a title changed hands mid-turn, which must fail.
        self._notes_read: set[str] = set()
        # R-99: each tool's accepted parameter names, keyed by tool name, derived
        # from the callable's own signature (`ToolRegistry.tool_param_names()`),
        # like `terminal`/`budgets`. `None` means the rule is OFF (as for
        # `lookups`), not "everything is unknown". A production caller always
        # passes the real table (`nora.engagement.stages.deliver.build`).
        self._param_names: Mapping[str, frozenset[str]] | None = param_names

    def _veto(self, rule: str, message: str) -> str:
        self._metrics.inc("nora_guard_vetoes_total", rule=rule)
        log.warning("guard veto %s: %s", rule, message)
        return message

    @property
    def counts(self) -> dict[str, int]:
        """Calls that COMPLETED this tag, per tool. The ledger's own dict, read
        by the guard test files."""
        return self._ledger.counts

    def record(self, tool_name: str) -> None:
        self._ledger.record(tool_name)

    def reserve(self, tool_name: str, *, ref: str = "") -> None:
        """Mark a call ADMITTED BUT NOT YET FINISHED, so it counts against the
        ceiling while it runs (see `nora.agent.call_ledger`)."""
        self._ledger.reserve(tool_name, ref=ref)

    def release(self, tool_name: str, *, ref: str = "") -> None:
        """Drop a reservation, whatever the call's outcome."""
        self._ledger.release(tool_name, ref=ref)

    def evaluate(self, tool_name: str, tool_input: dict) -> str | None:
        if tool_name not in self._budgets:
            return self._veto(
                "R-7", f"The tool {tool_name!r} does not exist. "
                       f"Use {_list_tool_names(self._budgets)}.",
            )

        # R-99: an argument the tool's OWN signature does not declare must not
        # run silently. Strands' generated pydantic model ignores any key it does
        # not declare (`FunctionToolMetadata.validate_input`, built by
        # `_create_input_model` with no `model_config`; pydantic v2's default is
        # `extra="ignore"`), so `media_fetch(file=...)` arrived empty. This fires
        # BEFORE that validation, on the same raw `tool_use` the R-3 comment below
        # establishes reaches this hook first.
        #
        # The whole check lives in `unknown_arguments_veto` (module level), shared
        # with `DreamGuard.evaluate`.
        #
        # Costs no budget whichever way it fires: this is a veto in `evaluate`, so
        # `_before_tool` never reaches `self.reserve` for it (DEC-33; see
        # `_after_tool`'s "a cancelled call did not run" branch).
        bad_arguments = unknown_arguments_veto(tool_name, tool_input, self._param_names)
        if bad_arguments is not None:
            return self._veto("R-99", bad_arguments)

        # R-3: scope is held by construction (DEC-21) and, since DEC-24, not even
        # expressible: no tool declares a conversation parameter, so
        # `load_transcript` reads the closure value and `send_reply` calls
        # `deliverer.deliver(text)` against a target its `Deliverer` bound at
        # construction. Vetoing on a value that changes nothing protects nothing
        # and makes a weak model unable to speak at all.
        #
        # This is not dead code (R-17: a counter that can never increment is
        # indistinguishable from a deleted one). The hook fires on the RAW
        # tool_use the model emitted, before Strands validates it, and validation
        # is a pydantic model built from the callable's signature, which ignores
        # properties it does not declare (`FunctionToolMetadata.validate_input`).
        # So a model that names a conversation still reaches here, its argument is
        # counted, and it is dropped before the tool body runs. The group id is
        # not in the prompt, so a value appearing here is invented.
        #
        # The check is `is not None`, not `!=`: absence is the normal case for
        # every well-formed call, so comparing a missing key against the bound id
        # would peg this counter at one-per-tool-call. Applied to every tool,
        # because the argument is out-of-schema everywhere.
        target = (tool_input or {}).get("conversation_id")
        if target is not None and target != self._conversation_id:
            log.warning(
                "tool scope mismatch on %s: model supplied conversation_id=%r (not a "
                "parameter of any tool); the bound %r is used regardless",
                tool_name, target, self._conversation_id,
            )
            self._metrics.inc("nora_tool_scope_violations_total", tool=tool_name)

        # R-7's per-tool budget, and for `send_reply` the message cap. THIS CHECK
        # RUNS BEFORE R-2's latch below, and that ordering is half of DEC-32.
        # `NATIVE_TOOLS["send_reply"].budget` is the cap (see the budget table in
        # `nora.agent.tools`), so the (cap+1)th reply is refused HERE and never
        # reaches `Deliverer.exhausted`. Relaxing R-2 alone would leave
        # multi-message dead: the model would be told "you already sent your
        # reply" by a budget nobody edited. Raising the number in
        # `NATIVE_TOOLS` is therefore required, and the latch below refuses the
        # message once it is raised. Both halves are pinned in
        # `tests/test_guards_replyguard.py`, under "the message cap lives in the
        # budget, not in R-2's latch".
        # The count is finished plus admitted-and-still-running, which makes the
        # ceiling hold inside one model turn's parallel batch (see
        # `nora.agent.call_ledger`).
        if self._ledger.at_ceiling(tool_name):
            if tool_name == "send_reply":
                # Labelled R-2 although the BUDGET is what refused it: the
                # model-facing sentence is the same either way, and the rule an
                # operator cares about is "one reply per tag". This counter cannot
                # tell the two branches apart, so anyone changing the cap must read
                # them together. `rule` values are append-only like metric names (a
                # new value silently splits every existing `rule="R-2"` query, R-17).
                return self._veto(
                    "R-2", "You already sent your reply. Do not call send_reply "
                           "again. If you still need something, use one of the "
                           "other tools; otherwise finish your turn.",
                )
            # The number the model is TOLD is finished plus in-flight, not `counts`
            # alone: vetoing the fifth call of a parallel batch while saying "you
            # already called it 0 time(s)" contradicts itself and reads as a
            # reason to retry.
            spent = self._ledger.spent(tool_name)
            if tool_name == "skills":
                # The one budgeted tool whose repeat call returns DIFFERENT content,
                # because the argument selects which body loads. Every other
                # budgeted tool is rationed on the premise that a second call
                # returns the identical answer, which is what makes "have that
                # context, answer from what you already have" true for them. For
                # `skills`, a budget smaller than the directory leaves some skills
                # unreachable, and the generic sentence would tell her she holds a
                # context she does not, inviting her to fill the gap from her own
                # vocabulary. The sentence below must be true at any budget and any
                # directory size, so the budget is not the fix;
                # `nora.agent.tools.NATIVE_TOOLS["skills"]` carries the reasoning
                # for the number itself.
                # "called", not "activated": `spent` counts what `record` saw, and
                # `record` fires for any completed call this guard did not veto,
                # `skills(<something that resolved nothing>)` included. A call that
                # came back empty spends the budget and loads no body, hence the
                # sentence is worded off what came BACK rather than what was asked.
                return self._veto(
                    "R-7", f"You already called {spent} skill(s) this turn, and "
                           "that is the limit. Any skill that did not come back is "
                           "not in front of you: answer from the ones that did, "
                           "and say you cannot check the rest rather than "
                           "describing what they say.",
                )
            spec = NATIVE_TOOLS.get(tool_name)
            if spec is not None and not spec.read_only:
                # A write tool's earlier calls may all have failed, so "you have
                # that context" is false and "answer" invites a claim it did not
                # earn.
                return self._veto(
                    "R-7", f"You already called {tool_name} {spent} time(s), which is the "
                           "limit, and no more will run. Only a call that returned ok did "
                           "anything: say what was not done, and do not say it was.",
                )
            return self._veto(
                "R-7", f"You already called {tool_name} "
                       f"{spent} time(s) and have that context. "
                       "Answer from what you already have.",
            )

        if tool_name == "send_reply":
            text = (tool_input or {}).get("text") or ""
            if not text.strip():
                return self._veto("R-4", "Your reply text is empty. "
                                         "Send real text or end your turn.")
            # R-2's latch, the authoritative bounded-delivery mechanism (spec S4),
            # asked of the DELIVERER rather than recomputed here. It is unreachable
            # for a well-behaved turn (the budget check above refuses the (cap+1)th
            # call first, from the same number) and is the backstop for the one
            # case where the two counters disagree: `record` skips a call that
            # raised or was cancelled, while `Deliverer` has already spent the
            # message if it got as far as the audit write.
            #
            # `exhausted`, not `attempted` (DEC-42): `attempted` would refuse the
            # second `send_reply` by the latch instead of the budget, leaving
            # multi-message dead after the budget was raised.
            if self._deliverer is not None and self._deliverer.exhausted:
                return self._veto(
                    "R-2", "You already sent your reply. Do not call send_reply "
                           "again. If you still need something, use one of the "
                           "other tools; otherwise finish your turn.",
                )
            # R-97: a turn nobody asked for does not take the floor to report an
            # absence. In tagged mode "I don't know" is an obligation, because she
            # was called; in participation nobody called her, and announcing the
            # absence is participation speech without grounding. A lookup's
            # `nothing_found`/`unavailable` must not be relayed as a contribution.
            #
            # It is a veto and not a sentence in the header because her
            # instruction already says it (silence.md: "silence is a complete
            # answer here, not a failure"; `ParticipationHeader`: "stay silent
            # anyway if you hold nothing") and she spoke anyway. What has to
            # change is the emission decision, not another paragraph.
            #
            # The predicate is "went looking and came back empty", not "holds
            # nothing". Zero lookups does not fire: the fast path answers from the
            # transcript, standing, notes and memory already in front of her
            # without opening a tool, and refusing that would silence the grounded
            # half too. One lookup answering `ok` does not fire either: she found
            # something, and what she does with it is hers.
            #
            # Costs no budget (DEC-33: `_after_tool` never `record`s a cancelled
            # call), and is not one-shot, for R-55's reason: the remedy is a
            # different call. A lookup that finds something lifts the refusal for
            # the rest of the turn; a model that re-fires `send_reply` against an
            # unchanged state is refused again.
            if (
                self._lookups is not None
                and self._lookups_made > 0
                and self._lookups_found == 0
            ):
                return self._veto(
                    "R-97",
                    f"You looked {self._lookups_made} time(s) this turn and "
                    "every one came back empty, so you hold nothing this room "
                    "does not. Nobody called you here — taking the floor to "
                    "report that you found nothing is not something the room "
                    "can use. Either look somewhere that would actually have "
                    "it, or say nothing and end your turn: silence is the "
                    "complete answer here.",
                )

        # R-55: the read-before-write latch. A refusal, not a nudge. It sits here
        # beside R-2's latch rather than in the budget check because it is the same
        # kind of rule: a per-turn fact about what has already happened. The
        # budget check answers "may she call this again"; this answers "was she
        # ever entitled to call it at all".
        #
        # It is a veto and not advice because the injected index carries title and
        # description only, nowhere near enough to revise a note. `note_revise`
        # REPLACES the note's rendered body, so a revision written without the body
        # in front of her destroys the arrangement that is the value of the tier,
        # silently: the prior revision survives in the table, but nothing in the
        # room learns the note stopped saying what it said.
        #
        # Not one-shot: the remedy is a different call (`note_read`, its own
        # budget), the veto names it, and a model that ignores the veto and
        # re-fires must be refused again; a one-shot latch would let the second
        # attempt through unread.
        #
        # Costs no budget: `_after_tool` never `record`s a cancelled call (DEC-33),
        # so read-then-revise is still two calls against a budget of two.
        if tool_name == "note_revise":
            title = (tool_input or {}).get("title") or ""
            if note_handle(title) not in self._notes_read:
                # Beside `_veto`'s own `nora_guard_vetoes_total{rule="R-55"}`, not
                # instead of it: that series answers "which rule fired" across every
                # guard; this one answers "why did a note_revise not land", whose
                # other two reasons are in the TOOL and never reach a veto.
                self._metrics.inc(
                    "nora_note_revise_refused_total", reason="not_read")
                return self._veto(
                    "R-55",
                    f"You have not read {title!r} this turn. Call note_read on it "
                    "first, then revise it — a note's value is how it is "
                    "arranged, and you cannot rewrite a body you have not seen.",
                )

        return None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)
        registry.add_callback(BeforeModelCallEvent, self._before_model)
        registry.add_callback(AfterModelCallEvent, self._after_model)

    def _terminal_budget_spent(self) -> bool:
        """True once NO terminal verb can be called again this turn.

        The turn-ending condition (DEC-32, generalised by DEC-42). With
        `send_reply`'s budget at 1 it is the same predicate as "a terminal verb has
        run", so raising the budget stops it firing after the first message
        without any other edit.

        `self._terminal` is checked for emptiness first because `all(())` is
        True: a guard told nothing is terminal would otherwise end the turn
        before the first model call.
        """
        return bool(self._terminal) and all(
            self.counts.get(name, 0) >= self._budgets.get(name, 0) for name in self._terminal
        )

    @property
    def reply_still_owed(self) -> bool:
        """Her last landed `send_reply` was a heads-up (`heads_up=true`): the
        answer she announced has not reached the room.

        `nora.engagement.pipeline` reads it to let DEC-93's recovery, the salvage
        and the cut-off fallback run although something WAS delivered; otherwise a
        delivered heads-up would disable all three, and an answer she then wrote as
        prose would be dropped while the turn read as `replied`.

        Her own flag, not an inference: "a `more=true` send, then work" cannot
        tell a heads-up from an answer followed by a note."""
        return self._heads_up_owed

    @staticmethod
    def _landed(result: Any) -> bool:
        try:
            text = result["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return False
        return isinstance(text, str) and text.strip() in ("sent", "held_for_review")

    @staticmethod
    def _reply_closes_turn(name: str, tool_input: Any, result: Any) -> bool:
        """True when a `send_reply` that LANDED said it was the last (DEC-42).

        After her first message the relaxed loop makes one more full model call so
        she can send a second, and that call almost always ends with nothing while
        re-sending the whole input. So the turn ends after a `send_reply` unless
        she passed `more=true` (any work after the reply, not only a second
        message), which keeps the relaxed path.

        Fail-open on purpose: only the tool's own `sent` or `held_for_review` word
        ends the turn. A `refused` send (R-6, R-18, an audit or send failure) is
        not the last word, since she may retry with different text, so it keeps the
        relaxed path, as does a result this cannot read. Only `send_reply` has the
        argument; `send_gif` is not covered here.
        """
        if name != "send_reply":
            return False
        # Read `more` the way the TOOL does: Strands validates the input with
        # pydantic's lax bool, so "true", "True", 1 and "yes" are all True to the
        # tool, and an `is True` here would end a turn the tool saw as continuing.
        # An unreadable value means the call itself failed validation, so it never
        # carries a `sent` word below.
        if not isinstance(tool_input, dict):
            return False
        for key in ("more", "heads_up"):
            try:
                if _MORE.validate_python(tool_input.get(key)):
                    return False
            except ValueError:
                return False
        return ReplyGuard._landed(result)

    def _before_model(self, event: BeforeModelCallEvent) -> None:
        """Relax `tool_choice` once she has spoken; end the turn at the cap.

        Two things, in that order, and the order is the mechanism (DEC-42).

        The cancel half. `tool_choice="required"` (see `nora.agent.factory`) is
        what makes this model call `send_reply` at all, but it applies to EVERY
        turn, so once the reply has gone the model is still forced to call
        something and the loop cannot terminate; it would run until the invocation
        timeout, costing time and resolving the indicator wrongly (the timeout path
        exits before `delivered` is set, so a succeeded turn would show the group
        a failure). Cancelling here is the narrow fix. The R-7 budget already
        refuses a `send_reply` past the cap (checked before R-2's latch), so
        nothing after the last allowed call can change what the group sees.

        "The turn is over" is a property of the tools, not of one tool's side
        effect: a verb is terminal by `terminal=True` in `NATIVE_TOOLS` (DEC-32),
        with no edit to this file. A `send_reply` the `Deliverer` REFUSED (R-6
        canary, R-18 egress, an audit-write or send failure) is still recorded, so
        it spends one of the cap instead of ending the turn; the turn ends when
        the terminal budget is spent.

        The relaxation half. Once a terminal verb has run she has spoken, and from
        that model call onwards `tool_choice` becomes `auto`, so she may stop by
        declining to call anything, which under `required` she structurally
        cannot. It is done here, and only here, because
        `BeforeModelCallEvent._can_write` is `return name == "cancel"`: the event
        refuses attribute assignment on itself, but `HookEvent.agent` is a public
        field and `OpenAIModel.update_config` exists, so `event.agent.model` is
        reachable and writable. `tests/test_strands_api_contract.py` pins this
        against the real SDK, because the mechanism rests on it.

        Reaching the model is only safe because it is per tag
        (`nora.agent.factory.ModelSource`); shared, this line would relax a
        concurrent tag's FIRST call. `tests/test_model_per_tag.py` is that probe.

        Ordered cancel-first so the relaxation is never applied to a model call
        that is not going to happen.
        """
        if self._turn_is_over:
            event.cancel = "Reply already sent — the turn is finished."
            # DEC-93: this turn's last "assistant text" is now the guard's, not
            # hers. Strands synthesises `message={"content":[{"text": cancel_text}]}`
            # with `stop_reason="end_turn"` for a cancelled model call
            # (`strands/event_loop/event_loop.py`), indistinguishable from the model
            # having written prose, and this string passes R-6 and R-18, so nothing
            # downstream would stop it reaching the group. Recorded here, at the one
            # place that knows.
            self.cancelled_model_call = True
            return
        model = getattr(getattr(event, "agent", None), "model", None)
        # A real call from here on. Set before the `_spoke` return so a relaxed
        # call is counted like any other.
        self._model_call_in_flight = True
        # Which call this is, counting from 1: completed calls plus this one. A
        # retry pass re-enters here with `_model_calls` unchanged, so it
        # re-computes the same index and re-applies the same pin.
        call_index = self._model_calls + 1
        if self._spoke:
            tool_choice.relax(model)
            return
        # Not spoken yet. Routing: force the routed tool on the FIRST model call,
        # then release the pin on the next so the loop is free to reach
        # `send_reply` (still `required`, never `auto`: before she has spoken,
        # `auto` sends prose, R-5's silent shape). Ordered after the `_spoke`
        # return so a forced pin never survives into the post-reply relaxation.
        if self._forced_first_tool is not None:
            if call_index == 1:
                tool_choice.force(model, self._forced_first_tool)
            elif call_index == 2:
                tool_choice.require(model)

    def _after_model(self, event: AfterModelCallEvent) -> None:
        """Count the model call that just COMPLETED.

        `exception is None` is the whole discriminator, and it is exact: the retry
        strategy (`strands/event_loop/_retry.py`) sets `event.retry` only after
        `is_retryable(event.exception)`, so a pass that raised produced no answer,
        and the next `BeforeModelCallEvent` is a re-attempt of the same call.

        `event.retry` is deliberately not consulted. `AfterModelCallEvent` uses
        reverse callback ordering, so a strategy registered after this guard has
        not necessarily run yet and the flag is not final here. A hook discarding a
        SUCCESSFUL response to re-ask is counted as a call, which is honest: the
        model was called and it answered.

        Never raises, for `_before_tool`'s reason: a counter is not worth killing a
        turn over.
        """
        try:
            if not self._model_call_in_flight:
                return                      # a call this guard cancelled
            if getattr(event, "exception", None) is not None:
                return                      # attempt failed; a retry re-uses the index
            self._model_calls += 1
            self._model_call_in_flight = False
        except Exception:
            log.exception("ReplyGuard._after_model raised; ignoring")

    def _before_tool(self, event) -> None:
        try:
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            tool_input = tool_use.get("input") or {}
            if not name:
                return
            message = self.evaluate(name, tool_input)
            if message:
                event.cancel_tool = message
                return
            # ADMITTED. Held against the ceiling from here until `_after_tool`
            # releases it, so the rest of a parallel batch is measured against this
            # call too. The mutation lives here rather than in `evaluate`, which is
            # pure and SDK-free so every rule stays testable without strands; this
            # hook is the thin translation layer that owns lifecycle.
            self.reserve(name, ref=str(tool_use.get("toolUseId") or ""))
        except Exception:
            log.exception("ReplyGuard._before_tool raised; allowing the call")

    def _after_tool(self, event) -> None:
        """Two early returns, and two kinds of per-call state with opposite
        correct placements:

        - "this call FINISHED": the in-flight reservation. True on every path,
          including the ones that raised or were vetoed, so it belongs ABOVE both
          returns.
        - "this call SUCCEEDED": `record` and the terminal latch. True on one
          path, so each belongs BELOW both returns.

        Only the second kind is visible through `counts`, which is why a mistake
        in the first kind survives a suite that asserts on `counts`.
        """
        try:
            # The reservation comes back first, on every path, because it is not
            # budget: it is "this call is still running". Releasing it after the
            # early return below would leak: a tool call that RAISED would hold its
            # reservation for the rest of the tag, `spent` would stay one too high,
            # and R-7 would refuse a later call the model was entitled to. `counts`
            # stays correct throughout, so only a direct read of the in-flight half
            # sees it.
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            # Keyed on the SDK's `toolUseId`, so this drops THIS call's reservation
            # and not whichever one happens to be outstanding. A vetoed call reaches
            # this hook too and never reserved: a name-keyed release would let it
            # decrement a SIBLING's reservation in the same parallel batch,
            # reopening the ceiling by one (the trace `call_ledger` exists to
            # prevent).
            self.release(name, ref=str(tool_use.get("toolUseId") or ""))
            if getattr(event, "exception", None) is not None:
                return                       # a failed call does not consume budget
            # A cancelled call did not run, so it consumes no budget and cannot have
            # ended the turn. Strands fires this hook for a call `_before_tool`
            # vetoed too (`cancel_tool` produces an error `ToolResult` and
            # `exception=None`, `strands.tools.executors._executor`), so
            # `cancel_message` is the only field that separates the two.
            # `tests/test_strands_api_contract.py` asserts it against the real SDK.
            #
            # This applies to `record` as well as the terminal latch (DEC-32,
            # DEC-33): `send_reply`'s budget can be 1, so a `send_reply("")`
            # refused by R-4 must not spend the tag's only reply, or the model's
            # own correction would be refused by R-7 with "you already sent your
            # reply", blocking the recovery silently.
            if getattr(event, "cancel_message", None) is not None:
                return
            self.record(name)
            # R-97's two counters, below both early returns for R-55's reason: they
            # mean "this lookup RAN", true on one path only. A call that raised, or
            # that this guard cancelled, went looking nowhere. Accumulated over the
            # turn: a turn whose first lookup found the answer and whose second came
            # back empty still holds the answer.
            if self._lookups is not None and name in self._lookups:
                self._lookups_made += 1
                if _unwrap_tool_status(getattr(event, "result", None)) == "ok":
                    self._lookups_found += 1
            # R-55's latch. This is below BOTH early returns on purpose, and that
            # placement is the rule. `release()` above means "this call FINISHED",
            # true on every path; the latch means "this call SUCCEEDED", true on one
            # path only, so the two take opposite positions in the same method.
            #
            # Three ways a read can fail to license a revision, all a call that
            # produced no body:
            #   - it RAISED (the first early return);
            #   - the guard CANCELLED it (the second);
            #   - it ran and answered `nothing_found` or `refused`: the note is not
            #     there, or the title matched two and the tool refused to guess.
            #     Strands calls both a successful tool call, so only
            #     `_unwrap_tool_status` can tell them from a real read, and a read
            #     of a note that isn't there must not license a revision.
            if (
                name == "note_read"
                and _unwrap_tool_status(getattr(event, "result", None)) == "ok"
            ):
                title = (
                    (getattr(event, "tool_use", None) or {}).get("input") or {}
                ).get("title") or ""
                if title:
                    self._notes_read.add(note_handle(title))
            if name in self._terminal and self._landed(getattr(event, "result", None)):
                self._heads_up_owed = name == "send_reply" and _flag(
                    tool_use.get("input") or {}, "heads_up")
            if name in self._terminal:
                # She has spoken: `_before_model` relaxes `tool_choice` from here
                # on, and ends the turn once every terminal verb's budget is spent.
                # Two flags because they answer two questions, "may she stop?" and
                # "must she?"; at a `send_reply` budget of 1 they flip together,
                # and collapsing them would cap her at one message.
                self._spoke = True
                self._turn_is_over = (
                    self.counts.get(name, 0) >= self._budgets.get(name, 0)
                    or self._reply_closes_turn(
                        name, (tool_use.get("input") or {}),
                        getattr(event, "result", None),
                    )
                )
        except Exception:
            log.exception("ReplyGuard._after_tool raised; ignoring")
