"""`VersionAnnouncer` — the boot sweep, with a real ledger and a stub turn.

WHAT IS REAL HERE AND WHAT IS NOT
---------------------------------
Real: the Postgres ledger (the claim IS the guarantee — see
`tests/infra/test_announcement_log.py` for why a double could not stand in), the
`ConversationSlots` the tag path and the tick also hold, the manifest reader
pointed at a real file, and `Metrics`.

Stubbed: the TURN, and only the turn — `_SpokenFor` stands in for
`FirePath.speak`. It behaves like the real thing where behaviour matters: it
answers one of §8.1's four outcome strings (never a bool, never None), it can be
told to answer a FAILURE, and it can be told to RAISE — all three of which the
real fire path does. It also records the `Event` it was handed, so the tests
below can assert what she would have been asked rather than only that something
was asked.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.app.announce import AnnouncementTarget, VersionAnnouncer
from nora.domain.events import Event
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.announcements import ANNOUNCING, DONE, FAILED, SqlAnnouncementLog
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.version import VersionManifest
from nora.obs.metrics import Metrics

T0 = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
G1 = AnnouncementTarget(platform="signal", conversation_id="g1")
G2 = AnnouncementTarget(platform="signal", conversation_id="g2")

STAMP = {
    "version": "V3",
    "versions": [
        {"version": "V3", "released": "2026-08-11",
         "notes": "You can now ask her which version she is running.",
         "changes": ["feat(version): the version tool"]},
        {"version": "V2", "notes": "Reminders."},
    ],
}


class _SpokenFor:
    """The fire path, as much of it as this module can see.

    `outcome` is one of `nora.app.fire`'s §8.1 strings — the real `speak`
    answers nothing else, and a stub that returned `True` would let a test pass
    against an announcer that could not tell a delivered note from a timeout.
    """

    def __init__(self, outcome: str = "sent", raises: Exception | None = None) -> None:
        self.outcome = outcome
        self.raises = raises
        self.occasions: list[Event] = []

    async def __call__(self, occasion: Event) -> str:
        self.occasions.append(occasion)
        if self.raises is not None:
            raise self.raises
        return self.outcome


@pytest.fixture
async def engine(pg_url):
    run_migrations(pg_url)
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _stamp(tmp_path, payload=None):
    import json
    path = tmp_path / "version.json"
    path.write_text(json.dumps(payload if payload is not None else STAMP),
                    encoding="utf-8")
    return path


def _announcer(
    engine, stamp_path, speak, *, targets=(G1,), slots=None, metrics=None,
) -> VersionAnnouncer:
    """A whole announcer, built the way the composition root builds one.

    Everything except `speak` is the production object. Constructed per call
    rather than shared, because that is exactly what a process restart does —
    see `test_a_restart_never_re_announces_the_same_version`.
    """
    return VersionAnnouncer(
        manifest=VersionManifest(stamp_path),
        store=SqlAnnouncementLog(engine),
        targets=targets,
        speak=speak,
        slots=slots if slots is not None else ConversationSlots(
            maxlen=4, spawn=lambda coro: None,
        ),
        metrics=metrics if metrics is not None else Metrics(),
        clock=lambda: T0,
    )


async def test_a_new_version_is_announced_once(engine, tmp_path):
    speak = _SpokenFor()
    metrics = Metrics()

    await _announcer(engine, _stamp(tmp_path), speak, metrics=metrics).run()

    assert len(speak.occasions) == 1
    occasion = speak.occasions[0]
    assert occasion.conversation_id == "g1"
    assert occasion.kind == "version_announcement"
    # The OPERATOR'S note, intact — she relays it, so it has to arrive whole.
    assert occasion.body == STAMP["versions"][0]["notes"]
    # Nobody asked for this turn, and that is what stops FirePath refusing it
    # under §12's "this requester left identity.toml" rule.
    assert occasion.person_id is None
    assert await SqlAnnouncementLog(engine).announced(version="V3") == [
        ("signal", "g1", DONE),
    ]
    assert metrics.get("nora_version_announcements_total", status="sent") == 1


async def test_a_restart_never_re_announces_the_same_version(engine, tmp_path):
    """R-42, and the reason the whole ledger exists.

    A RESTART is modelled the way a restart actually works: everything
    in-process is rebuilt — a new announcer, a new manifest reader, a new store
    handle, new slots — and the only thing that survives is the database. If the
    claim were in memory (or were a read-then-write), the second sweep would
    speak, and a crash-looping container would post the same upgrade note every
    few seconds into a group chat.

    Three boots, not two, because "the second one is quiet" can be satisfied by
    an off-by-one that speaks on every odd boot.
    """
    stamp = _stamp(tmp_path)
    first = _SpokenFor()
    await _announcer(engine, stamp, first).run()
    assert len(first.occasions) == 1

    for boot in range(2):
        again = _SpokenFor()
        metrics = Metrics()
        await _announcer(engine, stamp, again, metrics=metrics).run()

        assert again.occasions == [], f"re-announced on boot {boot + 2}"
        assert metrics.get(
            "nora_version_announcements_total", status="already_announced",
        ) == 1
    # …and the ledger still records the ONE announcement that happened.
    assert await SqlAnnouncementLog(engine).announced(version="V3") == [
        ("signal", "g1", DONE),
    ]


async def test_a_crash_between_claiming_and_speaking_is_never_re_announced(
    engine, tmp_path,
):
    """At-most-once across the ONE window a state check would get wrong.

    The turn raised after the claim — the group may already have heard part of
    it, because `Deliverer` sends before the turn ends. The row is left
    `announcing`, and the next boot stays quiet: nothing links a claim to a
    delivered message, so a retry double-posts anything that died after egress
    (R-37b's argument, applied to the second unprompted path).
    """
    stamp = _stamp(tmp_path)
    crashed = _SpokenFor(raises=RuntimeError("the process died mid-turn"))
    await _announcer(engine, stamp, crashed).run()      # must not propagate
    assert await SqlAnnouncementLog(engine).announced(version="V3") == [
        ("signal", "g1", ANNOUNCING),
    ]

    after_reboot = _SpokenFor()
    await _announcer(engine, stamp, after_reboot).run()

    assert after_reboot.occasions == []


async def test_a_failed_turn_is_recorded_and_not_retried(engine, tmp_path):
    """R-5 keeps the failure out of the room, so the row and the counter are its
    only trace — and `sent` is the ONLY outcome that means the group heard
    something, exactly as it is for a reminder."""
    stamp = _stamp(tmp_path)
    metrics = Metrics()
    await _announcer(
        engine, stamp, _SpokenFor(outcome="failed_timeout"), metrics=metrics,
    ).run()

    assert await SqlAnnouncementLog(engine).announced(version="V3") == [
        ("signal", "g1", FAILED),
    ]
    assert metrics.get(
        "nora_version_announcements_total", status="failed_timeout") == 1
    assert metrics.get("nora_version_announcements_total", status="sent") == 0

    retried = _SpokenFor()
    await _announcer(engine, stamp, retried).run()
    assert retried.occasions == []


async def test_a_new_version_after_an_announced_one_does_speak(engine, tmp_path):
    """The bar is per version. An announcer that went quiet forever after the
    first note would satisfy every «never twice» assertion above and be
    useless."""
    await _announcer(engine, _stamp(tmp_path), _SpokenFor()).run()

    speak = _SpokenFor()
    await _announcer(
        engine,
        _stamp(tmp_path, {"version": "V4",
                          "versions": [{"version": "V4", "notes": "Weather."}]}),
        speak,
    ).run()

    assert [o.body for o in speak.occasions] == ["Weather."]


async def test_every_allowed_room_hears_it(engine, tmp_path):
    speak = _SpokenFor()

    await _announcer(engine, _stamp(tmp_path), speak, targets=(G1, G2)).run()

    assert [o.conversation_id for o in speak.occasions] == ["g1", "g2"]


async def test_one_rooms_failure_does_not_cost_the_next_room_its_note(
    engine, tmp_path,
):
    """The per-target guard, made true: a store or a turn that explodes for g1
    must not silently skip every room ordered after it."""
    class _BoomInG1(_SpokenFor):
        async def __call__(self, occasion: Event) -> str:
            if occasion.conversation_id == "g1":
                raise RuntimeError("g1 exploded")
            return await super().__call__(occasion)

    speak = _BoomInG1()
    metrics = Metrics()
    await _announcer(
        engine, _stamp(tmp_path), speak, targets=(G1, G2), metrics=metrics,
    ).run()

    assert [o.conversation_id for o in speak.occasions] == ["g2"]
    assert metrics.get("nora_errors_total", stage="announce") == 1


async def test_an_unreadable_stamp_announces_nothing_and_claims_nothing(
    engine, tmp_path,
):
    """R-27 at the announcement level. An unstamped build must not announce a
    version it guessed — and must not CLAIM one either, or the day the build is
    fixed the note would already be marked as delivered."""
    speak = _SpokenFor()
    metrics = Metrics()

    await _announcer(
        engine, tmp_path / "never-stamped.json", speak, metrics=metrics,
    ).run()

    assert speak.occasions == []
    assert await SqlAnnouncementLog(engine).announced(version="V3") == []
    assert metrics.get("nora_version_announcements_total", status="unavailable") == 1


async def test_a_version_with_no_release_note_says_nothing(engine, tmp_path):
    """The operator writes the note; a version tagged without one has nothing
    anybody wrote. She stays quiet rather than reading out commit subjects —
    and, crucially, does NOT claim the version, so annotating the tag and
    rebuilding still announces it."""
    speak = _SpokenFor()
    metrics = Metrics()
    stamp = _stamp(tmp_path, {
        "version": "V3",
        "versions": [{"version": "V3", "notes": "",
                      "changes": ["refactor(agent): tidy the registry"]}],
    })

    await _announcer(engine, stamp, speak, metrics=metrics).run()

    assert speak.occasions == []
    assert await SqlAnnouncementLog(engine).announced(version="V3") == []
    assert metrics.get("nora_version_announcements_total", status="no_note") == 1


async def test_a_tag_holding_the_conversation_defers_the_note(engine, tmp_path):
    """The slot rule the tick uses: an announcement neither queues behind a tag
    nor sheds it. Nothing is claimed, so the next boot tries again — which is
    right, because nothing was said."""
    slots = ConversationSlots(maxlen=4, spawn=lambda coro: None)
    assert slots.try_acquire(("signal", "g1"))
    speak = _SpokenFor()
    metrics = Metrics()

    await _announcer(
        engine, _stamp(tmp_path), speak, slots=slots, metrics=metrics,
    ).run()

    assert speak.occasions == []
    assert await SqlAnnouncementLog(engine).announced(version="V3") == []
    assert metrics.get("nora_version_announcements_total", status="deferred") == 1


async def test_the_slot_is_released_even_when_the_turn_raises(engine, tmp_path):
    """A turn that dies must not strand whatever queued behind it — the tag
    path's and the tick's `finally`, kept here too."""
    slots = ConversationSlots(maxlen=4, spawn=lambda coro: None)

    await _announcer(
        engine, _stamp(tmp_path), _SpokenFor(raises=RuntimeError("boom")),
        slots=slots,
    ).run()

    assert not slots.held(("signal", "g1"))
