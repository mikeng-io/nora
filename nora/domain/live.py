"""Read-only projections of a live source, evaluated at use time."""

from collections.abc import (
    Callable,
    ItemsView,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
    Set,
    ValuesView,
)
from typing import TypeVar, overload

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")
_S = TypeVar("_S")


class LiveMapping(Mapping[K, V]):
    def __init__(self, read: Callable[[], Mapping[K, V]]) -> None:
        self._read = read

    def __getitem__(self, key: K) -> V:
        return self._read()[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._read())

    def __len__(self) -> int:
        return len(self._read())

    def items(self) -> ItemsView[K, V]:
        return self._read().items()

    def values(self) -> ValuesView[V]:
        return self._read().values()


class LiveSequence(Sequence[T]):
    def __init__(self, read: Callable[[], Sequence[T]]) -> None:
        self._read = read

    @overload
    def __getitem__(self, key: int) -> T: ...
    @overload
    def __getitem__(self, key: slice) -> Sequence[T]: ...
    def __getitem__(self, key: int | slice) -> T | Sequence[T]:
        return self._read()[key]

    def __iter__(self) -> Iterator[T]:
        return iter(self._read())

    def __len__(self) -> int:
        return len(self._read())


class LiveSet(Set[T]):
    def __init__(self, read: Callable[[], Set[T]]) -> None:
        self._read = read

    @classmethod
    def _from_iterable(cls, iterable: Iterable[_S]) -> "LiveSet[_S]":
        """`Set`'s algebra (`-`, `&`, `|`, `^`) builds its result as
        `cls(iterable)` — `cls` takes a READER, not an iterable, so the
        materialized set is wrapped in one. Without this the result's `_read`
        is the generator itself and its first use raises 'generator' object is
        not callable."""
        data = frozenset(iterable)
        return LiveSet(lambda: data)

    def __contains__(self, value: object) -> bool:
        return value in self._read()

    def __iter__(self) -> Iterator[T]:
        return iter(self._read())

    def __len__(self) -> int:
        return len(self._read())
