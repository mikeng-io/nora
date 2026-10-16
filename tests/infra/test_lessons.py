"""Lessons through the book and Postgres (`nora.language.lesson_book.LessonBook`,
`nora.infra.sql.lessons.SqlLessonStore`, `nora lessons`).

Real Postgres: the chain's head is the highest `seq` and `uq_lesson_revision_seq`
decides a race, and the row CHECKs are what make a bad write impossible rather
than unlikely. The pure refusals are `tests/unit/test_lessons_checks.py`.
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import nora.app.cli as cli
from nora.agent.dream_screen import offence
from nora.domain.culture import Citable
from nora.domain.lessons import LESSON_MAX_ACTIVE
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.language.lesson_book import LessonBook

UNIT = "friends"
LAW = ["Never share a member's location with anyone outside the room it came from."]
CITABLE = Citable(
    messages=frozenset(range(100, 140)), nora=frozenset({100}),
    episodes=frozenset({110, 111, 112}), echo_rule=True,
)
ONE = "when somebody asks what to eat, name one place and why, never a list of five"
TWO = "if a joke lands twice in a row in this room, leave the third one out and say less"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_790_000_000_000

    def __call__(self) -> int:
        self.now += 1
        return self.now


@pytest.fixture
async def env(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    store = SqlLessonStore(engine)
    book = LessonBook(store=store, law=lambda: LAW, screen=offence, clock_ms=_Clock())
    try:
        yield SimpleNamespace(engine=engine, store=store, book=book, url=pg_url)
    finally:
        await engine.dispose()


async def _write(env, body=ONE, citations=("110", "111"), **kw):
    return await env.book.learn(
        unit=UNIT, citable=CITABLE, body=body, citations=citations,
        lesson_id=kw.pop("lesson_id", None), established_by="dreamer:lessons", **kw,
    )


async def test_a_lesson_is_written_with_its_citations_and_listed(env):
    out = await _write(env)
    assert out == {"status": "ok", "lesson_id": out["lesson_id"], "revision": 1}
    [lesson] = await env.book.active(unit=UNIT)
    assert lesson.id == out["lesson_id"] and lesson.head.body == ONE
    assert lesson.head.citations == ("110", "111") and lesson.head.state == "active"
    assert await env.book.active(unit="ridgeline") == ()


async def test_reinforcing_appends_a_revision_and_keeps_the_chain(env):
    first = await _write(env)
    again = await _write(env, body=ONE + ", and say why", citations=("110", "112"),
                         lesson_id=first["lesson_id"])
    assert again["revision"] == 2 and again["lesson_id"] == first["lesson_id"]
    chain = await env.store.revisions(lesson_id=first["lesson_id"])
    assert [r.seq for r in chain] == [2, 1]
    assert chain[1].body == ONE and chain[0].body.endswith("say why")
    assert len(await env.book.active(unit=UNIT)) == 1


async def test_the_verbs_are_the_book_bound_to_one_session(env):
    write, retire = env.book.verbs(unit=UNIT, citable=CITABLE, established_by="dreamer:lessons",
                                   model="m")
    made = await write(body=ONE, citations=["110", "111"])
    assert made["status"] == "ok"
    refused = await write(body=TWO, citations=["999"])
    assert refused["status"] == "refused" and refused["reason"] == "unresolved_citation"
    done = await retire(lesson_id=made["lesson_id"], why="no longer true here")
    assert done["status"] == "ok"
    assert await env.book.active(unit=UNIT) == ()
    chain = await env.store.revisions(lesson_id=made["lesson_id"])
    assert chain[0].state == "retired" and chain[0].retired_by == "session"
    assert chain[0].relearn_after is None and chain[1].model == "m"
    assert (await retire(lesson_id=made["lesson_id"], why="again"))["reason"] == "not_active"


async def test_a_refused_write_reaches_no_row(env):
    for kwargs, reason in [
        ({"citations": ()}, "uncited"),
        ({"citations": ("999",)}, "unresolved_citation"),
        ({"citations": ("100",)}, "echo"),
        ({"body": LAW[0]}, "echoes_doctrine"),
        ({"body": "call her on +85291234567 for the details of how to act"}, "identifier"),
        ({"body": "x" * 500}, "too_long"),
    ]:
        out = await _write(env, **kwargs)
        assert (out["status"], out["reason"]) == ("refused", reason), kwargs
    async with env.engine.connect() as conn:
        assert (await conn.execute(sa.text("select count(*) from lesson"))).scalar_one() == 0
        assert (await conn.execute(
            sa.text("select count(*) from lesson_revision"))).scalar_one() == 0


async def test_a_second_copy_of_a_held_lesson_is_refused_and_a_foreign_id_is_unknown(env):
    first = await _write(env)
    assert (await _write(env))["reason"] == "echoes_lesson"
    assert (await _write(env, body=TWO, lesson_id="00000000-0000-0000-0000-000000000000")
            )["reason"] == "unknown_lesson"
    other = await env.book.learn(
        unit="ridgeline", citable=CITABLE, body=TWO, citations=("110",), lesson_id=None,
        established_by="t")
    foreign = await _write(env, body=TWO, lesson_id=other["lesson_id"])
    assert foreign["reason"] == "unknown_lesson"
    assert first["status"] == "ok"


async def test_the_cap_refuses_until_a_lesson_is_retired(env):
    ids = []
    for i in range(LESSON_MAX_ACTIVE):
        out = await _write(env, body=f"practice {i}: " + f"item{i} " * 6 + "ok")
        assert out["status"] == "ok", out
        ids.append(out["lesson_id"])
    refused = await _write(env, body="one more distinct practice about timing and tone")
    assert (refused["status"], refused["reason"]) == ("refused", "cap")
    assert (await env.book.retire(unit=UNIT, lesson_id=ids[0], by="session", why="folded",
                                  established_by="t"))["status"] == "ok"
    assert (await _write(env, body="one more distinct practice about timing and tone")
            )["status"] == "ok"


async def test_an_operator_retirement_is_not_learned_again_from_the_same_evidence(env):
    first = await _write(env, citations=("110", "111"))
    out = await env.book.retire(unit=UNIT, lesson_id=first["lesson_id"], by="operator",
                                why="wrong for this unit", established_by="operator:cli",
                                record_head=120)
    assert out["status"] == "ok"
    head = (await env.store.revisions(lesson_id=first["lesson_id"]))[0]
    assert (head.state, head.retired_by, head.relearn_after) == ("retired", "operator", 120)
    # The same outcomes, reworded, and older ones: refused.
    assert (await _write(env, citations=("110", "111")))["reason"] == "relearn"
    assert (await _write(env, body=TWO, citations=("105", "112")))["reason"] == "relearn"
    # Outcomes after the retirement are new evidence: allowed.
    assert (await _write(env, body=TWO, citations=("110", "125")))["status"] == "ok"
    # A SESSION's retirement carries no such floor: consolidation may re-cite old evidence.
    second = await _write(env, body="reply in the language the person wrote in, every time",
                          citations=("130",))
    await env.book.retire(unit=UNIT, lesson_id=second["lesson_id"], by="session", why="merge",
                          established_by="t")
    assert (await _write(env, body="match the language of whoever you answer, always",
                         citations=("130",)))["status"] == "ok"


async def test_rollback_appends_an_earlier_revision_and_undoes_a_retirement(env):
    first = await _write(env)
    lid = first["lesson_id"]
    await _write(env, body=ONE + ", and say why", lesson_id=lid)
    back = await env.book.rollback(
        unit=UNIT, lesson_id=lid, to_seq=1, established_by="operator:cli")
    assert back == {"status": "ok", "lesson_id": lid, "revision": 3}
    chain = await env.store.revisions(lesson_id=lid)
    assert chain[0].body == ONE and chain[0].note == "rollback to revision 1"
    assert [r.seq for r in chain] == [3, 2, 1]  # nothing was deleted or edited
    await env.book.retire(unit=UNIT, lesson_id=lid, by="operator", why="x",
                          established_by="operator:cli", record_head=120)
    assert await env.book.active(unit=UNIT) == ()
    undone = await env.book.rollback(unit=UNIT, lesson_id=lid, to_seq=3,
                                     established_by="operator:cli")
    assert undone["status"] == "ok"
    [lesson] = await env.book.active(unit=UNIT)
    assert lesson.head.relearn_after is None and lesson.head.seq == 5
    # Undoing the retirement lifts its re-learn floor with it.
    assert (await _write(env, body=TWO, citations=("105",)))["status"] == "ok"


async def test_rollback_refuses_an_unknown_revision_and_the_head(env):
    lid = (await _write(env))["lesson_id"]
    assert (await env.book.rollback(unit=UNIT, lesson_id=lid, to_seq=9,
                                    established_by="o"))["status"] == "refused"
    assert (await env.book.rollback(unit=UNIT, lesson_id=lid, to_seq=1,
                                    established_by="o"))["status"] == "refused"
    assert (await env.book.rollback(unit="other", lesson_id=lid, to_seq=1,
                                    established_by="o"))["reason"] == "unknown_lesson"


async def test_the_table_refuses_what_the_book_never_writes(env):
    lid = (await _write(env))["lesson_id"]
    cols = ("lesson_id", "seq", "body", "citations", "state", "retired_by", "written_at",
            "established_by")
    base = {"lesson_id": lid, "seq": 2, "body": "x", "citations": ["1"], "state": "active",
            "retired_by": None, "written_at": 1, "established_by": "t"}
    for override in (
        {"seq": 1},                                       # that seq is taken
        {"citations": []},                                # cites nothing
        {"state": "retired", "retired_by": "operator"},   # an operator retirement with no floor
        {"state": "retired"},                             # retired by nobody
        {"body": "x" * 2_001},                            # past the database ceiling
    ):
        row = {**base, **override}
        stmt = sa.text(
            f"insert into lesson_revision({', '.join(cols)}) "  # noqa: S608 (fixed names)
            f"values ({', '.join(':' + c for c in cols)})"
        )
        with pytest.raises(sa.exc.IntegrityError):
            async with env.engine.begin() as conn:
                await conn.execute(stmt, row)


# -- the operator's CLI --------------------------------------------------------


async def _cli(monkeypatch, env, func, capsys, **args):
    monkeypatch.setattr(cli, "_config", lambda a: SimpleNamespace())
    monkeypatch.setattr(cli, "_database_url", lambda cfg: env.url)
    rc = await func(Namespace(config="unused.toml", **args))
    return rc, capsys.readouterr().out


async def test_the_cli_lists_shows_retires_and_rolls_back(env, monkeypatch, capsys):
    lid = (await _write(env))["lesson_id"]
    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_list, capsys, unit=UNIT, all=False)
    assert rc == 0 and lid in out and ONE in out
    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_show, capsys, lesson_id=lid)
    assert rc == 0 and "rev 1" in out and "cites: 110, 111" in out

    # A dry run writes nothing.
    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_retire, capsys, lesson_id=lid,
                         why="wrong", apply=False)
    assert rc == 0 and "nothing written" in out
    assert len(await env.book.active(unit=UNIT)) == 1

    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_retire, capsys, lesson_id=lid,
                         why="wrong", apply=True)
    assert rc == 0 and json.loads(out)["status"] == "ok"
    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_list, capsys, unit=UNIT, all=False)
    assert "no active lessons" in out
    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_list, capsys, unit=UNIT, all=True)
    assert "retired by operator" in out
    # No re-learn: an empty Record means the floor is the lesson's own newest citation (111).
    assert (await _write(env, body=TWO, citations=("110", "111")))["reason"] == "relearn"

    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_rollback, capsys, lesson_id=lid,
                         to=1, apply=True)
    assert rc == 0 and json.loads(out)["revision"] == 3
    assert len(await env.book.active(unit=UNIT)) == 1
    rc, out = await _cli(monkeypatch, env, cli.cmd_lessons_rollback, capsys, lesson_id=lid,
                         to=9, apply=True)
    assert rc == 1 and json.loads(out)["status"] == "refused"
    with pytest.raises(cli.CliError):
        await _cli(monkeypatch, env, cli.cmd_lessons_show, capsys, lesson_id="not-there")
    assert rc == 1


def test_the_subcommands_are_registered():
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli.add_subcommands(sub, argparse.ArgumentParser(add_help=False))
    got = parser.parse_args(["lessons", "rollback", "abc", "--to", "2", "--apply"])
    assert (got.func, got.to, got.apply) == (cli.cmd_lessons_rollback, 2, True)
    assert parser.parse_args(["lessons", "list", "--unit", "u"]).func is cli.cmd_lessons_list


async def test_a_lesson_id_that_is_not_a_uuid_names_no_lesson(env):
    """The model supplies the id; a malformed one is a refusal, not a database error."""
    assert (await env.book.retire(unit=UNIT, lesson_id="not-there", by="session", why="x",
                                  established_by="t"))["reason"] == "unknown_lesson"
    assert (await env.book.rollback(unit=UNIT, lesson_id="3", to_seq=1,
                                    established_by="t"))["reason"] == "unknown_lesson"
    assert await env.store.get(lesson_id="x") is None
    assert await env.store.revisions(lesson_id="x") == ()


class _RacingStore:
    """The store, except that right after `lessons` is read once, `between` runs:
    the interleaving of a writer that has judged a head and an operator who then
    moves it."""

    def __init__(self, inner, between):
        self._inner, self._between, self._armed = inner, between, True

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def lessons(self, *, unit):
        held = await self._inner.lessons(unit=unit)
        if self._armed:
            self._armed = False
            await self._between()
        return held


async def test_an_operator_retirement_between_a_read_and_a_reinforce_is_not_undone(env):
    first = await _write(env)
    lid = first["lesson_id"]

    async def operator_retires():
        await env.book.retire(unit=UNIT, lesson_id=lid, by="operator", why="wrong",
                              established_by="operator:cli", record_head=120)

    racing = LessonBook(store=_RacingStore(env.store, operator_retires), law=lambda: LAW,
                        screen=offence, clock_ms=_Clock())
    out = await racing.learn(
        unit=UNIT, citable=CITABLE, body=ONE + ", and say why", citations=("110", "112"),
        lesson_id=lid, established_by="dreamer:lessons")
    assert (out["status"], out["reason"]) == ("refused", "conflict")
    head = (await env.store.revisions(lesson_id=lid))[0]
    assert (head.seq, head.state, head.retired_by, head.relearn_after) == (2, "retired",
                                                                           "operator", 120)


async def test_a_raced_seq_is_a_conflict_not_a_raw_integrity_error(env):
    """Two appends that both read head 1: the second loses to
    `uq_lesson_revision_seq` and says so as a conflict."""
    import asyncio

    from nora.domain.lessons import LessonConflictError
    lid = (await _write(env))["lesson_id"]

    async def one(n):
        return await env.store.append(
            lesson_id=lid, expected_seq=1, body=f"revision body number {n}", citations=("110",),
            state="active", retired_by=None, relearn_after=None, note=None, written_at=n,
            established_by="t")

    results = await asyncio.gather(one(1), one(2), return_exceptions=True)
    assert sum(isinstance(r, LessonConflictError) for r in results) == 1
    assert sum(not isinstance(r, BaseException) for r in results) == 1
    assert [r.seq for r in await env.store.revisions(lesson_id=lid)] == [2, 1]


async def test_the_operator_can_set_the_floor_on_a_lesson_a_session_already_retired(env):
    lid = (await _write(env, citations=("110", "111")))["lesson_id"]
    await env.book.retire(unit=UNIT, lesson_id=lid, by="session", why="merged",
                          established_by="t")
    # The session's retirement set no floor: the same evidence may be learned again.
    out = await env.book.retire(unit=UNIT, lesson_id=lid, by="operator", why="still wrong",
                                established_by="operator:cli", record_head=120)
    assert out["status"] == "ok" and out["revision"] == 3
    head = (await env.store.revisions(lesson_id=lid))[0]
    assert (head.state, head.retired_by, head.relearn_after) == ("retired", "operator", 120)
    assert (await _write(env, body=TWO, citations=("110", "111")))["reason"] == "relearn"
    # Nothing retires twice beyond that.
    again = await env.book.retire(unit=UNIT, lesson_id=lid, by="operator", why="x",
                                  established_by="o", record_head=130)
    assert again["reason"] == "not_active"
    assert (await env.book.retire(unit=UNIT, lesson_id=lid, by="session", why="x",
                                  established_by="t"))["reason"] == "not_active"


async def test_losing_the_unique_seq_race_is_a_conflict_and_other_integrity_errors_are_not(
    env, monkeypatch,
):
    """Two writers can both pass the head check and meet at `uq_lesson_revision_seq`;
    that loss is a conflict. Any other integrity error stays loud."""
    from nora.domain.lessons import LessonConflictError
    lid = (await _write(env))["lesson_id"]
    kw = {"lesson_id": lid, "expected_seq": 1, "body": "another revision body",
          "citations": ("110",), "state": "active", "retired_by": None, "relearn_after": None,
          "note": None, "written_at": 1, "established_by": "t"}

    def losing(constraint):
        async def boom(**_):
            raise sa.exc.IntegrityError("insert", {}, Exception(constraint))
        return boom

    monkeypatch.setattr(env.store, "_append", losing("duplicate key uq_lesson_revision_seq"))
    with pytest.raises(LessonConflictError):
        await env.store.append(**kw)
    monkeypatch.setattr(env.store, "_append", losing("ck_lesson_revision_state"))
    with pytest.raises(sa.exc.IntegrityError):
        await env.store.append(**kw)


async def test_a_tension_row_round_trips_and_lists_newest_first(env):
    """The operator's record of a lesson the doctrine check refused, with the
    rule quoted (`nora.infra.sql.tension`, migration `0101_self_tension`)."""
    from nora.domain.doctrine_check import Tension
    from nora.infra.sql.tension import SqlTensionStore

    store = SqlTensionStore(env.engine)
    for n, unit in enumerate(("friends", "ridgeline", "friends")):
        await store.record(Tension(
            unit=unit, source="lesson", doctrine_file="core/constraints.md",
            doctrine_line=10 + n, doctrine_text=LAW[0], would_have_said=f"{ONE} {n}",
            citations=("response:110", "response:111"), why="it shares a place",
            written_at=1_790_000_000_000 + n, established_by="dream:lessons:friends:1",
            instruction_sha256="a" * 64, model="writer", check_model="judge"))
    mine = await store.recent(unit="friends")
    assert [t.doctrine_line for t in mine] == [12, 10]
    assert mine[0].citations == ("response:110", "response:111") and mine[0].id
    assert mine[0].check_model == "judge" and mine[0].doctrine_text == LAW[0]
    assert len(await store.recent()) == 3
    with pytest.raises(sa.exc.IntegrityError):
        await store.record(Tension(
            unit="friends", source="character", doctrine_file="x", doctrine_line=1,
            doctrine_text="y", would_have_said="z", citations=(), why="w",
            written_at=1, established_by="t"))


async def test_the_snapshot_holds_the_lessons_active_at_the_instant(env):
    """The eval sandbox's copy of `<lessons>`: per lesson, the revision
    current at `before_ms`, kept when active; a lesson written after the instant
    is absent."""
    from nora.infra.sql.snapshot import _lessons

    first = await env.store.create(unit=UNIT, body=ONE, citations=("110",), written_at=10,
                                   established_by="t")
    await env.store.append(lesson_id=first.id, expected_seq=1, body=ONE, citations=("110",),
                           state="retired", retired_by="session", relearn_after=None,
                           note="gone", written_at=20, established_by="t")
    await env.store.create(unit=UNIT, body=TWO, citations=("111",), written_at=30,
                           established_by="t")
    await env.store.create(unit="ridgeline", body=TWO, citations=("111",), written_at=5,
                           established_by="t")
    assert await _lessons(env.engine, unit=UNIT, before_ms=5) == ()
    assert await _lessons(env.engine, unit=UNIT, before_ms=15) == (ONE,)
    assert await _lessons(env.engine, unit=UNIT, before_ms=25) == ()
    assert await _lessons(env.engine, unit=UNIT, before_ms=35) == (TWO,)
    third = await env.store.create(unit=UNIT, body="a third practice, said short",
                                   citations=("112",), written_at=40, established_by="t")
    await env.store.append(lesson_id=first.id, expected_seq=2, body=ONE, citations=("110",),
                           state="active", retired_by=None, relearn_after=None,
                           note="rollback to revision 1", written_at=50, established_by="t")
    # Oldest lesson first, and a lesson rolled back to active is shown again.
    assert await _lessons(env.engine, unit=UNIT, before_ms=60) == (
        ONE, TWO, "a third practice, said short")
    assert third.id


async def test_a_lessons_counterfactual_seeds_active_lessons_in_the_stated_order(env):
    """The eval's `lessons` tier writes the case's lessons into the subject's
    unit, active, oldest first, at the sandbox's own instant, so `<lessons>`
    renders them in the order the case states."""
    from nora.subject.sandbox import seed_lessons

    subject = SimpleNamespace(database_url=env.url, unit=UNIT)
    await seed_lessons(subject, "probe", [ONE, TWO], now_ms=1_790_000_000_000)
    held = await env.book.active(unit=UNIT)
    assert [x.head.body for x in held] == [ONE, TWO]
    assert held[0].head.written_at < held[1].head.written_at <= 1_790_000_000_000
    assert held[0].head.citations == ("case:probe",)
    await seed_lessons(subject, "probe", [], now_ms=1)
    assert len(await env.book.active(unit=UNIT)) == 2


async def test_a_lessons_counterfactual_is_refused_where_the_unit_already_held_lessons(env):
    from nora.subject.sandbox import seed_lessons

    subject = SimpleNamespace(database_url=env.url, unit=UNIT)
    await seed_lessons(subject, "probe", [ONE], now_ms=1_790_000_000_000)
    with pytest.raises(ValueError, match="already held 1"):
        await seed_lessons(subject, "probe", [TWO], now_ms=1_790_000_000_000)
