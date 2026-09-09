"""Every raw `chat.completions.create` carries what the SDK path gets for free.

**Four paths in this repo call the model without Strands** — the relevance
gate, the debrief, the standing-event fold (which shares the debrief's client)
and the vision read. That is a deliberate architecture: `03-no-agent-loop.md`
argues a tools-less adaptor is «a completion wearing an agent's construction»,
and the debrief's inputs are all knowable before the call, so there is no loop
to justify.

What was NOT decided is what a raw client must therefore carry itself. The
Strands path gets a declared `RetryPolicy`, bounded turns and tokens
(`build_limits`), `MaxTokensReachedException` on a cut-off, and `gen_ai.*`
instrumentation — all from `nora/agent/factory.py`. A raw call gets none of it,
and nothing said so. Measured 2026-09-09, the three clients had three different
subsets and no two the same:

    client                     retry  timeout  finish_reason  usage
    relevance gate               no      yes        yes        no*
    debrief / standing           no      yes        yes        no
    vision                      yes      yes         no        no

    * added the same day, after both real gate verdicts exported to Langfuse
      as a GENERATION costing zero tokens.

The gaps are not cosmetic. No retry means one transient 429 is a fail-closed
`no_go` nobody sees. No `finish_reason` check means a truncated body reaching a
JSON parser — the R-27 substitution, an ERROR reported as a PASS. No usage
means the cost of the highest-frequency model call in the system is invisible.

**A STATIC read, and an INVERTED allowlist.** The question is «was a raw call
forgotten», and a hand-written list of the four cannot answer that about a
fifth. `test_media_join_reaches_every_transcript.py` learned this the hard way
— «never listed» is the only failure mode that has actually happened — so the
call sites are DISCOVERED and each one is either required to carry the
machinery or exempted with its reason.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: What the SDK path provides and a raw call must therefore provide itself.
#: Each is checked on the ENCLOSING FUNCTION of the call, not on the module:
#: a module-level grep would pass on a file where some OTHER function has a
#: timeout, which is the proxy-for-identity failure this repo keeps hitting.
#:
#: `retry` is the exception — it is a property of the client object, not of
#: the call site — so it is satisfied by an explicit `max_retries` anywhere in
#: the module that builds the client, and its absence is what the audit found
#: everywhere. Listed here so a fifth client cannot inherit the OpenAI SDK's
#: default of 2 by accident and call it a decision.
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
    # `nora/app/composition.py` is GONE from this list, and that is the point
    # of the whole convention: the relevance gate, the debrief and the
    # standing fold now call the model through `nora.agent.structured`, so
    # they inherit the SDK's machinery instead of each hand-rolling a
    # different subset of it. Two raw call sites remain, both below.
    # NOT `usage`, and the reason is structural rather than an oversight:
    # `nora.infra` may not import `nora.obs` (the layered-architecture
    # contract, enforced by `lint-imports`), so a raw client in this layer
    # cannot annotate a span at all.
    #
    # **The SECOND half of this exemption was retired on 2026-09-09 and the
    # reason is kept here because it changed rather than went away.** It used
    # to read: threading the counts out would record them into a trace that is
    # DISCARDED anyway, because `comprehend`'s root declared
    # `TRACE_KIND: "plumbing"`. That contradiction — a model generation under
    # a root whose own vocabulary means "no model call anywhere under it" —
    # was the finding, and it is now fixed: the root declares `generation`,
    # and the media read reaches Langfuse.
    #
    # So what remains is one live gap, not two, and it is smaller: a vision
    # read now exports as a generation costing zero tokens, which is exactly
    # the defect the gate and the debrief were fixed for one commit earlier.
    # Closing it means carrying the counts out of `nora.infra` to a caller
    # that CAN annotate — `MediaDescription.provenance` already travels that
    # way for `media_caveat` — and that is a port-shape decision, weighed
    # against this file's own rule that usage belongs on the span and never in
    # a return type. Left for the operator to call rather than settled here.
    "nora/infra/vision/client.py": ("timeout", "finish_reason"),
}

#: Call sites that are NOT production raw clients, each with the reason.
EXEMPT: dict[str, str] = {
    "nora/app/eval_support.py": (
        "the eval harness's own measuring client. It exists to read token "
        "usage back out for the gate suite's cost measurement, and it is "
        "never constructed by the composition root — `evals/` may import "
        "`nora.app`, so it lives here rather than under `evals/`."
    ),
}


def test_every_raw_model_call_is_classified() -> None:
    """**The gate that catches the FIFTH one.**

    Four exist today and each picked a different subset of the machinery,
    because nothing was watching. A new raw client is a red test rather than a
    silent fifth subset."""
    discovered = set(_enclosing_functions_with_raw_calls())
    classified = set(REQUIRED) | set(EXEMPT)

    unclassified = discovered - classified
    assert not unclassified, (
        f"these modules call the model without Strands and are neither "
        f"required to carry its machinery nor exempted: {sorted(unclassified)}. "
        f"Either add them to REQUIRED with the facets they must carry, or to "
        f"EXEMPT with the reason they are not a production model call."
    )

    stale = classified - discovered - {"nora/ingest/relevance_gate.py"}
    assert not stale, (
        f"these are listed but no longer call the model: {sorted(stale)}. "
        f"A list that outlives what it describes stops being a gate."
    )


def _code_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """`ast.dump` of the function with its DOCSTRING REMOVED.

    Found by falsification, which is the only way it would have been found: a
    mutant that deleted vision's `finish_reason == "length"` check left the
    gate green, because `_checked`'s docstring explains the check by name and
    `ast.dump` renders a docstring as an ordinary `Constant`. The gate was
    reading prose about the property instead of the property.

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

    # EVERY string constant goes, not only the docstring. Stripping the
    # docstring alone was one class too narrow: deleting the real truncation
    # check and leaving `log.debug("... finish_reason not checked", ...)` kept
    # this green while a cut-off image description was returned as complete
    # (verified 2026-09-09). A facet has to be USED, and a mention inside a log
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


def _on_the_call_path(rel: str, fn_name: str, token: str) -> bool:
    """Whether `token` appears anywhere on the call path through `fn_name`.

    BOTH DIRECTIONS, and each was learned from a false positive this gate
    raised against correct code:

    * UP, because `VisionClient._create` is bounded by `describe` two frames
      above it — one `wait_for` around the whole retry loop, which is stronger
      than one per attempt.
    * DOWN, because the `finish_reason` check and the usage annotation were
      extracted into a shared helper the moment two call sites needed them.
      Requiring the token inside the calling function would have been a gate
      that punishes the deduplication it exists to encourage.

    Still not a module-level grep: the path is walked through the module's own
    call graph from THIS function, so a `wait_for` around something unrelated,
    or a `finish_reason` read in a function nothing here reaches, does not
    satisfy it. That is the proxy-for-identity failure the per-function check
    existed to avoid, and it survives.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    funcs = {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    if fn_name not in funcs:                                   # pragma: no cover
        return False

    callees: dict[str, set[str]] = {
        name: _names_called(node) & set(funcs) for name, node in funcs.items()
    }
    callers: dict[str, set[str]] = {}
    for name, targets in callees.items():
        for t in targets:
            callers.setdefault(t, set()).add(name)

    def _closure(graph: dict[str, set[str]]) -> set[str]:
        seen, frontier = {fn_name}, [fn_name]
        while frontier:
            for nxt in graph.get(frontier.pop(), set()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return seen

    return any(
        token in _code_of(funcs[name])
        for name in _closure(callees) | _closure(callers)
    )


@pytest.mark.parametrize(
    "rel", sorted(r for r, facets in REQUIRED.items() if facets),
)
def test_a_raw_call_bounds_itself(rel: str) -> None:
    """A bound on the call path.

    The bare `AsyncOpenAI` default is 600s x retries. On the gate that runs
    while the conversation's slot is HELD, so an unbounded one is not a slow
    background job — it is a room that stops answering."""
    for fn in _enclosing_functions_with_raw_calls()[rel]:
        assert _on_the_call_path(rel, fn.name, "wait_for"), (
            f"{rel}::{fn.name} calls the model with no bound on its path"
        )


@pytest.mark.parametrize(
    "rel", sorted(r for r, facets in REQUIRED.items() if "finish_reason" in facets),
)
def test_a_raw_call_reads_finish_reason(rel: str) -> None:
    """A raw completion does not raise on a cut-off the way a Strands agent's
    `MaxTokensReachedException` does — it returns `finish_reason="length"` and
    a silently truncated body, which can still parse as JSON into something
    shorter and wrong. That is an ERROR reported as a PASS."""
    for fn in _enclosing_functions_with_raw_calls()[rel]:
        assert _on_the_call_path(rel, fn.name, "finish_reason"), (
            f"{rel}::{fn.name} never reads `finish_reason`, so a truncated "
            f"body is indistinguishable from a complete one"
        )


def test_the_facet_lists_are_not_silently_empty() -> None:
    """A parametrized test over an empty set is SKIPPED, not passed — and
    pytest says so in a line nobody reads. `test_a_raw_call_records_its_token_usage`
    spent a day in that state after the commit that moved the last
    usage-carrying entry out of `REQUIRED` (2026-09-09), so the test named for
    the facet had never run at all."""
    for facet in FACETS:
        covered = [r for r, facets in REQUIRED.items() if facet in facets]
        if facet == "usage":
            # The only raw caller left is in `nora/infra`, which cannot import
            # `nora.obs` to annotate a span. Stated here so an empty list is a
            # RECORDED exemption rather than an invisible skip.
            assert not covered, "a usage-carrying entry returned; re-enable its test"
            continue
        assert covered, f"no REQUIRED entry carries {facet!r} — its test cannot run"


@pytest.mark.parametrize(
    "rel", sorted(r for r, facets in REQUIRED.items() if "usage" in facets),
)
def test_a_raw_call_records_its_token_usage(rel: str) -> None:
    """Strands emits `gen_ai.usage.*` on its own; a raw call emits nothing, and
    a generation reporting zero tokens is what Langfuse then shows forever.
    Read back live on 2026-09-08: both real gate verdicts cost zero."""
    for fn in _enclosing_functions_with_raw_calls()[rel]:
        assert _on_the_call_path(rel, fn.name, "usage"), (
            f"{rel}::{fn.name} never reads `response.usage`, so this call "
            f"exports to Langfuse costing nothing"
        )
