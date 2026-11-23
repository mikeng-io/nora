"""Run the ambient loop with scripted models, recording adapters, and graph storage.

An untagged question is admitted and answered. A tag posted in the same
second reaches the model and fails. The graph debrief sees the failed pass's
silence and the earlier delivered reply in its own room window.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
import openai
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.agent.delivery_acts import DeliveryActs
from nora.app.assembler import lineage_policy
from nora.app.composition_pipelines import build_graph_debrief
from nora.app.dispatch import Dispatcher
from nora.app.participation import ParticipationTrigger
from nora.app.runtime import BackgroundTasks
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import DeliveryStatus
from nora.engagement.stages import debrief
from nora.infra.sql import SqlRecordStore, create_sql_engine
from nora.infra.sql.cognitive_graph_schema import DEBRIEF_OBLIGATION, NORA_PASS_OUTPUT
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.infra.sql.schema import MESSAGES
from nora.ingest.policy import RelevanceGatePolicy
from nora.ingest.relevance_gate import Completion as GateCompletion
from nora.ingest.relevance_gate import RelevanceGate
from nora.memory import wire as memory_wire
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    InMemoryNoteStore,
    InMemoryStandingStore,
    ScriptedTurn,
    group_event,
)
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
#: The tag arrives in the same second as the participation reply.
T_TAG = _ms(16, 43, 57)
T_DEBRIEF = _ms(16, 45, 14)

QUESTION = "聽朝10點要由屋企到Cyberport ABC Service Centre，點去？"
ROUTE_ANSWER = "聽朝 10 點到數碼港：喺天華邨搭 969P，8:25 左右出門，轉 970 入數碼港。"


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
    def __init__(self) -> None:
        self._objective = shipped_objectives().objective_body("debrief")
        self.calls: list[str] = []
        self.other_calls: list[str] = []

    @property
    def model(self) -> str:
        return "scripted-debrief"

    async def complete(
        self, *, system: str, prompt: str, trace_attributes=None,
    ) -> debrief.Completion:
        if system == self._objective:
            self.calls.append(prompt)
            return debrief.Completion(content='{"operations":[]}', finish_reason="stop")
        self.other_calls.append(prompt)
        return debrief.Completion(content="{}", finish_reason="stop")


class _Clock:
    def __init__(self, at_ms: int) -> None:
        self.at_ms = at_ms

    def advance_to(self, at_ms: int) -> None:
        self.at_ms = at_ms

    def monotonic(self) -> float:
        return self.at_ms / 1000.0


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
    home_adapter: FakeAdapter
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
    records: tuple[Any, ...] = ()
    obligations: tuple[Any, ...] = ()
    pass_silences: tuple[Any, ...] = ()

    def block(self, name: str) -> ElementTree.Element:
        (prompt,) = self.debrief_client.calls
        context = ElementTree.fromstring(f"<context>{prompt}</context>")  # noqa: S314
        block = context.find(name)
        assert block is not None, prompt
        return block


class _HomeAdapter(FakeAdapter):
    """The home room's platform: a `FakeAdapter` that records sends on Telegram."""

    platform = "telegram"


def _sends(*adapters: FakeAdapter) -> tuple[tuple[str, str], ...]:
    return tuple(
        (target.conversation_id, text)
        for adapter in adapters for target, text in adapter.sent
    )


def _config(tmp_path: Path) -> Any:
    """Enable the tiers crossed by the ambient journey."""
    base = load_example_config(tmp_path)
    return override_config(base, {
        "store.data_dir": tmp_path,
        "doctrine.dir": DOCTRINE_DIR,
        "dreamer.dir": REPO_ROOT / "nora" / "dreamer",
        "identity_file": tmp_path / "identity.toml",
        "policy_file": tmp_path / "policy.toml",
        "relevance_gate.enabled": True,
        "participation.enabled": True,
        "standing.enabled": True,
        "notes.enabled": True,
    })


async def _drive(tmp_path: Path, engine: AsyncEngine) -> _Journey:
    """Run the whole journey once, recording each stage as it passes."""
    (tmp_path / "identity.toml").write_text(_IDENTITY_TOML)
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n'
        f'[[conversations]]\nplatform = "signal"\nid = "{THREAD_ROOM}"\n'
        f'label = "{THREAD_ROOM}"\npersons = ["miles"]\nrelevance_gate = true\n'
        f'unit = "{UNIT}"\n'
        # Thread and cross-platform home room belong to the same unit.
        f'[[conversations]]\nplatform = "telegram"\nid = "{HOME_ROOM}"\n'
        f'label = "{HOME_ROOM}"\npersons = ["miles"]\n'
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

    access = IdentityAccess.for_files(persons, policy)
    registration = SqlItemRegistration(
        RegistrationScope(policy, persons), RuntimeRevisions({}, policy, persons),
    )
    engine.update_execution_options(item_registration=registration)
    audit = SqlRecordStore(
        engine, registration=registration,
        delivery_acts=DeliveryActs(
            memory_wire.act_envelope(engine), registration.scope, registration.revisions,
        ),
    )
    standing_store = InMemoryStandingStore()
    note_store = InMemoryNoteStore()
    adapter = FakeAdapter()
    home_adapter = _HomeAdapter()
    clock = _Clock(T_QUESTION)

    # Use the SDK error class the tag stage classifies as llm_error.
    tag_runner = FakeAgentRunner(
        [],
        raises=openai.APIError(
            "event loop cycle failed",
            httpx.Request("POST", "http://omniroute/v1/chat/completions"),
            body=None,
        ),
    )
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
    debrief_client = _ScriptedDebriefClient()
    debrief_wiring = build_graph_debrief(
        memory_wire, shipped_objectives(), cfg, engine, access,
        lineage_policy(cfg, policy=policy, persons=persons, access=access),
        persons, policy, doctrine_store, cfg.content_checks.enabled,
        # Graph bounds compare against Postgres assertion time.
        lambda: datetime.now(UTC), metrics,
    )

    dispatcher, _engagement, background, trigger = build_offline_pipeline(
        cfg, adapter=adapter, audit=audit,
        access=access, room_policy=policy,
        doctrine_store=doctrine_store, runner=tag_runner, metrics=metrics,
        notes=note_store, standing=standing_store,
        debrief_wiring=replace(debrief_wiring, client=debrief_client),
        extra_engage_policies=(RelevanceGatePolicy(policy),),
        participation_runner=participation_runner,
        participation_clock=clock.monotonic,
        participation_gate=gate,
        extra_adapters={home_adapter.platform: home_adapter},
    )
    journey = _Journey(
        metrics=metrics, home_adapter=home_adapter,
        gate_client=gate_client, debrief_client=debrief_client,
        tag_runner=tag_runner, participation_runner=participation_runner,
    )

    assert trigger is not None
    await _run_stages(
        journey, dispatcher, background, trigger, adapter, clock, cfg.engagement.trigger.text,
    )
    journey.home_routes = (
        _engagement._adapter_for(home_adapter.platform),
        trigger._speak.__self__._adapter_for(home_adapter.platform),
    )
    async with engine.connect() as conn:
        fields = MESSAGES.c
        journey.records = tuple((await conn.execute(sa.select(
            fields.conversation_id, fields.is_nora, fields.body, fields.delivery_status,
        ))).mappings())
        obligations = DEBRIEF_OBLIGATION.c
        journey.obligations = tuple((await conn.execute(sa.select(
            obligations.pass_id, obligations.state, obligations.created,
        ))).mappings())
        output = NORA_PASS_OUTPUT.c
        journey.pass_silences = tuple((await conn.execute(sa.select(
            output["pass"], output.graph_id,
        ).where(output.output_kind == "silence"))).mappings())
    return journey


async def _run_stages(
    journey: _Journey, dispatcher: Dispatcher, background: BackgroundTasks,
    trigger: ParticipationTrigger, adapter: FakeAdapter, clock: _Clock, tag_text: str,
) -> None:
    home_adapter = journey.home_adapter

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

    # Timestamp the actual reply at the same second as the upcoming tag.
    clock.advance_to(T_ANSWER)
    with mock.patch("time.time", return_value=T_ANSWER / 1000):
        journey.windows_closed = await trigger.close_due_windows()
        await drain()
    journey.sent_after_participation = _sends(adapter, home_adapter)

    clock.advance_to(T_TAG)
    tagged = await adapter.parse(group_event(
        body=f"{tag_text} 點去 Cyberport?",
        sender=MILES, conv=THREAD_ROOM, ts=T_TAG,
    ))
    assert tagged is not None
    with mock.patch("time.time", return_value=T_DEBRIEF / 1000):
        await dispatcher.on_message(tagged)
        await drain()
    journey.sent_after_tag = _sends(adapter, home_adapter)

    journey.sent_all = _sends(adapter, home_adapter)


@pytest.fixture
async def journey(tmp_path, monkeypatch, clean_pg_url) -> _Journey:
    import time

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    engine = create_sql_engine(clean_pg_url)
    with monkeypatch.context() as zone:
        zone.setenv("TZ", "Asia/Hong_Kong")
        time.tzset()
        try:
            return await _drive(tmp_path, engine)
        finally:
            await engine.dispose()
            zone.undo()
            time.tzset()


# ---------------------------------------------------------------------------
# stage 0 — the case ran the stages it claims to assert on
# ---------------------------------------------------------------------------


def _block_rows(block: ElementTree.Element) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (block.text or "").splitlines() if line.strip()]


def test_0_every_stage_of_the_journey_actually_ran(journey: _Journey) -> None:
    assert journey.windows_closed == 1
    assert len(journey.gate_client.calls) == 1
    assert journey.participation_runner.invocations == 1
    assert journey.tag_runner.invocations == 1
    assert journey.debrief_client.other_calls == []
    assert len(journey.debrief_client.calls) == 1, journey.metrics.snapshot()
    assert journey.metrics.get("nora_debrief_total", outcome="written") == 1
    (obligation,) = journey.obligations
    assert obligation["state"] == "settled"

    question, answer, tag = _block_rows(journey.block("debrief_messages"))
    assert question["content"] == QUESTION
    assert answer["content"] == ROUTE_ANSWER and answer["who"] == "nora"
    assert answer["at"] == tag["at"] == datetime.fromtimestamp(T_ANSWER / 1000, UTC).isoformat()
    assert tag["who"] == question["who"] and tag["who"] != answer["who"]


def test_1_the_participation_turn_sent_exactly_one_answer(journey: _Journey) -> None:
    """16:43:57 — one `is_nora` row in the Signal room, `delivery_status=sent`.

    Read off the RECORD, not off the adapter: the room-visible truth is the row
    the `Deliverer` wrote after the platform accepted it, and a send that was
    attempted and refused leaves a row in a different state.
    """
    nora_rows = [
        row for row in journey.records
        if row["is_nora"] and row["conversation_id"] == THREAD_ROOM
    ]
    assert len(nora_rows) == 1, (
        f"expected exactly one is_nora row in {THREAD_ROOM}, got {nora_rows}"
    )
    assert nora_rows[0]["delivery_status"] == DeliveryStatus.SENT.value
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


def test_3a_the_fold_is_told_the_header_is_about_this_turn(journey: _Journey) -> None:
    items = journey.block("debrief_items")
    (obligation,) = journey.obligations
    assert items.attrib["began"] == obligation["created"].isoformat()
    assert items.attrib["occasion"] == "called"
    (silence,) = _block_rows(items)
    assert silence["kind"] == "silence"
    (recorded,) = [
        output for output in journey.pass_silences if output["pass"] == obligation["pass_id"]
    ]
    assert silence["id"] == recorded["graph_id"]


def test_3a2_the_fold_is_told_her_row_in_the_window_was_delivered(journey: _Journey) -> None:
    (reply,) = [
        row for row in _block_rows(journey.block("debrief_messages"))
        if row["who"] == "nora" and row["content"] == ROUTE_ANSWER
    ]
    assert reply["delivery"] == DeliveryStatus.SENT.value


def test_5_nothing_was_ever_sent_into_the_home_room(journey: _Journey) -> None:
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
        row for row in journey.records if row["conversation_id"] == HOME_ROOM
    ] == [], "no row of any kind may be written into the home room"
