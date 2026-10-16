"""`InMemoryDreamScheduleClock` — the double for
`nora.infra.sql.dream_schedule.SqlDreamScheduleClock`, bound to it by
`tests/contract/test_dream_schedule_clock_contract.py`.

**The irony is deliberate.** The SQL store exists because a clock kept in a
process-local dict is lost on restart; this class is that dict, on purpose, for
tests that must not need Postgres. It is a legitimate double
precisely because it is never wired into `composition.py` — the architecture
test that keeps `nora.testing` out of the production graph is what makes the
distinction enforceable rather than a naming convention.

**It keys on `(adaptor_name, key)` as a tuple, not on a joined string.** The
SQL table has a two-column primary key; a fake keyed on `f"{name}:{key}"` would
merge `("a:b", "c")` with `("a", "b:c")` where Postgres keeps them apart, and
every adaptor name and unit key in this repo is operator-supplied text
that may contain a colon (`100000000000000201/100000000000000202` is a live
scope id). A fake more permissive than the real store passes tests the real
store fails.
"""

from __future__ import annotations


class InMemoryDreamScheduleClock:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], int] = {}

    async def last_fired(self, *, adaptor_name: str, key: str) -> int | None:
        # `.get` returns None for an absent pair, which is the contract: a
        # never-fired clock is not a clock that fired at epoch 0.
        return self._rows.get((adaptor_name, key))

    async def record_fire(
        self, *, adaptor_name: str, key: str, at_ms: int
    ) -> None:
        self._rows[(adaptor_name, key)] = at_ms
