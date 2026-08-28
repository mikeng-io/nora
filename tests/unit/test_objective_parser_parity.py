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

from nora.language import fact_adaptor, note_revisitor

OBJECTIVES = Path(fact_adaptor.__file__).resolve().parents[1] / "dreamer" / "objectives"


def _return_examples(objective: str, envelope_key: str) -> list[dict[str, object]]:
    """Every item under `envelope_key` in the objective's own worked example.

    The union across items is deliberate: an objective demonstrates optional
    fields by omitting them from one example and including them in another
    (`fact-adjudication`'s `not_covered` entry carries no `source_url`), so any
    single item would understate the contract.
    """
    body = (OBJECTIVES / f"{objective}.md").read_text(encoding="utf-8")

    # The WHOLE file, not a `## What to return` section. That heading is the
    # fact tier's house style; `note-revisit` states its return shape in
    # running prose and has no such heading, so anchoring on it made this
    # test's coverage depend on a formatting convention rather than on the
    # contract. The discriminator is the ENVELOPE KEY the adaptor declares,
    # which is precise: no other JSON in an objective carries it.
    items: list[dict[str, object]] = []
    found_envelope = False
    for blob in _balanced_objects(body):
        try:
            envelope = json.loads(blob)
        except json.JSONDecodeError:
            # Prose containing braces, or a fragment. An objective's example is
            # valid JSON by construction — it is what the model is told to
            # return — so anything that will not parse is not one.
            continue
        if not isinstance(envelope, dict) or envelope_key not in envelope:
            continue
        found_envelope = True
        items.extend(envelope[envelope_key])
    assert found_envelope, (
        f"{objective}.md shows no worked example carrying the envelope key "
        f"{envelope_key!r} the adaptor declares — so the model is never shown "
        f"the shape the parser is written against"
    )
    return items


def _balanced_objects(text: str):
    """EVERY balanced `{...}` in the text, not just the first.

    Objectives show the empty case as a SECOND example (`{"candidates": []}`)
    and show optional fields by omitting them from one entry and including
    them in another. Reading only the first object made a field introduced
    later invisible, and taking the first `{` unconditionally meant a brace in
    prose ahead of the example silently broke the parse in the `json.loads`
    direction — a green test measuring nothing.
    """
    depth = 0
    start = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
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
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start:i + 1]
                start = None


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


#: Every module that can hold a `DreamAdaptor`. Named here rather than
#: discovered by import-walking the package: an adaptor living somewhere else
#: entirely is a real gap this list would miss, and `test_every_shipped_adaptor_
#: is_covered` below is what turns that gap into a red test instead of a silent
#: omission.
ADAPTOR_MODULES = (fact_adaptor, note_revisitor)


def _adaptors():
    """(module, class) for every adaptor declaring BOTH `objective` and
    `envelope_key` — the two fields `DreamerRuntime` actually uses to load the
    instruction and unwrap the reply."""
    for module in ADAPTOR_MODULES:
        for cls in vars(module).values():
            if (
                isinstance(cls, type)
                and isinstance(getattr(cls, "objective", None), str)
                and isinstance(getattr(cls, "envelope_key", None), str)
            ):
                yield module, cls


def _parsers_called_by(
    fn: Callable[..., object], module, owner: type | None = None,
) -> list[Callable[..., object]]:
    """The module-level functions `fn`'s body calls, in source order.

    This is what binds the checked parser to the adaptor that USES it. Without
    it the parity check compares two artefacts and asserts nothing about
    whether production reads either: point `accept` at a `_parse_claim_v2` and
    leave `_parse_claim` behind, and the old check stays green while measuring
    a function nothing calls — the exact failure this file exists to catch, one
    level up.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # `_parse_claim(item)` — a module-level function.
        if isinstance(node.func, ast.Name):
            target = getattr(module, node.func.id, None)
        # `self._accept_one(item, ...)` — a method. The note revisitor parses
        # this way, and a walk that only saw module-level names reported it as
        # UNCOVERED rather than checking it, which is the same silent gap in
        # the opposite direction.
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and owner is not None
        ):
            target = getattr(owner, node.func.attr, None)
        else:
            continue
        if _is_item_parser(target, module) and target not in out:
            out.append(target)
    return out


def _is_item_parser(target: object, module) -> bool:
    """A function in THIS module whose first real argument is one envelope
    item.

    `dataclasses.replace` and friends are module-level names too; the
    discriminator is the SHAPE the runtime hands an adaptor. `accept` receives
    `Sequence[Mapping[str, object]]`, and a parser takes one of those first —
    by convention named `item`, which is also the name `_keys_read_by` walks
    for, so anything else could not be checked even if it were included.
    """
    if not inspect.isfunction(target):
        return False
    if target.__module__ != module.__name__:
        return False
    params = [p for p in inspect.signature(target).parameters if p != "self"]
    return bool(params) and params[0] == "item"


def _cases():
    for module, cls in _adaptors():
        parsers = _parsers_called_by(cls.accept, module, owner=cls)
        # An adaptor whose `accept` calls no module-level function parses
        # inline; there is nothing to bind to and the pair is reported as
        # uncovered rather than silently skipped.
        for parser in parsers:
            yield pytest.param(
                cls.objective, cls.envelope_key, parser,
                id=f"{cls.__name__}:{parser.__name__}",
            )


CASES = list(_cases())


def test_the_case_list_is_derived_and_not_empty():
    """The run-that-ran control for the derivation itself. An empty or
    silently-shrinking `CASES` makes every parametrized test below vacuous, and
    a hand-written list was exactly the hand-copied vocabulary this file's own
    docstring outlaws."""
    assert len(CASES) >= 3
    assert {c.id for c in CASES} >= {
        "FactCandidateAdaptor:_parse_claim",
        "FactAdaptor:_parse_verdict",
    }


def test_every_shipped_adaptor_is_covered():
    """A new adaptor with a new objective must not arrive with zero coverage.
    Every class declaring an objective contributes at least one case, and every
    objective file an adaptor names exists."""
    covered = {c.values[0] for c in CASES}
    for _module, cls in _adaptors():
        assert cls.objective in covered, (
            f"{cls.__name__} declares objective={cls.objective!r} and no case "
            f"covers it — its `accept` calls no module-level parser this test "
            f"can bind to"
        )
        assert (OBJECTIVES / f"{cls.objective}.md").is_file()


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
