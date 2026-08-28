"""The SHIPPED objective's declared fields must be exactly the fields the
parser reads.

**This is the test no tier has, and it is the one that would have caught the
language tier shipping provably inert.** An objective is a prompt: it tells the
model what to return. A parser is code: it decides what to read. Nothing
connects them — not a type, not an import, not the gate. When they drift, the
model returns a field nobody reads, or the code reads a field nobody was asked
for, and **every counter in the tier stays healthy** because the envelope
arrived, the items parsed, and the result was empty. The tier is wired end to
end and inert.

Two properties make this a check rather than a claim:

1. **It reads the SHIPPED objective**, from `nora/dreamer/objectives/`, not a
   fixture. `fact-candidates` and `fact-adjudication` are `NATIVELY_AUTHORED` —
   no byte-pin, operator-editable on the bind mount — so a fixture would be a
   copy that drifts from the file the container loads, which is the same
   failure one level down.
2. **It derives the parser's fields from the PARSER**, by walking its AST for
   every `item[...]` / `item.get(...)` subscript. A hand-copied tuple would be
   a third place to drift; CLAUDE.md's rule about label vocabularies is the
   same rule and this is the same reason.

So it is red on either side moving alone, and green only when they agree.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from nora.language import fact_adaptor

OBJECTIVES = Path(fact_adaptor.__file__).resolve().parents[1] / "dreamer" / "objectives"


def _return_examples(objective: str, envelope_key: str) -> list[dict[str, object]]:
    """Every item under `envelope_key` in the objective's own worked example.

    The union across items is deliberate: an objective demonstrates optional
    fields by omitting them from one example and including them in another
    (`fact-adjudication`'s `not_covered` entry carries no `source_url`), so any
    single item would understate the contract.
    """
    text = (OBJECTIVES / f"{objective}.md").read_text(encoding="utf-8")
    marker = "## What to return"
    assert marker in text, f"{objective}.md has no '{marker}' section"
    body = text.split(marker, 1)[1]

    start = body.index("{")
    depth = 0
    end = None
    in_string = False
    escaped = False
    for i, ch in enumerate(body[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, f"{objective}.md's example JSON is unbalanced"
    envelope = json.loads(body[start:end])
    assert envelope_key in envelope, (
        f"{objective}.md's example does not carry the envelope key "
        f"{envelope_key!r} the adaptor declares"
    )
    return envelope[envelope_key]


def _keys_read_by(fn: Callable[..., object], *, name: str = "item") -> set[str]:
    """Every literal key the function reads off `name`, from its AST.

    Matches `name["k"]`, `name.get("k")` and `name.get("k", default)`. A key
    computed at runtime would be invisible here — and would also be
    unreviewable against a prompt, which is why no parser in this package has
    one.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.add(node.slice.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


CASES = [
    pytest.param(
        "fact-candidates", "candidates", fact_adaptor._parse_claim,
        id="fact-candidates",
    ),
    pytest.param(
        "fact-adjudication", "verdicts", fact_adaptor._parse_verdict,
        id="fact-adjudication",
    ),
]


@pytest.mark.parametrize(("objective", "envelope_key", "parser"), CASES)
def test_the_objective_declares_exactly_the_fields_the_parser_reads(
    objective, envelope_key, parser,
):
    declared: set[str] = set()
    for item in _return_examples(objective, envelope_key):
        declared |= set(item)
    read = _keys_read_by(parser)

    assert declared == read, (
        f"{objective}.md and {parser.__name__} disagree.\n"
        f"  asked for but never read: {sorted(declared - read)}\n"
        f"  read but never asked for: {sorted(read - declared)}\n"
        "A field on one side only is a tier that parses cleanly and produces "
        "nothing, with every counter healthy."
    )


@pytest.mark.parametrize(("objective", "envelope_key", "parser"), CASES)
def test_the_extractor_names_the_objective_and_envelope_this_test_checked(
    objective, envelope_key, parser,
):
    """The positive control. The test above is also satisfied by a pair of
    files nothing loads — it compares two things without asserting either is
    the one production uses. This binds both ends to the adaptor's own
    declaration, so renaming `objective` or `envelope_key` on the class cannot
    leave this suite green against the file it no longer reads."""
    adaptors = [
        cls for cls in vars(fact_adaptor).values()
        if isinstance(cls, type) and getattr(cls, "objective", None) == objective
    ]
    assert adaptors, f"no adaptor in fact_adaptor declares objective={objective!r}"
    assert {a.envelope_key for a in adaptors} == {envelope_key}
    assert (OBJECTIVES / f"{objective}.md").is_file()


def test_the_ast_walk_actually_finds_keys():
    """A zero-assertion passes against a crash: if `_keys_read_by` returned an
    empty set for every input, the parity test above would be green whenever
    the objective declared nothing, and its failure message would be
    misleading in every other case. This is the run-that-ran control."""
    def sample(item):
        a = item["alpha"]
        b = item.get("beta")
        c = item.get("gamma", None)
        return a, b, c

    assert _keys_read_by(sample) == {"alpha", "beta", "gamma"}
