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
from nora.domain.provenance import instruction_sha256
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_verdict import TurnVerdict
from nora.engagement.stages import debrief
from nora.obs.metrics import Metrics
from nora.obs.tracing import span
from nora.testing import FakeRecordStore, InMemoryStandingStore, InMemoryTurnVerdictStore
from tests.objectives import shipped_objectives

pytestmark = pytest.mark.asyncio


class _Client:
    """Answers a scripted sequence of `Completion`s, one per call, and records
    every prompt it was given. A `BaseException` in the script is RAISED rather
    than returned, which is how the cancellation case is reachable at all."""

    def __init__(self, *script) -> None:
        self._script = list(script)
        self.prompts: list[str] = []

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(self, *, system: str, prompt: str) -> debrief.Completion:
        self.prompts.append(f"{system}\n\n{prompt}")
        item = self._script.pop(0) if self._script else debrief.Completion("{}", "stop")
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeSink:
    """Records batches. `propose` is all-or-nothing and every failure ESCAPES —
    no short id list — so a raising variant is how the refusal path is tested."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.batches: list[list] = []
        self._raises = raises

    async def propose(self, *, key: str, proposals, now_ms: int):
        if self._raises is not None:
            raise self._raises
        self.batches.append(list(proposals))
        return [f"note-{i}" for i, _ in enumerate(proposals)]

    async def supersede(self, **kw) -> int:
        return 0


class _Objective:
    """Answers per call: the position half and the judge half get their own
    texts, so a prompt can be attributed to the completion that carried it."""

    def objective_body(self, name: str) -> str:
        assert name in {debrief.OBJECTIVE, debrief.VERDICT_OBJECTIVE}
        return "OBJECTIVE-TEXT" if name == debrief.OBJECTIVE else "VERDICT-TEXT"


def _ok(body: str) -> debrief.Completion:
    """Call A's answer: the STANDING shape only (issue #78 Phase 2). Spilling
    `grounded` back into this response would fake a one-completion fold back
    into existence — the verdict has its own call, and its own helper."""
    return debrief.Completion(json.dumps({"standing": body}), "stop")


def _grounded(*, grounded: bool = True, unsupported=()) -> debrief.Completion:
    """Call B's answer — its own completion since issue #78 Phase 2."""
    return debrief.Completion(json.dumps({
        "grounded": grounded, "unsupported": list(unsupported),
    }), "stop")


def _trace(outcome: str = "send_reply") -> TagTrace:
    row = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Miles", ts=1_756_000_920_000,
        body="去邊食", is_nora=False, delivery_status=DeliveryStatus.SENT,
        raw_json=None,
    )
    return TagTrace(
        tag_id="tag-1", conversation_id="g1", messages=(), transcript=(row,),
        outcome=outcome, created_at=1_756_000_930_000,
    )


def _trace_with_tag(tag_id: str) -> TagTrace:
    """A distinct turn. `TurnVerdict` is keyed on `tag_id` and NEVER rewritten,
    so re-using one across turns makes the second save a duplicate — caught and
    counted, but it would leave a multi-turn test asserting on a verdict that
    was never stored."""
    return dataclasses.replace(_trace(), tag_id=tag_id)


_UNSET = object()


async def _run(*, store=None, store_verdict=_UNSET, client=None, metrics=None,
               max_chars=1200, timeout_seconds=5.0, store_timeout_seconds=5.0,
               rebuild_after_folds=20, notes_read=frozenset(),
               now_ms_override=None, notes_sink=_UNSET,
               unit_of=None, trace=None, record_store=None,
               fold_batch=200) -> tuple:
    store = store if store is not None else InMemoryStandingStore()
    # `_UNSET`, not `None`: `None` is a REAL state the stage documents as
    # "the ordinary state on any deployment that has not wired one", and a
    # helper that substitutes a store for it makes that branch untestable.
    store_verdict = (InMemoryTurnVerdictStore() if store_verdict is _UNSET
                     else store_verdict)
    metrics = metrics if metrics is not None else Metrics()
    await debrief.run(
        trace=trace if trace is not None else _trace(),
        platform="signal", store=store, store_verdict=store_verdict,
        client=client if client is not None else _Client(_ok("a position")),
        objective=_Objective(), metrics=metrics, display_name="Nora",
        record_store=record_store if record_store is not None else FakeRecordStore(),
        max_chars=max_chars, fold_batch=fold_batch,
        timeout_seconds=timeout_seconds,
        store_timeout_seconds=store_timeout_seconds,
        rebuild_after_folds=rebuild_after_folds, notes_read=notes_read,
        notes_sink=(_FakeSink() if notes_sink is _UNSET else notes_sink),
        unit_of=unit_of or (lambda p, c: "ws"),
        now_ms=now_ms_override if now_ms_override is not None else 1_756_000_931_000,
    )
    return store, metrics, store_verdict


def _counter(metrics: Metrics, name: str, **labels) -> float:
    return metrics.get(name, **labels)


# --- the happy path and the non-negotiable ---------------------------------

async def test_a_fold_writes_the_position_keyed_on_platform_and_conversation():
    store, metrics, _sv = await _run()
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
    _, metrics, _sv = await _run(store=store, client=client)
    assert "They are planning dinner for the 9th." in client.prompts[0]
    assert _counter(metrics, "nora_debrief_prior_total", prior="read") == 1
    # Counted from the prior it read, not from zero — the ceiling half of S2's
    # re-derivation trigger has to count folds-since-rebuild.
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.fold_count == 5


async def test_a_first_fold_is_counted_as_absent_rather_than_read():
    _, metrics, _sv = await _run()
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
    _, metrics, _sv = await _run(store=store)
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
    store, metrics, _sv = await _run(trace=_trace(outcome))
    assert await store.read(platform="signal", conversation_id="g1") is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


# --- §4.0: refuse and consolidate, never truncate --------------------------

async def test_an_over_long_fold_is_refused_and_the_model_is_asked_to_consolidate():
    client = _Client(_ok("x" * 60), _ok("short"))
    store, metrics, _sv = await _run(client=client, max_chars=50)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.body == "short"
    # 1: standing first (over-long), 2: the consolidate retry. The judge is a
    # THIRD prompt (its own call); only the standing half ever retries.
    assert len(client.prompts) == 3
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
    """
    client = _Client(_ok("x" * 60), _ok("short"))
    await _run(client=client, max_chars=50)
    retry = client.prompts[1]
    assert "never the most recent" in retry, "the recency rule went missing"
    assert "What you CONCLUDED outranks what the room SAID" in retry, (
        "the reversed tiebreak is not in the retry, so a shrink could still "
        "drop Nora's own judgement in favour of a room quote"
    )
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
    assert "What you CONCLUDED outranks what the room SAID" in body
    assert "two things of the same kind" in body
    assert "What the room SAID outranks what you concluded" not in body


def test_the_objective_speaks_in_the_first_person_about_the_position():
    """The person contract (issue #78): the fold is Nora revising HER OWN
    position, so the objective must speak in the first person about the
    standing — a third-person cast ("you are not the assistant who answered
    it") is what produced transcript recaps instead of positions, because an
    observer who is not a participant has no position to revise.

    The judge is now its OWN objective (Phase 2) and is deliberately
    different: it grades the turn's SOURCING, and its impartiality comes from
    being a different call (26-standing §1), not from a third-person cast. So
    the standing objective must say "you" for the position, the verdict
    objective must keep the sourcing rubric's own-words requirement, and NEITHER
    may carry the other's job again — a one-completion fold drifting back
    would put the judge in the position writer's prompt.
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
    # The verdict half moved OUT of the position writer's instructions in
    # Phase 2: the sourcing rubric must not ride next to `<standing_prior>`
    # any more.
    assert "GROUNDED" not in body, (
        "the judging rubric is back in the standing objective — the verdict "
        "has its own call and its own objective"
    )
    verdict = shipped_objectives().objective_body(debrief.VERDICT_OBJECTIVE)
    assert "Your ONLY job is to judge whether the turn was GROUNDED" in verdict
    assert "a claim about who has a source or it does not" in verdict
    # The judge does not raise notes: they ride with the standing call.
    assert "notes" not in verdict


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
    _, metrics, _sv = await _run(store=store, client=client, max_chars=50)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row.body == "the old position"
    assert row.fold_count == 1
    assert _counter(
        metrics, "nora_standing_overflow_refusals_total", stage="final") == 1
    assert _counter(metrics, "nora_debrief_total", outcome="refused_overflow") == 1


async def test_a_fold_exactly_at_the_cap_is_written():
    store, _, _ = await _run(client=_Client(_ok("x" * 50)), max_chars=50)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and len(row.body) == 50


# --- the raw completion's own failure shapes -------------------------------

async def test_a_truncated_body_is_never_parsed():
    """The price of admission for a raw `chat.completions.create`. A Strands
    agent RAISES `MaxTokensReachedException` on a cut-off; this returns
    `finish_reason="length"` with a silently truncated body — and a truncated
    JSON document can parse into something shorter and wrong. That is R-27's
    substitution one layer down: an ERROR reported as a PASS.

    The scripted body here is deliberately VALID JSON, so a version of this
    module that parsed first and checked `finish_reason` afterwards (or never)
    goes green on the parse and red on this test.
    """
    store, metrics, _sv = await _run(client=_Client(
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
    store, metrics, _sv = await _run(
        client=_Client(debrief.Completion(content, "stop")))
    assert await store.read(platform="signal", conversation_id="g1") is None
    assert _counter(metrics, "nora_debrief_total", outcome="malformed") == 1


async def test_unavailable_and_malformed_are_different_readings():
    """R-27's discipline as an ASSERTION rather than as a comment. Both write
    nothing, and collapsing them would leave the one instrument this stage has
    unable to tell «the call was cut off» (raise the ceiling, shrink the input)
    from «the model answered in the wrong shape» (fix the objective text)."""
    _, cut, _ = await _run(client=_Client(debrief.Completion('{"standing":"x"}', "length")))
    _, bad, _ = await _run(client=_Client(debrief.Completion("nonsense", "stop")))
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
    store, metrics, _sv = await _run(client=_Client(_ok(body)))
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
    _, metrics, _sv = await _run(client=_Client(asyncio.CancelledError()))
    # The property is ABSORBED — `_run` returned rather than propagating. The
    # label is `cancelled` rather than `error` (see
    # `test_a_cancellation_is_not_counted_as_an_error`); asserting the total
    # here keeps this test about the escape, so narrowing the `except` still
    # turns it red regardless of how the exit is labelled.
    assert _counter(metrics, "nora_debrief_total") == 1


async def test_an_arbitrary_failure_is_absorbed():
    _, metrics, _sv = await _run(client=_Client(RuntimeError("boom")))
    assert _counter(metrics, "nora_debrief_total", outcome="error") == 1


async def test_a_slow_fold_is_cut_at_its_own_timeout():
    """The debrief holds the slot, so an unbounded fold is not a slow
    background job — it is a room that stops answering. Its own label, because
    this is the reading `[standing] timeout_seconds` is tuned against."""
    class _Slow(_Client):
        async def complete(self, *, system: str, prompt: str):
            await asyncio.sleep(5)
            raise AssertionError("should have been cut off")

    _, metrics, _sv = await _run(client=_Slow(), timeout_seconds=0.05)
    assert _counter(metrics, "nora_debrief_total", outcome="timeout") == 1


async def test_the_two_calls_share_one_budget():
    """One `wait_for` around BOTH calls, never one around each: the budget the
    slot can afford is a TOTAL, and two independently-bounded calls add up to
    twice it on exactly the turn that was already slow enough to need a retry.

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
        async def complete(self, *, system: str, prompt: str):
            self.prompts.append(f"{system}\n\n{prompt}")
            await asyncio.sleep(0.06)          # under a 0.10s per-call bound...
            # Keyed on the prompt's SHAPE, not the call count: the two calls
            # now interleave (issue #78 Phase 2), so `len(self.prompts)` at
            # answer time is whichever task happened to resume, not which
            # call this is. The first STANDING answer overflows, forcing the
            # consolidate retry; the judge is never retried and needs none.
            if "consolidate" not in prompt and "<standing_prior " in prompt:
                return _ok("x" * 60)
            return _ok("short")

    # 0.06 (first standing) + 0.06 (its retry) = 0.12 > 0.10 shared. Under two
    # INDEPENDENT 0.10s budgets both standing calls fit and the fold SUCCEEDS —
    # so `timeout` here is only reachable when the budget is genuinely shared.
    # The judge rides the same gather and finishes beside the first call.
    store, metrics, _sv = await _run(
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
    _, metrics, _sv = await _run(client=client())
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
    # Phase 2: the judge's objective reaches CALL B, read fresh the same way —
    # both objectives hot-reload, so neither may be cached.
    assert client.prompts[1].startswith("VERDICT-TEXT")
    assert "OBJECTIVE-TEXT" not in client.prompts[1]


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

    _, metrics, _sv = await asyncio.wait_for(
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

    _, metrics, _sv = await asyncio.wait_for(
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

    _, metrics, _sv = await asyncio.wait_for(
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
    _, metrics, _sv = await _run(client=_Client(asyncio.CancelledError()))
    assert _counter(metrics, "nora_debrief_total", outcome="cancelled") == 1
    assert _counter(metrics, "nora_debrief_total", outcome="error") == 0


# --- issue #78 Phase 2: TWO completions, one fold --------------------------
#
# The fold was ONE model completion carrying both the position and the
# self-judgement — the structural source of third-person recap rows and 21/21
# `grounded=true`. It is now two completions under one `asyncio.gather`.


async def test_the_fold_issues_two_completions_with_their_own_objectives():
    """AC1/AC3, through the model client: exactly two completions, the
    standing one carrying its objective and the `max_chars` cap, the judge
    carrying its own objective and NO cap — the cap is the standing ROW's
    constraint and a verdict is never injected into a prompt."""
    client = _Client(_ok("a position"), _grounded())
    await _run(client=client, max_chars=1200)
    assert len(client.prompts) == 2, "the fold must be TWO completions"
    assert client.prompts[0].startswith("OBJECTIVE-TEXT")
    assert "at most 1200 characters" in client.prompts[0]
    assert client.prompts[1].startswith("VERDICT-TEXT")
    assert "at most 1200 characters" not in client.prompts[1]


async def test_the_verdict_sees_the_whole_turn_and_the_standing_sees_the_prior():
    """AC2, both shapes at once: Call B's prompt carries the turn — outcome
    labelled, transcript, reasoning — and NEVER `<standing_prior>`; Call A's
    prompt carries `<standing_prior>`. Handing the judge the position it is
    about to write is the prompt pollution the split exists to remove: it
    would grade the position, not the turn."""
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="the old position",
        fold_count=1, updated_at=1,
    ))
    client = _Client(_ok("a position"), _grounded())
    await _run(store=store, client=client)
    standing, verdict = client.prompts[0], client.prompts[1]
    assert "<standing_prior " in standing
    assert "<standing_prior " not in verdict
    assert 'outcome="send_reply"' in verdict
    assert "<what_happened " in verdict
    assert "<transcript " in verdict and "<reasoning " in verdict
    # The SAME turn labelling the standing call reads — one turn block, two
    # consumers, no disagreement about what happened.
    assert 'outcome="send_reply"' in standing


async def test_the_two_calls_start_before_either_finishes():
    """AC4, mock timing: `asyncio.gather` starts BOTH calls before either
    ANSWERS, so the fold's wall time is max(A, B), not A + B. A sequential
    fold — one `await` then the other — produces the SUM, and both the event
    order and the bounded wall time below would go red under it."""
    events: list[str] = []

    class _Stagger(_Client):
        async def complete(self, *, system: str, prompt: str):
            events.append("start")
            await asyncio.sleep(0.10)
            events.append("end")
            return (
                _ok("a position") if "<standing_prior " in prompt
                else _grounded()
            )

    # A `perf_counter` bound used to stand beside this: `elapsed < 0.18` for
    # two overlapped 0.10s sleeps, 80ms of slack (#180). That is a machine
    # measurement dressed as a concurrency assertion — starvation makes
    # `elapsed` grow, so it gets MORE likely to fail exactly when the machine
    # is loaded, never less. The `events` order below is the grounded
    # property: it directly proves both calls started before either
    # finished, which is the only thing "the two calls start before either
    # finishes" claims, and a sequential fold cannot produce it regardless of
    # how fast the machine is.
    await _run(client=_Stagger())
    assert events == ["start", "start", "end", "end"], (
        f"the calls did not overlap: {events} — the second completed only "
        "after the first finished, so latency is the SUM of the two"
    )


# --- the per-turn verdict, 26 §3's second output ---------------------------

async def test_a_fold_writes_the_turn_verdict_beside_the_position():
    """Two CALLS' outputs (26 §3, split by issue #78 Phase 2), with opposite
    cardinalities: the position is one row per conversation revised in place,
    the verdict is one row per turn never rewritten."""
    store, metrics, verdicts = await _run(client=_Client(
        _ok("a position"),
        _grounded(grounded=False, unsupported=("claimed a closing time",))))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    v = await verdicts.read(tag_id="tag-1")
    assert v is not None and v.grounded is False
    assert v.unsupported == ("claimed a closing time",)
    assert _counter(metrics, "nora_turn_verdict_total", grounded="false") == 1


async def test_a_verdict_failure_never_costs_the_position():
    """**The asymmetry, and it is deliberate.** The position is what the NEXT
    TURN reads; the verdict is read by a re-derivation pass that does not exist
    yet. So a verdict that cannot be written must not undo a position that was
    written successfully — a partial result beats none.

    Falsification: move the verdict save above the position write, or let its
    failure set `outcome`, and this goes red.
    """
    class _Broken(InMemoryTurnVerdictStore):
        async def save(self, *, verdict):
            raise RuntimeError("verdict store down")

    store, metrics, _ = await _run(
        store_verdict=_Broken(),
        client=_Client(_ok("a position"),
                       _grounded(grounded=False, unsupported=("x",))))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_turn_verdict_total", grounded="unwritten") == 1


async def test_a_failing_verdict_call_never_costs_the_position():
    """The split's own asymmetric-error invariant: the judge is a CALL now, so
    its call can fail the way the position's ever did — and it must degrade to
    no verdict, not to a failed fold. The position writes; the disposition
    rides the verdict's own series, labelled `unavailable` (R-27: «I could not
    check» — NOT `absent`, which blames the objective text for an
    infrastructure outage).

    Falsification: let `_judge` re-raise into the gather, or relabel the fold
    from a judge failure, and this goes red."""
    store, metrics, _ = await _run(client=_Client(
        _ok("a position"), RuntimeError("model unreachable")))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_turn_verdict_total", grounded="unavailable") == 1
    assert _counter(metrics, "nora_turn_verdict_total", grounded="absent") == 0


async def test_a_cut_off_verdict_is_unavailable_and_never_a_partial_verdict():
    """The verdict call gets `_check`'s finish_reason-FIRST refusal too: a
    `"length"` finish degrades to no verdict and is never parsed — a truncated
    judge answer parsing to `{"grounded": true}` would otherwise become a
    NEVER-REWRITTEN row asserting a judgement nobody formed."""
    store, metrics, verdicts = await _run(client=_Client(
        _ok("a position"),
        debrief.Completion('{"grounded": tru', "length")))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_turn_verdict_total", grounded="unavailable") == 1
    assert await verdicts.read(tag_id="tag-1") is None


async def test_a_missing_verdict_is_absent_not_a_failure():
    """The fold revised the position but the judge answered nothing usable. A
    READING about the objective text, not a fault — and never folded in with
    `unwritten`, which is the database refusing (R-27: those two send the next
    investigation to different places)."""
    store, metrics, verdicts = await _run(client=_Client(
        _ok("a position"), debrief.Completion("not json", "stop")))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert await verdicts.read(tag_id="tag-1") is None
    assert _counter(metrics, "nora_turn_verdict_total", grounded="absent") == 1
    assert _counter(metrics, "nora_turn_verdict_total", grounded="unwritten") == 0


async def test_a_self_contradictory_verdict_is_dropped_not_written():
    """`grounded=true` with unsupported claims is a contradiction. The domain
    type refuses it, the stage degrades to no verdict rather than raising, and
    the position still lands — because this column is what S2's re-derivation
    trigger reads and a trigger firing off a contradictory row is worse than
    one that never fires."""
    store, metrics, verdicts = await _run(client=_Client(
        _ok("a position"),
        _grounded(grounded=True, unsupported=("but also this",))))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert await verdicts.read(tag_id="tag-1") is None
    assert _counter(metrics, "nora_turn_verdict_total", grounded="absent") == 1


async def test_the_verdict_comes_from_the_verdict_call_not_the_standing_call():
    """Issue #78 Phase 2's provenance rule: the two halves are different
    completions, so the verdict parses from Call B's answer even when Call A's
    raw answer ALSO carries a verdict-shaped payload. Provenance is asserted
    with a DECOY — a fold that ever parses the verdict off the standing
    completion again feeds the decoy to the store and this goes red.

    (The single-completion fold tested this as «the verdict comes from the
    kept consolidate answer»; the split removes the premise — the verdict
    never rides on the position's completion at all.)"""
    client = _Client(
        debrief.Completion(json.dumps({
            "standing": "a position", "grounded": False,
            "unsupported": ["decoy: parsed from the standing call"],
        }), "stop"),
        _grounded(grounded=False, unsupported=("parsed from the verdict call",)),
    )
    _, _, verdicts = await _run(client=client)
    v = await verdicts.read(tag_id="tag-1")
    assert v is not None and v.unsupported == ("parsed from the verdict call",)


# --- S2's re-derivation trigger --------------------------------------------

# The two clocks are EXPLICIT in these helpers because their ORDER is the
# subject: the trigger fires on an ungrounded verdict written SINCE the position
# was last revised. Both used to default to 1, so `created_at > updated_at` was
# false and the tests could not tell a fresh verdict from a stale one.
async def _with_prior(fold_count: int, *, body: str = "the carried position",
                      updated_at: int = 100, consumed_verdict_tag: str | None = None):
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body=body,
        fold_count=fold_count, updated_at=updated_at,
        consumed_verdict_tag=consumed_verdict_tag))
    return store


class _BlindableVerdicts:
    """`latest` raises while `blind` — R-27's «I could not check», the only
    state that distinguishes a watermark from a timestamp comparison.

    A double that could not fail this way is why the comparison shipped: every
    test drove a verdict store that always answered, so the one path that
    silently loses a verdict was unreachable through the suite.
    """

    def __init__(self, inner) -> None:
        self.inner, self.blind = inner, False

    async def latest_ungrounded(self, *, conversation_id: str):
        if self.blind:
            raise RuntimeError("verdict store unavailable")
        return await self.inner.latest_ungrounded(conversation_id=conversation_id)

    async def save(self, *, verdict) -> None:
        await self.inner.save(verdict=verdict)

    async def read(self, *, tag_id: str):
        return await self.inner.read(tag_id=tag_id)


async def _with_verdict(*, grounded: bool, created_at: int = 200,
                        tag_id: str = "earlier"):
    verdicts = InMemoryTurnVerdictStore()
    await verdicts.save(verdict=TurnVerdict(
        tag_id=tag_id, conversation_id="g1", grounded=grounded,
        unsupported=() if grounded else ("invented a price",),
        created_at=created_at))
    return verdicts


async def test_an_ungrounded_previous_turn_rebuilds_the_position():
    """S2's stronger trigger. The prior is READ but withheld from the prompt,
    so a drifted body cannot carry itself forward through the very fold meant
    to correct it."""
    client = _Client(_ok("a rebuilt position"))
    _, metrics, _ = await _run(
        store=await _with_prior(1), store_verdict=await _with_verdict(grounded=False),
        client=client)
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1
    assert "the carried position" not in client.prompts[0]
    # 26 §4's non-negotiable: it still READ the prior.
    assert _counter(metrics, "nora_debrief_prior_total", prior="read") == 1


async def test_the_fold_count_ceiling_rebuilds_the_position():
    _, metrics, _ = await _run(
        store=await _with_prior(20), store_verdict=await _with_verdict(grounded=True),
        rebuild_after_folds=20)
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ceiling") == 1


async def test_ungrounded_wins_when_both_conditions_hold():
    """**The measurement, not a preference.** 26 §6 reads the by-trigger split
    to ask whether the trigger has degraded into a clock. Labelling a turn that
    actually went wrong as `ceiling` manufactures exactly that false reading —
    the ceiling would have fired eventually anyway."""
    _, metrics, _ = await _run(
        store=await _with_prior(99), store_verdict=await _with_verdict(grounded=False),
        rebuild_after_folds=20)
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ceiling") == 0


async def test_a_grounded_previous_turn_below_the_ceiling_does_not_rebuild():
    client = _Client(_ok("a revised position"))
    _, metrics, _ = await _run(
        store=await _with_prior(3), store_verdict=await _with_verdict(grounded=True),
        client=client, rebuild_after_folds=20)
    assert _counter(metrics, "nora_standing_rederivations_total") == 0
    assert "the carried position" in client.prompts[0]


async def test_the_first_fold_of_a_conversation_is_not_a_rederivation():
    """`prior is None` builds from the window regardless — there is nothing to
    carry forward and nothing to correct. Counting it would drown the signal in
    the one event every conversation has exactly once."""
    store, metrics, _ = await _run()
    # **These two assertions alone were satisfied by a CRASH** — remove
    # `if prior is None: return None` from `_should_rebuild` and the stage
    # raises AttributeError on NoneType.fold_count, writes nothing, lands
    # outcome="error", and both still hold. Found by an independent reviewer
    # mutating the clause; R-72 is pinned to this test and it measured nothing.
    assert _counter(metrics, "nora_standing_rederivations_total") == 0
    assert _counter(metrics, "nora_debrief_prior_total", prior="absent") == 1
    # So assert the fold actually SUCCEEDED. A test whose assertions are all
    # true of a crash is not a test.
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert await store.read(platform="signal", conversation_id="g1") is not None


async def test_a_rebuild_resets_the_fold_count():
    """Or the ceiling fires on every turn forever after the first time — a
    trigger that latches on is a clock with extra steps."""
    store = await _with_prior(20)
    await _run(store=store, store_verdict=await _with_verdict(grounded=True),
               rebuild_after_folds=20)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.fold_count == 1


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
    assert row.fold_count == 4  # fold_count still increments as today


async def test_a_rebuild_does_not_advance_the_watermark():
    """Re-derivation (the `ungrounded` / `ceiling` trigger) re-reads the SAME
    stretch — it must NOT advance the watermark, or it would skip the stretch
    it was re-deriving (50-review finding D). The write carries the prior's
    watermark forward unchanged."""
    store = await _with_prior(20, body="the carried position")
    object.__setattr__(
        (await store.read(platform="signal", conversation_id="g1")),
        "last_folded_row_id", 42,
    ) if False else None
    # Write a prior WITH a watermark directly, then force an ungrounded rebuild.
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="the carried position",
        fold_count=20, updated_at=100, consumed_verdict_tag=None,
        last_folded_row_id=42))
    record = FakeRecordStore()
    for i in range(1, 4):
        await record.append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", i, f"m{i}", {}))
    await _run(
        store=store, store_verdict=await _with_verdict(grounded=False),
        record_store=record, rebuild_after_folds=20,
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.consumed_verdict_tag == "earlier"
    assert row.last_folded_row_id == 42, (
        "a rebuild re-reads the same stretch and must NOT advance the watermark"
    )


async def test_an_unreadable_verdict_store_does_not_rebuild():
    """R-27: an unreadable verdict is «I could not check». Re-deriving on it
    would throw a position away because an instrument was down — a worse
    outcome than carrying a possibly-drifted one for one more turn."""
    class _Broken(InMemoryTurnVerdictStore):
        async def latest_ungrounded(self, **kw):
            raise RuntimeError("verdict store down")

    client = _Client(_ok("a revised position"))
    _, metrics, _ = await _run(
        store=await _with_prior(1), store_verdict=_Broken(), client=client)
    assert _counter(metrics, "nora_standing_rederivations_total") == 0
    assert "the carried position" in client.prompts[0]


# --- 26 §7's read-set, threaded from R-55's latch ---------------------------

async def test_the_notes_the_turn_read_reach_the_debrief():
    """§7: a proposal against a note the tag path already read and DECLINED to
    touch must be refusable. Nothing consumes the set yet — the note-proposal
    output is blocked on the store's create-shaped payload — so this pins that
    it ARRIVES, which is the half that rots silently."""
    _, metrics, _ = await _run(notes_read=frozenset({"dinner-on-the-9th", "big-walk"}))
    assert _counter(metrics, "nora_debrief_notes_read_total") == 2


async def test_a_turn_that_read_no_notes_counts_nothing():
    """The ordinary case — most turns read no notes — and it must not look like
    a wiring failure."""
    store, metrics, _ = await _run()
    # POSITIVE CONTROL. `== 0` is also true of a fold that never ran, and this
    # test had none — the same shape as the three the cold review found in the
    # new work, missed in the same file (cold review, 2026-08-28).
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert await store.read(platform="signal", conversation_id="g1") is not None
    assert _counter(metrics, "nora_debrief_notes_read_total") == 0


async def test_one_ungrounded_verdict_causes_exactly_one_rebuild():
    """**The latch.** Verified live by an independent reviewer before this
    existed: one ungrounded verdict plus three verdict-less folds produced
    THREE rebuilds, `fold_count` pinned at 1, and
    `nora_standing_rederivations_total{trigger="ungrounded"}` climbing — which
    26 §6 reads as the trigger being healthy. Standing silently stopped being a
    rolling position and became a per-turn window summary.

    A verdict is written only when the fold fully succeeds, so verdict-less
    turns are the CORRECT state, not a fault — and the read has to cope with
    them. Second fold here revises rather than rebuilds, because the verdict is
    no longer newer than the position.

    Falsification: drop `last.created_at > prior.updated_at` and the second
    fold rebuilds too.
    """
    store = await _with_prior(1, updated_at=100)
    verdicts = await _with_verdict(grounded=False, created_at=200)
    metrics = Metrics()

    # BOTH folds emit NO verdict — that is the scenario, and it is the
    # ordinary one: a fold only writes a verdict when it fully succeeds, so a
    # timeout, an overflow or a screen leaves none. `_ok()` defaults to
    # grounded=true, which would write a FRESH verdict and mask the latch
    # entirely — the first version of this test did exactly that and passed
    # with the guard removed.
    def _no_verdict(body: str) -> debrief.Completion:
        return debrief.Completion(json.dumps({"standing": body}), "stop")

    # Turn N+1: the ungrounded verdict is newer than the position -> rebuild.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_no_verdict("rebuilt once")), now_ms_override=300)
    # Turn N+2: still no verdict, and the position is now NEWER than the stale
    # ungrounded one. Without the guard this rebuilds again, and again, forever.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_no_verdict("revised after")), now_ms_override=400)

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1, (
        "the stale verdict latched the trigger on"
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.fold_count == 2, (
        "fold_count pinned at 1 means the position never accumulates"
    )


async def test_the_trigger_fires_on_timestamps_a_real_turn_can_produce():
    """**The fixture that cannot occur.** Every other test of this trigger seeds
    `updated_at=100` / `created_at=200` by hand — a pair NO production turn
    reaches. `pipeline._debrief` stamps ONE `int(time.time() * 1000)` per turn
    and hands it to `run`, which writes the position with `updated_at=now_ms`
    and the verdict with `created_at=now_ms`, so on every fully-successful fold
    the two are EXACTLY EQUAL. A strict `>` therefore refuses the one sequence
    the guard exists for, and the ungrounded half of R-72 is unreachable — the
    counter reads flat zero, which 26 §6 interprets as "the trigger has
    degraded into a clock". The same false reading the original defect
    manufactured, arrived at from the other side.

    So this drives the REAL `run` twice and lets it stamp both clocks itself.
    A hand-seeded pair proves nothing about a comparison whose whole subject is
    where those numbers come from; a fixture that cannot occur is the same
    family as a test that cannot fail (cold review, 2026-08-28).

    Falsification: `>=` back to `>` and the second turn does not rebuild.
    """
    store = InMemoryStandingStore()
    verdicts = InMemoryTurnVerdictStore()
    metrics = Metrics()

    # Turn 1 — no prior, so no trigger is evaluated. It fully succeeds and is
    # UNGROUNDED, which is the only way a real ungrounded verdict is ever
    # written: position and verdict, one call, one clock.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("turn one"),
                              _grounded(grounded=False,
                                        unsupported=("invented a price",))),
               now_ms_override=1000)
    position = await store.read(platform="signal", conversation_id="g1")
    written = await verdicts.latest_ungrounded(conversation_id="g1")
    assert position is not None and written is not None
    assert written.created_at == position.updated_at, (
        "the premise: one turn stamps both clocks from one value"
    )
    assert _counter(metrics, "nora_standing_rederivations_total") == 0

    # Turn 2 — the position and the ungrounded verdict carry the SAME
    # timestamp, and this is the turn the guard exists to catch.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("turn two")),
               now_ms_override=2000, trace=_trace_with_tag("tag-2"))

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1, (
        "an ungrounded verdict from the immediately preceding turn did not "
        "trigger a rebuild — the guard refuses the equality every real turn "
        "produces"
    )
    rebuilt = await store.read(platform="signal", conversation_id="g1")
    assert rebuilt is not None and rebuilt.fold_count == 1, (
        "a rebuild RESETS the fold count; 2 means it revised instead"
    )


async def test_the_rebuilt_turn_does_not_rebuild_again_on_the_next(
):
    """The other half, on production timestamps: `>=` must not reinstate the
    latch. The shipped bug was position-written-verdict-not, which advances
    `updated_at` PAST the stale verdict — so a third turn whose fold writes no
    verdict must revise, not rebuild."""
    store = InMemoryStandingStore()
    verdicts = InMemoryTurnVerdictStore()
    metrics = Metrics()

    def _no_verdict(body: str) -> debrief.Completion:
        return debrief.Completion(json.dumps({"standing": body}), "stop")

    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("turn one"),
                              _grounded(grounded=False,
                                        unsupported=("invented a price",))),
               now_ms_override=1000)
    # Turn 2 rebuilds (verdict is as new as the position) and emits NO verdict
    # of its own, so the stale ungrounded one remains the newest there is.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_no_verdict("turn two")),
               now_ms_override=2000, trace=_trace_with_tag("tag-2"))
    # Turn 3: the position was revised at 2000, the verdict still says 1000.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_no_verdict("turn three")),
               now_ms_override=3000, trace=_trace_with_tag("tag-3"))

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1, (
        "the stale verdict latched the trigger on"
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.fold_count == 2, (
        "fold_count pinned at 1 means the position never accumulates"
    )


async def test_a_verdict_the_watermark_names_has_been_acted_on():
    """The guard from the other side: a verdict a re-derivation already
    consumed does not trigger a second one. Keyed on the WATERMARK, not on
    which row is newer — see the next test for why that distinction is the
    whole reason this column exists."""
    store = await _with_prior(1, updated_at=500, consumed_verdict_tag="earlier")
    _, metrics, _ = await _run(
        store=store,
        store_verdict=await _with_verdict(grounded=False, created_at=200))
    assert _counter(metrics, "nora_standing_rederivations_total") == 0
    # POSITIVE CONTROL. Every assertion above is also true of a fold that
    # never ran, and this test had none — the same signature as the decorative
    # ones this file already records. The fold must have happened AND revised.
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.fold_count == 2, (
        "a revise did not happen, so nothing was proven about the trigger"
    )


async def test_an_older_unconsumed_verdict_still_rebuilds():
    """**The case a timestamp comparison gets wrong, stated directly.** An
    ungrounded verdict that PREDATES the position's last write has still not
    been acted on if no re-derivation ever consumed it, and it must fire.

    The shipped comparison asserted the opposite — that older meant handled —
    because it used the position's `updated_at` as a proxy for "a rebuild
    consumed this verdict". The test that guarded it asserted the proxy rather
    than the property, so it went green on the defect."""
    _, metrics, _ = await _run(
        store=await _with_prior(1, updated_at=500),          # watermark: None
        store_verdict=await _with_verdict(grounded=False, created_at=200))
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1


async def test_one_unreadable_verdict_does_not_lose_the_verdict_forever():
    """**Case B — the defect that made this a column instead of an operator.**

    `latest` raising is R-27's «I could not check», and the trigger is right to
    decline: re-deriving on a down instrument throws away a position for no
    reason. But the fold then REVISES normally and writes the position with a
    newer `updated_at` — and under `created_at >= prior.updated_at` that made
    the comparison false from then on, permanently. One transient read failure
    and an ungrounded verdict was not deferred but GONE: «I could not check»
    silently converted into «there was nothing», in the stage whose own handler
    exists to keep those apart.

    Measured on the shipped comparison before this column existed: the counter
    read 0, the position sat at fold 3, and the ungrounded verdict was still
    the newest one there was.

    Falsification: swap the watermark back for any comparison against
    `prior.updated_at` and turn 3 does not rebuild.
    """
    store = InMemoryStandingStore()
    inner = InMemoryTurnVerdictStore()
    verdicts = _BlindableVerdicts(inner)
    metrics = Metrics()

    def _no_verdict(body: str) -> debrief.Completion:
        return debrief.Completion(json.dumps({"standing": body}), "stop")

    # Turn 1 — a real ungrounded verdict, written the only way one ever is.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("t1"),
                              _grounded(grounded=False,
                                        unsupported=("invented a price",))),
               now_ms_override=1000)
    # Turn 2 — THE INSTRUMENT IS DOWN. No rebuild, correctly; but the fold
    # succeeds and moves the position to 2000, past the verdict.
    verdicts.blind = True
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_no_verdict("t2")), now_ms_override=2000,
               trace=_trace_with_tag("tag-2"))
    assert _counter(metrics, "nora_standing_rederivations_total") == 0, (
        "re-derived on an instrument that was down"
    )
    # Turn 3 — the instrument is back and the verdict is STILL unconsumed.
    verdicts.blind = False
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_no_verdict("t3")), now_ms_override=3000,
               trace=_trace_with_tag("tag-3"))

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1, (
        "one turn of an unreadable verdict store dropped the ungrounded "
        "verdict permanently"
    )


async def test_a_grounded_verdict_cannot_bury_an_unconsumed_ungrounded_one():
    """**The read, not the watermark — and the watermark alone could not fix
    this.** The trigger used to ask for the newest verdict of EITHER kind and
    then test `not grounded` in Python. That answers a different question, and
    it coincides with the right one until a turn goes by without consuming an
    ungrounded verdict:

        turn 1  ungrounded verdict at 1000
        turn 2  the verdict read FAILS, so the trigger declines (R-27) — but
                the fold itself SUCCEEDS and saves its own GROUNDED verdict
        turn 3  the newest verdict is now grounded. The ungrounded one is
                buried, and nothing will ever look past it.

    Turn 2 there is the ORDINARY shape of a successful turn, not an exotic one,
    which is why this was reachable rather than theoretical. Measured before the
    fix: 0 re-derivations, watermark still None, the turn-1 verdict still
    sitting in the store.

    The previous test of this scenario was green by FIXTURE CHOICE — its turn 2
    used a client that emitted no verdict, so nothing was ever written to bury
    the ungrounded one. Swapping in an ordinary `_ok(...)` is the whole
    difference (cold review, 2026-08-28).

    Falsification: point the trigger back at a latest-of-either-kind read and
    this returns to 0.
    """
    store = InMemoryStandingStore()
    inner = InMemoryTurnVerdictStore()
    verdicts = _BlindableVerdicts(inner)
    metrics = Metrics()

    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("t1"),
                              _grounded(grounded=False,
                                        unsupported=("invented a price",))),
               now_ms_override=1000)
    verdicts.blind = True
    # An ORDINARY successful turn: it writes a grounded verdict of its own.
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("t2")), now_ms_override=2000,
               trace=_trace_with_tag("tag-2"))
    verdicts.blind = False
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("t3")), now_ms_override=3000,
               trace=_trace_with_tag("tag-3"))

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1, (
        "a later grounded verdict buried the unconsumed ungrounded one"
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.consumed_verdict_tag == "tag-1", (
        "the rebuild consumed something other than the verdict that caused it"
    )


async def test_two_ungrounded_verdicts_in_one_millisecond_are_both_acted_on():
    """**The watermark's own proxy, found one turn after the one it replaced.**

    It first held the consumed verdict's `created_at`. A millisecond DESCRIBES a
    verdict; `tag_id` IS one — so two ungrounded verdicts sharing a millisecond
    collapsed to a single watermark value and the second was never acted on, in
    either tag order.

    The premise is not hypothetical in this codebase: both `latest_ungrounded`
    implementations justify their `tag_id` tie-break with "two turns in one
    millisecond are reachable". If that is true this hole was real; if it is
    false those tie-breaks are decoration. It cannot be both.

    Falsification: key the watermark on `created_at` again and the second
    verdict is never consumed.
    """
    store = InMemoryStandingStore()
    verdicts = InMemoryTurnVerdictStore()
    metrics = Metrics()

    # A position that has already consumed ONE of two verdicts stamped in the
    # same millisecond.
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="carried",
        fold_count=1, updated_at=100, consumed_verdict_tag="tag-a"))
    for tag in ("tag-a", "tag-b"):
        await verdicts.save(verdict=TurnVerdict(
            tag_id=tag, conversation_id="g1", grounded=False,
            unsupported=("same millisecond",), created_at=500))

    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               now_ms_override=2000, trace=_trace_with_tag("tag-next"))

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 1, (
        "the second verdict of the pair was swallowed by a watermark that "
        "named a millisecond instead of a verdict"
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.consumed_verdict_tag == "tag-b"


async def test_a_refusing_rebuild_leaves_the_verdict_unconsumed():
    """The watermark advances only where the position is WRITTEN, so a fold
    that refuses retries next turn instead of marking a verdict it never acted
    on as handled. Three refusing folds, three triggers — and the trigger
    counter alone cannot tell that from a latch, which is what the second
    counter is for."""
    store, verdicts, metrics = (
        InMemoryStandingStore(), InMemoryTurnVerdictStore(), Metrics())
    await _run(store=store, store_verdict=verdicts, metrics=metrics,
               client=_Client(_ok("t1"),
                              _grounded(grounded=False,
                                        unsupported=("invented a price",))),
               now_ms_override=1000)
    for i, ts in enumerate((2000, 3000, 4000), start=2):
        # `finish_reason="length"` — the fold is refused BEFORE anything is
        # written, which is the state the watermark has to survive.
        await _run(store=store, store_verdict=verdicts, metrics=metrics,
                   client=_Client(debrief.Completion(
                       json.dumps({"standing": "cut off"}), "length")),
                   now_ms_override=ts, trace=_trace_with_tag(f"tag-{i}"))

    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ungrounded") == 3
    assert _counter(metrics, "nora_standing_rederivations_written_total") == 0, (
        "a re-derivation that wrote nothing was counted as landed"
    )
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.consumed_verdict_tag is None, (
        "a refusing fold marked the verdict consumed"
    )


async def test_a_ceiling_rebuild_does_not_claim_to_have_consumed_a_verdict():
    """A `ceiling` rebuild has read no verdict. If it advanced the watermark it
    would silence an ungrounded verdict it never looked at — the same
    swallow-by-proxy the column was added to remove, one trigger over."""
    store = await _with_prior(20, updated_at=100)
    _, metrics, _ = await _run(store=store, store_verdict=None,
                               rebuild_after_folds=20, now_ms_override=2000)
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ceiling") == 1
    assert _counter(metrics, "nora_standing_rederivations_written_total",
                    trigger="ceiling") == 1
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.consumed_verdict_tag is None


async def test_the_watermark_is_carried_forward_by_an_ordinary_revise():
    """A revise consumes nothing and must not FORGET what a past rebuild
    consumed either. Dropping it would make the next turn re-derive from a
    verdict already acted on — the latch, arrived at by omission."""
    store = await _with_prior(1, updated_at=100, consumed_verdict_tag="earlier")
    await _run(store=store,
               store_verdict=await _with_verdict(grounded=True, created_at=300),
               now_ms_override=2000)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None
    # The ROW MUST HAVE BEEN REWRITTEN, asserted before the watermark is read.
    # `consumed_verdict_tag == "earlier"` is the value the fixture seeded, so on its
    # own it cannot tell "carried forward" from "never written at all" — which
    # is what this test asserted, and it would have passed against a fold that
    # did nothing.
    assert row.fold_count == 2, "the revise never happened"
    assert row.updated_at == 2000, "the row was not rewritten by this fold"
    assert row.consumed_verdict_tag == "earlier"


async def test_no_verdict_store_is_inert_not_a_failure():
    """`store_verdict=None` is what every deployment that has not wired one
    runs, and the stage documents it as ordinary. It had NO test: the helper
    substituted a real store for `None`, so the branch was unreachable."""
    client = _Client(_ok("a position"))
    store, metrics, _ = await _run(store_verdict=None, client=client)
    assert await store.read(platform="signal", conversation_id="g1") is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_turn_verdict_total") == 0
    # Phase 2: the verdict exists for exactly one reader — its store. No
    # store wired, no judge call issued: a model call for output nothing can
    # ever read is not thrift, it is an unread answer on the live path.
    assert len(client.prompts) == 1, (
        "the judge call ran with nowhere to write its answer"
    )


async def test_a_cancel_after_the_write_does_not_unsay_the_position():
    """The verdict save and the note propose run AFTER the position is
    durable, and both guard only `except Exception` — so a `CancelledError` in
    either escapes to the stage's outer cancel branch.

    That branch used to relabel the turn `cancelled`, which reports a position
    that IS in the database as one that was never revised. `written` is a claim
    about a committed row; a rider failing after it must not be able to unsay
    it, which is the same rule the verdict and propose handlers already follow
    one level down.
    """
    class _CancellingVerdicts(InMemoryTurnVerdictStore):
        async def save(self, *, verdict):
            raise asyncio.CancelledError

    store, metrics, _ = await _run(
        store_verdict=_CancellingVerdicts(),
        # A verdict is actually WRITTEN so the cancelling save is reached —
        # an absent verdict would make the guard vacuous.
        client=_Client(_ok("a position"), _grounded()))

    assert await store.read(platform="signal", conversation_id="g1") is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_debrief_total", outcome="cancelled") == 0, (
        "a cancel in a rider relabelled a position that was written"
    )


async def test_a_cancel_before_the_write_is_still_a_cancel():
    """The other side, so the guard above cannot be satisfied by simply never
    labelling anything `cancelled` again."""
    _, metrics, _ = await _run(client=_Client(asyncio.CancelledError()))
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

    # The collaborators are whatever `run` and `_should_rebuild` are HANDED.
    receivers: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef)
                and node.name in {"run", "_should_rebuild"}):
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

async def test_an_unreadable_verdict_store_still_honours_the_ceiling():
    """R-72's row claimed, unqualified, that an unreadable verdict store does
    NOT rebuild. False: the ceiling reads `fold_count` off the row already in
    hand and needs no second store, so it still fires. The named test used
    fold_count=1 against a ceiling of 20 and only ever exercised the
    below-ceiling half (cold review, 2026-08-28)."""
    class _Broken(InMemoryTurnVerdictStore):
        async def latest_ungrounded(self, **kw):
            raise RuntimeError("verdict store down")

    _, metrics, _ = await _run(
        store=await _with_prior(20), store_verdict=_Broken(),
        rebuild_after_folds=20)
    assert _counter(metrics, "nora_standing_rederivations_total",
                    trigger="ceiling") == 1


# --- 26 §3's fourth output: note proposals ---------------------------------

_NOTE = {
    "title": "Where dinner is on the 9th",
    "description": "Three venues named, nobody has picked one",
    "uncertainty": "which venue",
    "closing_condition": "someone names the place",
    "anchor_date": 20260909,
    "body": "Miles, Grant and Tyler weighed three options and stopped",
}


def _with_notes(*notes, body: str = "a position") -> debrief.Completion:
    """Call A's answer with notes on it — notes ride with the standing call
    (issue #78 Phase 2), so this is the one shape that may carry them."""
    return debrief.Completion(json.dumps({
        "standing": body, "notes": list(notes),
    }), "stop")


async def test_a_fold_raises_a_note_through_the_sink():
    sink = _FakeSink()
    store, metrics, _ = await _run(client=_Client(_with_notes(_NOTE)), notes_sink=sink)
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert len(sink.batches) == 1 and len(sink.batches[0]) == 1
    proposal = sink.batches[0][0]
    assert proposal.payload.title == _NOTE["title"]
    assert proposal.payload.established_in == "g1"
    assert proposal.established_by == "tag-1"
    assert len(proposal.instruction_sha256) == 64
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="proposed") == 1


async def test_notes_parse_from_the_standing_call_not_the_verdict_call():
    """Phase 2's note wiring: notes shared the fold's ONE raw completion with
    the verdict; they must now parse from Call A's raw output. A judge
    answering with notes in its JSON must not raise a single one — the
    disposition of the verdict and of the notes are two different calls."""
    client = _Client(
        _ok("a position"),
        debrief.Completion(json.dumps({
            "grounded": True, "unsupported": [], "notes": [dict(_NOTE)],
        }), "stop"),
    )
    sink = _FakeSink()
    _, metrics, _ = await _run(client=client, notes_sink=sink)
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total") == 0, (
        "notes were parsed off the verdict call"
    )


async def test_a_note_the_turn_already_read_is_refused():
    """**§7's second sub-decision.** The tag path may have opened that note and
    deliberately decided against touching it; the debrief reviewing the same
    transcript would raise it again, and the two would argue once per turn
    forever. Matched through `note_handle`, the same normalisation R-55's latch
    uses, so two spellings of one title are not two notes."""
    from nora.agent.tools import note_handle

    sink = _FakeSink()
    _, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE)), notes_sink=sink,
        notes_read=frozenset({note_handle(_NOTE["title"])}))
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="already_read") == 1


async def test_an_over_long_title_is_refused_not_truncated():
    """The domain type raises rather than trimming, because the note index is
    uncapped and rests on being complete — a silent trim loses content nobody
    can see is gone. A 61-character title is the likely model output, so it
    costs that note and nothing else."""
    sink = _FakeSink()
    store, metrics, _ = await _run(
        client=_Client(_with_notes({**_NOTE, "title": "x" * 61})), notes_sink=sink)
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="refused") == 1
    assert (await store.read(platform="signal", conversation_id="g1")) is not None


async def test_a_note_with_no_anchor_is_refused_never_defaulted():
    """`None` reaches `__post_init__`, which refuses. Anchoring to today would
    file the note under a day nobody named — and mechanical retirement reads
    that day."""
    sink = _FakeSink()
    _, metrics, _ = await _run(
        client=_Client(_with_notes({k: v for k, v in _NOTE.items()
                                    if k != "anchor_date"})), notes_sink=sink)
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="refused") == 1


async def test_more_notes_than_the_cap_are_dropped_and_counted():
    """A fold wanting six notes has started narrating. The index is uncapped by
    design, so those would cost every later prompt forever."""
    sink = _FakeSink()
    _, metrics, _ = await _run(
        client=_Client(_with_notes(
            _NOTE, {**_NOTE, "title": "b"}, {**_NOTE, "title": "c"})),
        notes_sink=sink)
    assert len(sink.batches[0]) == debrief.MAX_NOTES_PER_FOLD
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="over_cap") == 1


async def test_a_sink_failure_never_costs_the_position():
    """The rule, a third output over. `propose` is atomic and every failure
    ESCAPES — no partial batch to reconcile — so there is ONE refusal path and
    it must not touch `outcome`."""
    from nora.domain.notes import StaleNoteHeadError

    store, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE)),
        notes_sink=_FakeSink(raises=StaleNoteHeadError(
            "lost the CAS", observed_head="other-revision")))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="unwritten") == 1


async def test_a_note_field_of_the_wrong_shape_is_refused_not_stringified():
    """**Stringifying is a guess wearing parsing.** `str(["dinner", "9th"])`
    is `"['dinner', '9th']"`, and that went into the notes index as a TITLE —
    uncapped, injected into every later prompt, indistinguishable downstream
    from a title a person would recognise.

    The objective promises an entry missing any required field is discarded
    whole rather than guessed at, and the verdict parser one function away
    refuses a non-bool `grounded` outright. Same rule, where it was missing.

    Falsification: put `str(...)` back around the title and the batch is
    proposed with a stringified list.
    """
    sink = _FakeSink()
    bad = dict(_NOTE, title=["dinner", "9th"])
    _, metrics, _ = await _run(client=_Client(_with_notes(bad)), notes_sink=sink)
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="malformed") == 1
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="proposed") == 0


async def test_a_missing_note_field_is_still_the_domain_types_refusal():
    """Absent is NOT the same as wrong-shaped, and only the second is refused
    at the parse. A missing field becomes `""` and reaches
    `NoteCreationProposal.__post_init__`, which refuses it with the domain's
    own message — that judgement belongs to the type, not to the parser, and
    the two land on different labels so an operator can tell a model that
    omitted a field from one that emitted a list."""
    sink = _FakeSink()
    missing = {k: v for k, v in _NOTE.items() if k != "closing_condition"}
    _, metrics, _ = await _run(
        client=_Client(_with_notes(missing)), notes_sink=sink)
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="refused") == 1
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="malformed") == 0


async def test_no_unit_means_no_proposal_never_a_guessed_one():
    """`unit_of` answers None when policy.toml cannot name the room's
    unit. Filing one social unit's notes into another's is not recoverable
    by looking at the index, so this refuses rather than guessing.

    **`sink.batches == []` is not enough on its own and used to be the whole
    test.** An empty list is equally what you get if the sink was never reached
    for some unrelated reason — a fold that refused, a parse that found no
    notes, a wiring change. So the refusal is NAMED on its own counter and
    asserted here. That counter is also the operator-facing point: a
    `policy.toml` edit that stops naming a room otherwise disables note
    proposals with no signal at all, and a flat `proposed` series looks exactly
    like a model that had nothing to raise.
    """
    sink = _FakeSink()
    store, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE)), notes_sink=sink,
        unit_of=lambda p, c: None)
    assert sink.batches == []
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="no_unit") == 1, (
        "an unmapped room disabled proposals silently"
    )
    # and the position still lands — a room policy cannot name is not a fold
    # failure, it is a refusal to propose.
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


async def test_a_resolver_that_raises_never_costs_the_position():
    """**`unit_of` is an INJECTED callable and it was on the position's
    critical path.** It ran before the overflow gate, before the screen, before
    `store.write`, outside any `try` of its own — and even when `notes_sink is
    None`, so a broken resolver cost the position on deployments that could not
    propose at all. This stage states twice that a proposal failure must never
    cost the position; the resolver was the exception nobody had written down.

    Falsification: move the `unit_of` call back above `store.write` and
    the position is `None` here.
    """
    sink = _FakeSink()

    def _explodes(platform: str, conversation_id: str) -> str | None:
        raise RuntimeError("policy resolver is broken")

    store, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE)), notes_sink=sink,
        unit_of=_explodes)

    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None, "a broken unit resolver cost the position"
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert sink.batches == []
    # A FAULT, not a policy decision, and they get different labels: one is a
    # room deliberately unmapped, the other is a resolver that needs fixing.
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="no_unit_unavailable") == 1
    assert _counter(metrics, "nora_debrief_note_proposals_total",
                    outcome="no_unit") == 0


async def test_the_resolver_is_not_called_at_all_when_notes_are_off():
    """Most deployments today run with `notes_sink is None`. The resolver used
    to run on every one of them, on the position's critical path, to produce a
    value nothing could consume."""
    calls: list[tuple[str, str]] = []

    def _counting(platform: str, conversation_id: str) -> str | None:
        calls.append((platform, conversation_id))
        return "ws"

    store, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE)), notes_sink=None,
        unit_of=_counting)
    # POSITIVE CONTROL: `calls == []` is also what a fold that never ran
    # produces, so the fold has to be shown to have happened first.
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert await store.read(platform="signal", conversation_id="g1") is not None
    assert calls == [], (
        "the unit resolver ran on a deployment that cannot propose"
    )


async def test_a_refused_fold_does_not_count_proposal_refusals():
    """The refusal counters used to be incremented ABOVE the overflow gate and
    the screen, so a fold about to be thrown away still described proposals it
    could never have made. `already_read` and `over_cap` then measured intent
    rather than events."""
    sink = _FakeSink()
    # A note naming something the turn already read AND a body over the cap:
    # under the old order this counted `already_read` on the way to refusing
    # the whole fold.
    over = "x" * 200
    from nora.agent.tools import note_handle
    _, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE, body=over),
                       _with_notes(_NOTE, body=over)), max_chars=100,
        notes_sink=sink, notes_read=frozenset({note_handle(_NOTE["title"])}))
    assert _counter(metrics, "nora_debrief_total",
                    outcome="refused_overflow") == 1
    assert _counter(metrics, "nora_debrief_note_proposals_total") == 0, (
        "a fold that wrote nothing was counted as having refused proposals"
    )
    assert sink.batches == []


async def test_no_notes_sink_is_inert():
    store, metrics, _ = await _run(
        client=_Client(_with_notes(_NOTE)), notes_sink=None)
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_note_proposals_total") == 0


# ---------------------------------------------------------------------------
# THE FOLD IS TRACED (issue #64). Standing's model call was the only model call
# in the process with no instrumentation of any kind: Strands traces the tag
# path's agent loop from the inside, and this stage is deliberately not a
# Strands agent, so nothing wrote a span for it. Counters said a fold took three
# seconds; nothing said what it read or what it answered — on the one tier whose
# output is injected into every later turn.
#
# `exported_spans` (tests/conftest.py) captures what is EXPORTED, so these
# assert the trace a collector would actually receive rather than that a helper
# was called.
# ---------------------------------------------------------------------------


def _spans(exporter) -> dict[str, object]:
    return {s.name: s for s in exporter.get_finished_spans()}


async def test_the_fold_is_traced_as_a_generation(exported_spans):
    """Model, prompt, output, outcome — the four things a claim about a fold has
    to re-derive from.

    PROBE: remove the `with span(...)` from `_complete` and there is no
    `engagement.debrief.fold` span at all.
    """
    client = _Client(_ok("a position"))
    await _run(client=client)

    gen = _spans(exported_spans)["engagement.debrief.fold"]
    assert gen.attributes["langfuse.observation.type"] == "generation"
    assert gen.attributes["langfuse.observation.model.name"] == "test-model"
    assert gen.attributes["gen_ai.request.model"] == "test-model"
    assert gen.attributes["gen_ai.response.finish_reasons"] == "stop"
    assert gen.attributes["nora.tag_id"] == "tag-1"
    assert gen.attributes["nora.conversation_id"] == "g1"
    assert "OBJECTIVE-TEXT" in gen.attributes["langfuse.observation.input"]
    assert "a position" in gen.attributes["langfuse.observation.output"]


async def test_the_traced_prompt_is_the_prompt_the_model_was_actually_given(
        exported_spans):
    """The trace must be the fold, not a paraphrase of it. `_Client` records
    every prompt it was handed, so this compares the span against the wire.

    PROBE: trace `rendered` instead of the assembled prompt — the objective text
    and the character cap disappear and the digest stops matching.
    """
    client = _Client(_ok("a position"))
    await _run(client=client)

    gen = _spans(exported_spans)["engagement.debrief.fold"]
    assert gen.attributes["langfuse.observation.input"] == client.prompts[0]
    assert gen.attributes["nora.debrief.prompt_sha256"] == instruction_sha256(
        client.prompts[0])
    assert gen.attributes["nora.debrief.prompt_withheld"] == ""


async def test_the_verdict_is_traced_as_a_generation_of_its_own(exported_spans):
    """AC1 on the trace side: two generations with distinct `call=` labels.
    Call B folded onto Call A's span would show one generation whose input
    does not explain its output — the exact defect the consolidate retry got
    its own span for."""
    client = _Client(_ok("a position"),
                     _grounded(grounded=False, unsupported=("claimed a thing",)))
    await _run(client=client)

    spans = _spans(exported_spans)
    assert "engagement.debrief.fold" in spans
    gen = spans["engagement.debrief.verdict"]
    assert gen.attributes["langfuse.observation.type"] == "generation"
    assert gen.attributes["langfuse.observation.model.name"] == "test-model"
    assert gen.attributes["langfuse.observation.input"] == client.prompts[1]
    assert "grounded" in gen.attributes["langfuse.observation.output"]
    assert gen.attributes["nora.tag_id"] == "tag-1"


async def test_the_consolidate_retry_gets_its_own_generation(exported_spans):
    """§4.0's retry is a DIFFERENT prompt producing a different answer. Folded
    onto one span it would render as a generation whose input does not explain
    its output.

    PROBE: reuse the `fold` span for the retry and the second prompt — the one
    that names the cap and quotes the over-long body — is never traced.
    """
    client = _Client(_ok("x" * 60), _ok("short"))
    await _run(client=client, max_chars=40)

    spans = _spans(exported_spans)
    assert "engagement.debrief.consolidate" in spans
    consolidated = spans["engagement.debrief.consolidate"]
    assert consolidated.attributes["langfuse.observation.input"] == client.prompts[1]
    assert "Consolidate it now" in consolidated.attributes["langfuse.observation.input"]
    assert "short" in consolidated.attributes["langfuse.observation.output"]


async def test_a_truncated_answer_is_visible_on_the_trace(exported_spans):
    """`finish_reason="length"` is the R-27 substitution this stage exists to
    refuse — a silently truncated body that can still parse. Reading it off the
    trace is how «the model answered badly» is told from «the answer was cut
    off» without a log.

    PROBE: drop `gen_ai.response.finish_reasons` from `_complete`'s annotation
    and the two become indistinguishable in Langfuse.
    """
    client = _Client(debrief.Completion('{"standing": "half a p', "length"))
    _store, metrics, _sv = await _run(client=client)

    gen = _spans(exported_spans)["engagement.debrief.fold"]
    assert gen.attributes["gen_ai.response.finish_reasons"] == "length"
    assert _counter(metrics, "nora_debrief_total", outcome="unavailable") == 1


async def test_an_identifier_never_reaches_the_collector(exported_spans):
    """**A trace is an egress.** The collector is a third party over the
    network, and `nora.agent.egress` says an identifier is never safe to emit OR
    TRANSMIT, whatever the destination. The fold's prompt carries the turn's raw
    tool results, so the floor predicate that guards every other write of that
    class runs here too.

    The provenance survives the refusal: the digest is of the REAL prompt, so
    the fold is re-derivable by reference exactly when it is not readable
    inline.

    PROBE: hand `_complete` the raw prompt instead of `_trace_safe`'s output and
    the number is on the span.
    """
    row = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Miles", ts=1_756_000_920_000,
        body="打去 +85290000101 啦", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    trace = dataclasses.replace(_trace(), transcript=(row,))
    client = _Client(_ok("a position"))
    store, _metrics, _sv = await _run(client=client, trace=trace)

    gen = _spans(exported_spans)["engagement.debrief.fold"]
    assert "+85290000101" not in gen.attributes["langfuse.observation.input"]
    assert gen.attributes["nora.debrief.prompt_withheld"] == "phone"
    assert gen.attributes["nora.debrief.prompt_sha256"] == instruction_sha256(
        client.prompts[0]), "the digest is of the real prompt, not the marker"
    # The FOLD still happened — the screen is on the trace, never on the call.
    written = await store.read(platform="signal", conversation_id="g1")
    assert written is not None and written.body == "a position"


async def test_a_coordinate_in_the_answer_never_reaches_the_collector(exported_spans):
    """R-28's half, on the output side. `dream_screen` already refuses to WRITE
    a position carrying a coordinate; without this the same string still left
    for the collector on the way past.

    PROBE: annotate `reply.content` directly and the pair is on the span.
    """
    client = _Client(_ok("佢喺 22.4106,114.1245"))
    _store, metrics, _sv = await _run(client=client)

    gen = _spans(exported_spans)["engagement.debrief.fold"]
    assert "22.4106,114.1245" not in gen.attributes["langfuse.observation.output"]
    assert gen.attributes["nora.debrief.output_withheld"] == "coordinate"
    assert _counter(metrics, "nora_debrief_total", outcome="screened") == 1


async def test_the_generation_is_a_child_of_the_span_the_pipeline_opened(
        exported_spans):
    """The fold has to appear IN the tag's trace, not beside it. The pipeline
    opens `engagement.debrief` around `run`, and nothing in this stage wires a
    parent — it rides the context, across `asyncio.wait_for`'s task boundary and
    all.

    PROBE: run the fold in a bare `asyncio.create_task` with the context
    detached and the generation becomes a root of its own trace.
    """
    client = _Client(_ok("a position"))
    with span("engagement.debrief"):
        await _run(client=client)

    spans = _spans(exported_spans)
    parent = spans["engagement.debrief"]
    gen = spans["engagement.debrief.fold"]
    assert gen.parent.span_id == parent.context.span_id
    assert gen.context.trace_id == parent.context.trace_id


async def test_the_folds_outcome_lands_on_the_span_the_pipeline_opened(
        exported_spans):
    """`nora_debrief_total{outcome}` is a series; it cannot answer «what
    happened on THIS tag». The span can, and it sits beside the generation
    children that show why.

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


# --- R-81: the grounding rubric is attribution-aware ------------------------


class _ShippedObjective:
    """The REAL instruction store over the shipped `nora/dreamer/` tree.

    Not `_Objective`, which answers `"OBJECTIVE-TEXT"` — the point of the test
    below is that the rubric the DEPLOYMENT reads reaches the model, so a
    stand-in string would assert the test's own text back to itself.
    """

    def objective_body(self, name: str) -> str:
        return shipped_objectives().objective_body(name)


def _misattribution_trace() -> TagTrace:
    """2026-08-28 16:35:01 UTC, with the names kept: Grant said 「收隊」 and the
    reply said Avery did. Two speakers, one of whom said the quoted line and
    one of whom did not — the whole shape the rubric was blind to."""
    said = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Grant Carver",
        ts=1_756_398_780_000, body="收隊", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    other = TranscriptRow(
        id=2, sender_id="+852222", sender_name="Avery Walsh",
        ts=1_756_398_840_000, body="我遲啲返", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    reply = {"role": "assistant", "content": [{"toolUse": {
        "name": "send_reply", "input": {"text": "Avery 話收隊喇"},
    }}]}
    return TagTrace(
        tag_id="tag-attr", conversation_id="g1", messages=(reply,),
        transcript=(said, other), outcome="send_reply",
        created_at=1_756_398_901_000,
    )


async def test_r81_a_quote_is_grounded_only_against_the_person_who_said_it():
    """**R-81.** On 2026-08-28 a reply attributed Grant's 「收隊」 to Avery and
    the fold answered `{"grounded": true, "unsupported": []}`. It was right by
    its own rubric: the rubric asked whether SOMETHING SOMEONE SAID supported
    the claim, and 「收隊」 was said — by someone. It never asked by whom, and it
    told the model in as many words that this was about sourcing «not about
    correctness», which reads as licence to wave a wrong name through.

    Two axes, because either one alone is a claim rather than a measurement:

    1. **The rule reaches the model.** Asserted on the prompt the fold was
       actually given, through the real `DreamerInstructions` over the shipped
       tree — not on `read_text()` of the markdown, which would also be
       satisfied by the clause sitting in the frontmatter `description`, where
       nothing ever sends it.
    2. **The surface the rule presupposes exists.** «the speaker on that
       transcript line» is unjudgeable unless the render puts the speaker ON
       the line and keeps two speakers apart. So the same prompt is read back:
       the quoted line names Grant and not Avery, and Avery is present
       elsewhere as the speaker of her own line — without that second half the
       Avery assertion is also true of a render that dropped her entirely.

    FALSIFIED (production only, never this file):
    - revert `standing-debrief.md`'s attribution paragraph -> axis 1 red;
    - `_speaker` in `nora/engagement/debrief_render.py` returning `""` ->
      axis 2 red, axis 1 still green.
    """
    client = _Client(_ok("a position"))
    metrics = Metrics()
    await debrief.run(
        trace=_misattribution_trace(), platform="signal",
        store=InMemoryStandingStore(), store_verdict=InMemoryTurnVerdictStore(),
        client=client, objective=_ShippedObjective(), metrics=metrics,
        display_name="Nora", max_chars=1200,
        record_store=FakeRecordStore(), fold_batch=200,
        timeout_seconds=5.0, store_timeout_seconds=5.0,
        rebuild_after_folds=20, notes_read=frozenset(), notes_sink=_FakeSink(),
        unit_of=lambda p, c: "ws", now_ms=1_756_398_902_000,
    )
    # The positive control: every assertion below is read off a prompt, and a
    # stage that raised before building one produces no prompt at all.
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    # The rubric rode the ONE fold prompt until Phase 2; it now ships to the
    # judge (the second completion), which is the call that actually grades.
    [standing_prompt, prompt] = client.prompts

    # --- axis 1: the rule the model is judging by -------------------------
    assert "speaker on the line it came from" in prompt, (
        "the rubric no longer ties a named attribution to the speaker of the "
        "line, so «someone said it» is grounding enough again"
    )
    assert "speaker on the line it came from" not in standing_prompt
    assert "an attribution is a claim about who" in prompt, (
        "«this is about SOURCING, not about correctness» stands unqualified "
        "again, which is what let a wrong name through as a correctness quibble"
    )

    # --- axis 2: the surface that rule is judged against ------------------
    # Both halves of the 16:35 turn are in front of the fold — the room's own
    # lines and the reply that misnamed one of them. Neither axis means
    # anything if the fold was never shown what it is judging.
    assert "Avery 話收隊喇" in prompt, "the reply under review never reached the fold"
    # Everything before the close tag, then after the LAST open tag: the rubric
    # itself names `<transcript>` when it tells the model what it is given, so
    # a leading split lands inside the instructions instead of the room.
    transcript = prompt.split("</transcript>")[0].rsplit("<transcript ", 1)[1]

    quoted = [line for line in transcript.splitlines() if "收隊" in line]
    assert len(quoted) == 1, (
        f"expected one transcript line carrying the quote, got {quoted}")
    assert "Grant Carver" in quoted[0], (
        "the quoted line does not name its speaker, so no rubric can tell who "
        f"said it: {quoted[0]!r}"
    )
    assert "Avery Walsh" not in quoted[0]
    assert any("Avery Walsh" in line and "我遲啲返" in line
               for line in transcript.splitlines()), (
        "Avery is not in the rendered transcript at all — the assertion above "
        "would then hold for a render that simply dropped her"
    )


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
    """Call A answering with a position AND the note the live fold raised."""
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

    Three fixes, one shape, asserted where each of them actually lands:

    - what the fold is TOLD -- the header is scoped to the turn and her
      delivered row is marked as delivered. The stage cannot assert what a
      model concludes, and the render IS the whole fix, so this is the honest
      place to measure it;
    - what the VERDICT says -- a code check, not a prompt line, because the
      verdict call has never once returned false in production (#189);
    - what reaches the note sink -- nothing.
    """
    client = _Client(_ok_with_note("he is waiting on a route"), _grounded())
    sink = _FakeSink()
    _, metrics, verdicts = await _run(
        trace=_incident_trace(), client=client, notes_sink=sink)

    fold_prompt = next(
        p for p in client.prompts if p.startswith("OBJECTIVE-TEXT"))
    assert "this turn's model call failed, so THIS TURN sent nothing" in fold_prompt
    assert "does not describe what any other turn of yours sent" in fold_prompt
    assert 'from="Nora" delivery="delivered">聽朝 10 點到數碼港' in fold_prompt

    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None
    assert verdict.grounded is False
    assert any("was delivered to this room at" in c for c in verdict.unsupported)
    assert _counter(metrics, "nora_turn_verdict_total", grounded="false") == 1

    assert sink.batches == []
    assert _counter(
        metrics, "nora_debrief_note_proposals_total", outcome="broken_turn") == 1


async def test_the_verdict_can_come_back_false_without_the_model_agreeing():
    """The model said `grounded: true` -- the only thing that call has ever
    said. The code check overrides it, and the flip is not optional: the
    domain type refuses a grounded verdict carrying unsupported claims, so
    "grounded, with a claim" is not even representable."""
    _, _, verdicts = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("a position"), _grounded(grounded=True)))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is False


async def test_the_deterministic_claim_survives_a_model_verdict_at_its_cap():
    """`UNSUPPORTED_MAX_ITEMS` truncates nothing -- the domain type refuses an
    over-long list outright -- so folding a claim into a verdict already
    carrying ten would have taken the whole verdict down with a `ValueError`.
    The code claim goes first and the model's oldest is dropped."""
    _, _, verdicts = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("a position"), _grounded(
            grounded=False,
            unsupported=tuple(f"claim {i}" for i in range(10)))))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None
    assert len(verdict.unsupported) == 10
    assert "was delivered to this room at" in verdict.unsupported[0]
    assert "claim 9" not in verdict.unsupported


async def test_a_verdict_is_still_written_when_the_judge_call_itself_failed():
    """The check needs no completion, which is the point of it being code. A
    judge call that never returned leaves the disposition `unavailable` and
    the model half with nothing -- and the turn still comes back ungrounded."""
    _, metrics, verdicts = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("a position"), RuntimeError("verdict call down")))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is False
    assert _counter(metrics, "nora_turn_verdict_total", grounded="false") == 1


async def test_a_turn_that_broke_with_no_reply_of_hers_in_it_is_left_alone():
    """The NEGATIVE half. A crash is not itself ungrounded -- the check needs
    BOTH halves, and a dead turn whose window holds nothing of hers is an
    ordinary dead turn. Without this the instrument would report every
    `llm_error` as a grounding failure and the series would say nothing."""
    trace = dataclasses.replace(
        _incident_trace(),
        transcript=(TranscriptRow(
            id=133, sender_id="+852111", sender_name="Miles",
            ts=1_757_666_637_000, body="@Nora 點去", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None),),
    )
    _, metrics, verdicts = await _run(
        trace=trace, client=_Client(_ok("a position"), _grounded()))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is True
    assert _counter(metrics, "nora_turn_verdict_total", grounded="true") == 1


async def test_a_row_of_hers_that_never_reached_the_room_is_not_a_contradiction():
    """`SENT` only. A `PENDING` row of hers is the record saying "a send was
    started", not "the room got it" -- and a check that read it as delivery
    would convict a turn on the very thing it exists to distinguish."""
    trace = _incident_trace()
    trace = dataclasses.replace(trace, transcript=(
        dataclasses.replace(trace.transcript[0],
                            delivery_status=DeliveryStatus.PENDING),
        trace.transcript[1],
    ))
    _, _, verdicts = await _run(
        trace=trace, client=_Client(_ok("a position"), _grounded()))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is True


async def test_a_reply_delivered_while_the_dead_turn_ran_is_seen_too():
    """The other half of the race: a participation turn replying WHILE the tag
    turn runs records its row after the turn captured its window, so it
    reaches the fold through the watermark batch and never through the trace.
    Bounded at the TAG's own clock -- an older batch row is not this turn's
    business."""
    trace = dataclasses.replace(
        _incident_trace(),
        transcript=(TranscriptRow(
            id=133, sender_id="+852111", sender_name="Miles",
            ts=1_757_666_637_000, body="@Nora 點去", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None),),
    )
    record = FakeRecordStore()
    await record.append(
        message=NormalizedMessage(
            "signal", "g1", "group", "nora", None, 1_757_666_700_000,
            "a route", {}),
        is_nora=True, delivery_status=DeliveryStatus.SENT,
    )
    _, _, verdicts = await _run(
        trace=trace, record_store=record,
        client=_Client(_ok("a position"), _grounded()))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is False


async def test_a_completed_turn_still_proposes_the_notes_it_raised():
    """The POSITIVE CONTROL for the note refusal. `broken_turn` has to be the
    reason nothing was proposed, not a sink that never worked in this harness
    -- and an `llm_error` assertion alone is true of both."""
    sink = _FakeSink()
    _, metrics, _ = await _run(
        trace=_incident_trace(outcome="send_reply"),
        client=_Client(_ok_with_note("a position"), _grounded()),
        notes_sink=sink)
    assert len(sink.batches) == 1 and len(sink.batches[0]) == 1
    assert _counter(
        metrics, "nora_debrief_note_proposals_total", outcome="proposed") == 1


async def test_a_dead_turn_still_revises_the_position():
    """The asymmetry is deliberate. A turn that died IS worth folding -- "I
    owe him an answer, my turn crashed" is exactly what a position should
    carry. What it is not worth is a permanent index line."""
    store, metrics, _ = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("my turn died, I owe him an answer"), _grounded()))
    position = await store.read(platform="signal", conversation_id="g1")
    assert position is not None
    assert position.body == "my turn died, I owe him an answer"
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1


async def test_a_turn_that_actually_replied_is_not_convicted_by_its_own_reply():
    """The OTHER negative half, and the one the fix needs most. Almost every
    healthy turn ends with a delivered row of hers inside its own window --
    that is what a reply IS. A check that looked only at the row and not at
    the outcome would return `grounded=false` on every successful turn in the
    deployment, which is a re-derivation on every fold and an instrument that
    says nothing."""
    _, metrics, verdicts = await _run(
        trace=_incident_trace(outcome="send_reply"),
        client=_Client(_ok("a position"), _grounded()))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is True
    assert _counter(metrics, "nora_turn_verdict_total", grounded="true") == 1


# --- issue #199, round two: the lines the mutation sweep could not reach ----


async def test_a_reply_from_days_ago_in_the_window_is_not_this_turns_business():
    """**`trace.transcript` is a ROW window, not a time window**, and that is
    the defect an independent review measured on the first version of this
    check: `recent(limit=40)` on a room that says a handful of things a day
    reaches back DAYS, so every broken turn came back ungrounded on the
    strength of a reply from last Tuesday — discarding the position and
    writing a claim whose bare clock read as though it were today. The repo's
    own time-grounding lesson, reappearing inside the fix for it.

    One floor over both sources, `_CONTEMPORARY_MS` wide."""
    trace = _incident_trace()
    stale = dataclasses.replace(
        trace.transcript[0], ts=trace.transcript[1].ts - 86_400_000)
    _, metrics, verdicts = await _run(
        trace=dataclasses.replace(trace, transcript=(stale, trace.transcript[1])),
        client=_Client(_ok("a position"), _grounded()))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is True
    assert _counter(metrics, "nora_debrief_room_scope_overrides_total",
                    model_verdict="grounded") == 0


async def test_a_batch_reply_from_before_the_turn_is_not_this_turns_business():
    """The same floor on the other source. The watermark batch is every row
    since the last fold, which on a conversation that has not folded since
    yesterday is everything she said yesterday — so the batch half needs the
    bound just as much as the trace half, and it may not be a bare `>= the
    newest row's ts` either: her send and the tag share a wall-clock second in
    this incident, so that comparison is a millisecond coin-flip."""
    trace = dataclasses.replace(
        _incident_trace(),
        transcript=(TranscriptRow(
            id=133, sender_id="+852111", sender_name="Miles",
            ts=1_757_666_637_000, body="@Nora 點去", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None),),
    )
    record = FakeRecordStore()
    await record.append(
        message=NormalizedMessage(
            "signal", "g1", "group", "nora", None,
            1_757_666_637_000 - 86_400_000, "yesterday's answer", {}),
        is_nora=True, delivery_status=DeliveryStatus.SENT,
    )
    _, _, verdicts = await _run(
        trace=trace, record_store=record,
        client=_Client(_ok("a position"), _grounded()))
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is True


async def test_the_override_says_what_the_model_had_said():
    """**The check destroys a reading unless it is counted.**
    `nora_turn_verdict_total{grounded="false"}` is where #189's finding — the
    judge has never once convicted — is read from, and a `false` there can now
    be code-authored with nothing able to tell which. The label answers it:
    what the model had said at the moment the code overrode it. A rising
    `grounded` here is the reading that says the prompt half still cannot see
    what the record does."""
    _, metrics, _ = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("a position"), _grounded(grounded=True)))
    assert _counter(metrics, "nora_debrief_room_scope_overrides_total",
                    model_verdict="grounded") == 1

    _, metrics, _ = await _run(
        trace=_incident_trace(),
        client=_Client(_ok("a position"), RuntimeError("judge down")))
    assert _counter(metrics, "nora_debrief_room_scope_overrides_total",
                    model_verdict="absent") == 1


async def test_a_verdict_the_domain_refuses_costs_the_verdict_and_not_the_position():
    """This rider runs below the fold's guards but ABOVE the position write,
    so anything it lets escape reaches `run`'s `BaseException` handler with
    `outcome` unset and NOTHING WRITTEN — on a turn that had already delivered
    its reply. The stage says twice that a rider must never cost the position;
    this is the test that means it, driven through the one refusal the domain
    type can actually produce here (`TurnVerdict` needs a `tag_id`).

    **And the fallback is `None`, never the model's verdict.** We have just
    established this turn is contradicted, so its `grounded=true` is the one
    answer known to be wrong; re-writing it would launder a failed override
    into a blessing. `except ValueError` returning `verdict` was the first
    shape and it did exactly that."""
    trace = dataclasses.replace(_incident_trace(), tag_id="  ")
    store, metrics, verdicts = await _run(
        trace=trace, client=_Client(_ok("a position"), _grounded()))
    position = await store.read(platform="signal", conversation_id="g1")
    assert position is not None and position.body == "a position"
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert (await verdicts.read(tag_id="  ")) is None
    assert _counter(metrics, "nora_turn_verdict_total", grounded="true") == 0


@pytest.mark.parametrize("outcome", ["send_reply", "recovered", "silent", "vetoed"])
async def test_only_a_turn_that_broke_is_refused_the_note_path(outcome: str):
    """`_BROKEN_TURN_OUTCOMES`'s MEMBERSHIP is the decision, and until this ran
    over every outcome it was guarded by nothing — widening the set to include
    `silent` and `vetoed` left the whole suite green. A silence is a decision
    26 §2 says is the case most worth reviewing, and a veto is a guard doing
    its job on reasoning that finished; both produce a turn there is something
    to say about."""
    sink = _FakeSink()
    _, metrics, verdicts = await _run(
        trace=_incident_trace(outcome=outcome),
        client=_Client(_ok_with_note("a position"), _grounded()),
        notes_sink=sink)
    assert len(sink.batches) == 1
    assert _counter(
        metrics, "nora_debrief_note_proposals_total", outcome="proposed") == 1
    assert _counter(
        metrics, "nora_debrief_note_proposals_total", outcome="broken_turn") == 0
    # The verdict half of the same membership: a turn that did not break is
    # never convicted by a delivered reply sitting in its own window, which is
    # what every healthy turn ends with.
    verdict = await verdicts.read(tag_id="2301f72a")
    assert verdict is not None and verdict.grounded is True
