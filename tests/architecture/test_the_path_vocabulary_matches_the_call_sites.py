"""Every `audience` a `deliver.build` call site can pass is a DECLARED one.

`audience` labels `nora_silent_failures_total`, `nora_llm_latency_seconds` and
`nora_llm_stop_reason_total`, and it is also `reasoning_strip_log.path` — the column
an operator groups by to ask which loop is losing its reasoning.

**This gate exists because a hand-written vocabulary drifts from the call
sites.** `nora/app/fire.py` sends `heartbeat` — every reminder and gathering
turn, which is tool-using and multi-turn — and `proactive`, besides
«tag / participation / dreamer:<adaptor>». Anyone filtering
`WHERE path IN ('tag','participation')` off documentation that omits them drops
the reminder path in silence. The sibling label `outcome` is derived from a
`Literal` precisely so it cannot drift; `path` is declared in
`nora.domain.audience` and checked here.

**Derived from the CALL SITES, not from a list.** The check walks every
`deliver.build(...)` under `nora/` — there is no registry of callers here, so a
third one is covered the moment it lands — resolves each `audience=` expression
against the module it lives in, and requires the result to be a value declared
in `nora.domain.audience`. A new audience is then a two-line change (declare it,
pass it) and an undeclared one is a red test rather than a silent label.

**What this CANNOT see, stated rather than left to be found.** An `audience`
computed from something that is not a constant — a config read, a dict lookup,
a function call — resolves to nothing and is reported as unresolvable rather
than as clean. That is the R-27 posture the rest of these gates keep: unchecked
is never the same as passing.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from nora.domain import audience as audience_names

NORA = Path(audience_names.__file__).resolve().parent.parent


def _modules_calling_deliver_build() -> dict[str, ast.Module]:
    """Every module under `nora/` with a `deliver.build(...)` in it."""
    out: dict[str, ast.Module] = {}
    for path in sorted(NORA.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "deliver.build(" not in text:
            continue
        rel = path.relative_to(NORA.parent).with_suffix("")
        out[".".join(rel.parts)] = ast.parse(text, filename=str(path))
    return out


def _build_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "build"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "deliver"
    ]


def _assignments(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """Name -> every expression ever assigned to it, anywhere in the module.

    Deliberately not scope-aware: over-collecting can only make the resolver
    consider MORE candidate values, which widens what must be declared. A
    scope-aware version that missed one would narrow it, and narrowing is how a
    gate goes quiet.
    """
    out: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(node.value)
        elif (isinstance(node, ast.AnnAssign)
              and isinstance(node.target, ast.Name)
              and node.value is not None):
            out.setdefault(node.target.id, []).append(node.value)
    return out


def _resolve(
    expr: ast.expr, *, module: object, assigns: dict[str, list[ast.expr]],
    depth: int = 0,
) -> set[str] | None:
    """Every string value this expression can take, or `None` if unresolvable.

    `None` is an answer — «this gate cannot see it» — never a pass.
    """
    if depth > 4:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        parts = [
            _resolve(e, module=module, assigns=assigns, depth=depth + 1)
            for e in (expr.body, expr.orelse)
        ]
        if any(p is None for p in parts):
            return None
        return set().union(*parts)  # type: ignore[arg-type]
    if isinstance(expr, ast.Attribute):
        # `audience_names.HEARTBEAT` — read the real value off the real module.
        value = getattr(audience_names, expr.attr, None)
        return {value} if isinstance(value, str) else None
    if isinstance(expr, ast.Name):
        value = getattr(module, expr.id, None)
        if isinstance(value, str):
            return {value}
        seen: set[str] = set()
        for assigned in assigns.get(expr.id, []):
            part = _resolve(assigned, module=module, assigns=assigns, depth=depth + 1)
            if part is None:
                return None
            seen |= part
        return seen or None
    return None


def _audiences() -> tuple[set[str], list[str]]:
    """`(resolved values, unresolvable call sites)` across every caller."""
    values: set[str] = set()
    blind: list[str] = []
    for name, tree in _modules_calling_deliver_build().items():
        module = importlib.import_module(name)
        assigns = _assignments(tree)
        for call in _build_calls(tree):
            keyword = next(
                (k for k in call.keywords if k.arg == "audience"), None,
            )
            if keyword is None:
                blind.append(f"{name}:{call.lineno} (no audience= at all)")
                continue
            resolved = _resolve(keyword.value, module=module, assigns=assigns)
            if resolved is None:
                blind.append(f"{name}:{call.lineno}")
                continue
            values |= resolved
    return values, blind


def test_every_audience_a_call_site_can_pass_is_declared() -> None:
    """THE GATE. An undeclared audience reaches `reasoning_strip_log.path` and
    every `audience`-labelled series as a string nothing documents."""
    values, _ = _audiences()
    undeclared = sorted(values - set(audience_names.AUDIENCES))
    assert not undeclared, (
        f"`deliver.build` can be passed {undeclared}, which "
        f"`nora.domain.audience` does not declare. These reach "
        f"`reasoning_strip_log.path` and every audience-labelled series, and "
        f"the help text and runbook are generated from the declaration — so an "
        f"undeclared one is a label no document mentions. Declare it (and say "
        f"whether it is a SPEAKING audience, which can produce a row, or a "
        f"SHADOW one, which cannot) rather than widening this test."
    )


def test_every_declared_speaking_audience_is_actually_reachable() -> None:
    """The other direction, and the one that would rot quietly.

    A declared audience nothing can pass is a value in the help text, the
    runbook and the ADR that will never appear in the table — documentation
    promising a row that cannot exist. The shadow audiences are excluded
    because they are declared precisely as the ones that never produce one.
    """
    values, _ = _audiences()
    unreachable = sorted(set(audience_names.SPEAKING_AUDIENCES) - values)
    assert not unreachable, (
        f"`nora.domain.audience` declares {unreachable} as speaking "
        f"audiences, but no `deliver.build` call site can pass them. Either a "
        f"path was deleted and the declaration was not, or the resolver has "
        f"stopped seeing a call site — and the second reads exactly like the "
        f"first from here."
    )


def test_the_resolver_can_see_the_call_sites_it_is_checking() -> None:
    """THE POSITIVE CONTROL. A resolver that resolved nothing would report «no
    undeclared audiences» with total confidence."""
    values, blind = _audiences()

    assert not blind, (
        f"these `deliver.build` call sites pass an audience this gate cannot "
        f"resolve: {blind}. Unresolvable is NOT clean — the value still "
        f"reaches the label. Pass a constant from `nora.domain.audience`, or "
        f"extend the resolver deliberately."
    )
    assert len(values) >= 4, f"the walk went thin — it resolved only {sorted(values)}"
    assert audience_names.HEARTBEAT in values, (
        "the heartbeat's audience is no longer reachable from a `deliver.build` "
        "call site, or the resolver cannot follow it — it is the value whose "
        "absence from the documentation started this file"
    )
    assert audience_names.TAG in values


def test_the_declared_constants_and_the_literal_cannot_drift() -> None:
    """`SPEAKING_AUDIENCES` is `get_args` off a `Literal`; the five names beside
    it are what the call sites import. Nothing makes those agree except this.

    A `Literal` narrowed without the constants (or the reverse) would leave the
    help text describing one set while the code passed another — the failure
    this module exists for, one layer in."""
    constants = {
        audience_names.TAG, audience_names.HEARTBEAT,
        audience_names.PARTICIPATION, audience_names.PROACTIVE,
        audience_names.SUMMONS,
    }
    assert constants == set(audience_names.SPEAKING_AUDIENCES), (
        "the named constants and the `SpeakingAudience` Literal disagree: "
        f"{sorted(constants ^ set(audience_names.SPEAKING_AUDIENCES))}"
    )
    assert not set(audience_names.SPEAKING_AUDIENCES) & set(
        audience_names.SHADOW_AUDIENCES
    ), "an audience is declared both speaking and shadow"
