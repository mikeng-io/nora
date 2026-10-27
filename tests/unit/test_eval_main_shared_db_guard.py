"""`python -m evals`'s wiring of the shared-eval-database guard — no DB, no
network: every collaborator `main()` calls that could touch Postgres or
Honcho is replaced, and what is pinned is the ORDER and the ARGUMENTS, not
their effect (`tests/unit/test_sandbox_shared_database_guard.py` covers the
guard functions themselves; `tests/integration/
test_shared_eval_database_survives_a_branch_ahead_run.py` covers a real
Postgres).

THE HAZARD: a run from a branch ahead of the shared `nora_eval_blank` upgrades
it, and every other session's run then fails seeding from an older head. The
guard has three parts, and each has its own test here: refuse before any case
if the READ-ONLY source itself diverges from this branch's head; capture both
shared databases' versions before anything runs; and re-check them — loudly,
never swallowed into a return code — after everything does, success or not.
"""

from __future__ import annotations

import signal

import pytest

import evals.__main__ as m


def _set_argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["python -m evals", *args])


def _stub_leaked_blank_sweep(monkeypatch: pytest.MonkeyPatch, order: list | None = None) -> None:
    """Every test below that gets past the refusal reaches
    `_sweep_stale_private_blanks` — never left unmocked, or it would try a
    real `asyncio.run` against a real (nonexistent, in a unit test) Postgres.
    Appends to `order` when given, matching the other stub helpers' shape."""
    if order is None:
        monkeypatch.setattr(m, "_sweep_stale_private_blanks", lambda url: None)
    else:
        monkeypatch.setattr(
            m, "_sweep_stale_private_blanks", lambda url: order.append("sweep_private_blanks"),
        )


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
        m, "_sweep_stale_private_blanks",
        lambda url: (_ for _ in ()).throw(AssertionError("reached past the refusal")),
    )
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
    """Pins the ORDER: refuse -> sweep leaked private blanks -> capture the
    baseline -> (config rebuild, Honcho sweep, the corpus itself) -> drop
    any private template -> assert the SAME baseline object is what gets
    checked at the end."""
    order: list[object] = []
    sentinel_before = object()

    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(
        m, "refuse_if_source_diverges_from_head", lambda url: order.append("refuse"),
    )
    _stub_leaked_blank_sweep(monkeypatch, order)

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
        "refuse", "sweep_private_blanks", "baseline", "materialise", "sweep", "run",
        "close", ("assert", True),
    ]


def test_the_guard_wraps_print_input_too(monkeypatch):
    """--print-input never runs a corpus, but it seeds the sandbox exactly
    like a case does (`_print_input`'s own docstring: "runs the ONE path —
    sandbox, snapshot, frozen clock") — so it must be guarded identically."""
    order: list[object] = []
    sentinel_before = object()

    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    _stub_leaked_blank_sweep(monkeypatch)
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
    _stub_leaked_blank_sweep(monkeypatch)
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
    _stub_leaked_blank_sweep(monkeypatch)
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


def test_a_case_raising_and_a_shared_mutation_both_surface(monkeypatch, capsys):
    """A case that raises AND a mutation the guard catches must BOTH be visible —
    the mutation as the exception that
    propagates (it fires last, inside the `finally`), the case's own error
    CHAINED onto it (`__context__`, Python's own implicit chaining for an
    exception raised while another is already propagating — no `raise ...
    from ...` needed, since nothing here catches the case's error to attach
    it by hand). Neither is swallowed: the mutation is not silently folded
    into the case's own traceback, and the case's error is not discarded
    just because something else was raised over it."""
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    _stub_leaked_blank_sweep(monkeypatch)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: "config.evaluation.toml")
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)

    case_error = RuntimeError("the corpus itself blew up")

    def _blows_up(*a: object, **k: object) -> bool:
        raise case_error

    monkeypatch.setattr(m, "_run_one", _blows_up)

    closed = []

    async def _close() -> None:
        closed.append(True)

    monkeypatch.setattr(m, "close_private_blank_templates", _close)

    def _assert_unchanged(url: str, before: object) -> None:
        raise m.SharedDatabaseMutatedError("shared db moved while the case was also failing")

    monkeypatch.setattr(m, "assert_shared_databases_unchanged", _assert_unchanged)

    _set_argv(monkeypatch, "no_trigger")
    with pytest.raises(m.SharedDatabaseMutatedError) as excinfo:
        m.main()

    # BOTH surfaced: the mutation is what propagated out of `main()`...
    assert "shared db moved while the case was also failing" in str(excinfo.value)
    # ...and the case's own exception is right there, chained, not lost.
    assert excinfo.value.__context__ is case_error
    # Cleanup still ran despite the case having raised first.
    assert closed == [True]
    assert "ERROR: shared db moved while the case was also failing" in capsys.readouterr().err


# --------------------------------------------------------------------------
# SIGTERM: a `kill <pid>` (not `-9`) must still run the `finally` above.
# --------------------------------------------------------------------------


def test_raise_on_sigterm_is_an_ordinary_system_exit_143():
    with pytest.raises(SystemExit) as excinfo:
        m._raise_on_sigterm(signal.SIGTERM, None)
    assert excinfo.value.code == 143


def test_main_installs_the_handler_and_restores_the_previous_one(monkeypatch):
    """No mock of `signal.signal` itself — this installs and restores a
    REAL handler on the real signal table, and proves it by reading
    `signal.getsignal` before and after. Safe: nothing here ever sends the
    process a SIGTERM, so the handler is never actually invoked."""
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    _stub_leaked_blank_sweep(monkeypatch)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: "config.evaluation.toml")
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)

    seen_during_run: list[object] = []

    def _run_one_reads_the_handler(*a: object, **k: object) -> bool:
        seen_during_run.append(signal.getsignal(signal.SIGTERM))
        return True

    monkeypatch.setattr(m, "_run_one", _run_one_reads_the_handler)

    async def _close() -> None:
        return None

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    monkeypatch.setattr(m, "assert_shared_databases_unchanged", lambda url, before: None)

    before = signal.getsignal(signal.SIGTERM)
    _set_argv(monkeypatch, "no_trigger")
    rc = m.main()
    after = signal.getsignal(signal.SIGTERM)

    assert rc == 0
    assert seen_during_run == [m._raise_on_sigterm]  # installed FOR THE RUN
    assert after is before  # and restored once it ends


def test_a_sigterm_mid_run_still_drops_the_private_template_and_checks_the_guard(
    monkeypatch,
):
    """The end-to-end shape: `_run_one` fires the SAME handler `main()`
    installed (simulating the OS delivering SIGTERM mid-case — deterministic
    here, rather than racing a real `os.kill` against interpreter bytecode
    timing), and the `finally` still runs before the process exits."""
    order: list[object] = []

    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    _stub_leaked_blank_sweep(monkeypatch)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: "config.evaluation.toml")
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)

    def _run_one_gets_killed(*a: object, **k: object) -> bool:
        order.append("run")
        m._raise_on_sigterm(signal.SIGTERM, None)
        return True  # never reached

    monkeypatch.setattr(m, "_run_one", _run_one_gets_killed)

    async def _close() -> None:
        order.append("close")

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    monkeypatch.setattr(
        m, "assert_shared_databases_unchanged", lambda url, before: order.append("assert"),
    )

    before = signal.getsignal(signal.SIGTERM)
    _set_argv(monkeypatch, "no_trigger")
    with pytest.raises(SystemExit) as excinfo:
        m.main()

    assert excinfo.value.code == 143
    assert order == ["run", "close", "assert"]
    assert signal.getsignal(signal.SIGTERM) is before  # restored even on this path


def test_context_experiment_requires_a_prefill_receipt_for_each_draw(monkeypatch, capsys):
    from types import SimpleNamespace

    report = SimpleNamespace(cases=[{"name": "generated#1"}, {"name": "generated#2"}])
    experiment = SimpleNamespace(cases=report.cases, run_evaluations=lambda **kwargs: report)
    monkeypatch.setattr(m, "experiment_for", lambda *a, **k: (experiment, None))
    monkeypatch.setattr(m, "census", lambda *a: "synthetic census")
    monkeypatch.setattr(m, "_print_spend", lambda: None)
    monkeypatch.setattr(m, "_print_memory_overhead", lambda: None)
    monkeypatch.setattr(m, "read", lambda *a: SimpleNamespace(passed=2, failed=0, errored=0))
    receipts = [{"case": "generated#1"}]
    assert not m._run_one("tool_selection", source_url="URL", judge=None, sample=1,
                          context_receipts=receipts)
    assert "no measured prefill" in capsys.readouterr().out
    receipts.append({"case": "generated#2"})
    assert m._run_one("tool_selection", source_url="URL", judge=None, sample=1,
                      context_receipts=receipts)


@pytest.mark.parametrize(("deployed", "flag", "graph", "header"), [
    (False, False, False, "memory surface: honcho"),
    (False, True, True, "memory surface: graph (--memory-index"),
    # The live config already runs the graph surface: a run without the flag
    # must measure it, not the Honcho surface's case filter (DEC-316).
    (True, False, True, "memory surface: graph ([memory.index] read = true"),
])
def test_main_resolves_the_memory_surface_from_the_generated_config(
    monkeypatch, capsys, tmp_path, deployed, flag, graph, header,
):
    generated = tmp_path / "config.evaluation.toml"
    generated.write_text(f"[memory.index]\nread = {'true' if deployed else 'false'}\n")
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    _stub_leaked_blank_sweep(monkeypatch)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: generated)
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)
    monkeypatch.setattr(m, "assert_shared_databases_unchanged", lambda url, before: None)

    async def _close() -> None:
        return None

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    checked: list[str] = []
    monkeypatch.setattr(
        m, "graph_surface_lines", lambda url, model: checked.append(model) or ["checked"],
    )
    seen: list[bool] = []
    monkeypatch.setattr(
        m, "_run_one", lambda *a, **k: seen.append(k["memory_index"]) or True,
    )
    # `--allow-long`: the whole corpus is over the run budget, which is not
    # what this pins (`test_eval_main_budget`).
    _set_argv(monkeypatch, "tool_selection", "--allow-long",
              *(["--memory-index"] if flag else []))
    assert m.main() == 0
    assert seen == [graph]
    out = capsys.readouterr().out
    assert header in out
    # The graph arm's preconditions are read before any draw, and only there.
    assert len(checked) == (1 if graph else 0)
    assert ("checked" in out) is graph


def test_a_graph_surface_run_refuses_before_any_draw(monkeypatch, capsys, tmp_path):
    """An embedder that does not serve the configured file, or a source
    without the model's vectors: rc 2, and no case runs lexical in its place."""
    from nora.subject import GraphIndexRefusedError

    generated = tmp_path / "config.evaluation.toml"
    generated.write_text("[memory.index]\nread = false\n")
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    _stub_leaked_blank_sweep(monkeypatch)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: object())
    monkeypatch.setattr(m, "materialise", lambda **k: generated)
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)
    monkeypatch.setattr(m, "assert_shared_databases_unchanged", lambda url, before: None)

    async def _close() -> None:
        return None

    monkeypatch.setattr(m, "close_private_blank_templates", _close)

    def _refuse(url: str, model: str) -> list[str]:
        raise GraphIndexRefusedError("the eval source holds no vector of it")

    monkeypatch.setattr(m, "graph_surface_lines", _refuse)
    ran: list[bool] = []
    monkeypatch.setattr(m, "_run_one", lambda *a, **k: ran.append(True) or True)
    _set_argv(monkeypatch, "tool_selection", "--memory-index", "--allow-long")
    assert m.main() == 2
    assert ran == []
    assert "refusing to run: the eval source holds no vector" in capsys.readouterr().err
