"""AGENTS.md's "Never" list: never assume your migration's parent is
unclaimed — "two branches cut from the same head each write `down_revision =
<that head>`, git merges both files without a conflict, every test that does
not actually migrate stays green, and the container fails at boot because
`upgrade "head"` refuses two heads." The documented recovery is `uv run
alembic heads` printing exactly one line after every merge; nothing ran that
check as a TEST, so it depended on someone remembering to type the command.

This file is that check, computed off the migration files directly rather
than by shelling out to the `alembic` CLI: each revision module under
`nora/infra/sql/migrations/versions/` declares a module-level `revision` and
`down_revision` (an id, a tuple of ids for a merge revision, or `None` for the
root). A HEAD is a revision no other revision names as a parent. Reading the
AST is faster than invoking Alembic (no DB, no `Config`, no import of the
package), and it is exactly the graph Alembic itself walks to answer `heads`.

Positive control: two migrations independently cut from the same parent — the
exact shape of the failure above — must resolve to two heads, not one; built in a
tmp_path so the assertion is about the DETECTOR, not about this repo's current
migration tree happening to be healthy.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "nora" / "infra" / "sql" / "migrations" / "versions"

# The floor is "just under" the real revision count, not a round number far
# below it (CLAUDE.md's own rule for a positive control) — a walk that stopped
# reading revision modules must fail loud, not report a
# suspiciously-empty-but-technically-single head set.
_MIN_REVISIONS = 50


def _parent_ids(value: ast.expr | None) -> list[str]:
    """The parent revision id(s) named in a `down_revision` assignment's RHS:
    `None` -> [], a string -> [that string], a tuple/list (a merge revision)
    -> every string element."""
    if value is None:
        return []
    if isinstance(value, ast.Constant):
        return [value.value] if isinstance(value.value, str) else []
    if isinstance(value, ast.Tuple | ast.List):
        return [p for elt in value.elts for p in _parent_ids(elt)]
    return []


def _revision_and_parents(path: Path) -> tuple[str | None, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    revision: str | None = None
    parents: list[str] = []
    for node in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "revision" and isinstance(value, ast.Constant) and isinstance(
                value.value, str
            ):
                revision = value.value
            elif target.id == "down_revision":
                parents = _parent_ids(value)
    return revision, parents


def revision_graph(directory: Path) -> dict[str, list[str]]:
    """`revision id -> [parent revision id(s)]`, read off every `*.py` file in
    `directory` that declares a module-level `revision`. Files that declare no
    `revision` (an `__init__.py`, an `env.py`) are silently skipped — they are
    not migrations."""
    graph: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.py")):
        revision, parents = _revision_and_parents(path)
        if revision is not None:
            graph[revision] = parents
    return graph


def heads(graph: dict[str, list[str]]) -> set[str]:
    """Revisions no other revision names as a `down_revision` — what `alembic
    heads` would print, computed from the same graph Alembic walks."""
    parents_of_someone = {p for parents in graph.values() for p in parents}
    return set(graph) - parents_of_someone


def test_the_migration_chain_resolves_to_exactly_one_head() -> None:
    graph = revision_graph(MIGRATIONS_DIR)

    # Positive control: a walk that stopped reading files would report an
    # empty graph, and an empty graph has zero heads — which is NOT one, so it
    # would already fail the assertion below. This makes the "we actually read
    # the tree" check explicit instead of relying on that coincidence.
    assert len(graph) >= _MIN_REVISIONS, (
        f"only read {len(graph)} revision(s) from "
        f"{MIGRATIONS_DIR.relative_to(ROOT)} — the AST walk likely stopped "
        "recognising the revision/down_revision assignments; fix the walk "
        "before trusting a green head count"
    )

    found = heads(graph)
    assert len(found) == 1, (
        "nora/infra/sql/migrations/versions/ resolves to "
        f"{len(found)} head(s), not one: {sorted(found)}. `alembic upgrade "
        '"head"` refuses to boot with more than one — this is the shape '
        "AGENTS.md warns about: two branches cut from the same parent, "
        "merged with no git conflict because they touch different files. "
        "Rechain the newer one's down_revision onto the other."
    )


def test_two_branches_cut_from_the_same_parent_are_detected_as_two_heads(
    tmp_path: Path,
) -> None:
    """Positive control: the failure itself, reconstructed. Two sessions
    branch off the same revision and each writes `down_revision = <that
    revision>` — exactly what git merges without a conflict. The detector
    must report two heads, not one."""
    (tmp_path / "0001_base.py").write_text(
        'revision = "0001_base"\ndown_revision = None\n'
    )
    (tmp_path / "0002_branch_a.py").write_text(
        'revision = "0002_branch_a"\ndown_revision = "0001_base"\n'
    )
    (tmp_path / "0002_branch_b.py").write_text(
        'revision = "0002_branch_b"\ndown_revision = "0001_base"\n'
    )

    graph = revision_graph(tmp_path)
    assert graph == {
        "0001_base": [],
        "0002_branch_a": ["0001_base"],
        "0002_branch_b": ["0001_base"],
    }
    assert heads(graph) == {"0002_branch_a", "0002_branch_b"}


def test_a_merge_revision_naming_both_parents_resolves_the_fork() -> None:
    """The other half of the same control: once a merge revision (Alembic's
    own fix for two heads) names BOTH branch tips as its `down_revision`
    tuple, the fork resolves back to a single head."""
    graph = {
        "0001_base": [],
        "0002_branch_a": ["0001_base"],
        "0002_branch_b": ["0001_base"],
        "0003_merge": ["0002_branch_a", "0002_branch_b"],
    }
    assert heads(graph) == {"0003_merge"}


def test_a_tuple_down_revision_is_read_as_two_parents(tmp_path: Path) -> None:
    """The AST parser itself, not just the hand-built graph above, must read
    Alembic's actual merge-revision syntax: `down_revision = ("a", "b")`."""
    (tmp_path / "0001_base.py").write_text(
        'revision = "0001_base"\ndown_revision = None\n'
    )
    (tmp_path / "0002_branch_a.py").write_text(
        'revision = "0002_branch_a"\ndown_revision = "0001_base"\n'
    )
    (tmp_path / "0002_branch_b.py").write_text(
        'revision = "0002_branch_b"\ndown_revision = "0001_base"\n'
    )
    (tmp_path / "0003_merge.py").write_text(
        'revision = "0003_merge"\n'
        'down_revision = ("0002_branch_a", "0002_branch_b")\n'
    )

    graph = revision_graph(tmp_path)
    assert graph["0003_merge"] == ["0002_branch_a", "0002_branch_b"]
    assert heads(graph) == {"0003_merge"}
