"""Every raw `chat.completions.create` carries what the SDK path gets for free.

**Two paths in this repo call the model without Strands**: the relevance
gate and the vision read. That is a deliberate architecture: `03-no-agent-loop.md`
argues a tools-less adaptor is «a completion wearing an agent's construction»,
and such an adaptor's inputs are all knowable before the call, so there is no
loop to justify.

A raw client must therefore carry for itself what the Strands path gets for
free: a declared `RetryPolicy`, bounded turns and tokens (`build_limits`),
`MaxTokensReachedException` on a cut-off, and `gen_ai.*` instrumentation — all
from `nora/agent/factory.py`. A raw call gets none of it, and without a gate
each client carries a different subset.

The gaps are not cosmetic. No retry means one transient 429 is a fail-closed
`no_go` nobody sees. No `finish_reason` check means a truncated body reaching a
JSON parser — the R-27 substitution, an ERROR reported as a PASS. No usage
means the cost of the highest-frequency model call in the system is invisible.

**Which paths are raw.** The relevance gate is raw on evidence: the SDK's
structured-output transport breached its suite's safety bar, because Strands
converts the schema into a tool specification and OmniRoute drops the
assistant turn's reasoning out of the resulting three-message exchange.
`tests/architecture/test_the_gate_does_not_form_its_verdict_through_the_sdk.py`
is what keeps it off that transport, and THIS file is what makes it carry the
machinery. The debrief and the standing fold stay on the SDK: neither decides
whether Nora may speak.

**A STATIC read, and an INVERTED allowlist.** The question is «was a raw call
forgotten», and a hand-written list cannot answer that about a
fifth. `test_media_join_reaches_every_transcript.py` uses the same shape —
«never listed» is the failure mode a hand-written list cannot catch — so the
call sites are DISCOVERED and each one is either required to carry the
machinery or exempted with its reason.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: What the SDK path provides and a raw call must therefore provide itself.
#: Each is checked on the ENCLOSING FUNCTION of the call, not on the module:
#: a module-level grep would pass on a file where some OTHER function has a
#: timeout, which is the proxy-for-identity failure this repo keeps hitting.
#:
#: `retry` is NOT here, and the reason is a gap rather than a decision. It is
#: a property of the client object rather than of the call site, so it would
#: have to be read off whoever builds the client — and nothing does: no test
#: reads it, and `nora/agent/factory.py::create_client` passes no
#: `max_retries`, so every raw caller inherits the OpenAI SDK's default of 2.
#: It is an unchosen setting, named here rather than implied by a facet list
#: that reads as exhaustive.
FACETS = ("timeout", "finish_reason", "usage")


def _enclosing_functions_with_raw_calls(
    root: Path | None = None,
) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function under `nora/` containing a `*.completions.create(...)`.

    Matched on the attribute chain rather than on a variable name, because the
    receiver is spelled `self._client`, `client` and `self._openai` across the
    four and a name-based scan would miss the fifth spelling.
    """
    base = root if root is not None else ROOT
    found: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for path in sorted((base / "nora").rglob("*.py")):
        rel = path.relative_to(base).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "create"
                    and isinstance(sub.func.value, ast.Attribute)
                    and sub.func.value.attr == "completions"
                ):
                    found.setdefault(rel, []).append(node)
                    break
    return found


#: The raw call sites, each with the facets it is REQUIRED to carry.
#:
#: A facet is listed as required unless there is a reason it cannot apply, and
#: the reason is written next to it — never omitted to make the gate green.
#: Reducing a requirement here is a design change and reads as one in review.
REQUIRED: dict[str, tuple[str, ...]] = {
    # **`_RelevanceGateModelClient` is raw on evidence.** The relevance gate
    # decides whether Nora speaks into a room she was not invited into, and the
    # SDK's structured-output transport breached the suite's safety bar: a
    # forged `[assistant]:` row was believed. The debrief and the standing fold
    # stay on the SDK — neither produces a security-relevant verdict — so this
    # module is a raw call site for exactly one of the clients it holds, and
    # this entry is what makes that one carry its own machinery rather than
    # quietly carrying a different subset.
    #
    # All three facets, none exempted: `nora.app` may import `nora.obs`, the
    # call runs while the conversation's slot is held, and a truncated verdict
    # still parses as JSON into something shorter and wrong.
    "nora/app/composition.py": ("timeout", "finish_reason", "usage"),
    # NOT `usage`, and the reason is structural rather than an oversight:
    # `nora.infra` may not import `nora.obs` (the layered-architecture
    # contract, enforced by `lint-imports`), so a raw client in this layer
    # cannot annotate a span at all.
    #
    # The live gap: a vision read exports as a generation costing zero tokens.
    # Closing it means carrying the counts out of `nora.infra` to a caller
    # that CAN annotate — `MediaDescription.provenance` already travels that
    # way for `media_caveat` — and that is a port-shape decision, weighed
    # against this file's own rule that usage belongs on the span and never in
    # a return type. Left for the operator to call rather than settled here.
    "nora/infra/vision/client.py": ("timeout", "finish_reason"),
}

#: Call sites that are NOT production raw clients, each with the reason.
EXEMPT: dict[str, str] = {
}


def test_every_raw_model_call_is_classified() -> None:
    """**The gate that catches the NEXT one.**

    Every raw client must carry the machinery. A new raw client is a red test
    rather than a silent different subset."""
    discovered = set(_enclosing_functions_with_raw_calls())
    classified = set(REQUIRED) | set(EXEMPT)

    unclassified = discovered - classified
    assert not unclassified, (
        f"these modules call the model without Strands and are neither "
        f"required to carry its machinery nor exempted: {sorted(unclassified)}. "
        f"Either add them to REQUIRED with the facets they must carry, or to "
        f"EXEMPT with the reason they are not a production model call."
    )

    # No subtrahend beyond `discovered`: a carve-out that cannot fire gives a
    # reader the false impression that something needed one.
    stale = classified - discovered
    assert not stale, (
        f"these are listed but no longer call the model: {sorted(stale)}. "
        f"A list that outlives what it describes stops being a gate."
    )


def _code_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """`ast.dump` of the function with its DOCSTRING REMOVED.

    A docstring that explains a check by name would satisfy the gate:
    `ast.dump` renders a docstring as an ordinary `Constant`, so deleting
    vision's `finish_reason == "length"` check would leave the gate green while
    `_checked`'s docstring still mentions it. The gate would be reading prose
    about the property instead of the property.

    Every facet here is a short identifier that any honest docstring will
    mention, so this is not an edge case — it is the default failure mode of
    matching tokens against a dumped tree.
    """
    stripped = copy.deepcopy(node)
    if (
        stripped.body
        and isinstance(stripped.body[0], ast.Expr)
        and isinstance(stripped.body[0].value, ast.Constant)
        and isinstance(stripped.body[0].value.value, str)
    ):
        del stripped.body[0]

    # EVERY string constant goes, not only the docstring: deleting the real
    # truncation check and leaving `log.debug("... finish_reason not checked",
    # ...)` would keep this green while a cut-off image description was
    # returned as complete. A facet has to be USED, and a mention inside a log
    # line is not a use.
    for sub in ast.walk(stripped):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            sub.value = ""
    return ast.dump(stripped)


def _names_called(node: ast.AST) -> set[str]:
    """Every function name invoked anywhere inside `node`."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "attr", None) or getattr(sub.func, "id", None)
            if name:
                out.add(name)
    return out


def _on_the_call_path(
    rel: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, token: str,
) -> bool:
    """Whether `token` appears anywhere on the call path through `fn`.

    BOTH DIRECTIONS, because each otherwise gives a false positive against
    correct code:

    * UP, because `VisionClient._create` is bounded by `describe` two frames
      above it — one `wait_for` around the whole retry loop, which is stronger
      than one per attempt.
    * DOWN, because the `finish_reason` check and the usage annotation live in
      a shared helper. Requiring the token inside the calling function would be
      a gate that punishes the deduplication it exists to encourage.

    **The walk starts from a NODE, not from a name**, and that is not tidiness.
    Keyed by name, a module holding two classes with a `complete` method has
    one of them silently overwrite the other, and the gate then reads the wrong
    function's body: `nora/app/composition.py` holds two model-client classes
    with a `complete` each (the debrief's serves the standing fold too), and a
    name-keyed walk would report the relevance gate's correctly-bounded
    `complete` as unbounded because the name resolves to the debrief's. A gate
    that answers about a different function than the one it names is worse than
    no gate — it is the proxy-for-identity failure this file's per-function
    check exists to avoid, reproduced inside the check itself. Same-named
    functions are still expanded TOGETHER once the walk is under way, which only
    widens the path and cannot move its start.

    Still not a module-level grep: the path is walked through the module's own
    call graph from THIS function, so a `wait_for` around something unrelated,
    or a `finish_reason` read in a function nothing here reaches, does not
    satisfy it.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    by_name: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            by_name.setdefault(node.name, []).append(node)
    every = [n for group in by_name.values() for n in group]

    # `fn` was parsed from the same source by `_enclosing_functions_with_raw_calls`,
    # so it is an equal-but-distinct node; match it back by position, which is
    # what makes two same-named methods distinguishable at the START.
    here = [
        n for n in by_name.get(fn.name, [])
        if (n.lineno, n.col_offset) == (fn.lineno, fn.col_offset)
    ]
    if not here:                                               # pragma: no cover
        return False

    def _closure(
        step: Callable[
            [ast.FunctionDef | ast.AsyncFunctionDef],
            list[ast.FunctionDef | ast.AsyncFunctionDef],
        ],
    ) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        seen = {id(n): n for n in here}
        frontier = list(here)
        while frontier:
            for nxt in step(frontier.pop()):
                if id(nxt) not in seen:
                    seen[id(nxt)] = nxt
                    frontier.append(nxt)
        return list(seen.values())

    def _down(node):  # type: ignore[no-untyped-def]
        return [
            target
            for name in _names_called(node)
            for target in by_name.get(name, [])
        ]

    def _up(node):  # type: ignore[no-untyped-def]
        return [n for n in every if node.name in _names_called(n)]

    return any(
        token in _code_of(node) for node in _closure(_down) + _closure(_up)
    )


@pytest.mark.parametrize(
    # Selected on the FACET NAME, like its two siblings: selecting on "has any
    # facet at all" would keep selecting an entry that dropped `timeout`
    # specifically.
    "rel", sorted(r for r, facets in REQUIRED.items() if "timeout" in facets),
)
def test_a_raw_call_bounds_itself(rel: str) -> None:
    """A bound on the call path.

    The bare `AsyncOpenAI` default is 600s x retries. On the gate that runs
    while the conversation's slot is HELD, so an unbounded one is not a slow
    background job — it is a room that stops answering."""
    for fn in _enclosing_functions_with_raw_calls()[rel]:
        assert _on_the_call_path(rel, fn, "wait_for"), (
            f"{rel}::{fn.name} calls the model with no bound on its path"
        )


@pytest.mark.parametrize(
    "rel", sorted(r for r, facets in REQUIRED.items() if "finish_reason" in facets),
)
def test_a_raw_call_reads_finish_reason(rel: str) -> None:
    """A raw completion does not raise on a cut-off the way a Strands agent's
    `MaxTokensReachedException` does — it returns `finish_reason="length"` and
    a silently truncated body, which can still parse as JSON into something
    shorter and wrong. That is an ERROR reported as a PASS.

    **This is a presence check, not a value check, and the difference is real.**
    Replacing the gate's `finish_reason=choice.finish_reason or ""` with a
    literal `finish_reason="stop"` leaves every test in this file green: the
    token still appears, and what it appears in is no longer the provider's
    answer. Closing that here would mean dataflow analysis in an AST gate;
    what closes it instead is a unit test per client that makes the provider
    report a cut-off and asserts it comes back —
    `tests/unit/test_gate_model_client_trace.py::test_the_gate_reports_the_providers_own_finish_reason`.
    So read this file as «the machinery is wired», never as «the machinery is
    correct»; the second claim is one layer up and is made per call site."""
    for fn in _enclosing_functions_with_raw_calls()[rel]:
        assert _on_the_call_path(rel, fn, "finish_reason"), (
            f"{rel}::{fn.name} never reads `finish_reason`, so a truncated "
            f"body is indistinguishable from a complete one"
        )


def test_the_facet_lists_are_not_silently_empty() -> None:
    """A parametrized test over an empty set is SKIPPED, not passed — and
    pytest says so in a line nobody reads. A facet no `REQUIRED` entry carries
    would leave the test named for it never run at all."""
    for facet in FACETS:
        covered = [r for r, facets in REQUIRED.items() if facet in facets]
        assert covered, f"no REQUIRED entry carries {facet!r} — its test cannot run"


@pytest.mark.parametrize(
    "rel", sorted(r for r, facets in REQUIRED.items() if "usage" in facets),
)
def test_a_raw_call_records_its_token_usage(rel: str) -> None:
    """Strands emits `gen_ai.usage.*` on its own; a raw call emits nothing, and
    a generation reporting zero tokens is what Langfuse then shows forever."""
    for fn in _enclosing_functions_with_raw_calls()[rel]:
        assert _on_the_call_path(rel, fn, "usage"), (
            f"{rel}::{fn.name} never reads `response.usage`, so this call "
            f"exports to Langfuse costing nothing"
        )
