"""Is sound ACTUALLY moving?

`playback_id is not None` is not proof of sound: it is true in five of the
arbiter's six states and audible in at most one. Nor is
`player.status == "playing"`: the AudioPlayer
reaches Playing whether or not a connection is subscribed to it, so an
autopaused player reads `playing` while the room hears silence.

The measurement that is not a proxy is `position_ms`. It is
`startOffsetMs + resource.playbackDuration`, and @discordjs/voice increments
`playbackDuration` for every 20 ms frame it has actually consumed and sent.
It cannot advance unless audio moved. Two readings a poll apart therefore
answer the question a status string cannot:

    playing + position advanced        -> audio is moving          OK
    playing + position frozen          -> nothing is being heard   WEDGED
    playing + autopaused               -> nobody is listening      WEDGED
    idle, while nora holds a track     -> a finish we never heard  ENDED
    unreachable                        -> we do not know           UNKNOWN

UNKNOWN is a distinct verdict on purpose. "I could not check" must never
collapse into "not playing" or into "fine": an absence of news is not good
news, and a fail-open liveness check would read it as such.

Pure by design — no I/O, no clock, no session. The transport hands it two
readings and it returns a verdict, so the rule can be tested exhaustively
without a controller, and the same rule is used by every caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Arbiter states in which a slot exists and NO audio is reaching the room.
#: `paused` is deliberately absent: a paused track is not playing, but it is
#: not broken either, and nothing should advance the queue for it.
_SILENT_STATES = frozenset({"starting", "resuming", "suspended"})


class PlaybackVerdict(StrEnum):
    OK = "ok"            # audio is moving
    WEDGED = "wedged"    # believed playing, provably producing no sound
    ENDED = "ended"      # the controller finished and nora never heard
    PAUSED = "paused"    # intentionally not playing
    UNKNOWN = "unknown"  # the controller could not be read — NEVER "not playing"


@dataclass(frozen=True)
class PlaybackReading:
    """One `/voice/status` sample, reduced to what the verdict needs.

    `reachable=False` is the whole point of the flag: an exception, a
    timeout, or a body nora could not parse must arrive here as a reading,
    not as a missing one, so the caller cannot silently treat it as idle."""

    reachable: bool
    status: str | None = None          # idle|starting|playing|paused|suspended|resuming
    position_ms: int | None = None
    autopaused: bool | None = None
    subscribers: int | None = None
    playback_id: str | None = None

    @classmethod
    def unreachable(cls) -> PlaybackReading:
        return cls(reachable=False)


def judge_playback(
    *,
    previous: PlaybackReading | None,
    current: PlaybackReading,
    believed_playing: bool,
    believed_paused: bool = False,
) -> PlaybackVerdict:
    """The verdict for one guild, from two consecutive readings.

    `previous` is None on the first poll after nora starts believing a track
    is live; with only one sample there is no position DELTA yet, so a frozen
    position cannot be distinguished from a track that just began. The first
    poll can still return ENDED or WEDGED-by-autopause, which need no delta.
    """
    if previous is not None and previous.playback_id != current.playback_id:
        # A position is only comparable within ONE playback. After an
        # autoplay advance the old track's last reading (e.g. 195 s) sits
        # against the new track's first (e.g. 7 s), which would read as "went
        # backwards" and judge WEDGED.
        previous = None
    if not believed_playing:
        # No claim, so nothing to falsify — not even an unreachable
        # controller is a problem when nora is not asserting anything.
        return PlaybackVerdict.OK
    if not current.reachable:
        return PlaybackVerdict.UNKNOWN

    status = current.status
    if status is None:
        # Reachable but shapeless: the controller answered something nora
        # cannot read. That is not evidence of silence.
        return PlaybackVerdict.UNKNOWN
    if status == "idle":
        return PlaybackVerdict.ENDED
    if status == "paused" or believed_paused:
        return PlaybackVerdict.PAUSED
    if current.autopaused:
        # The player is up, the decoder is fed, and nothing is subscribed:
        # silence with every other field green.
        return PlaybackVerdict.WEDGED
    if status in _SILENT_STATES:
        # A slot that never started, or one parked behind speech. The
        # controller's own start deadline reports these; nora only needs to
        # avoid believing they are audible.
        if previous is not None and previous.status == status:
            return PlaybackVerdict.WEDGED
        return PlaybackVerdict.OK
    if status != "playing":
        return PlaybackVerdict.UNKNOWN  # a state this rule does not know
    if previous is None or previous.position_ms is None or current.position_ms is None:
        return PlaybackVerdict.OK  # no delta available yet — not evidence
    if current.position_ms > previous.position_ms:
        return PlaybackVerdict.OK  # frames were consumed: audio moved
    return PlaybackVerdict.WEDGED
