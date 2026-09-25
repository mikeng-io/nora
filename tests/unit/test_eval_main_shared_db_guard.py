"""`python -m evals`'s wiring of the shared-eval-database guard — no DB, no
network: every collaborator `main()` calls that could touch Postgres or
Honcho is replaced, and what is pinned is the ORDER and the ARGUMENTS, not
their effect (`tests/unit/test_sandbox_shared_database_guard.py` covers the
guard functions themselves; `tests/integration/
test_shared_eval_database_survives_a_branch_ahead_run.py` covers a real
Postgres).

THE HAZARD (2026-09-25): a run from a branch ahead of the shared
`nora_eval_blank` upgraded it, and every other session's run then failed
seeding from an older head. The fix has three parts, and each has its own
test here: refuse before any case if the READ-ONLY source itself diverges
from this branch's head; capture both shared databases' versions before
anything runs; and re-check them — loudly, never swallowed into a return
code — after everything does, success or not.
"""

from __future__ import annotations

import pytest

import evals.__main__ as m


def _set_argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["python -m evals", *args])


def test_refuses_before_any_case_when_the_source_diverges_from_branch_head(
    monkeypatch, capsys,
):
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "postgresql+asyncpg://u:p@h/nora_eval")

    def _refuse(url: str) -> None:
        raise m.SourceRevisionMismatchError(
            "source at '0064_x', branch at '0065_standing_working_set': "
            "refresh the source or run on a branch at the source's head"
        )

    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", _refuse)
    # Nothing past the refusal may run: each of these blows up if reached.
    monkeypatch.setattr(
        m, "shared_database_versions",
        lambda url: (_ for _ in ()).throw(AssertionError("reached past the refusal")),
    )
    monkeypatch.setattr(
        m, "materialise",
        lambda **k: (_ for _ in ()).throw(AssertionError("reached past the refusal")),
    )

    _set_argv(monkeypatch, "no_trigger")
    rc = m.main()

    assert rc == 2
    err = capsys.readouterr().err
    assert "refusing to run" in err
    assert "source at '0064_x', branch at '0065_standing_working_set'" in err


def test_a_stable_run_captures_the_baseline_before_materialise_and_checks_it_last(
    monkeypatch,
):
    """Pins the ORDER: refuse -> capture the baseline -> (config rebuild,
    sweep, the corpus itself) -> drop any private template -> assert the
    SAME baseline object is what gets checked at the end."""
    order: list[object] = []
    sentinel_before = object()

    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(
        m, "refuse_if_source_diverges_from_head", lambda url: order.append("refuse"),
    )

    def _versions(url: str) -> object:
        order.append("baseline")
        return sentinel_before

    monkeypatch.setattr(m, "shared_database_versions", _versions)
    monkeypatch.setattr(
        m, "materialise", lambda **k: order.append("materialise") or "config.evaluation.toml",
    )
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: order.append("sweep"))
    monkeypatch.setattr(m, "_run_one", lambda *a, **k: order.append("run") or True)

    async def _close() -> None:
        order.append("close")

    monkeypatch.setattr(m, "close_private_blank_templates", _close)

    def _assert_unchanged(url: str, before: object) -> None:
        order.append(("assert", before is sentinel_before))

    monkeypatch.setattr(m, "assert_shared_databases_unchanged", _assert_unchanged)

    _set_argv(monkeypatch, "no_trigger")
    rc = m.main()

    assert rc == 0
    assert order == [
        "refuse", "baseline", "materialise", "sweep", "run", "close", ("assert", True),
    ]


def test_the_guard_wraps_print_input_too(monkeypatch):
    """--print-input never runs a corpus, but it seeds the sandbox exactly
    like a case does (`_print_input`'s own docstring: "runs the ONE path —
    sandbox, snapshot, frozen clock") — so it must be guarded identically."""
    order: list[object] = []
    sentinel_before = object()

    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: sentinel_before)
    monkeypatch.setattr(m, "materialise", lambda **k: "config.evaluation.toml")
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)
    monkeypatch.setattr(
        m, "_print_input", lambda *a, **k: order.append("print_input") or 0,
    )

    async def _close() -> None:
        order.append("close")

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    monkeypatch.setattr(
        m, "assert_shared_databases_unchanged",
        lambda url, before: order.append(("assert", before is sentinel_before)),
    )

    _set_argv(monkeypatch, "standing", "--print-input", "some_case")
    rc = m.main()

    assert rc == 0
    assert order == ["print_input", "close", ("assert", True)]


def test_a_shared_database_mutation_is_never_swallowed_into_a_return_code(
    monkeypatch, capsys,
):
    """ERROR is never a PASS: even a run that otherwise SUCCEEDED (every
    case passed) must not report `0` if the guard finds a shared database
    moved. The finding is raised out of `main()`, not folded into `rc`."""
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: "config.evaluation.toml")
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)
    monkeypatch.setattr(m, "_run_one", lambda *a, **k: True)  # every case PASSED

    closed = []

    async def _close() -> None:
        closed.append(True)

    monkeypatch.setattr(m, "close_private_blank_templates", _close)

    def _assert_unchanged(url: str, before: object) -> None:
        raise m.SharedDatabaseMutatedError(
            "this run changed a database every eval session on the host "
            "shares, which must never happen: source moved from 'a' to 'b'"
        )

    monkeypatch.setattr(m, "assert_shared_databases_unchanged", _assert_unchanged)

    _set_argv(monkeypatch, "no_trigger")
    with pytest.raises(m.SharedDatabaseMutatedError):
        m.main()

    assert closed == [True]  # cleanup ran BEFORE the loud raise
    assert "ERROR: this run changed a database" in capsys.readouterr().err


def test_the_guard_still_runs_when_a_case_raises(monkeypatch):
    """A `finally`, not a happy-path-only check: a run that dies mid-corpus
    must still drop its private template and check the shared databases —
    a killed run is exactly the scenario `close_private_blank_templates`
    exists for."""
    order: list[object] = []

    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: "config.evaluation.toml")
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)

    def _blows_up(*a: object, **k: object) -> bool:
        raise RuntimeError("the corpus itself blew up")

    monkeypatch.setattr(m, "_run_one", _blows_up)

    async def _close() -> None:
        order.append("close")

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    monkeypatch.setattr(
        m, "assert_shared_databases_unchanged", lambda url, before: order.append("assert"),
    )

    _set_argv(monkeypatch, "no_trigger")
    with pytest.raises(RuntimeError, match="the corpus itself blew up"):
        m.main()

    assert order == ["close", "assert"]
