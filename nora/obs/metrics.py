"""In-process metrics recording (spec C.9).

M4/M5 record through this interface; M6 swaps the backend for a Prometheus text
exporter without changing a single call site (spec §9, audit fix #11).
"""

from __future__ import annotations

from collections import defaultdict


def _key(name: str, labels: dict) -> tuple:
    return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))


class Metrics:
    def __init__(self) -> None:
        self._values: dict[tuple, float] = defaultdict(float)
        self._observations: dict[tuple, list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        self._values[_key(name, labels)] += value

    def gauge(self, name: str, value: float, **labels) -> None:
        self._values[_key(name, labels)] = value

    def observe(self, name: str, value: float, **labels) -> None:
        self._observations[_key(name, labels)].append(value)

    def get(self, name: str, **labels) -> float:
        return self._values.get(_key(name, labels), 0.0)

    def observations(self, name: str, **labels) -> list[float]:
        return list(self._observations.get(_key(name, labels), []))

    def snapshot(self) -> dict[tuple, float]:
        return dict(self._values)

    def reset(self) -> None:
        self._values.clear()
        self._observations.clear()


METRICS = Metrics()
