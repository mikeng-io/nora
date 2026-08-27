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
import json

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_verdict import TurnVerdict
from nora.engagement.stages import debrief
from nora.obs.metrics import Metrics
from nora.testing import InMemoryStandingStore, InMemoryTurnVerdictStore

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

    async def complete(self, *, prompt: str) -> debrief.Completion:
        self.prompts.append(prompt)
        item = self._script.pop(0) if self._script else debrief.Completion("{}", "stop")
        if isinstance(item, BaseException):
            raise item
        return item


class _Objective:
    def objective_body(self, name: str) -> str:
        assert name == debrief.OBJECTIVE
        return "OBJECTIVE-TEXT"


def _ok(body: str, *, grounded: bool = True, unsupported=()) -> debrief.Completion:
    return debrief.Completion(json.dumps({
        "standing": body, "grounded": grounded,
        "unsupported": list(unsupported),
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


async def _run(*, store=None, store_verdict=None, client=None, metrics=None, max_chars=1200,
               timeout_seconds=5.0, store_timeout_seconds=5.0,
               rebuild_after_folds=20, trace=None) -> tuple:
    store = store if store is not None else InMemoryStandingStore()
    store_verdict = (store_verdict if store_verdict is not None
                     else InMemoryTurnVerdictStore())
    metrics = metrics if metrics is not None else Metrics()
    await debrief.run(
        trace=trace if trace is not None else _trace(),
        platform="signal", store=store, store_verdict=store_verdict,
        client=client if client is not None else _Client(_ok("a position")),
        objective=_Objective(), metrics=metrics, display_name="Nora",
        max_chars=max_chars, window=40, timeout_seconds=timeout_seconds,
        store_timeout_seconds=store_timeout_seconds,
        rebuild_after_folds=rebuild_after_folds,
        now_ms=1_756_000_931_000,
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
    assert len(client.prompts) == 2
    assert "consolidate" in client.prompts[1].lower()
    assert "50-character limit" in client.prompts[1]
    assert _counter(
        metrics, "nora_standing_overflow_refusals_total", stage="first") == 1


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
        async def complete(self, *, prompt: str):
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
        async def complete(self, *, prompt: str):
            self.prompts.append(prompt)
            await asyncio.sleep(0.06)          # under a 0.10s per-call bound...
            if len(self.prompts) == 1:
                return _ok("x" * 60)           # ...overflows, forcing the retry
            return _ok("short")

    # 0.06 + 0.06 = 0.12 > 0.10 shared. Under two INDEPENDENT 0.10s budgets both
    # calls fit and this writes "short" — so `timeout` here is only reachable
    # when the budget is genuinely shared.
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


# --- the per-turn verdict, 26 §3's second output ---------------------------

async def test_a_fold_writes_the_turn_verdict_beside_the_position():
    """Two outputs of ONE call, with opposite cardinalities: the position is
    one row per conversation revised in place, the verdict is one row per turn
    never rewritten."""
    store, metrics, verdicts = await _run(client=_Client(
        _ok("a position", grounded=False, unsupported=("claimed a closing time",))))
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
        client=_Client(_ok("a position", grounded=False, unsupported=("x",))))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert _counter(metrics, "nora_debrief_total", outcome="written") == 1
    assert _counter(metrics, "nora_turn_verdict_total", grounded="unwritten") == 1


async def test_a_missing_verdict_is_absent_not_a_failure():
    """The fold revised the position and gave no usable verdict. A READING
    about the objective text, not a fault — and never folded in with
    `unwritten`, which is the database refusing (R-27: those two send the next
    investigation to different places)."""
    store, metrics, verdicts = await _run(client=_Client(
        debrief.Completion(json.dumps({"standing": "a position"}), "stop")))
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
        _ok("a position", grounded=True, unsupported=("but also this",))))
    assert (await store.read(platform="signal", conversation_id="g1")) is not None
    assert await verdicts.read(tag_id="tag-1") is None
    assert _counter(metrics, "nora_turn_verdict_total", grounded="absent") == 1


async def test_the_verdict_comes_from_the_answer_that_was_kept():
    """On the consolidate retry the verdict must be parsed from the SECOND
    completion — the one actually accepted. Taking it off the refused draft
    would pair a position with a judgement of text nobody kept."""
    client = _Client(
        _ok("x" * 60, grounded=True),                      # refused: over cap
        _ok("short", grounded=False, unsupported=("from the kept answer",)),
    )
    _, _, verdicts = await _run(client=client, max_chars=50)
    v = await verdicts.read(tag_id="tag-1")
    assert v is not None and v.unsupported == ("from the kept answer",)


# --- S2's re-derivation trigger --------------------------------------------

async def _with_prior(fold_count: int, *, body: str = "the carried position"):
    store = InMemoryStandingStore()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body=body,
        fold_count=fold_count, updated_at=1))
    return store


async def _with_verdict(*, grounded: bool):
    verdicts = InMemoryTurnVerdictStore()
    await verdicts.save(verdict=TurnVerdict(
        tag_id="earlier", conversation_id="g1", grounded=grounded,
        unsupported=() if grounded else ("invented a price",), created_at=1))
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
    _, metrics, _ = await _run()
    assert _counter(metrics, "nora_standing_rederivations_total") == 0
    assert _counter(metrics, "nora_debrief_prior_total", prior="absent") == 1


async def test_a_rebuild_resets_the_fold_count():
    """Or the ceiling fires on every turn forever after the first time — a
    trigger that latches on is a clock with extra steps."""
    store = await _with_prior(20)
    await _run(store=store, store_verdict=await _with_verdict(grounded=True),
               rebuild_after_folds=20)
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.fold_count == 1


async def test_an_unreadable_verdict_store_does_not_rebuild():
    """R-27: an unreadable verdict is «I could not check». Re-deriving on it
    would throw a position away because an instrument was down — a worse
    outcome than carrying a possibly-drifted one for one more turn."""
    class _Broken(InMemoryTurnVerdictStore):
        async def latest(self, **kw):
            raise RuntimeError("verdict store down")

    client = _Client(_ok("a revised position"))
    _, metrics, _ = await _run(
        store=await _with_prior(1), store_verdict=_Broken(), client=client)
    assert _counter(metrics, "nora_standing_rederivations_total") == 0
    assert "the carried position" in client.prompts[0]
