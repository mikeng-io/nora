"""Every `RelevanceGate(...)` under `nora/` is handed a ceiling it did not
choose for itself.

**The class has a default and the default is the hole.** `call_timeout_seconds`
mirrors `[relevance_gate] timeout_seconds` the way `transcript_limit` mirrors
`transcript_rows`, which means a construction that omits it still runs — at
20s, silently, whatever the operator's file says: a number that describes the
deployment sits in one place while the code reads another.

**Why a bound at all is not a matter of taste here.** The trigger takes the
room's SLOT before `decide` and walks its due windows serially
(`nora/app/participation.py`), so an unbounded gate call is not one slow
window — it is that room refusing tags and every other room's window never
being judged.

**DISCOVERED, not listed**, for its sibling's reason
(`test_the_gate_does_not_form_its_verdict_through_the_sdk.py`): the question is
«did a SECOND gate get wired without one», and a hand-written list of two
cannot answer that about a third.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The keyword that carries the ceiling. Named once so a rename breaks this
#: test loudly rather than turning it into a check for a keyword nobody passes.
CEILING = "call_timeout_seconds"


def _gate_constructions() -> list[tuple[str, int, frozenset[str]]]:
    """`(module, line, keywords)` for every `RelevanceGate(...)` under `nora/`.

    Matched on the TRAILING name so `relevance_gate.RelevanceGate(...)` counts
    as well as a bare `RelevanceGate(...)`.
    """
    out: list[tuple[str, int, frozenset[str]]] = []
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None,
            )
            if called == "RelevanceGate":
                out.append((
                    rel, node.lineno,
                    frozenset(kw.arg for kw in node.keywords if kw.arg),
                ))
    return out


def test_the_discovery_finds_the_gate_constructions() -> None:
    """The positive control. The check below is parametrized over what
    discovery returns, and a parametrized test over an empty list is SKIPPED
    rather than failed — so renaming the class would quietly retire the gate."""
    assert _gate_constructions(), (
        "no RelevanceGate construction was found under nora/ at all — the "
        "check below would have passed by measuring nothing"
    )


@pytest.mark.parametrize(
    ("rel", "line", "keywords"), _gate_constructions(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_gate_construction_states_its_ceiling(
    rel: str, line: int, keywords: frozenset[str],
) -> None:
    assert CEILING in keywords, (
        f"{rel}:{line} builds a RelevanceGate without {CEILING}, so it runs on "
        f"the class default rather than on [relevance_gate] timeout_seconds — "
        f"an operator lowering that key would change nothing here and the "
        f"config would describe a deployment that does not exist. Issue #163."
    )
