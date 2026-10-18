"""`PersonaVerdictAdaptor`: the judge a hypothesis's writer never is
(design/unified-memory/45 §3).

Through the real `SqlPersonaStore` and, for the code path, the real
`SqlRollupRead` over a seeded build, because what the judge can get wrong
without anything saying so is a verdict: a countable one the model graded, an
`expired` that was really an unread build (R-27), a window read past its end,
or a docket settled half-judged (DEC-280).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.culture import character_key
from nora.domain.persona import Account, AddOp, PersonaCitable, Said
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.schema import ROLLUP_BUILD, ROLLUP_TIE
from nora.language.character_session import AccountNotRecordableError
from nora.language.persona_verdict import (
    DocketAccount,
    PersonaVerdictAdaptor,
    PersonaVerdictEvidence,
)
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence

DAY = 86_400_000
T0 = 1_790_000_000_000
NOW = datetime.fromtimestamp((T0 + 40 * DAY) / 1000, tz=UTC)
UNIT = "friends"
KEY = character_key(UNIT, "ada")
KEYS = {KEY: DocketAccount(about="Ada", rooms=(("signal", "room-a"),))}
NAMES = {"ada": "Ada", "bo": "Bo"}
ACCOUNT = Account(key=KEY, entity_kind="human", unit=UNIT, subject="ada")
CITABLE = PersonaCitable(
    messages={n: Said(T0 + n * DAY, "ada", "room-a") for n in range(1, 5)},
    listed_rooms=frozenset({"room-a"}),
)


@pytest.fixture
async def env(pg_url: str) -> Any:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SimpleNamespace(
            engine=engine,
            store=SqlPersonaStore(engine, screen=lambda _t: None),
            rollup=SqlRollupRead(engine),
        )
    finally:
        await engine.dispose()


async def _hypothesis(env: Any, *, observable: str, window_end: int,
                      prediction: str = "picks short games on weekdays") -> str:
    """An induced trait and an abduced hypothesis on it, window closing at
    `window_end`; the hypothesis's line id."""
    premise = await env.store.persona_write(
        account=ACCOUNT, citable=CITABLE, now_ms=T0, written_by_session="t",
        apply_decay=False,
        ops=[AddOp(part="character", kind="trait", text="plays games most nights",
                   mode="induced", citations=("1", "2", "3"))])
    assert premise.status == "ok"
    added = await env.store.persona_write(
        account=ACCOUNT, citable=CITABLE, now_ms=T0 + 1, written_by_session="t",
        apply_decay=False,
        ops=[AddOp(part="hypothesis", kind="explanation", text="winding down",
                   mode="abduced", citations=("1",), draws_on=("P1",),
                   prediction=prediction, prediction_observable=observable,
                   prediction_window_end=window_end)])
    assert added.status == "ok", [r.reason for r in added.refusals]
    return added.written[0]


def _adaptor(env: Any, *, reads: Any = None, rollup: Any = None,
             metrics: Metrics | None = None,
             keys: Any = KEYS) -> PersonaVerdictAdaptor:
    return PersonaVerdictAdaptor(
        store=env.store, reads=reads or InMemoryCharacterEvidence(),
        rollup=rollup if rollup is not None else env.rollup,
        keys=keys, name_of=lambda pid: NAMES.get(pid or "", "someone"),
        clock_ms=lambda: T0 + 40 * DAY, metrics=metrics, clock=lambda: NOW)


async def test_the_docket_ignores_the_watermark_and_empties_as_verdicts_land(env):
    await _hypothesis(env, observable="a short game on a weekday evening",
                      window_end=T0 + 30 * DAY)
    adaptor = _adaptor(env)
    evidence: PersonaVerdictEvidence = adaptor.evidence
    assert await evidence.count(key=UNIT, after=0) == 1
    # The watermark does not narrow a live docket.
    assert await evidence.count(key=UNIT, after=9_999_999) == 1
    due, cursor = await evidence.newest(key=UNIT, after=0, limit=5)
    assert len(due) == 1 and cursor > T0
    assert await evidence.count(key="other-unit", after=0) == 0


async def test_a_countable_observable_is_judged_by_code_never_the_model(env):
    """`rollup:tie:...` exactly: code reads the current build over the window.
    A row in it confirms; none expires it; no current build is `unknown`
    (R-27), never `expired`. The prompt renders nothing, so no model runs."""
    await _hypothesis(env, observable="rollup:tie:friends|ada,bo",
                      window_end=T0 + 30 * DAY)
    adaptor = _adaptor(env)
    due, _ = await adaptor.evidence.newest(key=UNIT, after=0, limit=5)
    assert await adaptor.inputs(key=UNIT, batch=due) == ""
    metrics = Metrics()
    adaptor = _adaptor(env, metrics=metrics)
    # No current build yet: unknown, not expired.
    assert await adaptor.commit(key=UNIT, proposals=(), now_ms=T0 + 40 * DAY) == 1
    [head] = [h for h in await env.store.account_heads(key=KEY)
              if h.part == "hypothesis"]
    assert (head.verdict, head.verdict_by) == ("unknown", "code")
    assert metrics.get("nora_persona_verdict_total", verdict="unknown", judge="code") == 1

    async def _build(with_tie: bool) -> int:
        async with env.engine.begin() as conn:
            build = int((await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="v", params_sha256="p", status="current",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one())
            if with_tie:
                await conn.execute(sa.insert(ROLLUP_TIE).values(
                    build_id=build, unit=UNIT, lineage=[], valid_from_ms=T0,
                    valid_to_ms=T0 + 7 * DAY, as_of_ms=T0 + 7 * DAY,
                    person_a="ada", person_b="bo", iso_week="2026-W01",
                    replies_ab=1, replies_ba=0, mentions_ab=0, mentions_ba=0,
                    reactions_ab=0, reactions_ba=0, colocation_ms=0,
                ))
        return build
    # A fresh docket entry (the last was judged): same observable, tie absent.
    await _hypothesis(env, observable="rollup:tie:friends|ada,bo",
                      window_end=T0 + 30 * DAY)
    build = await _build(with_tie=False)
    assert await _adaptor(env).commit(key=UNIT, proposals=(), now_ms=T0 + 40 * DAY) == 1
    [head] = [h for h in await env.store.account_heads(key=KEY)
              if h.part == "hypothesis" and h.verdict != "unknown"]
    assert head.verdict == "expired"
    # And a build holding the tie confirms the next one.
    await _hypothesis(env, observable="rollup:tie:friends|ada,bo",
                      window_end=T0 + 30 * DAY)
    async with env.engine.begin() as conn:
        await conn.execute(sa.insert(ROLLUP_TIE).values(
            build_id=build, unit=UNIT, lineage=[], valid_from_ms=T0,
            valid_to_ms=T0 + 7 * DAY, as_of_ms=T0 + 7 * DAY,
            person_a="ada", person_b="bo", iso_week="2026-W02",
            replies_ab=2, replies_ba=1, mentions_ab=0, mentions_ba=0,
            reactions_ab=0, reactions_ba=0, colocation_ms=0,
        ))
    assert await _adaptor(env).commit(key=UNIT, proposals=(), now_ms=T0 + 40 * DAY) == 1
    heads = [h for h in await env.store.account_heads(key=KEY)
             if h.part == "hypothesis"]
    assert sorted(h.verdict for h in heads) == ["confirmed", "expired", "unknown"]


async def test_a_prose_observable_is_the_models_and_the_window_is_in_the_prompt(env):
    """The judge is handed the prediction, the observable and the window's
    rows — and nothing the writer wrote (no account lines)."""
    await _hypothesis(env, observable="a short game on a weekday evening",
                      window_end=T0 + 30 * DAY)
    reads = InMemoryCharacterEvidence()
    # Inside the window: evidence for it. Outside (too late): never shown.
    reads.add_message(id=11, platform="signal", room="room-a", person_id="ada",
                      ts=T0 + 10 * DAY, body="quick one tonight")
    reads.add_message(id=12, platform="signal", room="room-a", person_id="ada",
                      ts=T0 + 35 * DAY, body="after the window")
    reads.add_message(id=13, platform="signal", room="room-b", person_id="ada",
                      ts=T0 + 10 * DAY, body="a room that does not list them")
    adaptor = _adaptor(env, reads=reads, metrics=Metrics())
    due, _ = await adaptor.evidence.newest(key=UNIT, after=0, limit=5)
    out = await adaptor.inputs(key=UNIT, batch=due)
    assert '<hypothesis id="H1" about="Ada"' in out
    assert "picks short games on weekdays" in out
    assert "quick one tonight" in out
    assert "after the window" not in out and "a room that does not list them" not in out
    assert "plays games most nights" not in out  # nothing the adaptor wrote
    [verdict] = adaptor.verbs(key=UNIT)
    assert await verdict(verdicts=[{"line": "H1", "verdict": "confirmed"}]) == {"status": "ok"}
    staged = await adaptor.accept(key=UNIT, items=())
    written = await adaptor.commit(key=UNIT, now_ms=T0 + 40 * DAY, proposals=[
        Proposal(payload=p, instruction_sha256="a" * 64, model="flash",
                 established_by="dream:persona-verdict:friends:1") for p in staged])
    assert written == 1
    [head] = [h for h in await env.store.account_heads(key=KEY)
              if h.part == "hypothesis"]
    assert (head.verdict, head.verdict_by) == ("confirmed", "dream:persona-verdict:friends:1")


async def test_a_docket_settled_half_judged_is_held(env):
    """DEC-280's hold: a run that judged only some of what it was shown writes
    nothing and is retried, never settled half-judged."""
    await _hypothesis(env, observable="a short game on a weekday evening",
                      window_end=T0 + 30 * DAY)
    await _hypothesis(env, observable="a long session at the weekend",
                      window_end=T0 + 30 * DAY, prediction="longer sessions on weekends")
    adaptor = _adaptor(env)
    due, _ = await adaptor.evidence.newest(key=UNIT, after=0, limit=5)
    assert "H2" in await adaptor.inputs(key=UNIT, batch=due)
    [verdict] = adaptor.verbs(key=UNIT)
    assert await verdict(verdicts=[{"line": "H1", "verdict": "confirmed"}]) == {"status": "ok"}
    with pytest.raises(AccountNotRecordableError):
        await adaptor.accept(key=UNIT, items=())
    heads = await env.store.account_heads(key=KEY)
    assert [h.verdict for h in heads if h.part == "hypothesis"] == ["open", "open"]


async def test_a_hypothesis_whose_account_rooms_are_gone_is_unknown_by_code(env):
    """A person unlisted since the hypothesis was written: the window can never
    be read (35 §3), so code records `unknown` — the observable unread (R-27),
    never a judgement."""
    await _hypothesis(env, observable="a short game on a weekday evening",
                      window_end=T0 + 30 * DAY)
    assert await _adaptor(env, keys={}).commit(
        key=UNIT, proposals=(), now_ms=T0 + 40 * DAY) == 1
    [head] = [h for h in await env.store.account_heads(key=KEY)
              if h.part == "hypothesis"]
    assert (head.verdict, head.verdict_by) == ("unknown", "code")
