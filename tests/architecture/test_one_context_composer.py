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
    # Module assignments follow execution order, including alias chains.
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            value = aliases.get(_name(n.value), _name(n.value))
            for target in targets:
                if isinstance(target, ast.Name) and value in factories:
                    aliases[target.id] = value
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

    parameters: dict[tuple[ast.AST, str], bool] = {}

    def reaching(name: str, scope: ast.AST, use: ast.AST) -> list[ast.AST]:
        """Last straight-line write; join all possible values at branch exits."""

        def walk(statements: list[ast.stmt], values: list[ast.AST]) -> list[ast.AST]:
            for stmt in statements:
                if stmt.lineno >= use.lineno:
                    break
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(stmt, ast.If):
                    if stmt.end_lineno is not None and use.lineno <= stmt.end_lineno:
                        arm = stmt.body if use.lineno <= stmt.body[-1].end_lineno else stmt.orelse
                        return walk(arm, values)
                    left, right = walk(stmt.body, values), walk(stmt.orelse, values)
                    if left or right:
                        values = (left or [stmt]) + (right or [stmt])
                    continue
                if isinstance(
                    stmt,
                    (ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match),
                ):
                    # Loops/exception flow cannot establish a single reaching write.
                    if any(
                        isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)
                        for n in ast.walk(stmt)
                    ) or any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == name
                        for n in ast.walk(stmt)
                    ):
                        values = [stmt]
                    continue
                targets = (
                    stmt.targets
                    if isinstance(stmt, ast.Assign)
                    else [stmt.target]
                    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign))
                    else []
                )
                for target in targets:
                    if isinstance(target, (ast.Tuple, ast.List)) and stmt.value is not None:
                        for index, item in enumerate(target.elts):
                            if isinstance(item, ast.Name) and item.id == name:
                                values = [
                                    ast.copy_location(
                                        ast.Subscript(value=stmt.value, slice=ast.Constant(index)),
                                        stmt,
                                    )
                                ]
                if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                    if isinstance(stmt, ast.AugAssign):
                        values = [*values, stmt.value]
                    elif stmt.value is not None:
                        values = [stmt.value]
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    call = stmt.value
                    if (
                        isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == name
                        and call.func.attr in {"append", "extend"}
                    ):
                        values = [*values, *call.args]
            return values

        return walk(getattr(scope, "body", []), [])

    def resolve(expr: ast.AST, scope: ast.AST, seen: frozenset = frozenset()) -> bool:
        token = (id(expr), id(scope))
        if token in seen:
            return False
        seen = seen | {token}
        if isinstance(expr, ast.Constant):
            return True
        if isinstance(expr, ast.Name):
            assignments = reaching(expr.id, scope, expr)
            if assignments:
                return all(resolve(v, scope, seen) for v in assignments)
            args = getattr(scope, "args", None)
            if args and any(
                a.arg == expr.id for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
            ):
                return parameters.get((scope, expr.id), False)
            for n in ast.walk(scope):
                if (
                    isinstance(n, ast.comprehension)
                    and isinstance(n.target, ast.Name)
                    and n.target.id == expr.id
                ):
                    return resolve(n.iter, scope, seen)
            if scope is not s.tree:
                assignments = reaching(expr.id, s.tree, s.node)
                if assignments:
                    return all(resolve(v, s.tree, seen) for v in assignments)
            if expr.id in imports:
                module, _, name = imports[expr.id].rpartition(".")
                path = ROOT / (module.replace(".", "/") + ".py")
                if path.is_file():
                    for stmt in ast.parse(path.read_text()).body:
                        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                            targets = (
                                stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                            )
                            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                                try:
                                    ast.literal_eval(stmt.value)
                                except (ValueError, TypeError):
                                    return False
                                return True
            return False
        if isinstance(expr, ast.Attribute):
            return False
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
                assigned = reaching(expr.value.id, scope, expr)
                if assigned:
                    return all(
                        resolve(
                            ast.copy_location(ast.Subscript(value=v, slice=expr.slice), expr),
                            scope,
                            seen,
                        )
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
                args = [*fn.args.posonlyargs, *fn.args.args]
                bound = {
                    arg.arg: resolve(value, scope, seen)
                    for arg, value in zip(args, expr.args, strict=False)
                }
                bound.update(
                    {
                        k.arg: resolve(k.value, scope, seen)
                        for k in expr.keywords
                        if k.arg is not None
                    }
                )
                defaults = dict(
                    zip(
                        [a.arg for a in args][-len(fn.args.defaults) :] if fn.args.defaults else [],
                        fn.args.defaults,
                        strict=True,
                    )
                )
                defaults.update(
                    {
                        a.arg: v
                        for a, v in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True)
                        if v is not None
                    }
                )
                saved = parameters.copy()
                for arg in [*args, *fn.args.kwonlyargs]:
                    parameters[fn, arg.arg] = bound.get(
                        arg.arg,
                        resolve(defaults[arg.arg], s.tree, seen) if arg.arg in defaults else False,
                    )
                try:
                    return bool(returns) and all(resolve(v, fn, seen) for v in returns)
                finally:
                    parameters.clear()
                    parameters.update(saved)
        return False

    node = s.node
    bodies = []
    if isinstance(node, ast.Call):
        bodies = [k.value for k in node.keywords if k.arg == "body"]
        if (
            not bodies
            and _name(node.func) != "assemble"
            and any(k.arg is None for k in node.keywords)
        ):
            # Dynamic keyword construction is not proof of a composed body.
            bodies.append(node)
        if not bodies:
            for key, fn in functions.items():
                if key.split(".")[-1] == _name(node.func):
                    for index, arg in enumerate([*fn.args.posonlyargs, *fn.args.args]):
                        if arg.arg == "body" and index < len(node.args):
                            bodies.append(node.args[index])
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
        bodies.extend(
            value
            for n in ast.walk(node)
            if isinstance(n, ast.Dict)
            for key, value in zip(n.keys, n.values, strict=True)
            if isinstance(key, ast.Constant) and key.value == "body"
        )

    def forwarded(body: ast.AST) -> bool:
        # Transparent block factories are checked at their discovered call sites.
        if not (isinstance(s.node, ast.Call) and _name(s.node.func) == "ContextBlock"):
            return False
        if not isinstance(s.scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        args = [*s.scope.args.args, *s.scope.args.kwonlyargs]
        if not any(a.arg == "body" for a in args) and s.scope.name not in {
            "wrap_outside_text",
            "_social_post",
        }:
            return False
        value = body
        if isinstance(value, ast.Call) and _name(value.func) == "escape" and len(value.args) == 1:
            value = value.args[0]
        return (
            isinstance(value, ast.Name)
            and any(a.arg == value.id for a in args)
            and not reaching(value.id, s.scope, value)
        )

    return [body for body in bodies if not forwarded(body) and not resolve(body, s.scope)]


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
                problems.append(
                    f"{s.file}:{body.lineno} {s.function}: cannot prove this body comes "
                    f"from a registered composer; use the {kind} composer or add a "
                    "known-violation entry"
                )
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


def test_helper_arguments_keep_their_provenance() -> None:
    prefix = "def frame(text: str):\n return f'Recent messages: {text}'\n"
    bad = prefix + "def bad(rows):\n return ContextBlock(body=frame(json.dumps(rows)))\n"
    good = (
        "from nora.domain.blocks import render_messages\n"
        + prefix
        + "def good(rows):\n return ContextBlock(body=frame(render_messages(rows)))\n"
    )
    assert violations(discover(bad, "nora/new_occasion.py"))
    assert not violations(discover(good, "nora/new_occasion.py"))
    keyword = bad.replace("frame(json.dumps(rows))", "frame(text=json.dumps(rows))")
    assert violations(discover(keyword, "nora/new_occasion.py"))
    factory = (
        "def frame(body):\n body = json.dumps(rows)\n return ContextBlock(body=body)\n"
        "def bad(rows):\n return frame(body='Header')"
    )
    assert violations(discover(factory, "nora/new_occasion.py"))


def test_record_description_is_not_a_composer() -> None:
    source = "def bad(rows):\n return ContextBlock(body='\\n'.join(r.description for r in rows))"
    assert violations(discover(source, "nora/new_occasion.py"))


def test_assignment_alias_is_discovered_and_checked() -> None:
    source = "B = ContextBlock\nC = B\ndef bad(rows):\n return C(body=json.dumps(rows))"
    surfaces = discover(source, "nora/new_occasion.py")
    assert surfaces
    assert violations(surfaces)


def test_dict_table_and_dynamic_keywords_fail_closed() -> None:
    sources = (
        "_BLOCKS = ({'name': 'group_transcript', 'body': json.dumps(rows)},)",
        "def bad(row):\n return ContextBlock(**row)",
    )
    for source in sources:
        surfaces = discover(source, "nora/new_occasion.py")
        assert surfaces
        assert violations(surfaces)


def test_only_reaching_assignments_contribute_to_body() -> None:
    source = (
        "from nora.domain.blocks import render_messages\n"
        "def good(rows):\n text = render_messages(rows)\n"
        " block = ContextBlock(body=text)\n text = json.dumps(rows)\n return block"
    )
    surfaces = discover(source, "nora/new_occasion.py")
    assert surfaces
    assert not violations(surfaces)


def test_every_reaching_branch_must_be_compliant() -> None:
    prefix = (
        "from nora.domain.blocks import render_messages\n"
        "def case(rows, condition):\n text = json.dumps(rows)\n"
        " if condition:\n  text = render_messages(rows)\n"
    )
    bad = prefix + " return ContextBlock(body=text)"
    good = prefix + " else:\n  text = render_messages(rows)\n return ContextBlock(body=text)"
    assert violations(discover(bad, "nora/new_occasion.py"))
    assert not violations(discover(good, "nora/new_occasion.py"))
