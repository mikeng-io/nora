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
    AdaptorGrant,
    AdaptorSchedule,
    AdaptorToolError,
    DreamerRuntime,
    HomeRoom,
    RegistryToolSource,
    parse_envelope,
    proactive_grant,
    tool_subset_faults,
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
        self.built: list[tuple[str, frozenset[str], str | None]] = []
        self.grants: list[tuple[str, Any]] = []

    def read_only(self) -> frozenset[str]:
        return self._ceiling

    def build(
        self, *, key: str, names: frozenset[str], workspace: str | None = None,
        adaptor: str = "", grant: Any = None,
    ) -> list[Any]:
        self.built.append((key, names, workspace))
        if grant is not None and (grant.acts or grant.home_room):
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
    replies: list[str] | None = None,
    schedules: dict[str, AdaptorSchedule] | None = None,
    grants: dict[str, AdaptorGrant] | None = None,
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


async def test_a_workspace_keyed_adaptor_supplies_its_key_as_the_workspace(
    monkeypatch,
):
    """The key-path fix (design/dreamer-agent-loop/32 §3.4), pinned at the
    seam it crosses: a workspace-keyed adaptor's key IS the workspace, so the
    runtime SUPPLIES it to the tool source rather than letting the tool layer
    derive one from a room that does not exist (`08-state.md` §10's trap —
    «the seam looking like it works while every call fails closed»)."""
    tools = _ToolSource(ceiling=frozenset({"now", "web_search"}))
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"now"}))
    adaptor.key_kind = "workspace"
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}', tools=tools)

    await runtime.run(adaptor=adaptor, key="ridgeline", batch=())

    assert tools.built == [("ridgeline", frozenset({"now"}), "ridgeline")]


async def test_a_scope_keyed_adaptor_supplies_no_workspace(monkeypatch):
    """The other half: a scope key is a conversation id, and handing it to
    the workspace slot would make every workspace-scoped verb read the wrong
    store. `None` keeps the resolver path — the room answers as before."""
    tools = _ToolSource()
    sink = _Sink()
    runtime, _ = _runtime(monkeypatch, raw='{"observations": []}', tools=tools)

    await runtime.run(adaptor=_Adaptor(sink=sink), key="g1", batch=())

    assert tools.built[0][2] is None


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
        # `where_been` (DEC-155) answers from the derived substrate but is
        # gated by the SAME R-31 room/circle seam as `where_is` — the guard is
        # the room, which a workspace-keyed Dreamer tick does not have, so
        # room-scoped is the fail-closed side and the correct one. (It is
        # deliberately NOT in WORKSPACE_SAFE_TOOLS: spec §7.4 records the
        # grant was NOT made.)
        "where_been",
        # `reverse_geocode` (DEC-156) is a pure coordinate -> address lookup
        # with no room state in its ARGUMENTS, which is exactly why it must be
        # here rather than beside `weather`. Since 13c530aa it is the ONLY tool
        # that resolves a street address, and R-57 has it fill the turn-scoped
        # disclosed set `route` screens against. The coordinate it is handed
        # comes from a live fix, and everything that gates a live fix —
        # R-31's roster seam, `location_trusted` — is a property of the ROOM,
        # which a workspace-keyed Dreamer tick does not have. Granting it would
        # let a tick turn a person's live position into a street address with
        # no such gate anywhere in the path. Same argument as `where_been`
        # above, and the same fail-closed answer.
        "reverse_geocode",
        # The two parking verbs (2026-08-31), classified with the live-location
        # family they belong to (`transit`/`route`/`place_info` sit here too).
        # `find_parking` can even read the asker's PRESENCE when called with no
        # place — a room-shaped input a workspace-keyed tick does not have — so
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
        "list_reminders", "list_events", "suggest_venue", "search_gifs",
        # `music_status` (design/discord-music/07-agent-tool.md) reports on
        # ONE Discord conversation's voice session — the exact shape
        # `load_transcript`/`where_is` are room-scoped for, not a
        # workspace-wide fact like `weather`. It also refuses on
        # `room.platform != "discord"`, which the Dreamer's synthetic room
        # (`RegistryToolSource`) never satisfies today — classified here,
        # fail-closed, the same "not yet actually reachable" reasoning
        # `note_read`/`note_search` were.
        "music_status",
    }
    # The workspace-safe half is `WORKSPACE_SAFE_TOOLS` itself, read from the
    # module. Since design/dreamer-agent-loop/32 §3.4 that set includes the
    # workspace-scoped reads this test used to hold HERE, fail-closed:
    # `note_read`/`note_search` resolved their workspace THROUGH THE ROOM and
    # the Dreamer's synthetic room could never answer that, and
    # `search_memory_workspace` shipped WITH the fix. They moved when the key
    # path became SUPPLIED — `DreamerRuntime.run` hands a workspace-keyed
    # adaptor's key to the tool layer AS the workspace — which is exactly the
    # move these entries' own comments said to make. The knowledge tier's five
    # verbs were here too and went with that tier.
    workspace_scoped = {
        "note_read", "note_search", "search_memory_workspace",
    }
    assert workspace_scoped <= WORKSPACE_SAFE_TOOLS, (
        "a workspace-scoped read fell out of WORKSPACE_SAFE_TOOLS — 32 §3.4 "
        "grants these only with the supplied key path"
    )
    unclassified = ToolRegistry().read_only_tools() - WORKSPACE_SAFE_TOOLS - room_scoped
    assert unclassified == set(), (
        "read-only tool(s) classified neither workspace-safe nor room-scoped: "
        + ", ".join(sorted(unclassified))
    )


def test_the_tool_source_never_builds_a_tool_outside_the_ceiling():
    """The name-level guard, and — since DEC-162 — its ONE named exception.

    REWRITTEN ON PURPOSE (design/dreamer-agent-loop/39 D3): the original pin
    said no act verb is ever built. What is true now is that no act verb is
    built WITHOUT A GRANT, and that a grant admits exactly the verbs it
    names — `remind` for the proactive adaptor — and nothing beside them.
    Asserted against the REAL registry rather than a stub, because the
    thing under test is the intersection with a set the registry derives.
    """
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={"friends": HomeRoom(
            workspace="friends", platform="signal", conversation_id="g1",
        )},
    )
    asked = frozenset({"send_reply", "make_artifact", "remind", "now"})

    # No grant: the whole pre-DEC-162 ceiling. `now` is read-only and builds;
    # the three act verbs do not, `remind` included.
    ungranted = _names(source.build(key="friends", names=asked, workspace="friends"))
    assert ungranted == {"now"}

    # The proactive grant: `remind` and only `remind` joins the read set.
    granted = _names(source.build(
        key="friends", names=asked, workspace="friends", adaptor="proactive",
        grant=proactive_grant(),
    ))
    assert granted == {"now", "remind"}
    assert "send_reply" not in granted and "make_artifact" not in granted


def _names(built: list[Any]) -> set[str]:
    return {
        getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in built
    }


class _RecordingRegistry:
    """`ToolRegistry`'s two verbs the tool source calls, with every keyword
    `build` was handed kept — the home-room arguments (39 D2) are VALUES on
    that call and nothing else observes them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def read_only_tools(self) -> frozenset[str]:
        return frozenset({"where_is", "now"})

    def build(self, **kw: Any) -> list[Any]:
        self.calls.append(kw)
        return []


def test_a_home_room_grant_builds_the_room_the_operator_named():
    """39 D2's two review findings, pinned: `self_ids` so Nora's own account
    is skipped rather than counted `unnamed`, and `location_trusted` from
    the home room's `location` flag — both of which the pre-DEC-162 build
    left at their defaults, so `where_is` refused in every configuration.
    Plus the destination: the home room's conversation, never the workspace
    NAME the key carries (39 D3)."""
    registry = _RecordingRegistry()
    roster = object()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={"friends": HomeRoom(
            workspace="friends", platform="signal", conversation_id="g1",
            location_trusted=True,
        )},
        rosters={"signal": roster},
        self_ids=frozenset({"+85200000000"}),
    )

    source.build(
        key="friends", names=frozenset({"where_is"}), workspace="friends",
        adaptor="proactive", grant=proactive_grant(),
    )

    (call,) = registry.calls
    assert call["conversation_id"] == "g1"
    assert call["conversation_type"] == "group"
    assert call["location_trusted"] is True
    assert call["supplied_workspace"] == "friends"
    assert call["dream_adaptor"] == "proactive"
    assert call["deliverer"] is None
    room = call["room"]
    assert room.platform == "signal"
    assert room._roster_source is roster
    assert room._self_ids == frozenset({"+85200000000"})
    assert room.tagger is None


def test_a_home_room_grant_with_no_home_room_falls_back_closed():
    """The grant said «stand in the home room» and the workspace names none:
    no acts, the synthetic empty room, the key as the conversation — exactly
    the pre-DEC-162 build — and never a guessed room."""
    registry = _RecordingRegistry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(), home_rooms={},
    )

    source.build(
        key="wedding", names=frozenset({"where_is", "remind"}), workspace="wedding",
        adaptor="proactive", grant=proactive_grant(),
    )

    (call,) = registry.calls
    assert call["conversation_id"] == "wedding"
    assert call["location_trusted"] is False
    assert call["room"].platform == ""
    assert call["room"]._roster_source is None


def test_without_a_grant_the_build_is_byte_for_byte_the_old_one():
    """Every other adaptor: the key as the conversation, the empty room, the
    strict gate — a home room in the map is inert for an adaptor not
    granted it."""
    registry = _RecordingRegistry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,
        metrics=Metrics(),
        home_rooms={"friends": HomeRoom(
            workspace="friends", platform="signal", conversation_id="g1",
            location_trusted=True,
        )},
    )

    source.build(key="friends", names=frozenset({"now"}), workspace="friends")

    (call,) = registry.calls
    assert call["conversation_id"] == "friends"
    assert call["location_trusted"] is False
    assert call["room"].platform == ""


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
    """The protection three documents claimed and nothing applied — and,
    since DEC-162, the one adaptor it does not bind.

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
    apart.

    REWRITTEN ON PURPOSE (design/dreamer-agent-loop/39 D2): the refusal
    stands for every workspace-keyed adaptor WITHOUT a home-room grant, and
    is lifted for the one with it — the home room is the disclosure
    decision `WORKSPACE_SAFE_TOOLS`' own comment said nobody had made,
    made by the operator in `policy.toml`."""
    adaptor = _Adaptor(
        sink=_Sink(), tools=frozenset({"web_search", "load_transcript"}),
    )
    adaptor.key_kind = "workspace"
    source = _ToolSource(ceiling=frozenset({"web_search", "load_transcript", "now"}))

    with pytest.raises(AdaptorToolError) as exc:
        verify_tool_subsets([adaptor], tools=source)

    # The safe one is not BLAMED — it still appears further down, in the
    # message's list of what IS allowed, which is the useful half of a refusal.
    assert "declares room-scoped tools: load_transcript." in str(exc.value)
    assert "Workspace-safe tools are:" in str(exc.value)

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


def test_a_grant_admits_exactly_the_act_verbs_it_names():
    """Axis 1 with the allowance (39 D3): an act verb in `grant.acts` clears
    the ceiling for THIS adaptor; any other act verb — and the same verb on
    an adaptor with no grant — is still refused."""
    adaptor = _Adaptor(sink=_Sink(), tools=frozenset({"now", "remind"}))
    source = _ToolSource(ceiling=frozenset({"now"}))

    with pytest.raises(AdaptorToolError, match="outside the read-only ceiling: remind"):
        verify_tool_subsets([adaptor], tools=source)

    verify_tool_subsets(
        [adaptor], tools=source, grants={adaptor.name: proactive_grant()},
    )

    greedy = _Adaptor(sink=_Sink(), tools=frozenset({"now", "remind", "send_reply"}))
    with pytest.raises(AdaptorToolError, match="ceiling: send_reply"):
        verify_tool_subsets(
            [greedy], tools=source, grants={greedy.name: proactive_grant()},
        )


async def test_the_per_run_check_honours_the_same_grant_the_startup_check_does(
    monkeypatch,
):
    """One rule, two call sites, one grant (39 D3's three places). A runtime
    handed the grant runs the granted adaptor; the same runtime without it
    refuses the run `unavailable` — and the grant reaches `build`, which is
    the third place."""
    sink = _Sink()
    adaptor = _Adaptor(sink=sink, tools=frozenset({"remind", "load_transcript"}))
    adaptor.key_kind = "workspace"
    tools = _ToolSource(ceiling=frozenset({"load_transcript", "now"}))
    granted, _ = _runtime(
        monkeypatch, raw='{"observations": []}', tools=tools,
        grants={adaptor.name: proactive_grant()},
    )

    outcome = await granted.run(adaptor=adaptor, key="friends", batch=())

    assert outcome.status == "nothing_found"
    assert tools.built == [("friends", adaptor.tools, "friends")]
    assert tools.grants == [(adaptor.name, proactive_grant())]

    tools = _ToolSource(ceiling=frozenset({"load_transcript", "now"}))
    ungranted, _ = _runtime(monkeypatch, raw='{"observations": []}', tools=tools)

    outcome = await ungranted.run(adaptor=adaptor, key="friends", batch=())

    assert outcome.status == "unavailable"
    assert tools.built == []


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
    workspace-keyed research loop a verb nobody granted it — the exact shape
    `_tool_budgets`' own docstring refuses by reading BUILT tools."""
    from nora.agent.dreamer import _tool_budgets

    budgets = _tool_budgets(
        [_named_tool("web_fetch")], _Adaptor(sink=_Sink()),
        {"web_fetch": 6, "send_reply": 99},
    )

    assert budgets == {"web_fetch": 6}
    assert "send_reply" not in budgets


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
