"""Step 3 of the correction loop — what notices that somebody just contradicted
her (`design/dreamer-agent-loop/31-recognition.md`).

`RECOGNISE` occurred exactly once in thirty-one design nodes, as a line in a
diagram, and by elimination the recogniser was the reply-path model — which
rests the primary trust loop on `02-instrument-blindness.md`'s named pathology,
*she recognises and does not act*. This module is the mechanism.

**Two halves with different powers, and the cut is what must be RE-DERIVABLE.**

- **TARGETING is deterministic.** It decides which rows are touched, and a model
  choosing rows would be choosing them by IDENTIFIER — the exact shape
  `dream_screen` drops silently. It produces a bounded set the model addresses
  by ordinal handle, never by id.
- **The DISPUTE PREDICATE is semantic**, and pretending otherwise is how this
  step stayed undesigned. A cue list alone cannot separate *"no, big walk is a
  video game"* from *"no I can't make the big walk"* — the difference is that
  the first asserts a competing answer, and that is a judgement about meaning.

**Channel B is a DEDICATED CALL, not a tool on the reply path**, and three
readings of the code decided that rather than taste. `build_tool_specs` returns
a flat, unconditional tuple — there is no per-turn grant to reuse, so "it costs
nothing on turns without targets" would have been false. `08-state.md` §12 says
«no write tool, ever», and a verb that sets a column is one. And the transport
is measured not to honour tool choice reliably, so a recogniser whose only
channel is a tool call inherits that. A single-purpose call has none of those
problems and reaches this turn's own prefill.

**RULE-F is the fallback when that call is UNAVAILABLE**, not the primary. In
the design's first draft it ran always and was called unambiguous; the
adversarial review broke it with the draft's own example — *"no I can't make the
big walk"*, quote-replying her, satisfies quote, cue and surface and disputes
nothing. Its precision cost is survivable only because a dispute MARKS a claim
rather than muting it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nora.agent.dreamer import parse_envelope
from nora.domain.facts import RenderedClaim, mentions, normalise_name
from nora.domain.models import NormalizedMessage

log = logging.getLogger(__name__)

#: Ordinal handles, one alphabet — there is only one thing being named here, so
#: the two-alphabet split pass 6 needs would be ceremony. Still handles and
#: never ids: `dream_screen` drops any envelope item carrying an identifier, and
#: a recogniser that echoed a `fact.id` would be dropped whole while its own
#: counters looked healthy.
_HANDLES = "123456789"

#: 31 §5.2's negation cues, and they are DELIBERATELY NARROW. RULE-F only runs
#: when the classifier could not, so its job is the unambiguous case rather
#: than coverage.
#:
#: The refusal and surprise families are EXCLUDED on purpose: 唔係呀嘛 and 唔係呀
#: are disbelief, 冇啦 and 唔得 are refusals, and "no way" / "nah bro" / "not
#: gonna lie" negate nothing about a stored claim. A cue list that swept them in
#: would fire on jokes, which is what makes this a fallback and not the primary.
_NEGATION_CUES: tuple[str, ...] = (
    "no,", "no it", "no that", "not ", "isn't", "is not", "wrong",
    "唔係",          # 唔係 — "is not"
    "唔將",          # 唔啱 variants
    "錯",                # 錯 — "wrong"
    "不是",          # 不是
)

#: 31 §4.2's floor for boundary-free CJK containment. A two-character surface is
#: a substring of longer compounds constantly (大浪 inside 大浪灣), and marking a
#: row disputed on that costs a claim — so recognition runs stricter than the
#: retrieval slice, which is why the matcher takes this as a parameter.
_MIN_CJK_CHARS = 3


@dataclass(frozen=True)
class _Target:
    """One claim in scope, with the handle the model will name it by."""

    handle: str
    claim: RenderedClaim


class _DisputeClassifier(Protocol):
    """One completion. Structural, so this module names no provider."""

    async def complete(self, *, prompt: str) -> str: ...


class _FactDisputeStore(Protocol):
    async def mark_disputed(
        self, *, workspace: str, fact_id: str, person_id: str | None,
        reason: str | None, tag_id: str, now_ms: int,
    ) -> str: ...


class _LexemeDisputeStore(Protocol):
    async def mark_disputed(
        self, *, scope_id: str, surface: str, person_id: str | None,
        reason: str | None, tag_id: str, now_ms: int,
    ) -> int: ...


class _Ledger(Protocol):
    async def in_window(self, *, conversation_id: str, since_ms: int) -> Any: ...


class Recogniser:
    """Runs on the tag path, BEFORE context assembly, so a dispute it raises
    reaches this turn's own prefill."""

    #: The objective this reads, and the envelope key its parser unwraps —
    #: DECLARED, in the same two names every `DreamAdaptor` declares them
    #: under, because that pair is what binds a prompt to a parser and
    #: `test_objective_parser_parity` derives its coverage from finding it.
    #: They were a literal in `composition` and a literal in `_parse_disputes`,
    #: which is two hand-copies of one contract and the exact drift this
    #: recogniser's own objective is checked against. Recognition is not a
    #: Dreamer adaptor — 31 §2 says why it cannot be — but it reads an
    #: objective and parses an envelope, and that is the whole of what the
    #: parity gate needs to see.
    objective = "fact-dispute"
    envelope_key = "disputes"

    def __init__(
        self,
        *,
        ledger: _Ledger,
        facts: _FactDisputeStore,
        lexemes: _LexemeDisputeStore | None = None,
        classifier: _DisputeClassifier | None = None,
        instruction: str = "",
        classify_timeout_seconds: float = 8.0,
        metrics: Any = None,
        window_hours: int = 24,
        max_targets: int = 4,
        adjudicator_alive: bool = True,
    ) -> None:
        self._ledger = ledger
        self._facts = facts
        self._lexemes = lexemes
        self._classifier = classifier
        self._instruction = instruction
        self._classify_timeout = classify_timeout_seconds
        self._metrics = metrics
        self._window_ms = window_hours * 60 * 60 * 1000
        self._max_targets = max_targets
        self._adjudicator_alive = adjudicator_alive

    async def recognise(
        self, *, msg: NormalizedMessage, tag_id: str, person_id: str | None,
        quotes_nora: bool = False,
    ) -> int:
        """Mark whatever this message contradicts. Returns rows marked.

        **Never raises.** It runs before the reply is assembled, and a
        recogniser fault must not cost a turn — the correction is lost, which
        is the status quo, rather than the answer.
        """
        try:
            return await self._recognise(
                msg=msg, tag_id=tag_id, person_id=person_id,
                quotes_nora=quotes_nora,
            )
        except Exception:
            log.exception("recognition failed for tag %s", tag_id)
            self._inc("nora_dispute_targets_total", status="unavailable",
                      scope="direct", via="none")
            return 0

    async def _recognise(
        self, *, msg: NormalizedMessage, tag_id: str, person_id: str | None,
        quotes_nora: bool,
    ) -> int:
        now_ms = int(time.time() * 1000)
        read = await self._ledger.in_window(
            conversation_id=msg.conversation_id,
            since_ms=now_ms - self._window_ms,
        )
        if read.status == "unavailable":
            # R-27: an unreadable ledger is «I could not check what she
            # rendered», never «she rendered nothing». Reporting the latter
            # would let recognition rule out a dispute it never looked for.
            self._inc("nora_dispute_targets_total", status="unavailable",
                      scope="direct", via="none")
            return 0

        targets, via = self._target(msg, read.claims, quotes_nora)
        self._inc(
            "nora_dispute_targets_total", value=float(len(targets)),
            status="ok" if targets else "nothing_found",
            scope="direct", via=via,
        )
        if not targets:
            return 0

        # 31 §7.2: clearing a dispute happens only inside adjudication, and
        # adjudication is a Dreamer adaptor. Without a live one, marking would
        # accumulate and never resolve — so recognition degrades to proposing
        # rather than marking, which is the mechanism latent scope already has.
        if not self._adjudicator_alive:
            self._inc("nora_dispute_degraded_total")
            return 0

        disputes, channel = await self._decide(msg, targets, quotes_nora)
        if not disputes:
            self._inc("nora_dispute_unacted_total", cause=channel)
            return 0

        marked = 0
        for handle, reason in disputes:
            target = next((t for t in targets if t.handle == handle), None)
            if target is None:
                self._inc("nora_dispute_handle_refused_total")
                continue
            marked += await self._mark(
                target, msg=msg, person_id=person_id, reason=reason,
                tag_id=tag_id, now_ms=now_ms,
                channel="model" if channel == "declined" else "deterministic",
            )
        return marked

    # -- CHANNEL A: targeting ------------------------------------------------

    def _target(
        self, msg: NormalizedMessage, claims: Sequence[RenderedClaim],
        quotes_nora: bool,
    ) -> tuple[tuple[_Target, ...], str]:
        """Which claims could this message be about. Deterministic, and it
        decides the blast radius.

        Two sub-channels, unioned. **T1** is the quote link: a reply to one of
        Nora's own messages inside the window targets what that window
        rendered, because within one conversation the same rows render on
        consecutive turns and an exact turn join buys almost nothing over it.

        **`quotes_nora` means the quoted author IS her**, and the name says so
        because the previous one — `parent_is_nora` — was passed
        `msg.quote is not None`. That is «this is a reply to ANYBODY», so a
        member quoting another member with no surface of hers in the message
        put the entire 24-hour window in front of the classifier under
        `via="quote"`. Targeting is the half that must be right precisely
        because it decides the blast radius, and this was the widest possible
        reading of it. `ReplyToNoraPolicy` already matches `Quote.author_id`
        against her own identifier set to decide a reply IS a tag; this is the
        same predicate and it now comes from the same field.
        **T2** is normalised containment of the message against each claim's
        surface.

        **Capped.** Targeting decides the blast radius and the design's first
        draft capped nothing: one message naming three surfaces would mark
        three entity groups deterministically.
        """
        body = normalise_name(msg.body or "")
        matched = [
            c for c in claims
            if mentions(body, normalise_name(c.surface),
                        min_cjk_chars=_MIN_CJK_CHARS)
        ]
        via = "surface"
        if not matched and quotes_nora:
            # A quote-reply to her with no surface in it — 「唔係呀」 replying to
            # a turn that rendered something. The quote is what puts those rows
            # in scope; the classifier decides whether it is a contradiction.
            matched = list(claims)
            via = "quote"
        elif matched and quotes_nora:
            via = "quote+surface"
        return (
            tuple(
                _Target(handle=_HANDLES[i], claim=c)
                for i, c in enumerate(matched[: min(self._max_targets, len(_HANDLES))])
            ),
            via,
        )

    # -- CHANNEL B: the dispute predicate ------------------------------------

    async def _decide(
        self, msg: NormalizedMessage, targets: tuple[_Target, ...],
        quotes_nora: bool,
    ) -> tuple[tuple[tuple[str, str], ...], str]:
        """`(disputes, cause)` — the handles contradicted, and why nothing was
        returned if nothing was.

        `cause` separates *the classifier ran and declined* from *it never
        ran*, which is the label that makes `nora_dispute_unacted_total`
        readable: without it the series reads identically for a working
        recogniser on a quiet room and a broken one, and CLAUDE.md's rule is
        that an instrument must be checked for whether it can measure the
        thing.
        """
        if self._classifier is None or not self._instruction:
            self._inc("nora_dispute_classify_total", status="unavailable")
            return self._rule_f(msg, targets, quotes_nora), "not_reached"
        try:
            # WRAPPED as well as configured, which is `StandingConfig`'s own
            # belt-and-braces on the same slot: a client's own deadline is a
            # promise about the request, and this is the bound on the HOLD. A
            # transport that retries internally, or one that honours the
            # timeout for the connect and not the read, would otherwise keep a
            # live conversation waiting past a budget the gate says is
            # enforced.
            raw = await asyncio.wait_for(
                self._classifier.complete(prompt=self._prompt(msg, targets)),
                timeout=self._classify_timeout,
            )
        except Exception:
            # `asyncio.TimeoutError` IS `TimeoutError` and IS an `Exception`
            # on 3.11+, so one clause covers the deadline and the fault. They
            # are the same outcome here anyway: `unavailable`, RULE-F, and the
            # turn continues.
            log.exception("dispute classifier unavailable")
            self._inc("nora_dispute_classify_total", status="unavailable")
            return self._rule_f(msg, targets, quotes_nora), "not_reached"

        disputes = _parse_disputes(raw)
        if disputes is None:
            self._inc("nora_dispute_classify_total", status="unavailable")
            return self._rule_f(msg, targets, quotes_nora), "not_reached"
        self._inc(
            "nora_dispute_classify_total",
            status="ok" if disputes else "nothing_found",
        )
        return disputes, "declined"

    def _prompt(
        self, msg: NormalizedMessage, targets: tuple[_Target, ...],
    ) -> str:
        lines = [f"- [{t.handle}] {t.claim.surface}" for t in targets]
        return (
            f"{self._instruction}\n\n"
            f"The message:\n{msg.body or ''}\n\n"
            "Claims she holds about what it mentions:\n" + "\n".join(lines)
        )

    def _rule_f(
        self, msg: NormalizedMessage, targets: tuple[_Target, ...],
        quotes_nora: bool,
    ) -> tuple[tuple[str, str], ...]:
        """31 §5.2. Fires ONLY when the classifier could not.

        A quote-reply to one of Nora's own messages, carrying a negation cue,
        where the reply itself names the claim's surface. All three, and the
        surface must be in the REPLY rather than merely in the quoted message —
        otherwise a bare 「唔得」 would mark whatever that turn rendered.

        **`quotes_nora`, not `msg.quote is not None`** — the same conflation
        `_target` had. The docstring above said "one of Nora's own messages"
        and the code accepted a reply to anybody, so a member contradicting
        ANOTHER MEMBER while naming one of her surfaces marked her claim on a
        rule whose whole premise is that the correction was addressed to her.
        It fires only where the classifier could not, which is exactly where
        nothing else is checking.
        """
        if not quotes_nora:
            return ()
        body = (msg.body or "").casefold()
        if not any(cue in body for cue in _NEGATION_CUES):
            return ()
        normalised = normalise_name(msg.body or "")
        return tuple(
            (t.handle, "a reply contradicting what she said")
            for t in targets
            if mentions(normalised, normalise_name(t.claim.surface),
                        min_cjk_chars=_MIN_CJK_CHARS)
        )

    # -- the write -----------------------------------------------------------

    async def _mark(
        self, target: _Target, *, msg: NormalizedMessage,
        person_id: str | None, reason: str, tag_id: str, now_ms: int,
        channel: str,
    ) -> int:
        """Mark the claim and fan out to the other tier (31 §6).

        **Fan-out is by SURFACE, not by entity, and that is a degradation the
        design names.** It should be per entity across both tiers — but
        `fact.subject_id` has no entity pointer at all (only the OBJECT gained
        one, in migration 0039) and `room_lexeme.canonical_form` has no link to
        `fact_entity` either. So this matches the normalised surface, and the
        degradation is COUNTED rather than silent: a tier that fans out
        correctly and one that cannot see its siblings look identical from the
        outside, and the counter is the difference.
        """
        outcome = await self._facts.mark_disputed(
            workspace=target.claim.workspace or "",
            fact_id=target.claim.row_key,
            person_id=person_id, reason=reason, tag_id=tag_id, now_ms=now_ms,
        )
        self._inc("nora_dispute_marked_total", tier="fact", outcome=outcome)
        marked = 1 if outcome == "ok" else 0
        if outcome == "ok":
            self._inc("nora_dispute_recognised_total", tier="fact",
                      channel=channel)
            self._inc("nora_dispute_fanout_unresolved_total")

        if self._lexemes is not None:
            lex = await self._lexemes.mark_disputed(
                scope_id=msg.conversation_id, surface=target.claim.surface,
                person_id=person_id, reason=reason, tag_id=tag_id,
                now_ms=now_ms,
            )
            if lex:
                self._inc("nora_dispute_marked_total", tier="lexeme",
                          outcome="ok", value=float(lex))
                marked += lex
        return marked

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)


def _parse_disputes(raw: str) -> tuple[tuple[str, str], ...] | None:
    """`None` when the reply contains no envelope — which is `unavailable`,
    not `nothing_found`: a model that answered with only prose did not decide
    there was no dispute, it failed to answer.

    **A PREAMBLE is not that**, and this is the SEVENTH parser with the same
    contract — `parse_envelope` carries the other six and the measurement
    behind the change. `fact-dispute.md` tells the model the same «your entire
    reply is one JSON object» that `fact-adjudication.md` does, and the model
    narrates anyway.

    It matters more here than there. This one runs on the TAG PATH, before
    context assembly, inside the conversation's slot — so discarding a
    readable answer costs a correction on the turn it was made, and the
    correction is the whole point of the loop. `_decide` maps `None` to
    `unavailable` and falls back to RULE-F, which catches only the clearest
    case.

    Delegated to `parse_envelope` rather than reimplemented: a seventh copy of
    a rule that has now been wrong twice is a seventh place for it to be wrong
    again, and this module already imports from `nora.agent` for the same
    reason its objective and envelope key live on the class.
    """
    items = parse_envelope(raw, key=Recogniser.envelope_key)
    if items is None:
        return None
    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        handle, reason = item.get("claim"), item.get("reason")
        if not isinstance(handle, str) or handle not in _HANDLES:
            continue
        if len(handle) != 1:
            continue
        out.append((handle, reason if isinstance(reason, str) else ""))
    return tuple(out)
