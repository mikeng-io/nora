"""Intention classes and the arithmetic of the operator's learning reads."""

from dataclasses import dataclass
from typing import Generic, Literal, TypeAlias, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IntentionClass:
    form: Literal["achieve", "maintain"]
    owed_to: str
    condition_form: str | None


@dataclass(frozen=True, slots=True)
class Efficacy:
    resting_on_lesson: float
    resting_on_none: float


@dataclass(frozen=True, slots=True)
class TrackRecord:
    achieved: int
    dropped: int
    leaked: int
    unknown: int

    @property
    def achieved_rate(self) -> float | None:
        return share(self.achieved, self.achieved + self.dropped + self.leaked)


@dataclass(frozen=True, slots=True)
class LearningValue(Generic[T]):
    value: T
    status: Literal["ok"] = "ok"


@dataclass(frozen=True, slots=True)
class LearningMissing:
    status: Literal["nothing_found", "unavailable"]


LearningResult: TypeAlias = LearningValue[T] | LearningMissing


def share(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def efficacy(
    achieved_with: int, known_with: int, achieved_without: int, known_without: int,
) -> Efficacy | None:
    with_lesson = share(achieved_with, known_with)
    without_lesson = share(achieved_without, known_without)
    if with_lesson is None or without_lesson is None:
        return None
    return Efficacy(with_lesson, without_lesson)


def track_record(achieved: int, dropped: int, leaked: int, unknown: int) -> TrackRecord | None:
    if not (achieved + dropped + leaked):
        return None
    return TrackRecord(achieved, dropped, leaked, unknown)
