"""One block injector AND one semantic composer per content kind, everywhere.

ContextBlock is framing, not permission to serialize domain rows. Every discovered
body, assembly/table and audited tool-result surface has a closed classification:
its semantic owner, or a composition of named owners. Unknown flows fail closed.
The classification binds the AST of the function and its local helper closure;
changing a join, JSON projection, slice, escape or helper invalidates it. Calling
an owner somewhere in a function cannot bless an unclassified sibling body.

This deliberately conservative static grammar uses reviewed source certificates,
not a claim to infer arbitrary Python types. A certificate is not a path waiver:
new sinks and changed source need semantic classification. Do not refresh hashes
to silence a failure; trace every content leaf to its owner. Structural helpers
(escape/render_elements/json.dumps) alone are never semantic owners. _ALLOWED
only shrinks: the audited parallel composers remain violations, and removing a
flow requires removing its entry. Runtime final-request source duplication and
dynamically dispatched tool payload provenance need the complementary follow-up.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

# Each certificate: content kind(s), semantic owner(s), source closure digest.
# The separate data file keeps the discovery/measurement code readable.
from tests.architecture.context_composer_classifications import CLASSIFIED, KNOWN

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Surface:
    file: str
    function: str
    node: ast.AST
    scope: ast.AST
    tree: ast.Module
    line: int

    @property
    def key(self) -> str:
        return f"{self.file}::{self.function}"


def _name(node: ast.AST) -> str:
    return ast.unparse(node).split(".")[-1]


def _scopes(tree: ast.Module) -> dict[ast.AST, str]:
    scopes: dict[ast.AST, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            prefix = f"{prefix}.{node.name}".strip(".")
        scopes[node] = prefix
        for child in ast.iter_child_nodes(node):
            walk(child, prefix)

    walk(tree, "")
    return scopes


# Audited result producers, including helpers below room_read/load_transcript.
# These include raw semantic helpers and the Cognition result outside the
# native registry. General dynamic tool dispatch is outside this static census.
_AUDITED_RESULTS = {
    "nora/agent/tools.py": {
        "_transcript_messages",
        "_target_item",
        "_source_event_item",
        "event_search",
        "note_read",
        "note_search",
        "memo_read",
        "character_read",
        "rollup_read",
    },
    "nora/app/cognition_memory.py": {"propose"},
    "nora/agent/reaction.py": {"render_recent"},
}


# Semantic leaves named by the audit, independently bound even when their body
# is returned as text/JSON instead of a ContextBlock. Structure helpers confer
# no permission on new callers; the caller still needs its own classification.
_CANONICAL = {
    "nora/domain/blocks.py": {"render_messages", "render_row_window", "render_rows"},
    "nora/domain/media.py": {
        "render_placeholder",
        "placeholder_suffix",
        "body_with_handles",
        "enrich_placeholders",
    },
    "nora/language/profile_render.py": {"_body", "account_text"},
    "nora/language/comprehend_render.py": {"preview_text"},
    "nora/agent/memory_search.py": {"render_packet"},
    "nora/language/note_render.py": {"render_note_rows"},
    "nora/language/memo_render.py": {"render_memo_rows", "memo_rows"},
    "nora/language/event_render.py": {"render_event_rows", "event_when"},
    "nora/infra/social/render.py": {"render", "_reading_line"},
}


def discover(source: str, file: str) -> list[Surface]:
    tree = ast.parse(source, filename=file)
    scopes = _scopes(tree)
    functions = {
        scopes[n]: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    aliases = {
        a.asname or a.name: a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        for a in n.names
    }
    factories = {"ContextBlock", "wrap_outside_text"}
    if any(
        isinstance(n, ast.ImportFrom)
        and n.module
        and n.module.endswith("domain.blocks")
        and any(a.name == "assemble" for a in n.names)
        for n in ast.walk(tree)
    ):
        factories.add("assemble")
    factories.update(
        a.asname or a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("domain.blocks")
        for a in n.names
        if a.name == "assemble"
    )
    # Find local block factories, including import aliases and qualified calls.
    for name, node in functions.items():
        if any(a.arg == "body" for a in (*node.args.args, *node.args.kwonlyargs)) and any(
            isinstance(n, ast.Call) and aliases.get(_name(n.func), _name(n.func)) == "ContextBlock"
            for n in ast.walk(node)
        ):
            factories.add(name.split(".")[-1])
    sinks: dict[str, Surface] = {}
    for n in ast.walk(tree):
        hit = False
        if isinstance(n, ast.Call):
            callee = aliases.get(_name(n.func), _name(n.func))
            hit = callee in factories
            hit |= (
                callee in {"replace", "restamp"}
                and "ContextBlock" in aliases.values()
                and any(k.arg == "body" for k in n.keywords)
            )
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            hit |= any(isinstance(t, ast.Name) and t.id == "_BLOCKS" for t in targets)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            hit |= n.name in (_AUDITED_RESULTS.get(file, set()) | _CANONICAL.get(file, set()))
        # The audited V1 append isn't a ContextBlock; it feeds render_agenda.
        if isinstance(n, ast.AugAssign):
            hit |= isinstance(n.target, ast.Attribute) and n.target.attr == "graph_text"
        if not hit:
            continue
        scope_name = scopes[n]
        scope = functions.get(scope_name, n)
        # For V1 bind only the append: its deletion must make the pin stale.
        if isinstance(n, ast.AugAssign):
            scope_name += ".reading_sources"
            scope = n
        surface = Surface(file, scope_name or "<module>", n, scope, tree, n.lineno)
        sinks[surface.key] = surface
    return list(sinks.values())


def fingerprint(surface: Surface) -> str:
    """Bind the complete function, local helper calls and module declarations.

    Source certificates include siblings/attrs, not just a call-name allowlist.
    Local helper recursion is a fixed point, so moving JSON to a helper cannot
    launder it. Imported owner implementations are independently certified.
    """
    scopes = _scopes(surface.tree)
    functions = {
        scopes[n]: n
        for n in ast.walk(surface.tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending = [surface.scope]
    seen: dict[str, str] = {}
    while pending:
        node = pending.pop()
        key = scopes.get(node, "<module>")
        if key in seen:
            continue
        seen[key] = ast.dump(node, include_attributes=False)
        calls = {_name(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
        pending.extend(
            fn
            for name, fn in functions.items()
            if name.split(".")[-1] in calls and name not in seen
        )
    # Constants/imports affect projection too. Ignore prose and source positions.
    declarations = [
        n
        for n in surface.tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))
    ]
    payload = repr(sorted(seen.items())) + ast.dump(ast.Module(body=declarations, type_ignores=[]))
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def violations(surfaces: list[Surface]) -> list[str]:
    problems = []
    for s in surfaces:
        entry = CLASSIFIED.get(s.key) or KNOWN.get(s.key)
        if entry is None:
            problems.append(
                f"{s.file}:{s.line} {s.function}: unknown content flow; classify its semantic owner"
            )
        elif fingerprint(s) != entry[2]:
            problems.append(
                f"{s.file}:{s.line} {s.function}: changed content flow ({entry[0]} -> {entry[1]}); "
                "raw serialization, slicing/re-escaping or an unclassified helper requires review"
            )
    return problems


def test_every_context_body_has_one_classified_composer() -> None:
    surfaces = [
        s
        for path in sorted((ROOT / "nora").rglob("*.py"))
        for s in discover(path.read_text(), path.relative_to(ROOT).as_posix())
    ]
    assert surfaces, "positive control: no context surfaces discovered"
    problems = violations(surfaces)
    assert not problems, "Unclassified model context (Consistency/Conformance):\n" + "\n".join(
        problems
    )
    found = {s.key for s in surfaces}
    stale = (CLASSIFIED.keys() | KNOWN.keys()) - found
    assert not stale, (
        "Removed composer/violation: remove its stale classification "
        f"(_ALLOWED only shrinks): {sorted(stale)}"
    )
    assert not CLASSIFIED.keys() & KNOWN.keys(), (
        "a known violation cannot also be certified compliant"
    )


def test_discovery_rejects_raw_and_disguised_bodies() -> None:
    examples = (
        'from nora.domain.blocks import ContextBlock as B\ndef bad(rows):\n'
        ' return B(name="x", body=json.dumps(rows))',
        'import nora.domain.blocks as b\ndef bad(rows):\n'
        ' return b.ContextBlock(name="x", body="".join(f"{r.body}" for r in rows))',
        'from nora.domain.blocks import ContextBlock\ndef bad(block):\n'
        ' return replace(block, body=escape(block.body[:20]))',
        'def bad(rows):\n return ContextBlock(name="x", body=helper(rows))\n'
        'def helper(rows):\n return json.dumps(rows)',
        '_BLOCKS = (("x", 1, "turn"),)\ndef bad(parts):\n return assemble(parts)',
    )
    for source in examples:
        assert violations(discover(source, "nora/new_occasion.py")), source
