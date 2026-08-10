"""Guardrail enforcement (spec B). ReplyGuard is constructed fresh per tag, so counters
are per-invocation by construction rather than by bookkeeping.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping
from typing import Any, Protocol

from strands.hooks import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from ..doctrine.loader import Doctrine
from ..domain.models import ConversationTarget, DeliveryStatus, NormalizedMessage
from ..domain.ports import SendResult
from ..obs.metrics import Metrics
from . import tool_choice
from .egress import LeakGuard, scan_reply, strip_markdown

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
        self._attempts = 0
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
    def exhausted(self) -> bool:
        """No message left. R-2's latch, generalised from one to `message_cap`.

        This is what `ReplyGuard` asks instead of `attempted`: with the cap at
        1 the two are the same predicate, and above 1 `attempted` would refuse
        the second message while claiming multi-message shipped.
        """
        return self._attempts >= self._message_cap

    async def deliver(self, text: str, *, source: str = "tool") -> bool:
        if self.exhausted:                                     # R-2
            log.warning(
                "delivery refused: %d message(s) already spent of %d (tag=%s)",
                self._attempts, self._message_cap, self._tag_id,
            )
            self._metrics.inc("nora_guard_vetoes_total", rule="R-2")
            return False

        # DEC-82: strip Markdown BEFORE every remaining guard, so each one
        # measures the bytes that actually LEAVE rather than a draft that no
        # longer exists. The concrete case is R-6: a doctrine span broken up by
        # emphasis (`**You never post an error message…**`) is invisible to the
        # leak guard as drafted and plainly present once stripped, so cleaning
        # afterwards would let exactly that through.
        #
        # It does NOT rescue R-4 — the regex requires non-whitespace inside the
        # delimiters, so no input that is non-empty before the strip is empty
        # after it. Said explicitly because the reverse is the obvious guess.
        #
        # It also happens before `row` is built, so the audit row, `sent_texts`,
        # and the Honcho feed all carry the ONE canonical form — the words the
        # group actually saw.
        text = strip_markdown(text)

        if not text or not text.strip():                       # R-4
            log.warning("delivery refused: empty reply (tag=%s)", self._tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="empty_reply",
                platform=self._target.platform, audience=self._audience,
            )
            return False

        reason = self._leak.check(text)                        # R-6
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
        kind = scan_reply(text)
        if kind:
            log.error("egress_blocked: %s (tag=%s)", kind, self._tag_id)
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            self._metrics.inc(
                "nora_silent_failures_total", reason="egress_block",
                platform=self._target.platform, audience=self._audience,
            )
            return False

        self._attempts += 1
        row = NormalizedMessage(
            platform=self._target.platform,
            conversation_id=self._target.conversation_id,
            conversation_type=self._target.conversation_type,
            sender_id=self._nora_sender_id,
            sender_name=None,
            timestamp=int(time.time() * 1000),
            body=text,
            raw={"tag_id": self._tag_id, "source": source,
                 "doctrine_sha256": self._doctrine_sha256},
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
            result = await self._adapter.send(target=self._target, text=text)
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
            if source != "fallback":
                self._sent_texts.append(text)
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
        self.counts: dict[str, int] = dict.fromkeys(self._budgets, 0)
        # Set by `_after_tool` when a terminal verb has actually RUN — not
        # when one was merely attempted. See `_before_model`.
        self._spoke = False
        self._turn_is_over = False
        # Phase 2 routing (`nora.agent.intent_router`): a tool to FORCE via
        # `tool_choice` on the turn's FIRST model call, or None to abstain. The
        # counter lets `_before_model` force on call 1 and RELEASE the pin on
        # call 2 — a pin left in place would make her call it every pass and
        # never `send_reply`.
        self._forced_first_tool = forced_first_tool
        self._model_calls = 0

    def _veto(self, rule: str, message: str) -> str:
        self._metrics.inc("nora_guard_vetoes_total", rule=rule)
        log.warning("guard veto %s: %s", rule, message)
        return message

    def record(self, tool_name: str) -> None:
        if tool_name in self.counts:
            self.counts[tool_name] += 1

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
        # DEC-32. `NATIVE_TOOL_BUDGETS["send_reply"]` is 3 (DEC-42 activated), so
        # the FOURTH reply is refused HERE and never reaches `Deliverer.exhausted`.
        # Relaxing R-2 alone — which is what
        # `design/proposed/16-multi-message-replies.md` reads like — would leave
        # multi-message silently dead: the model would be told "you already sent
        # your reply" by a budget nobody had edited. Raising the number in
        # `nora.agent.tools.NATIVE_TOOLS` is therefore not optional bookkeeping,
        # and the latch below is what refuses the second message once it is
        # raised. Both halves are pinned in `tests/test_guards_replyguard.py`,
        # under "the message cap lives in the budget, not in R-2's latch".
        if self.counts[tool_name] >= self._budgets[tool_name]:
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
            return self._veto(
                "R-7", f"You already called {tool_name} "
                       f"{self.counts[tool_name]} time(s) and have that context. "
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
        return None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)
        registry.add_callback(BeforeModelCallEvent, self._before_model)

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
            return
        model = getattr(getattr(event, "agent", None), "model", None)
        self._model_calls += 1
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
            if self._model_calls == 1:
                tool_choice.force(model, self._forced_first_tool)
            elif self._model_calls == 2:
                tool_choice.require(model)

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
        except Exception:
            log.exception("ReplyGuard._before_tool raised; allowing the call")

    def _after_tool(self, event) -> None:
        try:
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
            name = (getattr(event, "tool_use", None) or {}).get("name", "")
            self.record(name)
            if name in self._terminal:
                # She has spoken: `_before_model` relaxes `tool_choice` from
                # here on, and ends the turn once every terminal verb's budget
                # is spent. Two flags rather than one because they answer two
                # questions — "may she stop?" and "must she?" — and at a
                # `send_reply` budget of 1 they happen to flip together, which
                # is precisely why collapsing them would look correct today and
                # cap her at one message forever.
                self._spoke = True
                self._turn_is_over = self._terminal_budget_spent()
        except Exception:
            log.exception("ReplyGuard._after_tool raised; ignoring")
