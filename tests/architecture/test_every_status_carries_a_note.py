"""ADR 0015 §3/§6: a tool result that is not `ok` carries a `note`.

The description only NAMES a tool's statuses; how to read or report one is the
result's `note`, sent only when that status comes back. A non-ok result with no note
is a status she was told exists and then given no guidance on. The per-tool tests
drive each tool's paths; this is the generic guard for a path or a tool added later.

A static check over the modules tool results are built in: every dict literal
whose `status` is a non-ok string must also carry a `note` key, or spread
another mapping (`**`) that may carry it. Dicts built by a helper are checked
where the helper builds them, which is why the helper modules are listed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Every module a tool result is assembled in. A module that starts returning
#: tool results is added here in the same commit.
RESULT_MODULES = (
    "nora/agent/tools.py",
    "nora/agent/whereis.py",
    "nora/agent/relay.py",
    "nora/agent/surface.py",
    "nora/agent/link_owners.py",
    "nora/infra/search/_shared.py",
    "nora/infra/routes/directions.py",
    "nora/infra/transit/arrivals.py",
    "nora/agent/arithmetic.py",
)


def _noteless_statuses(source: str) -> list[tuple[str, int]]:
    """`(status, line)` for every non-ok status dict with no `note` key and no
    `**` spread."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        if None in node.keys:  # a `**spread` may carry the note
            continue
        keys = {k.value: v for k, v in zip(node.keys, node.values, strict=True)
                if isinstance(k, ast.Constant)}
        status = keys.get("status")
        if (isinstance(status, ast.Constant) and isinstance(status.value, str)
                and status.value != "ok" and "note" not in keys):
            bad.append((status.value, node.lineno))
    return bad


def test_every_non_ok_status_dict_carries_a_note() -> None:
    found = {}
    for rel in RESULT_MODULES:
        bad = _noteless_statuses((ROOT / rel).read_text(encoding="utf-8"))
        if bad:
            found[rel] = bad
    assert not found, (
        f"a non-ok tool result with no `note` (ADR 0015 §3): {found}. Add a "
        "short English note saying how to read or report that status."
    )


def test_the_check_catches_a_noteless_status() -> None:
    """Positive control: the walk must flag the shape it exists for, and pass
    the shapes it must allow, or a broken walk passes every module vacuously."""
    assert _noteless_statuses('x = {"status": "unavailable"}') == [("unavailable", 1)]
    assert _noteless_statuses('x = {"status": "unavailable", "note": "n"}') == []
    assert _noteless_statuses('x = {"status": "ok"}') == []
    assert _noteless_statuses('x = {"status": "refused", **extra}') == []


def test_every_listed_module_exists() -> None:
    missing = [rel for rel in RESULT_MODULES if not (ROOT / rel).exists()]
    assert not missing, f"moved or renamed, update RESULT_MODULES: {missing}"
