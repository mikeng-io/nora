"""Current, scoped circle names and read-time authority for location tools."""

from collections.abc import Sequence
from contextvars import ContextVar
from functools import wraps
from typing import Any

from nora.agent.toolkit.results import _record
from nora.agent.whereis import Circle
from nora.domain.presence_circles import CircleUnavailableError


class _ResultMetrics:
    def __init__(self, reader: "ScopedCircles", metrics: Any) -> None:
        self._reader, self._metrics = reader, metrics

    def __getattr__(self, name: str) -> Any:
        return getattr(self._metrics, name)


class ScopedCircles:
    def __init__(self, room: Any, legacy: Sequence[Circle]) -> None:
        self._room, self._legacy = room, legacy
        self._tool: ContextVar[str | None] = ContextVar("location_tool_result", default=None)
        self._reads: ContextVar[list[tuple[Circle, ...]] | None] = ContextVar(
            "location_read_snapshots", default=None)

    @property
    def managed(self) -> bool:
        access = getattr(self._room, "access", None)
        return access is not None and not access.is_file_backend

    async def _load(self) -> tuple[Circle, ...]:
        if not self.managed:
            return tuple(self._legacy)
        access = getattr(self._room, "access", None)
        if access is None:
            raise CircleUnavailableError("selected identity source is unavailable")
        scope = self._room.room_scope
        groups: dict[str, dict[str, Any]] = {}
        for row, ref in await access.life360_refs(scope=scope):
            if row.circle_id is None:
                raise CircleUnavailableError("current circle is unavailable")
            groups.setdefault(row.circle_id, {})[ref.person_id] = ref
        return tuple(Circle(cid, frozenset(people), tuple(people[pid] for pid in sorted(people)))
                     for cid, people in sorted(groups.items()))

    async def read(self, *, track: bool = True) -> tuple[Circle, ...]:
        reads = self._reads.get()
        if reads:
            return reads[0]
        try:
            rows = await self._load()
        except Exception:  # noqa: BLE001 — no identifiers or transport details in tool errors
            raise CircleUnavailableError("current circle source is unavailable") from None
        if track:
            self.retain(rows)
        return rows

    def retain(self, rows: tuple[Circle, ...]) -> None:
        reads = self._reads.get()
        if reads is not None:
            reads.append(rows)

    def metrics(self, metrics: Any) -> Any:
        return _ResultMetrics(self, metrics) if self.managed else metrics

    def guarded(self, function: Any, name: str, metrics: Any) -> Any:
        if not self.managed:
            return function

        @wraps(function)
        async def call(*args: Any, **kwargs: Any) -> Any:
            snapshots: list[tuple[Circle, ...]] = []
            token = self._reads.set(snapshots)
            result_token = self._tool.set(name)
            try:
                result = await function(*args, **kwargs)
                if snapshots:
                    current = await self._load()
                    if any(snapshot != current for snapshot in snapshots):
                        raise CircleUnavailableError("location authority changed")
                _record(metrics, name, str(result.get("status", "unavailable")))
                return result
            except Exception:  # noqa: BLE001 — errors never turn into location absence
                _record(metrics, name, "unavailable")
                return {"status": "unavailable",
                        "note": "I can't verify current location access. Say you couldn't check."}
            finally:
                self._reads.reset(token)
                self._tool.reset(result_token)

        return call


def reader_for(deps: Any) -> ScopedCircles:
    reader = getattr(deps, "circle_reader", None)
    return reader if reader is not None else ScopedCircles(deps.room, deps.presence_circles)


def record_result(metrics: Any, tool: str, status: str, **detail: object) -> None:
    if isinstance(metrics, _ResultMetrics) and metrics._reader._tool.get() == tool:
        return
    _record(metrics, tool, status, **detail)
