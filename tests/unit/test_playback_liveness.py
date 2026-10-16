"""The rule that answers "is sound actually moving?".

Exhaustive on purpose: `playback_id is not None` is true in five of the
arbiter's six states and audible in at most one, so it cannot decide liveness.
"""

from __future__ import annotations

import pytest

from nora.domain.playback_liveness import (
    PlaybackReading,
    PlaybackVerdict,
    judge_playback,
)


def _reading(**kwargs) -> PlaybackReading:
    return PlaybackReading(reachable=True, **kwargs)


def test_position_advancing_is_the_measurement_that_says_audio_moved() -> None:
    """`position_ms` is `playbackDuration`: @discordjs/voice increments it per
    20ms frame actually consumed and sent. It cannot advance unless audio
    moved, which is why it — and not the status string — is the ground."""
    before = _reading(status="playing", position_ms=12_000)
    after = _reading(status="playing", position_ms=13_280)

    verdict = judge_playback(previous=before, current=after, believed_playing=True)
    assert verdict is PlaybackVerdict.OK


def test_a_frozen_position_while_status_says_playing_is_WEDGED() -> None:
    """The exact live shape: every field green, no sound in the room."""
    before = _reading(status="playing", position_ms=213_320)
    after = _reading(status="playing", position_ms=213_320)

    verdict = judge_playback(previous=before, current=after, believed_playing=True)
    assert verdict is PlaybackVerdict.WEDGED


def test_autopaused_is_WEDGED_even_when_the_position_advanced() -> None:
    """An autopaused player has no subscriber: ffmpeg keeps decoding and the
    room hears nothing, with neither 'idle' nor 'error' ever firing."""
    before = _reading(status="playing", position_ms=1_000)
    after = _reading(status="playing", position_ms=2_000, autopaused=True, subscribers=0)

    verdict = judge_playback(previous=before, current=after, believed_playing=True)
    assert verdict is PlaybackVerdict.WEDGED


def test_idle_while_nora_holds_a_track_is_ENDED_not_wedged() -> None:
    """A track-finished nora never received. It needs an advance, not a
    retry, so it must not collapse into WEDGED."""
    after = _reading(status="idle", position_ms=0)

    verdict = judge_playback(previous=None, current=after, believed_playing=True)
    assert verdict is PlaybackVerdict.ENDED


def test_an_unreachable_controller_is_UNKNOWN_and_never_a_silence_claim() -> None:
    """The whole point. Reading an absence of news as bad news would tear
    down live audio over a blip; reading it as good news is wrong too. It is neither — it is a third
    verdict."""
    verdict = judge_playback(
        previous=_reading(status="playing", position_ms=5_000),
        current=PlaybackReading.unreachable(),
        believed_playing=True,
    )

    assert verdict is PlaybackVerdict.UNKNOWN
    assert verdict is not PlaybackVerdict.WEDGED
    assert verdict is not PlaybackVerdict.OK


def test_a_reachable_but_shapeless_answer_is_UNKNOWN_not_ended() -> None:
    assert judge_playback(
        previous=None, current=_reading(status=None), believed_playing=True,
    ) is PlaybackVerdict.UNKNOWN


def test_a_paused_track_is_PAUSED_and_nothing_advances_for_it() -> None:
    after = _reading(status="paused", position_ms=9_000)

    verdict = judge_playback(previous=after, current=after, believed_playing=True)
    assert verdict is PlaybackVerdict.PAUSED
    assert judge_playback(
        previous=after, current=_reading(status="playing", position_ms=9_000),
        believed_playing=True, believed_paused=True,
    ) is PlaybackVerdict.PAUSED


def test_the_first_poll_cannot_call_a_track_wedged_on_a_frozen_position() -> None:
    """With one sample there is no delta, so a track that just began is
    indistinguishable from one that stopped. Guessing here would kill
    healthy audio."""
    assert judge_playback(
        previous=None, current=_reading(status="playing", position_ms=0), believed_playing=True,
    ) is PlaybackVerdict.OK


def test_a_new_playback_is_not_compared_against_the_previous_tracks_position() -> None:
    """An autoplay advance leaves the old track's last reading
    as `previous`; the new track's smaller position must not read as a position
    that went backwards and be judged WEDGED."""
    old_track = _reading(status="playing", position_ms=195_680, playback_id="pb-old")
    new_track = _reading(status="playing", position_ms=7_400, playback_id="pb-new")

    verdict = judge_playback(previous=old_track, current=new_track, believed_playing=True)
    assert verdict is PlaybackVerdict.OK


def test_a_reading_without_an_id_is_not_compared_across_playbacks_either() -> None:
    """Only one side naming a playback is still not provably the same playback."""
    old_track = _reading(status="playing", position_ms=195_680)
    new_track = _reading(status="playing", position_ms=7_400, playback_id="pb-new")

    verdict = judge_playback(previous=old_track, current=new_track, believed_playing=True)
    assert verdict is PlaybackVerdict.OK


def test_a_frozen_position_within_one_playback_is_still_WEDGED_with_ids() -> None:
    """The guard must not blunt the real detection: same playback, no delta."""
    frozen = _reading(status="playing", position_ms=50_000, playback_id="pb-1")

    verdict = judge_playback(previous=frozen, current=frozen, believed_playing=True)
    assert verdict is PlaybackVerdict.WEDGED


def test_a_slot_stuck_at_starting_across_two_polls_is_WEDGED() -> None:
    """`starting` means accepted-but-no-sound. One poll is not evidence; two
    consecutive ones are — the signature seen from nora's side rather than the
    controller's."""
    stuck = _reading(status="starting", position_ms=0)

    first = judge_playback(previous=None, current=stuck, believed_playing=True)
    second = judge_playback(previous=stuck, current=stuck, believed_playing=True)

    assert first is PlaybackVerdict.OK
    assert second is PlaybackVerdict.WEDGED


def test_nothing_is_falsified_when_nora_claims_nothing() -> None:
    assert judge_playback(
        previous=None, current=PlaybackReading.unreachable(), believed_playing=False,
    ) is PlaybackVerdict.OK


@pytest.mark.parametrize(
    "status",
    ["playing", "starting", "suspended", "resuming", "paused", "idle"],
)
def test_every_arbiter_state_gets_a_verdict_and_none_of_them_raises(status: str) -> None:
    """`playback_id is not None` is true in five of these six states. Each must
    land on a named verdict."""
    verdict = judge_playback(
        previous=None,
        current=_reading(status=status, position_ms=1, playback_id="pb-1"),
        believed_playing=True,
    )

    assert isinstance(verdict, PlaybackVerdict)
