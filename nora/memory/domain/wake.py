"""Wake admission policy and the claimed input to a reflection pass."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypeAlias

from nora.domain.graph_stimuli import GraphStimulus

WakeReason = Literal["settled", "strong_cue", "batch", "max_age", "clock"]
WakeBrake = Literal["single_flight", "concurrency", "cooldown", "backoff", "empty", "waiting"]
WakeFailure = Literal["error", "cancelled", "timeout", "bound", "room_busy"]


@dataclass(frozen=True, slots=True)
class WakeClosed:
    state: Literal["closed"] = "closed"


@dataclass(frozen=True, slots=True)
class WakeFailed:
    cause: WakeFailure
    state: Literal["failed"] = "failed"


WakeOutcome: TypeAlias = WakeClosed | WakeFailed


@dataclass(frozen=True, slots=True)
class WakeGroup:
    unit: str
    room: str | None


@dataclass(frozen=True, slots=True)
class WakeClaim:
    group: WakeGroup
    reading_room: str
    stimuli: tuple[GraphStimulus, ...]
    claimed: datetime
    reason: WakeReason

    @property
    def pass_id(self) -> str:
        keys = sorted((s.kind, s.graph_id, s.occurrence) for s in self.stimuli)
        instant = self.claimed.astimezone(UTC).isoformat()
        identity = (self.group.unit, self.group.room, keys, instant)
        return f"wake:{hashlib.sha256(json.dumps(identity).encode()).hexdigest()}"


class WakePass(Protocol):
    async def run(self, claimed: WakeClaim) -> WakeOutcome: ...


@dataclass(frozen=True, slots=True)
class WakePolicy:
    quiet_seconds: float
    cue_quiet_seconds: float
    batch_max: int
    max_age_seconds: float
    cooldown_seconds: float
    max_concurrent_sessions: int
    backoff_max_seconds: float


@dataclass(frozen=True, slots=True)
class WakeHistory:
    unit_in_flight: bool = False
    in_flight: int = 0
    latest_claim: datetime | None = None
    latest_closed_claim: datetime | None = None
    latest_settled: datetime | None = None


@dataclass(frozen=True, slots=True)
class WakeDecision:
    reason: WakeReason | None = None
    brake: WakeBrake | None = None


def decide_wake(
    eligible: Sequence[GraphStimulus], history: WakeHistory, now: datetime,
    policy: WakePolicy, *, attempts: int,
) -> WakeDecision:
    eligible = tuple(s for s in eligible if s.kind != "aggregate_crossing")
    if not eligible:
        return WakeDecision(brake="empty")
    if history.unit_in_flight:
        return WakeDecision(brake="single_flight")
    if history.in_flight >= policy.max_concurrent_sessions:
        return WakeDecision(brake="concurrency")
    ages = tuple((now - stimulus.eligible_at).total_seconds() for stimulus in eligible)
    cue = any(s.kind == "strong_cue" and age > policy.cue_quiet_seconds
              for s, age in zip(eligible, ages, strict=True))
    if history.latest_settled is not None:
        elapsed = now - history.latest_settled
        can_cut = cue and history.latest_closed_claim is not None and (
            history.latest_closed_claim == history.latest_claim
        )
        if elapsed < timedelta(seconds=policy.cooldown_seconds) and not can_cut:
            return WakeDecision(brake="cooldown")
        if attempts:
            backoff = min(policy.backoff_max_seconds, policy.cooldown_seconds * 2 ** (attempts - 1))
            if elapsed.total_seconds() < backoff:
                return WakeDecision(brake="backoff")
    if cue:
        return WakeDecision(reason="strong_cue")
    if min(ages) > policy.quiet_seconds:
        return WakeDecision(reason="settled")
    if len(eligible) >= policy.batch_max:
        return WakeDecision(reason="batch")
    if max(ages) > policy.max_age_seconds:
        return WakeDecision(reason="max_age")
    if any(s.kind in ("review_time", "valid_to", "deadline") for s in eligible):
        return WakeDecision(reason="clock")
    return WakeDecision(brake="waiting")
