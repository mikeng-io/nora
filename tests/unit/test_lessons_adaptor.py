"""`LessonsAdaptor` — one unit's lessons, per wake of its closed response
episodes.

The real `LessonBook` runs here (its citation, echo, cap and screen checks are
its own); the store under it is a list. What this file pins is the adaptor's:

- every CLOSED episode is shown, `unanswered` included, and may be cited;
- a lesson needs three episodes, and her own sends as bare ids never ground one;
- a session sees handles (`L1`), never a lesson's id, and a verb takes them;
- the session must settle, and an unsettled batch is bounded (DEC-280);
- the horizon wakes the key and `commit` retires what was not re-evidenced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nora.agent.dream_screen import offence
from nora.domain.culture import Episode
from nora.domain.errors import AccountNotRecordableError
from nora.domain.lessons import Lesson, LessonConflictError, LessonRevision
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import VerbProvenance
from nora.language.character import CharacterRoom
from nora.language.lesson_book import LessonBook
from nora.language.lessons import LessonEvidence, LessonsAdaptor, LessonsKey
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence

DAY_MS = 86_400_000
T0 = 1_790_000_000_000
HORIZON = 60 * 86_400
UNIT = "friends"
LK = LessonsKey(unit=UNIT, rooms=(CharacterRoom(key="room-a", platform="signal",
                                                 label="Friends"),))
PROV = VerbProvenance(instruction_sha256="s" * 64, model="m",
                      established_by="dream:lessons:friends:1")


class _Store:
    """`SqlLessonStore`'s surface over a list (append-only, head = max seq)."""

    def __init__(self) -> None:
        self.created: dict[str, tuple[str, int]] = {}
        self.chain: dict[str, list[LessonRevision]] = {}

    def _lesson(self, lesson_id: str) -> Lesson:
        unit, at = self.created[lesson_id]
        return Lesson(id=lesson_id, unit=unit, created_at=at,
                      head=self.chain[lesson_id][-1])

    async def lessons(self, *, unit: str) -> tuple[Lesson, ...]:
        return tuple(self._lesson(i) for i, (u, _a) in self.created.items() if u == unit)

    async def get(self, *, lesson_id: str) -> Lesson | None:
        return self._lesson(lesson_id) if lesson_id in self.created else None

    async def revisions(self, *, lesson_id: str) -> tuple[LessonRevision, ...]:
        return tuple(reversed(self.chain.get(lesson_id, [])))

    async def create(self, *, unit, body, citations, written_at, established_by,
                     instruction_sha256=None, model=None) -> Lesson:
        lesson_id = str(uuid.uuid4())
        self.created[lesson_id] = (unit, written_at)
        self.chain[lesson_id] = [LessonRevision(
            id=str(uuid.uuid4()), lesson_id=lesson_id, seq=1, body=body,
            citations=tuple(citations), state="active", retired_by=None,
            relearn_after=None, note=None, written_at=written_at,
            established_by=established_by, instruction_sha256=instruction_sha256,
            model=model)]
        return self._lesson(lesson_id)

    async def append(self, *, lesson_id, expected_seq, body, citations, state,
                     retired_by, relearn_after, note, written_at, established_by,
                     instruction_sha256=None, model=None) -> LessonRevision:
        chain = self.chain[lesson_id]
        if chain[-1].seq != expected_seq:
            raise LessonConflictError(lesson_id)
        rev = LessonRevision(
            id=str(uuid.uuid4()), lesson_id=lesson_id, seq=expected_seq + 1, body=body,
            citations=tuple(citations), state=state, retired_by=retired_by,
            relearn_after=relearn_after, note=note, written_at=written_at,
            established_by=established_by, instruction_sha256=instruction_sha256,
            model=model)
        chain.append(rev)
        return rev


class _Clock:
    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def _reply(rid: int, body: str) -> TranscriptRow:
    return TranscriptRow(
        id=rid, sender_id="+85200000000", sender_name="spoofed", ts=T0, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id="ada",
    )


EPISODES = (
    Episode(send_id=100, room="room-a", text="dinner at 8?", outcome="responded",
            reactions=2, edits=0, replies=(_reply(101, "yes!! finally a plan"),)),
    Episode(send_id=110, room="room-a", text="here are five places: ...",
            outcome="unanswered", reactions=0, edits=0),
    Episode(send_id=120, room="room-a", text="who's free sunday?", outcome="silent",
            reactions=0, edits=0),
    Episode(send_id=130, room="room-a", text="try the noodle place", outcome="responded",
            reactions=0, edits=0, follows=2),
    Episode(send_id=140, room="room-a", text="open", outcome="open", reactions=0, edits=0),
)

LESSON = "when someone asks where to eat, name one place and why, not a list"


class _Env:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.store = _Store()
        self.book = LessonBook(store=self.store, law=lambda: ["Never share a location."],
                               screen=offence, clock_ms=self.clock)
        self.episodes = InMemoryCharacterEvidence()
        self.metrics = Metrics()
        self.evidence = LessonEvidence(
            store=self.episodes, book=self.book, keys={UNIT: LK}, wake_batch=50,
            horizon_seconds=HORIZON, clock_ms=self.clock)
        self.adaptor = LessonsAdaptor(
            book=self.book, evidence=self.evidence, keys={UNIT: LK},
            name_of=lambda pid: {"ada": "Ada"}.get(pid or "", "someone"),
            horizon_seconds=HORIZON, metrics=self.metrics,
            clock=lambda: datetime.fromtimestamp(self.clock() / 1000, tz=UTC))

    async def session(self, batch=EPISODES):
        prompt = await self.adaptor.inputs(key=UNIT, batch=batch)
        verbs = {v.__name__: v for v in self.adaptor.verbs(key=UNIT, provenance=PROV)}
        return prompt, verbs

    def count(self, outcome: str) -> float:
        return self.metrics.get("nora_lessons_session_total", outcome=outcome)


@pytest.fixture
def env() -> _Env:
    return _Env()


async def test_every_closed_episode_is_shown_with_its_outcome_and_follows(env):
    prompt, _ = await env.session()
    for n in (100, 110, 120, 130):
        assert f'n="response:{n}"' in prompt
    assert 'outcome="unanswered"' in prompt and 'outcome="silent"' in prompt
    assert 'follows="2"' in prompt
    assert '<m n="110" from="you">here are five places: ...</m>' in prompt
    assert '<m n="101" from="Ada">yes!! finally a plan</m>' in prompt
    assert "response:140" not in prompt
    assert "spoofed" not in prompt and "+852" not in prompt


async def test_no_closed_episode_is_a_blank_prompt(env):
    assert await env.adaptor.inputs(key=UNIT, batch=(EPISODES[-1],)) == ""


async def test_a_lesson_rests_on_three_episodes_and_an_unanswered_one_counts(env):
    """An `unanswered` episode is citable (the session judges whether it asked);
    two episodes are not enough; her own sends as bare message ids are echo."""
    _, v = await env.session()
    thin = await v["lesson_write"](body=LESSON, citations=["response:100", "response:110"])
    assert (thin["status"], thin["reason"]) == ("refused", "thin_evidence")
    echo = await v["lesson_write"](body=LESSON, citations=["100", "110", "120"])
    assert (echo["status"], echo["reason"]) == ("refused", "echo")
    ok = await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:110", "response:120"])
    assert ok == {"status": "ok", "lesson_id": "L1", "revision": 1}
    [lesson] = await env.book.active(unit=UNIT)
    assert lesson.head.established_by == PROV.established_by
    assert (lesson.head.instruction_sha256, lesson.head.model) == (PROV.instruction_sha256, "m")


async def test_a_session_sees_handles_never_ids_and_retires_by_handle(env):
    _, v = await env.session()
    made = await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:110", "response:120"])
    assert made["lesson_id"] == "L1"
    await env.adaptor.accept(key=UNIT, items=())
    lesson_id = (await env.book.active(unit=UNIT))[0].id

    prompt, v = await env.session()
    assert '<lesson id="L1"' in prompt and lesson_id not in prompt
    again = await v["lesson_write"](
        body=LESSON + ", and say why", lesson_id="L1",
        citations=["response:100", "response:120", "response:130"])
    assert again == {"status": "ok", "lesson_id": "L1", "revision": 2}
    unknown = await v["lesson_retire"](lesson_id="L9", why="x")
    assert (unknown["status"], unknown["reason"]) == ("refused", "unknown_lesson")
    gone = await v["lesson_retire"](lesson_id="l1", why="folded into another")
    assert gone["status"] == "ok" and gone["lesson_id"] == "L1"
    assert await env.book.active(unit=UNIT) == ()


async def test_a_session_that_settles_nothing_holds_then_is_abandoned(env):
    """No write, no retire, no `lessons_no_change`: the batch is held (a raise
    the runtime reads as `unavailable`) twice, and the third time it is
    abandoned and consumed (DEC-280)."""
    for _ in range(2):
        await env.session()
        with pytest.raises(AccountNotRecordableError):
            await env.adaptor.accept(key=UNIT, items=())
    await env.session()
    assert await env.adaptor.accept(key=UNIT, items=()) == ()
    assert env.count("unsettled") == 3 and env.count("abandoned") == 1


async def test_a_refused_write_alone_does_not_settle_but_no_change_does(env):
    _, v = await env.session()
    await v["lesson_write"](body=LESSON, citations=["999"])
    with pytest.raises(AccountNotRecordableError):
        await env.adaptor.accept(key=UNIT, items=())
    _, v = await env.session()
    assert (await v["lessons_no_change"](why="nothing holds across them")) == {"status": "ok"}
    assert await env.adaptor.accept(key=UNIT, items=()) == ()
    assert env.count("kept") == 1
    assert (await v["lessons_no_change"](why="late"))["status"] == "unavailable"


async def test_a_written_lesson_settles_and_is_counted(env):
    _, v = await env.session()
    await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:110", "response:120"])
    assert await env.adaptor.accept(key=UNIT, items=()) == ()
    assert env.count("written") == 1


async def test_the_horizon_wakes_the_key_and_commit_retires_what_was_not_reinforced(env):
    """A lesson whose head is older than the horizon counts as a full batch, so
    a unit with no new episode still wakes; `commit` (called on a blank run too)
    retires it. A lesson reinforced in the same session is kept."""
    _, v = await env.session()
    old = await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:110", "response:120"])
    kept = await v["lesson_write"](
        body="if a plan gets a reaction, confirm the time once and stop",
        citations=["response:100", "response:120", "response:130"])
    await env.adaptor.accept(key=UNIT, items=())
    assert await env.evidence.count(key=UNIT, after=10_000) == 0

    env.clock.now += HORIZON * 1000
    assert await env.evidence.count(key=UNIT, after=10_000) == 50

    _, v = await env.session()
    await v["lesson_write"](
        body="if a plan gets a reaction, confirm the time once and stop, then go quiet",
        lesson_id=kept["lesson_id"],
        citations=["response:100", "response:120", "response:130"])
    await env.adaptor.accept(key=UNIT, items=())
    # The run wrote two rows: the session's reinforcement (landed through its
    # verb, which the runtime counts as `ok`) and the decay's retirement.
    assert await env.adaptor.commit(key=UNIT, proposals=(), now_ms=env.clock.now) == 2
    [left] = await env.book.active(unit=UNIT)
    assert left.head.body.endswith("then go quiet")
    assert env.count("decayed") == 1
    assert old["lesson_id"] == "L1"


async def test_evidence_counts_closed_episodes_unanswered_included(env):
    for e in EPISODES:
        env.episodes.add_episode(unit=UNIT, episode=e, open_=e.outcome == "open")
    assert await env.evidence.count(key=UNIT, after=0) == 4
    got, cursor = await env.evidence.newest(key=UNIT, after=0, limit=10)
    assert [e.send_id for e in got] == [100, 110, 120, 130] and cursor == 130
    assert env.evidence.held(UNIT) == 0


async def test_an_id_never_reaches_a_tool_result(env):
    _, v = await env.session()
    out = await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:110", "response:120"])
    assert offence(out) is None


def _example_config(**over):
    import tempfile
    from pathlib import Path

    import tomlkit

    from nora.config import load
    from tests.builders import copy_example_config

    with tempfile.TemporaryDirectory() as directory:
        path = copy_example_config(Path(directory))
        data = tomlkit.parse(path.read_text())
        for section, values in over.items():
            data[section].update(values)
        path.write_text(tomlkit.dumps(data))
        return load(path, require_secrets=False)


def test_lessons_ship_off_and_need_the_dreamer():
    assert _example_config().lessons.enabled is False
    from nora.config import ConfigError
    with pytest.raises(ConfigError, match="value_error"):
        _example_config(lessons={"enabled": True}, dreamer={"enabled": False})
    with pytest.raises(ConfigError, match="value_error"):
        _example_config(lessons={"enabled": True}, dreamer={"enabled": True})
    on = _example_config(lessons={"enabled": True, "check_model": "judge"},
                         dreamer={"enabled": True})
    assert (on.lessons.wake_batch, on.lessons.horizon_seconds) == (50, 5_184_000)


async def test_a_refusal_names_a_held_lesson_by_handle_never_by_id(env):
    """`echoes_lesson` names the lesson it repeats. A session addresses lessons
    by handle and the screen refuses an id as an argument, so the refusal must
    say `L1`, and must say what to cite in this session's own terms."""
    _, v = await env.session()
    await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:110", "response:120"])
    twice = await v["lesson_write"](
        body=LESSON, citations=["response:100", "response:120", "response:130"])
    assert twice["reason"] == "echoes_lesson" and "L1" in str(twice["detail"])
    assert offence(twice) is None
    thin = await v["lesson_write"](body="a different practice about timing",
                                   citations=["response:100"])
    assert thin["reason"] == "thin_evidence" and "responded" not in str(thin["detail"])


async def test_fewer_than_three_new_episodes_are_not_handed_over_or_consumed(env):
    """No write could cite so few, so a horizon or stale wake on one or two
    episodes is blank (no model call, `commit` still decays) and the cursor
    stays put, so they reach the batch they belong to."""
    for e in EPISODES[:2]:
        env.episodes.add_episode(unit=UNIT, episode=e)
    assert await env.evidence.newest(key=UNIT, after=0, limit=10) == ([], 0)
    env.episodes.add_episode(unit=UNIT, episode=EPISODES[2])
    got, cursor = await env.evidence.newest(key=UNIT, after=0, limit=10)
    assert [e.send_id for e in got] == [100, 110, 120] and cursor == 120



# -- the correction fast path ------------------------------------------

def _correction_env(quiet_s: int = 600) -> _Env:
    env = _Env()
    env.evidence = LessonEvidence(
        store=env.episodes, book=env.book, keys={UNIT: LK}, wake_batch=50,
        horizon_seconds=HORIZON, clock_ms=env.clock, correction_quiet_seconds=quiet_s,
        metrics=env.metrics)
    env.adaptor.evidence = env.evidence
    return env


def _say(env: _Env, mid: int, body: str, *, ago_s: int, quoted_ts: int | None = None,
         is_nora: bool = False) -> int:
    ts = env.clock.now - ago_s * 1000
    env.episodes.add_message(id=mid, platform="signal", room="room-a",
                             person_id=None if is_nora else "ada", body=body, ts=ts,
                             is_nora=is_nora, quoted_ts=quoted_ts)
    return ts


async def test_a_correction_to_her_wakes_the_unit_once_it_has_settled():
    """One message that speaks to her and tells her how to act is enough to
    wake the unit, without the 50-episode batch, once it is `quiet` old; before
    that it waits, and a message that does not address her never wakes it."""
    env = _correction_env()
    sent = _say(env, 500, "here are five places: a, b, c, d, e", ago_s=900, is_nora=True)
    _say(env, 501, "stop giving five options, one is enough", ago_s=300, quoted_ts=sent)
    _say(env, 502, "don't forget the cake", ago_s=900)
    assert await env.evidence.count(key=UNIT, after=0) == 0
    env.clock.now += 400_000
    assert await env.evidence.count(key=UNIT, after=0) == 50
    assert env.metrics.get("nora_lessons_correction_wake_total") == 1
    got, cursor = await env.evidence.newest(key=UNIT, after=0, limit=10)
    assert [m.row.id for m in got] == [501] and cursor == 0


async def test_a_lesson_citing_a_correction_needs_no_three_episodes():
    env = _correction_env()
    sent = _say(env, 500, "here are five places", ago_s=900, is_nora=True)
    _say(env, 501, "nora 唔好每次都俾五個選擇", ago_s=700, quoted_ts=sent)
    got, _ = await env.evidence.newest(key=UNIT, after=0, limit=10)
    prompt = await env.adaptor.inputs(key=UNIT, batch=got)
    assert "<lesson_corrections" in prompt and 'n="501"' in prompt and 'from="Ada"' in prompt
    assert "<lesson_episodes" not in prompt
    v = {f.__name__: f for f in env.adaptor.verbs(key=UNIT, provenance=PROV)}
    out = await v["lesson_write"](body="when someone asks for options, give one, not five",
                                  citations=["501"])
    assert out["status"] == "ok", out
    assert await env.adaptor.accept(key=UNIT, items=()) == ()
    assert await env.adaptor.commit(key=UNIT, proposals=(), now_ms=env.clock.now) == 1


async def test_a_correction_already_seen_by_a_session_does_not_wake_again():
    env = _correction_env()
    _say(env, 501, "nora stop asking so many questions", ago_s=900)
    assert await env.evidence.count(key=UNIT, after=0) == 50
    env.episodes.set_last_run(adaptor="lessons", key=UNIT, at_ms=env.clock.now)
    assert await env.evidence.count(key=UNIT, after=0) == 0
    assert await env.evidence.corrections(UNIT) == []


async def test_the_fast_path_off_never_reads_corrections():
    env = _Env()
    _say(env, 501, "nora stop asking so many questions", ago_s=900)
    assert await env.evidence.count(key=UNIT, after=0) == 0
    assert await env.evidence.corrections(UNIT) == []


def test_the_correction_pattern_reads_both_languages_and_skips_plain_requests():
    from nora.domain.lessons import is_correction
    for text in ("nora stop sending five options", "@Nora next time just give one",
                 "nora don\u2019t send gifs",
                 "nora 唔好每次都問咁多", "nora 你成日講好長", "nora that was too long"):
        assert is_correction(text), text
    for text in ("nora can you book a table", "what time nora", "nora 幾點食飯", None, ""):
        assert not is_correction(text), text
