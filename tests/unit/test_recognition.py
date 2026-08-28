"""Step 3 of the correction loop — the step that had no mechanism.

`RECOGNISE` occurred once in thirty-one design nodes, as a line in a diagram,
and by elimination the recogniser was the reply-path model — which rests the
primary trust loop on `02`'s named pathology, *she recognises and does not act*.

**What these tests are about is the SPLIT.** Targeting is deterministic because
it decides the blast radius and a model choosing rows would be choosing them by
identifier. The dispute predicate is semantic because a cue list genuinely
cannot separate *"no, big walk is a video game"* from *"no I can't make the big
walk"* — and the design's first draft claimed it could, which is the defect the
adversarial review broke with the draft's own example.

Doubles throughout, and they are faithful where this module BRANCHES: the ledger
reports R-27's three states, the classifier can be unavailable or answer prose,
and the fact store returns the four CAS outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.facts import RenderedClaim
from nora.domain.models import NormalizedMessage, Quote
from nora.engagement.recognition import Recogniser
from nora.obs.metrics import Metrics

NOW = 1_755_200_000_000
CONV = "signal:g1"
FACT = "0191f2c4-0000-7000-8000-00000000000a"


@dataclass
class _LedgerRead:
    claims: tuple
    status: str


class _FakeLedger:
    def __init__(self, claims=(), *, status="ok") -> None:
        self._claims = tuple(claims)
        self._status = status

    async def in_window(self, *, conversation_id, since_ms):
        return _LedgerRead(claims=self._claims, status=self._status)


class _FakeFacts:
    def __init__(self, outcome="ok") -> None:
        self.calls: list[dict] = []
        self._outcome = outcome

    async def mark_disputed(self, **kw):
        self.calls.append(kw)
        return self._outcome


class _FakeLexemes:
    def __init__(self, marked=1) -> None:
        self.calls: list[dict] = []
        self._marked = marked

    async def mark_disputed(self, **kw):
        self.calls.append(kw)
        return self._marked


class _FakeClassifier:
    def __init__(self, reply='{"disputes": []}', *, raises=False) -> None:
        self.prompts: list[str] = []
        self._reply = reply
        self._raises = raises

    async def complete(self, *, prompt: str) -> str:
        self.prompts.append(prompt)
        if self._raises:
            raise RuntimeError("the provider is down")
        return self._reply


def _claim(surface="big walk", row_key=FACT):
    return RenderedClaim(
        conversation_id=CONV, kind="fact", row_key=row_key, surface=surface,
        workspace="nora", last_tag_id="tag-0", last_rendered_at=NOW,
    )


def _msg(body="no, big walk is a video game", *, quote=None):
    return NormalizedMessage(
        platform="signal", conversation_id=CONV, conversation_type="group",
        sender_id="+852111", sender_name="Grant", timestamp=1, body=body,
        raw={}, quote=quote,
    )


def _recogniser(*, ledger=None, facts=None, lexemes=None, classifier=None,
                metrics=None, instruction="INSTRUCTION", **kw):
    return Recogniser(
        ledger=ledger or _FakeLedger((_claim(),)),
        facts=facts or _FakeFacts(),
        lexemes=lexemes,
        classifier=classifier,
        instruction=instruction,
        metrics=metrics,
        **kw,
    )


# ---------------------------------------------------------------------------
# targeting — deterministic, because it decides the blast radius
# ---------------------------------------------------------------------------


async def test_a_message_naming_a_rendered_claim_targets_it():
    facts = _FakeFacts()
    classifier = _FakeClassifier('{"disputes": [{"claim": "1", "reason": "a game"}]}')
    rec = _recogniser(facts=facts, classifier=classifier)

    marked = await rec.recognise(msg=_msg(), tag_id="tag-1", person_id="grant")

    assert marked == 1
    assert facts.calls[0]["fact_id"] == FACT
    assert facts.calls[0]["person_id"] == "grant"
    assert facts.calls[0]["reason"] == "a game"


async def test_a_message_naming_nothing_targets_nothing():
    """The positive control for every test above it: a recogniser that targeted
    everything would satisfy them all while marking a row on any message."""
    facts = _FakeFacts()
    classifier = _FakeClassifier('{"disputes": [{"claim": "1", "reason": "x"}]}')
    rec = _recogniser(facts=facts, classifier=classifier)

    marked = await rec.recognise(
        msg=_msg("what time is dinner"), tag_id="tag-1", person_id="g",
    )

    assert marked == 0
    assert facts.calls == []
    assert classifier.prompts == []


async def test_the_prompt_carries_handles_and_never_a_row_id():
    """`dream_screen` drops any envelope item carrying an identifier, silently,
    while the tier's own counters look healthy. A recogniser that showed a
    `fact.id` would be echoing one back."""
    classifier = _FakeClassifier()
    rec = _recogniser(classifier=classifier)

    await rec.recognise(msg=_msg(), tag_id="tag-1", person_id="g")

    assert "[1] big walk" in classifier.prompts[0]
    assert FACT not in classifier.prompts[0]
    assert "0191f2c4" not in classifier.prompts[0]


async def test_targeting_is_capped():
    """Targeting decides the blast radius, and the design's first draft capped
    nothing: one message naming three surfaces would mark three entity groups
    deterministically."""
    claims = tuple(_claim(surface=f"thing {i}", row_key=f"f{i}") for i in range(6))
    classifier = _FakeClassifier()
    rec = _recogniser(
        ledger=_FakeLedger(claims), classifier=classifier, max_targets=2,
    )

    await rec.recognise(
        msg=_msg("thing 0 thing 1 thing 2 thing 3"), tag_id="t", person_id="g",
    )

    prompt = classifier.prompts[0]
    assert prompt.count("- [") == 2


async def test_an_unreadable_ledger_is_unavailable_and_targets_nothing():
    """R-27. An unreadable ledger is «I could not check what she rendered»,
    never «she rendered nothing» — reporting the latter would let recognition
    rule out a dispute it never looked for."""
    metrics = Metrics()
    facts = _FakeFacts()
    rec = _recogniser(
        ledger=_FakeLedger((_claim(),), status="unavailable"),
        facts=facts, classifier=_FakeClassifier(), metrics=metrics,
    )

    assert await rec.recognise(msg=_msg(), tag_id="t", person_id="g") == 0
    assert facts.calls == []
    assert metrics.get(
        "nora_dispute_targets_total", scope="direct", via="none",
        status="unavailable",
    ) == 1


# ---------------------------------------------------------------------------
# the dispute predicate — semantic, and the case a cue list cannot make
# ---------------------------------------------------------------------------


async def test_declining_an_invitation_is_not_a_contradiction():
    """§1's own counterexample, and the one the adversarial review used to
    break the design's first draft. "no I can't make the big walk" carries a
    negation cue, names the surface, and disputes nothing.

    The classifier is what decides this — which is the whole reason the dispute
    predicate is not a word list."""
    facts = _FakeFacts()
    rec = _recogniser(facts=facts, classifier=_FakeClassifier('{"disputes": []}'))

    marked = await rec.recognise(
        msg=_msg("no I can't make the big walk"), tag_id="t", person_id="g",
    )

    assert marked == 0
    assert facts.calls == []


async def test_declining_is_counted_as_declined_not_as_never_ran():
    """The label that makes `unacted_total` readable: without it the series
    reads identically for a working recogniser on a quiet room and a broken
    one, which is 02's pathology made invisible again by the instrument built
    to expose it."""
    metrics = Metrics()
    rec = _recogniser(
        classifier=_FakeClassifier('{"disputes": []}'), metrics=metrics,
    )

    await rec.recognise(
        msg=_msg("no I can't make the big walk"), tag_id="t", person_id="g",
    )

    assert metrics.get("nora_dispute_unacted_total", cause="declined") == 1
    assert metrics.get("nora_dispute_unacted_total", cause="not_reached") == 0
    assert metrics.get("nora_dispute_classify_total", status="nothing_found") == 1


async def test_prose_from_the_model_is_unavailable_not_a_decision():
    """A model that replied with a sentence did not decide there was no
    dispute; it failed to answer. Counting it as a decision would make a broken
    provider look like a quiet room."""
    metrics = Metrics()
    rec = _recogniser(
        classifier=_FakeClassifier("Sure! Here's what I think..."),
        metrics=metrics,
    )

    await rec.recognise(msg=_msg(), tag_id="t", person_id="g")

    assert metrics.get("nora_dispute_classify_total", status="unavailable") == 1
    assert metrics.get("nora_dispute_unacted_total", cause="not_reached") == 1


async def test_a_handle_it_was_never_shown_is_refused():
    """The model may only name something it was shown. A dispute naming a row
    nobody put in front of it is a guess with a mark attached."""
    metrics = Metrics()
    facts = _FakeFacts()
    rec = _recogniser(
        facts=facts, metrics=metrics,
        classifier=_FakeClassifier('{"disputes": [{"claim": "7", "reason": "x"}]}'),
    )

    assert await rec.recognise(msg=_msg(), tag_id="t", person_id="g") == 0
    assert facts.calls == []
    assert metrics.get("nora_dispute_handle_refused_total") == 1


# ---------------------------------------------------------------------------
# RULE-F — the fallback, and only when the classifier could not
# ---------------------------------------------------------------------------


async def test_rule_f_fires_only_when_the_classifier_is_unavailable():
    """In the design's first draft it ran ALWAYS and was called unambiguous.
    The review broke that with the draft's own example. It is a fallback now,
    and its precision cost is survivable only because a dispute MARKS a claim
    rather than muting it."""
    facts = _FakeFacts()
    rec = _recogniser(facts=facts, classifier=_FakeClassifier(raises=True))
    quoted = _msg("no, big walk is a game", quote=Quote(
        author_id="nora", text="big walk means a walk", quoted_ts=999,
    ))

    marked = await rec.recognise(
        msg=quoted, tag_id="t", person_id="g", quotes_nora=True,
    )

    assert marked == 1
    assert facts.calls[0]["fact_id"] == FACT


async def test_rule_f_needs_a_reply_to_her_a_cue_and_the_surface():
    """All three. The surface must be in the REPLY rather than merely in the
    quoted message, or a bare 「唔得」 would mark whatever that turn rendered.

    **The third row is the one that was missing.** RULE-F guarded on
    `msg.quote is not None` while its docstring said "a quote-reply to one of
    Nora's own messages" — so a member contradicting ANOTHER MEMBER, while
    naming a surface she holds, marked her claim on a rule whose entire
    premise is that the correction was addressed to her. It fires only where
    the classifier could not, which is exactly where nothing else is looking.
    """
    for body, quote, quotes_nora, expected in (
        ("no, big walk is a game", None, False, 0),          # no quote
        ("big walk is a game", _q(), True, 0),               # no cue
        ("no, big walk is a game", _q(), False, 0),          # quotes SOMEBODY ELSE
        ("no way", _q(), True, 0),                           # no surface
        ("no, big walk is a game", _q(), True, 1),           # all three
    ):
        facts = _FakeFacts()
        rec = _recogniser(facts=facts, classifier=_FakeClassifier(raises=True))
        marked = await rec.recognise(
            msg=_msg(body, quote=quote), tag_id="t", person_id="g",
            quotes_nora=quotes_nora,
        )
        assert marked == expected, (body, quotes_nora)


def _q():
    return Quote(author_id="nora", text="big walk means a walk", quoted_ts=999)


async def test_a_bare_negation_reply_targets_but_does_not_mark_without_a_cue_match():
    """A quote-reply with no surface in it still TARGETS — the quote is what
    puts those rows in scope — and the classifier decides. RULE-F alone would
    mark nothing, which is the conservative half."""
    facts = _FakeFacts()
    classifier = _FakeClassifier('{"disputes": [{"claim": "1", "reason": "no"}]}')
    rec = _recogniser(facts=facts, classifier=classifier)

    marked = await rec.recognise(
        msg=_msg("唔係呀", quote=_q()), tag_id="t", person_id="g",
        quotes_nora=True,
    )

    assert classifier.prompts != []
    assert marked == 1


# ---------------------------------------------------------------------------
# what a mark does
# ---------------------------------------------------------------------------


async def test_a_dispute_fans_out_to_the_lexeme_tier():
    """31 §6: one correction contradicts a lexeme row and a fact row at once.
    By SURFACE, because no entity pointer exists on either side — and the
    degradation is counted rather than silent."""
    metrics = Metrics()
    lexemes = _FakeLexemes(marked=2)
    rec = _recogniser(
        lexemes=lexemes, metrics=metrics,
        classifier=_FakeClassifier('{"disputes": [{"claim": "1", "reason": "x"}]}'),
    )

    marked = await rec.recognise(msg=_msg(), tag_id="t", person_id="g")

    assert marked == 3
    assert lexemes.calls[0]["surface"] == "big walk"
    assert lexemes.calls[0]["scope_id"] == CONV
    assert metrics.get("nora_dispute_fanout_unresolved_total") == 1


async def test_a_second_dispute_is_counted_but_marks_nothing():
    metrics = Metrics()
    rec = _recogniser(
        facts=_FakeFacts(outcome="already_disputed"), metrics=metrics,
        classifier=_FakeClassifier('{"disputes": [{"claim": "1", "reason": "x"}]}'),
    )

    assert await rec.recognise(msg=_msg(), tag_id="t", person_id="g") == 0
    assert metrics.get(
        "nora_dispute_marked_total", tier="fact", outcome="already_disputed") == 1
    assert metrics.get(
        "nora_dispute_recognised_total", tier="fact", channel="model") == 0


async def test_without_a_live_adjudicator_it_proposes_rather_than_marks():
    """31 §7.2. Clearing a dispute happens only inside adjudication, and
    adjudication is a Dreamer adaptor — so without a live one, marking would
    accumulate and never resolve. The loop would be a ratchet rather than a
    loop, which is the difference between shipping this and shipping damage."""
    metrics = Metrics()
    facts = _FakeFacts()
    rec = _recogniser(
        facts=facts, metrics=metrics, adjudicator_alive=False,
        classifier=_FakeClassifier('{"disputes": [{"claim": "1", "reason": "x"}]}'),
    )

    assert await rec.recognise(msg=_msg(), tag_id="t", person_id="g") == 0
    assert facts.calls == []
    assert metrics.get("nora_dispute_degraded_total") == 1


async def test_recognition_never_raises():
    """It runs before the reply is assembled. A recogniser fault must cost the
    correction, which is the status quo, and never the turn."""
    class _Exploding:
        async def in_window(self, **kw):
            raise RuntimeError("boom")

    metrics = Metrics()
    rec = _recogniser(ledger=_Exploding(), metrics=metrics)

    assert await rec.recognise(msg=_msg(), tag_id="t", person_id="g") == 0
    assert metrics.get(
        "nora_dispute_targets_total", scope="direct", via="none",
        status="unavailable") == 1


async def test_an_unmapped_sender_still_disputes():
    """An unmapped sender has no `person_id`, and refusing their correction
    would make the loop depend on somebody having been added to a config
    file — a wiring gap deciding whether a correction counts."""
    facts = _FakeFacts()
    rec = _recogniser(
        facts=facts,
        classifier=_FakeClassifier('{"disputes": [{"claim": "1", "reason": "x"}]}'),
    )

    assert await rec.recognise(msg=_msg(), tag_id="t", person_id=None) == 1
    assert facts.calls[0]["person_id"] is None
