"""Every place Nora sends a prompt to a model is a declared context-assembly
occasion, found by DISCOVERY and not by a list somebody keeps.

`design/context-assembly` declared an occasion's blocks in one module-level
`_BLOCKS` table and walked it with `nora.domain.blocks.assemble`. The census
test that enforced it, `test_the_registry_is_exhaustive.py`, read a hand list
of five files, so a sixth occasion could not fail it. The Cognition session
was built after that list was written and hand-writes its whole prompt: no
`ContextBlock`, no escaping, a different id shape from the one its own
`note_create` demands. Nothing noticed, because nothing looked for it.

This test looks. It reads source (no import of `nora`, the reason the census
before it did too) and finds, with no list of occasions:

  1. every module that calls a model: `create_agent`, `create_dream_agent`,
     `ask_text`, `ask_structured`, `invoke_async`, `.invoke` on a runner or
     factory, a raw `completions.create`, or a `.complete(system=, prompt=)`;
  2. every class that defines `async def inputs`, which is how a Dreamer
     adaptor hands its prompt to a runtime that calls the model for it.

A module that builds a prompt must declare it: a module-level
`_BLOCKS: tuple[...] = (...)` literal, AND a call to `assemble(...)` so the
table's `order` is the order the model reads. Three kinds of module need no
table, each with a written reason: a MECHANISM (the SDK or transport layer
that never decides what a prompt says), a RUNNER (it runs a prompt that
another named module builds, and that builder must itself be declared) and
an EXEMPT occasion that is not a context at all (an authored prompt plus an
image).

**PENDING is a debt ledger, not an exemption.** It names what fails today and
the step of `design/context-assembly/04-every-occasion.md` that removes it.
Three guards keep it honest: an entry that no longer fails is refused (delete
it), the count can only go down (CEILING), and the last step of the plan
deletes the ledger and this paragraph with it.

**What this cannot see**, so a reviewer knows: a hand-built header paragraph
inside a module that DOES go through `assemble` (fire's `Event.header`), and a
new prompt that reaches an existing RUNNER by a new path. The RUNNER's
`builders` tuple is the only guard on that second case and it is kept by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nora"

#: Calls that send a prompt to a model, matched on the callee's trailing name.
_MODEL_CALLS = {
    "create_agent", "create_dream_agent", "ask_text", "ask_structured",
    "invoke_async",
}
#: `.invoke(...)` only on the two receivers that run an agent turn.
_INVOKE_RECEIVERS = {"runner", "factory"}

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

#: Not a context: an authored prompt plus media, or a model-authored question.
EXEMPT = {
    "nora/infra/vision/comprehend.py": (
        "describes an image or a video with an authored question per modality "
        "and no conversation; there is no transcript to assemble"
    ),
}

#: Runs a prompt that other named modules build. Each builder must be declared.
RUNNER = {
    "nora/agent/dreamer.py": (
        "nora/language/proactive.py",
        "nora/language/culture.py",
        "nora/language/note_candidates.py",
        "nora/language/note_curator.py",
        "nora/language/note_merge.py",
    ),
    "nora/app/agent_runner.py": ("nora/agent/prefill.py",),
    "nora/engagement/stages/invoke.py": ("nora/agent/prefill.py",),
    "nora/engagement/pipeline.py": ("nora/agent/prefill.py",),
    "nora/app/fire.py": ("nora/agent/prefill.py",),
    "nora/app/summons.py": ("nora/domain/summons.py",),
    "nora/app/cognition_agent_runner.py": ("nora/domain/cognition_render.py",),
    "nora/engagement/stages/debrief.py": ("nora/engagement/debrief_render.py",),
}

#: What fails TODAY. (module, kind) -> the plan step that removes it.
#: kind is `undeclared` (builds a prompt, no `_BLOCKS`) or `hand_joined`
#: (`_BLOCKS` declared but never walked by `assemble`, so its `order` is a
#: comment).
PENDING: dict[tuple[str, str], str] = {
    ("nora/domain/cognition_render.py", "undeclared"): "step 3",
    ("nora/language/note_merge.py", "undeclared"): "step 2",
    ("nora/engagement/debrief_render.py", "undeclared"): "step 2",
    ("nora/agent/reaction.py", "undeclared"): "step 2",
    ("nora/domain/summons.py", "undeclared"): "step 4",
    ("nora/language/proactive.py", "hand_joined"): "step 2",
    ("nora/language/culture.py", "hand_joined"): "step 2",
    ("nora/language/note_candidates.py", "hand_joined"): "step 2",
    ("nora/language/note_curator.py", "hand_joined"): "step 2",
}

#: The ledger may only shrink. Raising this number is the visible act of
#: adding a debt; it should never be done.
CEILING = 9


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


def calls_a_model(tree: ast.Module) -> bool:
    """True if the module sends a prompt to a model by any known door."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if not chain:
            continue
        tail = chain[-1]
        keywords = {kw.arg for kw in node.keywords}
        if tail in _MODEL_CALLS:
            return True
        if tail == "create" and "completions" in chain:
            return True
        if tail == "complete" and keywords & {"system", "prompt"}:
            return True
        if tail == "invoke" and len(chain) >= 2 and chain[-2] in _INVOKE_RECEIVERS:
            return True
        # `invoke.run(...)`, the tag turn's and the summons' door to the runner
        if tail == "run" and chain[-2:] == ["invoke", "run"]:
            return True
    return False


def defines_inputs(tree: ast.Module) -> bool:
    """True if a class in the module defines `async def inputs`, the door a
    Dreamer adaptor uses to hand its prompt to the runtime."""
    return any(
        isinstance(cls, ast.ClassDef) and any(
            isinstance(fn, ast.AsyncFunctionDef) and fn.name == "inputs"
            for fn in cls.body
        )
        for cls in ast.walk(tree)
    )


def declares_blocks(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name) and node.target.id == "_BLOCKS"
        and isinstance(node.value, ast.Tuple)
        for node in ast.walk(tree)
    )


def calls_assemble(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call) and _chain(node.func)[-1:] == ["assemble"]
        for node in ast.walk(tree)
    )


def violations(modules: dict[str, ast.Module]) -> set[tuple[str, str]]:
    """Every (module, kind) that breaks the rule right now, from the tree."""
    found: set[tuple[str, str]] = set()
    builders: set[str] = set()
    for rel, tree in modules.items():
        if rel == "nora/domain/ports.py":  # the Protocol that names `inputs`
            continue
        if defines_inputs(tree):
            builders.add(rel)
        if calls_a_model(tree) and rel not in MECHANISM | EXEMPT | RUNNER:
            builders.add(rel)
    for rels in RUNNER.values():
        builders.update(rels)
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


def test_every_prompt_builder_is_declared_or_on_the_ledger(modules):
    unexpected = sorted(violations(modules) - set(PENDING))
    assert not unexpected, (
        "a module builds a model prompt outside context assembly: "
        f"{unexpected}. Declare `_BLOCKS` and walk it with `assemble(...)` "
        "(design/context-assembly), or, if it truly is not a context, add it "
        "to MECHANISM, RUNNER or EXEMPT with a reason. Do not add it to "
        "PENDING: the ledger only shrinks."
    )


def test_no_pending_entry_has_been_paid_and_left_behind(modules):
    stale = sorted(set(PENDING) - violations(modules))
    assert not stale, (
        f"{stale} no longer fail. Delete them from PENDING: a ledger entry "
        "that is not a debt hides the next one."
    )


def test_the_ledger_only_shrinks():
    assert len(PENDING) <= CEILING, (
        f"PENDING holds {len(PENDING)} entries, CEILING is {CEILING}. A new "
        "debt is a violation to fix, not a line to add."
    )


@pytest.mark.parametrize("table", [MECHANISM, EXEMPT], ids=["mechanism", "exempt"])
def test_every_classification_names_a_real_module_and_a_real_reason(table, modules):
    for rel, reason in table.items():
        assert rel in modules, f"{rel} does not exist; delete its entry"
        assert len(reason.split()) >= 12, (
            f"{rel}: a reason under twelve words is a label, not a reason"
        )


def test_every_runner_names_builders_that_exist(modules):
    for rel, builders in RUNNER.items():
        assert rel in modules, f"{rel} does not exist; delete its entry"
        assert calls_a_model(modules[rel]), (
            f"{rel} no longer calls a model; it is not a RUNNER"
        )
        assert builders, f"{rel} is a RUNNER with no builder named"
        for builder in builders:
            assert builder in modules, f"{rel} names {builder}, which is gone"


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


def test_the_net_catches_a_hand_built_occasion():
    """The failure this file exists for, as a synthetic module: it builds a
    prompt from f-strings and runs it through a dream agent."""
    tree = ast.parse(
        "def render(rows):\n"
        "    return '\\n'.join(f'[#{r.id}] {r.who}: {r.body}' for r in rows)\n"
        "async def run(factory, rows):\n"
        "    agent = factory.create_dream_agent(system_prompt='x')\n"
        "    return await factory.invoke(agent, render(rows))\n"
    )
    assert calls_a_model(tree)
    assert not declares_blocks(tree)


def test_the_net_does_not_flag_a_declared_assembled_occasion():
    tree = ast.parse(
        "_BLOCKS: tuple[tuple[str, int, str], ...] = (('clock', 1, 'turn'),)\n"
        "def build(blocks):\n"
        "    return assemble(blocks)\n"
    )
    assert declares_blocks(tree) and calls_assemble(tree)


def test_a_declared_table_that_is_joined_by_hand_is_caught():
    tree = ast.parse(
        "_BLOCKS: tuple[tuple[str, int, str], ...] = (('clock', 1, 'turn'),)\n"
        "def build(parts):\n"
        "    return '\\n\\n'.join(parts)\n"
    )
    assert declares_blocks(tree) and not calls_assemble(tree)
