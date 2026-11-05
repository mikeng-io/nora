"""Context bodies delegate to semantic owners, independently of unrelated edits.

Follow body expressions through assignments and local helpers. Structural escapes
and headers may surround composed text; JSON, row projections and unknown helpers
cannot stand in for a content composer. Known violations use stable function names.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from tests.architecture.context_composer_classifications import COMPOSERS, KNOWN

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
        "character_read",
        "rollup_read",
    },
    "nora/app/cognition_memory.py": {"propose"},
    "nora/agent/reaction.py": {"render_recent"},
}


def _qualified(file: str, function: str) -> str:
    return file.removesuffix(".py").replace("/", ".") + "." + function


_OWNERS = {owner for owners in COMPOSERS.values() for owner in owners}


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
    factories = {"ContextBlock", "wrap_outside_text", "_social_post"}
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
    sinks: list[Surface] = []
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
            hit |= n.name in _AUDITED_RESULTS.get(file, set())
        if not hit:
            continue
        scope_name = scopes[n]
        scope = functions.get(scope_name, n)
        surface = Surface(file, scope_name or "<module>", n, scope, tree, n.lineno)
        sinks.append(surface)
    return sinks


def _body_problems(surface: Surface) -> list[ast.AST]:
    """Resolve only expressions contributing to a body, not the whole function."""
    s = surface
    qualified = _qualified(s.file, s.function)
    if any(qualified == owner or qualified.startswith(owner + ".") for owner in _OWNERS):
        return []
    scopes = _scopes(s.tree)
    functions = {
        scopes[n]: n
        for n in ast.walk(s.tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {}
    for n in ast.walk(s.tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            package = s.file.removesuffix(".py").replace("/", ".").split(".")[:-1]
            module = (
                ".".join([*package[: len(package) - n.level + 1], n.module])
                if n.level
                else n.module
            )
            imports.update({a.asname or a.name: module + "." + a.name for a in n.names})
        elif isinstance(n, ast.Import):
            imports.update({a.asname or a.name: a.name for a in n.names})

    def resolve(expr: ast.AST, scope: ast.AST, seen: frozenset = frozenset()) -> bool:
        token = (id(expr), id(scope))
        if token in seen:
            return True
        seen = seen | {token}
        if isinstance(expr, ast.Constant):
            return True
        if isinstance(expr, ast.Name):
            assignments = []
            for n in ast.walk(scope):
                if scopes.get(n) != scopes.get(scope):
                    continue
                targets = (
                    n.targets
                    if isinstance(n, ast.Assign)
                    else ([n.target] if isinstance(n, (ast.AnnAssign, ast.AugAssign)) else [])
                )
                if (
                    any(
                        any(isinstance(t, ast.Name) and t.id == expr.id for t in ast.walk(target))
                        for target in targets
                    )
                    and n.value is not None
                ):
                    value = n.value
                    target = next(
                        t
                        for t in targets
                        if any(isinstance(v, ast.Name) and v.id == expr.id for v in ast.walk(t))
                    )
                    if (
                        isinstance(target, ast.Tuple)
                        and isinstance(value, ast.Call)
                        and _name(value.func) == "next"
                    ):
                        index = next(
                            i
                            for i, t in enumerate(target.elts)
                            if isinstance(t, ast.Name) and t.id == expr.id
                        )
                        value = ast.Subscript(value=value.args[0], slice=ast.Constant(index))
                    assignments.append(value)
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == expr.id
                    and n.func.attr in {"append", "extend"}
                ):
                    assignments.extend(n.args)
            for n in ast.walk(scope):
                if (
                    isinstance(n, ast.comprehension)
                    and isinstance(n.target, ast.Name)
                    and n.target.id == expr.id
                ):
                    assignments.append(n.iter)
            if assignments:
                return all(resolve(v, scope, seen) for v in assignments)
            # Scalar text parameters are passthroughs; row collections are not.
            args = getattr(scope, "args", None)
            if args and any(
                a.arg == expr.id and a.annotation is not None and "str" in ast.unparse(a.annotation)
                for a in (*args.args, *args.kwonlyargs)
            ):
                return True
            return expr.id.isupper()
        if isinstance(expr, ast.Attribute):
            return ast.unparse(expr) in {
                "got.text",
                "ctx.message.body",
                "parent.body",
                "b.body",
                "block.body",
            } or expr.attr in {"graph_text", "description"}
        if isinstance(expr, ast.JoinedStr):
            return all(
                resolve(v.value if isinstance(v, ast.FormattedValue) else v, scope, seen)
                for v in expr.values
            )
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return resolve(expr.left, scope, seen) and resolve(expr.right, scope, seen)
        if isinstance(expr, ast.IfExp):
            return resolve(expr.body, scope, seen) and resolve(expr.orelse, scope, seen)
        if isinstance(expr, ast.BoolOp):
            return all(resolve(v, scope, seen) for v in expr.values)
        if isinstance(expr, (ast.List, ast.Tuple)):
            return all(resolve(v, scope, seen) for v in expr.elts)
        if isinstance(expr, (ast.GeneratorExp, ast.ListComp)):
            # The iteration variable inherits the iterable's provenance.
            # Attribute projections remain forbidden unless a semantic owner
            # consumes them (for example ContextBlock(body=render_bullets(...))).
            return resolve(expr.elt, scope, seen)
        if (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "assemble"
        ):
            return all(resolve(v, scope, seen) for v in expr.args)
        if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant):
            if isinstance(expr.value, ast.Name):
                assigned = [
                    n.value
                    for n in ast.walk(scope)
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == expr.value.id for t in n.targets)
                ]
                if assigned:
                    return all(
                        resolve(ast.Subscript(value=v, slice=expr.slice), scope, seen)
                        for v in assigned
                    )
            if isinstance(expr.value, ast.Call):
                call = expr.value
                if _name(call.func) == "iter":
                    return resolve(ast.Subscript(value=call.args[0], slice=expr.slice), scope, seen)
                if _name(call.func) == "zip" and isinstance(expr.slice.value, int):
                    return resolve(call.args[expr.slice.value], scope, seen)
            return resolve(expr.value, scope, seen)
        if isinstance(expr, ast.Call):
            name = ast.unparse(expr.func)
            root, *tail = name.split(".")
            full = ".".join([imports.get(root, root), *tail])
            local = _qualified(s.file, name)
            if full in _OWNERS or local in _OWNERS:
                return True
            if full == "re.split":
                return len(expr.args) > 1 and resolve(expr.args[1], scope, seen)
            if name.split(".")[-1] in {"_clamp_chars", "partition"}:
                value = expr.func.value if name.endswith("partition") else expr.args[0]
                return resolve(value, scope, seen)
            if name.split(".")[-1] in {"ContextBlock", "_block"}:
                bodies = [k.value for k in expr.keywords if k.arg == "body"]
                return bool(bodies) and all(resolve(v, scope, seen) for v in bodies)
            if isinstance(expr.func, ast.Attribute) and expr.func.attr == "render":
                return resolve(expr.func.value, scope, seen)
            if name.split(".")[-1] in {
                "escape",
                "one_line",
                "_one_line",
                "render_file_links",
                "wrap_outside_text",
            }:
                return bool(expr.args) and resolve(expr.args[-1], scope, seen)
            if isinstance(expr.func, ast.Attribute) and expr.func.attr == "join":
                return bool(expr.args) and resolve(expr.args[0], scope, seen)
            candidates = [
                fn
                for key, fn in functions.items()
                if key == name or key == scopes.get(scope, "") + "." + name
            ]
            if candidates:
                fn = candidates[0]
                if _qualified(s.file, scopes[fn]) in _OWNERS:
                    return True
                returns = [
                    n.value
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Return) and n.value is not None and scopes[n] == scopes[fn]
                ]
                return bool(returns) and all(resolve(v, fn, seen) for v in returns)
        return False

    node = s.node
    bodies = []
    if isinstance(node, ast.Call):
        bodies = [k.value for k in node.keywords if k.arg == "body"]
        if not bodies and _name(node.func) == "wrap_outside_text" and len(node.args) >= 3:
            bodies = [node.args[2]]
        if not bodies and _name(node.func) == "_social_post" and len(node.args) >= 2:
            bodies = [node.args[1]]
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bodies = [
            n.value
            for n in ast.walk(node)
            if isinstance(n, ast.Return) and n.value is not None and scopes[n] == scopes[node]
        ]
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        # Ordinary _BLOCKS tables contain placement metadata only. A body
        # expression embedded in a table is checked like a constructor body.
        bodies = [
            k.value
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            for k in n.keywords
            if k.arg == "body"
        ]
    return [body for body in bodies if not resolve(body, s.scope)]


def violations(surfaces: list[Surface], *, inventory: bool = False) -> list[str]:
    problems = []
    failing = set()
    for s in surfaces:
        key = s.file.removesuffix(".py").replace("/", ".") + ":" + s.function
        for body in _body_problems(s):
            failing.add(key)
            if key not in KNOWN:
                kind = "content"
                if isinstance(s.node, ast.Call):
                    label = next(
                        (
                            k.value.value
                            for k in s.node.keywords
                            if k.arg == "name" and isinstance(k.value, ast.Constant)
                        ),
                        "",
                    )
                    kind = {
                        "group_transcript": "message",
                        "profile_messages": "message",
                        "notes": "note",
                        "memos": "memo",
                        "graph": "graph",
                    }.get(label, kind)
                problems.append(f"{s.file}:{body.lineno} {s.function}: use the {kind} composer")
    if inventory:
        stale = KNOWN.keys() - failing
        problems.extend(f"stale known violation: {key}" for key in sorted(stale))
    return problems


def test_every_context_body_has_one_classified_composer() -> None:
    surfaces = [
        s
        for path in sorted((ROOT / "nora").rglob("*.py"))
        for s in discover(path.read_text(), path.relative_to(ROOT).as_posix())
    ]
    assert surfaces, "positive control: no context surfaces discovered"
    problems = violations(surfaces, inventory=True)
    assert not problems, "Parallel context composers (Consistency/Correctness):\n" + "\n".join(
        problems
    )


def test_discovery_rejects_raw_and_disguised_bodies() -> None:
    examples = (
        "from nora.domain.blocks import ContextBlock as B\ndef bad(rows):\n"
        ' return B(name="group_transcript", body=json.dumps(rows))',
        "import nora.domain.blocks as b\ndef bad(rows):\n"
        ' return b.ContextBlock(name="group_transcript", body="".join(f"{r.body}" for r in rows))',
        "from nora.domain.blocks import ContextBlock\ndef bad(block):\n"
        " return replace(block, body=escape(block.body[:20]))",
        'def bad(rows):\n return ContextBlock(name="group_transcript", body=helper(rows))\n'
        "def helper(rows):\n return json.dumps(rows)",
    )
    for source in examples:
        assert violations(discover(source, "nora/new_occasion.py")), source


def test_unrelated_edits_and_composed_passthroughs_stay_green() -> None:
    source = (
        "from nora.domain.blocks import ContextBlock, render_messages as messages\n"
        "def good(rows):\n unrelated = 42\n text = messages(rows)\n"
        " return ContextBlock(name='group_transcript', body='Header\\n' + text)\n"
    )
    surfaces = discover(source, "nora/new_occasion.py")
    assert surfaces
    assert not violations(surfaces)


def test_registry_owners_exist() -> None:
    definitions = set()
    for path in (ROOT / "nora").rglob("*.py"):
        file = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text())
        definitions.update(
            _qualified(file, scope)
            for node, scope in _scopes(tree).items()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    assert _OWNERS
    assert not _OWNERS - definitions, sorted(_OWNERS - definitions)


def test_known_violation_becomes_stale_when_repaired() -> None:
    file = "nora/agent/reaction.py"
    source = (
        "from nora.domain.blocks import render_messages\n"
        "def render_recent(rows):\n return render_messages(rows)\n"
    )
    problems = violations(discover(source, file), inventory=True)
    assert "stale known violation: nora.agent.reaction:render_recent" in problems
