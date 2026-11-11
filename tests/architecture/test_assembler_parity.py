"""Eval-injection parity: production and the offline harness must build their
`TranscriptInjection` through the SAME function, or the two prompts drift
apart: a site passing fewer keyword arguments than the other means every eval
measures an agent missing `<standing>`/`<notes>`/`<facts>` with nothing to
notice.

`nora.app.assembler.build_assembler` is one function both construction sites
call. This file is what keeps it that way — it parses the
two call sites (AST, not import, so it reads the SOURCE that ships rather
than whatever `TranscriptInjection` happens to default to at import time) and
asserts:

1. Both sites call `build_assembler`.
2. Neither constructs `TranscriptInjection` directly (only
   `nora/engagement/context.py`, where it is defined, and
   `nora/app/assembler.py`, the one call site, may).
3. The two `build_assembler` calls pass the SAME set of keyword arguments —
   a collaborator added to one side and not the other is exactly the drift
   this whole test exists to catch.

`tests/unit/test_offline_pipeline_assembler.py` is the companion check that
the wiring these calls describe actually RENDERS — this file only proves the
two call sites cannot structurally drift apart.

**FirePath, same trap.** The fire path has its OWN context assembly —
`build_context`, "This is the trap" (`nora/app/fire.py`) — and a tier missing
from it would render no `<notes>`/`<facts>` at all while the participation
header told the model they "came with it". The scan below pins the two
`FirePath(...)` construction sites (production's `composition.py`, the
eval/offline one in `offline.py`) to the same keyword set for the same reason
as the assembler scan: a tier wired into one side and forgotten on the other
is the drift this test exists to catch.

**Known limitations** (value-blind by design, documented rather than ignored):
`_kwarg_names` compares NAMES only, never the values or gatings — a site that
passes `standing=None` unconditionally, or gates a collaborator differently
from its sibling, stays green here (the value difference is caught by the
render test for the enabled tiers). The scan covers exactly the two files that
must use the helper; a third file importing `TranscriptInjection` directly is
a review-level rule (`test_neither_site_constructs_transcriptinjection_directly`
can only assert the files it scans).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
#: Production's call sites, split across the step modules the area/layer
#: rules produce — the parity each asserts is against the OFFLINE mirror,
#: never against a file name.
PRODUCTION = (
    REPO_ROOT / "nora" / "app" / "composition_fire.py",
    REPO_ROOT / "nora" / "app" / "composition_pipelines.py",
)
#: The offline mirror of production's composition; the PARITY checked is
#: between the two.
OFFLINE = REPO_ROOT / "tests" / "offline_pipeline.py"


def _calls(path: Path, func_name: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == func_name
    ]


def _returned_dict_keys(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Constant keys of `fn`'s `return {...}` — a `**fn(...)` spread's kwargs."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            out |= {
                k.value for k in node.value.keys if isinstance(k, ast.Constant)
            }
    return frozenset(out)


def _kwarg_names(call: ast.Call, path: Path) -> frozenset[str]:
    names = {kw.arg for kw in call.keywords if kw.arg is not None}
    if not any(kw.arg is None for kw in call.keywords):
        return frozenset(names)
    tree = ast.parse(path.read_text(), filename=str(path))
    module_fns = {
        n.name: n for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for kw in call.keywords:
        if kw.arg is not None:
            continue
        target = kw.value
        if isinstance(target, ast.Dict):
            names |= {k.value for k in target.keys if isinstance(k, ast.Constant)}
            continue
        # `**helper(...)` — the same call one function removed: the parity
        # checked is the keyword SET, which lives in the helper's return dict.
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Name):
            fn = module_fns.get(target.func.id)
            if fn is not None:
                names |= _returned_dict_keys(fn)
    return frozenset(names)


def test_composition_and_offline_each_call_build_assembler_exactly_once():
    calls = [c for p in PRODUCTION for c in _calls(p, "build_assembler")]
    assert len(calls) == 1, (
        "production must construct its TranscriptInjection through exactly "
        f"one build_assembler(...) call; found {len(calls)}"
    )
    calls = _calls(OFFLINE, "build_assembler")
    assert len(calls) == 1, (
        f"{OFFLINE.name} must construct its TranscriptInjection through "
        f"exactly one build_assembler(...) call; found {len(calls)}"
    )


def test_neither_site_constructs_transcriptinjection_directly():
    for path in (*PRODUCTION, OFFLINE):
        calls = _calls(path, "TranscriptInjection")
        assert not calls, (
            f"{path.name} calls TranscriptInjection(...) directly — route it "
            f"through nora.app.assembler.build_assembler instead, or a "
            f"collaborator added here can silently miss the sibling file"
        )


def test_build_assembler_call_sites_pass_the_same_collaborators():
    site = next(
        (p, c) for p in PRODUCTION for c in _calls(p, "build_assembler")
    )
    composition_kwargs = _kwarg_names(site[1], site[0])
    offline_kwargs = _kwarg_names(_calls(OFFLINE, "build_assembler")[0], OFFLINE)
    assert composition_kwargs, "expected build_assembler to be called with keyword arguments"
    assert offline_kwargs == composition_kwargs, (
        f"production passes {sorted(composition_kwargs)} to build_assembler; "
        f"the offline harness passes {sorted(offline_kwargs)} — a name only "
        f"on one side is the eval-injection-parity regression this test "
        f"exists to catch"
    )


def test_composition_and_offline_each_construct_firepath_exactly_once():
    calls = [c for p in PRODUCTION for c in _calls(p, "FirePath")]
    assert len(calls) == 1, (
        "production must construct its FirePath exactly once; found "
        f"{len(calls)} — the participation occasion's prompt is assembled "
        f"by FirePath.build_context, so the two sites drifting is the "
        f"Phase 1 «no <notes>/<facts> on the fire path» bug re-shipping"
    )
    calls = _calls(OFFLINE, "FirePath")
    assert len(calls) == 1, (
        f"{OFFLINE.name} must construct its FirePath exactly once; found "
        f"{len(calls)}"
    )


def test_firepath_call_sites_pass_the_same_collaborators():
    site = next(
        (p, c) for p in PRODUCTION for c in _calls(p, "FirePath")
    )
    composition_kwargs = _kwarg_names(site[1], site[0])
    offline_kwargs = _kwarg_names(_calls(OFFLINE, "FirePath")[0], OFFLINE)
    assert composition_kwargs, "expected FirePath to be called with keyword arguments"
    assert offline_kwargs == composition_kwargs, (
        f"production passes {sorted(composition_kwargs)} to FirePath; "
        f"the offline harness passes {sorted(offline_kwargs)} — a name only "
        f"on one side means an eval measures a fire/participation prompt "
        f"production never builds"
    )
