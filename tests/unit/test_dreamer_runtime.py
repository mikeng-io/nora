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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from nora.agent import factory
from nora.agent.dreamer import (
    MAX_ITEMS,
    AdaptorBudget,
    AdaptorToolError,
    DreamerRuntime,
    parse_envelope,
    verify_tool_subsets,
)
from nora.domain.ports import Proposal
from nora.obs.metrics import Metrics

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
        self.built: list[tuple[str, frozenset[str]]] = []

    def read_only(self) -> frozenset[str]:
        return self._ceiling

    def build(self, *, key: str, names: frozenset[str]) -> list[Any]:
        self.built.append((key, names))
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

    def __init__(
        self, *, sink: _Sink, tools: frozenset[str] = frozenset({"now"}),
    ) -> None:
        self.tools = tools
        self.sink = sink
        self.accepted: list[Sequence[Mapping[str, object]]] = []

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        return f"inputs for {key}"

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        self.accepted.append(items)
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
) -> tuple[DreamerRuntime, Metrics]:
    """Wires the runtime with the Strands boundary replaced.

    `create_dream_agent`, `invoke` and `final_text` are the three points where
    this module touches the SDK; patching exactly those three keeps every test
    below free of strands while leaving the runtime's own logic — envelope,
    screen, provenance, outcome — running for real."""
    metrics = metrics or Metrics()
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: object())
    monkeypatch.setattr(
        factory, "invoke",
        invoke or (lambda *a, **kw: _completed(_Result())),
    )
    monkeypatch.setattr(factory, "final_text", lambda result: raw or "")
    runtime = DreamerRuntime(
        models=_Models(),
        instructions=instructions or _InstructionStore(),
        tools=tools or _ToolSource(),
        budgets={},
        default_budget=BUDGET,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=metrics,
        model_name="deepseek-v4-flash",
    )
    return runtime, metrics


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


def test_an_adaptor_inside_the_ceiling_starts():
    verify_tool_subsets([_Adaptor(sink=_Sink())], tools=_ToolSource())


async def test_the_ceiling_is_rechecked_per_run_and_reports_unavailable(monkeypatch):
    """The second enforcement point, and it is not redundant: the objective
    corpus hot-reloads and an adaptor rebuilt between ticks would otherwise be
    measured once, at a moment that has passed. `unavailable` rather than a
    raise — a background loop must not take its own clock down — but loud."""
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"now", "make_artifact"}))
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
    """The decay path. `commit(key, ())` is a REAL call a sink may act on —
    `LanguageConsolidator` rescores for an empty batch because decay is
    time-driven — so treating an empty result as "nothing to do, skip the call"
    silently fades nothing and a dormant room's lexicon stays live describing a
    slang era that ended months ago."""
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
    runtime, metrics = _runtime(monkeypatch, invoke=_hang)
    runtime._budgets["test-adaptor"] = AdaptorBudget(   # noqa: SLF001
        turns=1, total_tokens=10, output_tokens=10, timeout_seconds=0.05,
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

    assert tools.built == [("g1", frozenset({"now", "web_search"}))]
