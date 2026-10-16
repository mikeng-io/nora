"""`_run_adaptor` wraps its whole call to `_dream_adaptor_once` in ONE
`asyncio.timeout`: one wall clock around the whole unit cannot be defeated by
adding a new call.

`_dream_adaptor_once`'s two store phases (`inputs`, `commit`) have their own
`wait_for`s, but the cursor reads around them — `clock.last_fired`/`record_fire`,
every `language.*` watermark call, `record.since`, `media_join.enrich` — do not,
and any one of them hanging would leave `dream_loop` holding
`<adaptor>:<key>` in `running` forever (the defect
`tests/unit/test_runtime.py::test_a_hung_cursor_read_is_released_by_the_unit_wall_clock`
falsifies end to end).

This file is the STRUCTURAL half of that proof: it does not run the hang, it
checks that the shape which prevents it — the whole call sitting inside an
`async with asyncio.timeout(...)` block — is still there. Checked so an edit
that unwraps the call, or moves the timeout to cover only PART of it (the
model call alone, say), fails a test rather than silently reopening the wedge
`[dreamer] max_unit_hold_seconds` exists to close.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from nora.app import runtime


def _dream_loop_tree() -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(runtime.dream_loop)))


def _find_run_adaptor(tree: ast.AST) -> ast.AsyncFunctionDef:
    """`_run_adaptor` is a function NESTED inside `dream_loop`'s own frame
    (it closes over `running`/`failures`/`last_failure_at`), so it is not an
    importable module attribute — the walk has to start from `dream_loop`'s
    own source."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_adaptor":
            return node
    raise AssertionError(
        "dream_loop no longer defines a nested _run_adaptor — this file's "
        "walk needs updating to match wherever the per-unit dispatch moved"
    )


def _find_call(tree: ast.AST, name: str) -> ast.Call:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ):
            return node
    raise AssertionError(f"no call to {name!r} found")


def test_run_adaptor_calls_dream_adaptor_once():
    """The positive control: prove the walk below is checking a call site
    that actually exists, so a rename that broke the OTHER test silently
    would not also make this one vacuously pass."""
    tree = _dream_loop_tree()
    run_adaptor = _find_run_adaptor(tree)
    call = _find_call(run_adaptor, "_dream_adaptor_once")
    assert call.func.id == "_dream_adaptor_once"


def test_run_adaptor_wraps_dream_adaptor_once_in_a_timeout():
    """The call must sit inside an `async with asyncio.timeout(...)` block —
    ANY ancestor node, so wrapping it in more structure (a `try`, a nested
    `if`) does not break this, but a call made OUTSIDE every such block does.

    PROBE: move the `async with asyncio.timeout(max_unit_hold_seconds) as
    unit_deadline:` line so it no longer encloses the `_dream_adaptor_once`
    call (e.g. wrap only the `if status == "unavailable":` branch below it
    instead) and this fails — the exact shape a careless refactor could
    produce while every other test here still passes, because the call still
    happens and still returns a status.
    """
    tree = _dream_loop_tree()
    run_adaptor = _find_run_adaptor(tree)
    call = _find_call(run_adaptor, "_dream_adaptor_once")

    parents: dict[ast.AST, ast.AST] = {
        child: node for node in ast.walk(run_adaptor)
        for child in ast.iter_child_nodes(node)
    }

    def _is_timeout_with(node: ast.AST) -> bool:
        if not isinstance(node, ast.AsyncWith):
            return False
        return any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "timeout"
            for item in node.items
        )

    cur: ast.AST | None = parents.get(call)
    wrapped = False
    while cur is not None and cur is not run_adaptor:
        if _is_timeout_with(cur):
            wrapped = True
            break
        cur = parents.get(cur)

    assert wrapped, (
        "_run_adaptor's call to _dream_adaptor_once is no longer inside an "
        "`async with asyncio.timeout(...)` block — issue #191's wall clock "
        "around the whole (adaptor, key) unit is gone, and a cursor read "
        "hanging anywhere inside it would wedge that unit forever again"
    )
