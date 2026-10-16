"""The entry point is a port: one runner drives both a component and a whole turn.

> If driving `RelevanceGate.decide` and `FirePath.speak` through one runner
> needs different lifecycles, different setup order, or different outcome
> shapes, then the boundary is drawn wrong.

Two entry points chosen for being maximally unalike: one drives a component and no turn,
the other a whole turn and no gate. These are the mechanical halves of that reading;
`tests/integration/test_two_entry_points_one_runner.py` is the half that actually drives
both.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REBUILT = ROOT / "evals" / "rebuilt"
#: The runner: `evals/backbone.py`'s `_drive` is the one path.
RUNNER = ROOT / "evals" / "backbone.py"

#: Stated so it can be checked: the eval layer never composes a subject. These are the
#: call names an eval-side assembly would use.
FORBIDDEN_CALLS = {
    "RelevanceGate",
    "FirePath",
    "DreamerRuntime",
    "RegistryToolSource",
    "LiveAgentRunner",
    "Agent",
    "build_offline_pipeline",
    "build_dependencies",
}


def _sources() -> list[tuple[Path, ast.Module]]:
    return [
        (p, ast.parse(p.read_text(encoding="utf-8")))
        for p in sorted(REBUILT.rglob("*.py"))
    ]


def test_the_eval_layer_composes_nothing() -> None:
    """The property, and ALIASES RESOLVE BACK TO IT.

    Matching the call's own name against a denylist is defeated by two lines:

        from nora.app.composition import RelevanceGate as _RG
        _PROBE = _RG(model=None)

    — because `_RG` is not on the list and `nora.app.composition` is on the IMPORT
    allowlist. So every local name bound to a forbidden symbol is resolved back through
    `import ... as` and `from ... import ... as`, so the alias is the thing that reddens.
    """
    offenders = []
    for path, tree in _sources():
        # Local name -> the forbidden symbol it was bound to.
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name in FORBIDDEN_CALLS:
                        aliases[a.asname or a.name] = a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    tail = a.name.rsplit(".", 1)[-1]
                    if tail in FORBIDDEN_CALLS:
                        aliases[a.asname or tail] = tail
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            resolved = aliases.get(name, name)
            if resolved in FORBIDDEN_CALLS:
                shown = name if resolved == name else f"{name} (= {resolved})"
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {shown}(")
    assert not offenders, (
        f"the eval layer composes a subject: {offenders}. There is one way to "
        "get Nora and it is `nora.subject.obtain`; a second is a second Nora, "
        "which is the entire finding this rebuild answers."
    )


def test_the_runner_has_no_suite_specific_branch() -> None:
    """The entry point is a SELECTION ON A CASE, not a branch.

    A runner that branches on `case.entry` is a runner that grows a limb per suite, which
    is the eight drivers reappearing inside one file.
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if isinstance(left, ast.Attribute) and left.attr == "entry":
            raise AssertionError(
                f"backbone.py:{node.lineno} compares `case.entry` — the entry "
                "point is a port looked up in ENTRY_POINTS, not a branch."
            )


def test_adding_a_suite_adds_no_code() -> None:
    """The property that decays first.

    Checked as: the runner reaches an entry point ONLY through the registry, so there is
    no name a new suite could need it to import.
    """
    src = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("entry"):
            imported = {a.name for a in node.names}
    assert imported == {"ENTRY_POINTS"}, (
        f"backbone.py imports {sorted(imported)} from the entry module. Only "
        "ENTRY_POINTS may cross: importing an entry point by name is how a "
        "runner acquires a suite-specific path without looking like it has one."
    )


def test_both_entry_points_have_the_same_signature() -> None:
    """One lifecycle for both.

    Same parameters and the same return type is the mechanical half of "they share a
    runner": a port whose implementations take different arguments is two ports wearing
    one name.
    """
    import inspect

    from evals.rebuilt.entry import ENTRY_POINTS

    sigs = {
        name: (
            tuple(inspect.signature(port.drive).parameters),
            str(inspect.signature(port.drive).return_annotation),
        )
        for name, port in ENTRY_POINTS.items()
    }
    assert len(set(sigs.values())) == 1, (
        f"entry points do not share a shape: {sigs}. §7: if they cannot share "
        "a runner, the boundary is drawn wrong and this is where we learn it."
    )
