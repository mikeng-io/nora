"""These are GRADERS, kept under `tests/` because their only consumers are tests, and because
the `evals/` import contract (`[tool.importlinter]` in `pyproject.toml`) applies to
`evals/` alone. `NoRedundantRead` is R-15's subject.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from nora.agent.registry import ToolRegistry
from nora.engagement.stages.deliver import BUDGET_STOP_REASONS

#: The reply guard's per-tool budgets, from the REGISTRY.
#:
#: `evals/` may reach `nora.app` and nothing else, so a grader living under `evals/` could
#: not read `nora.agent.registry` directly. Under `tests/` it can: `tests/` has no import
#: contract, because a test is allowed to know how the thing is built.
REPLY_GUARD_BUDGETS = ToolRegistry().budgets()


@dataclass(frozen=True)
class Result:
    passed: bool
    reason: str = ""


class RepliesAtLeastOnce:
    """A triggered turn must produce a reply.

    Was `ExactlyOnceSendReply` (n == 1), the cap-of-1 invariant. Multi-message
    (DEC-42) makes two or three replies valid, so the exactly-one upper bound is
    gone from here; the lower bound (she MUST reply when called) stays, and the
    upper bound (never past the cap) is owned by `ToolBudget`, which reads the
    same `send_reply` budget — so the two are one number, not two (DEC-10)."""

    def evaluate(self, t) -> Result:
        n = t.tool_calls.count("send_reply")
        return Result(n >= 1, f"send_reply called {n} time(s), expected at least 1")


class NoSendWithoutTrigger:
    def evaluate(self, t) -> Result:
        n = t.tool_calls.count("send_reply")
        return Result(n == 0, f"send_reply called {n} time(s) on a no-trigger case")


class ToolBudget:
    def evaluate(self, t) -> Result:
        counts = Counter(t.tool_calls)
        for name, count in counts.items():
            budget = REPLY_GUARD_BUDGETS.get(name)
            if budget is not None and count > budget:
                return Result(False, f"{name} called {count} time(s), budget is {budget}")
        return Result(True)


class NoRedundantRead:
    def evaluate(self, t) -> Result:
        if t.prefill_sufficient and "load_transcript" in t.tool_calls:
            return Result(False, "load_transcript called despite a sufficient pre-fill (R-15)")
        return Result(True)


class HonchoFedOncePerReply:
    def evaluate(self, t) -> Result:
        delivered = bool(t.sent)
        expected = 1 if delivered else 0
        ok = t.honcho_feeds == expected
        return Result(ok, f"honcho fed {t.honcho_feeds} time(s), expected {expected}")


# A turn ending in one of these is an ERROR for eval purposes, never a correct silence
# (see `evaluate`).
#
# DERIVED from `nora.engagement.stages.deliver.BUDGET_STOP_REASONS`, which is itself
# copied from `strands.types.event_loop.StopReason`. `Trajectory.stop_reason` is read off
# `nora_llm_stop_reason_total{reason}`, whose vocabulary is Strands';
# `invocation_timeout`, `llm_error`, `no_send_call` and `limit_request_timeout` are
# `nora_silent_failures_total` labels, a different series, so a hand-written set would
# hold strings that can never appear and the guard against a model outage certifying the
# flip gate would be dead. A hand-copied vocabulary is what drifts; this one is the same
# object the code increments against.
_BUDGET_STOP_REASONS = frozenset(BUDGET_STOP_REASONS)


class ParticipationVerdict:
    """The participation suite's speak/silence verdict.

    Grades `Trajectory.sent` — the room-visible truth. Live-mode `tool_calls` is
    reconstructed from Prometheus counters and is BLIND to a `ReplyGuard`-vetoed call, so
    "called send_reply" and "the room heard it" are different verdicts. `sent` matches the
    no-indicate design: the room sees a message or nothing, and the eval grades exactly
    that.

    `expect` is `"speak"` or `"silence"`; anything else is a case-authoring error and
    fails closed.
    """

    def __init__(self, expect: str) -> None:
        if expect not in ("speak", "silence"):
            raise ValueError(
                f"ParticipationVerdict expects 'speak' or 'silence', got {expect!r}"
            )
        self._expect = expect

    def evaluate(self, t) -> Result:
        spoke = bool(t.sent)
        # A turn that never successfully invoked the model is NOT a correct silence — it
        # is an ERROR, and grading it as PASS would let a live model outage certify the
        # flip gate. `FirePath._turn` catches timeouts/errors and returns a failure string
        # without raising, so `Trajectory.sent` stays empty either way; `stop_reason` is
        # the signal that disambiguates.
        #
        # `None` is the load-bearing half. A transport failure, a timeout or an
        # `llm_error` never reaches the stop-reason counter at all — `FirePath` leaves the
        # field at its initialiser — so the ONLY signal that the model was never invoked
        # is the absence of a reason. Without this a dead provider produces a silent turn
        # indistinguishable from a chosen one.
        if not spoke and (t.stop_reason is None or t.stop_reason in _BUDGET_STOP_REASONS):
            return Result(
                False,
                f"expected {self._expect}; the model was never successfully "
                f"invoked (stop_reason={t.stop_reason!r}) — ERROR, not silence",
            )
        ok = spoke == (self._expect == "speak")
        return Result(
            ok,
            f"expected {self._expect}; the room {'heard' if spoke else 'heard nothing'}"
            + (f": {t.sent[-1][:80]!r}" if spoke else ""),
        )


# --- The intent graph -------------------------------------------------
#
# The evaluators above grade one turn's *mechanics* (did she reply, stay in budget, feed
# Honcho once). The intent graph grades her *routing*: given a user journey, did she reach
# for the RIGHT tool and not a plausible-but-wrong neighbour? This is the "same same but
# different" problem — "外面凍唔凍?" and "我而家喺邊?" are near-identical short questions that must
# route to `weather` and `where_is` respectively, and a model that confuses them fails
# silently (it answers, just wrongly). A frozen-reply judge cannot see that; the tool path
# can.


@dataclass(frozen=True)
class Intent:
    """One user-journey's intended path through the tool graph.

    `required` is the tool(s) a correct trajectory MUST reach for this journey.
    `budget` is the number of *work* tool calls (everything except the terminal
    `send_reply`) beyond which we call the turn flailing rather than converging —
    the web_fetch "took that many rounds of search" failure the operator flagged.
    The sibling set a `NoSiblingLeakage` check forbids is NOT stored here: it is
    derived from the `IntentGraph`, so confusability is declared once, centrally,
    not re-listed per journey.
    """

    name: str
    required: frozenset[str]
    budget: int = 3


class IntentGraph:
    """The tool-*confusability* graph: an undirected adjacency where an edge
    `(a, b)` means "a near-identical user query could plausibly route to either
    tool `a` or tool `b`". This is the graph Miles asked for — the amplifying
    layer. Journeys (`Intent`) declare only their CORRECT tool(s); the
    plausible-but-wrong siblings a trajectory must avoid are read off this graph
    (the neighbours of the correct tools, minus the correct tools themselves).

    Defining confusability in ONE place is the point: add a journey and its
    leakage check comes for free from the edges already here; add an edge (a new
    tool the model starts confusing) and every existing journey touching those
    nodes tightens at once. A per-journey sibling list would rot the moment a new
    tool joined a cluster.
    """

    def __init__(self, edges: Iterable[tuple[str, str]]) -> None:
        self._adj: dict[str, set[str]] = {}
        for a, b in edges:
            if a == b:
                continue
            self._adj.setdefault(a, set()).add(b)
            self._adj.setdefault(b, set()).add(a)

    def nodes(self) -> frozenset[str]:
        """Every tool named anywhere in the graph — for the measurability guard
        (`UNMEASURABLE_LIVE`) that keeps an unreconstructable tool out of it."""
        return frozenset(self._adj)

    def neighbours(self, tool: str) -> frozenset[str]:
        return frozenset(self._adj.get(tool, set()))

    def siblings_of(self, required: Collection[str]) -> frozenset[str]:
        """The plausible-but-wrong tools for a journey whose correct path is
        `required`: every graph-neighbour of a required tool that is not itself
        required. `required` tools shadow each other's edges — a journey that
        legitimately needs two tools in the same cluster is not leaking by
        calling both."""
        req = set(required)
        near: set[str] = set()
        for tool in req:
            near |= self._adj.get(tool, set())
        return frozenset(near - req)


# Tools whose live calls the harness CANNOT reconstruct by name, so they must not
# be graded as intent-graph nodes. `evals.harness._tool_calls_from_metrics` reads
# the two per-tool call counters (`nora_tool_calls_total`, `nora_room_lookups_total`),
# both of which carry a `tool` label. `media_fetch` records `nora_media_fetch_total`,
# which has NO `tool` label — a media_fetch call is invisible to the reconstruction,
# so grading it would read a blindness as a verdict (the CLAUDE.md instrument rule).
# `test_no_intent_graph_tool_is_live_unmeasurable` fails closed if a graph node or a
# journey ever names one of these.
UNMEASURABLE_LIVE = frozenset({"media_fetch"})


# The seed confusability graph for Nora's live tool set. The clusters are the ones the
# operator actually hit; these edges are what the journeys grade against:
#
# - LOCAL-vs-WEB: a HK-specific ask has a dedicated tool, and reaching for `web_search`
#   instead is a flail.
# - LOCATION: "where am I" (`where_is`, live GPS) vs "where's my work" (`saved_places`, a
#   remembered label) vs "what's at this place" (`place_info`) vs "how do I get there"
#   (`transit`/`route`) — four short questions, four different tools.
# - MEMORY: `about` / `ask_memory` / `search_memory` are three verbs over the same store
#   and the model picks among them on phrasing alone.
SEED_INTENT_GRAPH = IntentGraph(
    [
        # local HK tools vs the generic web
        ("weather", "web_search"),
        ("transit", "web_search"),
        ("place_info", "web_search"),
        ("saved_places", "web_search"),
        ("parking_status", "web_search"),
        ("parking_status", "place_info"),
        # find_parking (discover 'nearby') vs parking_status (one named lot)
        ("find_parking", "parking_status"),
        ("find_parking", "web_search"),
        ("find_parking", "place_info"),
        # the ambient-short-question pair: "外面凍唔凍?" → weather vs "我而家喺邊?" → where_is is
        # the graph's own motivating example, and it needs its edge or that exact
        # confusion is unpoliced.
        ("weather", "where_is"),
        # the location cluster
        ("where_is", "saved_places"),
        ("where_is", "place_info"),
        ("saved_places", "place_info"),
        ("place_info", "transit"),
        ("transit", "route"),
        ("place_info", "route"),
        # the memory verbs — READ-READ edges DELIBERATELY ABSENT:
        # `about`/`ask_memory`/`search_memory`/`search_knowledge` are different READ verbs
        # over the SAME store, and a model that reaches for a raw-message search
        # before/after a profile read is doing a normal multi-step lookup, not a routing
        # error. An `(about, search_memory)` edge would grade that reasonable behaviour as
        # a sibling leak. Only the WRITE lane is a true routing fork: answering 記唔記得 by
        # WRITING, or a 記低 ask by reading — the pair these edges police.
        #
        # the write lane: 「記低」 a settled person-fact, 「記唔記得」 a recall ask, and the note
        # lane that refuses person facts by design. These edges are what make a recall ask
        # answered by WRITING — or a 記低 ask answered by reading elsewhere — visible to
        # NoSiblingLeakage; without them the remember tool could never fail a routing eval
        # for leaking.
        ("remember", "about"),
        ("remember", "note_create"),
    ]
)


class PathMatchesIntent:
    """Every tool the journey REQUIRES was actually reached.

    The positive half of routing: a weather journey must call `weather`. Missing
    a required tool means she answered from the wrong source (or from nothing).
    """

    def __init__(self, intent: Intent) -> None:
        self._intent = intent

    def evaluate(self, t) -> Result:
        called = set(t.tool_calls)
        missing = self._intent.required - called
        if missing:
            reason = f"intent {self._intent.name!r}: missing required tool(s) {sorted(missing)}"
        else:
            reason = f"intent {self._intent.name!r}: reached {sorted(self._intent.required)}"
        return Result(not missing, reason)


class NoSiblingLeakage:
    """No plausible-but-wrong sibling tool was reached.

    The negative half of routing, and the "same same but different" check: the
    forbidden set is derived from the `IntentGraph`, so this asks "did she stay in
    the right cluster?" A weather journey that calls `web_search` or `where_is`
    leaked, even if it also called `weather` and even if the reply reads fine.
    """

    def __init__(self, intent: Intent, graph: IntentGraph) -> None:
        self._intent = intent
        self._siblings = graph.siblings_of(intent.required)

    def evaluate(self, t) -> Result:
        leaked = self._siblings & set(t.tool_calls)
        return Result(
            not leaked,
            f"intent {self._intent.name!r}: leaked into sibling tool(s) {sorted(leaked)}"
            if leaked else f"intent {self._intent.name!r}: no sibling leakage",
        )


class ConvergesInBudget:
    """The journey reached its answer within its work-tool-call budget.

    Counts every tool call EXCEPT the terminal `send_reply` (delivering the
    answer is the goal, not flail, and multi-message allows up to three). Over
    budget is the runaway-search shape the operator flagged: the same tool called
    round after round because the model would not decide it had enough.
    """

    def __init__(self, intent: Intent) -> None:
        self._intent = intent

    def evaluate(self, t) -> Result:
        work = [c for c in t.tool_calls if c != "send_reply"]
        n = len(work)
        return Result(
            n <= self._intent.budget,
            f"intent {self._intent.name!r}: {n} work tool-call(s) "
            f"({work}), budget {self._intent.budget}",
        )
