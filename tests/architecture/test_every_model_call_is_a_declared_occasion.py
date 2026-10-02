"""Every place Nora sends a prompt to a model is a declared context-assembly
occasion, found by DISCOVERY and not by a list somebody keeps.

The rule, from the operator (2026-10-01): **every Nora agent assembles its
prompt through context assembly, no exception.** A violation is a failure. There
is no list of known violations, no debt count and no ceiling to hide one in: an
earlier version of this file kept a PENDING ledger while the occasions were
moved over (design/context-assembly/04-every-occasion.md, steps 0 to 5); it was
empty when step 6 deleted it.

The census this replaced, `test_the_registry_is_exhaustive.py`, read a hand list
of five files, so a sixth occasion could not fail it. The Cognition session was
built after that list was written and hand-wrote its whole prompt: no
`ContextBlock`, no escaping, a different id shape from the one its own
`note_create` demands. Nothing noticed, because nothing looked for it.

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
(design/context-assembly/04-every-occasion.md, «How a new occasion joins»),
never to widen a table below to excuse it.

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
        "the structured-output call wrapper; it forwards a prompt a caller "
        "built and has no caller in nora/ today"
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
        "nora/language/proactive.py",
        "nora/language/culture.py",
        "nora/language/note_candidates.py",
        "nora/language/note_curator.py",
        "nora/language/note_merge.py",
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
        if tail in _MODEL_CALLS or tail in aliases:
            return True
        if tail == "create" and "completions" in chain:
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


def test_every_runner_names_builders_that_exist_and_are_reached(modules):
    for rel, (builders, how) in RUNNER.items():
        assert rel in modules, f"{rel} does not exist; delete its entry"
        assert how in {"imports", "dispatched"}, f"{rel}: how is {how!r}"
        assert calls_a_model(modules[rel]), (
            f"{rel} no longer calls a model; it is not a RUNNER"
        )
        assert builders, f"{rel} is a RUNNER with no builder named"
        for builder in builders:
            assert builder in modules, f"{rel} names {builder}, which is gone"
        if how == "imports":
            assert any(imports_module(modules[rel], b) for b in builders), (
                f"{rel} says it imports one of {builders} and imports none; "
                "re-pointing a RUNNER at a declared module must not launder a "
                "module that builds a prompt"
            )


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
    assert "nora/language/note_merge.py" in adaptors, (
        "the `inputs` net must find note-merge, the adaptor that has no table"
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
], ids=["alias", "self-runner", "self-factory", "agent-runner"])
def test_the_net_follows_an_alias_and_a_wider_receiver(source):
    assert calls_a_model(_tree(source))


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
], ids=["hand-joined", "local-assemble", "method-assemble"])
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
