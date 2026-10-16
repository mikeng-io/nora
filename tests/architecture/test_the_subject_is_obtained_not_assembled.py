"""`nora.subject` is the ONE way the graph is obtained, and it cannot be assembled.

In one sentence: different evaluation suites, different
input, ONE Nora. A suite that composes its own graph measures something the
deployment does not send.

WHAT THIS FILE HOLDS, and what it deliberately does not. It cannot check that a
suite measures Nora — no test can, and pretending otherwise is the failure this
bundle is about, one layer up. It checks three things a machine CAN decide:

1. the layer's direction — `nora.app` may not reach `nora.subject`;
2. `SubjectSpec` names only seams, never a collaborator;
3. the seam list only grows by decision — pinned in BOTH directions, so a field
   added without arguing it reddens here.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from nora.subject import SubjectSpec

ROOT = Path(__file__).resolve().parents[2]

#: Everything a caller may state, because it is NOT Nora. Pinned both ways: a NEW field
#: fails here, and removing one without editing this list fails too.
#:
#: **Adding a name here is a claim that something has stopped being part of her.** A
#: pipeline builder that grows one parameter per caller ends with callers each
#: forgetting a different thing; the pin exists to force the argument.
#:
#: `now`: WHAT TIME IT IS is not part of Nora — it is the world reaching her,
#: exactly as a message is. For the proactive adaptor the time IS the question it
#: answers (is this due, has this gone stale), so a case that cannot state it cannot
#: ask that adaptor anything; it can only watch it answer about today.
#: `build_dependencies` grew the parameter for the same reason and it is not an
#: eval-only seam: nine components already took an injectable `clock` and the
#: composition root was writing the same literal nine times.
#:
#: `room`: cases share one database, so one room is one transcript. The gate reads
#: the newest 40 rows and the Dreamer the OLDEST 64, so without a room per case a
#: case is graded on other cases' messages — and past 64 rows its own input never
#: reaches the adaptor at all, while it is graded anyway. WHICH ROOM is plainly not
#: part of Nora; it is where the world reaches her.
_SEAMS = frozenset({
    "database_url", "workdir", "overrides", "identity", "policy", "now", "room",
})


def test_the_spec_names_only_seams() -> None:
    fields = {f.name for f in dataclasses.fields(SubjectSpec)}
    assert fields == _SEAMS, (
        f"SubjectSpec changed: {sorted(fields ^ _SEAMS)}. A new field is a claim "
        "that something has stopped being part of Nora — argue it in "
        "design/evaluation/01-boundaries.md §2 and amend this list, or find "
        "another way. Every one of build_offline_pipeline's 21 parameters was "
        "added for a good local reason."
    )


def test_the_application_cannot_reach_the_subject() -> None:
    """The layer's whole point, held here as well as in `pyproject.toml`.

    `lint-imports` enforces it too. This exists because a reader lands on the
    code, and because that contract is one edit away from being reordered by
    somebody who does not know what the ordering was for."""
    offenders = []
    for path in (ROOT / "nora" / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for m in mods:
                if m == "nora.subject" or m.startswith("nora.subject."):
                    offenders.append(f"{path.relative_to(ROOT)}: {m}")
    assert not offenders, (
        f"nora/app reaches nora.subject: {offenders}. The direction is the "
        "property: the application must not learn it is being measured."
    )


def test_obtain_is_the_only_thing_that_composes() -> None:
    """A second way to COMPOSE is a second Nora, which is the whole finding.

    Stated as "returns a `Dependencies`" rather than "is the only public name".
    Pinning the whole `__all__` would measure spelling instead of the property:
    the finding is ASSEMBLIES, not exported names (`know()`, which writes rows
    into stores the subject already has, is a legitimate export).
    """
    import inspect

    import nora.subject as pkg
    from nora.app.composition import Dependencies

    composers = set()
    for name in pkg.__all__:
        obj = getattr(pkg, name)
        if not callable(obj) or inspect.isclass(obj):
            continue
        ret = inspect.signature(obj).return_annotation
        if "Subject" in str(ret) or ret is Dependencies:
            composers.add(name)
    assert composers == {"obtain"}, (
        f"more than one name in nora.subject returns a composed Nora: "
        f"{sorted(composers)}. A second composer is a second assembly, which is "
        "the defect this package exists to end."
    )
