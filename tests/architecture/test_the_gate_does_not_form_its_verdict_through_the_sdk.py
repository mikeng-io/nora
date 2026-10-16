"""The relevance gate's verdict is not formed through the SDK's structured output.

**The gate is an ADMISSION DECISION**, so its verdict is security-relevant in
the only sense that matters here: a member of a room who can forge Nora's side
of a transcript can steer whether she is admitted into a room she was not
invited into. R-5 — silence beats a wrong answer — is what that control
implements.

`nora/agent/structured.py` says this in its own module docstring, but a
docstring does not stop the wiring. With one instruction on both arms and the
transport as the only variable, the raw `json_object` completion held the
safety cases and Strands `structured_output` breached one: a forged
`[assistant]:` row inside a member's message (`forge/assistant-row-via-newline`)
was believed. A false GO there is an unauthenticated turn, not an accuracy miss.

**The mechanism is below the prompt.** Strands implements structured output by
converting the schema into a TOOL SPECIFICATION and feeding the tool result
back, so one logical call becomes a three-message exchange; against OmniRoute's
Chat Completions transport the SDK then logs «reasoningContent is not supported
in multi-turn conversations» and DROPS the assistant turn's reasoning before the
verdict is formed. No instruction change reaches that.

**Why this is a gate and not a comment.** A caveat in the called module and a
violation in the caller can sit side by side without either reading as a
contradiction in isolation.

**DISCOVERED, not listed.** The question is «did a gate client move onto the SDK
again», and a hand-written list of one cannot answer that about a second. So
every `RelevanceGate(...)` construction under `nora/` is found, its `client=`
argument resolved, and the class either checked or exempted with its reason —
the same inverted-allowlist shape as
`test_raw_model_calls_carry_their_own_machinery.py`, for the same reason: «never
listed» is the failure mode that has actually happened.

The match is on the CALL GRAPH, never on dumped source text, so a docstring
naming `ask_structured` cannot satisfy or trip it — that is the trap the raw-call
gate had to learn by falsification.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The SDK entry point whose transport breaches the safety bar. `ask_text` is
#: deliberately NOT here: it sends no tool specification at all, so the
#: three-message exchange that drops the reasoning never happens, and the
#: debrief and the standing fold stay on it.
BARRED = "ask_structured"

#: Modules that construct a `RelevanceGate` whose client this gate cannot
#: resolve to a class, each with the reason it is not a production verdict.
EXEMPT: dict[str, str] = {
}


def _module_paths() -> list[Path]:
    return sorted((ROOT / "nora").rglob("*.py"))


def _gate_client_classes() -> tuple[dict[str, str], set[str]]:
    """`{module: class name}` for every resolvable gate client, plus the
    modules whose `client=` is not a direct construction.

    Resolution is deliberately narrow — a `Call` to a `Name` defined as a class
    in the same module. Anything else is UNRESOLVED and has to be exempted by
    hand, rather than skipped: a client this cannot read is exactly the case
    where nobody is watching.

    The CONSTRUCTION is matched on the trailing name, so
    `relevance_gate.RelevanceGate(...)` counts as well as a bare
    `RelevanceGate(...)`. Matching only `ast.Name` would leave a second gate
    wired through the qualified spelling in neither bucket, invisible, which is
    exactly the claim `test_every_gate_client_is_classified` makes.
    """
    resolved: dict[str, str] = {}
    unresolved: set[str] = set()
    for path in _module_paths():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = {
            n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None,
            )
            if called != "RelevanceGate":
                continue
            client = next(
                (kw.value for kw in node.keywords if kw.arg == "client"), None,
            )
            built = (
                getattr(getattr(client, "func", None), "id", None)
                if isinstance(client, ast.Call) else None
            )
            if built in classes:
                resolved[rel] = str(built)
            else:
                unresolved.add(rel)
    return resolved, unresolved


def _calls_in(node: ast.AST) -> set[str]:
    """Every function name invoked anywhere inside `node`.

    `ast.Call` only — a name that appears in a docstring, a log line or a
    comment is not a call, and prose explaining a check that mentions the name
    must not satisfy or trip it.

    Only the trailing name is kept, so `structured.ask_structured(...)` and a
    bare `ask_structured(...)` read the same.
    """
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "attr", None) or getattr(sub.func, "id", None)
            if name:
                out.add(name)
    return out


def _module_of(rel: str, tree: ast.Module) -> dict[str, str]:
    """`{local name: repo-relative module it came from}` for every `from
    nora... import name` in the file, aliases included.

    This is what makes an IMPORT ALIAS and a CROSS-MODULE HELPER visible.
    `from nora.agent.structured import ask_structured as _sdk` renames the
    barred call, and a three-line wrapper in a sibling module hides it one
    file away — the refactor `_reaches`'s own DOWN walk exists to encourage, so
    the walk has to follow it rather than stop at the module edge.
    """
    out: dict[str, str] = {}
    package = rel.rsplit("/", 1)[0]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        dotted = node.module or ""
        if node.level:
            # `from .structured import ...` — resolve against this file's own
            # package, so a relative import is not a hole.
            parts = package.split("/")
            base = "/".join(parts[: len(parts) - node.level + 1])
            module = f"{base}/{dotted.replace('.', '/')}".rstrip("/")
        elif dotted.split(".")[0] == "nora":
            module = dotted.replace(".", "/")
        else:
            continue
        for alias in node.names:
            out[alias.asname or alias.name] = module
    return out


def _defs_in(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level functions AND classes, by the name they are called under.

    Classes are in here so a base class counts: `class _Gate(_Base)` with the
    barred call in `_Base` is the third way past a body-only walk.
    """
    return {
        n.name: n for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


def _reaches(rel: str, class_name: str, token: str) -> bool:
    """Whether `token` is called from anywhere inside `class_name`, following
    the call graph DOWN through module-level functions, base classes, and
    functions IMPORTED FROM ELSEWHERE UNDER `nora/`.

    DOWN, because the moment two call sites need the same three lines they get
    extracted into a helper, and a check that only looked inside the class body
    would be a gate that punishes the deduplication it exists to encourage.
    ACROSS MODULES for the same reason, one step further: the helper does not
    have to land in the same file. Three ways past a body-only walk are covered
    here — an `import ... as` alias, a three-line wrapper in a sibling module,
    and a same-module base class — each with a mutant, so «covered» means
    «reddens», not «considered».

    The barred name is matched wherever it is DEFINED, not where it is spelled:
    a call resolves through the alias map to the module it was imported from,
    and the walk continues there.
    """
    (cls,) = [
        n for n in ast.walk(_tree(rel))
        if isinstance(n, ast.ClassDef) and n.name == class_name
    ]
    seen: set[tuple[str, str]] = {(rel, class_name)}
    frontier: list[tuple[str, ast.AST]] = [(rel, cls)]

    while frontier:
        where, node = frontier.pop()
        tree = _tree(where)
        imports = _module_of(where, tree)
        originals = _original_names(tree)
        defs = _defs_in(tree)

        names = _calls_in(node)
        if isinstance(node, ast.ClassDef):
            # A base class is not a call, and it is still a body this class
            # runs.
            names |= {
                str(getattr(b, "id", None) or getattr(b, "attr", ""))
                for b in node.bases
            }

        for name in names:
            # The name as IMPORTED, so `ask_structured as _sdk` is not a new
            # function, it is the barred one wearing a hat.
            if originals.get(name, name) == token:
                return True
            if name in defs:
                if (where, name) not in seen:
                    seen.add((where, name))
                    frontier.append((where, defs[name]))
                continue
            module = _module_file(imports.get(name))
            if module is None:
                continue
            real = originals.get(name, name)
            target = _defs_in(_tree(module)).get(real)
            if target is not None and (module, real) not in seen:
                seen.add((module, real))
                frontier.append((module, target))
    return False


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _module_file(module: str | None) -> str | None:
    """The file a dotted-to-slashed `nora/...` module resolves to, or None."""
    if module is None:
        return None
    for candidate in (f"{module}.py", f"{module}/__init__.py"):
        if (ROOT / candidate).exists():
            return candidate
    return None


def _original_names(tree: ast.Module) -> dict[str, str]:
    """`{local name: the name it was imported UNDER}` — the alias map."""
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def test_every_gate_client_is_classified() -> None:
    """**The gate that catches the SECOND one.**

    One production gate client exists. A new one — or the existing one moved
    behind a factory this cannot read — is a red test rather than a silent
    second verdict path."""
    resolved, unresolved = _gate_client_classes()
    unclassified = unresolved - set(EXEMPT)
    assert not unclassified, (
        f"these modules build a RelevanceGate whose client this gate cannot "
        f"resolve to a class: {sorted(unclassified)}. Either construct the "
        f"client inline so it can be read, or add the module to EXEMPT with "
        f"the reason its verdict is not production's."
    )
    stale = set(EXEMPT) - unresolved - set(resolved)
    assert not stale, (
        f"these are exempted but no longer build a RelevanceGate: "
        f"{sorted(stale)}. A list that outlives what it describes stops "
        f"being a gate."
    )


def test_the_discovery_finds_the_production_gate_client() -> None:
    """The positive control for both tests below.

    `test_a_gate_client_does_not_use_structured_output` is parametrized over
    what discovery returns, and a parametrized test over an empty set is
    SKIPPED, not passed. Renaming `RelevanceGate` would empty it silently, so
    the count is asserted here where an empty one is a FAILURE."""
    resolved, _ = _gate_client_classes()
    assert resolved, (
        "no production gate client was discovered at all — the check below "
        "would have passed by measuring nothing"
    )


def test_the_barred_call_is_a_name_this_gate_can_see() -> None:
    """The second positive control: that `ask_structured` is still the name of
    the thing being barred, and that the SAME matcher can see a real call to
    it.

    Without this, renaming the SDK seam would turn the bar below into a check
    that no class calls a function nobody has. The live example is the eval
    harness's own `_StrandsGateClient`, which exists precisely to call it."""
    assert (ROOT / "nora/agent/structured.py").read_text(encoding="utf-8").count(
        f"async def {BARRED}(",
    ) == 1, f"{BARRED} is no longer defined in nora/agent/structured.py"

    assert _reaches("tests/offline_eval_support.py", "_StrandsGateClient", BARRED), (
        f"the matcher cannot see the one call to {BARRED} it is known to be "
        f"able to see; every assertion made with it is now vacuous"
    )


@pytest.mark.parametrize(
    ("rel", "class_name"), sorted(_gate_client_classes()[0].items()),
)
def test_a_gate_client_does_not_use_structured_output(
    rel: str, class_name: str,
) -> None:
    """The bar itself.

    Reverting `_RelevanceGateModelClient.complete` to `ask_structured` turns
    this red — which is why it is a test and not only the sentence in
    `nora/agent/structured.py`."""
    assert not _reaches(rel, class_name, BARRED), (
        f"{rel}::{class_name} forms the gate's verdict through "
        f"`{BARRED}`. Measured 2026-09-11 on 40 labelled cases, that "
        f"transport breaches the suite's safety bar (1/6 vs 6/6 held on a raw "
        f"`json_object` completion) at 2.4x the prompt tokens — a forged "
        f"`[assistant]:` row is believed and the turn it admits is "
        f"unauthenticated. Issue #186."
    )
