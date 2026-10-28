"""R-C: a fake standing in for a port lives in `nora/testing/` and passes that
port's contract suite. Inline classes are for local scaffolding.

CLAUDE.md says «a port never gains a second implementation — INCLUDING a test
fake — without a shared contract suite both pass». A fake defined inside a
test module is invisible to every contract suite by construction, so keeping
fakes inline makes that rule unenforceable. This gate is what makes it
enforceable.

What «standing in for a port» means here, read off the source, not guessed:

- The ports are the `Protocol` classes in `nora/domain/ports.py`.
- A class in `tests/` stands in for a port when it defines EVERY public method
  the port declares, each with EXACTLY the port's parameter names (or a
  `*args`/`**kwargs` wildcard). Exact names, not a superset: `describe(data,
  media_type, prompt, model, provider)` is a `VisionClient` double, not a
  `MediaInterpreter`, and a one-method port like `SocialProvider.read(url)`
  must not claim every class with a `read` method. A noisy gate is a disabled
  gate (CLAUDE.md, the R-29 story).
- Port ATTRIBUTES (`platform: str`, `name: str`) are not required — a fake sets
  them in `__init__` as often as on the class body, and the methods are what
  can drift.

Three exemptions, all structural rather than by name:

1. `tests/contract/` — a class defined inside a contract suite is under that
   suite by construction; that is the file's whole purpose.
2. A double every one of whose port methods only FAILS — a bare `raise`, or
   an `await asyncio.sleep(...)` stall followed by one. It is the fault
   injector that drives the `unavailable` and timeout paths; it implements
   nothing, so it has nothing to drift from the production adapter. It IS the
   local scaffolding the rule leaves alone.
3. A subclass of a `nora.testing` fake — the contract-bound fake with one
   local twist (`_SlowMediaInterpreter(FakeMediaInterpreter)` inserting a
   suspension point). What it inherits is under the suite; what it overrides
   is the test's own scaffolding.

Everything else is pinned in `_ALLOWED` with the reason it is still inline,
and the pin is checked in BOTH directions: an unpinned stand-in fails, and a
pin whose class no longer stands in for a port fails too (a stale exemption is
a claim). The list is an inventory to burn down, never a place to add to —
the fix for a new entry is `nora/testing/` plus the port's contract suite.

Positive control: the matcher is run against `nora/testing/fakes.py` and must
see the shared fakes it exists to point at. A detector that stops seeing
`FakeBlobStore` as a `BlobStore` fails here rather than passing green with an
empty offender list.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTS = ROOT / "nora" / "domain" / "ports.py"
SHARED_FAKES = ROOT / "nora" / "testing" / "fakes.py"

# `tests/<path>::<ClassName>` -> why it is still inline. Burn down, never grow.
# Three kinds of reason appear, and only these:
#   NO_SHARED_FAKE — the port has no fake in nora/testing yet; this is the
#     candidate to promote, together with a contract suite against the
#     production implementation.
#   DRIVES_THE_LOOP — the class steers a scheduler/supervisor (slow, failing,
#     counting); the port's own semantics are not what the test measures.
#   FAULT_SWITCH_OVER_SHARED — composes the shared fake to add a failure mode
#     the shared fake does not offer; promote the switch into the fake.
_NO_SHARED_FAKE = "NO_SHARED_FAKE: no nora/testing fake for this port yet"
_DRIVES_THE_LOOP = "DRIVES_THE_LOOP: steers dream_loop's supervision, not the port's semantics"
_ALLOWED: dict[str, str] = {
    # SearchProvider / GifSource / SocialProvider — the web tier. The search
    # contract suite is envelope-driven (each vendor's real wire shape) and has
    # no in-memory implementation yet; `CannedSearchProvider` (evals) answers
    # every query `ok` and is not under it either.
    "tests/test_web_fetch_tool.py::_FakeSearchProvider": _NO_SHARED_FAKE,
    "tests/test_tools.py::_GifSource": _NO_SHARED_FAKE,
    "tests/unit/test_reaction.py::_Gifs": _NO_SHARED_FAKE,
    # ImageGenerator — two implementations, `XaiImageGenerator` and
    # `OpenAICodexImageGenerator` (`nora/infra/image_gen/`), each with its own
    # suite (`tests/contract/test_image_generator_contract.py`,
    # `tests/contract/test_openai_codex_image_generator.py`) run against the
    # adapter over a local server, not a fake; no in-memory `ImageGenerator`
    # exists to promote to yet.
    "tests/test_skill_content.py::_ImageGenerator": _NO_SHARED_FAKE,
    # TrackSource / MusicToolBridge — nora/testing/music.py holds the two
    # STORE fakes; the source and the bridge have none.
    "tests/unit/test_music_player.py::_StubSource": _NO_SHARED_FAKE,
    "tests/unit/test_music_player_manager.py::_StubSource": _NO_SHARED_FAKE,
    "tests/unit/test_music_agent_tools.py::_FakeBridge": _NO_SHARED_FAKE,
    # DreamAdaptor / DreamSink — the contract suites carry their own in-suite
    # implementations (`_FakeAdaptor`, `_FakeDreamSink`); nothing is exported.
    "tests/unit/test_dreamer_runtime.py::_Adaptor": _NO_SHARED_FAKE,
    "tests/unit/test_runtime.py::_StubAdaptor": _NO_SHARED_FAKE,
    # Speaker (voice playout seam) — the only real implementation is the
    # Discord adapter; no in-memory Speaker exists in nora/testing yet, and
    # there is no voice-playout contract suite.
    "tests/test_discord_adapter.py::_FakeSpeaker": _NO_SHARED_FAKE,
}


@dataclass(frozen=True)
class _Method:
    params: frozenset[str]
    wildcard: bool  # *args / **kwargs — accepts any call shape
    fault_only: bool  # body only fails: `raise`, or `await asyncio.sleep(...)` then `raise`


def _is_sleep(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
        and isinstance(stmt.value.value, ast.Call)
        and isinstance(stmt.value.value.func, ast.Attribute)
        and stmt.value.value.func.attr == "sleep"
    )


def _method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Method:
    a = node.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if names and names[0] in {"self", "cls"}:
        names = names[1:]
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return _Method(
        params=frozenset(names),
        wildcard=a.vararg is not None or a.kwarg is not None,
        fault_only=(
            bool(body) and isinstance(body[-1], ast.Raise)
            and all(_is_sleep(st) for st in body[:-1])
        ),
    )


def _methods_of(cls: ast.ClassDef) -> dict[str, _Method]:
    return {
        item.name: _method(item)
        for item in cls.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _is_protocol(cls: ast.ClassDef) -> bool:
    return any(
        (isinstance(b, ast.Name) and b.id == "Protocol")
        or (isinstance(b, ast.Attribute) and b.attr == "Protocol")
        for b in cls.bases
    )


def port_methods() -> dict[str, dict[str, _Method]]:
    """Port name -> its public methods, read off `nora/domain/ports.py`."""
    tree = ast.parse(PORTS.read_text(encoding="utf-8") + "\n" +
                     (ROOT / "nora/domain/memory_graph.py").read_text(encoding="utf-8"))
    ports: dict[str, dict[str, _Method]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_protocol(node):
            methods = {n: m for n, m in _methods_of(node).items() if not n.startswith("_")}
            if methods:
                ports[node.name] = methods
    return ports


def _stands_in(cls_methods: dict[str, _Method], port: dict[str, _Method]) -> bool:
    for name, spec in port.items():
        mine = cls_methods.get(name)
        if mine is None:
            return False
        if not (mine.wildcard or spec.wildcard or mine.params == spec.params):
            return False
    return True


def _fault_only_for(cls_methods: dict[str, _Method], port: dict[str, _Method]) -> bool:
    return all(cls_methods[name].fault_only for name in port)


def _shared_fake_names(tree: ast.Module) -> set[str]:
    """Names this module imports from `nora.testing` (or a submodule of it)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and (
            node.module == "nora.testing" or node.module.startswith("nora.testing.")
        ):
            names.update(a.asname or a.name for a in node.names)
    return names


def _extends_shared_fake(cls: ast.ClassDef, shared: set[str]) -> bool:
    return any(isinstance(b, ast.Name) and b.id in shared for b in cls.bases)


@dataclass(frozen=True)
class _Hit:
    key: str  # tests/<rel>::<ClassName>
    port: str
    exempt: bool  # a fault injector, or a subclass of a shared fake


def stand_ins(path: Path, ports: dict[str, dict[str, _Method]]) -> list[_Hit]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(ROOT).as_posix()
    shared = _shared_fake_names(tree)
    hits: list[_Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        mine = _methods_of(node)
        for port_name, port in ports.items():
            if _stands_in(mine, port):
                exempt = _fault_only_for(mine, port) or _extends_shared_fake(node, shared)
                hits.append(_Hit(f"{rel}::{node.name}", port_name, exempt))
    return hits


def _test_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "tests").rglob("*.py")
        if "contract" not in p.relative_to(ROOT / "tests").parts
    )


def test_the_matcher_sees_the_shared_fakes_it_points_at() -> None:
    """Positive control for the gate below: an empty offender list must mean
    «no inline stand-ins», not «the matcher went blind»."""
    ports = port_methods()
    seen = {(h.key.split("::")[1], h.port) for h in stand_ins(SHARED_FAKES, ports)}
    for fake, port in (
        ("FakeBlobStore", "BlobStore"),
        ("FakeMemoryStore", "MemoryStore"),
        ("FakeRosterSource", "RosterSource"),
        ("FakeMediaInterpreter", "MediaInterpreter"),
        ("FakeMediaObjectStore", "MediaObjectStore"),
        ("FakeReverseGeocoder", "ReverseGeocoder"),
    ):
        assert (fake, port) in seen, f"nora/testing/fakes.py::{fake} not seen as a {port}"


def test_a_port_stand_in_outside_nora_testing_is_pinned_with_a_reason() -> None:
    ports = port_methods()
    found: dict[str, set[str]] = {}
    for path in _test_modules():
        for hit in stand_ins(path, ports):
            if hit.exempt:
                continue  # a fault injector, or the shared fake with a local twist
            found.setdefault(hit.key, set()).add(hit.port)

    unpinned = sorted(k for k in found if k not in _ALLOWED)
    assert not unpinned, (
        "class(es) in tests/ standing in for a port from nora/domain/ports.py "
        "(R-C, issue #113). A fake for a port lives in nora/testing/ and is "
        "parametrized into that port's contract suite under tests/contract/; "
        "an inline class is for local scaffolding only:\n  "
        + "\n  ".join(f"{k} -> {', '.join(sorted(found[k]))}" for k in unpinned)
    )
    stale = sorted(k for k in _ALLOWED if k not in found)
    assert not stale, (
        "pinned in _ALLOWED but no longer standing in for any port — drop the "
        f"pin (a stale exemption is a claim): {stale}"
    )


def test_memory_graph_fake_is_bound_to_the_port_and_contract() -> None:
    path = ROOT / "nora/testing/memory_graph.py"
    seen = {(h.key.split("::")[1], h.port) for h in stand_ins(path, port_methods())}
    assert ("FakeMemoryGraph", "MemoryGraph") in seen
    contract = (ROOT / "tests/contract/test_memory_graph_contract.py").read_text()
    assert "return FakeMemoryGraph()" in contract
