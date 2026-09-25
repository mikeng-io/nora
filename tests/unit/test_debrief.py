"""The debrief stage — `26-standing` §1/§4/§4.0/§4.3/§5.

**The fake model client answers DIFFERENTLY per call and can be asked to fail
the way the real route fails** (CLAUDE.md: never write a test whose fake answers
every request the same way). A stub that always returned one well-formed JSON
body would go green on every failure this module exists to handle — the
truncated `finish_reason="length"`, the overflow-then-consolidate round trip,
and the cancellation — because none of them is reachable through a single happy
answer.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from nora.domain.models import DeliveryStatus, NormalizedMessage, TranscriptRow
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.tag_trace import TagTrace
from nora.engagement.stages import debrief
from nora.obs.metrics import Metrics
from nora.obs.tracing import span
from nora.testing import FakeRecordStore, InMemoryStandingStore

pytestmark = pytest.mark.asyncio


class _Client:
    """Answers a scripted sequence of `Completion`s, one per call, and records
    every prompt it was given. A `BaseException` in the script is RAISED rather
    than returned, which is how the cancellation case is reachable at all."""

    def __init__(self, *script) -> None:
        self._script = list(script)
        self.prompts: list[str] = []
        #: One entry per `complete()` call, in order — what `_complete`
        #: passed as `trace_attributes`. `_Client` never calls Strands
        #: itself (see the module-level note above the retired trace-shape
        #: tests), so this is how a debrief-level test verifies the
        #: DELEGATION is correct without needing a real Agent invocation.
        self.trace_attributes: list[dict | None] = []

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(
        self, *, system: str, prompt: str, trace_attributes=None,
    ) -> debrief.Completion:
        self.trace_attributes.append(trace_attributes)
        self.prompts.append(f"{system}\n\n{prompt}")
        item = self._script.pop(0) if self._script else debrief.Completion("{}", "stop")
        if isinstance(item, BaseException):
            raise item
        return item


class _Objective:
    """The fold's one objective, as a marker string, so a test can tell the
    objective reached the prompt. Any other name is refused: the stage reads
    exactly one objective since the verdict's was deleted (2026-09-24)."""

    def objective_body(self, name: str) -> str:
        assert name == debrief.OBJECTIVE, name
        return "OBJECTIVE-TEXT"


def _ok(body: str) -> debrief.Completion:
    """The fold's answer: the STANDING shape."""
    return debrief.Completion(json.dumps({"standing": body}), "stop")


def _ok_ws(body: str, working_set: list) -> debrief.Completion:
    """The fold's answer with a `working_set` array beside `standing`
    (design/standing-compact/30-working-set.md §3)."""
    return debrief.Completion(
        json.dumps({"standing": body, "working_set": working_set}), "stop")


def _trace(outcome: str = "send_reply", messages: tuple = ()) -> TagTrace:
    row = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Miles", ts=1_756_000_920_000,
        body="去邊食", is_nora=False, delivery_status=DeliveryStatus.SENT,
        raw_json=None,
    )
    return TagTrace(
        tag_id="tag-1", conversation_id="g1", messages=messages, transcript=(row,),
        outcome=outcome, created_at=1_756_000_930_000,
    )


async def _run(*, store=None, client=None, metrics=None,
               max_chars=1200, timeout_seconds=5.0, store_timeout_seconds=5.0,
               now_ms_override=None, trace=None, record_store=None,
               fold_batch=200, culture=None,
               working_set_max_items=5, working_set_max_chars=600) -> tuple:
    store = store if store is not None else InMemoryStandingStore()
    metrics = metrics if metrics is not None else Metrics()
    await debrief.run(
        trace=trace if trace is not None else _trace(),
        platform="signal", store=store,
        client=client if client is not None else _Client(_ok("a position")),
        objective=_Objective(), metrics=metrics, display_name="Nora",
        record_store=record_store if record_store is not None else FakeRecordStore(),
        max_chars=max_chars, fold_batch=fold_batch,
        timeout_seconds=timeout_seconds,
        store_timeout_seconds=store_timeout_seconds,
        now_ms=now_ms_override if now_ms_override is not None else 1_756_000_931_000,
        culture=culture,
        working_set_max_items=working_set_max_items,
        working_set_max_chars=working_set_max_chars,
    )
    return store, metrics


def _counter(metrics: Metrics, name: str, **labels) -> float:
    return metrics.get(name, **labels)


# --- the happy path and the non-negotiable ---------------------------------

async def test_a_fold_writes_the_position_keyed_on_platform_and_conversation():
    store, metrics = await _run()
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.body == "a position"
    assert row.fold_count == 1
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


async def test_the_fold_reads_the_current_standing_before_writing_the_next():
    """**§4's non-negotiable**, and the one requirement in this bundle with
    live evidence behind it: across 963 processed messages the Dreamer wrote
    two rows, both wrong, both about the same thing — the second run knew MORE
    than the first and still wrote a second wrong row beside it, because
    nothing in its write path could see what was already there.

    Asserted through the PROMPT rather than through a spy on the store: what
    matters is not that `read` was called, it is that what it returned reached
    the model. A read whose result is dropped before the prompt is the same
    failure with a passing call count.
    """
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1",
        body="They are planning dinner for the 9th.", fold_count=4, updated_at=1,
    ))
    client = _Client(_ok("revised"))
    _, metrics = await _run(store=store, client=client)
    assert "They are planning dinner for the 9th." in client.prompts[0]
    assert _counter(metrics, "nora_debrief_prior_total", prior="read") == 1
    # Counted from the prior it read, not from zero.
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.fold_count == 5


async def test_the_fold_is_shown_its_own_working_set_prior():
    """design/standing-compact/30-working-set.md §4: `<standing_working_set_
    prior>` rides beside `<standing_prior>` in the FOLD's own input — the
    same always-present contract, so the model can re-judge it rather than
    re-deriving it from nothing every turn."""
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="a position",
        fold_count=1, updated_at=1, working_set=(
            WorkingSetItem(what="an existing option", source="web_search",
                            found_at=1),
        ),
    ))
    client = _Client(_ok("revised"))
    await _run(store=store, client=client)
    prompt = client.prompts[0]
    assert "<standing_working_set_prior" in prompt
    assert "an existing option" in prompt
    assert prompt.index("<standing_prior") < prompt.index("<standing_working_set_prior")


async def test_a_first_fold_is_counted_as_absent_rather_than_read():
    _, metrics = await _run()
    assert _counter(metrics, "nora_debrief_prior_total", prior="absent") == 1


async def test_a_store_that_cannot_be_read_folds_nothing():
    """R-27 at the top of the stage: an unreachable store is `unavailable`
    («I could not check»), never a fold against a guessed-empty prior. Folding
    anyway would write a fresh position beside an existing one the fold never
    saw — the exact two-contradictory-rows failure, reached from the other
    direction."""
    class _Broken(InMemoryStandingStore):
        async def read(self, **kw):
            raise RuntimeError("db down")

    store = _Broken()
    _, metrics = await _run(store=store)
    assert await InMemoryStandingStore.read(
        store, platform="signal", conversation_id="g1") is None
    assert _counter(metrics, "nora_debrief_total", outcome="unavailable") == 1


# --- §2: every completed turn, not only the ones that sent -----------------

@pytest.mark.parametrize(
    "outcome", ["send_reply", "recovered", "silent", "vetoed", "timeout", "llm_error"])
async def test_every_outcome_is_debriefed_including_the_silences(outcome):
    """§2, and it is the decision most easily lost to an inherited gate: a turn
    that deliberately said nothing produced just as much reasoning as one that
    replied, and R-5 silence is the decision most likely to be wrong in a way
    nobody complains about. A debrief that only reviews successful replies
    measures the path that already works."""
    store, metrics = await _run(trace=_trace(outcome))
    assert await store.read(platform="signal", conversation_id="g1") is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


# --- §4.0: refuse and consolidate, never truncate --------------------------

async def test_an_over_long_fold_is_refused_and_the_model_is_asked_to_consolidate():
    client = _Client(_ok("x" * 60), _ok("short"))
    store, metrics = await _run(client=client, max_chars=50)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.body == "short"
    # 1: the fold (over-long), 2: the consolidate retry — and nothing else.
    assert len(client.prompts) == 2
    assert "consolidate" in client.prompts[1].lower()
    assert "50-character limit" in client.prompts[1]
    assert _counter(
        metrics, "nora_standing_overflow_refusals_total", stage="first") == 1


async def test_the_tiebreak_is_reversed_and_recency_scoped_in_the_retry():
    """**§4.0.1's ordering rule, REVERSED by Phase 2 and still paired with its
    recency scope.**

    The old rule — what the room SAID outranks what Nora concluded — taught
    the shrink to prefer exactly the recap-shaped clauses that kept the
    standing row an event list; the flip and its falsifier live in
    `design/standing-split-fold/20-ratchet-and-tiebreak.md`. The position is
    hers, so her synthesis wins against any room quote, and recency is scoped
    to a tie between two things of the SAME KIND.

    Asserts the reversed rule is present, the recency scope survived it, and
    the old inversion is gone.

    **Three ranks since 2026-09-24** (design/standing-compact/10 §5): what
    she found and has not shared sits between the two. Unranked, a finding
    was always the most recent clause and so the last to go, and it evicted
    older commitments first. Asserted on the prompt the fake client actually
    RECEIVED, and on the ORDER of the three, not merely their presence.
    """
    client = _Client(_ok("x" * 60), _ok("short"))
    await _run(client=client, max_chars=50)
    retry = client.prompts[1]
    assert "never the most recent" in retry, "the recency rule went missing"
    rule = (
        "What you CONCLUDED or COMMITTED to outranks what you FOUND and have "
        "not shared, and what you found outranks what the room SAID"
    )
    assert rule in retry, (
        "the three-rank order is not in the retry, so a shrink could drop a "
        "commitment for a finding, or her judgement for a room quote"
    )
    concluded = retry.index("CONCLUDED or COMMITTED")
    found = retry.index("what you FOUND and have not shared")
    said = retry.index("what the room SAID")
    assert concluded < found < said, retry
    assert "drop in the reverse of that order" in retry
    assert "two things of the same kind" in retry, (
        "recency is stated unqualified, so it still overrides the synthesis "
        "rule whenever the newer item is the room's"
    )
    assert "What the room SAID outranks what you concluded" not in retry, (
        "the old, inverted tiebreak survived in the retry prompt"
    )


def test_the_objective_states_the_synthesis_tiebreak_too():
    """The retry prompt and the objective are two texts the model sees and
    they must not disagree about which rule governs. The objective is the one
    that hot-reloads on mtime, so a drift between them is invisible until a
    fold picks the wrong thing to drop."""
    from pathlib import Path

    body = Path("nora/dreamer/objectives/standing-debrief.md").read_text()
    assert (
        "What you CONCLUDED or COMMITTED to outranks what you FOUND and have "
        "not shared, and what you found outranks what the room SAID"
    ) in body
    assert "two things of the same kind" in body
    assert "What the room SAID outranks what you concluded" not in body


def test_the_objective_speaks_in_the_first_person_about_the_position():
    """The person contract (issue #78): the fold is Nora revising HER OWN
    position, so the objective must speak in the first person about the
    standing — a third-person cast ("you are not the assistant who answered
    it") is what produced transcript recaps instead of positions, because an
    observer who is not a participant has no position to revise.

    The grounding verdict had its own objective from Phase 2 until it was
    deleted on 2026-09-24 (design/standing-compact/); its rubric must not
    drift back into the position writer's prompt.
    """
    from tests.objectives import shipped_objectives

    body = shipped_objectives().objective_body("standing-debrief")
    # The position half: first person, and the old third-person cast is gone.
    assert "You are Nora, reviewing your own finished exchange" in body
    assert "You are not the assistant who answered it" not in body
    assert "what I have committed to" in body
    assert "praise or criticism of my reply" in body
    # No residual third-person self-reference anywhere in the body — the
    # fold is addressed as "you" throughout, so "the assistant's" (the
    # possessive third-person reference to the actor being reviewed) must
    # not reappear. "You are the assistant who answered it" is the
    # identity cast itself and stays.
    assert "the assistant's" not in body
    # The sourcing rubric must not ride next to `<standing_prior>`: the
    # verdict it fed is deleted, and a fold told to grade the turn writes a
    # grade, not a position.
    assert "GROUNDED" not in body, (
        "the judging rubric is back in the standing objective"
    )


def test_the_ratchet_rejudges_whole_rather_than_revising_in_place():
    """Phase 2's ratchet (AC5): «keep its wording and change only what this
    turn changed» is what preserved inherited third-person recap clauses —
    Phase 1 re-voiced the rule as prose and the first post-deploy fold still
    wrote an event list. The fold now re-judges the whole paragraph every
    turn, and inherited clauses survive only if they still hold AND are
    re-voiced in the fold's own voice.

    Pinned on the shipped objective through the real loader, like the
    deployment's hot-reload reads it — not on `read_text()`, which cannot
    tell a body from an unshipped frontmatter clause."""
    from tests.objectives import shipped_objectives

    body = shipped_objectives().objective_body("standing-debrief")
    assert "RE-JUDGE the prior position" in body, (
        "the re-judge posture is gone — the ratchet is back to revising in "
        "place"
    )
    assert "keep its wording" not in body, (
        "the keep-its-wording ratchet survived the Phase 2 flip"
    )
    assert "never carried over verbatim" in body, (
        "the re-voice rule went missing with the flip"
    )


def test_the_objective_gives_a_concrete_recap_vs_position_contrast():
    """The recap-shape failure survived the person contract and the ratchet:
    post-split rows are first-person but still event lists — a position that
    duplicates the transcript. Prose rules ("if it reads like a recap, it is
    wrong") did not move behaviour; the objective now carries a concrete
    contrast of the SAME turn in both shapes, plus a derivability test that
    names the failure mode directly.

    Pinned on the shipped objective through the real loader, like the
    deployment's hot-reload reads it.
    """
    from tests.objectives import shipped_objectives

    body = shipped_objectives().objective_body("standing-debrief")
    # The derivability test — the sentence-level rule that makes "recap"
    # decidable rather than a vibe.
    assert "could someone who read the transcript write this sentence too" in body
    # The concrete contrast: both shapes present, labelled, and the recap
    # shape is explicitly wrong.
    assert "RECAP — wrong" in body
    assert "POSITION — right" in body
    # The recap example must not be the only shape the model sees — the
    # position example carries the state vocabulary (commitment, open
    # problem, hypothesis, next step) that the recap lacks.
    assert "commit 咗" in body
    assert "root cause" in body
    # The closing line states the axis: what happened vs what I hold open.
    assert 'The recap answers "what happened"' in body
    assert 'The position answers "what I am holding open' in body


def test_the_derivability_test_and_closing_line_require_usefulness_for_a_finding():
    """Issue #445 review: v8 first added "found and have not yet given the
    room" to the derivability test and the closing line without the bullet's
    own condition, "while it is still useful to someone here" (line "what I
    found or worked out..." above). Without it, the every-sentence test lets
    in a finding that is mere trivia — another placing, a field size, a team
    background — because those too are "found and not yet given". Both
    sentences must carry the full clause, not just "found ... not yet given
    the room".

    Pinned on the shipped objective through the real loader, like the
    deployment's hot-reload reads it. Guarded by
    scripts/mutations/standing-compact.json (two entries, one per sentence)."""
    from tests.objectives import shipped_objectives

    body = shipped_objectives().objective_body("standing-debrief")
    clause = "found, have not yet given the room, and is still useful to someone here"
    assert (
        "it is what YOU concluded, committed to, are still holding open, "
        f"or {clause}."
    ) in body, "the derivability test dropped the usefulness condition"
    assert (
        'The position answers "what I am holding open, committed to, '
        f'concluding, or {clause}". Write the second.'
    ) in body, "the closing line dropped the usefulness condition"


async def test_a_second_overflow_writes_nothing_and_the_prior_position_stands():
    """§4.0's fail-closed half. Truncating here would be the silent eviction
    that turns a rolling position into a recent summary; writing the over-long
    body would be refused by the database anyway. A coherent stale position
    beats a sentence cut in half."""
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="the old position",
        fold_count=1, updated_at=1,
    ))
    client = _Client(_ok("x" * 60), _ok("y" * 60))
    _, metrics = await _run(store=store, client=client, max_chars=50)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.body == "the old position"
    assert row.fold_count == 1
    assert _counter(
        metrics, "nora_standing_overflow_refusals_total", stage="final") == 1
    assert _counter(metrics, "nora_debrief_total", outcome="refused_overflow") == 1


async def test_a_fold_exactly_at_the_cap_is_written():
    store, _ = await _run(client=_Client(_ok("x" * 50)), max_chars=50)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and len(row.body) == 50


# --- the raw completion's own failure shapes -------------------------------

async def test_a_truncated_body_is_never_parsed():
    """The price of admission for a raw `chat.completions.create`. A Strands
    agent RAISES `MaxTokensReachedException` on a cut-off — and, since
    `ask_text` now catches it too (2026-09-19), the debrief's real production
    client also arrives here with `finish_reason="length"`, not just this
    test's fake. Either way, this checks the CONSUMING side: a
    `finish_reason="length"` body must never be parsed, and a truncated
    JSON document can parse into something shorter and wrong. That is R-27's
    substitution one layer down: an ERROR reported as a PASS.

    The scripted body here is deliberately VALID JSON, so a version of this
    module that parsed first and checked `finish_reason` afterwards (or never)
    goes green on the parse and red on this test.
    """
    store, metrics = await _run(client=_Client(
        debrief.Completion(json.dumps({"standing": "half a th"}), "length")))
    assert await store.read(platform="signal", conversation_id="g1") is None
    assert _counter(metrics, "nora_debrief_total", outcome="unavailable") == 1


@pytest.mark.parametrize("content", [
    "not json at all",
    '{"stnading": "typo"}',
    '{"standing": 42}',
    '{"standing": "   "}',
    '["standing"]',
])
async def test_a_malformed_answer_writes_nothing(content):
    store, metrics = await _run(
        client=_Client(debrief.Completion(content, "stop")))
    assert await store.read(platform="signal", conversation_id="g1") is None
    assert _counter(metrics, "nora_debrief_total", outcome="malformed") == 1


async def test_unavailable_and_malformed_are_different_readings():
    """R-27's discipline as an ASSERTION rather than as a comment. Both write
    nothing, and collapsing them would leave the one instrument this stage has
    unable to tell «the call was cut off» (raise the ceiling, shrink the input)
    from «the model answered in the wrong shape» (fix the objective text)."""
    _, cut = await _run(client=_Client(debrief.Completion('{"standing":"x"}', "length")))
    _, bad = await _run(client=_Client(debrief.Completion("nonsense", "stop")))
    assert _counter(cut, "nora_debrief_total", outcome="unavailable") == 1
    assert _counter(cut, "nora_debrief_total", outcome="malformed") == 0
    assert _counter(bad, "nora_debrief_total", outcome="malformed") == 1
    assert _counter(bad, "nora_debrief_total", outcome="unavailable") == 0


# --- §4.3: screened before the write ---------------------------------------

@pytest.mark.parametrize("body, reason", [
    ("Miles said to call +85290000101 about it.", "identifier"),
    ("They agreed to meet at 22.4106,114.1245.", "coordinate"),
])
async def test_an_identifier_or_a_coordinate_never_reaches_the_row(body, reason):
    """§4.3, and this is the path nobody had recorded: `tag_trace.messages`
    holds raw tool results, a `where_is` result contains a live position, and
    Standing is injected into every subsequent turn — so a coordinate that gets
    in persists **by design** rather than by attack. R-18/R-28 guard egress
    from a REPLY and see nothing here, because this is an input."""
    store, metrics = await _run(client=_Client(_ok(body)))
    assert await store.read(platform="signal", conversation_id="g1") is None
    assert _counter(metrics, "nora_debrief_total", outcome="screened") == 1
    assert _counter(
        metrics, "nora_dream_screened_total", adaptor="debrief", reason=reason) == 1


# --- §5: it never turns a delivered turn into a failure --------------------

async def test_a_cancellation_is_absorbed_and_never_re_raised():
    """§5's second failure shape, and `except Exception` is exactly what does
    NOT catch it: `CancelledError` derives from `BaseException`. Escaping here
    reaches `EngagementPipeline`'s own outer handler, which counts
    `nora_silent_failures_total{reason="handler_error"}` and logs a silent
    failure **on a turn that just delivered a complete reply** — a false alarm
    on a working turn, which is worse than the debrief not running.

    Narrow the `except` in `debrief.run` to `Exception` and this goes red.
    """
    _, metrics = await _run(client=_Client(asyncio.CancelledError()))
    # The property is ABSORBED — `_run` returned rather than propagating. The
    # label is `cancelled` rather than `error` (see
    # `test_a_cancellation_is_not_counted_as_an_error`); asserting the total
    # here keeps this test about the escape, so narrowing the `except` still
    # turns it red regardless of how the exit is labelled.
    assert _counter(metrics, "nora_debrief_total") == 1


async def test_an_arbitrary_failure_is_absorbed():
    _, metrics = await _run(client=_Client(RuntimeError("boom")))
    assert _counter(metrics, "nora_debrief_total", outcome="error") == 1


async def test_a_slow_fold_is_cut_at_its_own_timeout():
    """The debrief holds the slot, so an unbounded fold is not a slow
    background job — it is a room that stops answering. Its own label, because
    this is the reading `[standing] timeout_seconds` is tuned against."""
    class _Slow(_Client):
        async def complete(self, *, system: str, prompt: str, trace_attributes=None):
            await asyncio.sleep(5)
            raise AssertionError("should have been cut off")

    _, metrics = await _run(client=_Slow(), timeout_seconds=0.05)
    assert _counter(metrics, "nora_debrief_total", outcome="timeout") == 1


async def test_the_two_calls_share_one_budget():
    """One `wait_for` around the fold AND its consolidate retry, never one
    around each: the budget the slot can afford is a TOTAL, and two
    independently-bounded calls add up to twice it on exactly the turn that
    was already slow enough to need a retry.

    **The first version of this test could not fail** (found by review,
    2026-08-27). It made call 1 instant and call 2 sleep past the timeout, so
    the two-independent-budgets implementation — the very defect it is named
    for — still timed out on call 2 and still produced `outcome="timeout"`. The
    assertion was identical either way.

    The fix is to spend MOST of the budget on the FIRST call. Each call is under
    the per-call timeout, so two independent budgets would let both through and
    the fold would SUCCEED; only a shared budget is exhausted by their sum.
    Asserting `written` rather than `timeout` is what makes the two
    implementations disagree — which is the whole point of the test.
    """
    class _SlowFirst(_Client):
        async def complete(self, *, system: str, prompt: str, trace_attributes=None):
            self.prompts.append(f"{system}\n\n{prompt}")
            await asyncio.sleep(0.06)          # under a 0.10s per-call bound...
            # Keyed on the prompt's SHAPE, not the call count. The first
            # answer overflows, forcing the consolidate retry.
            if "consolidate" not in prompt and "<standing_prior " in prompt:
                return _ok("x" * 60)
            return _ok("short")

    # 0.06 (first standing) + 0.06 (its retry) = 0.12 > 0.10 shared. Under two
    # INDEPENDENT 0.10s budgets both standing calls fit and the fold SUCCEEDS —
    # so `timeout` here is only reachable when the budget is genuinely shared.
    store, metrics = await _run(
        client=_SlowFirst(), max_chars=50, timeout_seconds=0.10)
    assert _counter(metrics, "nora_debrief_total", outcome="timeout") == 1, (
        "the two calls did not share one budget — each was bounded separately, "
        "so their SUM can reach twice the slot hold this stage is allowed"
    )
    assert await store.read(platform="signal", conversation_id="g1") is None


# --- the instrument, on every exit path ------------------------------------

@pytest.mark.parametrize("client, expected", [
    (lambda: _Client(_ok("fine")), "written"),
    (lambda: _Client(debrief.Completion("nonsense", "stop")), "malformed"),
    (lambda: _Client(RuntimeError("boom")), "error"),
])
async def test_latency_is_observed_on_every_exit_including_the_failures(client, expected):
    """§2's own argument applied to this stage's instrument instead of its
    input: a latency series that only records successes describes the path that
    already works, and the exits worth timing are the ones that went wrong."""
    _, metrics = await _run(client=client())
    assert _counter(metrics, "nora_debrief_total", outcome=expected) == 1
    assert metrics.observations("nora_debrief_seconds")


async def test_the_objective_text_reaches_the_prompt():
    """The instructions are read through the injected SOURCE on every call, not
    captured at construction — the objective file is bind-mounted and
    hot-reloads on mtime, so a stage that cached it would keep sending whatever
    was on disk at boot and an operator's edit would look like it did nothing.
    """
    client = _Client(_ok("a position"))
    await _run(client=client)
    assert client.prompts[0].startswith("OBJECTIVE-TEXT")
    assert "at most 1200 characters" in client.prompts[0]


# --- the store calls are bounded too (review finding, 2026-08-27) -----------

async def test_a_hanging_store_read_cannot_hold_the_slot_forever():
    """The stage's docstring used to say it was "bounded twice" and named only
    the model call. `store.read` sat OUTSIDE the `wait_for`, and
    `create_sql_engine` sets no `command_timeout` — so a Postgres connection
    hanging mid-query during the post-reply fold held the per-conversation lock
    indefinitely, on a turn that had already delivered its reply.

    Falsification: move `store.read` back outside `asyncio.wait_for` and this
    hangs rather than failing.
    """
    class _Hangs(InMemoryStandingStore):
        async def read(self, **kw):
            await asyncio.sleep(30)
            raise AssertionError("should have been cut off")

    _, metrics = await asyncio.wait_for(
        _run(store=_Hangs(), store_timeout_seconds=0.05), timeout=5,
    )
    assert _counter(metrics, "nora_debrief_total", outcome="unavailable") == 1


async def test_a_hanging_store_write_cannot_hold_the_slot_forever():
    """The write half of the same defect — and it is the worse one: it happens
    AFTER the reply was delivered and after the model call succeeded, so the
    turn is already a success by every measure the user can see."""
    class _Hangs(InMemoryStandingStore):
        async def write(self, **kw):
            await asyncio.sleep(30)
            raise AssertionError("should have been cut off")

    _, metrics = await asyncio.wait_for(
        _run(store=_Hangs(), store_timeout_seconds=0.05), timeout=5,
    )
    assert _counter(metrics, "nora_debrief_total", outcome="unavailable") == 1


async def test_a_store_write_timeout_is_unavailable_and_never_timeout():
    """R-27, on the one series `[standing] timeout_seconds` is tuned against.

    `asyncio.TimeoutError IS TimeoutError` on every Python this project
    supports, so without its own `except` a timed-out WRITE falls through to
    `except TimeoutError` and is counted as «the fold did not finish inside its
    own budget». That sends the next investigation at the model when the
    problem is Postgres. A store that could not be written is «I could not
    check», exactly as the read already was.
    """
    class _Hangs(InMemoryStandingStore):
        async def write(self, **kw):
            await asyncio.sleep(30)

    _, metrics = await asyncio.wait_for(
        _run(store=_Hangs(), store_timeout_seconds=0.05), timeout=5,
    )
    assert _counter(metrics, "nora_debrief_total", outcome="timeout") == 0, (
        "a store write timeout was counted on the debrief-budget series"
    )
    assert _counter(metrics, "nora_debrief_total", outcome="unavailable") == 1


async def test_a_cancellation_is_not_counted_as_an_error():
    """Alert fatigue, not correctness — but on the one counter meant to mean
    «something is wrong».

    Swallowing the cancellation is design-mandated (26 §5) and
    `test_a_cancellation_is_absorbed_and_never_re_raised` above pins that. What
    this pins is that ordinary shutdown does not look like a defect: every
    routine cancel of an in-flight tag used to write an ERROR traceback and
    increment `outcome="error"`, so the series fired on healthy shutdowns and
    stopped being readable. A noisy gate is a disabled gate.
    """
    _, metrics = await _run(client=_Client(asyncio.CancelledError()))
    assert _counter(metrics, "nora_debrief_total", outcome="cancelled") == 1
    assert _counter(metrics, "nora_debrief_total", outcome="error") == 0


# --- the watermark ---------------------------------------------------------

async def _with_prior(fold_count: int, *, body: str = "the carried position",
                      updated_at: int = 100):
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body=body,
        fold_count=fold_count, updated_at=updated_at))
    return store


async def test_a_normal_fold_advances_the_watermark_to_the_last_row_read():
    """design/standing-watermark/20: the fold reads `since_including_nora`
    since the prior's watermark and advances it to the LAST row of the batch —
    written in the SAME statement as the position. NULL watermark reads from
    the beginning (D5: no backfill)."""
    store = await _with_prior(3, body="the carried position")
    record = FakeRecordStore()
    # 5 rows since the watermark (NULL -> from the beginning), fold_batch 200:
    for i in range(1, 6):
        await record.append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", i, f"m{i}", {}))
    await _run(store=store, record_store=record)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None
    assert row.last_folded_row_id == 5, (
        "a normal fold advances the watermark to the last row it read"
    )
    assert row.fold_count == 4  # and the fold still counts itself


# --- design/standing-compact: one call, the prior every turn, a plain count -

# The fold count the deleted re-derivation ceiling fired at in production
# (`[standing] rebuild_after_folds`, default 20). Named so the tests below say
# what they straddle.
_OLD_CEILING = 20


async def test_a_tag_fold_makes_exactly_one_model_call():
    """design/standing-compact/10 §1: the verdict call is deleted, so a fold
    that fits is ONE completion — on a first fold and on a fold over a prior
    alike. The `trace_attributes` label is read as well as the count, so a
    second call wearing any label shows up as a second entry."""
    client = _Client(_ok("a position"))
    await _run(client=client)
    assert len(client.prompts) == 1
    assert [ta["nora.debrief.call"] for ta in client.trace_attributes] == ["fold"]

    client = _Client(_ok("revised"))
    await _run(client=client, store=await _with_prior(_OLD_CEILING))
    assert len(client.prompts) == 1
    assert [ta["nora.debrief.call"] for ta in client.trace_attributes] == ["fold"]


async def test_a_tag_fold_makes_two_calls_only_on_a_consolidate_retry():
    """The one path to a second call is §4.0's retry, and it is labelled as
    such. Nothing else rides beside it."""
    client = _Client(_ok("x" * 60), _ok("short"))
    store, _ = await _run(client=client, max_chars=50)
    assert [ta["nora.debrief.call"] for ta in client.trace_attributes] == [
        "fold", "consolidate"]
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.body == "short"


async def test_the_prior_reaches_the_fold_on_every_turn_including_past_the_old_ceiling():
    """design/standing-compact/10 §1: **the fold always reads, and always is
    shown, the prior.** Both deleted triggers — an ungrounded verdict and the
    fold-count ceiling — withheld it, and the prompt then told the model «no
    position yet — this is the first fold», which in production wrote a
    145-character position over a 435-character one and dropped three threads
    held for days.

    Driven through the real stage across the old ceiling: each turn's prompt
    must carry the body the PREVIOUS turn wrote, by its exact words, and must
    never say there is no position. Reintroducing any withholding at any fold
    count on this range reddens it."""
    store = await _with_prior(_OLD_CEILING - 2, body="position after fold 18")
    metrics = Metrics()
    expected = "position after fold 18"
    for n in range(_OLD_CEILING - 1, _OLD_CEILING + 4):
        client = _Client(_ok(f"position after fold {n}"))
        await _run(store=store, client=client, metrics=metrics)
        [prompt] = client.prompts
        prior_block = prompt.split("<standing_prior ", 1)[1].split(
            "</standing_prior>", 1)[0]
        assert expected in prior_block, (
            f"fold {n}: the prior was withheld from the prompt"
        )
        assert "no position yet" not in prompt, (
            f"fold {n}: the prompt told the model there was no position "
            "while one existed"
        )
        expected = f"position after fold {n}"
    # POSITIVE CONTROL: every fold read a prior and wrote.
    assert _counter(metrics, "nora_debrief_prior_total", prior="read") == 5
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 5


async def test_a_prior_far_past_the_old_ceiling_is_still_shown():
    """The same property at a count nothing ordinary reaches in one test run,
    so a withholding rule keyed on ANY threshold between 20 and 500 is caught
    by one of the two tests."""
    client = _Client(_ok("revised"))
    await _run(client=client,
               store=await _with_prior(500, body="held for a very long time"))
    assert "held for a very long time" in client.prompts[0]
    assert "no position yet" not in client.prompts[0]


async def test_fold_count_increments_by_one_per_tag_fold_and_is_never_reset():
    """`fold_count` is a plain count since 2026-09-24. It used to RESET to 1
    on a re-derivation; nothing resets it now, and it crosses the old ceiling
    like any other number."""
    store = InMemoryStandingStore()
    counts = []
    for _ in range(3):
        await _run(store=store)
        counts.append((await store.read(
            platform="signal", conversation_id="g1")).fold_count)
    assert counts == [1, 2, 3]

    store = await _with_prior(_OLD_CEILING - 2)
    counts = []
    for _ in range(5):
        await _run(store=store)
        counts.append((await store.read(
            platform="signal", conversation_id="g1")).fold_count)
    assert counts == [19, 20, 21, 22, 23]


async def test_a_cancel_before_the_write_is_still_a_cancel():
    """A cancel before the position is durable is `cancelled`, never
    `written` and never `error`."""
    _, metrics = await _run(client=_Client(asyncio.CancelledError()))
    assert _counter(metrics, "nora_debrief_total", outcome="cancelled") == 1


def test_the_slot_hold_counts_every_store_await_the_stage_makes():
    """**The arithmetic has been wrong four times; this is what stops a fifth.**

    `STANDING_STORE_CALLS_PER_FOLD` feeds
    `Config._the_standing_slot_hold_fits_its_budget`, the gate that decides
    whether the declared ceiling is honest. It said 2 while the stage made 4,
    then 4 while it made 5.

    **It walks the AST, and the two earlier versions of this test were each
    blind in the direction they advertised.**

    v1 counted `asyncio.wait_for` occurrences in two named functions. The
    ORIGINAL defect was a store call with NO `wait_for` at all, which a count of
    wrappers cannot see, and a call in a third function was invisible.

    v2 claimed to find "every collaborator call in the module" and was a
    hardcoded regex over three receiver names and five method names. It passed
    with `notes_sink.supersede` unbounded (a real `DreamSink` method), with a
    `facts_sink` the module's own docstring names as the next output, and with
    an unbounded `store.write` sitting under an unrelated `wait_for` that
    happened to fall inside its 160-character backwards text window.

    So the receiver set is DERIVED from the stage's own parameter names rather
    than written down: a collaborator this stage gains is caught the moment it
    is awaited, without anyone remembering to extend a list. That derivation is
    the half that matters — v2 caught exactly what it had been written against
    and nothing else (cold review, 2026-08-28).
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(debrief))
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    # The collaborators are whatever `run` is HANDED.
    receivers: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef)
                and node.name == "run"):
            a = node.args
            receivers |= {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}

    # `client` is the MODEL call, not a store call: it is bounded by the
    # `wait_for` around `_fold()` rather than by one of its own, and
    # `[standing] timeout_seconds` covers it separately in the sum. Named here
    # so the exclusion is a decision rather than an oversight.
    receivers.discard("client")

    def _in_wait_for(node: ast.AST) -> bool:
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, ast.Call):
                f = cur.func
                if ((isinstance(f, ast.Attribute) and f.attr == "wait_for")
                        or (isinstance(f, ast.Name) and f.id == "wait_for")):
                    return True
            if isinstance(cur, ast.AsyncFunctionDef | ast.FunctionDef):
                return False
            cur = parents.get(cur)
        return False

    # A collaborator call is one that is AWAITED — directly, or by being handed
    # to `wait_for`. Both shapes have to be collected, because the bounded form
    # `await wait_for(store.read(...))` makes `wait_for` the awaited call and
    # `store.read` merely its argument. Collecting only the awaited node finds
    # nothing at all; collecting every call on a collaborator would sweep up
    # `metrics.inc` and `objective.objective_body`, which are neither awaited
    # nor bounded and are not store calls.
    calls: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
            continue
        if f.value.id not in receivers:
            continue
        parent = parents.get(node)
        bounded = _in_wait_for(node)
        if not (bounded or isinstance(parent, ast.Await)):
            continue          # not awaited at all — not a store call
        calls.append((f"{f.value.id}.{f.attr}", bounded))

    named = [n for n, _ in calls]
    assert len(calls) == debrief.STORE_CALLS_PER_FOLD, (
        f"the stage awaits {len(calls)} collaborator calls {named} but "
        f"STORE_CALLS_PER_FOLD is {debrief.STORE_CALLS_PER_FOLD}. The "
        f"slot-hold validator computes the declared ceiling from that "
        f"constant, so disagreement means the ceiling is not the real worst "
        f"case — which is exactly how it came to be understated by 2x."
    )
    unbounded = [n for n, ok in calls if not ok]
    assert not unbounded, (
        f"{unbounded} awaited with no asyncio.wait_for. The engine sets no "
        f"command_timeout and this runs while the conversation's slot is HELD, "
        f"so a connection hanging mid-query wedges that conversation "
        f"indefinitely — on a turn that has already replied."
    )

# ---------------------------------------------------------------------------
# THE FOLD IS TRACED (issue #64) — now through strands.telemetry, not a
# hand-rolled span. `_complete` no longer opens any span itself (see its own
# docstring): `client.complete` — `_DebriefModelClient.complete` in
# production — calls `nora.agent.structured.ask_text`, a real Strands `Agent`
# invocation that Strands instruments from the inside, the same way it always
# has for the tag path's own agent loop.
#
# `_Client`, this file's fake, does NOT call Strands — it returns a scripted
# `Completion` directly, which is why it never produced a span even before
# this change (the OLD `with generation(...)` lived in `_complete` itself,
# independent of the client). So this file's own tests can verify only
# `_complete`'s DELEGATION — that it hands `client.complete` the right
# `trace_attributes` — not the span Strands eventually produces from them.
# That deeper claim ("does `ask_text(trace_attributes=...)` really land on a
# Strands span") is `nora.agent.structured`'s to test, with a real or
# stubbed Agent; it is not covered here, and is a known gap left by this
# change rather than a defect this file was ever positioned to catch.
#
# TWO GUARANTEES THIS STAGE USED TO HOLD ARE GONE, not moved: `trace_safe`'s
# identifier/coordinate screen no longer runs before the fold's real
# prompt/completion reaches a span, because Strands' own event recording
# writes the real message content directly (`Tracer._add_event_messages`) —
# there is no seam left to hand it a pre-screened substitute without
# reintroducing the double-instrumentation this change removes. The fold's
# content now reaches Langfuse exactly as unscreened as the tag path's own
# agent loop already does (`design/13-trace-content-and-redaction.md`,
# `status: open-decision`) — this makes the fold CONSISTENT with that
# existing precedent rather than introducing a new exposure class, but it is
# a real, disclosed behavior change from what `test_an_identifier_never_
# reaches_the_collector` and `test_a_coordinate_in_the_answer_never_reaches_
# the_collector` asserted before this change, and those two tests are
# deleted rather than inverted to assert the new, weaker guarantee silently.
# ---------------------------------------------------------------------------


def _spans(exporter) -> dict[str, object]:
    return {s.name: s for s in exporter.get_finished_spans()}


async def test_the_fold_hands_its_trace_attributes_to_the_client(exported_spans):
    """`_complete` no longer traces the call itself; it must still hand the
    client enough to trace it — `nora.tag_id`, `nora.conversation_id`, and
    `nora.debrief.call` (the replacement for the old per-call span NAME,
    since fold/consolidate share one Strands `agent_name`).

    PROBE: drop `trace_attributes=` from `_complete`'s call to
    `client.complete` and every entry below becomes `None`.
    """
    client = _Client(_ok("a position"))
    await _run(client=client)

    assert client.trace_attributes == [{
        "nora.tag_id": "tag-1", "nora.conversation_id": "g1",
        "nora.debrief.call": "fold",
    }]


async def test_the_consolidate_retry_carries_its_own_label(exported_spans):
    """§4.0's retry is a DIFFERENT call from the fold, and must be labelled as
    one rather than silently reusing `"fold"`."""
    client = _Client(_ok("x" * 60), _ok("short"))
    await _run(client=client, max_chars=40)

    calls = [ta["nora.debrief.call"] for ta in client.trace_attributes]
    assert calls == ["fold", "consolidate"]


async def test_the_folds_outcome_lands_on_the_span_the_pipeline_opened(
        exported_spans):
    """`nora_debrief_total{outcome}` is a series; it cannot answer «what
    happened on THIS tag». The `engagement.debrief` span the PIPELINE opens
    (not `_complete` — this stage annotates it, `annotate()`, not `span()`)
    can, and this is unaffected by the strands.telemetry change: it was
    never part of the retired `generation()` wrapper.

    PROBE: delete the `annotate(...)` in `run`'s `finally`.
    """
    with span("engagement.debrief"):
        await _run(client=_Client(_ok("a position")))
    assert _spans(exported_spans)["engagement.debrief"].attributes[
        "nora.debrief.outcome"] == "written"


async def test_a_refused_fold_says_so_on_the_span(exported_spans):
    """The same attribute, a different value — so the assertion above reads the
    fold rather than a constant. A fold that wrote nothing is exactly the one an
    operator opens the trace for."""
    with span("engagement.debrief"):
        await _run(client=_Client(debrief.Completion("not json at all", "stop")))
    assert _spans(exported_spans)["engagement.debrief"].attributes[
        "nora.debrief.outcome"] == "malformed"


# --- issue #199: a turn outcome is not a room position ----------------------


def _incident_trace(outcome: str = "llm_error") -> TagTrace:
    """The 2026-09-11 shape, as the record holds it (issue #199).

    Miles asked for a route at 16:42; the participation turn answered at
    16:43:57 and the row is `is_nora=1 delivery_status=sent`. He tagged her in
    the same second, having not seen it. The tag turn started 7 seconds later,
    re-derived the route from scratch, and died with `APIError` --
    `outcome="llm_error"`. So her delivered answer is the second-to-last row
    of the window the dead turn was reading, and the tag is the last.
    """
    answer = TranscriptRow(
        id=132, sender_id="nora", sender_name=None, ts=1_757_666_637_000,
        body="聽朝 10 點到數碼港 — 969P", is_nora=True,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    tag = TranscriptRow(
        id=133, sender_id="+852111", sender_name="Miles",
        ts=1_757_666_637_000, body="@Nora 點去", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    return TagTrace(
        tag_id="2301f72a", conversation_id="g1", messages=(),
        transcript=(answer, tag), outcome=outcome,
        created_at=1_757_666_714_000,
    )


def _ok_with_note(body: str) -> debrief.Completion:
    """The fold answering with a position AND the note the live fold raised —
    the shape the objective asked for until 2026-09-24. Kept so the tests can
    show a stray `notes` field reaches nothing."""
    return debrief.Completion(json.dumps({
        "standing": body,
        "notes": [{
            "title": "Miles's route to Cyberport is unanswered",
            "description": "the model call failed and nothing was sent",
            "uncertainty": "whether he ever got a route",
            "closing_condition": "Nora successfully sends a route",
            "anchor_date": 20260912,
            "body": "Miles asked at 16:42 how to get to Cyberport",
        }],
    }), "stop")


async def test_a_dead_turn_never_becomes_a_claim_that_she_never_answered():
    """**The incident, whole** (issue #199). A tag turn died with
    `llm_error`; the fold was handed "the model call failed; nothing was
    sent" beside a transcript whose second-to-last row was her own delivered
    answer, and wrote the ROOM position "I have not sent a single reply
    today". It then proposed a note on the same premise, the verdict returned
    `grounded: true`, and ninety minutes later the proactive pass raised it at
    the operator in a different room.

    What the fold is TOLD is the fix that remains here: the header is scoped
    to the turn and her delivered row is marked as delivered. The stage cannot
    assert what a model concludes, and the render IS the fix, so this is the
    honest place to measure it. (The note refusal went with note proposals on
    2026-09-24, and the grounding-verdict override with the verdict.)
    """
    client = _Client(_ok_with_note("he is waiting on a route"))
    await _run(trace=_incident_trace(), client=client)

    fold_prompt = next(
        p for p in client.prompts if p.startswith("OBJECTIVE-TEXT"))
    assert "this turn's model call failed, so THIS TURN sent nothing" in fold_prompt
    assert "does not describe what any other turn of yours sent" in fold_prompt
    assert 'from="Nora" delivery="delivered">聽朝 10 點到數碼港' in fold_prompt


async def test_a_dead_turn_still_revises_the_position():
    """The asymmetry is deliberate. A turn that died IS worth folding -- "I
    owe him an answer, my turn crashed" is exactly what a position should
    carry."""
    store, metrics = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("my turn died, I owe him an answer")))
    position = await store.read(platform="signal", conversation_id="g1")
    assert position is not None
    assert position.body == "my turn died, I owe him an answer"
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


async def test_the_fold_writes_the_position_and_nothing_else():
    """The debrief compresses the room's position; it does not create notes
    (removed 2026-09-24). It made 52 of the 64 live notes: it never saw the
    unit's open notes and could only create, so every rewording was a new
    note that never got revised.

    Two halves. `run` is handed no note collaborator at all, so there is no
    path from a fold to the note store to leave switched on. And a model that
    still answers in the old shape, `notes` beside `standing`, gets its
    position written and nothing else: the stray field is not an error."""
    import inspect

    params = set(inspect.signature(debrief.run).parameters)
    assert not params & {"notes_sink", "unit_of", "notes_read"}, params
    assert not {"notes_sink", "unit_of"} & {
        f.name for f in dataclasses.fields(debrief.Wiring)}

    store, metrics = await _run(client=_Client(_ok_with_note("a position")))
    assert (await store.read(platform="signal", conversation_id="g1")).body == (
        "a position")
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


# --- design/standing-compact/10 §5 and the review's older gaps (2026-09-24) -


async def test_the_priors_age_is_measured_from_the_last_write_on_the_tag_path():
    """M6 from the review. `written` is only honest if every fold REWRITES
    `updated_at`: a write that kept the prior's would show the second fold a
    position «written» at whatever instant the row was first created, and a
    clause saying «tomorrow» would be re-judged against the wrong day. Two
    folds with an advancing clock; the second must see the first's write."""
    store = await _with_prior(3, updated_at=1)  # a row from long before
    first_write = 1_756_000_931_000
    await _run(store=store, trace=_trace(), now_ms_override=first_write,
               client=_Client(_ok("after the first fold")))
    second_turn = dataclasses.replace(
        _trace(), tag_id="tag-2", created_at=first_write + 5 * 3_600_000)
    client = _Client(_ok("after the second fold"))
    await _run(store=store, trace=second_turn, client=client,
               now_ms_override=second_turn.created_at + 1_000)
    opening = client.prompts[0].split("<standing_prior ", 1)[1].split(">", 1)[0]
    assert 'written="5h ago"' in opening, opening
    assert "after the first fold" in client.prompts[0]
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.updated_at == second_turn.created_at + 1_000


async def test_a_fold_with_an_empty_batch_carries_the_watermark_forward():
    """M5 from the review. The watermark advances on ONE condition — a fold
    that read new rows — and is otherwise carried forward. A fold whose batch
    came back empty still WRITES (the turn's own rows are in the render), and
    a write that dropped the watermark to NULL would make the next fold
    re-read the conversation from its first row."""
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="the carried position",
        fold_count=3, updated_at=100, last_folded_row_id=42))
    await _run(store=store, record_store=FakeRecordStore())
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.body == "a position", "the fold did write"
    assert row.last_folded_row_id == 42


# --- the objective, v6 (design/standing-compact/10 §5) ----------------------


def _objective_body() -> str:
    from tests.objectives import shipped_objectives

    return shipped_objectives().objective_body("standing-debrief")


def test_the_objective_says_a_finding_is_never_a_commitment():
    """§5 «A finding is a finding», and it answers the security review's first
    finding: web text shown at 4000 characters could otherwise be written as
    her own promise, and a promise in `<standing>` is read on every later turn
    as hers."""
    body = _objective_body()
    assert "when and where I found it" in body
    assert (
        "What a page, a search or any tool says is a finding and nothing more: "
        "it is never something I committed to or concluded"
    ) in body
    assert "what I owe is only what I said to the room" in body


def test_the_objective_scopes_the_date_rule_to_a_day_dependent_clause():
    """§5 «The dated rule is scoped». «Write every time as a date»
    contradicted the worked example («而家», «下次») and the exclusion of
    settled dates. The absence check is paired with the presence of its
    replacement, so it cannot pass on an objective that lost both."""
    body = _objective_body()
    assert "When a clause depends on a day" in body
    assert "write the date, not the relative word" in body
    assert "A plan still open keeps its date." in body
    assert "Write every time as a date" not in body


def test_the_objective_describes_the_rollups_input():
    """The event path folds `<conversation>` with no `<steps>`; an objective
    describing only the tag path's input tells the rollup fold to look for
    blocks it was never given."""
    body = _objective_body()
    assert (
        "When you are folding a stretch of the room nobody addressed you in, "
        "you get that stretch (`<conversation>`, with the time you are folding "
        "at) and no steps."
    ) in body


def test_the_objective_ranks_a_finding_between_a_commitment_and_the_room():
    """§5 «A third consolidation rank», in the objective as well as the retry
    prompt: the two texts must not disagree about what to drop first."""
    body = _objective_body()
    rule = (
        "What you CONCLUDED or COMMITTED to outranks what you FOUND and have "
        "not shared, and what you found outranks what the room SAID"
    )
    assert rule in body
    assert "What you CONCLUDED outranks what the room SAID" not in body


# --- the objective, v9 (design/standing-compact/30-working-set.md) --------


def test_the_objective_v9_states_the_working_set_schema_and_its_own_sentence():
    """The objective tells the model what a working-set item IS — one
    sentence, not a menu of cases (`context-framing-beats-capability-listing`,
    `judgement-not-a-menu`) — and the JSON schema gains the key beside
    `standing`."""
    body = _objective_body()
    assert (
        "the rest are a working set — each still worth offering, each with "
        "what it is, where you found it, and what it's for if you know"
    ) in body
    assert '"working_set": [{"what"' in body
    assert "<standing_working_set_prior>" in body


def test_the_objective_v9_does_not_enumerate_working_set_cases():
    """`judgement-not-a-menu`: no bulleted list of "restaurants found by
    web_search", "other candidates from place_info", "alternate time
    slots" — exactly the shape that cost the Relevance Gate accuracy."""
    body = _objective_body()
    assert "restaurants found by web_search" not in body
    assert "other candidates from place_info" not in body


async def test_the_fold_reads_the_rooms_account_the_turn_read():
    """DEC-207: the tag-turn fold is handed the account the turn read at its
    start and renders it above the prior — asserted through the PROMPT, for
    this file's own reason."""
    from nora.domain.culture import CultureRevision

    client = _Client(_ok("revised"))
    await _run(client=client, culture=CultureRevision(
        id="r1", culture_key="g1", body="they decide by attrition",
        written_at=1_756_000_000_000, established_by="dream:culture:test"))
    prompt = client.prompts[0]
    assert "<culture" in prompt and "they decide by attrition" in prompt
    assert prompt.index("<culture") < prompt.index("<standing_prior")


# --- working_set (design/standing-compact/30-working-set.md, issue #445) ---

_TOOL_STEP = (
    {"role": "assistant", "content": [
        {"toolUse": {"name": "web_search", "input": {"q": "chicken hotpot"}}},
    ]},
    {"role": "user", "content": [
        {"toolResult": {"content": [{"text": "three restaurants found"}]}},
    ]},
)


async def test_found_at_is_stamped_by_the_write_path_not_the_model():
    """§3: `found_at` is NEVER read off the model's JSON — the model is not
    even asked for it, and a value it supplies anyway (a rogue or confused
    answer) is silently ignored in favour of the write path's own clock.
    `WorkingSetItem`'s own rule: "there is no second clock"."""
    client = _Client(_ok_ws("a position", [
        {"what": "a restaurant", "source": "web_search", "found_at": 1},
    ]))
    store, _ = await _run(
        client=client, trace=_trace(messages=_TOOL_STEP),
        now_ms_override=1_756_000_931_000,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert len(row.working_set) == 1
    assert row.working_set[0].found_at == 1_756_000_931_000


async def test_a_carried_forward_item_keeps_its_original_found_at():
    """The other half of the same rule: an item the model re-states UNCHANGED
    from its prior (same `what` AND `source`) is a CARRY-FORWARD, not a new
    find, and keeps the `found_at` it already had — without this, TTL
    (render-time, §2) would never fire, because every fold would re-stamp
    every surviving item to the current instant.

    `found_at` is one hour before `now_ms_override`, deliberately WELL
    inside the TTL: a value near epoch would (correctly, since #541's
    review) be dropped by the write path's own TTL check, which is a
    different guard with its own test below.
    """
    now_ms = 1_756_000_931_000
    found_at = now_ms - 3_600_000
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="prior position",
        fold_count=1, updated_at=1, working_set=(
            WorkingSetItem(
                what="a restaurant", source="web_search", found_at=found_at,
                bears_on="dinner",
            ),
        ),
    ))
    client = _Client(_ok_ws("revised", [
        {"what": "a restaurant", "source": "web_search", "bears_on": "dinner"},
    ]))
    await _run(
        store=store, client=client, trace=_trace(messages=()),
        now_ms_override=now_ms,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert len(row.working_set) == 1
    assert row.working_set[0].found_at == found_at


async def test_a_carried_item_past_its_ttl_is_not_written():
    """Review of #541: the write path used to preserve `found_at` on a
    carry-forward WITHOUT checking whether that `found_at` was already past
    the TTL — so a model that kept re-stating a stale item kept it written
    back forever, occupying a budget slot no live item could use. The write
    path now drops a carry-forward past `WORKING_SET_TTL_HOURS`
    deterministically, whatever the model does — this is a SEPARATE guard
    from `render_standing_working_set`'s own read-time filter
    (tests/unit/test_standing.py), enforced where the row is written."""
    from nora.domain.standing import WORKING_SET_TTL_HOURS

    now_ms = 1_756_000_931_000
    stale_found_at = now_ms - int((WORKING_SET_TTL_HOURS + 1) * 3_600_000)
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="prior position",
        fold_count=1, updated_at=1, working_set=(
            WorkingSetItem(
                what="a stale restaurant", source="web_search",
                found_at=stale_found_at, bears_on="dinner",
            ),
        ),
    ))
    client = _Client(_ok_ws("revised", [
        {"what": "a stale restaurant", "source": "web_search",
         "bears_on": "dinner"},
    ]))
    store, metrics = await _run(
        store=store, client=client, trace=_trace(messages=()),
        now_ms_override=now_ms,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == ()
    assert _counter(
        metrics, "nora_standing_working_set_expired_dropped_total") == 1


async def test_a_carried_item_within_its_ttl_is_written():
    """The positive control the test above needs: a carry-forward just
    INSIDE the TTL still survives, so the guard is a boundary check, not an
    accidental drop of every carry-forward."""
    from nora.domain.standing import WORKING_SET_TTL_HOURS

    now_ms = 1_756_000_931_000
    fresh_found_at = now_ms - int((WORKING_SET_TTL_HOURS - 1) * 3_600_000)
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="prior position",
        fold_count=1, updated_at=1, working_set=(
            WorkingSetItem(
                what="a fresh-enough restaurant", source="web_search",
                found_at=fresh_found_at, bears_on="dinner",
            ),
        ),
    ))
    client = _Client(_ok_ws("revised", [
        {"what": "a fresh-enough restaurant", "source": "web_search",
         "bears_on": "dinner"},
    ]))
    store, metrics = await _run(
        store=store, client=client, trace=_trace(messages=()),
        now_ms_override=now_ms,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert len(row.working_set) == 1
    assert row.working_set[0].found_at == fresh_found_at
    assert _counter(
        metrics, "nora_standing_working_set_expired_dropped_total") == 0


async def test_an_item_sourced_to_a_tool_the_fold_was_shown_survives():
    client = _Client(_ok_ws("a position", [
        {"what": "a restaurant the search returned", "source": "web_search",
         "bears_on": "dinner"},
    ]))
    store, metrics = await _run(client=client, trace=_trace(messages=_TOOL_STEP))
    row = await store.read(platform="signal", conversation_id="g1")
    assert len(row.working_set) == 1
    assert row.working_set[0].what == "a restaurant the search returned"
    assert _counter(
        metrics, "nora_standing_working_set_unsourced_dropped_total") == 0


async def test_an_item_whose_source_does_not_match_a_shown_step_is_dropped():
    """§3: `source` is constrained to the SAME `<steps>` the fold was
    actually shown — never a paraphrase of the room's question. An item
    naming a tool this turn never called (or naming nothing at all,
    `"I reasoned it out"`) is dropped rather than written, and counted."""
    client = _Client(_ok_ws("a position", [
        {"what": "a restaurant", "source": "I reasoned it out",
         "bears_on": "dinner"},
    ]))
    store, metrics = await _run(client=client, trace=_trace(messages=_TOOL_STEP))
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == ()
    assert _counter(
        metrics, "nora_standing_working_set_unsourced_dropped_total") == 1


async def test_a_source_that_merely_mentions_a_shown_tool_is_dropped():
    """§3, review of #541: the check is EQUALITY, not a substring match. A
    description that happens to contain a real tool's name —
    `"the room's own web_search question, paraphrased"` — must not pass as
    sourced just because `web_search` appears inside it somewhere; only the
    bare tool name, exactly, is a valid source."""
    client = _Client(_ok_ws("a position", [
        {"what": "a restaurant", "source":
         "the room's own web_search question, paraphrased",
         "bears_on": "dinner"},
    ]))
    store, metrics = await _run(client=client, trace=_trace(messages=_TOOL_STEP))
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == ()
    assert _counter(
        metrics, "nora_standing_working_set_unsourced_dropped_total") == 1


async def test_an_unsourced_item_with_no_tool_calls_this_turn_is_dropped():
    """The degenerate case: a turn that called no tools at all has no shown
    step ANY source could match, so every "new" item is dropped — most
    turns, since most turns call no tools."""
    client = _Client(_ok_ws("a position", [
        {"what": "a restaurant", "source": "web_search"},
    ]))
    store, metrics = await _run(client=client, trace=_trace(messages=()))
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == ()
    assert _counter(
        metrics, "nora_standing_working_set_unsourced_dropped_total") == 1


async def test_an_over_budget_working_set_is_refused_and_the_prior_is_kept():
    """§1: the OPERATING budget, enforced at write — an oversized new working
    set is refused AS A WHOLE and the PRIOR one is kept unchanged, never a
    silent truncation, the same "a coherent stale position beats one cut in
    half" argument §4.0 makes for the body."""
    store = InMemoryStandingStore()
    kept_item = WorkingSetItem(
        what="the prior's own item", source="web_search", found_at=1_000,
    )
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="prior position",
        fold_count=1, updated_at=1, working_set=(kept_item,),
    ))
    # 3 items > working_set_max_items=2, all correctly sourced.
    client = _Client(_ok_ws("revised", [
        {"what": f"item {i}", "source": "web_search"} for i in range(3)
    ]))
    store, metrics = await _run(
        store=store, client=client, trace=_trace(messages=_TOOL_STEP),
        working_set_max_items=2, working_set_max_chars=600,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == (kept_item,)
    assert _counter(
        metrics, "nora_standing_working_set_overflow_refusals_total") == 1
    # The paragraph itself still wrote — only the working set was refused.
    assert row.body == "revised"


async def test_a_working_set_within_budget_is_written_whole():
    """The positive control the refusal test above needs: a working set at
    or under both caps is written exactly as the model gave it, not
    refused."""
    client = _Client(_ok_ws("revised", [
        {"what": "a restaurant", "source": "web_search", "bears_on": "dinner"},
    ]))
    store, metrics = await _run(
        client=client, trace=_trace(messages=_TOOL_STEP),
        working_set_max_items=5, working_set_max_chars=600,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert len(row.working_set) == 1
    assert row.working_set[0].what == "a restaurant"
    assert _counter(
        metrics, "nora_standing_working_set_overflow_refusals_total") == 0


async def test_a_missing_working_set_key_leaves_it_empty():
    """The ordinary state, per the objective's own words — most turns have
    nothing to add. Omitting the key entirely must not raise and must not
    carry anything forward by accident."""
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="prior position",
        fold_count=1, updated_at=1,
        working_set=(WorkingSetItem(
            what="an old item", source="web_search", found_at=1),),
    ))
    client = _Client(_ok("revised"))  # no "working_set" key at all
    store, _ = await _run(store=store, client=client)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == ()


async def test_a_working_set_item_is_screened_like_the_body():
    """§3/§4.3: screened before the write, item by item, exactly like
    `body`. An identifier reaching a working-set item does not get a second,
    unscreened path into the prompt just because it sits in a new field."""
    client = _Client(_ok_ws("revised", [
        {"what": "call them at +85290000101", "source": "web_search"},
    ]))
    store, metrics = await _run(client=client, trace=_trace(messages=_TOOL_STEP))
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.working_set == ()
    assert _counter(
        metrics, "nora_dream_screened_total",
        adaptor="debrief_working_set", reason="identifier") == 1


async def test_the_working_set_rule_names_both_caps_in_the_system_prompt():
    """The operating budget reaches the model as text, the same way
    `max_chars` does for the body — otherwise the model has no way to
    self-manage a limit it is never told."""
    client = _Client(_ok("a position"))
    await _run(
        client=client, working_set_max_items=5, working_set_max_chars=600)
    assert "at most 5 items" in client.prompts[0]
    assert "600 characters combined" in client.prompts[0]
