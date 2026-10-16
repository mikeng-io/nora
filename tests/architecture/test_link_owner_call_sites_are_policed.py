"""No function under `nora/agent/` calls a network parser or host predicate
outside the link-owner registry, except an owner's own read of its own network
and the Instagram share helper. AST over the call sites, with an allowlist that
only shrinks.

WHAT IS GOVERNED: `parse_link` from `nora.domain.x_links`/
`linkedin_links`/`instagram_links`, and each module's own host predicate
(`is_x_host`, `is_linkedin_host`, and any future `is_..._host`) -- read off
each file's OWN imports, never hand-copied, so a rename is followed
automatically. `is_handle` is NOT governed: it checks a typed HANDLE, never
a URL's host.

WHERE THIS RUNS: every `.py` file under `nora/agent/`, except
`nora/agent/link_owners.py` itself -- the registry IS the thing every other
call site is supposed to go through instead, so it is exempt everywhere in
its own body.

THE ALLOWLIST (`_ALLOWED`) is keyed by the ENCLOSING FUNCTION's name, not a
line number, so it survives the file moving around it: `x_read`,
`linkedin_read` and `instagram_read` each read their OWN network's link to
decide what to read (never another's, which goes through
`nora.agent.tools._other_link_owner`/`owner_of` instead), and
`_shared_page_link` is the Instagram share render's own helper (a different
question, from a different input -- a rendered transcript line or a stored
share's own two links, never a model-facing tool argument). It only SHRINKS:
removing a row because a hand-written branch was replaced by the registry is
the point, and a cross-network branch is never a row, since it should go
through the registry.

AST, not import: importing `nora.agent.tools` constructs the whole tool
registry and its dependencies, which a census over source text does not
need -- the same reason `test_tool_builders_forward_everything.py` and
`test_the_registry_is_exhaustive.py` read source too.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "nora" / "agent"

#: The registry module is exempt everywhere: it IS the registry.
_EXEMPT_MODULES = frozenset({"link_owners.py"})

#: The three domain modules whose `parse_link`/host predicate are governed.
_GOVERNED_DOMAIN_MODULES = ("x_links", "linkedin_links", "instagram_links")


def _is_governed_original_name(name: str) -> bool:
    """`parse_link`, or a host predicate (`is_x_host`, `is_linkedin_host`,
    and any future `is_..._host`) -- never `is_handle`."""
    return name == "parse_link" or (name.startswith("is_") and "host" in name)


#: enclosing function name -> the ONE reason it may call a governed name.
#: An allowlist that only SHRINKS: a fourth owner
#: adds one row for its own read; nothing here should ever grow for a
#: cross-network branch, since that is exactly what the registry replaced.
_ALLOWED: dict[str, str] = {
    "x_read": "the tool's own read of an X link",
    "linkedin_read": "the tool's own read of a LinkedIn link",
    "instagram_read": "the tool's own read of an Instagram link",
    "_shared_page_link": "the Instagram share render's own stale-cover link",
}


def _local_name(alias: ast.alias) -> str:
    return alias.asname or alias.name


def _governed_aliases(tree: ast.Module) -> dict[str, str]:
    """local alias -> original name, for every governed import in this
    module (`from ..domain.x_links import parse_link as _parse_x_link` ->
    `{"_parse_x_link": "parse_link"}`)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not any(node.module.endswith(m) for m in _GOVERNED_DOMAIN_MODULES):
            continue
        for alias in node.names:
            if _is_governed_original_name(alias.name):
                aliases[_local_name(alias)] = alias.name
    return aliases


def _enclosing_function_name(tree: ast.Module, call: ast.Call) -> str | None:
    """The innermost named `def`/`async def` containing `call`, however
    deeply nested (`x_read` is a closure inside `build_tool_specs`). The
    innermost enclosing def is the one with the LATEST start line among
    every def whose span contains the call -- a nested def is always
    written after its enclosing def's own `def` line."""
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= call.lineno <= end and (best is None or node.lineno > best.lineno):
            best = node
    return best.name if best is not None else None


def _offenders_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _governed_aliases(tree)
    if not aliases:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in aliases:
            continue
        fn_name = _enclosing_function_name(tree, node)
        if fn_name in _ALLOWED:
            continue
        out.append(
            f"{path.relative_to(ROOT)}:{node.lineno}: "
            f"{fn_name or '<module scope>'} calls {node.func.id} "
            f"({aliases[node.func.id]})"
        )
    return out


def test_no_function_calls_a_network_parser_or_host_predicate_outside_the_registry() -> None:
    offenders: list[str] = []
    for path in sorted(AGENT_DIR.rglob("*.py")):
        if path.name in _EXEMPT_MODULES:
            continue
        offenders.extend(_offenders_in(path))
    assert not offenders, (
        "a function outside the link-owner registry calls a network parser "
        "or host predicate directly (design/social-per-network/"
        "30-link-owner-registry.md, \"Tests and enforcement\"): "
        f"{offenders}. Route it through nora.agent.link_owners.owner_of "
        "instead, or add the function to _ALLOWED with the reason."
    )


def test_the_allowlist_is_actually_exercised() -> None:
    """Positive control (CLAUDE.md: pair every zero-assertion with one): a
    stale `_ALLOWED` row (a function renamed or deleted) is a row that
    guards nothing forever, exactly the shape the sweep above cannot itself
    detect -- an empty offenders list is also what a vacuous allowlist
    produces."""
    tree = ast.parse((AGENT_DIR / "tools.py").read_text(encoding="utf-8"))
    aliases = _governed_aliases(tree)
    called_in: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in aliases:
            continue
        fn_name = _enclosing_function_name(tree, node)
        if fn_name is not None:
            called_in.add(fn_name)
    stale = set(_ALLOWED) - called_in
    assert not stale, f"_ALLOWED name(s) that call nothing governed: {sorted(stale)}"
