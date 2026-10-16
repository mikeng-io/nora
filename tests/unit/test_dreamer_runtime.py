"""`DreamerRuntime` — the bounded read-only loop and its three-state outcome.

Every test here runs against a SCRIPTED model rather than a live one, and the
script is what makes each case real: the runtime's whole job is deciding what a
model's answer means, so a fake that returns the same well-formed envelope to
every prompt would make this file decorative (CLAUDE.md's rule, and the reason
each fake below returns the specific WRONG shape its test is named for).

The mapping under test is R-27's, one layer up from the tool results it was
written for:

- `ok` — the model returned items. Rows may still be zero: everything screened
  out, or every verdict `not_covered`, is a batch that WAS read.
- `nothing_found` — the model returned a well-formed EMPTY list. «I looked and
  there was nothing.» `commit` is still called, because that empty call is
  where time-driven decay lives.
- `unavailable` — nobody read this batch. Prose, truncated JSON, the wrong top
  level key, a timeout, a raised commit. The caller must not advance a
  watermark past it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from nora.agent import dreamer, factory
from nora.agent.dreamer import (
    MAX_ITEMS,
    UNIT_SAFE_TOOLS,
    AdaptorBudget,
    AdaptorGrant,
    AdaptorSchedule,
    AdaptorToolError,
    DreamerRuntime,
    HomeRoom,
    RegistryToolSource,
    parse_envelope,
    tool_call_faults,
    tool_subset_faults,
    verify_tool_subsets,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import Proposal
from nora.obs.metrics import Metrics


def _REMIND_GRANT() -> AdaptorGrant:
    """An adaptor built from a real room."""
    return AdaptorGrant(home_room=True)

BUDGET = AdaptorBudget(
    turns=4, total_tokens=10_000, output_tokens=1_000, timeout_seconds=5.0,
)
SHA = "c" * 64


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Instructions:
    text: str
    sha256: str
    versions: dict[str, int]


class _InstructionStore:
    """The real store reads the corpus PER CALL and this one counts the calls,
    because "read per run, never cached at construction" (DEC-128) is a
    property no assertion about the returned text can see."""

    def __init__(self, *, sha: str = SHA, raises: bool = False) -> None:
        self._sha = sha
        self._raises = raises
        self.calls = 0

    def for_objective(self, name: str) -> _Instructions:
        self.calls += 1
        if self._raises:
            raise RuntimeError(f"no objective {name!r}")
        return _Instructions(
            text="be a research agent", sha256=self._sha,
            versions={"dreamer": 1, name: 1},
        )


class _ToolSource:
    """Builds nothing. The runtime never inspects a tool object beyond reading
    its name for the guard's budget, so an empty list exercises every path this
    file cares about without dragging the whole tag-path collaborator graph in."""

    def __init__(self, *, ceiling: frozenset[str] | None = None) -> None:
        self._ceiling = ceiling if ceiling is not None else frozenset(
            {"load_transcript", "now", "web_search"}
        )
        self.built: list[tuple[str, frozenset[str], str | None]] = []
        self.grants: list[tuple[str, Any]] = []

    def read_only(self) -> frozenset[str]:
        return self._ceiling

    def tool_param_names(self) -> dict[str, frozenset[str]]:
        """Builds nothing (class docstring), so there is no signature for
        R-99 to check anything against here either."""
        return {}

    def build(
        self, *, key: str, names: frozenset[str], unit: str | None = None,
        adaptor: str = "", grant: Any = None,
    ) -> list[Any]:
        self.built.append((key, names, unit))
        if grant is not None and grant.home_room:
            self.grants.append((adaptor, grant))
        return []


class _Models:
    def create(self) -> Any:
        return object()


class _Sink:
    def __init__(self) -> None:
        self.commits: list[tuple[str, tuple[Proposal, ...]]] = []
        self.raises = False

    async def commit(self, *, key: str, proposals: Sequence[Proposal], now_ms: int) -> int:
        if self.raises:
            raise RuntimeError("sink is down")
        self.commits.append((key, tuple(proposals)))
        return len(proposals)


class _Adaptor:
    name = "test-adaptor"
    key_kind = "scope"
    uses_watermark = True
    objective = "room-language"
    envelope_key = "observations"
    #: `DreamAdaptor.store_calls_per_tick` (issue #60). 1 keeps the
    #: default bound at one `store_timeout_seconds`, which every fake
    #: below returns inside.
    store_calls_per_tick = 1

    def __init__(
        self, *, sink: _Sink, tools: frozenset[str] = frozenset({"now"}),
        accept_raises: bool = False,
    ) -> None:
        self.tools = tools
        self.sink = sink
        #: `DreamAdaptor.accept`'s narrow raise — «the model's result was read
        #: and is being discarded, so do not spend the batch too».
        self.accept_raises = accept_raises
        self.accepted: list[Sequence[Mapping[str, object]]] = []

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        return f"inputs for {key}"

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        self.accepted.append(items)
        if self.accept_raises:
            raise RuntimeError("this result cannot be recorded")
        return tuple(dict(item) for item in items if item.get("term"))

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        return await self.sink.commit(key=key, proposals=proposals, now_ms=now_ms)


@dataclass
class _Result:
    stop_reason: str | None = "end_turn"


def _runtime(
    monkeypatch, *, raw: str | None = None, invoke=None,
    instructions: _InstructionStore | None = None,
    tools: _ToolSource | None = None, metrics: Metrics | None = None,
    budgets: dict[str, AdaptorBudget] | None = None,
    replies: list[str] | None = None,
    schedules: dict[str, AdaptorSchedule] | None = None,
    grants: dict[str, AdaptorGrant] | None = None,
    store_timeout_seconds: float = 5.0,
) -> tuple[DreamerRuntime, Metrics]:
    """Wires the runtime with the Strands boundary replaced.

    `create_dream_agent`, `invoke` and `final_text` are the three points where
    this module touches the SDK; patching exactly those three keeps every test
    below free of strands while leaving the runtime's own logic — envelope,
    screen, provenance, outcome — running for real.

    `replies` scripts ONE ANSWER PER SAMPLE (`AdaptorSchedule.samples`), taken
    in order; `raw` is the single-answer form and repeats. A sampling run
    calls the boundary N times, so a fixture that could only answer once
    would make every sample identical and the union untestable."""
    metrics = metrics or Metrics()
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: object())
    monkeypatch.setattr(
        factory, "invoke",
        invoke or (lambda *a, **kw: _completed(_Result())),
    )
    scripted = list(replies) if replies is not None else None

    def _final_text(result):
        if scripted is None:
            return raw or ""
        return scripted.pop(0) if scripted else ""

    monkeypatch.setattr(factory, "final_text", _final_text)
    runtime = DreamerRuntime(
        models=_Models(),
        instructions=instructions or _InstructionStore(),
        tools=tools or _ToolSource(),
        budgets=budgets or {},
        default_budget=BUDGET,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=metrics,
        model_name="deepseek-v4-flash",
        schedules=schedules,
        grants=grants,
        store_timeout_seconds=store_timeout_seconds,
    )
    return runtime, metrics


def _sampled(n: int) -> dict[str, AdaptorSchedule]:
    """A schedule entry whose only live field here is `samples` — the
    interval and the batch limit are the caller's clock's business, not the
    runtime's."""
    return {
        "test-adaptor": AdaptorSchedule(
            min_interval_seconds=3600, batch_limit=200, samples=n,
        ),
    }


def _completed(value):
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_result(value)
    return fut


# ---------------------------------------------------------------------------
# the envelope — where "nobody read this" is separated from "there was nothing"
# ---------------------------------------------------------------------------


def test_a_well_formed_envelope_yields_its_items():
    items = parse_envelope('{"observations": [{"term": "a"}]}', key="observations")
    assert items == ({"term": "a"},)


def test_an_empty_list_is_items_not_a_failure():
    """The distinction the whole outcome type rests on: `()` is «I looked and
    found nothing», `None` is «nobody read this». Collapsing them advances a
    watermark past messages no model ever saw."""
    assert parse_envelope('{"observations": []}', key="observations") == ()


@pytest.mark.parametrize(
    "raw",
    [
        "",                                        # empty body
        "I could not find anything useful.",       # prose
        '{"observations": [{"term": "a"}',         # truncated
        '{"observations": {"term": "a"}}',         # object, not a list
        '{"results": [{"term": "a"}]}',            # the wrong top-level key
        '{"observations": ["a string"]}',          # an element that is not a mapping
        "[]",                                      # a list at the top level
    ],
)
def test_a_shape_that_is_not_the_envelope_is_none(raw):
    assert parse_envelope(raw, key="observations") is None


def test_an_oversized_body_is_refused_rather_than_parsed():
    """A response 20x the size of any legal one is evidence the model was doing
    something other than answering, so nothing in it is trusted — as opposed to
    an over-eager ITEM COUNT, which is capped rather than refused."""
    huge = '{"observations": [' + ",".join(['{"term": "a"}'] * 40_000) + "]}"
    assert parse_envelope(huge, key="observations") is None


# ---------------------------------------------------------------------------
# the ceiling — the hard line, enforced twice on purpose
# ---------------------------------------------------------------------------


def test_an_adaptor_reaching_past_the_ceiling_refuses_to_start():
    """R-5's shape. A background agent holding an act verb is not a condition
    to discover at 3am from a metric."""
    adaptor = _Adaptor(sink=_Sink(), tools=frozenset({"now", "send_reply"}))

    with pytest.raises(AdaptorToolError) as exc:
        verify_tool_subsets([adaptor], tools=_ToolSource())

    assert "send_reply" in str(exc.value)


def test_no_adaptor_may_hold_the_cognition_sessions_two_reads_however_it_derived_them():
    """The class behind #647 M1: a grant DERIVED from the read-only ceiling
    (`granted_tools`) swallowed `rollup_read` and `standing_read` silently. Both
    are inside the ceiling, so only a check on the tools themselves refuses them,
    at startup and at every run, granted a home room or not."""
    ceiling = frozenset({"now", "rollup_read", "standing_read"})
    adaptor = _Adaptor(sink=_Sink(), tools=frozenset({"now", "rollup_read", "standing_read"}))

    for grant in (AdaptorGrant(), AdaptorGrant(home_room=True)):
        (fault,) = tool_subset_faults(adaptor, ceiling=ceiling, grant=grant)
        assert "rollup_read, standing_read" in fault and "Cognition" in fault
    with pytest.raises(AdaptorToolError):
        verify_tool_subsets([adaptor], tools=_ToolSource(ceiling=ceiling))
    assert tool_subset_faults(
        _Adaptor(sink=_Sink(), tools=frozenset({"now"})), ceiling=ceiling,
    ) == ()


def test_an_adaptor_inside_the_ceiling_starts():
    verify_tool_subsets([_Adaptor(sink=_Sink())], tools=_ToolSource())


def test_a_tool_ceiling_for_a_tool_the_adaptor_lacks_refuses_to_start():
    """**A raised ceiling that reaches nothing is a boot failure, not a
    warning.**

    `_tool_budgets` already SKIPS a key naming a tool that did not build, so
    the wrong outcome is safe — it cannot conjure permission. What it cannot
    be is visible: the pass runs on the table's number while `config.toml`
    records a raised one, and the only tell is a veto counter nobody watches.
    So the operator's own typo refuses the boot instead, the same posture the
    act-verb check above takes.

    Deliberately the OPPOSITE of `budget_for`'s leniency for a misspelled
    ADAPTOR name, and the difference is what the mistake costs: there the run
    is bounded by numbers nobody chose, which is survivable; here the fix the
    operator applied is inert and nothing says so.
    """
    adaptor = _Adaptor(sink=_Sink(), tools=frozenset({"now", "web_search"}))

    with pytest.raises(AdaptorToolError) as exc:
        verify_tool_subsets(
            [adaptor], tools=_ToolSource(),
            tool_calls={adaptor.name: {"web_search": 8, "note_read": 4}},
        )

    assert "note_read" in str(exc.value)
    assert "web_search: 8" not in str(exc.value), (
        "the message names the UNHELD tool, not the one that was fine"
    )

    # The same config, minus the key it does not hold: starts.
    verify_tool_subsets(
        [adaptor], tools=_ToolSource(),
        tool_calls={adaptor.name: {"web_search": 8}},
    )
    assert tool_call_faults(adaptor, tool_calls={"web_search": 8}) == ()
    assert tool_call_faults(adaptor) == (), "no config at all is no fault"


async def test_the_ceiling_is_rechecked_per_run_and_reports_unavailable(monkeypatch):
    """The second enforcement point, and it is not redundant: the objective
    corpus hot-reloads and an adaptor rebuilt between ticks would otherwise be
    measured once, at a moment that has passed. `unavailable` rather than a
    raise — a background loop must not take its own clock down — but loud."""
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"now", "computer_run"}))
    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')

    outcome = await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert outcome.status == "unavailable"
    assert sink.commits == []          # the sink was never reached


# ---------------------------------------------------------------------------
# the three states
# ---------------------------------------------------------------------------


async def test_a_well_formed_return_is_validated_screened_written_and_ok(monkeypatch):
    sink = _Sink()
    adaptor = _Adaptor(sink=sink)
    runtime, metrics = _runtime(
        monkeypatch,
        raw='{"observations": [{"term": "big walk"}, {"term": "gourd"}]}',
    )

    outcome = await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert outcome.status == "ok"
    assert outcome.written == 2
    assert outcome.instruction_sha256 == SHA
    assert len(sink.commits) == 1
    assert metrics.get("nora_dream_run_total", adaptor="test-adaptor", status="ok") == 1


async def test_the_run_opens_an_app_root_span_and_annotates_its_outcome(
    monkeypatch, exported_spans,
) -> None:
    """A dream pass is one trace UNDER the adaptor's root, not a bare Strands
    agent span. Before this wrapper existed the pass traced as only
    `invoke_agent dreamer:<name>` — no `nora.*` attributes, no outcome
    (measured 2026-09-14: 119 dreamer traces, every one rootless)."""
    sink = _Sink()
    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "ok"
    (root,) = [s for s in exported_spans.get_finished_spans()
               if s.name == "dream:test-adaptor"]
    attrs = dict(root.attributes or {})
    assert attrs["nora.trace_kind"] == "generation"
    assert attrs["nora.dreamer.adaptor"] == "test-adaptor"
    assert attrs["nora.dreamer.key"] == "g1"
    assert attrs["nora.outcome"] == "ok"


async def test_a_blank_input_run_still_annotates_its_status_on_the_root(
    monkeypatch, exported_spans,
) -> None:
    """The span records the outcome even when nothing is found — the reason
    the wrapper exists. A blank-input run is a REAL outcome
    (nothing_found), not a silent gap. (Named for what the assert checks;
    an early draft asserted `unavailable`, which this path never is.)"""
    sink = _Sink()
    adaptor = _Adaptor(sink=sink)

    async def _empty_inputs(self, *, key, batch):
        return ""

    adaptor.inputs = _empty_inputs.__get__(adaptor, type(adaptor))
    runtime, _ = _runtime(monkeypatch)

    outcome = await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert outcome.status == "nothing_found"
    (root,) = [s for s in exported_spans.get_finished_spans()
               if s.name == "dream:test-adaptor"]
    attrs = dict(root.attributes or {})
    assert attrs["nora.outcome"] == "nothing_found"


async def test_every_proposal_carries_the_runs_own_provenance(monkeypatch):
    """The adaptor returns payloads; the RUNTIME stamps. An adaptor cannot know
    the instruction digest, the model or the run id, and a `Proposal` filled in
    anywhere else is provenance travelling one layer further than it needs to."""
    sink = _Sink()
    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')

    await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    (_key, proposals) = sink.commits[0]
    assert proposals[0].instruction_sha256 == SHA
    assert proposals[0].model == "deepseek-v4-flash"
    assert proposals[0].established_by.startswith("dream:test-adaptor:g1:")


async def test_a_well_formed_empty_return_is_nothing_found_and_still_commits(
    monkeypatch,
):
    """The decay path. `commit(key, ())` is a REAL call a sink may act on:
    a sink whose state decays on a clock rather than on new input needs the
    empty call to do it, so treating an empty result as "nothing to do, skip
    the call" silently fades nothing. The room-language consolidator was the
    original such sink and is deleted; the contract it earned is what this
    test still holds, and the note curator's expiry sweep is the shape that
    would need it next."""
    sink = _Sink()
    runtime, metrics = _runtime(monkeypatch, raw='{"observations": []}')

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "nothing_found"
    assert outcome.written == 0
    assert len(sink.commits) == 1              # CALLED, with nothing
    assert sink.commits[0][1] == ()
    assert metrics.get(
        "nora_dream_run_total", adaptor="test-adaptor", status="nothing_found",
    ) == 1


async def test_a_return_that_parses_but_carries_the_wrong_shape_never_reaches_the_sink(
    monkeypatch,
):
    """`unavailable`, and the sink is not called AT ALL — which is what makes it
    distinguishable from `nothing_found` at the caller. This is
    `ExtractionUnparseableError`'s guarantee expressed as a value: the watermark
    must not advance past a batch nobody read."""
    sink = _Sink()
    runtime, metrics = _runtime(monkeypatch, raw='{"results": [{"term": "a"}]}')

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "unavailable"
    assert sink.commits == []
    assert metrics.get(
        "nora_dream_run_total", adaptor="test-adaptor", status="unavailable",
    ) == 1


async def test_prose_instead_of_json_is_unavailable_not_nothing_found(monkeypatch):
    sink = _Sink()
    runtime, _ = _runtime(
        monkeypatch, raw="I searched but the page did not cover the question.",
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "unavailable"
    assert sink.commits == []


async def test_an_instruction_corpus_that_will_not_assemble_is_unavailable(monkeypatch):
    """An unknown objective RAISES in the real store rather than assembling the
    doctrine alone, because a run with no research goal produces output that
    looks ordinary and is answering nothing."""
    sink = _Sink()
    runtime, _ = _runtime(
        monkeypatch, raw='{"observations": []}',
        instructions=_InstructionStore(raises=True),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "unavailable"
    assert sink.commits == []


async def test_a_sink_that_raises_is_unavailable_and_does_not_escape(monkeypatch):
    """`dream_loop`'s per-key try/except drives an exponential backoff against
    an external model PROVIDER. A store outage reaching it would back off the
    model for a database's problem, so it is absorbed here."""
    sink = _Sink()
    sink.raises = True
    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "unavailable"


async def test_an_accept_that_RAISES_is_unavailable_and_never_reaches_commit(
    monkeypatch,
):
    """`DreamAdaptor.accept`'s narrow raise, and the only reason it is worth
    having: `unavailable` is the one status `_dream_once` holds the watermark
    on, so an adaptor that read its model's result and refused to record it
    does not also spend the messages it read.

    `nora.language.culture.CultureAdaptor` is the shipped user — an account
    past `BODY_MAX_CHARS` is thrown away, and returning `()` for it gave status
    `ok` and advanced the cursor, so the account AND the batch were lost with
    nothing durable saying so. This is the runtime half of that pin: the unit
    test says the adaptor raises, this says what the raise buys."""
    sink = _Sink()
    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')

    outcome = await runtime.run(
        adaptor=_Adaptor(sink=sink, accept_raises=True), key="g1", batch=(),
    )

    assert outcome.status == "unavailable"
    assert outcome.written == 0
    # NOT called: the batch is held, so nothing about this run is recorded —
    # `commit(key, ())` would be the `nothing_found` claim this run is not
    # entitled to make.
    assert sink.commits == []


async def test_an_unsettled_session_is_a_warning_and_a_crashing_accept_is_an_error(
    monkeypatch, caplog,
):
    """A Character session that settles nothing is an expected outcome (the
    batch is held and retried), so it is a WARNING with no stack trace; an
    `accept` that crashes is a fault and stays an ERROR with its traceback."""
    import logging

    from nora.domain.errors import AccountNotRecordableError

    class _Unsettled(_Adaptor):
        async def accept(self, *, key, items):
            raise AccountNotRecordableError("the session did not settle its entity")

    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')
    with caplog.at_level(logging.WARNING, logger="nora.dreamer"):
        outcome = await runtime.run(adaptor=_Unsettled(sink=_Sink()), key="g1", batch=())
    assert outcome.status == "unavailable"
    ours = [r for r in caplog.records if r.name == "nora.dreamer"]
    assert [r.levelno for r in ours] == [logging.WARNING] and ours[0].exc_info is None

    caplog.clear()
    runtime, _ = _runtime(monkeypatch, raw='{"observations": [{"term": "a"}]}')
    with caplog.at_level(logging.WARNING, logger="nora.dreamer"):
        await runtime.run(adaptor=_Adaptor(sink=_Sink(), accept_raises=True), key="g1", batch=())
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and errors[0].exc_info is not None


# ---------------------------------------------------------------------------
# bounds — the ones that fail QUIETLY if they are missing
# ---------------------------------------------------------------------------


async def test_a_hung_model_call_is_bounded_and_reported_unavailable(monkeypatch):
    """The bounded timeout is not optional, and its absence is invisible: a
    hung call leaves the key `running`, so `dream_loop` skips it on every later
    tick without ever raising — the backoff only engages on an exception — and
    a hung provider is skipped forever rather than backed off."""
    async def _hang(*_a, **_kw):
        await asyncio.sleep(10)

    sink = _Sink()
    runtime, _metrics = _runtime(
        monkeypatch, invoke=_hang,
        budgets={"test-adaptor": AdaptorBudget(
            turns=1, total_tokens=10, output_tokens=10, timeout_seconds=0.05,
        )},
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "unavailable"
    assert outcome.stop_reason == "timeout"
    assert sink.commits == []


@pytest.mark.parametrize(
    "reason", ["limit_turns", "limit_total_tokens", "limit_output_tokens"],
)
async def test_each_limit_stop_reason_is_reported_not_swallowed(monkeypatch, reason):
    """Strands raises nothing and logs nothing when a cap binds — `stop_reason`
    is the only channel there is. A runtime that dropped it would report a
    budget-exhausted loop exactly the way it reports a healthy quiet one, four
    keys every 300s, with nobody watching."""
    sink = _Sink()
    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": []}',
        invoke=lambda *a, **kw: _completed(_Result(stop_reason=reason)),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.stop_reason == reason
    assert metrics.get(
        "nora_dream_stop_reason_total", adaptor="test-adaptor", reason=reason,
    ) == 1


async def test_an_over_eager_item_count_is_capped_and_counted(monkeypatch):
    """Capped, never silently: a cap nobody counts reads as «we wrote
    everything it found»."""
    sink = _Sink()
    body = ",".join(f'{{"term": "t{i}"}}' for i in range(MAX_ITEMS + 5))
    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [' + body + "]}",
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "ok"
    assert outcome.written == MAX_ITEMS
    assert metrics.get(
        "nora_dream_screened_total", adaptor="test-adaptor", reason="cap",
    ) == 5


async def test_the_budget_defaults_rather_than_raising_for_an_unknown_adaptor(
    monkeypatch,
):
    """An adaptor added later must be bounded BY OMISSION, never unbounded by
    it and never a `KeyError` at tick time."""
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}')
    assert runtime.budget_for("an-adaptor-nobody-configured") == BUDGET


# ---------------------------------------------------------------------------
# provenance — the property that looks correct forever when it is wrong
# ---------------------------------------------------------------------------


async def test_the_instruction_corpus_is_read_once_per_run(monkeypatch):
    """DEC-128, and it is free to get right now and effectively unfixable
    later: a digest captured at construction keeps naming the corpus as it was
    at process start, and the wrongly-stamped rows are indistinguishable from
    right ones.

    Asserted as a CALL COUNT rather than on the returned text, because a cached
    digest returns the correct text too — right up until someone edits the
    objective."""
    store = _InstructionStore()
    sink = _Sink()
    runtime, _ = _runtime(
        monkeypatch, raw='{"observations": []}', instructions=store,
    )

    await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())
    await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert store.calls == 2


async def test_the_adaptors_declared_names_are_what_gets_built(monkeypatch):
    tools = _ToolSource()
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"now", "web_search"}))
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}', tools=tools)

    await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert tools.built == [("g1", frozenset({"now", "web_search"}), None)]


async def test_a_unit_keyed_adaptor_supplies_its_key_as_the_unit(
    monkeypatch,
):
    """The key-path fix (design/dreamer-agent-loop/32 §3.4), pinned at the
    seam it crosses: a unit-keyed adaptor's key IS the unit, so the
    runtime SUPPLIES it to the tool source rather than letting the tool layer
    derive one from a room that does not exist (`08-state.md` §10's trap —
    «the seam looking like it works while every call fails closed»)."""
    tools = _ToolSource(ceiling=frozenset({"now", "web_search"}))
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"now"}))
    adaptor.key_kind = "unit"
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}', tools=tools)

    await runtime.run(adaptor=adaptor, key="ridgeline", batch=())

    assert tools.built == [("ridgeline", frozenset({"now"}), "ridgeline")]


async def test_a_scope_keyed_adaptor_supplies_no_unit(monkeypatch):
    """The other half: a scope key is a conversation id, and handing it to
    the unit slot would make every unit-scoped verb read the wrong
    store. `None` keeps the resolver path — the room answers as before."""
    tools = _ToolSource()
    sink = _Sink()
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}', tools=tools)

    await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert tools.built[0][2] is None


# ---------------------------------------------------------------------------
# the tool source — two independent guards, and a hand-list that cannot rot
# ---------------------------------------------------------------------------


def test_the_unit_safe_set_is_a_subset_of_the_read_only_ceiling():
    """A unit-safe tool that is not read-only would be an act verb granted
    to a background loop through the back door."""
    from nora.agent.registry import ToolRegistry

    assert ToolRegistry().read_only_tools() >= UNIT_SAFE_TOOLS


def test_every_read_only_tool_is_classified_unit_safe_or_room_scoped():
    """The test that keeps the hand-list from going stale, and it is the whole
    reason a hand-list is acceptable here.

    A read tool added tomorrow appears in neither set and turns this red BY
    NAME, so somebody decides whether it can answer without a room. The
    alternative — deriving it — would need a third field on `NativeTool` for a
    distinction exactly one consumer makes.

    The room-scoped half is listed rather than computed as a complement, so
    that a tool moving between the two is a two-line diff a reviewer can see
    rather than a silent set-difference."""
    from nora.agent.registry import ToolRegistry

    room_scoped = {
        "transit", "route", "where_is", "place_info", "saved_places",
        # `where_been` (DEC-155) answers from the derived substrate but is
        # gated by the SAME R-31 room/circle seam as `where_is` — the guard is
        # the room, which a unit-keyed Dreamer tick does not have, so
        # room-scoped is the fail-closed side and the correct one. (It is
        # deliberately NOT in UNIT_SAFE_TOOLS: spec §7.4 records the
        # grant was NOT made.)
        "where_been",
        # `routine_read` (design/27 §4.3): the same substrate behind the SAME
        # guard as `where_been`, so the same fail-closed side.
        "routine_read",
        # `character_read` (self-growth C4) resolves its unit from the BOUND room
        # (`lineage.render_room`), so a unit-keyed run without a home room has none.
        "character_read",
        # `online_now` (design/online-status/) answers about the ROOM's own
        # members — the invoking conversation's roster is its input domain
        # (03-guards), which a unit-keyed Dreamer tick does not have.
        # The PROACTIVE decide turn reaches it through the home-room grant
        # with its per-build input-domain override, never through
        # `UNIT_SAFE_TOOLS` — the same fail-closed side as `where_been`.
        "online_now",
        # `rollup_read` and `standing_read` (DEC-246): lineage runs for the
        # ROOM the tool is bound to (its live roster, its `location` flag), and
        # a person resolves among that room's people (R-21), so a unit-keyed
        # tick with no room could answer neither. They are granted through the
        # Cognition adaptor's home-room grant only, and are Dreamer-only tools
        # (`nora.agent.surface.DREAMER_LOOP_TOOLS`).
        "rollup_read", "standing_read",
        # `document_read` (design/artifact §26): a document is read only from the
        # room it was made in (the same-room rule), which a unit-keyed tick has
        # none of, so room-scoped is the fail-closed side. Not in
        # `UNIT_SAFE_TOOLS`, and an act (`document_publish`) is never granted.
        "document_read",
        # `file_fetch` (design/external-files §3): a `code` resolves among the
        # BOUND room's own message rows, which a unit-keyed tick has none of,
        # so room-scoped is the fail-closed side.
        "file_fetch",
        # `reverse_geocode` (DEC-156) is a pure coordinate -> address lookup
        # with no room state in its ARGUMENTS, which is exactly why it must be
        # here rather than beside `weather`. Since 13c530aa it is the ONLY tool
        # that resolves a street address, and R-57 has it fill the turn-scoped
        # disclosed set `route` screens against. The coordinate it is handed
        # comes from a live fix, and everything that gates a live fix —
        # R-31's roster seam, `location_trusted` — is a property of the ROOM,
        # which a unit-keyed Dreamer tick does not have. Granting it would
        # let a tick turn a person's live position into a street address with
        # no such gate anywhere in the path. Same argument as `where_been`
        # above, and the same fail-closed answer.
        "reverse_geocode",
        # The two parking verbs (2026-08-31), classified with the live-location
        # family they belong to (`transit`/`route`/`place_info` sit here too).
        # `find_parking` can even read the asker's PRESENCE when called with no
        # place — a room-shaped input a unit-keyed tick does not have — so
        # the fail-closed side is the honest one: no Dreamer adaptor has been
        # granted a parking lookup, and this keeps it that way by name.
        "parking_status", "find_parking",
        # `who_said` (DEC-146) is room-scoped in the strictest sense in the
        # table: it answers out of ONE turn's prefill window, which a Dreamer
        # tick does not have at all — `RegistryToolSource` builds it with no
        # transcript, so every call there is honestly `unavailable`. Room-scoped
        # is the fail-closed side and the correct one; it does not move.
        "who_said",
        "load_transcript", "group_members", "lookup_person", "about",
        "ask_memory", "search_memory", "recall_lookups", "media_fetch",
        "list_reminders", "event_search", "suggest_venue", "search_gifs",
        # `music_status` (design/discord-music/07-agent-tool.md) reports on
        # ONE Discord conversation's voice session — the exact shape
        # `load_transcript`/`where_is` are room-scoped for, not a
        # unit-wide fact like `weather`. It also refuses on
        # `room.platform != "discord"`, which the Dreamer's synthetic room
        # (`RegistryToolSource`) never satisfies today — classified here,
        # fail-closed, the same "not yet actually reachable" reasoning
        # `note_read`/`note_search` were.
        "music_status",
        # The room tools (design/self-awareness/07) resolve «this unit»
        # through the ROOM's (platform, conversation_id); a unit-keyed tick's
        # synthetic room has neither, so both answer `unavailable`, the
        # fail-closed side. Admitting them would need a supplied-unit path,
        # and `room_read` under it would hand a background loop verbatim
        # words from every room of a unit, which a proactive row could then
        # carry into another room. Nobody has made that disclosure decision.
        "room_list", "room_read",
        # `record_query` (design/cognition-loop/15-map.md §4) reads through
        # the ROOM's lineage: a unit-keyed tick's synthetic room is in no
        # policy, so every call there is `unavailable`. Cognition's per-item
        # bound room (30 §1, DEC-223) is what admits it, at step 3.
        "record_query",
        # `instagram_read` (design/instagram-adoption/18) resolves the name it
        # is given against the ROOM's live roster (`room.scope().find`, the
        # lookup `about` uses) — the input a unit-keyed tick does not have,
        # so every call there is honestly `unavailable`. Room-scoped is the
        # fail-closed side, the same one `about` and `online_now` sit on;
        # design 18 said "unit-safe" before the name resolution moved into
        # the tool, and a move to UNIT_SAFE_TOOLS is a separate decision.
        "instagram_read",
        # `linkedin_read` and `linkedin_search` (design/social-per-network,
        # DEC-201) are refused in every Dreamer pass by the tools themselves
        # (a LinkedIn read is a profile visit, a search spends a monthly
        # limit), so they are not unit-safe. Room-scoped is the fail-closed
        # side.
        "linkedin_read", "linkedin_search",
        # `computer_read` (design/sandbox-runtime §4) reads the ROOM's own
        # workspace, keyed on the room's (platform, conversation_id); a
        # unit-keyed tick's synthetic room has no workspace of its own, and
        # nobody has decided a background loop may read one. Fail-closed.
        "computer_read",
        # `computer_session_list` (design/sandbox-runtime §15.2) reads the
        # ROOM's own sessions and workspace state; same reason, fail-closed.
        "computer_session_list",
        # `subagent_list` / `subagent_read` (design/subagent §3) read THIS room's jobs, keyed
        # on the room's (platform, conversation_id), and are tag-turn tools by `ToolScope`.
        "subagent_list", "subagent_read",
    }
    # The unit-safe half is `UNIT_SAFE_TOOLS` itself, read from the
    # module. Since design/dreamer-agent-loop/32 §3.4 that set includes the
    # unit-scoped reads this test used to hold HERE, fail-closed:
    # `note_read`/`note_search` resolved their unit THROUGH THE ROOM and
    # the Dreamer's synthetic room could never answer that, and
    # `search_memory_workspace` shipped WITH the fix. They moved when the key
    # path became SUPPLIED — `DreamerRuntime.run` hands a unit-keyed
    # adaptor's key to the tool layer AS the unit — which is exactly the
    # move these entries' own comments said to make. The knowledge tier's five
    # verbs were here too and went with that tier.
    unit_scoped = {
        "note_read", "note_search", "search_memory_workspace",
    }
    assert unit_scoped <= UNIT_SAFE_TOOLS, (
        "a unit-scoped read fell out of UNIT_SAFE_TOOLS — 32 §3.4 "
        "grants these only with the supplied key path"
    )
    unclassified = ToolRegistry().read_only_tools() - UNIT_SAFE_TOOLS - room_scoped
    assert unclassified == set(), (
        "read-only tool(s) classified neither unit-safe nor room-scoped: "
        + ", ".join(sorted(unclassified))
    )


def test_the_tool_source_never_builds_a_tool_outside_the_ceiling():
    """The name-level guard: no act verb is ever built, with a home-room grant or
    without one (design/dreamer-agent-loop/39 D3; the `acts` allowance that once
    admitted `remind` was deleted with the proactive adaptor, DEC-277).
    Asserted against the REAL registry rather than a stub, because the
    thing under test is the intersection with a set the registry derives.
    """
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={"friends": HomeRoom(
            unit="friends", platform="signal", conversation_id="g1",
        )},
    )
    asked = frozenset({"send_reply", "computer_run", "remind", "now"})

    ungranted = _names(source.build(key="friends", names=asked, unit="friends"))
    assert ungranted == {"now"}

    granted = _names(source.build(
        key="friends", names=asked, unit="friends", adaptor="culture",
        grant=_REMIND_GRANT(),
    ))
    assert granted == {"now"}


def _names(built: list[Any]) -> set[str]:
    return {
        getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in built
    }


class _RecordingRegistry:
    """`ToolRegistry`'s two verbs the tool source calls, with every keyword
    `surface` was handed kept — the home-room arguments (39 D2) are VALUES on
    that call and nothing else observes them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def read_only_tools(self) -> frozenset[str]:
        return frozenset({"where_is", "now"})

    def surface(self, **kw: Any) -> Any:
        # `surface()` is the one builder (design/tool-catalogue phase B); the
        # source reads `.tools` off what it returns.
        self.calls.append(kw)
        return SimpleNamespace(tools=[])


def test_a_home_room_grant_builds_the_room_the_operator_named():
    """39 D2's two review findings, pinned: `self_ids` so Nora's own account
    is skipped rather than counted `unnamed`, and `location_trusted` from
    the home room's `location` flag — both of which the pre-DEC-162 build
    left at their defaults, so `where_is` refused in every configuration.
    Plus the destination: the home room's conversation, never the unit
    NAME the key carries (39 D3)."""
    registry = _RecordingRegistry()
    roster = object()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={"friends": HomeRoom(
            unit="friends", platform="signal", conversation_id="g1",
            location_trusted=True,
        )},
        rosters={"signal": roster},
        self_ids=frozenset({"+85200000000"}),
    )

    source.build(
        key="friends", names=frozenset({"where_is"}), unit="friends",
        adaptor="proactive", grant=_REMIND_GRANT(),
    )

    (call,) = registry.calls
    assert call["conversation_id"] == "g1"
    assert call["conversation_type"] == "group"
    assert call["location_trusted"] is True
    assert call["supplied_unit"] == "friends"
    assert call["dream_adaptor"] == "proactive"
    assert call["deliverer"] is None
    room = call["room"]
    assert room.platform == "signal"
    assert room._roster_source is roster
    assert room._self_ids == frozenset({"+85200000000"})
    assert room.tagger is None


def test_a_home_room_grant_with_no_home_room_falls_back_closed():
    """The grant said «stand in the home room» and the unit names none:
    no acts, the synthetic empty room, the key as the conversation — exactly
    the pre-DEC-162 build — and never a guessed room."""
    registry = _RecordingRegistry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(), home_rooms={},
    )

    source.build(
        key="wedding", names=frozenset({"where_is", "remind"}), unit="wedding",
        adaptor="proactive", grant=_REMIND_GRANT(),
    )

    (call,) = registry.calls
    assert call["conversation_id"] == "wedding"
    assert call["location_trusted"] is False
    assert call["room"].platform == ""
    assert call["room"]._roster_source is None


def test_a_SCOPE_keyed_grant_resolves_the_adaptors_OWN_room():
    """**The wiring defect this closes** (design/culture-tier `03-decisions`
    correction 14, `03-no-agent-loop §7.4`). `build` resolved every home room
    through `home_rooms[unit or ""]`, and `DreamerRuntime.run` supplies
    `unit=None` for a scope-keyed adaptor — so a scope-keyed grant looked
    up `home_rooms[""]`, found nothing, and took the loud CLOSED branch. Every
    room-scoped verb then answered `unavailable` forever while the grant read
    as honoured, which is the state that closed PR #237 on a false premise.

    A scope-keyed adaptor's key IS a conversation, so the room is an identity
    rather than a choice: no `home_rooms` lookup, no `policy.toml`
    `home_room = true`, and no disclosure decision — the adaptor is already
    running FOR that room and was already handed its whole message batch."""
    registry = _RecordingRegistry()
    roster = object()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={},
        scope_rooms={"g1": HomeRoom(
            unit="friends", platform="discord", conversation_id="g1",
            location_trusted=True,
        )},
        rosters={"discord": roster},
        self_ids=frozenset({"+85200000000"}),
    )

    source.build(
        key="g1", names=frozenset({"where_is"}), unit=None,
        adaptor="culture", grant=AdaptorGrant(home_room=True),
    )

    (call,) = registry.calls
    assert call["conversation_id"] == "g1"
    assert call["location_trusted"] is True
    # STILL `None`: the key is a conversation, not a unit, and the
    # unit-scoped verbs must keep resolving through the room (32 §3.4).
    assert call["supplied_unit"] is None
    room = call["room"]
    assert room.platform == "discord"
    assert room._roster_source is roster
    assert room._self_ids == frozenset({"+85200000000"})


def test_a_scope_keyed_grant_for_an_unknown_room_falls_back_closed_and_says_so(
    caplog,
):
    """The grant said «stand in a real room» and the key names no policy
    conversation: the empty room, no acts, and a LOUD line naming the SCOPE —
    not the unit, which is `None` here and would have read as the config
    fault the unit branch describes."""
    metrics = Metrics()
    source = RegistryToolSource(
        registry=_RecordingRegistry(), store=None, persons=None, honcho=None,
        metrics=metrics, scope_rooms={},
    )

    with caplog.at_level(logging.ERROR, logger="nora.dreamer"):
        source.build(
            key="g-unknown", names=frozenset({"where_is"}), unit=None,
            adaptor="culture", grant=AdaptorGrant(home_room=True),
        )

    assert metrics.get(
        "nora_dream_home_room_missing_total", adaptor="culture", unit="",
    ) == 1
    said = [r.getMessage() for r in caplog.records]
    assert any("'g-unknown'" in m and "scope" in m for m in said), said


def test_two_broken_scopes_are_logged_SEPARATELY_not_collapsed_onto_one():
    """The dedupe key was `(adaptor, unit or "")`, and every scope-keyed
    miss carries `unit=None` — so all of them would collapse onto one
    entry and the first broken room would be reported as though it were the
    only one."""
    source = RegistryToolSource(
        registry=_RecordingRegistry(), store=None, persons=None, honcho=None,
        metrics=Metrics(), scope_rooms={},
    )

    with caplog_at_error() as records:
        for key in ("g1", "g2", "g1"):
            source.build(
                key=key, names=frozenset({"where_is"}), unit=None,
                adaptor="culture", grant=AdaptorGrant(home_room=True),
            )

    said = [r.getMessage() for r in records]
    assert len(said) == 2, said
    assert any("'g1'" in m for m in said) and any("'g2'" in m for m in said)


@contextlib.contextmanager
def caplog_at_error():
    """A local capture rather than the `caplog` fixture: this test asserts on
    the DEDUPE, so it needs every record the one `RegistryToolSource` emitted
    across three builds and nothing else."""
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("nora.dreamer")
    handler = _Sink(level=logging.ERROR)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def test_the_missing_home_room_is_counted_every_pass_and_logged_once(caplog):
    """A CONFIG state, not an incident: true on every dream until somebody
    edits policy.toml.

    Live it filed the same Sentry issue on every pass, once per unit
    without a home room (NORA-2 — `wedding` and `friends`). Deduplicating the
    log alone would have traded a stream of identical errors for no signal at
    all, so the counter carries the standing reading and the log carries the
    news: «the proactive adaptor has been inert here for a week» has to stay
    answerable.
    """
    metrics = Metrics()
    source = RegistryToolSource(
        registry=_RecordingRegistry(), store=None, persons=None, honcho=None,
        metrics=metrics, home_rooms={},
    )

    with caplog.at_level(logging.ERROR, logger="nora.dreamer"):
        for _ in range(3):
            source.build(
                key="wedding", names=frozenset({"where_is"}), unit="wedding",
                adaptor="proactive", grant=_REMIND_GRANT(),
            )
        source.build(
            key="friends", names=frozenset({"where_is"}), unit="friends",
            adaptor="proactive", grant=_REMIND_GRANT(),
        )

    assert metrics.get(
        "nora_dream_home_room_missing_total", adaptor="proactive", unit="wedding",
    ) == 3
    assert metrics.get(
        "nora_dream_home_room_missing_total", adaptor="proactive", unit="friends",
    ) == 1
    said = [r.getMessage() for r in caplog.records if "names none" in r.getMessage()]
    assert len(said) == 2, "once per unit, not once per pass"
    assert any("'wedding'" in m for m in said)
    assert any("'friends'" in m for m in said)


def test_without_a_grant_the_build_is_byte_for_byte_the_old_one():
    """Every other adaptor: the key as the conversation, the empty room, the
    strict gate — a home room in the map is inert for an adaptor not
    granted it."""
    registry = _RecordingRegistry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={"friends": HomeRoom(
            unit="friends", platform="signal", conversation_id="g1",
            location_trusted=True,
        )},
    )

    source.build(key="friends", names=frozenset({"now"}), unit="friends")

    (call,) = registry.calls
    assert call["conversation_id"] == "friends"
    assert call["location_trusted"] is False
    assert call["room"].platform == ""


def test_without_a_grant_a_SCOPE_keyed_build_is_the_closed_empty_room():
    """The same guarantee as the test above, on the OTHER branch — and the one
    the suite did not cover.

    `if not allowance.home_room: home = None` is the ceiling by omission: an
    adaptor with no `AdaptorGrant` gets the pre-DEC-162 shape whatever the maps
    hold. An audit mutated that branch to `home = self._scope_rooms.get(key)`
    — the grant ignored, every scope-keyed adaptor standing in a real room —
    and the WHOLE suite stayed green. It was equivalent only by accident:
    `test_without_a_grant_the_build_is_byte_for_byte_the_old_one` passes
    `unit="friends"` and populates `home_rooms`, so it exercises the
    unit branch only, and the one ungranted scope-keyed adaptor shipped
    (`NoteCandidateAdaptor`) declares `tools = frozenset()`, so `build`
    returns at the `if not wanted` guard before any room is constructed.

    It goes live the moment a scope-keyed adaptor declares a tool without a
    grant — which is exactly the drift `AdaptorGrant` exists to stop, so the
    pin is on the ROOM's SHAPE rather than on a byte comparison: what must
    hold is «closed», not «identical to the call I happened to record»."""
    registry = _RecordingRegistry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(),
        scope_rooms={"g1": HomeRoom(
            unit="friends", platform="discord", conversation_id="g1",
            location_trusted=True,
        )},
        rosters={"discord": object()},  # type: ignore[dict-item]
    )

    # NO `grant=`, and a tool asked for: the ungranted scope-keyed adaptor of
    # tomorrow, not the tool-free one of today.
    source.build(key="g1", names=frozenset({"where_is"}), unit=None)

    (call,) = registry.calls
    room = call["room"]
    assert room.platform == "", "a granted room would name its platform"
    assert room._roster_source is None, "no roster: the view is «I could not look»"
    assert room._self_ids == frozenset()
    assert call["location_trusted"] is False, "the strict gate, never the room's flag"
    assert call["conversation_id"] == "g1"
    assert call["supplied_unit"] is None


def test_the_tool_source_builds_the_unit_safe_tools():
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None, honcho=None,
        metrics=Metrics(),
    )

    built = source.build(key="nora", names=frozenset({"now", "calculate"}))

    names = {
        getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in built
    }
    assert names == {"now", "calculate"}


def test_asking_for_nothing_builds_nothing_and_touches_no_collaborator():
    """The extraction adaptor's case: a cheap tool-free pass. Building the whole
    registry to then filter it all away would drag every collaborator into a
    run that needs none of them."""
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None, honcho=None,
        metrics=Metrics(),
    )

    assert source.build(key="nora", names=frozenset()) == []


# ---- the second axis: room-scoped vs unit-safe -----------------------


def test_a_unit_keyed_adaptor_is_refused_a_room_scoped_tool():
    """The protection three documents claimed and nothing applied — and,
    since DEC-162, the one adaptor it does not bind.

    `RegistryToolSource`'s docstring, `verify_tool_subsets`' docstring, DEC-133
    and the release fragment all asserted that a unit-keyed adaptor is
    restricted to `UNIT_SAFE_TOOLS`. A review probe built
    `about`/`load_transcript`/`search_memory` for a unit key with no
    complaint — nothing checked `key_kind` anywhere.

    **The failure it would have caused is worse than a missing tool.** A
    unit name is not a conversation id, so `load_transcript` against one
    does not raise — it returns `{"messages": []}`. That is «I could not
    check» reported as «there was nothing», the one substitution R-27 exists to
    forbid, arriving in the tier whose whole purpose is to keep those two
    apart.

    REWRITTEN ON PURPOSE (design/dreamer-agent-loop/39 D2): the refusal
    stands for every unit-keyed adaptor WITHOUT a home-room grant, and
    is lifted for the one with it — the home room is the disclosure
    decision `UNIT_SAFE_TOOLS`' own comment said nobody had made,
    made by the operator in `policy.toml`."""
    adaptor = _Adaptor(
        sink=_Sink(), tools=frozenset({"web_search", "load_transcript"}),
    )
    adaptor.key_kind = "unit"
    source = _ToolSource(ceiling=frozenset({"web_search", "load_transcript", "now"}))

    with pytest.raises(AdaptorToolError) as exc:
        verify_tool_subsets([adaptor], tools=source)

    # The safe one is not BLAMED — it still appears further down, in the
    # message's list of what IS allowed, which is the useful half of a refusal.
    assert "declares room-scoped tools: load_transcript." in str(exc.value)
    assert "Unit-safe tools are:" in str(exc.value)

    # The SAME adaptor, granted a home room: not refused. `verify_tool_subsets`
    # and `tool_subset_faults` read one grant, so the exemption is measured
    # at the function the per-run check calls too.
    verify_tool_subsets(
        [adaptor], tools=source,
        grants={adaptor.name: AdaptorGrant(home_room=True)},
    )
    assert tool_subset_faults(
        adaptor, ceiling=source.read_only(), grant=AdaptorGrant(home_room=True),
    ) == ()


def test_a_scope_keyed_adaptor_may_hold_a_room_scoped_tool():
    """The other direction, and the reason the rule is on `key_kind` rather
    than on the tool: a room-scoped tool is exactly right for an adaptor whose
    key IS a room. The candidate extractor reads its room; only the
    adjudicator, keyed on a unit, has no room to bind one to."""
    adaptor = _Adaptor(
        sink=_Sink(), tools=frozenset({"load_transcript"}),
    )
    assert adaptor.key_kind == "scope"

    verify_tool_subsets([adaptor], tools=_ToolSource(
        ceiling=frozenset({"load_transcript", "now"})
    ))


async def test_the_per_run_check_refuses_the_same_thing_the_startup_check_does(
    monkeypatch,
):
    """One rule, two call sites. A per-run check that had drifted from the
    startup one would be worse than none: the startup check is the one people
    trust, and the corpus it validates against hot-reloads."""
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"load_transcript"}))
    adaptor.key_kind = "unit"
    runtime, _ = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        tools=_ToolSource(ceiling=frozenset({"load_transcript", "now"})),
    )

    outcome = await runtime.run(adaptor=adaptor, key="nora", batch=())

    assert outcome.status == "unavailable"
    assert sink.commits == []


async def test_a_blank_prompt_skips_the_model_and_still_commits(monkeypatch):
    """The fire condition, and the reason it is general rather than the fact
    tier's own.

    An adaptor with `uses_watermark = False` gets no lag, quiet or age
    condition — the loop dispatches it on EVERY tick. Without this, the fact
    adjudicator would spend a full `web_search`/`web_fetch` research loop 288
    times a day per unit on a prompt that says «Claims to settle: (none)».

    `commit(key, ())` is still called, so the decay contract survives: a
    watermarked adaptor over a quiet room renders a blank prompt too, and a
    clock-driven sink does its fading on exactly that call."""
    invoked: list[int] = []
    sink = _Sink()

    class _Blank(_Adaptor):
        async def inputs(self, *, key, batch):
            return "   \n  "

    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        invoke=lambda *a, **kw: (invoked.append(1), _completed(_Result()))[1],
    )

    outcome = await runtime.run(adaptor=_Blank(sink=sink), key="g1", batch=())

    assert outcome.status == "nothing_found"
    assert invoked == [], "a model was called with nothing to reason about"
    assert len(sink.commits) == 1 and sink.commits[0][1] == ()
    assert metrics.get(
        "nora_dream_run_total", adaptor="test-adaptor", status="nothing_found",
    ) == 1


async def test_a_real_prompt_still_reaches_the_model(monkeypatch):
    """The control. A fire condition that never fires is the same subsystem
    that does nothing, and this file would not otherwise notice."""
    invoked: list[int] = []
    sink = _Sink()
    runtime, _ = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        invoke=lambda *a, **kw: (invoked.append(1), _completed(_Result()))[1],
    )

    row = TranscriptRow(
        id=7, sender_id="p1", sender_name="A", ts=1, body="big walk 今晚?",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=(row,))

    assert invoked == [1]
    assert outcome.status == "ok"


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (
            'I could not find any source about this.\n\n'
            '{"observations": [{"term": "a"}]}',
            "the shape production actually produced: narration, then the answer",
        ),
        (
            '```json\n{"observations": [{"term": "a"}]}\n```',
            "a fence is a preamble with a suffix",
        ),
        (
            '{"observations": [{"term": "a"}]}\n\nI hope that helps.',
            "and a trailing sentence is the same defect the other way round",
        ),
    ],
)
def test_an_envelope_preceded_by_prose_is_still_read(raw, why):
    """**A readable answer discarded because a sentence preceded it is an
    outage reported for a batch that WAS read**, and the watermark is held, so
    the same batch is re-read forever while the counter says the model could
    not be reached. That is R-27 upside down.

    Measured on 2026-08-28: driving the shipped `fact-adjudication` objective
    through its whole tool loop, the model converged at turn 6 with
    «Search returning empty results consistently. Cannot find any source…»
    followed by a well-formed `{"verdicts": [...]}`. Both production runs
    failed as `no readable envelope`, at 950 and 1009 characters.
    """
    assert parse_envelope(raw, key="observations") == ({"term": "a"},), why


def test_prose_with_no_envelope_at_all_is_still_none():
    """The positive control, and the line this change must not cross: a reply
    that never produces the envelope is still `unavailable`. What was dropped
    is the requirement that the envelope come FIRST, not the requirement that
    it exist."""
    assert parse_envelope(
        "I looked and I am not sure. Probably yes?", key="observations",
    ) is None


def test_an_envelope_named_in_prose_does_not_count_as_one():
    """Keyed on the declared envelope KEY inside a balanced object, not on
    «the first thing with braces» — so a preamble that merely talks about the
    shape is not mistaken for it, and the real envelope after it still wins."""
    raw = ('I would normally answer with {"note": "an example"} here.\n'
           '{"observations": [{"term": "real"}]}')

    assert parse_envelope(raw, key="observations") == ({"term": "real"},)


def test_a_brace_inside_a_string_does_not_split_the_envelope():
    """Brace counting is string-aware, because a `source_excerpt` quoting code
    plainly can contain one — and a parser that split there would truncate a
    legal answer into an unreadable one."""
    # An UNBALANCED brace, which is what discriminates. A balanced `{x}` inside
    # a string leaves the depth count correct by accident, so a parser with no
    # string-awareness passes that case and this one is the test.
    raw = ('here you go\n'
           '{"observations": [{"term": "a", "excerpt": "the sample is {x"}]}')

    items = parse_envelope(raw, key="observations")

    assert items is not None, (
        "an unbalanced brace inside a string must not end the object early"
    )
    assert items[0]["excerpt"] == "the sample is {x"


def test_the_last_envelope_wins_when_the_model_quotes_its_instructions():
    """**Taking the first would be silent, irreversible data loss.**

    Every objective prints its empty form — `{"verdicts": []}` — as the
    nothing-found instruction, so a model that quotes its instructions on the
    way to answering puts a VALID but empty envelope ahead of its real one.
    First-wins yields zero items, which is `nothing_found`, which ADVANCES the
    watermark: real verdicts discarded, the batch consumed permanently, every
    counter green.

    The bug this whole change replaces was loud, retryable and lost nothing.
    Getting the pick rule wrong would have made it worse than what it fixed.
    """
    raw = ('The shape I must return is {"observations": []}.\n\n'
           'Here is my answer:\n{"observations": [{"term": "real"}]}')

    assert parse_envelope(raw, key="observations") == ({"term": "real"},)


def test_a_populated_example_in_the_preamble_is_not_stored_as_the_answer():
    """The sharper form, and the reason this is not merely lossy:
    `fact-adjudication.md` prints a POPULATED worked example. First-wins would
    write the documentation's own example as a fact about the world."""
    raw = ('For example: {"observations": [{"term": "from the objective"}]}\n'
           '{"observations": [{"term": "what I actually found"}]}')

    items = parse_envelope(raw, key="observations")

    assert items == ({"term": "what I actually found"},)


def test_a_malformed_envelope_does_not_shadow_a_valid_one_after_it():
    """Validity is checked while scanning, not after picking. An object
    carrying the key whose value is not a list of mappings is not an envelope,
    and treating it as one would let a bad first draft hide a good answer."""
    raw = '{"observations": ["a string"]}\n{"observations": [{"term": "a"}]}'

    assert parse_envelope(raw, key="observations") == ({"term": "a"},)


@pytest.mark.parametrize(
    ("preamble", "why"),
    [
        ('I could not find a source for the 5" pipe.',
         "an unterminated quote — an inch measurement, not an exotic input"),
        ("the set { is open here",
         "an unclosed brace"),
        ('he said \\"maybe\\" and left',
         "an escaped quote inside prose"),
        ("stray } close",
         "a stray close, which the first implementation did recover from"),
    ],
)
def test_odd_punctuation_before_the_envelope_does_not_swallow_it(preamble, why):
    """**The first implementation counted braces by hand and could not
    resynchronise.** State carried across the whole reply, so one unterminated
    quote or one unclosed `{` in the prose consumed everything after it — and
    reinstated the exact bug this change exists to fix, on inputs a research
    pass produces routinely.

    `json.JSONDecoder.raw_decode` at each `{` is the real parser: strings,
    escapes and nesting are right by construction, and a candidate that fails
    simply advances to the next `{`.
    """
    raw = f'{preamble}\n{{"observations": [{{"term": "a"}}]}}'

    assert parse_envelope(raw, key="observations") == ({"term": "a"},), why


def test_deeply_nested_json_does_not_raise_into_the_caller():
    """`run`'s own docstring says nothing here raises into the caller, and a
    `RecursionError` from the parser lands where `_finish` never runs — so a
    tick that HAPPENED increments no counter in any bucket. Pre-existing for
    whole-reply payloads and widened by scanning for objects anywhere in the
    text, so it is caught here."""
    raw = "prefix " + "{\"a\":" * 20_000 + "1" + "}" * 20_000

    assert parse_envelope(raw, key="observations") is None


def _named_tool(name: str):
    """A built tool the way `_tool_budgets` reads one: by `tool_name`."""
    def _t() -> None: ...
    _t.tool_name = name          # type: ignore[attr-defined]
    return _t


def test_the_dreamer_applies_the_configured_tool_budget_over_the_table():
    """**`[llm.budgets]` reaches the Dreamer, and until 2026-09-07 it did not.**

    The tag path applies it through
    `ToolRegistry(budget_overrides=cfg.llm.budgets).budgets()`; `_tool_budgets`
    read `NATIVE_TOOLS[name].budget` straight off the table. So one number in
    `config.toml` meant two ceilings depending on which loop asked, with
    nothing naming the difference — and the Dreamer got the lower one, on the
    loop that does the research.

    Measured live, first tick after the knowledge tier's schedule landed:
    `web_fetch = 6` was configured and `DreamGuard` vetoed the Dreamer at «you
    already called web_fetch 2 time(s)» — the TABLE value — twelve times in one
    run, which then produced a 0-char envelope.

    Falsification: drop the `overrides` lookup in `_tool_budgets` and this
    returns the table's 2.
    """
    from nora.agent.dreamer import _tool_budgets
    from nora.agent.tools import NATIVE_TOOLS

    adaptor = _Adaptor(sink=_Sink())
    tools = [_named_tool("web_fetch")]

    assert NATIVE_TOOLS["web_fetch"].budget == 2, (
        "the table's own value — if this changes, the test below is comparing "
        "against the wrong baseline and says nothing"
    )
    assert _tool_budgets(tools, adaptor) == {"web_fetch": 2}, "no override"
    assert _tool_budgets(tools, adaptor, {"web_fetch": 6}) == {"web_fetch": 6}


def test_an_override_naming_a_tool_the_adaptor_lacks_grants_nothing():
    """A name present in `budgets` is what turns `DreamGuard`'s default-deny
    into permission. `[llm.budgets]` is deployment-wide and names tools this
    adaptor was never given, so folding one in unconditionally would hand a
    unit-keyed research loop a verb nobody granted it — the exact shape
    `_tool_budgets`' own docstring refuses by reading BUILT tools."""
    from nora.agent.dreamer import _tool_budgets

    budgets = _tool_budgets(
        [_named_tool("web_fetch")], _Adaptor(sink=_Sink()),
        {"web_fetch": 6, "send_reply": 99},
    )

    assert budgets == {"web_fetch": 6}
    assert "send_reply" not in budgets


def test_an_adaptors_own_tool_ceiling_wins_over_the_table_and_the_deployment():
    """**`[dreamer.budgets.<adaptor>.tool_calls]` is the narrowest of three
    layers and must win both.**

    `NativeTool.budget` is a TAG TURN's number — `note_read = 3` is sized for
    a reply that reads a note or two before answering — and the Dreamer was
    inheriting it with no way to say otherwise. `[llm.budgets]` is no answer:
    it is deployment-wide, so raising `note_read` there raises it for every
    reply Nora writes, which is the blast radius `[dreamer] max_tokens`
    (issue #291) was added to avoid one ceiling over.

    Measured on norty 2026-09-17, the 05:00 proactive pass: `friends` holds 5
    open notes and `ridgeline` 6, against a ceiling of 3. Each scope read three
    and was refused a FOURTH DISTINCT note, then decided every note anyway —
    three grounded in a body, the rest in an index line. Status `ok`, no
    error, watermark advanced.

    Falsification: drop the `adaptor_overrides` lookup in `_tool_budgets` and
    the last assertion returns the deployment's 5, or the table's 3 with the
    `[llm.budgets]` layer gone too.
    """
    from nora.agent.dreamer import _tool_budgets
    from nora.agent.tools import NATIVE_TOOLS

    adaptor = _Adaptor(sink=_Sink())
    tools = [_named_tool("note_read")]

    assert NATIVE_TOOLS["note_read"].budget == 3, (
        "the table's own value — if this changes, the assertions below are "
        "comparing against the wrong baseline and say nothing"
    )
    assert _tool_budgets(tools, adaptor) == {"note_read": 3}, "table only"
    assert _tool_budgets(tools, adaptor, {"note_read": 5}) == {"note_read": 5}, (
        "[llm.budgets] over the table"
    )
    assert _tool_budgets(
        tools, adaptor, {"note_read": 5}, {"note_read": 8},
    ) == {"note_read": 8}, "the adaptor's own number is the narrowest and wins"
    assert _tool_budgets(tools, adaptor, None, {"note_read": 8}) == {
        "note_read": 8
    }, "and it does not need a deployment-wide layer under it"


def test_an_adaptor_tool_ceiling_for_a_tool_it_lacks_grants_nothing():
    """The built-only rule, for the third layer too: a name in `budgets` is
    what turns `DreamGuard`'s default-deny into permission, so a per-adaptor
    ceiling naming a tool this adaptor does not hold must not conjure an
    entry. `tool_call_faults` refuses that config at BOOT; this is the second
    half, for the window where a hot-reloaded objective drops a tool the
    config still names."""
    from nora.agent.dreamer import _tool_budgets

    budgets = _tool_budgets(
        [_named_tool("note_read")], _Adaptor(sink=_Sink()),
        None, {"note_read": 8, "remind": 99},
    )

    assert budgets == {"note_read": 8}
    assert "remind" not in budgets


# ---------------------------------------------------------------------------
# per-fire sampling (design/dreamer-agent-loop/37, issue #118) — N model calls
# per run, unioned. Measured on `note-candidates`: one shape of entry is
# recognised in ~30% of samples while no negative sample ever produced one, so
# the union raises recall on the exact axis where precision cannot be spent.
# ---------------------------------------------------------------------------


def test_an_adaptor_with_no_schedule_takes_one_sample(monkeypatch):
    """The default is one call. Sampling is opt-in per adaptor; an adaptor
    nobody scheduled must not silently start costing N times as much."""
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}')
    assert runtime.samples_for("test-adaptor") == 1
    assert runtime.samples_for("anything") == 1


def test_a_schedule_with_samples_reports_them():
    assert AdaptorSchedule(
        min_interval_seconds=1, batch_limit=1, samples=5,
    ).samples == 5


def test_a_schedule_cannot_ask_for_zero_samples():
    """A zero would make `run` take no model call at all and report
    `unavailable` forever, which reads as an outage rather than as the
    misconfiguration it is."""
    with pytest.raises(ValueError, match="samples must be >= 1"):
        AdaptorSchedule(min_interval_seconds=1, batch_limit=1, samples=0)


async def test_three_samples_make_three_calls_and_union_their_items(monkeypatch):
    """The whole point: an item ONE sample saw reaches the sink. The three
    replies are disjoint, so a runtime that kept only the last would write
    one row and a runtime that kept the first would write one row."""
    sink = _Sink()
    runtime, metrics = _runtime(
        monkeypatch,
        replies=[
            '{"observations": [{"term": "a"}]}',
            '{"observations": [{"term": "b"}]}',
            '{"observations": [{"term": "c"}]}',
        ],
        schedules=_sampled(3),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "ok"
    assert outcome.written == 3, "every sample's item reached the sink"
    (_key, proposals) = sink.commits[0]
    assert [p.payload["term"] for p in proposals] == ["a", "b", "c"]
    assert metrics.get(
        "nora_dream_sample_total", adaptor="test-adaptor", status="ok") == 3


async def test_identical_items_across_samples_collapse(monkeypatch):
    """Two samples that answer the same thing are one answer, not two rows.
    Byte-identical only: two samples that PHRASE one arrangement differently
    are the tier's own identity question, and a runtime that guessed at it
    would be deciding a tier's dedupe rule for it."""
    sink = _Sink()
    runtime, _ = _runtime(
        monkeypatch,
        replies=[
            '{"observations": [{"term": "a"}, {"term": "b"}]}',
            '{"observations": [{"term": "a"}]}',
        ],
        schedules=_sampled(2),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.written == 2
    (_key, proposals) = sink.commits[0]
    assert [p.payload["term"] for p in proposals] == ["a", "b"]


async def test_one_unreadable_sample_does_not_lose_the_others(monkeypatch):
    """R-27, one level down. A sample that returned prose is `unavailable` FOR
    THAT SAMPLE and is counted; the run read the batch through the samples
    that worked, so it is `ok` and the watermark may advance. Reporting the
    whole run unavailable here would hold a batch that WAS read."""
    sink = _Sink()
    runtime, metrics = _runtime(
        monkeypatch,
        replies=[
            "I could not find anything useful.",
            '{"observations": [{"term": "a"}]}',
        ],
        schedules=_sampled(2),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "ok"
    assert outcome.written == 1
    assert metrics.get(
        "nora_dream_sample_total", adaptor="test-adaptor", status="ok") == 1
    assert metrics.get(
        "nora_dream_sample_total", adaptor="test-adaptor",
        status="unavailable") == 1


async def test_every_sample_unreadable_is_an_unavailable_run(monkeypatch):
    """The positive control for the test above: when NO sample was readable,
    nobody read the batch, and that is the outcome that holds the cursor."""
    sink = _Sink()
    runtime, metrics = _runtime(
        monkeypatch,
        replies=["prose", "more prose"],
        schedules=_sampled(2),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "unavailable"
    assert outcome.written == 0
    assert sink.commits == [], "nothing is written on a run nobody could read"
    assert metrics.get(
        "nora_dream_sample_total", adaptor="test-adaptor",
        status="unavailable") == 2


async def test_a_sampling_run_that_finds_nothing_is_nothing_found(monkeypatch):
    """Every sample readable and every sample empty is «I looked and there was
    nothing», not an outage — the distinction the whole three-state contract
    exists for, and it must survive the union."""
    sink = _Sink()
    runtime, _ = _runtime(
        monkeypatch,
        replies=['{"observations": []}', '{"observations": []}'],
        schedules=_sampled(2),
    )

    outcome = await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert outcome.status == "nothing_found"
    assert len(sink.commits) == 1, "the decay contract still gets its call"


async def test_a_blank_prompt_takes_no_sample_at_all(monkeypatch):
    """Sampling multiplies MODEL CALLS, and the cheapest call is the one not
    made: an adaptor whose `inputs` render nothing still short-circuits before
    the first sample, N or no N."""

    class _Blank(_Adaptor):
        async def inputs(self, *, key, batch):
            return ""

    sink = _Sink()
    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        schedules=_sampled(5),
    )

    outcome = await runtime.run(adaptor=_Blank(sink=sink), key="g1", batch=())

    assert outcome.status == "nothing_found"
    assert metrics.get(
        "nora_dream_sample_total", adaptor="test-adaptor", status="ok") == 0


async def test_each_sample_gets_its_own_guard(monkeypatch):
    """`DreamGuard` owns a `CallLedger`, and a ledger is SPENT STATE. One
    guard shared across samples would let the first sample's tool calls count
    against the second's budget, so an adaptor with N samples would get a
    fraction of its declared budget per sample — inert for the tool-free
    adaptors that ship, and exactly the shape that stays inert until it does
    not.

    Asserted on OBJECT IDENTITY rather than on a call count, because the
    defect is one object reused: a test that counted vetoes would pass on a
    shared guard whose budget simply never bound."""
    guards: list[Any] = []

    def _create(**kwargs):
        guards.append(kwargs["guard"])
        return object()

    monkeypatch.setattr(factory, "create_dream_agent", _create)
    monkeypatch.setattr(factory, "invoke", lambda *a, **kw: _completed(_Result()))
    monkeypatch.setattr(factory, "final_text", lambda result: '{"observations": []}')
    runtime = DreamerRuntime(
        models=_Models(), instructions=_InstructionStore(), tools=_ToolSource(),
        budgets={}, default_budget=BUDGET,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=Metrics(), model_name="deepseek-v4-flash",
        schedules=_sampled(3),
    )

    await runtime.run(adaptor=_Adaptor(sink=_Sink()), key="g1", batch=())

    assert len(guards) == 3, "three samples, three agents"
    assert len({id(g) for g in guards}) == 3, (
        "each sample must get a fresh guard; a shared one shares its ledger"
    )


# ---------------------------------------------------------------------------
# issue #60 — the store phases are bounded, and a timeout is COUNTED
# ---------------------------------------------------------------------------
#
# `asyncio.wait_for` appeared exactly once in this module, around
# `factory.invoke`. `adaptor.inputs` and `adaptor.commit` were bare awaits, and
# the engine sets no `command_timeout` — so a connection hanging mid-query left
# `dream_loop` holding `<adaptor>:<key>` in its `running` set FOREVER. The skip
# was counted, under `reason="in_flight_adaptor"`, which is also what an
# ordinary long run produces: the tier went quiet and the only symptom was an
# absence.
#
# Every test here drives a REAL hang (an `Event` nobody sets) under a tiny
# bound, so what is asserted is that the runtime RETURNED — the release — and
# not merely that a number moved.


class _Wedged(_Adaptor):
    """An adaptor whose named phase never returns. `blocked` is set as soon as
    the phase is entered, so a test can tell "the store hung" from "the store
    was never called" — a bound that passed because nothing ran would be the
    exit-code-5 failure one layer up."""

    def __init__(self, *, sink: _Sink, phase: str) -> None:
        super().__init__(sink=sink)
        self._phase = phase
        self.blocked = asyncio.Event()
        self._forever = asyncio.Event()

    async def _hang(self) -> None:
        self.blocked.set()
        await self._forever.wait()

    async def inputs(self, *, key, batch):
        if self._phase == "inputs":
            await self._hang()
        return f"inputs for {key}"

    async def commit(self, *, key, proposals, now_ms):
        if self._phase == "commit":
            await self._hang()
        return await self.sink.commit(key=key, proposals=proposals, now_ms=now_ms)


async def test_a_wedged_inputs_is_cut_off_counted_and_the_unit_released(
    monkeypatch,
):
    """The whole defect in one test: the run RETURNS (so `dream_loop`'s
    `finally: running.discard(unit)` fires and this key ticks again), it is
    R-27's `unavailable` (nobody read this batch, the watermark holds), and the
    timeout lands on its own series rather than being absorbed into the
    `unavailable` a 429 also produces."""
    adaptor = _Wedged(sink=_Sink(), phase="inputs")
    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        store_timeout_seconds=0.01,
    )

    outcome = await asyncio.wait_for(
        runtime.run(adaptor=adaptor, key="g1", batch=()), timeout=5,
    )

    assert adaptor.blocked.is_set(), "inputs was never entered — nothing hung"
    assert outcome.status == "unavailable"
    assert metrics.get(
        "nora_dream_store_timeout_total", adaptor="test-adaptor",
        phase="inputs",
    ) == 1
    assert metrics.get(
        "nora_dream_store_timeout_total", adaptor="test-adaptor",
        phase="commit",
    ) == 0, "a wedged inputs must not be reported as a wedged commit"


async def test_a_wedged_commit_is_cut_off_counted_and_the_unit_released(
    monkeypatch,
):
    """The other half. `commit` is where the tier WRITES, so this is the one
    whose cancellation costs something — and the trade is deliberate: a commit
    cut off mid-sequence leaves live state the next tick re-selects, while a
    commit that never returns leaves the unit dead and nothing re-selects
    that."""
    adaptor = _Wedged(sink=_Sink(), phase="commit")
    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        store_timeout_seconds=0.01,
    )
    row = TranscriptRow(
        id=7, sender_id="p1", sender_name="A", ts=1, body="big walk",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )

    outcome = await asyncio.wait_for(
        runtime.run(adaptor=adaptor, key="g1", batch=(row,)), timeout=5,
    )

    assert adaptor.blocked.is_set(), "commit was never entered — nothing hung"
    assert outcome.status == "unavailable"
    assert metrics.get(
        "nora_dream_store_timeout_total", adaptor="test-adaptor",
        phase="commit",
    ) == 1


async def test_the_blank_prompt_commit_is_bounded_too(monkeypatch):
    """The decay path's `commit(key, ())` is a SECOND call site, on the branch
    that never reaches the model at all. It was the easiest one to forget: the
    fire condition returns before any of the model machinery is built, so a
    bound applied only to the normal path would leave the watermark-free
    adaptors — which take this branch on most ticks — exactly as exposed as
    before."""
    class _BlankWedged(_Wedged):
        async def inputs(self, *, key, batch):
            return "   \n  "

    adaptor = _BlankWedged(sink=_Sink(), phase="commit")
    runtime, metrics = _runtime(monkeypatch, store_timeout_seconds=0.01)

    outcome = await asyncio.wait_for(
        runtime.run(adaptor=adaptor, key="g1", batch=()), timeout=5,
    )

    assert adaptor.blocked.is_set()
    assert outcome.status == "unavailable"
    assert metrics.get(
        "nora_dream_store_timeout_total", adaptor="test-adaptor",
        phase="commit",
    ) == 1


async def test_a_store_phase_inside_the_bound_is_not_cut_off(monkeypatch):
    """**The positive control, and it is not optional here.** Every assertion
    above is satisfied by a runtime that cancels `inputs` unconditionally, and
    a bound that fires on a healthy store is worse than none: it would turn
    every tick into an `unavailable` and hold every watermark. So one slow —
    but returning — phase must come through untouched, with the counter at
    zero."""
    slept: list[float] = []

    class _Slow(_Adaptor):
        async def inputs(self, *, key, batch):
            await asyncio.sleep(0.01)
            slept.append(0.01)
            return f"inputs for {key}"

    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}',
        store_timeout_seconds=5.0,
    )

    outcome = await runtime.run(adaptor=_Slow(sink=_Sink()), key="g1", batch=())

    assert slept == [0.01], "the slow phase did not run to completion"
    assert outcome.status == "ok"
    assert metrics.get(
        "nora_dream_store_timeout_total", adaptor="test-adaptor",
        phase="inputs",
    ) == 0


def test_the_bound_is_the_adaptors_own_declared_call_count(monkeypatch):
    """The bound is `store_calls_per_tick` x the config allowance, read off
    the ADAPTOR — so an adaptor that grows a store call widens its own hold
    and nobody else's. Asserted as arithmetic rather than through a timing
    test, because a timing test cannot tell 10x5 from 50 flat."""
    runtime, _ = _runtime(monkeypatch, store_timeout_seconds=5.0)

    class _Ten(_Adaptor):
        store_calls_per_tick = 10

    assert runtime.store_bound_for(_Adaptor(sink=_Sink())) == 5.0
    assert runtime.store_bound_for(_Ten(sink=_Sink())) == 50.0


# ---------------------------------------------------------------------------
# issue #63 — a batch the screen takes ALL of is named, not inferred
# ---------------------------------------------------------------------------


async def test_a_batch_the_screen_rejects_entirely_is_counted(monkeypatch):
    """The silent failure, given a name.

    An adaptor whose objective asks the model to echo a row id back has every
    item dropped here — `dream_screen` walks every string in an item and
    `egress._UUID` matches any hyphenated uuid — so the tick spends a model
    call, `accept` receives `()`, `commit` writes nothing, and the run reports
    `ok`. Three adaptors shipped that way and every counter stayed healthy.

    The STATUS stays `ok` on purpose (R-27: a run whose model DID answer may
    not claim «there was nothing»), which is exactly why the condition needs
    its own series."""
    sink = _Sink()
    adaptor = _Adaptor(sink=sink)
    runtime, metrics = _runtime(
        monkeypatch,
        raw='{"observations": [{"term": "a", "id": '
            '"7c9e6679-7425-40de-944b-e07fc1f90ae7"}]}',
    )

    outcome = await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert outcome.status == "ok", (
        "the model answered; `nothing_found` would be a claim about the world "
        "that this run cannot make"
    )
    assert outcome.written == 0
    assert metrics.get(
        "nora_dream_empty_after_screen_total", adaptor="test-adaptor") == 1
    assert metrics.get(
        "nora_dream_screened_total", adaptor="test-adaptor",
        reason="identifier") == 1


async def test_a_batch_that_survives_the_screen_is_not_counted(monkeypatch):
    """The control for the series above: `nora_dream_empty_after_screen_total`
    has to be structurally zero on a healthy adaptor, or it is an alarm nobody
    can act on."""
    runtime, metrics = _runtime(
        monkeypatch, raw='{"observations": [{"term": "big walk"}]}')

    outcome = await runtime.run(adaptor=_Adaptor(sink=_Sink()), key="g1", batch=())

    assert outcome.status == "ok"
    assert metrics.get(
        "nora_dream_empty_after_screen_total", adaptor="test-adaptor") == 0


async def test_a_model_that_returned_nothing_is_not_counted_as_a_rejection(
    monkeypatch,
):
    """And the other side of the same line: an EMPTY envelope is
    `nothing_found` and is not a screen event. Without this the counter would
    also fire on every quiet tick, which is the fastest way to make an alarm
    worthless."""
    runtime, metrics = _runtime(monkeypatch, raw='{"observations": []}')

    outcome = await runtime.run(adaptor=_Adaptor(sink=_Sink()), key="g1", batch=())

    assert outcome.status == "nothing_found"
    assert metrics.get(
        "nora_dream_empty_after_screen_total", adaptor="test-adaptor") == 0


async def test_a_collaborators_own_timeout_is_not_counted_as_the_runtimes(
    monkeypatch,
):
    """**Since 3.10 `socket.timeout IS TimeoutError`**, so an aiohttp read
    timeout, an asyncpg `command_timeout`, or any other collaborator's own
    clock raises the exact class this handler catches. Counted as «the runtime
    cut it off», that puts a false reading on the one series an operator would
    page on — and the log line says «a store call did not return» about a
    store that answered promptly, with an error.

    `asyncio.timeout` plus `cm.expired()` is the only thing that separates
    them; `wait_for`'s bare `except TimeoutError` cannot. The run is still
    `unavailable` either way — an inner timeout IS a failed read — so what is
    asserted here is the DIAGNOSIS, not the outcome."""
    class _InnerTimeout(_Adaptor):
        async def inputs(self, *, key, batch):
            raise TimeoutError("the driver's own command_timeout")

    runtime, metrics = _runtime(monkeypatch, store_timeout_seconds=30.0)

    outcome = await runtime.run(
        adaptor=_InnerTimeout(sink=_Sink()), key="g1", batch=())

    assert outcome.status == "unavailable", "a failed read is still unavailable"
    assert metrics.get(
        "nora_dream_store_timeout_total", adaptor="test-adaptor",
        phase="inputs",
    ) == 0, (
        "the collaborator's own clock was reported as the runtime's wall "
        "clock — the series that exists to say «a store wedged» now says it "
        "about a store that answered"
    )


def test_the_phase_label_vocabulary_is_derived_from_the_type():
    """`nora_dream_store_timeout_total{phase}` takes its values from three
    bare literals at the call sites. A typo there creates a NEW series rather
    than an error and the old one silently goes flat, which is the failure
    `nora.domain.bounds.BOUND_SITES` and `NoteCuratorAdaptor._VOCABULARIES`
    already exist to prevent. Asserted against the module's own `Literal`, so
    the two cannot drift."""
    import ast
    import inspect

    emitted: set[str] = set()
    tree = ast.parse(inspect.getsource(dreamer))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "_bounded_store_call"):
            continue
        for kw in node.keywords:
            if kw.arg == "phase" and isinstance(kw.value, ast.Constant):
                emitted.add(kw.value.value)

    assert emitted, "the walk found no phases at all — it is not measuring"
    assert emitted <= dreamer.STORE_PHASES, (
        f"emitted but not in the vocabulary: {emitted - dreamer.STORE_PHASES}"
    )
    assert {"inputs", "commit"} == dreamer.STORE_PHASES


async def test_a_dream_pass_publishes_its_own_deadline_to_its_tools(monkeypatch):
    """A room-scoped pass can replay `media_fetch`, whose describer is built
    from the `[llm]` loop; the pass's OWN `timeout_seconds` is what must bound
    it (DEC-197 review)."""
    from nora.domain.turn_deadline import seconds_left

    seen: list[float | None] = []

    async def _invoke(*a, **kw):
        seen.append(seconds_left())
        return _Result()

    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: object())
    monkeypatch.setattr(factory, "invoke", _invoke)
    monkeypatch.setattr(factory, "final_text", lambda result: '{"observations": []}')
    runtime = DreamerRuntime(
        models=_Models(), instructions=_InstructionStore(), tools=_ToolSource(),
        budgets={}, default_budget=BUDGET,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=Metrics(), model_name="deepseek-v4-flash",
    )

    await runtime.run(adaptor=_Adaptor(sink=_Sink()), key="g1", batch=())

    assert seen and seen[0] is not None, seen
    assert BUDGET.timeout_seconds - 1.0 < seen[0] <= BUDGET.timeout_seconds


# ---------------------------------------------------------------------------
# design/dreamer-agent-loop/43 §3: the rows a pass loaded reach its grounding
# ---------------------------------------------------------------------------


class _LoadingToolSource(_ToolSource):
    """Stands in for `load_transcript` running inside the loop: the build
    receives the runtime's `on_rows` and the "tool" reports two rows to it."""

    def __init__(self, rows: Sequence[Any]) -> None:
        super().__init__()
        self._rows = tuple(rows)
        self.on_rows_given: list[bool] = []

    def build(  # type: ignore[override]
        self, *, key: str, names: frozenset[str], unit: str | None = None,
        adaptor: str = "", grant: Any = None, on_rows: Any = None,
    ) -> list[Any]:
        self.on_rows_given.append(on_rows is not None)
        if on_rows is not None:
            on_rows(self._rows)
        return super().build(
            key=key, names=names, unit=unit, adaptor=adaptor, grant=grant,
        )


class _SawAdaptor(_Adaptor):
    def __init__(self, *, sink: _Sink) -> None:
        super().__init__(sink=sink)
        self.saw_calls: list[tuple[str, tuple[Any, ...], int]] = []

    def saw(self, *, key: str, rows: Sequence[Any]) -> None:
        # `len(self.accepted)` pins the ORDER: grounding grows before accept.
        self.saw_calls.append((key, tuple(rows), len(self.accepted)))


async def test_rows_a_tool_loaded_reach_the_adaptor_before_accept(monkeypatch):
    rows = ("row-7", "row-8")
    tools = _LoadingToolSource(rows)
    runtime, _ = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}', tools=tools,
    )
    adaptor = _SawAdaptor(sink=_Sink())

    outcome = await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert outcome.status == "ok"
    assert tools.on_rows_given == [True]
    assert adaptor.saw_calls == [("g1", rows, 0)]


async def test_an_adaptor_with_no_saw_gets_the_unchanged_build(monkeypatch):
    """No `saw`: the build is not handed `on_rows`, so every other adaptor's
    tools are exactly what they were (no ids on a transcript read)."""
    tools = _LoadingToolSource(("row-7",))
    runtime, _ = _runtime(
        monkeypatch, raw='{"observations": [{"term": "a"}]}', tools=tools,
    )
    await runtime.run(adaptor=_Adaptor(sink=_Sink()), key="g1", batch=())
    assert tools.on_rows_given == [False]


# ---------------------------------------------------------------------------
# an adaptor's own write verbs (design/self-growth/10 §3, `character_revise`)
# ---------------------------------------------------------------------------


class _VerbAdaptor(_Adaptor):
    """Answers through a verb, never an envelope: `stage` validates nothing
    and stages its text; `accept` returns what was staged."""

    def __init__(self, *, sink: _Sink) -> None:
        super().__init__(sink=sink)
        self.staged: list[str] = []

    def verbs(self, *, key: str):
        async def stage(text: str) -> dict[str, object]:
            """Stage one thing.

            Args:
                text: What to stage.
            """
            self.staged.append(text)
            return {"status": "ok"}

        return [stage]

    async def accept(self, *, key, items):
        self.accepted.append(items)
        return tuple(self.staged)


async def test_a_verb_adaptor_holds_its_verbs_screened_and_needs_no_envelope(monkeypatch):
    """The verb is offered beside the read tools, under a budget (R-7's
    default-deny would veto a name with none), a call carrying an identifier is
    REFUSED before the verb runs (the envelope screen, applied to arguments),
    and a loop that returned with no envelope at all is a readable answer:
    what the verb staged is committed with the run's provenance, `ok`."""
    sink = _Sink()
    adaptor = _VerbAdaptor(sink=sink)
    answers: list[dict[str, object]] = []
    built: dict[str, Any] = {}

    async def _invoke(agent, prompt, **_kw):
        (verb,) = [t for t in built["tools"] if t.tool_name == "stage"]
        answers.append(await verb(text="they decide by attrition"))
        answers.append(await verb(text="ring her on +85291234567"))
        return _Result()

    runtime, metrics = _runtime(monkeypatch, invoke=_invoke, raw="prose, no envelope")
    monkeypatch.setattr(
        factory, "create_dream_agent",
        lambda **kw: built.update(tools=kw["tools"], guard=kw["guard"]) or object(),
    )

    outcome = await runtime.run(adaptor=adaptor, key="g1", batch=())

    assert outcome.status == "ok" and outcome.written == 1
    assert answers[0] == {"status": "ok"}
    assert answers[1]["status"] == "refused" and answers[1]["reason"] == "identifier"
    assert adaptor.staged == ["they decide by attrition"]
    ((_key, (proposal,)),) = sink.commits
    assert proposal.payload == "they decide by attrition"
    assert proposal.instruction_sha256 == SHA
    assert built["guard"].evaluate("stage", {"text": "x"}) is None
    assert metrics.get(
        "nora_dream_screened_total", adaptor="test-adaptor", reason="identifier",
    ) == 1


async def test_a_verb_adaptor_that_staged_nothing_is_nothing_found(monkeypatch):
    """No envelope and nothing staged: the loop read the batch and wrote
    nothing, «I looked and there was nothing». Whether that settles what it
    was asked is the adaptor's `accept` to judge (it may raise)."""
    sink = _Sink()
    runtime, _ = _runtime(monkeypatch, raw="")
    outcome = await runtime.run(adaptor=_VerbAdaptor(sink=sink), key="g1", batch=())
    assert outcome.status == "nothing_found"
    assert sink.commits == [("g1", ())]


class _WritingVerbAdaptor(_VerbAdaptor):
    """A verb that WRITES when called (the Lessons shape): it asks for the
    run's provenance, which a staging verb never sees."""

    def __init__(self, *, sink: _Sink) -> None:
        super().__init__(sink=sink)
        self.provenance: list[Any] = []

    def verbs(self, *, key: str, provenance):  # type: ignore[override]
        self.provenance.append(provenance)
        return super().verbs(key=key)


async def test_a_verb_that_writes_when_called_is_handed_the_runs_provenance(monkeypatch):
    """The three fields a `Proposal` carries reach a verb that takes
    `provenance` (design/self-growth/40, L2): the instruction digest read for
    THIS run, the model, and an `established_by` naming the adaptor and key. A
    staging verb (no such argument) is called exactly as before, above."""
    sink = _Sink()
    adaptor = _WritingVerbAdaptor(sink=sink)
    runtime, _ = _runtime(monkeypatch, raw="")
    await runtime.run(adaptor=adaptor, key="g1", batch=())
    (prov,) = adaptor.provenance
    assert prov.instruction_sha256 == SHA and prov.model == "deepseek-v4-flash"
    assert prov.established_by.startswith("dream:test-adaptor:g1:")


async def test_a_run_whose_verbs_wrote_is_ok_not_nothing_found(monkeypatch):
    """A verb that writes when called answers through what `commit` says
    landed (DEC-299 (4)): measured live, four lessons written read as
    `nothing_found`, which says «there was nothing»."""
    sink = _Sink()

    class _Landed(_WritingVerbAdaptor):
        async def commit(self, *, key, proposals, now_ms):
            await super().commit(key=key, proposals=proposals, now_ms=now_ms)
            return 1

    runtime, _ = _runtime(monkeypatch, raw="")
    outcome = await runtime.run(adaptor=_Landed(sink=sink), key="g1", batch=())
    assert outcome.status == "ok" and outcome.written == 1
