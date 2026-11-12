"""AST view of a tool's callable for the surface-policy checks: after the
area split the closure lives in `nora/<area>/adapters/tools*.py` as
a generated factory plus `__` steps; `_tool_view` rebuilds the single nested
shape the checks were written against. Shared by
`tests/test_tool_surface_policy.py` and `tests/unit/test_generate_image_description.py`.
"""

import ast
import copy
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def _tool_source_paths() -> list[Path]:
    """Every module that carries tool code after the area split:"""
    paths = [_ROOT / "nora" / "agent" / "runtime_tools.py"]
    # `tool_deps.py` carries the shared payloads (`notes_off`, `memos_off`,
    # `subagent_read_notes`, `_online_states`) that used to sit in
    # `build_tool_specs`'s own body: scanned for dict constants only.
    paths.append(_ROOT / "nora" / "agent" / "tool_deps.py")
    paths += sorted((_ROOT / "nora" / "agent" / "toolkit").rglob("*.py"))
    for area in sorted((_ROOT / "nora").iterdir()):
        adapters = area / "adapters"
        if not adapters.is_dir():
            continue
        paths += sorted(adapters.glob("tools.py"))
        # `adapters/tools/__init__.py` is included: each multi-module area's
        # `build()` and `_spec` calls live there. Session-verb modules
        # (`SessionDeps`) were never in this scan's scope at base.
        paths += [
            p for p in sorted((adapters / "tools").rglob("*.py"))
            if "SessionDeps" not in p.read_text()
        ]
    return paths


_TOOL_SOURCES: dict[Path, tuple[str, ast.Module]] = {
    p: (p.read_text(), ast.parse(p.read_text())) for p in _tool_source_paths()
}


def _spec_targets() -> dict[str, tuple[Path, str]]:
    """tool name -> (file, its callable's name), from every `build()`'s"""
    out: dict[str, tuple[Path, str]] = {}
    for path, (src, _) in _TOOL_SOURCES.items():
        for m in re.finditer(r'_spec\("([^"]+)",\s*([a-z_]+)', src):
            out[m.group(1)] = (path, m.group(2))
    return out


def _func_docs(fname: str) -> list[tuple[Path, str]]:
    """Every (file, docstring) pair whose source declares a def named"""
    out: list[tuple[Path, str]] = []
    for path, (_, tree) in _TOOL_SOURCES.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fname:
                doc = ast.get_docstring(node) or ""
                if len(doc.strip()) >= 40:
                    out.append((path, doc))
    return out


def _dispatch_call(stmt: ast.stmt) -> str | None:
    """The `k_, f_, v_ = await _tool__step(c)` chunk-dispatch line a generated"""
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Tuple)
        and [getattr(e, "id", None) for e in stmt.targets[0].elts]
        in (["k_", "f_", "v_"], ["k_", "_", "v_"])
    ):
        v = stmt.value
        if isinstance(v, ast.Await):
            v = v.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
            return v.func.id
    return None


def _tail_call(stmt: ast.stmt) -> str | None:
    """A `return (await) _X__<step>(c)` chain call — the split's own seam."""
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, (ast.Call, ast.Await)):
        v = stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value
        if (
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Name)
            and len(v.args) == 1
            and isinstance(v.args[0], ast.Name)
            and v.args[0].id == "c"
            and "__" in v.func.id
        ):
            return v.func.id
    return None


def _dispatch_continuation(stmt: ast.stmt) -> bool:
    """The `if k_: return v_` / `if f_ == "break"|"continue":` lines that"""
    if not isinstance(stmt, ast.If):
        return False
    t = stmt.test
    if isinstance(t, ast.Name) and t.id == "k_":
        return True
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "f_"
        and len(t.ops) == 1
        and isinstance(t.ops[0], ast.Eq)
        and len(t.comparators) == 1
        and isinstance(t.comparators[0], ast.Constant)
        and t.comparators[0].value in ("break", "continue")
    )


class _Splice(ast.NodeTransformer):
    """Inline each `_X__<step>` chunk: its dispatch line plus the `if k_`/`if f_`
    continuations are replaced by the chunk's own statements, recursively —
    restoring the unsplit statement lists the source checks below read."""

    def __init__(self, helpers: dict[str, ast.AST]) -> None:
        self.helpers = helpers

    def _fix(self, stmts: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        i = 0
        while i < len(stmts):
            call = _dispatch_call(stmts[i])
            if call is not None and call in self.helpers:
                j = i + 1
                while j < len(stmts) and _dispatch_continuation(stmts[j]):
                    j += 1
                body = copy.deepcopy(self.helpers[call]).body
                out += [self.visit(s) for s in self._fix(body)]
                i = j
                continue
            tail = _tail_call(stmts[i])
            if tail is not None and tail in self.helpers:
                body = copy.deepcopy(self.helpers[tail]).body
                out += [self.visit(s) for s in self._fix(body)]
                i += 1
                continue
            out.append(self.visit(stmts[i]))
            i += 1
        return out

    def _lists(self, node: ast.AST, *fields: str) -> ast.AST:
        self.generic_visit(node)
        for field in fields:
            setattr(node, field, self._fix(getattr(node, field)))
        return node

    def visit_If(self, node):
        return self._lists(node, "body", "orelse")

    def visit_For(self, node):
        return self._lists(node, "body", "orelse")

    def visit_AsyncFor(self, node):
        return self._lists(node, "body", "orelse")

    def visit_While(self, node):
        return self._lists(node, "body", "orelse")

    def visit_With(self, node):
        return self._lists(node, "body")

    def visit_AsyncWith(self, node):
        return self._lists(node, "body")

    def visit_ExceptHandler(self, node):
        return self._lists(node, "body")

    def visit_Try(self, node):
        return self._lists(node, "body", "orelse", "finalbody")

    def visit_TryStar(self, node):
        return self._lists(node, "body", "orelse", "finalbody")

    def visit_FunctionDef(self, node):
        return self._lists(node, "body")

    def visit_AsyncFunctionDef(self, node):
        return self._lists(node, "body")


class _Unwrap(ast.NodeTransformer):
    """Fold the generated `c.` namespace and `(k_, f_, v_)` sentinel returns
    back to the plain-local shape the source checks were written against."""

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "c":
            return ast.Name(id=node.attr, ctx=node.ctx)
        return node

    def visit_BoolOp(self, node):
        node = self.generic_visit(node)
        # `(c.__dict__.__setitem__("x", v) or c.x)` — the lifted walrus.
        if isinstance(node.op, ast.Or) and len(node.values) == 2:
            a, b = node.values
            if (
                isinstance(a, ast.Call)
                and isinstance(a.func, ast.Attribute)
                and a.func.attr == "__setitem__"
                and isinstance(a.func.value, ast.Attribute)
                and a.func.value.attr == "__dict__"
                and isinstance(a.func.value.value, ast.Name)
                and a.func.value.value.id == "c"
                and len(a.args) == 2
                and isinstance(a.args[0], ast.Constant)
                and isinstance(b, ast.Name)
                and b.id == a.args[0].value
            ):
                return ast.NamedExpr(
                    target=ast.Name(id=a.args[0].value, ctx=ast.Store()),
                    value=a.args[1],
                )
        return node

    def visit_Return(self, node):
        node = self.generic_visit(node)
        v = node.value
        if isinstance(v, ast.Tuple) and len(v.elts) == 3:
            k, f, val = v.elts
            if (
                isinstance(k, ast.Constant)
                and k.value is True
                and isinstance(f, ast.Constant)
                and f.value is None
            ):
                node.value = val
            elif isinstance(k, ast.Constant) and k.value is False:
                if isinstance(f, ast.Constant) and f.value == "break":
                    return ast.Break()
                if isinstance(f, ast.Constant) and f.value == "continue":
                    return ast.Continue()
                return ast.Pass()  # "(False, None, None)": chunk done, run on
        return node


def _desplit(node: ast.AST, helpers: dict[str, ast.AST]) -> ast.AST:
    node = _Splice(helpers).visit(copy.deepcopy(node))
    return _Unwrap().visit(node)


def _scan_dict_constants(container: ast.AST) -> dict[str, ast.Dict]:
    """name -> its `ast.Dict` literal, for every `NAME = {...}`, `NAME: T ="""
    out: dict[str, ast.Dict] = {}
    for node in ast.iter_child_nodes(container):
        target: ast.expr | None
        value: ast.expr | None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or value is None:
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "MappingProxyType"
            and value.args
            and isinstance(value.args[0], ast.Dict)
        ):
            value = value.args[0]
        if isinstance(value, ast.Dict):
            out[target.id] = value
    return out


def _tool_view(name: str) -> tuple[ast.AST, dict[str, ast.AST], dict[str, ast.Dict]]:
    """`(fn, functions, constants)` — the Level 3 view of `tool` that the"""
    path, fname = _spec_targets()[name]
    # The callable usually sits in the file that registers it; when the area split put
    # `build()` in `adapters/tools/__init__.py` and the closure in a sibling module,
    # search the sibling (preferring the file that owns `_make_<fname>` on a name
    def_defs = [
        (p, n)
        for p, (_, t) in _TOOL_SOURCES.items()
        for n in ast.walk(t)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname
    ]

    def owns_factory(p: Path) -> bool:
        _, t = _TOOL_SOURCES[p]
        return any(getattr(n, "name", "") == f"_make_{fname}" for n in ast.iter_child_nodes(t))

    def_path, inner = sorted(def_defs, key=lambda pd: (pd[0] != path, not owns_factory(pd[0])))[0]
    _, tree = _TOOL_SOURCES[def_path]
    file_defs = {
        n.name: n
        for n in ast.iter_child_nodes(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    inner_defs = {
        c.name: c
        for f in file_defs.values()
        for c in ast.iter_child_nodes(f)
        if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    fn = _desplit(inner, file_defs)

    functions = {n: _desplit(d, file_defs) for n, d in {**file_defs, **inner_defs}.items()}

    # Re-nest the closures the split lifted out: `c.attr = partial(_X, c)` (the `take`
    # hand-off), `attr = _make_Y(deps)` in the `c` namespace (`_computer_copy_in`,
    # `_held_image`), and `c.attr = <module def>` (`_made_note`). `deps.`/external
    _renest_closures(fn, functions, file_defs, fname)
    constants = _view_constants()
    return fn, functions, constants


def _renest_closures(
    fn: ast.AST,
    functions: dict[str, ast.AST],
    file_defs: dict[str, ast.AST],
    fname: str,
) -> None:
    """Re-nest the closures the split lifted out, under their `c.attr` names."""
    bound: dict[str, ast.expr] = {}
    kept: list[ast.stmt] = []
    for s in fn.body:
        if (
            isinstance(s, ast.Assign)
            and len(s.targets) == 1
            and isinstance(s.value, ast.Call)
            and isinstance(s.value.func, ast.Name)
            and s.value.func.id == "SimpleNamespace"
        ):
            bound.update({kw.arg: kw.value for kw in s.value.keywords if kw.arg})
            continue
        kept.append(s)
    fn.body = kept
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            bound[node.targets[0].id] = node.value

    injected: list[ast.AST] = []
    for attr, value in bound.items():
        source = None
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "partial"
            and value.args
            and isinstance(value.args[0], ast.Name)
        ):
            source = functions.get(value.args[0].id)
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id.startswith("_make_")
        ):
            fac = file_defs.get(value.func.id)
            if fac is not None:
                source = functions.get(
                    next(
                        (
                            c.name
                            for c in ast.iter_child_nodes(fac)
                            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ),
                        "",
                    )
                )
        elif isinstance(value, ast.Name):
            source = functions.get(value.id)
        if source is not None and getattr(source, "name", None) != fname:
            d = copy.deepcopy(source)
            d.name = attr
            d.decorator_list = []
            injected.append(d)
            functions[attr] = d

    doc = fn.body[0] if fn.body and isinstance(fn.body[0], ast.Expr) else None
    fn.body = ([doc] if doc else []) + injected + (fn.body[1:] if doc else fn.body)
    # The factories append `return None` after the last chunk dispatch (the
    # original fell through to the same implicit None); drop the marker so
    # the view reads like the source shape the checks were written against.
    if (
        fn.body
        and isinstance(fn.body[-1], ast.Return)
        and isinstance(fn.body[-1].value, ast.Constant)
        and fn.body[-1].value.value is None
    ):
        fn.body.pop()


def _view_constants() -> dict[str, ast.Dict]:
    """Every `NAME = {...}` a tool can reach: module level plus `build()`/`_make_*` bodies."""
    constants: dict[str, ast.Dict] = {}
    for _, (_, other_tree) in _TOOL_SOURCES.items():
        for n, d in _scan_dict_constants(other_tree).items():
            constants.setdefault(n, d)
        # The old scan also covered `build_tool_specs`'s OWN body-level constants
        # (`notes_off`): the equivalents now sit in `build()`/`_make_*` bodies and
        # in `ToolDeps`' own init (`__post_init__`/`_init_derived`).
        for f in ast.walk(other_tree):
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                f.name == "build" or f.name == "__post_init__"
                or f.name.startswith(("_make_", "_init"))
            ):
                for n, d in _scan_dict_constants(f).items():
                    constants.setdefault(n, d)
    return constants


def _tuple_bound_helper(
    fn: ast.AST, name: str, functions: dict[str, ast.AST],
) -> tuple[ast.AST, int, ast.Call] | None:
    found = None
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
            continue
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Tuple)):
            continue
        elts = node.targets[0].elts
        idx = next(
            (i for i, e in enumerate(elts) if isinstance(e, ast.Name) and e.id == name),
            None,
        )
        call = node.value.value if isinstance(node.value, ast.Await) else node.value
        if idx is None or not (isinstance(call, ast.Call)
                               and isinstance(call.func, ast.Name)):
            continue
        helper = functions.get(call.func.id)
        if helper is not None:
            found = (helper, idx, call)
    return found


def _named_def(root: ast.AST, name: str) -> ast.AST | None:
    return next(
        (n for n in ast.walk(root)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
        None,
    )


def _helper_closure_params(call: ast.Call, helper: ast.AST, fn: ast.AST) -> set[str]:
    args = {
        a.arg: v for a, v in zip(helper.args.args, call.args, strict=False)  # type: ignore[attr-defined]
        if isinstance(v, ast.Name)
    }
    args.update(
        {kw.arg: kw.value for kw in call.keywords if isinstance(kw.value, ast.Name)}
    )
    return {p for p, v in args.items() if _named_def(fn, v.id) is not None}
