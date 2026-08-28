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
import importlib
import inspect
import json
import pkgutil
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


def _adaptor_modules():
    """Every module in `nora.language` that can hold a `DreamAdaptor`,
    DISCOVERED rather than listed.

    A hand-written tuple was the hand-copied vocabulary this file exists to
    outlaw, and it failed in the specific way the rule predicts: a new adaptor
    in a NEW module was invisible to the coverage check as well as to the
    parametrisation, so `test_every_shipped_adaptor_is_covered` iterated the
    same tuple it was meant to police and could never fail. Its own docstring
    claimed otherwise.
    """
    package = importlib.import_module("nora.language")
    for info in pkgutil.iter_modules(package.__path__):
        yield importlib.import_module(f"nora.language.{info.name}")


def _adaptors():
    """(module, class) for every adaptor declaring BOTH `objective` and
    `envelope_key` — the two fields `DreamerRuntime` actually uses to load the
    instruction and unwrap the reply."""
    for module in _adaptor_modules():
        for cls in vars(module).values():
            if (
                isinstance(cls, type)
                and isinstance(getattr(cls, "objective", None), str)
                and isinstance(getattr(cls, "envelope_key", None), str)
            ):
                yield module, cls


def _read_sites(cls, module) -> list:
    """`accept` and every function it calls — the adaptor's whole READ SURFACE.

    **Three parse shapes exist in this repo and the test has to cover all
    three.** A module-level function (`_parse_claim`); a method taking one item
    (`NoteRevisitor._accept_one`); and a loop reading `item[...]` INLINE in
    `accept` itself, with more keys read in a second method it calls — which is
    what `NoteCuratorAdaptor` does.

    The first version bound only to a callable whose first argument was named
    `item`. That covered two shapes and reported the third as UNCOVERED: a red
    test for a real gap, except the gap was in this file rather than in the
    curator. So the unit is the ADAPTOR, not the parser — whatever `accept`
    reads, by whatever route, is what the objective must declare — and
    following calls one level deep catches every shape without needing to know
    which one an adaptor chose.
    """
    sites = [cls.accept]
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.accept)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = None
        if isinstance(node.func, ast.Name):
            target = getattr(module, node.func.id, None)
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            target = getattr(cls, node.func.attr, None)
        if (
            inspect.isfunction(target)
            and target.__module__ == module.__name__
            and target not in sites
        ):
            sites.append(target)
    return sites


def _cases():
    for module, cls in _adaptors():
        yield pytest.param(
            cls.objective, cls.envelope_key, _read_sites(cls, module),
            id=cls.__name__,
        )


CASES = list(_cases())


def test_the_case_list_is_derived_and_not_empty():
    """The run-that-ran control for the derivation itself. An empty or
    silently-shrinking `CASES` makes every parametrized test below vacuous, and
    a hand-written list was exactly the hand-copied vocabulary this file's own
    docstring outlaws."""
    assert len(CASES) >= 4
    assert {c.id for c in CASES} >= {
        "FactCandidateAdaptor", "FactAdaptor", "NoteCuratorAdaptor",
    }


def test_every_shipped_adaptor_is_covered():
    """A new adaptor with a new objective must not arrive with zero coverage —
    including one in a module nobody added to a list, which is the case the
    previous hand-written tuple could not see."""
    covered = {c.values[0] for c in CASES}
    for _module, cls in _adaptors():
        assert cls.objective in covered, (
            f"{cls.__name__} declares objective={cls.objective!r} and no case "
            f"covers it"
        )
        assert (OBJECTIVES / f"{cls.objective}.md").is_file()


@pytest.mark.parametrize(("objective", "envelope_key", "sites"), CASES)
def test_the_objective_declares_exactly_the_fields_the_parser_reads(
    objective, envelope_key, sites,
):
    declared: set[str] = set()
    for item in _return_examples(objective, envelope_key):
        declared |= set(item)
    read: set[str] = set()
    for site in sites:
        read |= _keys_read_by(site)

    assert declared == read, (
        f"{objective}.md and "
        f"{', '.join(site.__name__ for site in sites)} disagree.\n"
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
