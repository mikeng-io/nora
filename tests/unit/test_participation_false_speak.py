"""R-97 — a participation turn that went looking and came back empty stays
silent.

WHAT THIS FILE PINS, AND WHY IT IS NOT THE LIVE SUITE
-----------------------------------------------------
`make eval-participation` grades the corpus against the real model; without
R-97 the turn takes the floor to say it found nothing (e.g. «查咗但搵唔到實質嘢 —
Redbeet 冇公開過下個 update 嘅日子»). A stochastic suite cannot be a regression
gate, so what is pinned here is the same five cases with the model's
trajectory SCRIPTED to that shape: a lookup, its R-27
answer, and the send she made off the back of it. The room's verdict is
`Trajectory.sent` — the room-visible truth, the signal `ParticipationVerdict`
already grades on.

`FALSE_SPEAK_TWINS` is therefore a table of four false-speaks and one correct
silence, and `test_the_five_silence_twins_stay_silent` pins that all five stay
silent. The `lookups=` argument in `nora.app.fire` and the R-97 branch in
`nora.agent.guards` are what keep the four out of the room.

THE THREE CONTROLS ARE THE POINT
--------------------------------
A rule that silenced every participation turn would pass the table above and
be worthless, so three tests pin what must NOT change: the same lookup
answering `ok` still speaks (`test_a_lookup_that_found_something_still_speaks`
— only the tool's R-27 status varies between it and the first twin); a turn
that never opened a tool still speaks (the fast path, the whole
grounded half of the suite); and a TAG turn whose lookup came back empty still
answers, because there somebody asked and «I looked and there is nothing» is
what she owes them.
"""

from __future__ import annotations

import json

import pytest

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.agent.tools import NATIVE_TOOLS
from nora.obs.metrics import Metrics
from nora.testing import ScriptedTurn
from tests.offline_eval_evaluators import ParticipationVerdict
from tests.offline_eval_harness import Case, run_case

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()
LOOKUPS = ToolRegistry().lookup_tools()


@pytest.fixture(autouse=True)
def _omniroute_key(monkeypatch):
    """`run_case` loads the real config, which is fail-closed on the LLM key.
    Nothing here reaches a provider — the model is scripted — so a placeholder
    is the honest value (`tests/test_evals_participation.py` carries the same
    fixture)."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")


# ── The five silence twins, with a scripted trajectory ───────────────────────
#
# `who_said` is the lookup for every case that "searched": it is the one read
# on the table whose R-27 answer this harness can choose deterministically and
# for real — words that are NOT in the seeded window are a true `nothing_found`
# (the window was read and they are not in it), and words that ARE in it are a
# true `ok`. No result is stubbed anywhere in this file's end-to-end half; the
# real tool runs and answers.
#
# `other-service-command` uses `weather` instead, because the shape there is
# the OTHER non-ok status — «the forecast is unreliable» is a
# wiring gap relayed as news, which R-27 calls `unavailable` and which R-97
# must refuse on exactly as it refuses a `nothing_found`.

FALSE_SPEAK_TWINS = [
    pytest.param(
        "stalled-question-unknown",
        "when is the next Raft update?",
        ["when is the next Raft update?", "anyway 聽日 lunch 食咩"],
        ("who_said", {"words": "the next Raft update ships on"}),
        "查咗但搵唔到實質嘢 — Redbeet 冇公開過下個 update 嘅日子",
        id="stalled-question-unknown",
    ),
    pytest.param(
        "commitment-followup-unknown",
        "so did anyone book the restaurant for Saturday?",
        ["Saturday dinner 8pm 好唔好", "上次話咗要訂嗰間 tapas 啦"],
        ("who_said", {"words": "I booked the tapas place"}),
        "冇 booking 喺我度 — 我搵唔到有人訂咗枱嘅記錄",
        id="commitment-followup-unknown",
    ),
    pytest.param(
        "planning-context-unknown",
        "weekday or weekend then?",
        ["Tai O 一日遊幾時去好", "咁即係得星期日或者平日"],
        ("who_said", {"words": "we settled on Sunday"}),
        "冇人揀咗邊日 — 我搵返嘅嘢都冇講到最後點決定",
        id="planning-context-unknown",
    ),
    pytest.param(
        "other-service-command",
        "/weather tomorrow",
        ["聽日會唔會落雨"],
        ("weather", {"place": "Sham Shui Po"}),
        "而家個 forecast 唔太可靠，我讀唔到聽日嘅數",
        id="other-service-command",
    ),
]


async def _drive(name: str, body: str, window: list[str], script: list[ScriptedTurn]):
    """One untagged message into a seeded room, through the REAL
    `ParticipationTrigger` -> `FirePath.speak` path with only the model
    scripted (`evals.harness.run_case`, the same drive `make
    eval-participation` uses)."""
    case = Case.from_dict({
        "name": name, "input": body, "expect": "silence",
        "seed": {"transcript": window},
    })
    return await run_case(case, script=script)


@pytest.mark.parametrize("name,body,window,lookup,reply", FALSE_SPEAK_TWINS)
async def test_the_five_silence_twins_stay_silent(name, body, window, lookup, reply):
    """All five stay silent. Each case looks, comes back empty, and tries to take the
    floor to say so; the room must hear nothing.

    The assertion is on `Trajectory.sent` rather than on the veto, because
    "send_reply was refused" and "the room heard nothing" are different
    verdicts and only the second is the one R-97 is about.
    """
    trajectory = await _drive(name, body, window, [ScriptedTurn(
        tool_calls=[lookup, ("send_reply", {"text": reply})], final_text=reply,
    )])

    assert trajectory.sent == [], (
        f"{name} false-spoke: the room heard {trajectory.sent!r}"
    )
    assert ParticipationVerdict("silence").evaluate(trajectory).passed
    # The declined floor is the HEALTHY outcome, not a malfunction — the same
    # partition `test_a_declined_floor_is_recorded_and_is_not_a_silent_failure`
    # pins. A refusal that landed on `nora_silent_failures_total` would fix the
    # room and break the instrument.
    assert trajectory.participation_silence == 1
    assert trajectory.silent_failures == {}


async def test_the_fifth_twin_that_declined_on_its_own_still_declines():
    """`status-update`: she already declines it, and
    a rule that only ever ADDS silence has to be shown not to have turned that
    correct silence into an error (an R-8 cap cut, a silent failure, a
    stop_reason of None). It is the one twin that is not a false-speak."""
    trajectory = await _drive(
        "status-update", "check this out 👉 https://example.com/that-thing",
        ["你哋睇咗尋晚嗰場波未", "睇咗 勁"],
        [ScriptedTurn(final_text="nothing here for me")],
    )
    assert trajectory.sent == []
    assert trajectory.stop_reason == "end_turn"
    assert trajectory.participation_silence == 1
    assert trajectory.silent_failures == {}


# ── The controls: what R-97 must NOT take down ──────────────────────────────


async def test_a_lookup_that_found_something_still_speaks():
    """THE falsification pair. Byte for byte the first twin above — same case,
    same window, same tool, same reply — with ONE thing varied: the words
    handed to `who_said` are in the window, so the real tool answers `ok`
    instead of `nothing_found`.

    If this goes red the rule is not "she came back empty", it is "she opened a
    tool", and the whole `-known` half of the suite goes with it.
    """
    window = ["when is the next Raft update?", "anyway 聽日 lunch 食咩"]
    case = Case.from_dict({
        "name": "stalled-question-known", "input": "when is the next Raft update?",
        "expect": "speak", "seed": {"transcript": window},
    })
    trajectory = await run_case(case, script=[ScriptedTurn(
        tool_calls=[
            ("who_said", {"words": "when is the next Raft update?"}),
            ("send_reply", {"text": "Miles 問過，冇人答到"}),
        ],
        final_text="Miles 問過，冇人答到",
    )])
    assert trajectory.sent == ["Miles 問過，冇人答到"]
    assert ParticipationVerdict("speak").evaluate(trajectory).passed


async def test_a_turn_that_never_looked_still_speaks():
    """The fast path: the value of speaking is visible in what is
    already in front of her — the transcript, standing, notes, memory — and she
    answers without opening a tool at all. Zero lookups is not "came back
    empty", and a rule that read it as one would silence the majority of the
    grounded half."""
    trajectory = await _drive(
        "planning-context-known", "weekday or weekend then?",
        ["Tai O 一日遊幾時去好", "咁即係得星期日或者平日"],
        [ScriptedTurn(tool_calls=[("send_reply", {"text": "星期日啦"})],
                      final_text="星期日啦")],
    )
    assert trajectory.sent == ["星期日啦"]


async def test_a_tag_turn_that_found_nothing_still_answers():
    """The occasion boundary, through the real tag path and not asserted off
    the keyword that sets it.

    This line is the reason R-97 is not a general
    rule: in tagged mode «I looked and there is nothing» is an acceptable
    obligation, because she was called. Arming the rule on the tag path would
    turn every honest "I checked and it isn't there" into `no_send_call` — a
    silent failure on the one path where silence is never the right answer
    (R-5).

    **`final_text=""` is load-bearing here and nowhere else in this file.** The
    tag path RECOVERS prose written without a `send_reply` call into a delivery
    (DEC-93) — a gate participation is exempt from — so a
    tag turn that ends with the same words it tried to send lands them in the
    room whether the guard refused or not, and the assertion below would hold
    against a rule that DID fire. Ending with no prose is what leaves the send
    as the only way words reach the room.
    """
    case = Case.from_dict({
        "name": "tag", "input": "nora when is the next Raft update?",
        "metadata": {"trigger": True},
        "seed": {"transcript": ["when is the next Raft update?"]},
    })
    trajectory = await run_case(case, script=[ScriptedTurn(
        tool_calls=[
            ("who_said", {"words": "the next Raft update ships on"}),
            ("send_reply", {"text": "查咗但搵唔到，冇人講過個日子"}),
        ],
        final_text="",
    )])
    assert trajectory.sent == ["查咗但搵唔到，冇人講過個日子"]


# ── The guard itself, with no harness around it ─────────────────────────────


class _After:
    """`AfterToolCallEvent` as Strands really shapes it: the tool's own dict is
    `json.dumps`-ed into `content[0]["text"]`, which is what makes
    `_unwrap_tool_status` necessary at all (see its docstring). `result=None`
    is the status-free shape — a tool that carries no R-27 axis."""

    def __init__(self, name: str, *, status: str | None = None,
                 cancel_message: str | None = None,
                 exception: Exception | None = None) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": "t1"}
        self.exception = exception
        self.cancel_message = cancel_message
        payload = "True" if status is None else json.dumps({"status": status})
        self.result = {"toolUseId": "t1", "status": "success",
                       "content": [{"text": payload}]}


def _guard(*, lookups: frozenset[str] | None = LOOKUPS, metrics=None) -> ReplyGuard:
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                      metrics=metrics or Metrics(), lookups=lookups)


@pytest.mark.parametrize("status", ["nothing_found", "unavailable", "refused"])
def test_every_non_ok_answer_refuses_the_send(status):
    """All three non-`ok` states, not just `nothing_found`. R-27 keeps them
    apart because they mean different things about the WORLD; for the question
    R-97 asks — does she now hold something the room does not — they are the
    same answer, and pinning only the first would let a `weather` outage relay
    itself into a room nobody asked in."""
    metrics = Metrics()
    guard = _guard(metrics=metrics)
    guard._after_tool(_After("who_said", status=status))
    assert guard.evaluate("send_reply", {"text": "I couldn't find anything"})
    assert metrics.get("nora_guard_vetoes_total", rule="R-97") == 1


def test_one_ok_among_several_empties_lifts_the_refusal():
    """ACCUMULATED, not last-call-wins: a turn whose first lookup found the
    answer and whose second came back empty still holds the answer. The
    opposite reading would make the refusal depend on the order she happened to
    call her tools in."""
    guard = _guard()
    guard._after_tool(_After("who_said", status="ok"))
    guard._after_tool(_After("search_memory", status="nothing_found"))
    assert guard.evaluate("send_reply", {"text": "Grant said it"}) is None


def test_a_lookup_that_was_cancelled_or_raised_never_looked_anywhere():
    """`_after_tool`'s two early returns, asked of R-97's counters. A call the
    guard vetoed and a call that raised both produced no body, so neither is
    "she went looking" — counting them would refuse a send on the strength of
    tool calls that never ran, which is the exact `unavailable`-as-
    `nothing_found` substitution in the guard's own bookkeeping."""
    guard = _guard()
    guard._after_tool(_After("who_said", cancel_message="R-7: no"))
    guard._after_tool(_After("search_memory", exception=RuntimeError("boom"),
                             status="nothing_found"))
    assert guard.evaluate("send_reply", {"text": "here you go"}) is None


def test_the_rule_is_off_unless_a_caller_arms_it():
    """`lookups=None` is the tag path — she was called, so «I looked and there
    is nothing» is an answer she owes, not noise. The default matters as much
    as the rule: every existing construction site keeps its
    behaviour."""
    guard = _guard(lookups=None)
    guard._after_tool(_After("who_said", status="nothing_found"))
    assert guard.evaluate("send_reply", {"text": "I couldn't find anything"}) is None


def test_a_status_free_result_is_not_a_lookup():
    """`load_transcript` is `read_only` and deliberately carries NO status —
    its empty list IS the answer (ADR 0009). It is out of `lookup_tools()`
    by declaration, and this pins the consequence: reading older messages and
    then speaking is not "went looking and came back empty"."""
    guard = _guard()
    guard._after_tool(_After("load_transcript"))
    assert guard.evaluate("send_reply", {"text": "Grant said it earlier"}) is None


# ── The set R-97 watches, derived ───────────────────────────────────────────


def test_lookup_tools_is_the_reads_that_declare_the_three_states():
    """Both halves of the AND, pinned by the members that would move if either
    were dropped.

    `load_transcript` is the `read_only`-without-a-status case and `note_update`
    is the `three_state`-without-`read_only` case; a rule armed on either would
    refuse sends it has no business refusing. Non-empty is asserted because an
    armed-but-empty set is a rule that can never fire — DEC-42's inert feature,
    which reads exactly like a working one.
    """
    lookups = ToolRegistry().lookup_tools()
    assert lookups, "R-97 armed on an empty set could never fire"
    assert {"who_said", "search_memory", "weather", "note_read"} <= lookups
    assert "load_transcript" not in lookups          # read_only, no status axis
    assert "note_update" not in lookups              # three_state, but a WRITE
    assert "send_reply" not in lookups
    # And the set really is derived from the table, not a copy of it: every
    # member declares both fields.
    for name in lookups:
        spec = NATIVE_TOOLS[name]
        assert spec.read_only and spec.output_contract == "three_state", name
