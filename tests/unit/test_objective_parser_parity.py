"""The SHIPPED objective's declared fields must be exactly the fields the
parser reads.

An objective is a prompt: it tells the
model what to return. A parser is code: it decides what to read. Nothing
connects them — not a type, not an import, not the gate. When they drift, the
model returns a field nobody reads, or the code reads a field nobody was asked
for, and every counter in the tier stays healthy because the envelope
arrived, the items parsed, and the result was empty. The tier is wired end to
end and inert.

Two properties make this a check rather than a claim:

1. It reads the SHIPPED objective, from `nora/dreamer/objectives/`, not a
   fixture. The objectives are `NATIVELY_AUTHORED` — no byte-pin,
   operator-editable on the bind mount — so a fixture would be a copy that
   drifts from the file the container loads, which is the same failure one
   level down.
2. It derives the parser's fields from the PARSER, by walking its AST for
   every `item[...]` / `item.get(...)` subscript. A hand-copied tuple would be
   a third place to drift; CLAUDE.md's rule about label vocabularies is the
   same rule.

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

import nora

NORA_ROOT = Path(nora.__file__).resolve().parent
OBJECTIVES = NORA_ROOT / "dreamer" / "objectives"


def _return_examples(objective: str, envelope_key: str) -> list[dict[str, object]]:
    """Every item under `envelope_key` in the objective's own worked example.

    The union across items is deliberate: an objective demonstrates optional
    fields by omitting them from one example and including them in another
    so any single item would understate the contract.
    """
    body = (OBJECTIVES / f"{objective}.md").read_text(encoding="utf-8")

    # The WHOLE file, not a `## What to return` section. Not every objective
    # has that heading — some state their return shape in running prose — so
    # anchoring on it made this
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
    them in another. Reading only the first object would make a field introduced
    later invisible, and taking the first `{` unconditionally would let a brace
    in prose ahead of the example silently break the parse in the `json.loads`
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


#: Where a reader can live. `nora.language` holds the Dreamer's adaptors;
#: `nora.engagement` is walked too because a reader on the REPLY path is not
#: an adaptor at all, and restricting the walk to one package is how a
#: contract goes uncovered.
READER_PACKAGES = ("nora.language", "nora.engagement")


def _adaptor_modules():
    """Every module that can hold an objective reader, DISCOVERED rather than
    listed, walked RECURSIVELY.

    A hand-written tuple is the hand-copied vocabulary this file exists to
    outlaw: a new adaptor in a NEW module would be invisible to the coverage
    check as well as to the parametrisation, so
    `test_every_shipped_adaptor_is_covered` would iterate the same tuple it is
    meant to police and could never fail.

    Recursive because `nora.engagement` has subpackages and a reader in one of
    them would otherwise be invisible in exactly the same way.
    """
    for name in READER_PACKAGES:
        package = importlib.import_module(name)
        for info in pkgutil.walk_packages(package.__path__, f"{name}."):
            # No try/except. A module under these packages that will not
            # import is a red test, not a skipped one — swallowing it is how a
            # reader becomes invisible to the very gate that polices readers.
            yield importlib.import_module(info.name)


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

    Three parse shapes exist in this repo and the test covers all three: a
    module-level function (`_parse_claim`); a method taking one item
    (`NoteCuratorAdaptor._ground`); and a loop reading `item[...]` INLINE in
    `accept` itself, with more keys read in a second method it calls — which
    is what `NoteCuratorAdaptor.accept` itself does before handing a resolved
    note off to `_ground`.

    So the unit is the ADAPTOR, not the parser — whatever `accept` reads, by
    whatever route, is what the objective must declare — and following calls
    one level deep catches every shape without needing to know which one an
    adaptor chose.

    The start is EVERY function on the class, not `accept` alone, because a
    reader that is not an adaptor has no `accept` at all. Naming the entry
    point would be a hand-copied vocabulary, and the breadth costs nothing.
    This is safe rather than over-broad because
    `_keys_read_by` only matches subscripts on a variable literally named
    `item`, which in these modules is only ever an envelope item.
    """
    sites = [f for f in vars(cls).values() if inspect.isfunction(f)]
    for fn in list(sites):
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
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


def _verbs_of(cls) -> dict[str, list[str]]:
    """Each write verb an adaptor offers (the `async def`s nested in its
    `verbs` method) and the parameters the model fills in."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.verbs)))
    return {
        node.name: [a.arg for a in node.args.args + node.args.kwonlyargs]
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name != "verbs"
    }


def _cases():
    for module, cls in _adaptors():
        if getattr(cls, "verbs", None) is not None:
            continue  # answers through its verbs, never an envelope
        yield pytest.param(
            cls.objective, cls.envelope_key, _read_sites(cls, module),
            id=cls.__name__,
        )


CASES = list(_cases())

#: The adaptors that answer through their own write verbs: their contract with
#: the objective is the verbs and their parameters, not an envelope's fields.
VERB_CASES = [
    pytest.param(cls.objective, _verbs_of(cls), id=cls.__name__)
    for _module, cls in _adaptors()
    if getattr(cls, "verbs", None) is not None
]


def test_the_case_list_is_derived_and_not_empty():
    """The run-that-ran control for the derivation itself. An empty or
    silently-shrinking `CASES` makes every parametrized test below vacuous, and
    a hand-written list is exactly the hand-copied vocabulary this file's own
    docstring outlaws."""
    assert len(CASES) + len(VERB_CASES) >= 1
    assert {c.id for c in VERB_CASES} >= {"CultureAdaptor"}


@pytest.mark.parametrize(("objective", "verbs"), VERB_CASES)
def test_the_objective_names_every_verb_and_every_argument_the_model_fills(
    objective, verbs,
):
    """The verb adaptor's parity: a verb the objective never names is one the
    model is not told to call, and an argument it never names is one the model
    fills by guessing. Both are a tier that runs and writes nothing."""
    text = (OBJECTIVES / f"{objective}.md").read_text(encoding="utf-8")
    assert verbs, f"{objective}: no verbs found"
    missing = [
        f"{verb}({arg})" if arg else verb
        for verb, args in verbs.items()
        for arg in ("", *args)
        if f"`{arg or verb}`" not in text
    ]
    assert not missing, f"{objective}.md never names: {missing}"


def test_every_shipped_adaptor_is_covered():
    """A new adaptor with a new objective must not arrive with zero coverage —
    including one in a module nobody added to a list, which a hand-written
    tuple could not see."""
    covered = {c.values[0] for c in (*CASES, *VERB_CASES)}
    for _module, cls in _adaptors():
        assert cls.objective in covered, (
            f"{cls.__name__} declares objective={cls.objective!r} and no case "
            f"covers it"
        )
        assert (OBJECTIVES / f"{cls.objective}.md").is_file()


def test_every_shipped_objective_is_read_by_something():
    """The inversion: the half that catches an objective nobody reads rather than
    a reader with no objective.

    Walking readers can only ever police the readers that exist. An objective
    file is a prompt shipped on the bind mount: it can be added, renamed or
    orphaned with nothing in the codebase mentioning it, and every counter in
    every tier stays healthy because no tier was ever asked to run it. So the
    directory is the population and the code is checked against it, not the
    other way round.

    The floor is deliberately weak — the objective's NAME appears somewhere in `nora/` — because
    a reader can declare its objective in more than one shape (a `DreamAdaptor` class attribute,
    or a module constant). A strong check would need to know which, which is the hand-copied
    vocabulary this file exists to outlaw. A weak check over the whole population still fails on
    the case that matters: an objective loaded by nobody.
    """
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in NORA_ROOT.rglob("*.py")
    )
    orphans = [
        path.stem for path in sorted(OBJECTIVES.glob("*.md"))
        if f'"{path.stem}"' not in source and f"'{path.stem}'" not in source
    ]
    assert not orphans, (
        f"objectives no code names: {orphans}. A prompt shipped on the bind "
        f"mount that nothing loads is inert, and no counter can say so."
    )


def test_the_orphan_check_can_actually_fire():
    """The run-that-ran control for the check above: a name that IS in the
    directory but not in the source has to come back as an orphan, or the
    assertion is `not []` over a list that is empty for the wrong reason."""
    source = "nothing here names anything"
    orphans = [
        path.stem for path in sorted(OBJECTIVES.glob("*.md"))
        if f'"{path.stem}"' not in source
    ]
    # Every objective shipped in the directory is an orphan against this source.
    assert len(orphans) == len(list(OBJECTIVES.glob("*.md"))) >= 3


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


# ---------------------------------------------------------------------------
# the objective's own worked example, through the runtime's egress screen
# ---------------------------------------------------------------------------


def test_the_worked_example_survives_the_egress_screen():
    """The gate that reads what the MODEL reads.

    `tests/contract/test_dream_adaptor_contract.py` drives each adaptor's
    items through the real `screen()`, and that is the right place for it —
    but those items are hand-written per entry, so an author who invents a
    clean item satisfies it while the objective on the bind mount still shows
    the model a uuid. This one has no fixture to be honest about: it reads the
    SHIPPED objective and screens the example it TELLS the model to copy.

    That is the whole mechanism of the defect. `dream_screen._offence` walks
    every string reachable in an item and `egress._UUID` matches any
    hyphenated uuid, so an objective whose example carries a row id teaches
    the model to return one — and every item it returns is then dropped
    before `accept`, while the run reports `ok`, `commit` writes nothing, and
    the tier is inert with every counter healthy.

    The remedy is never widening the screen: render an ordinal handle (`### note 1`), keep the id
    in the adaptor's own per-run map, resolve it in `accept`. The screen is the R-18/R-28 floor
    and an exemption for one tier is how that rule dies.
    """
    from nora.agent.dream_screen import screen
    from nora.obs.metrics import Metrics

    offences: dict[str, list[tuple[str, str]]] = {}
    for module, cls in _adaptors():
        if getattr(cls, "verbs", None) is not None:
            # No envelope to copy: what it is shown is the whole text, and
            # whatever example it holds is what a verb call will copy.
            items: list[dict[str, object]] = [{"objective": (
                OBJECTIVES / f"{cls.objective}.md").read_text(encoding="utf-8")}]
        else:
            items = _return_examples(cls.objective, cls.envelope_key)
        assert items, f"{cls.objective}.md shows no example items"
        _, rejected = screen(
            adaptor=cls.name, items=items, metrics=Metrics(),
        )
        if rejected:
            offences[f"{module.__name__}:{cls.objective}"] = [
                (r.reason, r.path) for r in rejected
            ]

    assert not offences, (
        f"an objective's own worked example carries something the runtime's "
        f"egress screen drops: {offences}. Whatever the example shows, the "
        f"model copies — and every copy is dropped before `accept`, silently, "
        f"with the run still reporting `ok`."
    )


def test_the_example_screen_would_catch_an_identifier():
    """The run-that-ran control for the test above. `not rejected` is also
    what an empty example list, a screen that rejects nothing, or an import
    that quietly resolved to a no-op would produce."""
    from nora.agent.dream_screen import screen
    from nora.obs.metrics import Metrics

    _, rejected = screen(
        adaptor="probe",
        items=({"note_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"},),
        metrics=Metrics(),
    )

    assert [r.reason for r in rejected] == ["identifier"]
