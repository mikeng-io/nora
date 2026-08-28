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
    WORKSPACE_SAFE_TOOLS,
    AdaptorBudget,
    AdaptorToolError,
    DreamerRuntime,
    RegistryToolSource,
    parse_envelope,
    verify_tool_subsets,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
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
    budgets: dict[str, AdaptorBudget] | None = None,
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
        budgets=budgets or {},
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

    assert tools.built == [("g1", frozenset({"now", "web_search"}))]


# ---------------------------------------------------------------------------
# the tool source — two independent guards, and a hand-list that cannot rot
# ---------------------------------------------------------------------------


def test_the_workspace_safe_set_is_a_subset_of_the_read_only_ceiling():
    """A workspace-safe tool that is not read-only would be an act verb granted
    to a background loop through the back door."""
    from nora.agent.registry import ToolRegistry

    assert ToolRegistry().read_only_tools() >= WORKSPACE_SAFE_TOOLS


def test_every_read_only_tool_is_classified_workspace_safe_or_room_scoped():
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
        # `who_said` (DEC-146) is room-scoped in the strictest sense in the
        # table: it answers out of ONE turn's prefill window, which a Dreamer
        # tick does not have at all — `RegistryToolSource` builds it with no
        # transcript, so every call there is honestly `unavailable`. Room-scoped
        # is the fail-closed side and the correct one; it does not move.
        "who_said",
        "load_transcript", "group_members", "lookup_person", "about",
        "ask_memory", "search_memory", "recall_lookups", "media_fetch",
        "list_reminders", "list_events", "suggest_venue", "search_gifs",
        # The note tier's two read verbs (landed 2026-08-21). Notes are
        # workspace-keyed, so these are not room-scoped in the way
        # `load_transcript` is — but they are classified here, fail-closed,
        # because nothing wires their collaborators into `RegistryToolSource`
        # yet. A Dreamer adaptor asking for one today would get a tool that
        # cannot answer, which is the `nothing_found`-for-`unavailable`
        # substitution this classification exists to prevent. Move them when
        # the note tier's curator adaptor lands and the wiring is real.
        "note_read", "note_search",
        # `search_knowledge` for the same reason and a sharper one: `23` §9
        # grants it to Dreamer adaptors, and it resolves its workspace THROUGH
        # THE ROOM, while `RegistryToolSource.build` hands a workspace-keyed
        # adaptor a room with no platform and the workspace in the
        # conversation slot. Policy cannot name that, so the verb answers
        # `unavailable` on every call — which is `08-state.md` §10's stated
        # trap, "the seam looking like it works while every call fails
        # closed". Move it when the key path is SUPPLIED rather than derived.
        "search_knowledge",
        # `music_status` (design/discord-music/07-agent-tool.md) reports on
        # ONE Discord conversation's voice session — the exact shape
        # `load_transcript`/`where_is` are room-scoped for, not a
        # workspace-wide fact like `weather`. It also refuses on
        # `room.platform != "discord"`, which the Dreamer's synthetic room
        # (`RegistryToolSource`) never satisfies today — classified here,
        # fail-closed, the same "not yet actually reachable" reasoning
        # `note_read`/`note_search` are.
        "music_status",
    }
    unclassified = ToolRegistry().read_only_tools() - WORKSPACE_SAFE_TOOLS - room_scoped
    assert unclassified == set(), (
        "read-only tool(s) classified neither workspace-safe nor room-scoped: "
        + ", ".join(sorted(unclassified))
    )


def test_the_tool_source_never_builds_a_tool_outside_the_ceiling():
    """The name-level guard. Asserted against the REAL registry rather than a
    stub, because the thing under test is the intersection with a set the
    registry derives."""
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None, honcho=None,
        metrics=Metrics(),
    )

    built = source.build(key="g1", names=frozenset({"send_reply", "make_artifact"}))

    assert built == []


def test_the_tool_source_builds_the_workspace_safe_tools():
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


# ---- the second axis: room-scoped vs workspace-safe -----------------------


def test_a_workspace_keyed_adaptor_is_refused_a_room_scoped_tool():
    """The protection three documents claimed and nothing applied.

    `RegistryToolSource`'s docstring, `verify_tool_subsets`' docstring, DEC-133
    and the release fragment all asserted that a workspace-keyed adaptor is
    restricted to `WORKSPACE_SAFE_TOOLS`. A review probe built
    `about`/`load_transcript`/`search_memory` for a workspace key with no
    complaint — nothing checked `key_kind` anywhere.

    **The failure it would have caused is worse than a missing tool.** A
    workspace name is not a conversation id, so `load_transcript` against one
    does not raise — it returns `{"messages": []}`. That is «I could not
    check» reported as «there was nothing», the one substitution R-27 exists to
    forbid, arriving in the tier whose whole purpose is to keep those two
    apart."""
    adaptor = _Adaptor(
        sink=_Sink(), tools=frozenset({"web_search", "load_transcript"}),
    )
    adaptor.key_kind = "workspace"

    with pytest.raises(AdaptorToolError) as exc:
        verify_tool_subsets([adaptor], tools=_ToolSource(
            ceiling=frozenset({"web_search", "load_transcript", "now"})
        ))

    # The safe one is not BLAMED — it still appears further down, in the
    # message's list of what IS allowed, which is the useful half of a refusal.
    assert "declares room-scoped tools: load_transcript." in str(exc.value)
    assert "Workspace-safe tools are:" in str(exc.value)


def test_a_scope_keyed_adaptor_may_hold_a_room_scoped_tool():
    """The other direction, and the reason the rule is on `key_kind` rather
    than on the tool: a room-scoped tool is exactly right for an adaptor whose
    key IS a room. The candidate extractor reads its room; only the
    adjudicator, keyed on a workspace, has no room to bind one to."""
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
    adaptor.key_kind = "workspace"
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
    times a day per workspace on a prompt that says «Claims to settle: (none)».

    `commit(key, ())` is still called, so the decay contract survives: a
    watermarked adaptor over a quiet room renders a blank prompt too, and that
    is exactly where `LanguageConsolidator`'s time-driven rescore lives."""
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
