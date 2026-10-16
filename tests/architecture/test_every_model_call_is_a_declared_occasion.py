"""Every place Nora sends a prompt to a model is a declared context-assembly
occasion, found by DISCOVERY and not by a list somebody keeps.

The rule: **every Nora agent assembles its prompt through context assembly, no
exception.** A violation is a failure. There is no list of known violations, no
debt count and no ceiling to hide one in.

A hand list of occasions (as `test_the_registry_is_exhaustive.py` reads) cannot
fail for a new occasion, which could then hand-write its whole prompt: no
`ContextBlock`, no escaping, a different id shape from the one its own tools
demand. Nothing would notice, because nothing looks for it.

This test looks. It reads source (no import of `nora`) and finds, with no list
of occasions:

  1. every module that calls a model: `create_agent`, `create_dream_agent`,
     `ask_text`, `ask_structured`, `invoke_async`, `.invoke` on a runner or
     factory, `invoke.run`, a raw `completions.create`, or a
     `.complete(system=, prompt=)`, under the name it was imported by;
  2. every class that defines `inputs`, which is how a Dreamer adaptor hands
     its prompt to a runtime that calls the model for it.

A module that builds a prompt must declare it: a module-level, non-empty
`_BLOCKS: tuple[...] = (...)` literal, AND a call to `assemble(...)` imported
from `nora.domain.blocks`, so the table's `order` is the order the model
reads. Three kinds of module need no table, each with a written reason: a
MECHANISM (the SDK or transport layer that never decides what a prompt says),
a RUNNER (it runs a prompt that another named module builds, and that
builder must itself be declared and, for `imports`, really imported) and an
EXEMPT occasion that is not a context at all (an authored prompt plus an
image). A module that is none of these and builds a prompt fails
`test_nothing_builds_a_prompt_outside_context_assembly`; the fix is to join it
to context assembly, never to widen a table below to excuse it.

**What the net cannot see**, so a reviewer knows what is left to review. None
of it is recorded as debt here: a hand-built text the net cannot see is fixed,
or covered by a purpose-built architecture test like
`test_framing_is_one_typed_place.py` (authored prompt text in one typed
module), `test_withdrawal_reaches_every_prompt.py` (a withdrawn head reaches no
prompt) or `test_one_quote_resolver.py`; never added to a list.

  - text a module hand-builds INSIDE a RUNNER, a MECHANISM or an EXEMPT. The net
    knows such a module runs a prompt, not that it also writes one. A NEW such
    text is found by a person, and gets a test of its own;
  - a model door this file does not know. Honcho's `chat` (a dialectic query
    to a memory service's own model) and image generation are not matched;
  - a model call reached only through an imported helper (the net is
    per-module, with no call graph);
  - a dead `_BLOCKS` joined by `assemble` over a list that does not come from
    it. `test_the_registry_is_exhaustive` checks rows against builds for the
    occasions it finds, not how they are joined.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nora"

#: Calls that send a prompt to a model, matched on the callee's trailing name
#: and on the name it was imported under.
_MODEL_CALLS = {
    "create_agent", "create_dream_agent", "ask_text", "ask_structured",
    "invoke_async",
}
#: Agent methods that send a prompt, matched on the trailing name alone.
_MODEL_METHODS = {"stream_async", "structured_output", "structured_output_async"}
#: A provider SDK's `.create(...)` on `<client>.<one of these>.create`: the
#: chat, messages and responses endpoints.
_SDK_ENDPOINTS = ("completions", "messages", "responses")
#: `.invoke(...)` on a receiver whose name ends in one of these (so `runner`,
#: `_runner`, `self._factory` and `agent_runner` all count).
_INVOKE_RECEIVER_ENDINGS = ("runner", "factory")

#: The SDK or transport layer. It runs whatever prompt it is handed.
MECHANISM = {
    "nora/agent/factory.py": (
        "builds the Strands agent and runs one turn; the prompt it is given is "
        "assembled by the occasion that called it, never here"
    ),
    "nora/agent/structured.py": (
        "the structured-output call wrapper, `ask_text` and `ask_structured`; "
        "it forwards a prompt a caller built, and its one caller today is the "
        "debrief client composition.py constructs"
    ),
    "nora/app/composition.py": (
        "constructs the raw gate client and the debrief's ask_text client; the "
        "text each sends is built by relevance_gate and the debrief renderer"
    ),
    "nora/infra/vision/client.py": (
        "the raw vision transport; the question it sends is authored in "
        "comprehend.py and the media rides beside it"
    ),
    "nora/subject/standing_fold.py": (
        "an eval seam that wraps the debrief client and forwards the prompt "
        "it was handed unchanged"
    ),
}

#: Not a context: an authored prompt plus media.
EXEMPT = {
    "nora/infra/vision/comprehend.py": (
        "the describe path asks an authored question per modality about one "
        "image or video, with no conversation to assemble; its summarize path "
        "sends the authored question and then the one piece of outside text, a "
        "transcript or chunk run, wrapped by `wrap_outside_text` as a typed "
        "`media_text` block (step 4c), so there is no prompt of its own to declare"
    ),
}

#: Runs a prompt that other named modules build: rel -> (builders, how).
#: `how` is `imports` (the runner imports the builder; checked) or
#: `dispatched` (a port or an argument hands the prompt in, so there is
#: nothing to import; each builder must still be declared).
RUNNER: dict[str, tuple[tuple[str, ...], str]] = {
    "nora/agent/dreamer.py": ((
        "nora/language/culture.py",
    ), "dispatched"),
    "nora/app/agent_runner.py": (("nora/agent/prefill.py",), "dispatched"),
    "nora/engagement/stages/invoke.py": (("nora/agent/prefill.py",), "dispatched"),
    "nora/engagement/pipeline.py": (("nora/agent/prefill.py",), "imports"),
    "nora/app/fire.py": (("nora/agent/prefill.py",), "imports"),
    "nora/app/summons.py": (("nora/domain/summons.py",), "imports"),
    "nora/app/cognition_agent_runner.py": (
        ("nora/language/cognition_render.py",), "dispatched",
    ),
    "nora/engagement/stages/debrief.py": (
        ("nora/engagement/debrief_render.py",), "imports",
    ),
    # The sub-agent worker's run: its prompt is the brief, built by ONE
    # formatter that walks the declared `subagent_*` blocks through
    # `ContextBlock`.
    "nora/app/subagent_runner.py": (("nora/language/subagent_render.py",), "imports"),
}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _modules() -> dict[str, ast.Module]:
    return {_rel(p): _parse(p) for p in sorted(PKG.rglob("*.py"))}


def _chain(func: ast.expr) -> list[str]:
    names: list[str] = []
    while isinstance(func, ast.Attribute):
        names.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        names.append(func.id)
    return list(reversed(names))


def _model_aliases(tree: ast.Module) -> set[str]:
    """Names this module imported a model-call function under, whatever the
    alias: `from nora.agent.structured import ask_text as at` makes `at` one."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in _MODEL_CALLS
    }


def calls_a_model(tree: ast.Module) -> bool:
    """True if the module sends a prompt to a model by any known door."""
    aliases = _model_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if not chain:
            continue
        tail = chain[-1]
        keywords = {kw.arg for kw in node.keywords}
        if tail in _MODEL_CALLS or tail in aliases or tail in _MODEL_METHODS:
            return True
        if tail == "create" and set(chain) & set(_SDK_ENDPOINTS):
            return True
        if tail == "complete" and keywords & {"system", "prompt"}:
            return True
        if (
            tail == "invoke" and len(chain) >= 2
            and chain[-2].lstrip("_").endswith(_INVOKE_RECEIVER_ENDINGS)
        ):
            return True
        # `invoke.run(...)`, the tag turn's and the summons' door to the runner
        if tail == "run" and chain[-2:] == ["invoke", "run"]:
            return True
    return False


def defines_inputs(tree: ast.Module) -> bool:
    """True if the module defines `inputs` as a function or a method, async or
    not: the door a Dreamer adaptor uses to hand its prompt to the runtime."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "inputs"
        for node in ast.walk(tree)
    )


def declares_blocks(tree: ast.Module) -> bool:
    """A module-level, non-empty `_BLOCKS: tuple = (...)`; one assigned inside
    a function, or empty, does not count."""
    return any(
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name) and node.target.id == "_BLOCKS"
        and isinstance(node.value, ast.Tuple) and node.value.elts
        for node in tree.body
    )


def _is_blocks_module(node: ast.ImportFrom) -> bool:
    """`from nora.domain.blocks import ...` or the package-relative spelling
    (`from ..domain.blocks import ...`) the agent modules use."""
    return bool(node.module) and node.module.split(".")[-2:] == ["domain", "blocks"]


def calls_assemble(tree: ast.Module) -> bool:
    """A call to `assemble` as imported from `nora.domain.blocks`. A local
    function of that name, or `ContextAssembler.assemble` reached by an
    attribute, is not the one that walks a `_BLOCKS` table."""
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and _is_blocks_module(node)
        for alias in node.names
        if alias.name == "assemble"
    }
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in aliases
        for node in ast.walk(tree)
    )


def hand_joins_blocks(tree: ast.Module) -> bool:
    """True if a `.join(...)` is handed a rendered collection of blocks: a
    `<name>.render()` call or `map(<x>.render, ...)` inside its argument. That
    is what `assemble` does and what reverting ONE of a module's several
    `assemble` calls writes, which `calls_assemble` (any call in the module)
    cannot see. A block nested inside another block's body is rendered from a
    constructor call, not from a name, so it does not match."""
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
        ):
            continue
        for arg in node.args:
            for inner in ast.walk(arg):
                if not isinstance(inner, ast.Call):
                    continue
                f = inner.func
                if (
                    isinstance(f, ast.Attribute) and f.attr == "render"
                    and isinstance(f.value, ast.Name) and not inner.args
                ):
                    return True
                if (
                    isinstance(f, ast.Name) and f.id == "map" and inner.args
                    and isinstance(inner.args[0], ast.Attribute)
                    and inner.args[0].attr == "render"
                ):
                    return True
    return False


def imports_module(tree: ast.Module, rel: str) -> bool:
    dotted = rel[: -len(".py")].replace("/", ".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == dotted or any(
                f"{node.module}.{alias.name}" == dotted for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import) and any(
            alias.name == dotted for alias in node.names
        ):
            return True
    return False


def violations(modules: dict[str, ast.Module]) -> set[tuple[str, str]]:
    """Every (module, kind) that breaks the rule right now."""
    found: set[tuple[str, str]] = set()
    builders: set[str] = set()
    for rel, tree in modules.items():
        if rel == "nora/domain/ports.py":  # the Protocol that names `inputs`
            continue
        if defines_inputs(tree):
            builders.add(rel)
        if calls_a_model(tree) and rel not in MECHANISM | EXEMPT | RUNNER:
            builders.add(rel)
    for named, _ in RUNNER.values():
        builders.update(named)
    for rel in builders:
        tree = modules[rel]
        if not declares_blocks(tree):
            found.add((rel, "undeclared"))
        elif not calls_assemble(tree):
            found.add((rel, "hand_joined"))
    for rel, tree in modules.items():
        if rel != "nora/domain/blocks.py" and hand_joins_blocks(tree):
            found.add((rel, "hand_join_call"))
    return found


@pytest.fixture(scope="module")
def modules() -> dict[str, ast.Module]:
    return _modules()


def check_the_rule(modules: dict[str, ast.Module]) -> None:
    """The rule itself: a violation is a failure. Its own test below runs it
    over a tree with a synthetic occasion added, so it cannot go vacuous."""
    found = sorted(violations(modules))
    assert not found, (
        "a module builds a model prompt outside context assembly: "
        f"{found}. Declare `_BLOCKS` and walk it with `assemble(...)` "
        "(design/context-assembly/04-every-occasion.md, «How a new occasion "
        "joins»), or, if it truly is not a context, add it to MECHANISM, "
        "RUNNER or EXEMPT with a reason. There is no list of exceptions to "
        "add it to."
    )


def test_nothing_builds_a_prompt_outside_context_assembly(modules):
    check_the_rule(modules)


@pytest.mark.parametrize("table", [MECHANISM, EXEMPT], ids=["mechanism", "exempt"])
def test_every_classification_names_a_real_module_and_a_real_reason(table, modules):
    for rel, reason in table.items():
        assert rel in modules, f"{rel} does not exist; delete its entry"
        assert len(reason.split()) >= 12, (
            f"{rel}: a reason under twelve words is a label, not a reason"
        )


def runner_problems(
    modules: dict[str, ast.Module],
    runner: dict[str, tuple[tuple[str, ...], str]],
) -> list[str]:
    """What is wrong with a RUNNER table, as messages (empty when it is sound)."""
    problems: list[str] = []
    for rel, (builders, how) in runner.items():
        if rel not in modules:
            problems.append(f"{rel} does not exist; delete its entry")
            continue
        if how not in {"imports", "dispatched"}:
            problems.append(f"{rel}: how is {how!r}")
        if not calls_a_model(modules[rel]):
            problems.append(f"{rel} no longer calls a model; it is not a RUNNER")
        if not builders:
            problems.append(f"{rel} is a RUNNER with no builder named")
        for builder in builders:
            if builder not in modules:
                problems.append(f"{rel} names {builder}, which is gone")
        if how == "imports" and builders and not any(
            imports_module(modules[rel], b) for b in builders if b in modules
        ):
            problems.append(
                f"{rel} says it imports one of {builders} and imports none; "
                "re-pointing a RUNNER at a declared module must not launder a "
                "module that builds a prompt"
            )
    return problems


def test_every_runner_names_builders_that_exist_and_are_reached(modules):
    assert runner_problems(modules, RUNNER) == []


def test_a_runner_entry_that_is_wrong_is_reported():
    """Controls for each check `runner_problems` makes, on synthetic modules,
    so none of them can be dropped without a test going red."""
    caller = ast.parse("agent = create_agent(system_prompt='x')\n")
    quiet = ast.parse("x = 1\n")
    mods = {"nora/r.py": caller, "nora/q.py": quiet, "nora/b.py": quiet}
    def one(rel, builders, how):
        return runner_problems(mods, {rel: (builders, how)})
    assert one("nora/r.py", ("nora/b.py",), "dispatched") == []
    assert one("nora/gone.py", ("nora/b.py",), "dispatched")
    assert one("nora/r.py", ("nora/b.py",), "sideways")
    assert one("nora/q.py", ("nora/b.py",), "dispatched")
    assert one("nora/r.py", (), "dispatched")
    assert one("nora/r.py", ("nora/gone.py",), "dispatched")
    assert one("nora/r.py", ("nora/b.py",), "imports")
    importing_caller = ast.parse(
        "from nora.scratch_builder import build\n"
        "agent = create_agent(system_prompt='x')\n"
    )
    assert runner_problems(
        {"nora/i.py": importing_caller, "nora/scratch_builder.py": quiet},
        {"nora/i.py": (("nora/scratch_builder.py",), "imports")},
    ) == []


def test_discovery_sees_the_known_call_sites_and_occasions(modules):
    """A zero from an instrument that cannot see is not evidence: the net
    must find what is known to be there before its silence means anything."""
    callers = {rel for rel, tree in modules.items() if calls_a_model(tree)}
    for known in (
        "nora/agent/factory.py", "nora/app/composition.py",
        "nora/ingest/relevance_gate.py", "nora/app/cognition_agent_runner.py",
        "nora/agent/reaction.py", "nora/engagement/stages/debrief.py",
    ):
        assert known in callers, f"discovery missed {known}"
    declared = {rel for rel, tree in modules.items() if declares_blocks(tree)}
    assert len(declared) >= 6
    adaptors = {rel for rel, tree in modules.items() if defines_inputs(tree)}
    assert "nora/language/culture.py" in adaptors, (
        "the `inputs` net must find culture, the one Dreamer adaptor left"
    )


def _tree(source: str) -> ast.Module:
    return ast.parse(source)


def test_the_net_catches_a_hand_built_occasion():
    """The failure this file exists for, as a synthetic module: it builds a
    prompt from f-strings and runs it through a dream agent."""
    tree = _tree(
        "def render(rows):\n"
        "    return '\\n'.join(f'[#{r.id}] {r.who}: {r.body}' for r in rows)\n"
        "async def run(factory, rows):\n"
        "    agent = factory.create_dream_agent(system_prompt='x')\n"
        "    return await factory.invoke(agent, render(rows))\n"
    )
    assert calls_a_model(tree)
    assert not declares_blocks(tree)


def test_the_rule_fails_on_a_hand_built_occasion_added_to_the_tree(modules):
    """The rule itself, not just its detectors: put a synthetic hand-built
    occasion (an f-string prompt, a model call, no table) and one that declares
    a table and joins it by hand into the real tree, and the violations name
    both, beyond whatever the real tree already holds. With no ledger to hide
    them in, that is a red test."""
    hand_built = _tree(
        "async def run(factory, rows):\n"
        "    agent = factory.create_dream_agent(system_prompt='x')\n"
        "    prompt = '\\n'.join(f'[#{r.id}] {r.who}: {r.body}' for r in rows)\n"
        "    return await factory.invoke(agent, prompt)\n"
    )
    hand_joined = _tree(
        "_BLOCKS: tuple[tuple[str, int, str], ...] = (('c', 1, 'turn'),)\n"
        "async def run(factory, parts):\n"
        "    agent = factory.create_dream_agent(system_prompt='x')\n"
        "    return await factory.invoke(agent, '\\n\\n'.join(parts))\n"
    )
    scratch = {
        **modules,
        "nora/scratch_hand_built.py": hand_built,
        "nora/scratch_hand_joined.py": hand_joined,
    }
    with pytest.raises(AssertionError, match="scratch_hand_built"):
        check_the_rule(scratch)
    assert violations(scratch) - violations(modules) == {
        ("nora/scratch_hand_built.py", "undeclared"),
        ("nora/scratch_hand_joined.py", "hand_joined"),
    }


@pytest.mark.parametrize("source", [
    "from nora.agent.structured import ask_text as at\nat(p)\n",
    "self._runner.invoke(agent, prompt)\n",
    "self._factory.invoke(agent, prompt)\n",
    "agent_runner.invoke(agent, prompt)\n",
    "invoke.run(request)\n",
], ids=["alias", "self-runner", "self-factory", "agent-runner", "invoke-run"])
def test_the_net_follows_an_alias_and_a_wider_receiver(source):
    assert calls_a_model(_tree(source))


#: Spelled out, never read off `_MODEL_CALLS`: a parametrisation over the
#: constant it pins cannot fail when a name is removed from it.
@pytest.mark.parametrize("source", [
    "create_agent(system_prompt='x')\n",
    "factory.create_agent(system_prompt='x')\n",
    "create_dream_agent(system_prompt='x')\n",
    "factory.create_dream_agent(system_prompt='x')\n",
    "ask_text(client, prompt)\n",
    "ask_structured(client, prompt)\n",
    "await invoke_async(agent, prompt)\n",
    "agent.stream_async(prompt)\n",
    "agent.structured_output(Model, prompt)\n",
    "agent.structured_output_async(Model, prompt)\n",
    "client.chat.completions.create(model='m', messages=[])\n",
    "client.messages.create(model='m', messages=[])\n",
    "client.responses.create(model='m', input='p')\n",
    "client.complete(system='s', prompt='p')\n",
], ids=[
    "create_agent", "factory.create_agent", "create_dream_agent",
    "factory.create_dream_agent", "ask_text", "ask_structured", "invoke_async",
    "stream_async", "structured_output", "structured_output_async",
    "completions.create", "messages.create", "responses.create", "complete",
])
def test_every_known_model_door_is_seen(source):
    assert calls_a_model(_tree(source))


@pytest.mark.parametrize("source", [
    "rows.create(x)\n",
    "store.messages.append(x)\n",
    "client.complete(1)\n",
    "runner.run(x)\n",
    "other.invoke(x)\n",
], ids=["plain-create", "messages-append", "complete-no-kwargs", "run", "other-invoke"])
def test_an_ordinary_call_is_not_a_model_door(source):
    assert not calls_a_model(_tree(source))


@pytest.mark.parametrize("source", [
    "class A:\n    def inputs(self, key, batch): ...\n",
    "def inputs(key, batch): ...\n",
    "class A:\n    async def inputs(self, *, key, batch): ...\n",
], ids=["sync-method", "module-function", "async-method"])
def test_the_inputs_net_finds_every_shape(source):
    assert defines_inputs(_tree(source))


def test_the_net_does_not_flag_a_declared_assembled_occasion():
    tree = _tree(
        "from nora.domain.blocks import assemble\n"
        "_BLOCKS: tuple[tuple[str, int, str], ...] = (('clock', 1, 'turn'),)\n"
        "def build(blocks):\n"
        "    return assemble(blocks)\n"
    )
    assert declares_blocks(tree) and calls_assemble(tree)


@pytest.mark.parametrize("source", [
    "_BLOCKS: tuple[tuple[str, int, str], ...] = (('c', 1, 'turn'),)\n"
    "def build(parts):\n    return '\\n\\n'.join(parts)\n",
    "_BLOCKS: tuple[tuple[str, int, str], ...] = (('c', 1, 'turn'),)\n"
    "def assemble(x):\n    return ''.join(x)\n"
    "def build(parts):\n    return assemble(parts)\n",
    "_BLOCKS: tuple[tuple[str, int, str], ...] = (('c', 1, 'turn'),)\n"
    "def build(self, r):\n    return self._ctx.assemble(request=r)\n",
    "from nora.domain.blocks import ContextBlock\n"
    "_BLOCKS: tuple[tuple[str, int, str], ...] = (('c', 1, 'turn'),)\n"
    "def build(parts):\n    return ContextBlock(name='c', body=parts)\n",
], ids=["hand-joined", "local-assemble", "method-assemble", "other-blocks-import"])
def test_a_declared_table_that_is_not_walked_by_the_real_assemble_is_caught(source):
    tree = _tree(source)
    assert declares_blocks(tree) and not calls_assemble(tree)


@pytest.mark.parametrize("source", [
    "_BLOCKS: tuple = ()\n",
    "def g():\n    _BLOCKS: tuple = (('c', 1, 'turn'),)\n",
    "_BLOCKS = (('c', 1, 'turn'),)\n",
], ids=["empty", "inside-a-function", "unannotated"])
def test_an_empty_or_nested_or_unannotated_table_is_not_a_declaration(source):
    assert not declares_blocks(_tree(source))


def test_assemble_imported_under_an_alias_or_a_relative_path_counts():
    aliased = _tree(
        "from nora.domain.blocks import assemble as walk\n"
        "def build(b):\n    return walk(b)\n"
    )
    relative = _tree(
        "from ..domain.blocks import assemble\n"
        "def build(b):\n    return assemble(b)\n"
    )
    assert calls_assemble(aliased) and calls_assemble(relative)


@pytest.mark.parametrize("source", [
    "def build(blocks):\n    return '\\n'.join(b.render() for b in blocks)\n",
    "def build(blocks):\n    return ''.join([b.render() + '\\n' for b in blocks])\n",
    "def build(blocks):\n    return '\\n'.join(map(ContextBlock.render, blocks))\n",
], ids=["genexp", "list-comp", "map-render"])
def test_a_hand_join_of_rendered_blocks_is_caught(source):
    assert hand_joins_blocks(_tree(source))


@pytest.mark.parametrize("source", [
    "def build(blocks):\n    return assemble(blocks)\n",
    "def build(rows):\n    return '\\n'.join(r.text for r in rows)\n",
    "def build(items):\n    return '\\n'.join(\n"
    "        ContextBlock(name='p', body=i).render() for i in items)\n",
], ids=["assemble", "plain-join", "nested-constructor-render"])
def test_a_join_that_is_not_of_rendered_blocks_is_not_flagged(source):
    assert not hand_joins_blocks(_tree(source))


def test_one_hand_join_beside_a_working_assemble_is_a_violation(modules):
    """The per-call gap `calls_assemble` leaves (it asks for ANY call in the
    module): a module that still calls `assemble` once and joins its other
    blocks by hand."""
    mixed = _tree(
        "from nora.domain.blocks import assemble\n"
        "_BLOCKS: tuple[tuple[str, int, str], ...] = (('c', 1, 'turn'),)\n"
        "def one(blocks):\n    return assemble(blocks)\n"
        "def two(blocks):\n    return '\\n'.join(b.render() for b in blocks)\n"
    )
    found = violations({**modules, "nora/scratch_mixed.py": mixed})
    assert ("nora/scratch_mixed.py", "hand_join_call") in found
    assert ("nora/scratch_mixed.py", "hand_joined") not in found


def test_an_adaptor_that_defines_inputs_is_a_builder_in_the_real_function(modules):
    """The wiring, not the detector: `violations` itself must treat a module
    that defines `inputs` as a builder, with no model call in it."""
    adaptor = _tree(
        "class Adaptor:\n"
        "    def inputs(self, key, batch):\n"
        "        return f'[#{batch[0].id}] {batch[0].body}'\n"
    )
    found = violations({**modules, "nora/scratch_adaptor.py": adaptor})
    assert ("nora/scratch_adaptor.py", "undeclared") in found


def test_a_runners_named_builder_is_checked_by_the_real_function(modules, monkeypatch):
    """A builder reached only through a RUNNER entry (it neither calls a model
    nor defines `inputs`) must still be declared: this is what makes
    reverting a named builder red."""
    monkeypatch.setitem(
        RUNNER, "nora/scratch_runner.py", (("nora/scratch_builder.py",), "dispatched"),
    )
    runner = _tree("agent = create_agent(system_prompt='x')\n")
    builder = _tree("def render(rows):\n    return '\\n'.join(map(str, rows))\n")
    found = violations({
        **modules, "nora/scratch_runner.py": runner, "nora/scratch_builder.py": builder,
    })
    assert ("nora/scratch_builder.py", "undeclared") in found
    assert ("nora/scratch_runner.py", "undeclared") not in found
