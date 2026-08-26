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

from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from ..doctrine.loader import Doctrine
from ..domain.media import MediaBlob
from ..domain.models import ConversationTarget, DeliveryStatus, NormalizedMessage
from ..domain.ports import OutboundAttachment, SendResult, Styled
from ..obs.metrics import Metrics
from . import tool_choice
from .call_ledger import CallLedger
from .egress import LeakGuard, scan_reply
from .links import LinkCarry
from .markdown import render_markdown
from .outbound import Outbound, OutboundComposer
from .tools import note_handle, utc_now

log = logging.getLogger("nora.guards")


class DeliveredObserver(Protocol):
    """Told once a reply has actually reached the group (DEC-83).

    A one-method port so `Deliverer` — which lives in `nora.agent` — never
    imports the reaction stage in `nora.engagement`, which the layered import
    contract forbids in that direction anyway.
    `nora.engagement.stages.indicate.DeliverySettler` is the shipped
    implementation; `_NoDeliveredObserver` below is the default.

    `settle()` returns None and must never raise: it is decoration on the far
    side of a message the group already has, and R-5's rule is that decoration
    failing is never allowed to become the turn's problem.
    """

    def settle(self) -> None: ...


class _NoDeliveredObserver:
    """The default. Delivery still happens; nothing is told about it.

    Constructed PER `Deliverer` rather than shared as a module-level instance —
    `tests/architecture/test_no_module_level_mutable_singletons.py` refuses one,
    and it is right to even though this object holds no state: the rule is
    structural precisely so nobody has to re-audit "is it still stateless?" on
    every later edit. A `Deliverer` already lives and dies with one tag, so this
    costs one empty object per tag."""

    def settle(self) -> None:
        return None





class Deliverer:
    """Bounded delivery: audit-first, then send. The ONLY caller of adapter.send().

    The latch counts *attempts*, not successes (R-2): a failed send consumes a
    message, because v2 has no auto-retry and a second attempt would be an
    unbounded loop.

    **`message_cap` is the number, and it is REQUIRED (DEC-42).** It is the
    same number `ReplyGuard` enforces as `send_reply`'s R-7 budget, threaded
    from the same `ToolRegistry.budgets()` mapping through
    `nora.engagement.stages.deliver.build`, because DEC-32's whole argument is
    that the cap must be ONE number: R-7's budget check runs BEFORE R-2's
    latch, so two numbers that disagree resolve silently in favour of the
    smaller and report the refusal under the other one's name.

    No default, deliberately. A `message_cap = 1` default would make "raised
    the budget and forgot the deliverer" — the precise failure
    `docs/superpowers/plans/2026-08-06-nora-grounding-implementation.md` §5e
    names — construct cleanly and cap her at one message anyway, which is
    exactly the invisible half of that trap.

    **`audience` is REQUIRED and has no default**, for the reason `message_cap`
    has none. It labels every `nora_silent_failures_total` this class raises —
    five branches, more than any other single site — with which path was
    speaking (`"tag"` or `"heartbeat"`). A default would let the heartbeat's
    fire path construct cleanly and report its failures as conversation
    failures, spending the A8 budget (`docs/runbook.md`) that is filtered to
    `audience="tag"` and sized for tags. The reverse mistake is louder and
    therefore safer: a missing keyword is a `TypeError` at construction.

    **`sent_texts` is a LIST, not a `last_text` (Honcho v2, design/24).** The
    old `last_text` fed Nora's reply to Honcho but was WRONG under multi-message
    — a four-message reply fed the last message and silently dropped three
    (design/16 §4), so DEC-36 deleted it. v2 restores the feed CORRECTLY: every
    delivered message is appended, and the feedback stage feeds each under the
    Nora aiPeer into the conversation's session (§3). A list is the fix the
    scalar could not be — it cannot drop the earlier messages of a multi-message
    turn because it holds all of them.
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
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._target = target
        self._tag_id = tag_id
        self._nora_sender_id = nora_sender_id
        # Which path is speaking — `"tag"` or `"heartbeat"`. Carried only onto
        # `nora_silent_failures_total`; see the class docstring for why it is
        # required, and the metric spec for why only that one series gets it.
        self._audience = audience
        self._metrics = metrics
        self._leak = LeakGuard(doctrine, canary)
        self._doctrine_sha256 = doctrine.sha256
        self._message_cap = message_cap
        # Told when a reply actually reaches the group (DEC-83). Defaults to a
        # no-op so this stays one optional collaborator rather than a required
        # argument on nine construction sites, and so `Deliverer` never has to
        # branch on None at the call site.
        self._on_delivered = on_delivered or _NoDeliveredObserver()
        # What decoration rides with the text (`nora.agent.outbound`). **This
        # one DOES default to None**, unlike `message_cap` and `audience` two
        # paragraphs up, and the asymmetry is the argument rather than an
        # inconsistency: those two are numbers that a missing value would get
        # WRONG while looking right — capping her at one message, or charging a
        # heartbeat's failures to the tag budget — whereas a missing composer
        # produces exactly the message this class sent before the feature
        # existed. There is no wrong claim available to it. `deliver.build`
        # takes it with NO default all the same, so the two production callers
        # still have to decide; the default lives here because a `Deliverer`
        # built in a test that is not about mentions should not have to.
        self._composer = composer
        # DEC-89: the links `place_info` minted this turn, shared with the tools
        # (`nora.agent.links.LinkCarry`). Defaulted to a fresh EMPTY carrier for
        # the reason `composer` is defaulted rather than required — an empty one
        # appends nothing and sends the exact message this class sent before the
        # feature existed, so there is no wrong claim available to it. The
        # decision of whether to SHARE one with the tools is `deliver.build`'s,
        # where both halves of the wiring are visible at once; a `Deliverer`
        # holding its own private carrier is the inert case, not a broken one.
        self._links = links if links is not None else LinkCarry()
        self._attempts = 0
        # Every `deliver()` ENTRY, whether or not it survived the guards —
        # distinct from `_attempts`, which counts the ones that reached the
        # platform. R-2's latch must keep counting attempts (an empty reply
        # deliberately does not consume a message,
        # `test_an_empty_reply_does_not_consume_the_latch`), but
        # `check_outcome` needs a different question: did the model call
        # `send_reply` AT ALL? Answering that with `attempted` reports a turn
        # whose every reply was guard-refused as `no_send_call` — on top of the
        # `egress_block`/`doctrine_leak`/`empty_reply` already counted for it
        # (external review 2026-08-10). Two counts for one turn, and the second
        # one is false: she DID try to speak, and a guard stopped her.
        #
        # That mislabelling matters beyond tidiness: ADR-0006's OPEN root-cause
        # investigation reasons from `no_send_call` meaning "the loop ended
        # having never called the tool". This makes that reading true.
        self._send_calls = 0
        self._delivered_count = 0
        # Every message actually SENT this turn (Honcho v2): the feedback stage
        # feeds each under the Nora aiPeer. Populated only on a successful send,
        # so a refused/failed message is never fed.
        self._sent_texts: list[str] = []

    @property
    def sent_texts(self) -> tuple[str, ...]:
        """Every message actually sent this turn, in order (Honcho v2). A tuple
        so a caller cannot mutate the Deliverer's record; the feedback stage
        feeds each under the Nora aiPeer."""
        return tuple(self._sent_texts)

    @property
    def delivered(self) -> bool:
        return self._delivered_count > 0

    @property
    def delivered_count(self) -> int:
        """How many messages actually reached the group this tag.

        Derived from, rather than kept beside, the `delivered` flag — one
        counter, so the two can never disagree. `nora_messages_per_tag` reads
        it, and the histogram's baseline was recorded while the answer was
        still known to be 0-or-1, before this became able to exceed 1.
        """
        return self._delivered_count

    @property
    def attempts(self) -> int:
        """Messages this tag has SPENT — sent, refused, or failed in flight."""
        return self._attempts

    @property
    def attempted(self) -> bool:
        return self._attempts > 0

    @property
    def called(self) -> bool:
        """Did the model call `send_reply` at all, however it went?

        `attempted` asks whether a message reached the platform; this asks
        whether she TRIED. A guard-refused turn is False for the first and True
        for the second, and conflating them is what put `no_send_call` on turns
        that were in fact blocked."""
        return self._send_calls > 0

    @property
    def exhausted(self) -> bool:
        """No message left. R-2's latch, generalised from one to `message_cap`.

        This is what `ReplyGuard` asks instead of `attempted`: with the cap at
        1 the two are the same predicate, and above 1 `attempted` would refuse
        the second message while claiming multi-message shipped.
        """
        return self._attempts >= self._message_cap

    async def _compose(self, styled: Styled) -> Outbound:
        """`styled` as an `Outbound` — decorated if a composer is wired, bare if not.

        The `except` is not redundant with `compose`'s own never-raise
        contract. A mention is decoration on a message a human is waiting for,
        and the one failure this class must never have is «she went silent
        because a name lookup blew up». Degrading to `Outbound(styled)` sends
        the identical message the composer-less path sends, which is the honest
        floor rather than a guess.

        Note what the degraded answer KEEPS: the text and its style ranges,
        because both live in the `Styled` this was handed. A composer that
        blows up costs the @-mention and the preview card — the two things this
        class computes — and not the bold, which was already decided upstream
        and cannot be made wrong by anything that happens here.
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
    ) -> bool:
        self._send_calls += 1
        if self.exhausted:                                     # R-2
            log.warning(
                "delivery refused: %d message(s) already spent of %d (tag=%s)",
                self._attempts, self._message_cap, self._tag_id,
            )
            self._metrics.inc("nora_guard_vetoes_total", rule="R-2")
            return False

        # DEC-82: convert Markdown ONCE, here, BEFORE the remaining guards, so
        # each one measures the bytes that actually LEAVE rather than a draft
        # that no longer exists. It also happens before `row` is built, so the
        # audit row, `sent_texts` and the Honcho feed all carry the ONE
        # canonical form — the words the group actually saw.
        #
        # ONE call, and `styled` is the only model of this reply from here on
        # (comprehend-once). `render_markdown` both deletes the asterisks and
        # produces the ranges that index what is left, so a SECOND call would
        # be a second interpretation of one text — and the two would agree
        # right up until somebody made the function stateful or non-total. The
        # value is passed whole to `_compose` below rather than unpacked; see
        # `nora.agent.markdown` for why the text and its ranges never travel
        # separately.
        drafted = text

        if (not text or not text.strip()) and not attachments:  # R-4
            log.warning("delivery refused: empty reply (tag=%s)", self._tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="empty_reply",
                platform=self._target.platform, audience=self._audience,
            )
            return False

        # DEC-89: put back the links this reply EARNED and dropped. Ordered by
        # three constraints, and the merge of DEC-82 and DEC-89 is what pins it
        # to exactly here rather than anywhere else that also "works":
        #   - AFTER R-4, so a link can never rescue an empty reply into a sent
        #     one. That is why R-4 reads the DRAFT rather than the rendered
        #     value — it is the authored reply that has to be non-empty.
        #   - BEFORE the render, so `render_markdown` is called ONCE. The first
        #     merge of these two features appended the link after the render and
        #     re-rendered, which `test_the_composer_is_handed_the_rendered_value`
        #     caught: a second interpretation is a second model of one message,
        #     and comprehend-once is the whole reason `Styled` exists.
        #   - BEFORE R-6/R-18, so those guards measure the bytes that actually
        #     LEAVE. A text transformation sitting in front of a fail-closed
        #     guard without the guard seeing its output is the exact hole the
        #     2026-08-10 audit found in DEC-82's first cut.
        text, carried = self._links.apply(text)
        if carried:
            self._metrics.inc("nora_reply_links_carried_total", outcome="appended")

        # DEC-82: ONE interpretation of this reply, and `styled` is the only
        # model of it from here on. `render_markdown` both removes the emphasis
        # delimiters and produces the offsets into what is left, so the text and
        # its ranges can never be separated.
        styled = render_markdown(text)

        # R-6 against BOTH forms, because DEC-82's rewrite cuts both ways and
        # only one direction was noticed at first (review 2026-08-10). Removing
        # the delimiters REVEALS a doctrine span broken up by emphasis —
        # checking the draft alone would miss it. But `_MD_HEADING` also DELETES
        # the `# ` that the raw doctrine haystack still contains, so a window
        # straddling a heading stops matching — measured, that let 293 verbatim
        # voice.md spans through that the draft form blocks. Neither form is a
        # superset of the other, so checking either one alone is a hole. Check
        # both and refuse on either: a false block costs one silent reply, a
        # leak is not recoverable.
        #
        # Converting the emphasis to a STYLE rather than dropping it does not
        # weaken this by a character: the guard's subject is the text, the
        # ranges ride in a parallel field, and `styled.text` is exactly the
        # string the old strip produced. What changed is that the same string
        # now also carries offsets — which is why it arrives as one value.
        reason = self._leak.check(styled.text) or self._leak.check(drafted)  # R-6
        if reason:
            log.error("doctrine_leak_blocked: %s (tag=%s)", reason, self._tag_id)
            self._metrics.inc("nora_leak_blocks_total")
            self._metrics.inc(
                "nora_silent_failures_total", reason="doctrine_leak",
                platform=self._target.platform, audience=self._audience,
            )
            return False

        # R-18 — see `nora.agent.egress` for why this exists and what it
        # deliberately does not match. This is the ONE caller of `scan_reply`,
        # the strict predicate: `text` here is addressed to the group, so the
        # model name is a disclosure. Everywhere else uses `scan_identifiers`.
        #
        # BOTH forms, for the same reason R-6 above checks both — and this one is
        # the sharper case (fail-closed audit 2026-08-10). Every identifier
        # pattern is `\b`-anchored, and removing an asterisk BETWEEN two word
        # characters joins them and destroys the boundary. Measured on the
        # committed code and on the conversion that replaced it — the emphasis
        # becomes a BOLD range in both these cases, and the characters that made
        # the boundary are gone either way:
        #
        #   "*a*deadbeef-1234-5678-9abc-def012345678"  raw→uuid   converted→None
        #   "*+85298765432*a"                          raw→phone  converted→None
        #
        # i.e. a member's real number, wrapped in emphasis, would have been SENT
        # — and would have been sent in BOLD. A text transformation must never
        # sit in front of a fail-closed guard without the guard seeing both
        # sides; this module's own docstring is explicit that blocking costs one
        # reply and leaking is not recoverable.
        kind = scan_reply(styled.text) or scan_reply(drafted)
        if kind:
            log.error("egress_blocked: %s (tag=%s)", kind, self._tag_id)
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            self._metrics.inc(
                "nora_silent_failures_total", reason="egress_block",
                platform=self._target.platform, audience=self._audience,
            )
            return False

        self._attempts += 1
        # **THE LAST THING THAT MAY LOOK AT THE TEXT.** `outbound.text` is the
        # string that goes on the wire and into the audit row from here on, and
        # neither `text` nor `styled` is read again below — see
        # `nora.agent.outbound`'s module docstring for why that is the
        # mechanism. A transformation added after this line cannot corrupt a
        # mention offset, because it cannot reach the wire at all; one that is
        # genuinely needed belongs in `nora.agent.markdown.render_markdown`,
        # where the text and every range over it are produced together. Pinned
        # on the AST by
        # `tests/architecture/test_outbound_offsets_are_computed_last.py`.
        #
        # `styled` and not `styled.text`: handing the composer the string alone
        # would strand the style ranges here, and re-pairing them with the
        # composed text afterwards is precisely the assignment that this design
        # exists to make unwritable.
        outbound = await self._compose(styled)
        row = NormalizedMessage(
            platform=self._target.platform,
            conversation_id=self._target.conversation_id,
            conversation_type=self._target.conversation_type,
            sender_id=self._nora_sender_id,
            sender_name=None,
            timestamp=int(time.time() * 1000),
            # The AUDITED body is the text as written, `@grant` and all —
            # which is also exactly what goes on the wire, because a mention
            # replaces no characters (`nora.agent.outbound`). That equality is
            # what keeps her own rows readable in the transcript: Signal's
            # inbound convention is a U+FFFC placeholder, and `MentionRenderer`
            # cannot rehydrate one in a row of HERS (her `raw` is tag
            # bookkeeping, not an envelope, so `_row`'s cheap reject leaves the
            # body as stored) — so a placeholder-shaped outbound message would
            # have reintroduced DEC-34's exact void inside Nora's own history.
            body=outbound.text if record_text is None else record_text,
            raw={"tag_id": self._tag_id, "source": source,
                 "doctrine_sha256": self._doctrine_sha256,
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
            return False

        try:
            result = await self._adapter.send(
                target=self._target, text=outbound.text,
                mentions=outbound.mentions, styles=outbound.styles,
                preview=outbound.preview,
                attachments=attachments,
            )
        except Exception:
            log.exception("send raised (tag=%s)", self._tag_id)
            result = SendResult(ok=False)

        try:
            await self._store.set_delivery_outcome(
                row_id=row_id,
                status=DeliveryStatus.SENT if result.ok else DeliveryStatus.FAILED,
                ts=result.timestamp,          # the platform's stamp, not ours
            )
        except Exception:
            log.exception("delivery_status update failed (tag=%s)", self._tag_id)

        if result.ok:
            self._delivered_count += 1
            # The never-silent fallback (`source="fallback"`) is DELIVERED to the
            # group — so the turn counts as non-silent (`delivered`) — but it is a
            # canned apology, NOT one of Nora's authored words, so it is kept OUT
            # of `sent_texts` and never feeds her Honcho self-model card (review
            # 2026-08-09). Only her real replies build that model.
            if source != "fallback" and (not attachments or outbound.text.strip()):
                self._sent_texts.append(outbound.text)
            self._metrics.inc("nora_replies_total", status="sent", platform=self._target.platform)
            # DEC-83: the group can SEE the reply now, so mark the turn done now
            # rather than when the agent loop finally drains — that is a whole
            # extra model round trip later (measured median 14.9 s). Told to a
            # collaborator, not a callback, per the `__init__`-only seam rule;
            # the default is a no-op, so a Deliverer built without one (the fire
            # path, every test that does not care) behaves exactly as before.
            #
            # `source != "fallback"` is deliberately NOT applied here: the
            # fallback IS delivered, the group did get something, and leaving the
            # 👀 up through a fallback turn would misreport the one case where a
            # human is most likely to be watching for it.
            self._on_delivered.settle()
        else:
            self._metrics.inc("nora_replies_total", status="failed", platform=self._target.platform)
            self._metrics.inc(
                "nora_silent_failures_total", reason="send_failed",
                platform=self._target.platform, audience=self._audience,
            )
        return result.ok


def _list_tool_names(names: Any) -> str:
    """`"now, load_transcript or send_reply"` — sorted so the R-7 unknown-tool
    message is deterministic and reads in a natural order."""
    ordered = sorted(names)
    if len(ordered) <= 1:
        return ", ".join(ordered)
    return ", ".join(ordered[:-1]) + " or " + ordered[-1]


def _unwrap_tool_status(result: Any) -> str | None:
    """The tool's OWN status (`ok`/`refused`/`unavailable`/...), unwrapped
    from Strands' own success/error envelope.

    `AfterToolCallEvent.result` is a `strands.types.tools.ToolResult` —
    `{"status": "success"/"error", "content": [...], "toolUseId": ...}` —
    which is STRANDS' verdict on whether the call raised, not the tool's
    own semantic answer. Every native tool in this codebase returns a plain
    dict with no `content` key, so `DecoratedFunctionTool._wrap_tool_result`
    always takes its ELSE branch: `content` becomes `[{"text":
    json.dumps(the_tools_own_dict)}]`. Reaching the real status means
    parsing that text back out — this is the one place in the codebase that
    does, shared by `ToolCallLog` (this module) and `ReplyGuard`'s R-45
    check (`evaluate`, below) so the unwrap logic exists exactly once.

    Returns `None` for a result shaped in a way this cannot parse (a tool
    that carries no `status` key at all — `now` and `send_reply` return a
    bare value, `load_transcript` a status-free envelope) — callers treat
    `None` as "not `ok`", never as "assume ok"."""
    try:
        content = result["content"]
        payload = json.loads(content[0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None
    return payload.get("status") if isinstance(payload, dict) else None


class ToolCallLog(HookProvider):
    """Write path for the tool-recall log (R-48, tool-recall-log spec §3).
    Constructed fresh per tag, like `ReplyGuard`.

    A DIFFERENT responsibility from `ReplyGuard` (veto/budget logic) — this
    is persistence, so it is its own small collaborator and its own
    `HookProvider`, not a method bolted onto `ReplyGuard`. `store=None` is
    the honest INERT default (offline/eval compositions, or any deployment
    that has not wired one) — a missing store means every write is a no-op,
    never a raised error and never a fallback that pretends to remember
    something it didn't (R-5's shape, applied to a write path rather than a
    read).
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
            # `content[0]["text"]` is `json.dumps(the tool's own dict)` —
            # see `_unwrap_tool_status`'s own docstring — so this second
            # `json.loads` is the tool's REAL return value, not Strands'
            # wrapper. Guarded the same way `_unwrap_tool_status` is;
            # unreachable here in practice since that call already
            # succeeded above, but never assume a second parse of the same
            # string cannot fail for a reason the first one didn't hit.
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
            # Decoration on a turn that has already completed correctly
            # without it (spec §3) — never let a log write cost the reply
            # that already went out.
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

    `budgets` is DEC-10: the per-tool call-count ceiling R-7 enforces used to be
    a hardcoded `BUDGETS` class attribute, which meant any tool not in that
    literal table (a fourth native tool, `end_episode`) was vetoed before it
    ever ran. It is now injected — `nora.agent.registry.ToolRegistry.budgets()`
    via `nora.engagement.stages.deliver.build` — so the number a tool costs
    lives with the tool, not with the guard. R-7's OTHER half, unknown-tool
    default-deny, is unchanged: a tool absent from `budgets` is still vetoed.

    `terminal` is DEC-32 and is the same argument applied to the OTHER thing
    this guard used to hardcode: which verbs end a turn. It comes from
    `ToolRegistry.terminal_tools()` along the same path, and it has NO DEFAULT
    on purpose. A guard constructed without it would end no turn at all —
    `tool_choice="required"` means the loop cannot stop by itself — which is
    the ~25s-per-tag runaway DEC-21 fixed, and defaulting to `()` would hide
    that behind a working-looking constructor.
    """

    def __init__(
        self, *, conversation_id: str, budgets: Mapping[str, int],
        terminal: Collection[str], deliverer=None, metrics: Metrics,
        forced_first_tool: str | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._budgets = dict(budgets)
        self._terminal = frozenset(terminal)
        self._deliverer = deliverer
        self._metrics = metrics
        # Completed calls AND admitted-but-unfinished ones, in one object
        # (`nora.agent.call_ledger`) — the whole of DEC-33's reservation, held
        # rather than re-derived so `DreamGuard` enforces the identical ceiling
        # with the identical code. That module's docstring carries the live
        # trace this shape exists because of.
        self._ledger = CallLedger(self._budgets)
        # Set by `_after_tool` when a terminal verb has actually RUN — not
        # when one was merely attempted. See `_before_model`.
        self._spoke = False
        self._turn_is_over = False
        # Did THIS guard cancel a model call? Public because
        # `nora.engagement.pipeline` and `nora.app.fire` read it to refuse
        # DEC-93's recovery — the text such a turn ends with is this class's
        # veto string, not anything the model authored.
        self.cancelled_model_call = False
        # Phase 2 routing (`nora.agent.intent_router`): a tool to FORCE via
        # `tool_choice` on the turn's FIRST model call, or None to abstain. The
        # counter lets `_before_model` force on call 1 and RELEASE the pin on
        # call 2 — a pin left in place would make her call it every pass and
        # never `send_reply`.
        self._forced_first_tool = forced_first_tool
        # Model calls that COMPLETED, not attempts — audit F12/A11.
        # `BeforeModelCallEvent` fires INSIDE the retry loop
        # (`strands/event_loop/event_loop.py::_handle_model_execution`: the
        # `while True:` invokes it at the top of every pass, and both the
        # exception path and the hook-requested path `continue` back to it), so
        # a throttle-retry of call #1 used to increment this to 2 and release
        # the `forced_first_tool` pin before any successful call had used it.
        # Counted in `_after_model` instead, where the difference between «the
        # model answered» and «the attempt raised» is actually visible.
        self._model_calls = 0
        # A real (not cancelled) model call is admitted and not yet counted.
        # Distinguishes the retry pass — which must NOT advance the counter —
        # from a fresh call, and keeps a call this guard cancelled out of the
        # count entirely: Strands fires `AfterModelCallEvent` with a synthesised
        # stop response for a cancelled call, which is indistinguishable from a
        # real answer at that event alone.
        self._model_call_in_flight = False
        # R-45: has the make_artifact/context nudge already fired this turn?
        # One-shot, like R-4/R-2's own care about not silently blocking a
        # correction (DEC-33) — a second veto on the SAME defect the model
        # already ignored once would not change its answer, only spend a
        # turn refusing the model's only remaining path to `make_artifact`
        # at all (its own budget is 1, and this veto does not consume it —
        # see `evaluate`).
        self._context_nudge_given = False
        # R-45's status-aware half (tool-recall-log spec §5): did the LAST
        # `recall_lookups` call this turn actually find something? `counts`
        # alone can't answer this — it increments for any non-cancelled
        # call regardless of what the tool itself answered — so this is
        # read directly off the event in `_after_tool`, the one place that
        # sees the raw result. Overwritten on every `recall_lookups` call,
        # not accumulated: only the MOST RECENT call's own answer is what
        # "have I just seen something to reuse" means.
        self._recall_found_something = False
        # R-55, the read-before-write latch (note-tier spec §6.2): the notes a
        # `note_read` SUCCEEDED on this turn. Per-tag by construction, like
        # every other counter here — a latch that outlived the turn would let a
        # read in one tag license a revision in the next, against a body that
        # has moved.
        #
        # Keyed on the note's TITLE, normalised by `nora.agent.tools.
        # note_handle`, and not on an id — spec §16.1: the injected index
        # carries title + description and no id, so the title is the only
        # handle the model can name and therefore the only one that reaches
        # this guard. `evaluate` sees the raw tool_use the model emitted;
        # `note_update`'s own tool closure holds the id→head map the
        # compare-and-swap needs, and refuses separately when the title
        # resolved to a note this turn never read. Two keys for one fact, on
        # purpose: this one is what the MODEL is told, that one is what the
        # WRITE is bound to, and they disagree exactly when a title changed
        # hands mid-turn — which is the case that must fail.
        self._notes_read: set[str] = set()

    def _veto(self, rule: str, message: str) -> str:
        self._metrics.inc("nora_guard_vetoes_total", rule=rule)
        log.warning("guard veto %s: %s", rule, message)
        return message

    @property
    def counts(self) -> dict[str, int]:
        """Calls that COMPLETED this tag, per tool. The ledger's own dict, read
        by R-45's nudge here and by the guard test files."""
        return self._ledger.counts

    def record(self, tool_name: str) -> None:
        self._ledger.record(tool_name)

    def reserve(self, tool_name: str, *, ref: str = "") -> None:
        """Mark a call ADMITTED BUT NOT YET FINISHED, so it counts against the
        ceiling while it runs — `nora.agent.call_ledger` holds the mechanism
        and the reasoning."""
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

        # R-3 — scope, held BY CONSTRUCTION rather than by veto (DEC-21) and,
        # since DEC-24, not even expressible: no tool declares a conversation
        # parameter, so `load_transcript` reads the closure value (Task 1's
        # cross-group leak fix) and `send_reply` calls `deliverer.deliver(text)`
        # against a target its `Deliverer` bound at construction. Vetoing on a
        # value that changes nothing protected nothing while making a weak model
        # unable to speak at all — on the first real tag the model echoed a wrong
        # group id three times, R-3 cancelled each, and the turn ended
        # `no_send_call`.
        #
        # This is deliberately NOT dead code, and that distinction is load-bearing
        # (R-17: a counter that can never increment is indistinguishable from a
        # deleted one). The hook fires on the RAW tool_use the model emitted,
        # before Strands validates it — and validation is a pydantic model built
        # from the callable's signature, which IGNORES properties it does not
        # declare (`FunctionToolMetadata.validate_input`, verified against
        # 1.50.2). So a model that keeps naming a conversation still reaches
        # here, its argument is still counted, and it is still dropped before the
        # tool body runs. That is the one observation DEC-24 must not lose: "the
        # model is trying to address a conversation" is exactly the behaviour
        # that killed the first real tag, and after DEC-24 the group id is not in
        # the prompt for it to echo, so a value appearing here is invented.
        #
        # The check is `is not None`, not `!=`: absence is now the NORMAL case
        # for every well-formed call, so comparing a missing key against the
        # bound id would peg this counter at one-per-tool-call and make it
        # useless in the other direction. Applied to every tool, not just the
        # two scoped ones, because the argument is out-of-schema everywhere now.
        target = (tool_input or {}).get("conversation_id")
        if target is not None and target != self._conversation_id:
            log.warning(
                "tool scope mismatch on %s: model supplied conversation_id=%r (not a "
                "parameter of any tool); the bound %r is used regardless",
                tool_name, target, self._conversation_id,
            )
            self._metrics.inc("nora_tool_scope_violations_total", tool=tool_name)

        # R-7's per-tool budget, and — for `send_reply` — **the message cap**.
        # THIS CHECK RUNS BEFORE R-2's latch below, and that ordering is half of
        # DEC-32. `NATIVE_TOOLS["send_reply"].budget` is the cap (1 → 3 activated
        # multi-message, DEC-42; → 6 for the burst, DEC-95), so the (cap+1)th
        # reply is refused HERE and never reaches `Deliverer.exhausted`.
        # Relaxing R-2 alone — which is what
        # `design/proposed/16-multi-message-replies.md` reads like — would leave
        # multi-message silently dead: the model would be told "you already sent
        # your reply" by a budget nobody had edited. Raising the number in
        # `nora.agent.tools.NATIVE_TOOLS` is therefore not optional bookkeeping,
        # and the latch below is what refuses the second message once it is
        # raised. Both halves are pinned in `tests/test_guards_replyguard.py`,
        # under "the message cap lives in the budget, not in R-2's latch".
        # Finished PLUS admitted-and-still-running. The second term is what
        # makes the ceiling hold inside one model turn's parallel batch — see
        # `nora.agent.call_ledger`.
        if self._ledger.at_ceiling(tool_name):
            if tool_name == "send_reply":
                # Labelled R-2 although the BUDGET is what refused it: the
                # model-facing sentence is the same either way, and the rule an
                # operator cares about is "one reply per tag". Anyone changing
                # the cap must read the two branches together — this counter
                # cannot tell them apart.
                #
                # DEC-33 considered splitting them and DECIDED NOT TO, for now.
                # `rule` values are as append-only as the metric names (a new
                # value silently splits every existing `rule="R-2"` query), and
                # the branch a new value would name — the latch below — is
                # UNREACHABLE while the budget is 1. Adding it today buys a
                # series that cannot increment, which R-17 says is
                # indistinguishable from one that was deleted. The moment the
                # cap is raised, both branches become live at once and both
                # have to be revised together; that is the edit where the
                # second label is worth its cardinality, and DEC-32 (5) is
                # already the note that forces the two to be read together.
                return self._veto(
                    "R-2", "You already sent your reply. Do not call send_reply "
                           "again. If you still need something, use one of the "
                           "other tools; otherwise finish your turn.",
                )
            # The number the model is TOLD is finished plus in-flight, not
            # `counts` alone. Vetoing the fifth call of a parallel batch while
            # saying "you already called it 0 time(s)" is a sentence that
            # contradicts itself, and the model reads it as a reason to retry.
            spent = self._ledger.spent(tool_name)
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
            # R-2's latch, the authoritative bounded-delivery mechanism (spec
            # S4), asked of the DELIVERER rather than recomputed here. It is
            # still unreachable for a well-behaved turn — the budget check
            # above refuses the (cap+1)th call first, from the same number —
            # and it is the backstop for the one case where the two counters
            # legitimately disagree: `record` skips a call that raised or was
            # cancelled, while `Deliverer` has already spent the message if it
            # got as far as the audit write.
            #
            # `exhausted`, not `attempted` (DEC-42). Asking `attempted` is what
            # would leave multi-message dead after the budget was raised: the
            # second `send_reply` would be refused by the latch instead of the
            # budget, which is the same silence wearing the other rule's name.
            if self._deliverer is not None and self._deliverer.exhausted:
                return self._veto(
                    "R-2", "You already sent your reply. Do not call send_reply "
                           "again. If you still need something, use one of the "
                           "other tools; otherwise finish your turn.",
                )

        # R-55 — the read-before-write latch (note-tier spec §6.2). A refusal,
        # not a nudge, and it sits HERE beside R-2's latch rather than in the
        # budget check above because it is the same KIND of rule: a per-turn
        # fact about what has already happened, asked of state this guard owns.
        # The budget check answers "may she call this again"; this answers "was
        # she ever entitled to call it at all".
        #
        # WHY A VETO AND NOT ADVICE. The injected index carries title and
        # description only — enough to know a note exists, nowhere near enough
        # to revise one. `note_update` REPLACES the note's rendered body
        # (§6.1's head), so a revision written without the body in front of her
        # destroys the arrangement that is the whole value of the tier, and
        # does it silently: the prior revision survives in the table, but
        # nothing in the room ever learns the note stopped saying what it said.
        #
        # NOT ONE-SHOT, unlike R-45 above. R-45's argument for firing once is
        # that a second refusal would not change the model's answer and would
        # only spend a budget of 1 on refusals. Neither half holds here: the
        # remedy IS a different call (`note_read`, its own budget), the veto
        # names it, and a model that ignores the veto and re-fires must be
        # refused again — a one-shot latch would let the SECOND attempt through
        # unread, which is precisely the write this rule exists to stop.
        #
        # Costs no budget: `_after_tool` never `record`s a cancelled call
        # (DEC-33), so read-then-revise is still two calls against a budget of
        # two.
        if tool_name == "note_update":
            title = (tool_input or {}).get("title") or ""
            if note_handle(title) not in self._notes_read:
                return self._veto(
                    "R-55",
                    f"You have not read {title!r} this turn. Call note_read on it "
                    "first, then revise it — a note's value is how it is "
                    "arranged, and you cannot rewrite a body you have not seen.",
                )

        # R-45 (DEC-101): a nudge, not a refusal — `make_artifact` itself
        # still runs if the model ignores this once. Root cause: the model
        # had ALREADY drafted a full prose answer from `place_info`/`route`
        # results earlier this turn and then reused that draft wholesale as
        # `task`, leaving `context` empty — `make_artifact`'s own docstring
        # already says "do not re-derive or summarise them into prose
        # first", but advisory prose alone did not hold against a model that
        # had already written the words once. One-shot (`_context_nudge_
        # given`) because vetoing this repeatedly would not change the
        # model's answer, only spend the turn's only `make_artifact` call
        # (budget 1) on refusals — this veto is exempt from that spend since
        # `_after_tool` never `record()`s a cancelled call (DEC-33's own
        # mechanism, reused here rather than re-invented).
        if (
            tool_name == "make_artifact" and not self._context_nudge_given
            and not (tool_input or {}).get("context")
            and (
                self.counts.get("place_info", 0) + self.counts.get("route", 0) > 0
                or self._recall_found_something
            )
        ):
            self._context_nudge_given = True
            gathered = self.counts.get("place_info", 0) + self.counts.get("route", 0)
            source = (
                f"place_info/route {gathered} time(s)" if gathered > 0
                else "recall_lookups"
            )
            return self._veto(
                "R-45",
                f"You already called {source} this turn and have real data "
                "from it. Pass it as `context` — the small dicts you already "
                "have — instead of typing it into `task` as prose.",
            )
        return None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)
        registry.add_callback(BeforeModelCallEvent, self._before_model)
        registry.add_callback(AfterModelCallEvent, self._after_model)

    def _terminal_budget_spent(self) -> bool:
        """True once NO terminal verb can be called again this turn.

        The turn-ending condition, generalised from DEC-32's "a terminal verb
        has run" by DEC-42. With `send_reply`'s budget at 1 the two are the
        same predicate — running it spends it — which is what makes
        multi-message ship INERT: raise the budget and this stops firing after
        the first message without any other edit.

        `self._terminal` is checked for emptiness first because `all(())` is
        True: a guard told nothing is terminal would otherwise end the turn
        before the first model call, which is the opposite of the ~25s runaway
        DEC-21 fixed but just as broken.
        """
        return bool(self._terminal) and all(
            self.counts.get(name, 0) >= self._budgets.get(name, 0) for name in self._terminal
        )

    def _before_model(self, event: BeforeModelCallEvent) -> None:
        """Relax `tool_choice` once she has spoken; end the turn at the cap.

        **Two things, in that order, and the order is the mechanism** (DEC-42,
        `design/proposed/16-multi-message-replies.md` §2). The counter half
        first, because it is what the docstring below is about.

        `tool_choice="required"` (see `nora.agent.factory`) is the only reason
        this model calls `send_reply` at all — but it applies to EVERY turn, so
        once the reply has gone the model is still forced to call something and
        the loop cannot terminate. It ran until the invocation timeout killed
        it, which cost ~25s per tag AND resolved the indicator wrongly: the
        timeout path exits before `delivered` is set, so a turn that had
        actually succeeded showed the group ⚠️. One cause, two symptoms, both
        observed live.

        Cancelling here is the narrow fix. The R-7 budget already refuses a
        `send_reply` past the cap (it is checked before R-2's latch), so
        nothing after the last allowed call can change what the group sees —
        the remaining turns were pure cost.

        **This asked `self._deliverer.delivered` until DEC-32**, which made
        "the turn is over" a property of ONE tool's side effect rather than of
        the tools. `design/proposed/15-gif-replies.md` §1 needs a second
        terminal verb and proposed defining the class here; a Honcho
        investigation on 2026-08-06 raised a third candidate (a distinct feed
        step on the tag path). Both are now a `terminal=True` in `NATIVE_TOOLS`,
        with no edit to this file.

        The one behavioural difference DEC-32 accepted is a `send_reply` the
        `Deliverer` REFUSED (R-6 canary, R-18 egress, an audit-write or send
        failure): `delivered` stayed False, so the loop used to keep running,
        and DEC-32 made it stop once the terminal budget was spent. Under the cap
        of 3 (DEC-42) that difference shrinks — a refused reply is RECORDED, so it
        spends one of the three rather than ending the turn; the turn ends only
        when all three are spent, and the (cap+1)th is already refused by the
        budget.

        **The RELAXATION half (DEC-42).** Once a terminal verb has run she has
        spoken, and from that model call onwards `tool_choice` becomes `auto`
        — so she may stop by declining to call anything, which under `required`
        she structurally cannot (§2.1's C: `required` after a reply never
        stops, 6/6). It is done HERE, and only here, because
        `BeforeModelCallEvent._can_write` is `return name == "cancel"`: the
        event refuses attribute ASSIGNMENT on itself, which an earlier draft of
        design/16 §4 misread as the model being unreachable. `HookEvent.agent`
        is a public field and `OpenAIModel.update_config` exists, so
        `event.agent.model` is reachable and writable — pinned against the real
        SDK by `tests/test_strands_api_contract.py`, because the whole
        mechanism rests on it.

        Reaching the model would be UNSAFE without step 5a. It is per tag now
        (`nora.agent.factory.ModelSource`); shared, this line would have
        relaxed a concurrent tag's FIRST call, which design/16 §2.1 measured as
        sending nothing 5/6. `tests/test_model_per_tag.py` is that probe.

        Ordered cancel-first so the relaxation is never applied to a model call
        that is not going to happen.
        """
        if self._turn_is_over:
            event.cancel = "Reply already sent — the turn is finished."
            # DEC-93: this turn's last "assistant text" is now MINE, not hers.
            # Strands synthesises `message={"content":[{"text": cancel_text}]}`
            # with `stop_reason="end_turn"` for a cancelled model call
            # (`strands/event_loop/event_loop.py`), which is indistinguishable
            # from the model having written prose — and this string trivially
            # passes R-6 and R-18, so nothing downstream would stop it reaching
            # the group. Recorded here, at the one place that knows.
            self.cancelled_model_call = True
            return
        model = getattr(getattr(event, "agent", None), "model", None)
        # A real call from here on: whatever this pass does, it is not the
        # cancel branch above. Set BEFORE the `_spoke` return so a relaxed call
        # is counted like any other.
        self._model_call_in_flight = True
        # Which call this IS, counting from 1 — completed calls plus this one.
        # A retry pass re-enters here with `_model_calls` unchanged, so it
        # re-computes the SAME index and re-applies the same pin (A11).
        call_index = self._model_calls + 1
        if self._spoke:
            tool_choice.relax(model)
            return
        # Not spoken yet. Phase 2 routing: force the routed tool on the FIRST
        # model call, then RELEASE the pin on the next so the loop is free to
        # reach `send_reply` (still `required`, never `auto` — she has not
        # spoken, and `auto` before speaking sends prose, R-5's silent shape).
        # Ordered after the `_spoke` return so a forced pin never survives into
        # the post-reply relaxation.
        if self._forced_first_tool is not None:
            if call_index == 1:
                tool_choice.force(model, self._forced_first_tool)
            elif call_index == 2:
                tool_choice.require(model)

    def _after_model(self, event: AfterModelCallEvent) -> None:
        """Count the model call that just COMPLETED (A11, audit F12).

        `exception is None` is the whole discriminator, and it is exact: the
        shipped retry strategy (`strands/event_loop/_retry.py`) sets
        `event.retry` only after `is_retryable(event.exception)`, so a pass that
        raised is always a pass that produced no answer, and the next
        `BeforeModelCallEvent` is a re-attempt of the SAME call rather than the
        next one.

        `event.retry` is deliberately NOT consulted. `AfterModelCallEvent` uses
        reverse callback ordering, so a strategy registered after this guard has
        not necessarily run yet and the flag is not final here. The one case
        that reads differently — a hook discarding a SUCCESSFUL response to
        re-ask — is counted as a call, which is honest: the model was called and
        it answered.

        Never raises, for `_before_tool`'s reason: a counter is not worth
        killing a turn over.
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
            # releases it, so the rest of a parallel batch is measured against
            # this call too. The mutation lives HERE rather than in `evaluate`
            # on purpose: `evaluate` is pure and SDK-free so every rule stays
            # testable without strands, and this hook is the thin translation
            # layer that owns lifecycle.
            self.reserve(name, ref=str(tool_use.get("toolUseId") or ""))
        except Exception:
            log.exception("ReplyGuard._before_tool raised; allowing the call")

    def _after_tool(self, event) -> None:
        """Two early returns, and TWO kinds of per-call state with opposite
        correct placements. Getting the pair the wrong way round is how the
        2026-08-21 leak below happened, so the distinction is stated here
        rather than left to be re-derived:

        - **«this call FINISHED»** — the in-flight reservation. True on every
          path, including the ones that raised or were vetoed, so it belongs
          ABOVE both returns.
        - **«this call SUCCEEDED»** — `record`, the terminal latch, and
          `_recall_found_something`. True on ONE path, so each belongs BELOW
          both returns, and `_recall_found_something` additionally reads the
          result's own status.

        Only the second kind is visible through `counts`, which is why a
        mistake in the first kind survives a suite that looks thorough.
        """
        try:
            # The RESERVATION comes back first, on every path, because it is
            # not budget — it is "this call is still running". Releasing it
            # after the early return below is a LEAK that was live until
            # 2026-08-21: a tool call that RAISED held its reservation for the
            # rest of the tag, so `spent` stayed one too high and R-7 refused a
            # later call the model was entitled to. Invisible, because `counts`
            # — the thing every existing test asserts on — was correct the
            # whole time; only the in-flight half was wrong, and nothing read
            # it directly. Found while extracting `CallLedger` for `DreamGuard`,
            # by someone reading a comment that already claimed "the
            # reservation goes back on EVERY path" and checking whether it did.
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            # Keyed on the SDK's `toolUseId`, so this drops THIS call's
            # reservation and not whichever one happens to be outstanding. A
            # vetoed call reaches this hook too and never reserved: a
            # name-keyed release let it decrement a SIBLING's reservation in
            # the same parallel batch, reopening the ceiling by one and with it
            # the 2026-08-13 trace `call_ledger` exists to prevent (2026-08-21
            # review, measured on both guards).
            self.release(name, ref=str(tool_use.get("toolUseId") or ""))
            if getattr(event, "exception", None) is not None:
                return                       # a failed call does not consume budget
            # A CANCELLED CALL DID NOT RUN, so it consumes no budget and cannot
            # have ended the turn. Strands fires this hook for a call
            # `_before_tool` vetoed too — `cancel_tool` produces an error
            # `ToolResult` and `exception=None`
            # (`strands.tools.executors._executor`, verified against 1.50.2) —
            # so `cancel_message` is the ONLY field that separates the two.
            # `tests/test_strands_api_contract.py` asserts it against the real
            # SDK, because that is load-bearing rather than incidental.
            #
            # DEC-32 applied this to the terminal latch below and NOT to
            # `record`, which left a live bug (DEC-33): `send_reply`'s budget is
            # 1, so a `send_reply("")` refused by R-4 spent the tag's only
            # reply, and the model's own correction was then refused by R-7's
            # budget check with "you already sent your reply". One recoverable
            # mistake, and the RECOVERY was what got blocked — silently, since
            # under R-5 the group just hears nothing.
            if getattr(event, "cancel_message", None) is not None:
                return
            self.record(name)
            if name == "recall_lookups":
                self._recall_found_something = (
                    _unwrap_tool_status(getattr(event, "result", None)) == "ok"
                )
            # R-55's latch (note-tier spec §6.2). **This is below BOTH early
            # returns on purpose, and that placement is the whole rule.**
            # `release()` above belongs where it is because it means «this call
            # FINISHED», which is true on every path — vetoed, raised, done —
            # and the bug 4d44d0d fixed was exactly that sentence being written
            # below a return that made it false on one path. The latch means
            # «this call SUCCEEDED», which is true on one path only, so the two
            # take OPPOSITE positions in the same method and neither placement
            # generalises to the other.
            #
            # Three ways a read can fail to license a revision, and all three
            # are the same failure wearing different clothes — a call that
            # produced no body:
            #   - it RAISED (the first early return);
            #   - the guard CANCELLED it (the second);
            #   - it ran and answered `nothing_found` or `refused` — the note
            #     is not there, or the title matched two and the tool refused
            #     to guess. Strands calls both of those a successful tool call,
            #     so only `_unwrap_tool_status` can tell them from a real read,
            #     and reading a note that ISN'T THERE licensing a revision is
            #     the single case this latch most exists to catch.
            if (
                name == "note_read"
                and _unwrap_tool_status(getattr(event, "result", None)) == "ok"
            ):
                title = (
                    (getattr(event, "tool_use", None) or {}).get("input") or {}
                ).get("title") or ""
                if title:
                    self._notes_read.add(note_handle(title))
            if name in self._terminal:
                # She has spoken: `_before_model` relaxes `tool_choice` from
                # here on, and ends the turn once every terminal verb's budget
                # is spent. Two flags rather than one because they answer two
                # questions — "may she stop?" and "must she?" — and at a
                # `send_reply` budget of 1 they happen to flip together, which
                # is precisely why collapsing them would look correct today and
                # cap her at one message forever.
                self._spoke = True
                self._turn_is_over = self.counts.get(name, 0) >= self._budgets.get(name, 0)
        except Exception:
            log.exception("ReplyGuard._after_tool raised; ignoring")
