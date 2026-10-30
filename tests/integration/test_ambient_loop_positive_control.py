"""The ambient loop's POSITIVE CONTROL, as one executable journey.

WHAT THIS FILE IS
-----------------
One scenario (11 Sep 2026, HKT) driven through the REAL stages with a fake
clock and a scripted model, asserting at every stage boundary. In one line: an
untagged question is admitted by the gate and answered by a participation turn;
the user tags her one second later, having not yet seen the answer; the tag
turn dies with `llm_error`; and **nothing more may land in any room**.

The timestamps this case replays:

| when | stage | what happened |
|---|---|---|
| 16:42:43 | Observe | Miles, untagged, on Signal: how to get to Cyberport at 10 tomorrow? |
| 16:42:54 | Decide · gate | the Relevance Gate ADMITS the closed window |
| 16:43:00 | Act · participation | the participation turn runs |
| 16:43:57 | Act | the full route is SENT — one `is_nora` row, `delivery_status=sent` |
| 16:43:57 | Observe | Miles tags her — the same second; he has not seen the answer |
| 16:45:13 | Act · tag | the tag turn dies — `APIError`, `llm_error`, staying silent |
| 16:45:14 | Orient · debrief | the fold runs over that dead turn |

The defect this guards: the debrief is handed
`<what_happened>the model call failed; nothing was sent</what_happened>` —
true of that TURN, false of the ROOM. A fold that generalises it into a
Standing position saying she sent nothing all day, and proposes a note on the
same false premise, misleads everything downstream that reads that position.

THE ASSERTIONS, EACH AT THE STAGE WHERE IT BECOMES DECIDABLE
------------------------------------------------------------
1. after the participation turn — exactly one `is_nora` row in the Signal room,
   `delivery_status=sent`  (`test_1_*`)
2. after the tag turn — `llm_error`, and no second send  (`test_2_*`)
3. after the debrief — the header is turn-scoped (`3a`), the fold is told her
   row in the window was delivered (`3a2`)
5. across the whole run — zero sends into the home room  (`test_5_*`)

(There is no stage 4; the numbers follow the test names.)

WHY IT IS NOT GREEN FOR THE WRONG REASON
----------------------------------------
Every stage that must RUN is asserted to have run, because the failure mode of
a journey case is a stage that silently never happened:

* the gate was actually asked (`gate_client.calls == 1`), and it is a REAL
  `RelevanceGate` over the shipped `participation.md`, not a trigger admitting
  everything — `build_offline_pipeline(participation_gate=...)` is the seam;
* the participation turn actually invoked its model and actually sent;
* the tag turn actually reached the model and actually raised (the scripted
  runner records its invocations; a tag that never ran would leave zero);
* the debrief fold actually ran — the scripted debrief client records its
  call, and `test_0_*` refuses a run where it made none.

WHAT IS FAKE, AND WHAT THAT COSTS
---------------------------------
Only the models and the platform. Every stage between them is production code:
the real `IngestPipeline` + `EngagementPipeline` + `Dispatcher` from
`tests.offline_pipeline.build_offline_pipeline`, the real `RelevanceGatePolicy`, the
real `RelevanceGate`, the real `ParticipationTrigger`, the real `FirePath`, the
real guards and `Deliverer`, the real `debrief.run` stage over the real
`nora/dreamer/` objectives. The stores are
`nora/testing/`'s in-memory doubles, each of which is bound to its production
sibling by a suite under `tests/contract/`.

WHY `integration/` WITH NO POSTGRES
------------------------------------
`tests/README.md` puts a test here when it is true because *"the wired pipeline
behaves, through the real composition root"*, and names Postgres under **needs**
— what the tests there happen to require, not what makes them true. This one is
true for exactly that reason: it assembles the real pipelines through
`tests.offline_pipeline.build_offline_pipeline`, the offline composition root, and
nothing it asserts would be more true against SQL — every store it touches is a
double the contract suites already bind to its production sibling. Driving it
on the real engine would buy one fact this case is not about (the SQL stores
agree with their doubles) for a Postgres container. The precedent for the shape
is `tests/unit/test_offline_pipeline_assembler.py`, which sits in `unit/`
because it asserts about a call, not about a run; this asserts about a run.

The home room is on another platform (Telegram here), served by its own `FakeAdapter` passed as
`extra_adapters`. With no adapter for its platform a send there would raise
instead of being recorded, and assertion 5 would pass vacuously. Because the
home room has its own adapter, "zero sends into the home room" is a measurement
rather than a tautology, and `test_5_*` checks that both the engagement pipeline
and the participation `FirePath` route the home room's platform to that adapter.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import httpx
import openai
import pytest

from nora.doctrine.loader import DoctrineStore
from nora.domain.models import DeliveryStatus
from nora.engagement.stages import debrief
from nora.ingest.policy import RelevanceGatePolicy
from nora.ingest.relevance_gate import Completion as GateCompletion
from nora.ingest.relevance_gate import RelevanceGate
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeMemoryStore,
    FakeRecordStore,
    InMemoryNoteStore,
    InMemoryStandingStore,
    ScriptedTurn,
    group_event,
)
from nora.testing.reminders import FakeReminderStore
from tests.builders import load_example_config, override_config
from tests.objectives import shipped_objectives
from tests.offline_pipeline import build_offline_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"
HKT = ZoneInfo("Asia/Hong_Kong")

UNIT = "ridgeline"
#: The room the thread lives in — Signal, opted in to the gate.
THREAD_ROOM = "signal-thread"
#: The room a proactive row would fire into, on its own platform and adapter
#: (module docstring). Nothing in this journey may ever send here.
HOME_ROOM = "home-room"

MILES = "+852111"

_IDENTITY_TOML = (
    '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
)


def _ms(hh: int, mm: int, ss: int) -> int:
    """A wall-clock time on 11 Sep 2026 HKT, as epoch milliseconds."""
    return int(datetime(2026, 9, 11, hh, mm, ss, tzinfo=HKT).timestamp() * 1000)


#: The scenario's clock, to the second.
T_QUESTION = _ms(16, 42, 43)
T_ANSWER = _ms(16, 43, 57)
#: The SAME second her answer landed: the tag is a RACE, not a reply to what she
#: said, so the fold has her row in its window and must not generalise past it.
T_TAG = _ms(16, 43, 57)
T_DEBRIEF = _ms(16, 45, 14)

QUESTION = "聽朝10點要由屋企到Cyberport ABC Service Centre，點去？"
ROUTE_ANSWER = "聽朝 10 點到數碼港：喺天華邨搭 969P，8:25 左右出門，轉 970 入數碼港。"

#: The position the fold writes in the scenario, scripted verbatim: this case
#: replays a fixed model output and measures what the CODE does with it — it
#: does not measure the model.
INCIDENT_STANDING = (
    "我今日冇出過任何回覆——model call 死咗，成個 turn 冇嘢送出。"
    "佢問緊聽朝點去 Cyberport，佢未收到任何路線，要補返。"
)
#: The note the same fold proposed in the scenario.
INCIDENT_NOTE = {
    "title": "Miles 聽朝 10 點去 Cyberport 路線未覆",
    "description": "model call 失敗，Nora 冇送出任何回覆",
    "uncertainty": "唔知佢係咪已經自己搵咗路線",
    "closing_condition": "Nora 成功送出路線建議",
    "body": "Miles 16:42 問聽朝 10 點由屋企去 Cyberport ABC Service Centre 點去。",
    "anchor_date": 20260912,
}


# ---------------------------------------------------------------------------
# the scripted model clients — the ONLY fakes between the stages
# ---------------------------------------------------------------------------


class _ScriptedGateClient:
    """The Relevance Gate's `ModelClient`, scripted to ADMIT.

    A real `RelevanceGate` around it, so the admit is a decision made by
    production code over the room's real transcript — the prompt is built, the
    verdict is parsed, the counters move. What is scripted is the verdict
    itself, which is the one thing a deterministic case cannot measure.
    """

    def __init__(self, *, verdict: str = "go", score: float = 0.82) -> None:
        self._body = json.dumps({"verdict": verdict, "relevance_score": score})
        self.calls: list[tuple[str, str]] = []

    @property
    def model(self) -> str:
        return "scripted-gate"

    async def complete(self, *, system: str, prompt: str) -> GateCompletion:
        self.calls.append((system, prompt))
        return GateCompletion(content=self._body, finish_reason="stop")


class _ScriptedDebriefClient:
    """The debrief's `ModelClient`, replaying the scripted position.

    The stage makes ONE call per fold. This dispatches on the objective body
    in the system prompt, and records anything else as an OTHER call answered
    with an empty object, so a second call per fold is counted rather than
    silently served.

    The reply is fixed, verbatim: the false position. The case asserts on
    what the CODE does with it.
    """

    def __init__(self, *, standing_body: str) -> None:
        self._standing_objective = shipped_objectives().objective_body(
            debrief.OBJECTIVE,
        )
        self._standing_body = standing_body
        self.standing_calls: list[str] = []
        self.other_calls: list[str] = []

    @property
    def model(self) -> str:
        return "scripted-debrief"

    async def complete(
        self, *, system: str, prompt: str, trace_attributes=None,
    ) -> debrief.Completion:
        if system.startswith(self._standing_objective):
            self.standing_calls.append(prompt)
            return debrief.Completion(
                content=self._standing_body, finish_reason="stop",
            )
        self.other_calls.append(prompt)
        return debrief.Completion(content="{}", finish_reason="stop")


class _Clock:
    """The journey's one clock, in two shapes.

    `monotonic()` is what `ParticipationTrigger` measures its settle window
    against (a float, like `time.monotonic`); `now()` is the aware wall clock
    the proactive adaptor and the reminder tools read. Both are driven off the
    same `at_ms`, so advancing the journey advances every stage's clock at once
    and the two can never disagree about what time it is.
    """

    def __init__(self, at_ms: int) -> None:
        self.at_ms = at_ms

    def advance_to(self, at_ms: int) -> None:
        self.at_ms = at_ms

    def monotonic(self) -> float:
        return self.at_ms / 1000.0

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.at_ms / 1000, tz=UTC)


# ---------------------------------------------------------------------------
# what the journey leaves behind
# ---------------------------------------------------------------------------


@dataclass
class _Journey:
    """Every stage's observable state, recorded as the journey passes it.

    A snapshot per stage rather than one final read, because half these
    assertions are about ORDER — "no second send" is a statement about what was
    true after the tag turn, and a final read cannot distinguish it from "the
    send happened later".
    """

    metrics: Metrics
    adapter: FakeAdapter
    home_adapter: FakeAdapter
    audit: FakeRecordStore
    standing: InMemoryStandingStore
    notes: InMemoryNoteStore
    reminders: FakeReminderStore
    gate_client: _ScriptedGateClient
    debrief_client: _ScriptedDebriefClient
    tag_runner: FakeAgentRunner
    participation_runner: FakeAgentRunner
    windows_closed: int = 0
    #: sends recorded immediately after the participation turn
    sent_after_participation: tuple[tuple[str, str], ...] = ()
    #: sends recorded immediately after the tag turn
    sent_after_tag: tuple[tuple[str, str], ...] = ()
    #: every send of the whole run, on either adapter, `(conversation_id, text)`
    sent_all: tuple[tuple[str, str], ...] = ()
    #: the adapters the engagement pipeline and the participation `FirePath`
    #: route the home room's platform to
    home_routes: tuple[Any, ...] = ()
    #: the fold's INPUT, as the debrief stage rendered it
    fold_input: str = ""


class _HomeAdapter(FakeAdapter):
    """The home room's platform: a `FakeAdapter` that records sends on Telegram."""

    platform = "telegram"


def _sends(*adapters: FakeAdapter) -> tuple[tuple[str, str], ...]:
    return tuple(
        (target.conversation_id, text)
        for adapter in adapters for target, text in adapter.sent
    )


def _config(tmp_path: Path) -> Any:
    """The shipped config, with the four tiers this journey crosses turned on.

    `tests/builders` is the only module allowed to build a config object
    (`tests/README.md`, R-B), and `load_example_config` reads the template
    rather than the deployment's own `config.toml`.
    """
    base = load_example_config(tmp_path)
    return override_config(base, {
        "store.data_dir": tmp_path,
        "doctrine.dir": DOCTRINE_DIR,
        "dreamer.dir": REPO_ROOT / "nora" / "dreamer",
        "identity_file": tmp_path / "identity.toml",
        "policy_file": tmp_path / "policy.toml",
        # The ambient loop, end to end: the gate decides the window, the
        # participation turn answers it, the debrief folds the tag that
        # follows, and the note tier is where the fold's proposal would land.
        "relevance_gate.enabled": True,
        "participation.enabled": True,
        "standing.enabled": True,
        "notes.enabled": True,
    })


async def _drive(tmp_path: Path) -> _Journey:
    """Run the whole journey once, recording each stage as it passes."""
    (tmp_path / "identity.toml").write_text(_IDENTITY_TOML)
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n'
        f'[[conversations]]\nplatform = "signal"\nid = "{THREAD_ROOM}"\n'
        f'label = "{THREAD_ROOM}"\npersons = "*"\nrelevance_gate = true\n'
        f'unit = "{UNIT}"\n'
        # The home room is on ANOTHER platform, served by its own adapter;
        # a thread plus a home room are two rooms of one unit.
        f'[[conversations]]\nplatform = "telegram"\nid = "{HOME_ROOM}"\n'
        f'label = "{HOME_ROOM}"\npersons = "*"\n'
        f'unit = "{UNIT}"\nhome_room = true\n'
    )
    cfg = _config(tmp_path)
    persons = load_persons(tmp_path / "identity.toml")
    policy = load_policy(tmp_path / "policy.toml")
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        cfg.doctrine.dir, strict=cfg.doctrine.strict, reload="never",
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, metrics=metrics,
    )

    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    standing_store = InMemoryStandingStore()
    note_store = InMemoryNoteStore()
    reminders = FakeReminderStore()
    adapter = FakeAdapter()
    home_adapter = _HomeAdapter()
    clock = _Clock(T_QUESTION)

    # THE TAG PATH'S MODEL — the one that dies. `openai.APIError` is the SDK's
    # own class (`openai` is a declared dependency), raised out of the runner
    # exactly where `LiveAgentRunner` would let it out, so the stage that
    # classifies it (`nora/engagement/stages/invoke.py`) sees the real shape
    # rather than a `RuntimeError` stand-in.
    tag_runner = FakeAgentRunner(
        [],
        raises=openai.APIError(
            "event loop cycle failed",
            httpx.Request("POST", "http://omniroute/v1/chat/completions"),
            body=None,
        ),
    )
    # THE PARTICIPATION TURN'S MODEL — the one that answers.
    participation_runner = FakeAgentRunner([
        ScriptedTurn(
            tool_calls=[("send_reply", {"text": ROUTE_ANSWER})],
            final_text=ROUTE_ANSWER,
        ),
    ])

    gate_client = _ScriptedGateClient()
    gate = RelevanceGate(
        client=gate_client, prompts=doctrine_store, records=audit, metrics=metrics,
        persons=persons, standing=standing_store,
        transcript_limit=cfg.relevance_gate.transcript_rows,
    )
    debrief_client = _ScriptedDebriefClient(
        standing_body=json.dumps(
            {"standing": INCIDENT_STANDING, "notes": [INCIDENT_NOTE]},
            ensure_ascii=False,
        ),
    )
    debrief_wiring = debrief.Wiring(
        store=standing_store,
        client=debrief_client, objective=shipped_objectives(), record_store=audit,
        max_chars=cfg.standing.max_chars, fold_batch=cfg.standing.fold_batch,
        timeout_seconds=cfg.standing.timeout_seconds,
        store_timeout_seconds=cfg.standing.store_timeout_seconds,
    )

    dispatcher, _engagement, background, trigger = build_offline_pipeline(
        cfg, adapter=adapter, audit=audit, memory=memory, persons=persons,
        doctrine_store=doctrine_store, runner=tag_runner, metrics=metrics,
        notes=note_store, standing=standing_store,
        debrief_wiring=debrief_wiring,
        extra_engage_policies=(RelevanceGatePolicy(policy),),
        participation_runner=participation_runner,
        participation_clock=clock.monotonic,
        participation_gate=gate,
        extra_adapters={home_adapter.platform: home_adapter},
    )
    journey = _Journey(
        metrics=metrics, adapter=adapter, home_adapter=home_adapter, audit=audit,
        standing=standing_store,
        notes=note_store, reminders=reminders,
        gate_client=gate_client, debrief_client=debrief_client,
        tag_runner=tag_runner, participation_runner=participation_runner,
    )

    async def drain() -> None:
        while background._tasks:
            await asyncio.gather(*list(background._tasks), return_exceptions=True)
            await asyncio.sleep(0)

    # --- 16:42:43 — Observe. The untagged question. ------------------------
    message = await adapter.parse(group_event(
        body=QUESTION, sender=MILES, conv=THREAD_ROOM, ts=T_QUESTION,
    ))
    assert message is not None
    await dispatcher.on_message(message)
    await drain()

    # --- 16:42:54 → 16:43:57 — Decide (gate) + Act (participation). --------
    # The window closes on the quiescence arm; the REAL gate is asked, admits,
    # and the REAL FirePath runs the one turn for the window.
    #
    # Patched for the same reason the tag dispatch below is, and for a row that
    # matters more: her OWN message's timestamp is stamped by
    # `nora/agent/guards.py`'s `send_reply` from `time.time()`, and that row is
    # the one the fold's transcript window renders as
    # `<m at="… 16:43" from="Nora" …>`. Left on the wall clock it
    # renders as whatever time the suite runs at, so the rendered input would
    # differ from the scripted one and any ordering check ("a `Nora:` row in
    # the window AFTER the tag") would pass or fail by hour of day.
    clock.advance_to(T_ANSWER)
    assert trigger is not None
    with mock.patch("time.time", return_value=T_ANSWER / 1000):
        journey.windows_closed = await trigger.close_due_windows()
        await drain()
    journey.sent_after_participation = _sends(adapter, home_adapter)

    # --- 16:43:57 — Observe. The tag, in the same second as the answer. ----
    # The SAME second, as `messages` recorded it live: he was typing while the
    # row landed and had not seen it. The rows stay distinguishable because
    # `_fold_rows` orders the fold's window by row id, not by ts.
    clock.advance_to(T_TAG)
    tagged = await adapter.parse(group_event(
        body=f"{cfg.engagement.trigger.text} 點去 Cyberport?",
        sender=MILES, conv=THREAD_ROOM, ts=T_TAG,
    ))
    assert tagged is not None
    # --- 16:45:13 — Act (tag): the model call raises. The debrief runs in
    # the turn's own `finally`, synchronously, before the slot is released.
    #
    # `EngagementPipeline._debrief` stamps `now_ms=int(time.time() * 1000)` at
    # the call site and takes no clock (`nora/engagement/pipeline.py`, whose
    # own docstring says the clock belongs to the caller) — so the position
    # and any note the fold writes would be stamped with the WALL clock, and
    # every downstream "has anything moved since" check would then depend on
    # what time the suite happened to run. Patched for exactly the one
    # dispatch, and only over `time.time`: asyncio measures its own waits on
    # the monotonic loop clock, so nothing here hangs on it.
    with mock.patch("time.time", return_value=T_DEBRIEF / 1000):
        await dispatcher.on_message(tagged)
        await drain()
    journey.sent_after_tag = _sends(adapter, home_adapter)
    journey.fold_input = (
        debrief_client.standing_calls[-1] if debrief_client.standing_calls else ""
    )

    journey.sent_all = _sends(adapter, home_adapter)
    assert trigger is not None
    journey.home_routes = (
        _engagement._adapter_for(home_adapter.platform),
        trigger._speak.__self__._adapter_for(home_adapter.platform),
    )
    await audit.aclose()
    return journey


@pytest.fixture
async def journey(tmp_path, monkeypatch) -> _Journey:
    import time

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    with monkeypatch.context() as zone:
        zone.setenv("TZ", "Asia/Hong_Kong")
        time.tzset()
        try:
            return await _drive(tmp_path)
        finally:
            zone.undo()
            time.tzset()


# ---------------------------------------------------------------------------
# stage 0 — the case ran the stages it claims to assert on
# ---------------------------------------------------------------------------


def test_0_every_stage_of_the_journey_actually_ran(journey: _Journey) -> None:
    """The positive control on the positive control.

    A journey case's characteristic failure is a stage that silently never
    happened: the gate never asked, the tag turn never reached the model, the
    fold never ran — and every downstream assertion then passes about nothing.
    Each of these is the smallest fact that could only be true if that stage
    executed.
    """
    assert journey.windows_closed == 1, "the quiescence window must have closed once"
    assert len(journey.gate_client.calls) == 1, (
        "the REAL RelevanceGate must have been asked exactly once — a trigger "
        "with gate=None would admit the window without a call and make the "
        "Decide stage vacuous"
    )
    assert journey.participation_runner.invocations == 1, (
        "the participation turn must have reached its model"
    )
    assert journey.tag_runner.invocations == 1, (
        "the tag turn must have reached its model and raised there — zero "
        "invocations means the tag never ran and assertion 2 proves nothing"
    )
    assert len(journey.debrief_client.standing_calls) == 1, (
        "the debrief fold's position call must have run"
    )
    assert journey.debrief_client.other_calls == [], (
        "the debrief fold made a call other than its one position call"
    )
    # THE CONTROL THE WHOLE OF STAGE 3 RESTS ON: the fold's own window carries
    # her sent row, at the recorded clock, ABOVE the tag. Without it every
    # stage-3 assertion is about a prompt the fold never saw.
    # Split PAST the opening tag, not just past the tag NAME: the opening tag
    # carries `untrusted` and a `note=` (the block is built through
    # `ContextBlock`), so splitting on `"<transcript "` alone would leave the
    # rest of that tag as a fourth "line" and this control would count it as a
    # message.
    window = journey.fold_input.split("<transcript ", 1)[1].split(">\n", 1)[1]
    window = window.split("</transcript>", 1)[0]
    lines = [line for line in window.splitlines() if line.strip()]
    assert len(lines) == 3, f"expected question, answer, tag; got {lines}"
    # The clock and the speaker, as ATTRIBUTES, deliberately NOT pinning what
    # else the opening tag carries. The row may also carry a delivery mark,
    # which is one of the shapes `3a2` accepts for "the fold is told this row
    # went out"; pinning the full tag would make this control refuse a fix
    # `3a2` is written to pass.
    assert 'at="2026-09-11 16:43"' in lines[1] and 'from="Nora"' in lines[1], (
        f"her sent row must render at 16:43 as the middle element; got {lines}"
    )
    assert ROUTE_ANSWER[:12] in lines[1], (
        f"her sent row must carry its own text; got {lines}"
    )
    assert "Nora" not in lines[2], (
        f"the tag must render AFTER her row, not before it; got {lines}"
    )


# ---------------------------------------------------------------------------
# 1 — after the participation turn
# ---------------------------------------------------------------------------


def test_1_the_participation_turn_sent_exactly_one_answer(journey: _Journey) -> None:
    """16:43:57 — one `is_nora` row in the Signal room, `delivery_status=sent`.

    Read off the RECORD, not off the adapter: the room-visible truth is the row
    the `Deliverer` wrote after the platform accepted it, and a send that was
    attempted and refused leaves a row in a different state.
    """
    nora_rows = [
        row for row in journey.audit.rows
        if row["is_nora"] and row["conversation_id"] == THREAD_ROOM
    ]
    assert len(nora_rows) == 1, (
        f"expected exactly one is_nora row in {THREAD_ROOM}, got {nora_rows}"
    )
    assert nora_rows[0]["delivery_status"] is DeliveryStatus.SENT
    assert nora_rows[0]["body"] == ROUTE_ANSWER
    assert journey.sent_after_participation == ((THREAD_ROOM, ROUTE_ANSWER),)


# ---------------------------------------------------------------------------
# 2 — after the tag turn
# ---------------------------------------------------------------------------


def test_2_the_tag_turn_died_with_llm_error_and_sent_nothing(
    journey: _Journey,
) -> None:
    """16:45:13 — `llm_error`, and the room heard nothing new.

    `nora_silent_failures_total{reason="llm_error"}` is where
    `nora/engagement/stages/invoke.py` records an invocation that never
    produced a turn; the send list being unchanged from the participation
    snapshot is the "no second send" half.
    """
    assert journey.metrics.get(
        "nora_silent_failures_total", reason="llm_error", audience="tag",
    ) == 1, (
        "the tag turn must have been classified llm_error — "
        f"{journey.metrics.snapshot()}"
    )
    assert journey.sent_after_tag == journey.sent_after_participation, (
        "the dead tag turn must not have sent anything: "
        f"{journey.sent_after_tag}"
    )
    assert '<turn outcome="llm_error"' in journey.fold_input, (
        "the fold must have been handed the llm_error turn itself"
    )


# ---------------------------------------------------------------------------
# 3 — after the debrief
# ---------------------------------------------------------------------------


#: Wordings that bind a claim to the turn that produced it. Any of them
#: satisfies the scope half of `test_3a_*`.
_TURN_SCOPE_TOKENS = ("this turn", "the turn", "this attempt")
#: Wordings that widen a claim past the turn, which is the defect itself. None
#: of them may appear in an `llm_error` header.
_ROOM_SCOPE_TOKENS = ("today", "anywhere", "at all", "any reply", "nothing all")


def test_3a_the_fold_is_told_the_header_is_about_this_turn(
    journey: _Journey,
) -> None:
    """Shape 1, first half — the INPUT, where the defect lives.

    The header is RENDERED, not asked for: `_OUTCOME_GLOSS` is a constant, so
    this is decidable with no model in it at all. The property is that an
    `llm_error` header states what is true of the TURN and never widens past
    it — because the fold's own transcript window, two lines below, holds a
    `Nora:` row that WAS sent.

    **Asserted as a pair, and the pair is the point**: a scope token must be
    present, and no widening token may be. Scored against candidate wordings:

    | candidate | verdict |
    |---|---|
    | `the model call failed; nothing was sent` | red — no scope |
    | `the model call failed; nothing was sent in this turn` | green |
    | `this turn: the model call failed; nothing was sent` | green |
    | `this turn produced no reply` | green |
    | `the model call failed; this attempt produced no reply` | green |
    | `the turn ran; nothing was delivered anywhere today` | red — widened |
    | `the model call failed` (clause deleted) | red — no scope |

    Scored by running the predicate, not by reading it. A flat ban on the
    phrase «nothing was sent» would fail the scoped wordings, which is a test
    pinning one sentence rather than the property.

    The last row is deliberate: deleting the clause rather than scoping it
    leaves a fold that cannot tell a dead turn from a silent one, which is what
    `_OUTCOME_GLOSS`'s own docstring says the gloss exists to prevent.
    """
    header = journey.fold_input.split("<what_happened ")[1].split(
        "</what_happened>",
    )[0].lower()
    window = journey.fold_input.split("<transcript ")[1].split("</transcript>")[0]
    assert any(token in header for token in _TURN_SCOPE_TOKENS), (
        "an llm_error <what_happened> must scope its claim to the turn; got "
        f"{header!r} — rendered beside a window that reads:\n{window}"
    )
    widened = [token for token in _ROOM_SCOPE_TOKENS if token in header]
    assert not widened, (
        f"an llm_error header may not widen past the turn; {widened} in "
        f"{header!r} — rendered beside a window that reads:\n{window}"
    )


def test_3a2_the_fold_is_told_her_row_in_the_window_was_delivered(
    journey: _Journey,
) -> None:
    """Shape 1, second half — «the fold is told that a `Nora:` row in the
    window after the tag is a reply that was sent, whatever the turn's
    outcome».

    Both halves of shape 1 are render, not instruction, and this is the half a
    scoped header alone does not give you: the fold can read a correctly
    scoped «this turn produced no reply» and STILL not know that the `Nora:`
    line above the tag went out rather than being drafted, vetoed or held.

    Decidable on the render, and deliberately shape-agnostic about WHERE: a
    marker on the row itself, a `note=` on the `<transcript>` block, or a line
    of its own all satisfy it. What it refuses is a render that shows the row
    and says nothing about whether it went out.

    The header is cut out of the haystack first, on purpose: an `llm_error`
    header may contain the word «sent» («this turn's model call failed, so THIS
    TURN sent nothing»), and a search that counted it would read the defect as
    the fix.
    """
    header_block = journey.fold_input.split("<what_happened ")[1].split(
        "</what_happened>",
    )[0]
    rendered = journey.fold_input.replace(header_block, "").lower()
    assert any(
        token in rendered for token in ("was sent", "delivered", "went out")
    ), (
        "the fold must be told a Nora: row in its window was DELIVERED, not "
        "just that it exists — it reads:\n"
        + journey.fold_input.split("<transcript ")[1].split("</transcript>")[0]
    )
    # AND THE ROW ITSELF CARRIES IT. With the per-row marker stripped and only
    # the legend left standing, every assertion above would still pass: the
    # legend contains the words "delivered" and "was sent", so the haystack
    # is satisfied by an explanation of a mark that is not there. A legend
    # explaining a marker nobody rendered is the block-that-does-not-say-what-
    # it-is defect this case exists to catch, one level up.
    window = journey.fold_input.split("<transcript ")[1].split("</transcript>")[0]
    her_row = next(
        line for line in window.splitlines()
        if 'at="2026-09-11 16:43"' in line and 'from="Nora"' in line
    )
    assert any(
        token in her_row.lower() for token in ("delivered", "was sent", "went out")
    ), (
        "her row must say on the row that it went out; the legend alone is an "
        f"explanation of a mark that is not there. The row reads: {her_row!r}"
    )


# ---------------------------------------------------------------------------
# 5 — across the whole run
# ---------------------------------------------------------------------------


def test_5_nothing_was_ever_sent_into_the_home_room(journey: _Journey) -> None:
    """The only thing the group ever heard is the one answer, in the room the
    thread lives in.

    Non-vacuous because the home room's platform is routed to its own
    recording adapter on both send paths (asserted first): a send there would be
    recorded, not raised. The exact-equality check on `sent_all` is the broad
    guard. The `HOME_ROOM` filters are narrower: the pipeline runs on
    `permissive_policy()`, so nothing here knows `HOME_ROOM` is the unit's home
    room, and those filters catch only a send or row addressed to that id.

    SCOPE. This covers the paths this journey drives: ingest, the
    participation turn, the tag turn and the fold. The proactive ACT turn is
    never reached here, because the decide pass never renders; read this as
    "the tag and participation paths did not misroute", not as a claim about
    the proactive path.
    """
    assert all(route is journey.home_adapter for route in journey.home_routes), (
        "the home room's platform must route to its own recording adapter, or "
        "'nothing was sent there' cannot fail"
    )
    assert [cid for cid, _ in journey.sent_all if cid == HOME_ROOM] == [], (
        f"nothing may be sent into the home room: {journey.sent_all}"
    )
    assert journey.sent_all == ((THREAD_ROOM, ROUTE_ANSWER),), (
        f"the run's only send is her one answer; got {journey.sent_all}"
    )
    assert [
        row for row in journey.audit.rows if row["conversation_id"] == HOME_ROOM
    ] == [], "no row of any kind may be written into the home room"
