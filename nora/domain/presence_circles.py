"""Fresh provider circle observations, independent of identity or permission grants."""

from dataclasses import dataclass
from typing import Protocol


class CircleUnavailableError(RuntimeError):
    """Current provider membership could not be established."""


@dataclass(frozen=True)
class CircleObservation:
    circle_id: str
    observed_ms: int
    member_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.circle_id, str) or not self.circle_id.strip()
                or type(self.observed_ms) is not int or self.observed_ms <= 0
                or not self.member_ids
                or any(not isinstance(i, str) or not i.strip() for i in self.member_ids)
                or len(set(self.member_ids)) != len(self.member_ids)):
            raise CircleUnavailableError("invalid circle observation")


class CircleObservations(Protocol):
    async def read(self) -> tuple[CircleObservation, ...]:
        """All selected fresh circles; missing, stale or failed reads raise."""
        ...


def checked_observations(
    rows: tuple[CircleObservation, ...], *, expected: tuple[str, ...], now_ms: int, ttl_ms: int,
) -> tuple[CircleObservation, ...]:
    ids = tuple(row.circle_id for row in rows)
    if (not expected or len(ids) != len(set(ids)) or set(ids) != set(expected)
            or any(not 0 <= now_ms - row.observed_ms <= ttl_ms for row in rows)):
        raise CircleUnavailableError("current circle inventory is unavailable")
    return rows
