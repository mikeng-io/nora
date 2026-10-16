"""`nora.subject.sandbox`'s shared-eval-database guard — OFFLINE, no Postgres.

A run from a branch whose alembic head is AHEAD of the shared
`nora_eval_blank` template must not upgrade it (`_blank_template` calling
`run_migrations` unconditionally would): every OTHER session's run would then
fail at seeding, from an older head, with `Can't locate revision '…'`.

These tests pin the guard with a FAKE async engine (`_FakeEngine`
below) standing in for Postgres, and a mocked `run_migrations`/
`current_revision`/`head_revision` standing in for Alembic: no Docker, no
real database, and no real migration is ever run. What is asserted is the
SHAPE of the calls — which database name `run_migrations` is handed, and
whether the fake engine ever executes DDL against the shared name — not
their effect, which `tests/integration/test_shared_eval_database_survives_a_
branch_ahead_run.py` covers against a real Postgres.

`nora.subject.sandbox._migrated`/`_private_blanks` are process-lifetime
caches (module docstring: "once per process per source"), so every test
resets them — otherwise test order would decide which branch of
`_blank_template` a given test actually exercises.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nora.infra.sql.migrations_runner import revision_is_known
from nora.subject import sandbox

SOURCE_URL = "postgresql+asyncpg://nora:pw@127.0.0.1:5435/nora_eval"
BLANK_URL = "postgresql+asyncpg://nora:pw@127.0.0.1:5435/nora_eval_blank"


@pytest.fixture(autouse=True)
def _reset_module_state():
    sandbox._migrated.clear()
    sandbox._private_blanks.clear()
    yield
    sandbox._migrated.clear()
    sandbox._private_blanks.clear()


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeConn:
    def __init__(self, engine: _FakeEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, stmt: object, params: dict | None = None) -> _FakeResult:
        sql = str(stmt)
        self._engine.calls.append((sql, dict(params or {})))
        if "pg_database" in sql:
            name = (params or {}).get("n")
            return _FakeResult(name in self._engine.existing)
        if sql.strip().upper().startswith('CREATE DATABASE'):
            # `CREATE DATABASE "x"` or `CREATE DATABASE "x" TEMPLATE "y"` —
            # record the new name as existing, so a later `_exists`-shaped
            # check in the SAME test would see it (none currently does, but
            # this keeps the fake honest rather than a rubber stamp).
            self._engine.existing.add(_created_name(sql))
        return _FakeResult(None)


def _created_name(sql: str) -> str:
    # `CREATE DATABASE "name"` or `... "name" TEMPLATE "other"` — the first
    # quoted token is always the one being created.
    return sql.split('"')[1]


class _FakeEngine:
    """One shared `calls`/`existing` per test, across every
    `create_async_engine(...)` call `_blank_template`/
    `close_private_blank_templates` makes — mirroring the real functions,
    which open and dispose a fresh admin engine per statement group rather
    than holding one open."""

    def __init__(self, calls: list[tuple[str, dict]], existing: set[str]) -> None:
        self.calls = calls
        self.existing = existing

    def connect(self) -> _FakeConn:
        return _FakeConn(self)

    async def dispose(self) -> None:
        return None


def _fake_create_async_engine_factory(existing: set[str]):
    calls: list[tuple[str, dict]] = []

    def factory(url: str, **kwargs: object) -> _FakeEngine:
        return _FakeEngine(calls, existing)

    return factory, calls


def _ddl_targets(calls: list[tuple[str, dict]]) -> list[str]:
    """Every database name a `CREATE DATABASE`/`DROP DATABASE` statement in
    `calls` named — the thing a branch-ahead run must never do to the
    SHARED template."""
    targets = []
    for sql, _params in calls:
        upper = sql.strip().upper()
        if upper.startswith("CREATE DATABASE") or upper.startswith("DROP DATABASE"):
            targets.append(_created_name(sql))
    return targets


# --------------------------------------------------------------------------
# `_blank_template`
# --------------------------------------------------------------------------


async def test_blank_template_first_creation_migrates_the_shared_name(monkeypatch):
    """Nothing depends on the template yet, so establishing it IS migrating
    the shared name — the one intentional exception."""
    factory, calls = _fake_create_async_engine_factory(existing=set())
    monkeypatch.setattr(sandbox, "create_async_engine", factory)
    migrated: list[str] = []
    monkeypatch.setattr(sandbox, "run_migrations", migrated.append)
    # Never reached on the "doesn't exist" branch, but must not blow up if
    # it were — there is no current revision to compare against yet.
    monkeypatch.setattr(sandbox, "current_revision", lambda url: None)
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0064_x")

    name = await sandbox._blank_template(SOURCE_URL)

    assert name == "nora_eval_blank"
    assert migrated == [BLANK_URL]
    assert "nora_eval_blank" in _ddl_targets(calls)


async def test_blank_template_reuses_shared_when_already_at_local_head(monkeypatch):
    """Current == this process's head: reused AS IS, no `run_migrations`
    call at all — not even the old idempotent no-op."""
    factory, calls = _fake_create_async_engine_factory(existing={"nora_eval_blank"})
    monkeypatch.setattr(sandbox, "create_async_engine", factory)
    migrated: list[str] = []
    monkeypatch.setattr(sandbox, "run_migrations", migrated.append)
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0064_x")
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0064_x")

    name = await sandbox._blank_template(SOURCE_URL)

    assert name == "nora_eval_blank"
    assert migrated == []
    assert _ddl_targets(calls) == []  # no CREATE, no DROP — untouched


@pytest.mark.parametrize(
    "current, local_head",
    [
        ("0064_x", "0065_standing_working_set"),  # branch AHEAD of the template
        ("0065_standing_working_set", "0064_x"),  # branch BEHIND the template
    ],
)
async def test_blank_template_never_migrates_the_shared_name_when_diverged(
    monkeypatch, current, local_head,
):
    """THE HAZARD, pinned in both directions. A branch whose head differs
    from the shared template's — ahead or behind — must build and migrate a PRIVATE copy,
    and `run_migrations` must never be called with the shared blank's URL."""
    factory, calls = _fake_create_async_engine_factory(existing={"nora_eval_blank"})
    monkeypatch.setattr(sandbox, "create_async_engine", factory)
    migrated: list[str] = []
    monkeypatch.setattr(sandbox, "run_migrations", migrated.append)
    monkeypatch.setattr(sandbox, "current_revision", lambda url: current)
    monkeypatch.setattr(sandbox, "head_revision", lambda: local_head)

    name = await sandbox._blank_template(SOURCE_URL)

    assert name != "nora_eval_blank"
    assert name.startswith("nora_eval_blank_run_")
    # The ONLY `run_migrations` call names the PRIVATE database.
    assert migrated == [sandbox._sibling(SOURCE_URL, name)]
    assert BLANK_URL not in migrated
    # DDL touched the private name (created FROM the shared one) and never
    # dropped or recreated the shared name itself.
    targets = _ddl_targets(calls)
    assert name in targets
    assert "nora_eval_blank" not in targets


async def test_blank_template_caches_the_private_copy_within_one_process(monkeypatch):
    """A second case in the SAME run must reuse the one private copy —
    never re-create or re-migrate it — the cost `_blank_template`'s own
    docstring says is worth paying once per run, not once per case."""
    factory, calls = _fake_create_async_engine_factory(existing={"nora_eval_blank"})
    monkeypatch.setattr(sandbox, "create_async_engine", factory)
    migrated: list[str] = []
    monkeypatch.setattr(sandbox, "run_migrations", migrated.append)
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0064_x")
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0065_y")

    first = await sandbox._blank_template(SOURCE_URL)
    calls_after_first = len(calls)
    migrated_after_first = list(migrated)
    second = await sandbox._blank_template(SOURCE_URL)

    assert second == first
    assert len(calls) == calls_after_first  # no new CREATE/terminate calls
    assert migrated == migrated_after_first  # no new `run_migrations` call


async def test_close_private_blank_templates_drops_only_the_private_name(monkeypatch):
    factory, calls = _fake_create_async_engine_factory(existing={"nora_eval_blank"})
    monkeypatch.setattr(sandbox, "create_async_engine", factory)
    monkeypatch.setattr(sandbox, "run_migrations", lambda url: None)
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0064_x")
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0065_y")

    private_name = await sandbox._blank_template(SOURCE_URL)
    assert sandbox._private_blanks == {SOURCE_URL: private_name}

    await sandbox.close_private_blank_templates()

    assert sandbox._private_blanks == {}
    drop_calls = [
        (sql, params) for sql, params in calls
        if sql.strip().upper().startswith("DROP DATABASE")
    ]
    assert len(drop_calls) == 1
    assert f'"{private_name}"' in drop_calls[0][0]
    assert '"nora_eval_blank"' not in drop_calls[0][0]  # the SHARED name, exact match


async def test_close_private_blank_templates_is_a_noop_when_nothing_diverged(monkeypatch):
    calls_made = []
    monkeypatch.setattr(
        sandbox, "create_async_engine",
        lambda *a, **k: calls_made.append((a, k)) or _FakeEngine([], set()),
    )

    await sandbox.close_private_blank_templates()  # nothing was ever recorded

    assert calls_made == []


# --------------------------------------------------------------------------
# `shared_database_versions` / `assert_shared_databases_unchanged`
# --------------------------------------------------------------------------


def test_shared_database_versions_is_a_pure_read(monkeypatch):
    """No engine is created at all — every value comes from
    `current_revision`/`_exists`, which is what makes this call safe to run
    twice (start and end of a run) with no side effect of its own."""
    monkeypatch.setattr(sandbox, "create_async_engine", None)  # would blow up if touched
    revisions = {SOURCE_URL: "0064_x", BLANK_URL: "0064_x"}
    monkeypatch.setattr(sandbox, "current_revision", lambda url: revisions[url])
    monkeypatch.setattr(sandbox, "_exists", lambda url: url in revisions)

    versions = sandbox.shared_database_versions(SOURCE_URL)

    assert versions == sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev="0064_x")


def test_shared_database_versions_reads_none_for_a_template_that_does_not_exist(monkeypatch):
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0064_x")
    monkeypatch.setattr(sandbox, "_exists", lambda url: False)

    versions = sandbox.shared_database_versions(SOURCE_URL)

    assert versions.blank_rev is None


def test_assert_shared_databases_unchanged_passes_when_stable(monkeypatch):
    before = sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev="0064_x")
    monkeypatch.setattr(sandbox, "shared_database_versions", lambda url: before)

    sandbox.assert_shared_databases_unchanged(SOURCE_URL, before)  # no raise


def test_assert_shared_databases_unchanged_allows_the_blank_bootstrap(monkeypatch):
    """`blank_rev` moving from `None` to a real value is `_blank_template`
    establishing the baseline the first time — not a mutation."""
    before = sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev=None)
    after = sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev="0064_x")
    monkeypatch.setattr(sandbox, "shared_database_versions", lambda url: after)

    sandbox.assert_shared_databases_unchanged(SOURCE_URL, before)  # no raise


def test_assert_shared_databases_unchanged_errors_loudly_when_source_moved(monkeypatch):
    before = sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev="0064_x")
    after = sandbox.SharedDatabaseVersions(source_rev="0065_y", blank_rev="0064_x")
    monkeypatch.setattr(sandbox, "shared_database_versions", lambda url: after)

    with pytest.raises(sandbox.SharedDatabaseMutatedError, match="source moved"):
        sandbox.assert_shared_databases_unchanged(SOURCE_URL, before)


def test_assert_shared_databases_unchanged_errors_loudly_when_established_template_moved(
    monkeypatch,
):
    before = sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev="0064_x")
    after = sandbox.SharedDatabaseVersions(source_rev="0064_x", blank_rev="0065_y")
    monkeypatch.setattr(sandbox, "shared_database_versions", lambda url: after)

    with pytest.raises(sandbox.SharedDatabaseMutatedError, match="template moved"):
        sandbox.assert_shared_databases_unchanged(SOURCE_URL, before)


# --------------------------------------------------------------------------
# `refuse_if_source_diverges_from_head`
# --------------------------------------------------------------------------


def test_refuse_if_source_diverges_from_head_passes_when_equal(monkeypatch):
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0064_x")
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0064_x")
    monkeypatch.setattr(
        sandbox, "revision_is_known",
        lambda rev: (_ for _ in ()).throw(AssertionError("equal short-circuits before this")),
    )

    sandbox.refuse_if_source_diverges_from_head(SOURCE_URL)  # no raise


def test_refuse_if_source_diverges_from_head_allows_a_source_behind_head(monkeypatch):
    """Source at `0063_note_merge_decision`, head at `0064_proactive_open_note`
    (an index-only migration) must not refuse an ordinary master run: the rule is
    direction-aware. A source behind head — a revision THIS checkout's own chain
    recognizes, i.e. an ANCESTOR of head — must be allowed to run; `snapshot()`
    is the fail-closed net for the rare migration that actually matters."""
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0063_note_merge_decision")
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0064_proactive_open_note")
    monkeypatch.setattr(
        sandbox, "revision_is_known", lambda rev: rev == "0063_note_merge_decision",
    )

    sandbox.refuse_if_source_diverges_from_head(SOURCE_URL)  # no raise


def test_refuse_if_source_diverges_from_head_refuses_when_source_is_ahead_of_head(monkeypatch):
    """The source names a revision this checkout has never heard of —
    `0065_standing_working_set` — the ONE
    direction that is unsafe: a read could hit a column only that migration
    added."""
    monkeypatch.setattr(sandbox, "current_revision", lambda url: "0065_standing_working_set")
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0064_proactive_open_note")
    monkeypatch.setattr(sandbox, "revision_is_known", lambda rev: False)

    with pytest.raises(
        sandbox.SourceRevisionMismatchError,
        match=r"source at '0065_standing_working_set', branch at '0064_proactive_open_note'",
    ) as excinfo:
        sandbox.refuse_if_source_diverges_from_head(SOURCE_URL)
    assert "make eval-source" in str(excinfo.value)
    assert "read-only" in str(excinfo.value)


def test_refuse_if_source_diverges_from_head_refuses_when_source_has_no_revision(monkeypatch):
    """`current_revision` reads `None` — an unmigrated or unreadable
    source. Nothing to call "behind" about a database with no history, and
    `revision_is_known(None)` is never even asked."""
    monkeypatch.setattr(sandbox, "current_revision", lambda url: None)
    monkeypatch.setattr(sandbox, "head_revision", lambda: "0064_x")
    monkeypatch.setattr(
        sandbox, "revision_is_known",
        lambda rev: (_ for _ in ()).throw(AssertionError("None must short-circuit before this")),
    )

    with pytest.raises(sandbox.SourceRevisionMismatchError, match=r"source at None"):
        sandbox.refuse_if_source_diverges_from_head(SOURCE_URL)


# --------------------------------------------------------------------------
# `revision_is_known` — the REAL function, against the REAL script
# directory (no Postgres either way: it never opens a connection).
# --------------------------------------------------------------------------


def test_revision_is_known_is_true_for_a_real_ancestor_revision():
    # The very first migration — always an ancestor of head, whatever head
    # currently is.
    assert revision_is_known("0001_pg_baseline") is True


def test_revision_is_known_is_false_for_an_unknown_revision():
    assert revision_is_known("totally_bogus_revision_id") is False


# --------------------------------------------------------------------------
# `sweep_stale_private_blank_templates` — the safe-leak-cleanup selection
# logic (SIGTERM/kill can skip `close_private_blank_templates`'s own
# `finally`, and `_run_`-suffixed private copies from a killed run must not
# accumulate forever).
# --------------------------------------------------------------------------


class _SweepResult:
    def __init__(self, *, rows: list[str] | None = None, scalar_value: object = None) -> None:
        self._rows = rows
        self._scalar_value = scalar_value

    def scalars(self) -> _SweepResult:
        return self

    def all(self) -> list[str]:
        return list(self._rows or [])

    def scalar(self) -> object:
        return self._scalar_value


class _SweepConn:
    def __init__(self, engine: _SweepEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _SweepConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, stmt: object, params: dict | None = None) -> _SweepResult:
        sql = str(stmt)
        self._engine.calls.append((sql, dict(params or {})))
        if "SELECT datname FROM pg_database" in sql:
            return _SweepResult(rows=self._engine.candidates)
        if "pg_stat_activity" in sql:
            name = (params or {}).get("n")
            return _SweepResult(scalar_value=name in self._engine.active)
        if sql.strip().upper().startswith("DROP DATABASE"):
            return _SweepResult()
        raise AssertionError(f"unexpected SQL in the sweep fake: {sql}")


class _SweepEngine:
    def __init__(self, candidates: list[str], active: set[str]) -> None:
        self.candidates = candidates
        self.active = active
        self.calls: list[tuple[str, dict]] = []

    def connect(self) -> _SweepConn:
        return _SweepConn(self)

    async def dispose(self) -> None:
        return None


def _stamped(name_suffix: str, *, at: datetime) -> str:
    return f"nora_eval_blank_run_{at:%Y%m%dT%H%M%S}-{name_suffix}"


async def test_sweep_drops_only_old_databases_with_no_active_connection(monkeypatch):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    old_idle = _stamped("aaaa", at=now - timedelta(hours=7))
    old_active = _stamped("bbbb", at=now - timedelta(hours=7))
    young_idle = _stamped("cccc", at=now - timedelta(hours=1))
    unparseable = "nora_eval_blank_run_not-a-timestamp"

    engine = _SweepEngine(
        candidates=[old_idle, old_active, young_idle, unparseable],
        active={old_active},
    )
    monkeypatch.setattr(sandbox, "create_async_engine", lambda *a, **k: engine)

    swept = await sandbox.sweep_stale_private_blank_templates(SOURCE_URL, now=now)

    assert swept == (old_idle,)
    drop_calls = [c for c in engine.calls if c[0].strip().upper().startswith("DROP DATABASE")]
    assert len(drop_calls) == 1
    assert f'"{old_idle}"' in drop_calls[0][0]


async def test_sweep_never_drops_a_database_with_a_live_connection_even_if_old(monkeypatch):
    """The safety this whole function exists for: a concurrent session's
    genuinely in-use copy is never dropped, however old it reads."""
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    ancient_but_active = _stamped("live", at=now - timedelta(days=3))
    engine = _SweepEngine(candidates=[ancient_but_active], active={ancient_but_active})
    monkeypatch.setattr(sandbox, "create_async_engine", lambda *a, **k: engine)

    swept = await sandbox.sweep_stale_private_blank_templates(SOURCE_URL, now=now)

    assert swept == ()
    assert not any(c[0].strip().upper().startswith("DROP DATABASE") for c in engine.calls)


async def test_sweep_never_drops_a_database_too_young_even_if_idle(monkeypatch):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    young_idle = _stamped("cccc", at=now - timedelta(minutes=1))
    engine = _SweepEngine(candidates=[young_idle], active=set())
    monkeypatch.setattr(sandbox, "create_async_engine", lambda *a, **k: engine)

    swept = await sandbox.sweep_stale_private_blank_templates(SOURCE_URL, now=now)

    assert swept == ()


async def test_sweep_is_a_noop_with_no_candidates(monkeypatch):
    engine = _SweepEngine(candidates=[], active=set())
    monkeypatch.setattr(sandbox, "create_async_engine", lambda *a, **k: engine)

    swept = await sandbox.sweep_stale_private_blank_templates(SOURCE_URL)

    assert swept == ()
    assert not any(c[0].strip().upper().startswith("DROP DATABASE") for c in engine.calls)


def test_private_blank_created_at_parses_new_run_ids_own_shape():
    name = "nora_eval_blank_run_20260925T120000-a1b2"
    assert sandbox._private_blank_created_at(name) == datetime(
        2026, 9, 25, 12, 0, 0, tzinfo=UTC,
    )


def test_private_blank_created_at_is_none_for_a_name_it_does_not_recognize():
    assert sandbox._private_blank_created_at("nora_eval_blank_run_not-a-timestamp") is None
    assert sandbox._private_blank_created_at("nora_eval_blank") is None
