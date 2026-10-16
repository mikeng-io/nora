"""The authored framing text around the blocks is built in ONE typed place:
`nora.domain.framing`.

Fire-turn header paragraphs, the summons' head and the tag prefill's clock
line, `<context_header>` message and closing line are the same kind of text.
Nothing but this file stops another from being written inline, escaping its
values its own way.

Four rules, each with a positive control (a synthetic offender it must catch)
and a negative control (the real tree it must pass):

1. **The table.** Every kind of fire `Event` built anywhere in `nora/` has
   exactly one header builder in `framing.FIRE_HEADERS`; the construction site
   renders that builder and no other, and writes no inline text for its header
   or its body. A kind with no builder, a builder shared by two kinds, an `Event`
   whose header is a lambda returning an f-string, all fail.
2. **One typed builder per piece.** `framing.FRAMING` lists exactly the
   builders the module defines, each a frozen dataclass, each referenced from
   somewhere in `nora/`, so a builder cannot exist outside the table, a row
   cannot outlive its builder, and none is dead.
3. **One function for every value.** In `framing.py` every interpolation (an
   f-string placeholder, a `+` or `%` operand, a `.format` argument) is a call
   to one of the helpers `CLEAN_CALLS` NAMES (and the module defines no helper
   that list does not), a constant, or a local bound from one, and the
   person-controlled values enter through `_put`, the only function that calls
   `escape`. A raw `{self.title}`, or a call to a helper nobody listed, fails.
   Every value that is NOT escaped (`carried`, `joined`, `system`) is pinned as
   an exact (builder, expression, kind) triple in `NON_ESCAPED`, so a new one is
   a visible diff; there is no `bare` kind, and a value marked `system` or
   `carried` that a person controls is exactly what that pin makes a reviewer
   look at.
4. **No prose outside it.** In the modules that consume the text
   (`nora/app/fire.py`, `nora/domain/summons.py`, `nora/agent/prefill.py`) no
   string of five words or more is built outside `framing`: not an f-string, not a `+` or
   `%` or `.format` over literals, not a bare literal returned or assigned.
   Docstrings, `raise`, `assert` and log messages are not prompt text and are
   skipped. The few entries that remain are DATA a block carries or a label an
   attribute carries, each named in `ALLOWED` with why; a stale entry is
   refused.

**What a test like this cannot see**, so a reviewer knows what is left. None of
these is closed on purpose; each would cost more than it buys, or is a different
step's:

* prose built in a module this file does not scan: `nora/app/summons.py`'s
  `UNSENT_REPLY_FOLLOW_UP`, `nora/agent/clock.py`'s `state_time`,
  `nora/agent/factory.py`'s `_DRAFT_INSTRUCTION`, the tool-result prose in
  `nora/agent/relay.py`, `guards.py`, `surface.py` and `dream_guard.py` (the
  design note lists them with their lines);
* prose shorter than five words, and a sentence split into fragments of fewer
  than five words that are joined in a way no rule reads (`" ".join([...])`, a
  list of parts, text assembled across several statements or through variables);
  the five-word floor is where the repo's own data lines stop (the longest is
  the summons' elision note), not a claim about what a sentence is;
* rule 1 reads a construction written `Event(..., header=...)` or
  `<module>.Event(..., header=...)` and a `replace(..., header=)` call
  (`replace(..., body=)` is also how media rows are rewritten, so a body
  rewritten that way is not seen); it does not see an `Event(**kwargs)`, an
  `Event` built by a helper outside `nora/`, a header assigned after
  construction (`Event` is frozen, so only `object.__setattr__` could), or a
  `kind` that is not a string literal (that one it refuses by name);
* rule 3 reads `framing.py` only: a helper OUTSIDE it that returns text a
  builder interpolates is another module's business, and `_put`'s own
  `"carried"` and `"system"` are labels the pin lists, not proof a person did not
  write the value; whether a pinned value is really system-computed is a
  reviewer's call at the one line the pin shows.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from nora.domain import framing

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nora"
FRAMING_REL = "nora/domain/framing.py"

#: The modules that consume the framing text. Rule 4 reads these.
SCANNED = (
    "nora/app/fire.py",
    "nora/domain/summons.py",
    "nora/agent/prefill.py",
)

#: Authored text of five words or more that is still allowed to stand outside
#: `framing`, by (module, the constant or function that holds it): DATA a block
#: carries and labels an attribute carries, never a situation paragraph or an
#: instruction. Each reason is why it is not framing; a step that moves one
#: removes the row.
ALLOWED: dict[tuple[str, str], str] = {
    ("nora/domain/summons.py", "_NOT_SEEN"): (
        "the line an item shows when there is no reading, by state: facts only, "
        "inside a `social_post` block (step 4a left it a data line)"
    ),
    ("nora/domain/summons.py", "_BLOCKS"): (
        "the `source` attribute of each `social_post` block: a label for what "
        "the block is"
    ),
    ("nora/domain/summons.py", "render_summons"): (
        "the data lines INSIDE the `social_post` blocks (labels and values, two "
        "elision notes, the empty-question note); the head is `SummonsHead`"
    ),
}

#: Every value that enters framing text WITHOUT `escape`, as (builder, the
#: expression handed to `_put`, kind): the whole list, so a new one is a visible
#: diff. `joined` and `carried` are the BODY of a block that escapes it once
#: itself (`build_tag_message`); `system` is computed by code, never typed by a
#: person. Nothing reaches the model bare.
NON_ESCAPED: dict[tuple[str, str, str], str] = {
    ("RequestBody", "text", "joined"): (
        "the requester's own sentences, the body of `<tag_message>`; joined, so a "
        "non-str refuses as it did on master"
    ),
    ("ComputeBody", "self.task", "carried"): "the job's task, the body of `<tag_message>`",
    ("ComputeBody", "result.report or result.summary", "carried"): (
        "the worker's report (else its summary), in the body"
    ),
    ("ComputeBody", "doc.title", "carried"): "a read-back document's title, in the body",
    ("ComputeBody", "doc.url", "carried"): "a read-back document's link, in the body",
    ("ComputeBody", "f", "carried"): "a file the worker kept, a workspace path, in the body",
    ("ComputeBody", "self.checkpoint", "carried"): (
        "where a stopped job had got to, rendered by `checkpoint_render` as plain markdown"
    ),
    ("ComputeBody", "i", "carried"): "an id that did not read back, named missing in the body",
    ("ComputeBody", "result.url", "carried"): "the backend's link, in the body",
    ("ComputeBody", "result.error or 'no reason given'", "carried"): "the failure, in the body",
    ("LostJobBody", "self.task", "carried"): "the job's task, the body of `<tag_message>`",
    ("ContextHeader", "self.situation", "system"): (
        "the output of a header builder (or `TAG_HEADER`), already escaped where it "
        "carries a value"
    ),
    ("ClockLine", "self.stated", "system"): "`state_time` of the clock, the zone's name",
    ("SummonsHead", "self.summoned", "system"): "`age(...)`, computed from two instants",
    ("ProactiveHeader", "age(int(row.created_at.timestamp() * 1000), self.now)", "system"): (
        "`age(...)` of the decision, computed from two instants"
    ),
}

#: The helpers a placeholder may call. Anything else a builder interpolates is a
#: raw value or an unreviewed function; adding to this list is the review. Each
#: is defined in framing (`test_clean_calls_are_exactly_the_helpers_framing_defines`).
CLEAN_CALLS: frozenset[str] = frozenset({
    "_put", "_figure", "stamp", "_name", "_repeat", "_gate_note", "_utc_minute",
    "_asked", "_repeats", "_request", "_decided", "_about", "_outcome", "_documents", "len",
})

_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_LOG_ROOTS = {"log", "logger", "_log", "logging", "warnings"}
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
#: Prose is a sentence, and a data line is a label and a value: five words is
#: where the repo's own data lines stop.
PROSE_WORDS = 5


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _modules() -> dict[str, ast.Module]:
    return {_rel(p): ast.parse(p.read_text(encoding="utf-8")) for p in sorted(PKG.rglob("*.py"))}


@pytest.fixture(scope="module")
def modules() -> dict[str, ast.Module]:
    return _modules()


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node in parents:
        node = parents[node]
        yield node


# =============================================================================
# rule 1: every Event kind has exactly one header builder, built at one site
# =============================================================================


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _event_sites(tree: ast.Module) -> list[ast.Call]:
    """`Event(..., header=...)` constructions: the keyword is what separates
    the domain `Event` from `asyncio.Event` and every other class so named."""
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) == "Event"
        and any(k.arg == "header" for k in node.keywords)
    ]


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _builder_rendered(header: ast.expr) -> str | None:
    """The builder a header renders: `lambda tagger, now: X(...).render()`.
    None for anything else (an inline string, a helper, a bare function)."""
    if not isinstance(header, ast.Lambda):
        return None
    body = header.body
    if not (
        isinstance(body, ast.Call) and isinstance(body.func, ast.Attribute)
        and body.func.attr == "render" and isinstance(body.func.value, ast.Call)
        and isinstance(body.func.value.func, ast.Name)
    ):
        return None
    return body.func.value.func.id


def _is_inline_text(node: ast.expr) -> bool:
    return isinstance(node, (ast.JoinedStr, ast.BinOp)) or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    )


def event_findings(modules: dict[str, ast.Module]) -> tuple[dict[str, list[str]], list[str]]:
    """(kind -> the builders its construction sites render, problems found)."""
    found: dict[str, list[str]] = {}
    problems: list[str] = []
    for rel, tree in modules.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call) and _callee(node) == "replace"
                and any(k.arg == "header" for k in node.keywords)
            ):
                problems.append(
                    f"{rel}:{node.lineno}: an `Event` is rewritten with `replace(...)`; "
                    "build it with its builder at the construction site"
                )
        for site in _event_sites(tree):
            where = f"{rel}:{site.lineno}"
            kind = _keyword(site, "kind")
            if not (isinstance(kind, ast.Constant) and isinstance(kind.value, str)):
                problems.append(f"{where}: `kind` must be a string literal")
                continue
            header = _keyword(site, "header")
            assert header is not None
            builder = _builder_rendered(header)
            if builder is None:
                problems.append(
                    f"{where}: the header of kind {kind.value!r} is not "
                    "`lambda tagger, now: <Builder>(...).render()`; write the "
                    "text in nora/domain/framing.py"
                )
                continue
            found.setdefault(kind.value, []).append(builder)
            body = _keyword(site, "body")
            if body is not None and _is_inline_text(body):
                problems.append(f"{where}: the body of kind {kind.value!r} is built inline")
    return found, problems


def test_every_fire_kind_has_exactly_one_header_builder(modules):
    found, problems = event_findings(modules)
    assert not problems, problems
    table = {kind: cls.__name__ for kind, cls in framing.FIRE_HEADERS.items()}
    built = dict(found)
    assert set(built) == set(table), {
        "built but not in the table": sorted(set(built) - set(table)),
        "in the table but never built": sorted(set(table) - set(built)),
    }
    for kind, builders in built.items():
        assert builders == [table[kind]], (
            f"kind {kind!r} is built {len(builders)} time(s) rendering {builders}; "
            f"the table says {table[kind]}"
        )


def test_the_table_gives_every_kind_its_own_builder():
    classes = list(framing.FIRE_HEADERS.values())
    assert len(set(classes)) == len(classes), "two kinds share one header builder"
    for kind, cls in framing.FIRE_HEADERS.items():
        assert cls.__name__ in framing.FRAMING, f"{kind}: {cls.__name__} is not in FRAMING"
        assert callable(getattr(cls, "render", None)), f"{kind}: no render()"


def _event_source(header: str, kind: str = "reminder", body: str = "''") -> ast.Module:
    return ast.parse(
        "def occasion(rows):\n"
        f"    return Event(kind={kind!r}, body={body}, header={header})\n"
    )


@pytest.mark.parametrize("header", [
    "lambda tagger, now: f'You are in a group chat {tagger.name}'",
    "lambda tagger, now: 'You are in a group chat'",
    "_inline_header",
    "lambda tagger, now: _helper(rows)",
    "lambda tagger, now: ReminderHeader(rows, tagger)",
], ids=["f-string", "literal", "function", "helper-call", "forgot-render"])
def test_an_event_whose_header_is_written_inline_is_caught(header):
    _found, problems = event_findings({"nora/app/x.py": _event_source(header)})
    assert problems


def test_an_event_whose_body_is_built_inline_is_caught():
    _found, problems = event_findings({"nora/app/x.py": _event_source(
        "lambda tagger, now: ReminderHeader(rows, tagger).render()",
        body="f'(asked earlier) {job.task}'",
    )})
    assert problems and "body" in problems[0]


def test_an_event_that_renders_a_typed_builder_is_clean():
    found, problems = event_findings({"nora/app/x.py": _event_source(
        "lambda tagger, now: ReminderHeader(tuple(rows), tagger).render()",
        body="RequestBody(()).render()",
    )})
    assert not problems and found == {"reminder": ["ReminderHeader"]}


def test_a_kind_built_with_the_wrong_builder_is_caught(modules):
    swapped = {"nora/app/x.py": _event_source(
        "lambda tagger, now: ProactiveHeader(tagger).render()", kind="reminder")}
    found, _problems = event_findings(swapped)
    assert found["reminder"] != [framing.FIRE_HEADERS["reminder"].__name__]


@pytest.mark.parametrize("source", [
    "def o(r):\n    return events.Event(kind='reminder', body='', "
    "header=lambda t, n: f'Earlier {t.name} asked you')\n",
    "def o(ev):\n    return dataclasses.replace(ev, header=lambda t, n: 'x')\n",
    "def o(ev):\n    return replace(ev, header=lambda t, n: f'(asked) {t}')\n",
], ids=["module-attribute", "replace-header", "bare-replace-header"])
def test_the_two_other_spellings_are_caught(source):
    _found, problems = event_findings({"nora/app/x.py": ast.parse(source)})
    assert problems


def test_the_discovery_sees_every_known_construction_site(modules):
    """A zero from an instrument that cannot see is not evidence."""
    found, _problems = event_findings(modules)
    assert set(found) >= {
        "reminder", "proactive", "compute", "relay", "compute_lost",
        "participation",
    }


# =============================================================================
# rule 2: one typed builder per piece, declared and used
# =============================================================================


def _defined_builders(tree: ast.Module) -> set[str]:
    return {
        node.name for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name != "Header"
        and any(isinstance(item, ast.FunctionDef) and item.name == "render"
                for item in node.body)
    }


def test_the_table_lists_exactly_the_builders_the_module_defines():
    defined = _defined_builders(_parse(FRAMING_REL))
    assert set(framing.FRAMING) == defined, {
        "defined, not in FRAMING": sorted(defined - set(framing.FRAMING)),
        "in FRAMING, not defined": sorted(set(framing.FRAMING) - defined),
    }


def test_every_builder_is_a_frozen_dataclass_with_typed_fields():
    for name, cls in framing.FRAMING.items():
        assert dataclasses.is_dataclass(cls), f"{name} is not a dataclass"
        assert cls.__dataclass_params__.frozen, f"{name} is not frozen"  # type: ignore[attr-defined]
        fields = dataclasses.fields(cls)
        assert all(f.type not in ("", None) for f in fields), f"{name}: an untyped field"
        assert not any(f.type in ("dict", "Any", "object") for f in fields), (
            f"{name}: a loose field; inputs are typed, not a dict or an Any"
        )


def real_uses(modules: dict[str, ast.Module]) -> dict[str, int]:
    """How often each builder is NAMED outside the two tables that list them:
    a call site, or another builder's body. The `FRAMING` tuple naming every
    class is not a use, and counting it made this test pass for a dead builder."""
    uses = dict.fromkeys(framing.FRAMING, 0)
    for tree in modules.values():
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
                continue
            if node.id not in uses:
                continue
            in_table = any(
                isinstance(a, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(t, ast.Name) and t.id in {"FRAMING", "FIRE_HEADERS"}
                    for t in (a.targets if isinstance(a, ast.Assign) else [a.target])
                )
                for a in _ancestors(node, parents)
            )
            if not in_table:
                uses[node.id] += 1
    return uses


def test_every_builder_is_used_somewhere(modules):
    unused = sorted(name for name, n in real_uses(modules).items() if n == 0)
    assert not unused, f"builders nothing uses outside the tables: {unused}"


def test_a_builder_named_only_in_the_tables_is_not_used():
    """The positive control: a table row is not a use. Counting the `FRAMING`
    tuple as a use would mark every builder used."""
    tree = ast.parse(
        "FRAMING = {c.__name__: c for c in (RepeatClause, ReminderHeader)}\n"
        "FIRE_HEADERS = {'reminder': ReminderHeader}\n"
        "def f():\n    return ReminderHeader(1, 2)\n"
    )
    uses = real_uses({"x.py": tree})
    assert uses["ReminderHeader"] == 1 and uses["RepeatClause"] == 0


# =============================================================================
# rule 3: every value enters through `_put`
# =============================================================================


def _module_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(callables defined anywhere in the module, module-level constants)."""
    callables = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    constants: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            constants |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            constants.add(node.target.id)
    return callables, constants


class _Clean:
    """Is an expression free of a raw value? Clean means: a constant, a call to a
    function or class this module defines (its own body is checked on its own),
    `len`, a module constant (or a subscript of one), a local bound only from
    clean expressions, or a clean text built from those. A raw field
    (`self.title`), a raw parameter or a loop variable is not clean: it has to
    pass through `_put`, which is a call."""

    def __init__(
        self, callables: frozenset[str] | set[str], constants: set[str], env: dict[str, bool],
    ) -> None:
        self.callables = callables
        self.constants = constants
        self.env = env

    def __call__(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.JoinedStr):
            return all(self(v.value) for v in node.values if isinstance(v, ast.FormattedValue))
        if isinstance(node, ast.Call):
            name = _callee(node)
            if name in self.callables or name == "len":
                return True
            if (
                isinstance(node.func, ast.Attribute) and node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant) and len(node.args) == 1
            ):
                return self(node.args[0])
            return False
        if isinstance(node, ast.Name):
            return self.env.get(node.id, node.id in self.constants)
        if isinstance(node, ast.IfExp):
            return self(node.body) and self(node.orelse)
        if isinstance(node, ast.BinOp):
            return self(node.left) and self(node.right)
        if isinstance(node, (ast.GeneratorExp, ast.ListComp)):
            return self(node.elt)
        if isinstance(node, ast.Subscript):
            return isinstance(node.value, ast.Name) and node.value.id in self.constants
        if isinstance(node, ast.Tuple):
            return all(self(e) for e in node.elts)
        return False


def _is_text(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def unvetted_values(source: str, calls: frozenset[str] | set[str] = CLEAN_CALLS) -> list[str]:
    """Every interpolation in `source` that is not vetted (see `_Clean`): a call
    counts as vetted only if `calls` names it."""
    tree = ast.parse(source)
    _defined, constants = _module_names(tree)
    callables = calls
    offenders: list[str] = []
    functions = (
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for func in functions:
        env: dict[str, bool] = {}
        clean = _Clean(callables, constants, env)
        for stmt in ast.walk(func):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                    and isinstance(stmt.targets[0], ast.Name):
                name = stmt.targets[0].id
                env[name] = env.get(name, True) and clean(stmt.value)
        raised = {id(n) for r in ast.walk(func) if isinstance(r, ast.Raise) for n in ast.walk(r)}
        for node in ast.walk(func):
            if id(node) in raised:  # an exception message is not prompt text
                continue
            operands: list[ast.AST] = []
            if isinstance(node, ast.JoinedStr):
                operands = [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
            elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)) and (
                _is_text(node.left) or _is_text(node.right)
            ):
                operands = [node.right] if isinstance(node.op, ast.Mod) else [node.left, node.right]
                operands = [o for o in operands if not _is_text(o)]
                if isinstance(node.op, ast.Mod) and isinstance(node.right, ast.Tuple):
                    operands = list(node.right.elts)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "format" and isinstance(node.func.value, ast.Constant):
                operands = [*node.args, *(k.value for k in node.keywords)]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "join" and isinstance(node.func.value, ast.Constant):
                operands = [node.args[0]] if node.args else []
            for operand in operands:
                if not clean(operand):
                    offenders.append(f"{func.name}:{node.lineno}: {ast.unparse(operand)}")
    return sorted(set(offenders))


def test_every_value_in_framing_enters_through_put():
    source = (ROOT / FRAMING_REL).read_text(encoding="utf-8")
    assert unvetted_values(source) == []


_OFFENDING_FIELDS = {
    "an f-string placeholder": "def r(self):\n    return f'hello {self.title}'\n",
    "a placeholder bound from a raw field": (
        "def r(self):\n    who = self.name\n    return f'hello {who}'\n"),
    "a plus": "def r(self):\n    return 'hello ' + self.title\n",
    "a percent": "def r(self):\n    return 'hello %s' % self.title\n",
    "a format call": "def r(self):\n    return 'hello {}'.format(self.title)\n",
    "a join of raw values": "def r(self):\n    return '\\n'.join(self.texts)\n",
    "a raw loop variable": (
        "def r(self):\n    return ''.join(f'- {row.text}' for row in self.rows)\n"),
    "a float with a spec": "def r(self):\n    return f'{self.score:.2f}'\n",
    "a bare number": "def r(self):\n    return f'{self.count} more'\n",
}


@pytest.mark.parametrize("source", list(_OFFENDING_FIELDS.values()), ids=list(_OFFENDING_FIELDS))
def test_a_raw_value_in_a_header_is_caught(source):
    assert unvetted_values(source)


@pytest.mark.parametrize("source", [
    "def _raw(v):\n    return v\ndef r(self):\n    return f'hello {_raw(self.title)}'\n",
    "def r(self):\n    return f'hello {_figure(self.title)}'\n",
    "def _helper(self):\n    return self.title\ndef r(self):\n    return f'x {self._helper()}'\n",
], ids=["unlisted-helper", "figure-is-listed-but-the-guard-is-runtime", "unlisted-method"])
def test_a_bypass_through_a_helper_nobody_listed_is_caught(source):
    flagged = unvetted_values(source)
    # `_figure` IS listed (it is how a number enters); a string through it is
    # refused at runtime by `_figure` itself, which the next test pins.
    assert flagged or "_figure(self.title)" in source


def test_clean_calls_are_exactly_the_helpers_framing_defines():
    tree = _parse(FRAMING_REL)
    defined = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    } - {"render"}
    assert defined == set(CLEAN_CALLS) - {"len"}, {
        "defined, not listed (a helper nobody reviewed)": sorted(defined - set(CLEAN_CALLS)),
        "listed, not defined": sorted(set(CLEAN_CALLS) - {"len"} - defined),
    }


def test_a_value_through_put_or_a_module_function_is_not_flagged():
    clean = (
        "RATES = {'a': 'x'}\n"
        "def _put(v, how='escaped'):\n    return str(v)\n"
        "def stamp(m):\n    return str(m)\n"
        "def _figure(v, spec=''):\n    return format(v, spec)\n"
        "def r(self):\n"
        "    unit = RATES['a']\n"
        "    more = 'last' if len(self.rows) == 1 else f'{_figure(self.n)} more'\n"
        "    return f'hello {_put(self.title)} {stamp(self.at)} {unit} {more} {self._m()}' "
        "+ _put(self.x)\n"
        "def _m(self):\n    return ''.join(f'- {stamp(r.at)}' for r in self.rows)\n"
    )
    assert unvetted_values(clean, {"_put", "stamp", "_figure", "_m", "len"}) == []


def test_escape_is_called_by_put_and_nowhere_else_in_framing():
    tree = _parse(FRAMING_REL)
    parents = _parents(tree)
    callers = {
        next((a.name for a in _ancestors(node, parents)
              if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))), "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"escape", "escape_attr"}
    }
    assert callers == {"_put"}


def _put_calls(tree: ast.Module) -> list[tuple[str, str, ast.Call]]:
    """(enclosing class, how, call) for every `_put(...)`."""
    parents = _parents(tree)
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_put"
        ):
            cls = next((a.name for a in _ancestors(node, parents) if isinstance(a, ast.ClassDef)),
                       "<module>")
            how = "escaped"
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                how = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "how" and isinstance(kw.value, ast.Constant):
                    how = str(kw.value.value)
            out.append((cls, how, node))
    return out


def test_the_values_not_escaped_are_exactly_the_recorded_ones():
    found = {
        (cls, ast.unparse(call.args[0]), how)
        for cls, how, call in _put_calls(_parse(FRAMING_REL)) if how != "escaped"
    }
    assert found == set(NON_ESCAPED), {
        "new (a person's value reaching the model without escape?)":
            sorted(found - set(NON_ESCAPED)),
        "gone (record the change)": sorted(set(NON_ESCAPED) - found),
    }
    for key, reason in NON_ESCAPED.items():
        assert len(reason.split()) >= 4, f"{key}: say why"


def test_a_new_non_escaped_kind_is_seen():
    tree = ast.parse(
        "class B:\n    def render(self):\n        return f'x {_put(self.who, \"system\")}'\n")
    calls = _put_calls(tree)
    assert [(c, ast.unparse(n.args[0]), h) for c, h, n in calls] == [("B", "self.who", "system")]


def test_the_number_helper_refuses_a_string():
    with pytest.raises(TypeError):
        framing._figure("a title")  # type: ignore[arg-type]


def test_every_put_names_a_known_entry():
    for _cls, how, call in _put_calls(_parse(FRAMING_REL)):
        assert how in {"escaped", "carried", "joined", "system"}, ast.unparse(call)


# =============================================================================
# rule 4: no prose outside framing
# =============================================================================


def _holder(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """The function a literal sits in, else the module-level name it is
    assigned to, else `<module>`."""
    chain = list(_ancestors(node, parents))
    for a in chain:
        if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return a.name
    for a in chain:
        if isinstance(a, ast.Assign) and isinstance(a.targets[0], ast.Name):
            return a.targets[0].id
        if isinstance(a, ast.AnnAssign) and isinstance(a.target, ast.Name):
            return a.target.id
    return "<module>"


def _not_prompt_text(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """A docstring or bare expression, a `raise` or `assert` message, a log call."""
    for a in _ancestors(node, parents):
        if isinstance(a, (ast.Raise, ast.Assert)):
            return True
        if isinstance(a, ast.Call):
            root: ast.AST = a.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in _LOG_ROOTS:
                return True
            if isinstance(a.func, ast.Attribute) and a.func.attr in _LOG_METHODS:
                return True
    return isinstance(parents.get(node), ast.Expr)


def _text_roots(tree: ast.Module, parents: dict[ast.AST, ast.AST]) -> list[tuple[ast.AST, str]]:
    """Each piece of built text once, with its literal words joined: a string
    constant, an f-string (its literal fragments together), or a `+` chain over
    either (every literal in the chain together)."""
    roots: list[tuple[ast.AST, str]] = []

    def literals(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            return [v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return [*literals(node.left), *literals(node.right)]
        return []

    for node in ast.walk(tree):
        is_text = (
            isinstance(node, (ast.JoinedStr, ast.Constant, ast.BinOp))
            and (not isinstance(node, ast.Constant) or isinstance(node.value, str))
            and (not isinstance(node, ast.BinOp) or isinstance(node.op, (ast.Add, ast.Mod)))
        )
        if not is_text:
            continue
        parent = parents.get(node)
        if isinstance(parent, (ast.JoinedStr, ast.FormattedValue)) or (
            isinstance(parent, ast.BinOp) and isinstance(parent.op, (ast.Add, ast.Mod))
            and isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp))
        ):
            continue
        parts = literals(node)
        if parts:
            roots.append((node, " ".join(parts)))
    return roots


def prose_in(source: str, rel: str = "x.py") -> set[tuple[str, str, str]]:
    """(module, holder, first words) for every piece of built prompt-shaped text
    of PROSE_WORDS words or more that is not a docstring, a raise, an assert or a
    log message."""
    tree = ast.parse(source)
    parents = _parents(tree)
    found: set[tuple[str, str, str]] = set()
    for node, text in _text_roots(tree, parents):
        if len(_WORD.findall(text)) < PROSE_WORDS or _not_prompt_text(node, parents):
            continue
        found.add((rel, _holder(node, parents), " ".join(text.split())[:40]))
    return found


def prose_outside_framing(modules: dict[str, ast.Module]) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for rel in SCANNED:
        source = (ROOT / rel).read_text(encoding="utf-8")
        found |= prose_in(source, rel)
    return found


def test_no_prose_is_built_outside_framing_but_the_allowed_data(modules):
    offenders = sorted(
        (rel, holder, head) for rel, holder, head in prose_outside_framing(modules)
        if (rel, holder) not in ALLOWED
    )
    assert not offenders, (
        "authored text of five words or more is built outside nora/domain/framing.py: "
        f"{offenders}. Write a typed builder there (and give it its golden), or, if "
        "it is DATA a block carries, name it in ALLOWED with why."
    )


def test_every_allowed_entry_still_holds_prose(modules):
    holders = {(rel, holder) for rel, holder, _ in prose_outside_framing(modules)}
    stale = sorted(set(ALLOWED) - holders)
    assert not stale, f"{stale} no longer hold prose; delete them from ALLOWED"
    for key, reason in ALLOWED.items():
        assert len(reason.split()) >= 12, f"{key}: a reason under twelve words is a label"


_PROSE_OFFENDERS = {
    "an f-string header (the shape _fire_header had)": (
        "def _fire_header(rows, tagger):\n"
        "    return ('You are in a group chat you have been reading all along. '\n"
        "            f'Earlier, {tagger.name} asked you to bring something back up.')\n"
    ),
    "a bare literal returned": (
        "def _participation_header():\n"
        "    return 'The conversation has gone quiet, and this is the offer of the floor.'\n"
    ),
    "a module-level constant": (
        "TAG_HEADER = 'You do not speak unless somebody calls you, and somebody just did.'\n"
    ),
    "a plus chain of short fragments": (
        "def r(who):\n"
        "    return 'Nobody has ' + 'called you. ' + who + ' asked you to ' + 'come back'\n"
    ),
    "a percent format": (
        "def r(who):\n    return 'Earlier, %s asked you to bring it up again' % who\n"),
    "a format call": (
        "def r(who):\n    return 'Earlier, {} asked you to bring it up again'.format(who)\n"),
    "an assigned f-string returned later": (
        "def r(who):\n"
        "    text = f'Earlier, {who} asked you to bring it up again'\n    return text\n"
    ),
    "a literal inside a call": (
        "def r(m):\n"
        "    return m('assistant', 'Understood. I will read the conversation and reply.')\n"
    ),
}


@pytest.mark.parametrize("source", list(_PROSE_OFFENDERS.values()), ids=list(_PROSE_OFFENDERS))
def test_prose_built_outside_framing_is_caught(source):
    assert prose_in(source)


@pytest.mark.parametrize("source", [
    "def r(rows):\n    return ReminderHeader(tuple(rows), None).render()\n",
    "def r(n):\n    raise KeyError(f'no event factory registered for kind={n!r} at all')\n",
    "def r(n):\n    log.error('%s turn not fired: %s is no longer in identity.toml', n, n)\n",
    "def r(n):\n    log.warning(f'fire turn {n} recovered a reply written without send_reply')\n",
    "def r(n):\n    '''A docstring of well over five words that says what it does.'''\n"
    "    return n\n",
    "def r(n):\n    return f'posted by {n} just now'\n",
    "def r(a, b):\n    return f'{a}, {b} ago: {a}'\n",
    "def r():\n    assert x, 'a message of more than five words for a failed assertion'\n",
], ids=["builder", "raise", "log-percent", "log-fstring", "docstring", "short-data-line",
        "no-words", "assert"])
def test_the_rule_does_not_flag_what_is_not_prompt_prose(source):
    assert prose_in(source) == set()


def test_the_scan_reads_the_files_that_carried_the_text():
    """A zero from a scan that read nothing is not evidence: the allowed data
    lines are known to be there, so the scan must find them."""
    found = {(rel, holder) for rel, holder, _ in prose_outside_framing({})}
    assert ("nora/domain/summons.py", "render_summons") in found
    for rel in SCANNED:
        assert (ROOT / rel).exists(), rel
