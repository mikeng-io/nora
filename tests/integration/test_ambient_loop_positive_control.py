"""The ambient loop's POSITIVE CONTROL, as one executable journey (issue #199).

WHAT THIS FILE IS
-----------------
The incident of 2026-09-11 read end to end, driven through the REAL stages with
a fake clock and a scripted model, asserting at every stage boundary. In one
line: an untagged question is admitted by the gate and answered by a
participation turn; the user tags her one second later, having not yet seen the
answer; the tag turn dies with `llm_error`; and **nothing more may land in any
room**.

The incident, with the timestamps this case replays (all HKT, 2026-09-11):

| when | stage | what happened |
|---|---|---|
| 16:42:43 | Observe | Miles, untagged, in the Signal room: how to get to Cyberport at 10 tomorrow? |
| 16:42:54 | Decide · gate | the Relevance Gate ADMITS the closed window |
| 16:43:00 | Act · participation | the participation turn runs |
| 16:43:57 | Act | the full route is SENT — one `is_nora` row, `delivery_status=sent` |
| 16:43:57 | Observe | Miles tags her — the same second; he has not seen the answer |
| 16:45:13 | Act · tag | the tag turn dies — `APIError`, `llm_error`, staying silent |
| 16:45:14 | Orient · debrief | the fold runs over that dead turn |
| 18:03:33 | Decide · proactive | the hourly proactive pass, +80 minutes |

What actually shipped: the debrief was handed
`<what_happened>the model call failed; nothing was sent</what_happened>` — true
of that TURN, false of the ROOM — generalised it into a Standing position
saying she had sent nothing all day, proposed a note on the same false premise,
had that blessed by a `grounded: true` verdict, and 80 minutes later the
proactive decider told him in a different room that she had never answered a
question she had answered.

THE ASSERTIONS, EACH AT THE STAGE WHERE IT BECOMES DECIDABLE
------------------------------------------------------------
1. after the participation turn — exactly one `is_nora` row in the Signal room,
   `delivery_status=sent`  (`test_1_*`)
2. after the tag turn — `llm_error`, and no second send  (`test_2_*`)
3. after the debrief — the header is turn-scoped (`3a`), the fold is told her
   row in the window was delivered (`3a2`), the verdict refuses a position
   claiming no reply was sent (`3b`), no note and no create-candidate come out
   of a dead turn (`3c`), and neither background pass is shown the false
   premise (`3d`)
4. after the proactive decide tick at +80min — the pass declines to render
   (`unchanged`) and no `kind="proactive"` reminder row exists  (`test_4_*`;
   read its docstring for what a green there is and is not worth)
5. across the whole run — zero sends into the home room  (`test_5_*`)

**3 WAS RED ON MASTER AND THAT WAS THE POINT.** This case was landed as the
instrument for the debrief fix, not as the fix: the five stage-3 assertions
carried `@pytest.mark.xfail(strict=True)` citing #199, so the gate stayed
green here while the case stayed honest, and `strict=True` meant the XPASS
would fail the suite the day the fix landed and force the markers out. That
happened on 2026-09-11 and all five markers are gone. They are now ordinary
assertions and a regression in any of the four fixes reddens this file — which
is the only reason the instrument was worth building before the fix.

Two things the XPASS caught that the fix's own unit tests did not, and both
are why a journey case earns its keep: the first draft of the scoped header
also carried a clause about replies in the transcript, which `3a` scored as
widening the claim past the turn (correctly — a header whose one job is not to
widen); and the transcript legend was rendered as a line INSIDE `<transcript>`,
where `test_0`'s own control counted it as a fourth message.

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
* the debrief fold actually ran — the scripted debrief client records both of
  its calls, and `test_0_*` refuses a run where it made none;
* the proactive pass actually ran — it counted itself on
  `nora_proactive_pass_total`.

WHAT IS FAKE, AND WHAT THAT COSTS
---------------------------------
Only the models and the platform. Every stage between them is production code:
the real `IngestPipeline` + `EngagementPipeline` + `Dispatcher` from
`nora.app.offline.build_offline_pipeline`, the real `RelevanceGatePolicy`, the
real `RelevanceGate`, the real `ParticipationTrigger`, the real `FirePath`, the
real guards and `Deliverer`, the real `debrief.run` stage over the real
`nora/dreamer/` objectives, and the real `ProactiveAdaptor`. The stores are
`nora/testing/`'s in-memory doubles, each of which is bound to its production
sibling by a suite under `tests/contract/`.

WHY `integration/` WITH NO POSTGRES
------------------------------------
`tests/README.md` puts a test here when it is true because *"the wired pipeline
behaves, through the real composition root"*, and names Postgres under **needs**
— what the tests there happen to require, not what makes them true. This one is
true for exactly that reason: it assembles the real pipelines through
`nora.app.offline.build_offline_pipeline`, the offline composition root, and
nothing it asserts would be more true against SQL — every store it touches is a
double the contract suites already bind to its production sibling. Driving it
on the real engine would buy one fact this case is not about (the SQL stores
agree with their doubles) for a Postgres container and 0.9s -> minutes. The
precedent for the shape is `tests/unit/test_offline_pipeline_assembler.py`,
which sits in `unit/` because it asserts about a call, not about a run; this
asserts about a run.

Two deliberate departures from the incident, stated rather than hidden:

* **Both rooms are on one platform.** The live home room is Discord and the
  thread was on Signal; `build_offline_pipeline` wires exactly one adapter
  (`adapters={adapter.platform: adapter}`). Putting the home room on a platform
  with no adapter would make assertion 5 pass vacuously — nothing could reach
  it even if the code tried. So the home room is a second conversation on the
  SAME adapter: a send into it is genuinely reachable, and "zero sends into the
  home room" is therefore a measurement rather than a tautology.
* **The two background passes are driven at `inputs`**, the real
  deterministic check that decides whether a model is paid for at all, not
  through `DreamerRuntime.run`. `inputs() == ""` IS the runtime's fire
  condition, and everything that could write a row — the tool build, the sample
  loop — sits strictly below it, so the reduction can only ever suppress, never
  manufacture, a raise. With a scripted model, "she chose not to remind" is not
  a measurement; it is my script. The pass check is.
  **The blind spot this leaves**, named rather than discovered later:
  `DreamerRuntime.run` declines a pass at `tool_subset_faults` BEFORE `inputs`
  is called, so a drift in the proactive grant or tool set would make
  production decline while this case still reports "declined for the right
  reason". `test_4_*` would not see it.

THE TWO SHORT VARIANTS, FOR LIFTING INTO A SUITE LATER
-------------------------------------------------------
Not executed here — written in the shape `evals/`'s YAML cases take, so they
can be moved into a suite file without re-deriving them. Both come from the
same incident, and both are about the two Decide paths not seeing each other.

```yaml
# S2 — participation already spoke; the proactive pass must HOLD.
- name: participation_spoke_then_proactive_holds
  expect: none                       # no reminder row, no raise
  workspace: ridgeline
  now: 2026-09-11T16:00:00+08:00
  rooms:
    - {platform: signal, conversation_id: signal-thread, label: thread, home: false}
    - {platform: signal, conversation_id: home-room,     label: home,   home: true}
  notes:
    - title: X is open
      description: the group has not settled X
      anchor_date: 20260912
  standing:
    - room: thread
      age_hours: 2
      body: >-
        at 14:00 I spoke about X in this room, unprompted, and said what I
        had; nobody has come back on it yet
  # THE POINT: X is open AND she already spoke to X two hours ago. A pass that
  # raises X again is raising something she has already said. PASS is zero rows.

# S3 — the tag one second after the participation send; the tag must not
# re-derive what was just sent.
- name: tag_one_second_after_participation_does_not_rederive
  expect: silent                     # graded on the tag turn's trajectory
  room: signal-thread
  transcript:
    - {at: "16:42:43", who: miles, body: "how do I get to Cyberport at 10 tomorrow?"}
    - {at: "16:43:57", who: nora, body: "<the route: 969P from 天華邨, ~8:25, 970 to 數碼港>"}
    - {at: "16:43:58", who: miles, body: "@Nora", tag: true}
  # THE POINT: the tag is a RACE, not a new question — he had not seen the row
  # above it when he typed. PASS is a turn that does not re-run `route`;
  # grade on the tool path (`route` absent), never on the reply text. The live
  # turn re-derived `saved_places`, `skills` and `route` twice and died doing it.
```
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

from nora.app.offline import build_offline_pipeline
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import DeliveryStatus
from nora.engagement.stages import debrief
from nora.ingest.policy import RelevanceGatePolicy
from nora.ingest.relevance_gate import Completion as GateCompletion
from nora.ingest.relevance_gate import RelevanceGate
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.proactive import PASS_CLOCK_NAME, ProactiveAdaptor, WorkspaceRoom
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeMemoryStore,
    FakeRecordStore,
    InMemoryDreamScheduleClock,
    InMemoryNoteStore,
    InMemoryStandingStore,
    InMemoryTurnVerdictStore,
    ScriptedTurn,
    group_event,
)
from nora.testing.reminders import FakeReminderStore
from tests.builders import load_example_config, override_config
from tests.objectives import shipped_objectives

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"
HKT = ZoneInfo("Asia/Hong_Kong")

WORKSPACE = "ridgeline"
#: The room the thread lives in — Signal in the incident, opted in to the gate.
THREAD_ROOM = "signal-thread"
#: The room a proactive row would fire into — Discord in the incident. Here it
#: is a second conversation on the SAME adapter, on purpose: see the module
#: docstring. Nothing in this journey may ever send here.
HOME_ROOM = "home-room"

MILES = "+852111"

_IDENTITY_TOML = (
    '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
)


def _ms(hh: int, mm: int, ss: int) -> int:
    """A wall-clock time on 2026-09-11 HKT, as epoch milliseconds."""
    return int(datetime(2026, 9, 11, hh, mm, ss, tzinfo=HKT).timestamp() * 1000)


#: The incident's clock, to the second.
T_QUESTION = _ms(16, 42, 43)
T_ANSWER = _ms(16, 43, 57)
#: The SAME second her answer landed — `messages` recorded both at 16:43:57.
#: That the tag is a RACE and not a reply to what she said is the whole reason
#: the fold had her row in its window and generalised past it anyway.
T_TAG = _ms(16, 43, 57)
T_DEBRIEF = _ms(16, 45, 14)
#: The hourly proactive tick between the debrief and the one this case asserts
#: on. The adaptor ticks hourly per workspace; 17:00 is the first tick that sees
#: the debrief's write, and 18:03 — the one that actually raised it live — is
#: the next. Stamping the pass clock here is what makes the +80-minute tick a
#: statement about "has anything moved SINCE", which is the check the adaptor
#: actually makes, rather than about a pass that never happened.
T_PASS_BEFORE = _ms(17, 0, 0)
T_PROACTIVE = _ms(18, 3, 33)

QUESTION = "聽朝10點要由屋企到Cyberport ABC Service Centre，點去？"
ROUTE_ANSWER = "聽朝 10 點到數碼港：喺天華邨搭 969P，8:25 左右出門，轉 970 入數碼港。"

#: The position the fold ACTUALLY wrote, from the `standing` row in #199.
#: Scripted verbatim: this case reproduces the incident's model output and
#: measures what the CODE does with it — it does not measure the model.
INCIDENT_STANDING = (
    "我今日冇出過任何回覆——model call 死咗，成個 turn 冇嘢送出。"
    "佢問緊聽朝點去 Cyberport，佢未收到任何路線，要補返。"
)
#: The note the same fold proposed, from `note` 589863b9 in #199.
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
    """The debrief's `ModelClient`, replaying the incident's two outputs.

    The stage makes TWO calls in one `asyncio.gather` — the position call and
    the verdict call — so this dispatches on the objective body in the system
    prompt rather than on arrival order, which `gather` does not fix.

    Both replies are the incident's own, verbatim: the false position and the
    `grounded: true` that blessed it. The case asserts on what the CODE does
    with them.
    """

    def __init__(self, *, standing_body: str, verdict_body: str) -> None:
        self._standing_objective = shipped_objectives().objective_body(
            debrief.OBJECTIVE,
        )
        self._standing_body = standing_body
        self._verdict_body = verdict_body
        self.standing_calls: list[str] = []
        self.verdict_calls: list[str] = []

    @property
    def model(self) -> str:
        return "scripted-debrief"

    async def complete(self, *, system: str, prompt: str) -> debrief.Completion:
        if system.startswith(self._standing_objective):
            self.standing_calls.append(prompt)
            return debrief.Completion(
                content=self._standing_body, finish_reason="stop",
            )
        self.verdict_calls.append(prompt)
        return debrief.Completion(content=self._verdict_body, finish_reason="stop")


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
    audit: FakeRecordStore
    standing: InMemoryStandingStore
    notes: InMemoryNoteStore
    verdicts: InMemoryTurnVerdictStore
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
    #: every send of the whole run, `(conversation_id, text)`
    sent_all: tuple[tuple[str, str], ...] = ()
    #: the fold's INPUT, as the debrief stage rendered it
    fold_input: str = ""
    #: the note-candidates tick's rendered batch
    note_candidates_envelope: str = ""
    #: the proactive pass's rendered envelope; `""` means it did not render
    proactive_envelope: str = ""
    proactive_pass_reasons: tuple[str, ...] = ()
    #: the envelope of the FIRST pass that could see the debrief's writes — the
    #: pass clock stamped before the question rather than after the fold. This
    #: one renders, so it is where what the decider is SHOWN becomes readable.
    first_seeing_envelope: str = ""
    note_titles_at_decide: tuple[str, ...] = ()


def _sends(adapter: FakeAdapter) -> tuple[tuple[str, str], ...]:
    return tuple((target.conversation_id, text) for target, text in adapter.sent)


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
        'persons = "*"\nrelevance_gate = true\n'
        f'workspace = "{WORKSPACE}"\n'
        f'[[conversations]]\nplatform = "signal"\nid = "{HOME_ROOM}"\n'
        'persons = "*"\n'
        f'workspace = "{WORKSPACE}"\nhome_room = true\n'
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
    verdict_store = InMemoryTurnVerdictStore()
    reminders = FakeReminderStore()
    adapter = FakeAdapter()
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
        verdict_body=json.dumps({"grounded": True, "unsupported": []}),
    )
    debrief_wiring = debrief.Wiring(
        store=standing_store, store_verdict=verdict_store, notes_sink=note_store,
        workspace_of=lambda _platform, _conversation_id: WORKSPACE,
        client=debrief_client, objective=shipped_objectives(), record_store=audit,
        max_chars=cfg.standing.max_chars, fold_batch=cfg.standing.fold_batch,
        timeout_seconds=cfg.standing.timeout_seconds,
        store_timeout_seconds=cfg.standing.store_timeout_seconds,
        rebuild_after_folds=cfg.standing.rebuild_after_folds,
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
    )
    journey = _Journey(
        metrics=metrics, adapter=adapter, audit=audit, standing=standing_store,
        notes=note_store, verdicts=verdict_store, reminders=reminders,
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
    # the one the fold's transcript window renders as `<m at="16:43" from="Nora" …>`. Left on
    # the wall clock it renders as whatever time the suite runs at — so the
    # instrument's rendered input would differ from the incident's at exactly
    # the place #199 says it matters, and any ordering check ("a `Nora:` row in
    # the window AFTER the tag") would pass or fail by hour of day.
    clock.advance_to(T_ANSWER)
    assert trigger is not None
    with mock.patch("time.time", return_value=T_ANSWER / 1000):
        journey.windows_closed = await trigger.close_due_windows()
        await drain()
    journey.sent_after_participation = _sends(adapter)

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
    journey.sent_after_tag = _sends(adapter)
    journey.fold_input = (
        debrief_client.standing_calls[-1] if debrief_client.standing_calls else ""
    )

    # --- the note-candidates tick over the same room ------------------------
    # The real `NoteCandidateAdaptor`, over the real batch the runtime would
    # hand it (`RecordStore.since`, human rows only). Driven at `inputs` only,
    # for the same reason the proactive pass below is: `inputs` is the
    # runtime's own fire condition (`nora/agent/dreamer.py`: a blank prompt
    # means no model and no tools), and everything past it is a model's
    # decision, which with a scripted model would be my decision wearing a
    # measurement's clothes.
    #
    # What it is here for: this is the SECOND tier shown what the fold wrote,
    # and `test_3d_*` reads the envelope back.
    batch = await audit.since(
        conversation_id=THREAD_ROOM, after_id=0, limit=cfg.standing.fold_batch,
    )
    candidates = NoteCandidateAdaptor(
        notes=note_store, candidates=note_store,
        workspace_for=lambda _scope: WORKSPACE, metrics=metrics,
        timezone=HKT,
        # #161 (PR #202) landed after this case was written: the adaptor now
        # requires the room's label and the operator's description, keyed the
        # same way `composition.py` keys them. The journey's two rooms carry
        # the labels its policy block declares; no description is set, which
        # is the deployment's state too.
        label_for={THREAD_ROOM: "thread", HOME_ROOM: "home"}.get,
        description_for={}.get,
    )
    journey.note_candidates_envelope = await candidates.inputs(
        key=THREAD_ROOM, batch=batch,
    )

    # --- 18:03:33 — Decide (proactive), +80 minutes. -----------------------
    # BEFORE both passes, and load-bearing: `clock.now` is read by reference
    # inside `_pass_reason`, whose daily-floor arm compares LOCAL DATES.
    clock.advance_to(T_PROACTIVE)
    rooms = (
        WorkspaceRoom(
            platform="signal", conversation_id=THREAD_ROOM, label="thread",
        ),
        WorkspaceRoom(
            platform="signal", conversation_id=HOME_ROOM, label="home", home=True,
        ),
    )

    async def _pass(*, last_pass_ms: int) -> tuple[str, tuple[str, ...]]:
        """One real proactive pass over THIS journey's own stores.

        Reads nothing but the adaptor's own inputs and writes nothing (the
        wake and the pass stamp are `commit`'s, which a pass that does not
        render never reaches), so the two passes below are independent.
        """
        pass_clock = InMemoryDreamScheduleClock()
        await pass_clock.record_fire(
            adaptor_name=PASS_CLOCK_NAME, key=WORKSPACE, at_ms=last_pass_ms,
        )
        pass_metrics = Metrics()
        adaptor = ProactiveAdaptor(
            notes=note_store, standing=standing_store, reminders=reminders,
            memory=memory, rooms_of=lambda ws: rooms if ws == WORKSPACE else (),
            ai_peer=cfg.memory.ai_peer, clock=clock.now, timezone=HKT,
            clock_store=pass_clock, records=audit, metrics=pass_metrics,
        )
        envelope = await adaptor.inputs(key=WORKSPACE, batch=())
        reasons = tuple(sorted({
            dict(labels).get("reason", "")
            for (name, labels), value in pass_metrics.snapshot().items()
            if name == "nora_proactive_pass_total" and value
        }))
        return envelope, reasons

    journey.note_titles_at_decide = tuple(
        note.title for note in await note_store.standing(workspace=WORKSPACE)
    )
    # The pass this case's assertion 4 is about: the last pass ran at 17:00,
    # after the fold, so the 18:03 tick is deciding whether anything has moved
    # SINCE — which is the check the adaptor actually makes.
    journey.proactive_envelope, journey.proactive_pass_reasons = await _pass(
        last_pass_ms=T_PASS_BEFORE,
    )
    # And the pass that FIRST sees the fold's writes — last pass at 16:42,
    # before the question. This one renders, which is what makes the decide
    # turn's INPUT readable at all.
    journey.first_seeing_envelope, _ = await _pass(last_pass_ms=T_QUESTION)

    journey.sent_all = _sends(adapter)
    # AFTER both passes, and load-bearing: `_quiet` fails closed on a read
    # error, so a store closed first would turn both passes into `held_quiet`
    # — a reason `test_4_*` rejects, but for the wrong cause.
    await audit.aclose()
    return journey


@pytest.fixture
async def journey(tmp_path, monkeypatch) -> _Journey:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    return await _drive(tmp_path)


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
    assert len(journey.debrief_client.verdict_calls) == 1, (
        "the debrief fold's verdict call must have run"
    )
    assert journey.note_candidates_envelope, (
        "the note-candidates tick must have rendered the room's batch — an "
        "empty batch means the tick never saw the exchange"
    )
    # THE CONTROL THE WHOLE OF STAGE 3 RESTS ON, asserted here rather than
    # inside an xfail (where it would never execute): the fold's own window
    # carries her sent row, at the clock the incident recorded, ABOVE the tag.
    # Without it every stage-3 assertion is about a prompt the fold never saw.
    # PAST the opening tag, not just past the tag NAME: since #292 routed this
    # block through `ContextBlock` the opening tag carries `untrusted` and a
    # `note=`, so splitting on `"<transcript "` alone left the rest of that tag
    # as a fourth "line" and this control counted it as a message.
    window = journey.fold_input.split("<transcript ", 1)[1].split(">\n", 1)[1]
    window = window.split("</transcript>", 1)[0]
    lines = [line for line in window.splitlines() if line.strip()]
    assert len(lines) == 3, f"expected question, answer, tag; got {lines}"
    # The clock and the speaker, as ATTRIBUTES — and deliberately NOT pinning
    # what else the opening tag carries. The row may also carry a delivery
    # mark, which is one of the shapes `3a2` accepts for "the fold is told this
    # row went out"; pinning the full tag would make this control refuse a fix
    # `3a2` was written to pass, exactly as pinning the trailing colon would
    # have under the flat grammar.
    assert 'at="16:43"' in lines[1] and 'from="Nora"' in lines[1], (
        f"her sent row must render at 16:43 as the middle element; got {lines}"
    )
    assert ROUTE_ANSWER[:12] in lines[1], (
        f"her sent row must carry its own text; got {lines}"
    )
    assert "Nora" not in lines[2], (
        f"the tag must render AFTER her row, not before it; got {lines}"
    )
    assert journey.proactive_pass_reasons, (
        "the proactive pass must have counted itself — no reason label means "
        "no pass ran and assertion 4 proves nothing"
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
# 3 — after the debrief.  RED ON MASTER UNTIL 2026-09-11 (#199); the five
# `xfail(strict=True)` markers came off when the fix landed, exactly as they
# were built to.
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
    """#199 shape 1, first half — the INPUT, where the defect actually lives.

    The header is RENDERED, not asked for: `_OUTCOME_GLOSS` is a constant, so
    this is decidable with no model in it at all. The property is that an
    `llm_error` header states what is true of the TURN and never widens past
    it — because the fold's own transcript window, two lines below, holds a
    `Nora:` row that WAS sent.

    **Asserted as a pair, and the pair is the point**: a scope token must be
    present, and no widening token may be. Scored against the wordings a fix
    for #199 could plausibly choose:

    | candidate | verdict |
    |---|---|
    | `the model call failed; nothing was sent` (master) | red — no scope |
    | `the model call failed; nothing was sent in this turn` | green |
    | `this turn: the model call failed; nothing was sent` | green |
    | `this turn produced no reply` (#199's own wording) | green |
    | `the model call failed; this attempt produced no reply` | green |
    | `the turn ran; nothing was delivered anywhere today` | red — widened |
    | `the model call failed` (clause deleted) | red — no scope |

    Scored by running the predicate, not by reading it. So the two obvious
    minimal fixes pass, #199's own wording passes, and a "fix" that keeps a
    room-wide claim while mentioning the turn does not. A flat ban on the
    phrase «nothing was sent» would have failed the first two, which is a test
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
    """#199 shape 1, second half — «and the fold is told that a `Nora:` row in
    the window after the tag is a reply that was sent, whatever the turn's
    outcome».

    Both halves of shape 1 are render, not instruction, and this is the half a
    scoped header alone does not give you: the fold can read a correctly
    scoped «this turn produced no reply» and STILL not know that the `Nora:`
    line above the tag went out rather than being drafted, vetoed or held.

    Decidable on the render, and deliberately shape-agnostic about WHERE: a
    marker on the row itself, a `note=` on the `<transcript>` block, or a line
    of its own all satisfy it. What it refuses is a render that shows the row
    and says nothing about whether it went out.

    The header is cut out of the haystack first, on purpose: master's header
    contains the word «sent» («nothing was sent»), and a search that counted it
    would read the defect as the fix.
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
    # AND THE ROW ITSELF CARRIES IT. Measured 2026-09-11: with the per-row
    # marker stripped and only the legend left standing, every assertion in
    # this file passed — the legend contains the words "delivered" and "was
    # sent", so the haystack above is satisfied by an explanation of a mark
    # that is not there. A legend explaining a marker nobody rendered is
    # precisely the block-that-does-not-say-what-it-is defect this case
    # exists to catch, one level up.
    window = journey.fold_input.split("<transcript ")[1].split("</transcript>")[0]
    her_row = next(
        line for line in window.splitlines()
        if 'at="16:43"' in line and 'from="Nora"' in line
    )
    assert any(
        token in her_row.lower() for token in ("delivered", "was sent", "went out")
    ), (
        "her row must say on the row that it went out; the legend alone is an "
        f"explanation of a mark that is not there. The row reads: {her_row!r}"
    )


async def test_3b_the_verdict_refuses_a_position_that_claims_no_reply(
    journey: _Journey,
) -> None:
    """#199 shape 2 — the verdict must be able to SEE it.

    The model was scripted to return the incident's own `grounded: true,
    unsupported: []`. The check this asks for is one the code can make from the
    transcript alone: a position asserting that no reply was sent, while the
    window holds a `Nora:` row after the tag, is `unsupported`.

    Decidable without reading the position's language: the stored verdict's own
    `grounded` flag is the output property, and `InMemoryTurnVerdictStore`
    refuses `grounded=True` with a non-empty `unsupported`, so the two cannot
    drift apart.

    **THIS DELIBERATELY PINS A CODE-SIDE CHECK, and the constraint is chosen,
    not accidental.** The verdict model here is hard-scripted to bless the
    position, so the only thing that can ever flip `grounded` is a
    deterministic check inside `debrief.py` overriding what the model said.
    #199 shape 2 asks for exactly that — «one check the verdict can make from
    the transcript alone» — and a prompt-side fix would leave this red forever
    with the strict XPASS never tripping. If the fix lands prompt-side instead,
    this assertion is the wrong instrument and must be rewritten, not deleted:
    `test_3a2_*` is the half that holds under either shape.
    """
    rows = list(journey.verdicts._rows.values())
    assert len(rows) == 1, f"expected one turn verdict, got {rows}"
    verdict = rows[0]
    assert verdict.grounded is False, (
        "a position claiming no reply was sent, written beside a transcript "
        "window that holds her sent row, must not be blessed as grounded; "
        f"got {verdict}"
    )
    assert verdict.unsupported, "the unsupported claim must be named"
    # The read production actually makes, so a fake-vs-SQL divergence on the
    # ungrounded index would show up here rather than at the next incident.
    assert await journey.verdicts.latest_ungrounded(
        conversation_id=THREAD_ROOM,
    ) is not None


async def test_3c_no_note_is_proposed_from_a_dead_turn(journey: _Journey) -> None:
    """#199 shape 3 — a note proposed from a dead turn is about the TURN.

    The fold was scripted to propose the incident's own note, whose premise
    («the model call failed, Nora sent no reply») was false at the moment of
    writing, and whose closing condition («Nora successfully sends a route»)
    had already been met, by her own row, 78 seconds earlier.

    Decidable on the notebook, not on the note's language: an `llm_error` fold
    must leave the workspace with no new note and no staged create-candidate.
    """
    assert await journey.notes.standing(workspace=WORKSPACE) == (), (
        "an llm_error fold must not create a note: "
        f"{[n.title for n in journey.notes.notes.values()]}"
    )
    assert journey.notes.create_candidates == {}, (
        "an llm_error fold must not even stage a create-candidate: "
        f"{journey.notes.create_candidates}"
    )


def test_3d_the_proactive_decider_is_never_shown_the_false_premise(
    journey: _Journey,
) -> None:
    """#199 shape 3, read one stage later — «and the loop raised it in the
    wrong room 90 minutes later».

    The decide turn reads `<open_notes>` and Standing and, by D9, no
    transcript: faced with a note saying «未覆» it had no input that could
    contradict it. So the measurement that matters at this stage is not what
    the model chose — with a scripted model that is my script — but what it was
    SHOWN.

    This is the pass whose clock sits BEFORE the question, i.e. the first one
    that can see the fold's writes at all; it renders, so its envelope is
    readable. Decidable on the structure, not on the note's language: the
    workspace must have no open note for the tick to carry.

    The note-candidates tick is asserted here too, and for the same reason at a
    different tier: it is the SECOND background pass shown the note index, and
    the false note reaches it the same way.

    KNOWN FALSE-NEGATIVE, low severity and loud: if the fix also stops the fold
    writing a POSITION on `llm_error`, this pass has no open notes and no
    standing lines, returns `""` via `nothing_shown`, and the first assert
    below fails on a correct fix. It fails with that message, not silently.
    """
    assert journey.first_seeing_envelope, (
        "the first pass after the fold must have rendered, or this assertion "
        "is about an envelope nobody was shown (if the fix also stopped the "
        "fold writing a position, this pass now renders nothing — rewrite, "
        "do not delete)"
    )
    # `<notes>` since #314, not `<open_notes>`: this pass renders the tag
    # path's block through the tag path's renderer, and the second name was an
    # accident of the two prompts being written months apart.
    open_notes = journey.first_seeing_envelope.split("<notes")[1].split(
        "</notes>",
    )[0]
    assert "(none)" in open_notes, (
        "a dead tag turn must leave the proactive decider nothing to raise; "
        f"it was shown {journey.note_titles_at_decide}"
    )
    assert not any(
        title in journey.note_candidates_envelope
        for title in journey.note_titles_at_decide
    ), (
        "the note-candidates tick must not be shown the false note either; "
        f"its rendered index carries {journey.note_titles_at_decide}"
    )


# ---------------------------------------------------------------------------
# 4 — the proactive decide tick, +80 minutes
# ---------------------------------------------------------------------------


async def test_4_the_proactive_tick_raises_nothing(journey: _Journey) -> None:
    """18:03:33 — the pass runs, declines to render, and writes no row.

    WHAT THIS DOES AND DOES NOT COVER, because the distinction is the whole
    value of the assertion. The `remind` rows are written inside the model
    loop, so with a scripted model "she chose not to remind" would be my
    script, not a measurement. What IS a measurement is the deterministic pass
    check that runs BEFORE any model is near (`_pass_reason`, issue #136): it
    reads this journey's own note store, standing store and record store and
    decides whether the tick renders at all. A pass that does not render cannot
    raise anything, whatever a model would have said.

    WHAT IT IS WORTH, said plainly so nobody reads more into a green than is
    there. The pass clock is stamped at 17:00 — after the fold — and nothing in
    this journey moves between 17:00 and 18:03, so `unchanged` is guaranteed by
    CONSTRUCTION: `_pass_reason` returns it at the `_changed_since` arm before
    `held_quiet` is ever evaluated. This assertion would have been green during
    the live incident too. **It has no discriminating power for #199** — it is
    a regression guard on the change-detector, and the instrument for the
    incident's own 90-minute raise is `test_3d_*`.

    `unchanged` is asserted exactly, not as a disjunction with `nothing_shown`:
    `nothing_shown` is unreachable here (the workspace has both a note and a
    position), and a disjunction that can only ever take one branch reads like
    two covered cases while covering one.
    """
    assert journey.proactive_pass_reasons == ("unchanged",), (
        "the +80-minute pass must have declined to render, and for the right "
        f"reason; got {journey.proactive_pass_reasons}. It was shown notes "
        f"{journey.note_titles_at_decide} and rendered "
        f"{journey.proactive_envelope[:400]!r}"
    )
    assert journey.proactive_envelope == "", (
        "a pass that rendered an envelope paid for a model call it did not "
        f"need: {journey.proactive_envelope[:400]!r}"
    )
    # THE LITERAL CLAIM — «nothing lands in any room after the first answer» —
    # and it cannot fail in this journey: no stage here reaches the `remind`
    # tool, which the runtime builds below the fire condition. Kept because it
    # is what the claim actually says and it costs nothing; NOT an instrument.
    # The day a sibling case drives a real `commit`, that is where it earns.
    proactive_rows = [
        row for row in await journey.reminders.all_pending(limit=1000)
        if row.kind == "proactive"
    ]
    assert proactive_rows == [], (
        f"nothing may be scheduled into any room after her answer: {proactive_rows}"
    )


# ---------------------------------------------------------------------------
# 5 — across the whole run
# ---------------------------------------------------------------------------


def test_5_nothing_was_ever_sent_into_the_home_room(journey: _Journey) -> None:
    """The only thing the group ever heard is the one answer, in the room the
    thread lives in.

    Non-vacuous because the home room is served by the SAME adapter as the
    thread (module docstring): a send there is reachable, and `test_1_*` proves
    that adapter records sends.

    SCOPE. This covers the paths this journey drives — ingest, the
    participation turn, the tag turn and the fold. The stage that actually
    misrouted live, the proactive ACT turn, is never reached here, because the
    decide pass never rendered; so read this as "the tag and participation
    paths did not misroute", not as the whole of the incident's final harm.
    """
    assert [cid for cid, _ in journey.sent_all if cid == HOME_ROOM] == [], (
        f"nothing may be sent into the home room: {journey.sent_all}"
    )
    assert journey.sent_all == ((THREAD_ROOM, ROUTE_ANSWER),), (
        f"the run's only send is her one answer; got {journey.sent_all}"
    )
    assert [
        row for row in journey.audit.rows if row["conversation_id"] == HOME_ROOM
    ] == [], "no row of any kind may be written into the home room"
