"""The JSON Schema a document is validated against, read the way the checker
will read it: by Python's `jsonschema`, with nothing else in front of it.

`nora/documents/document.schema.json` and `corpus.json` are generated from the
TypeScript catalog (`app/src/lib/documents/catalog.ts`, `npm run documents:emit`).
This file is the half of the comparison the app's own tests cannot run: they
check the schema under ajv, a JavaScript engine, where `$` and `\\d` mean what
they mean in the Zod regexes. Python's `$` also matches before a trailing
newline and its `\\d` matches other scripts' digits, which is how a link with a
trailing newline passes here and fails in Zod.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

DOCS = Path(__file__).resolve().parents[2] / "nora" / "documents"
SCHEMA: dict[str, Any] = json.loads((DOCS / "document.schema.json").read_text())
CORPUS: dict[str, Any] = json.loads((DOCS / "corpus.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def _page(*children: Any) -> dict[str, Any]:
    root = {"type": "Page", "props": {"title": "T"}, "children": list(children)}
    return {"version": 1, "root": root}


def test_the_schema_is_itself_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_the_corpus_is_not_empty() -> None:
    # A silent empty corpus would make every test below pass.
    assert len(CORPUS["valid"]) >= 10
    assert len(CORPUS["invalid"]) >= 40


@pytest.mark.parametrize("label", sorted(CORPUS["valid"]))
def test_a_valid_document_is_accepted(label: str) -> None:
    errors = [e.message for e in VALIDATOR.iter_errors(CORPUS["valid"][label])][:2]
    assert not errors, errors


@pytest.mark.parametrize("case", CORPUS["invalid"], ids=[c["label"] for c in CORPUS["invalid"]])
def test_an_invalid_document_is_refused(case: dict[str, Any]) -> None:
    assert not VALIDATOR.is_valid(case["doc"])


def test_defaulted_props_may_be_left_out() -> None:
    # The prompt fragment tells the model `tone?` and `columns?` are optional.
    # The schema must not list them as required.
    doc = _page(
        {"type": "Badge", "props": {"text": "x"}},
        {"type": "Grid", "props": {}, "children": []},
    )
    assert VALIDATOR.is_valid(doc)


@pytest.mark.parametrize("length", [3999, 4000, 4001])
def test_published_mermaid_limit_counts_unicode_code_points(length: int) -> None:
    from nora.documents.check import check_document

    prefix, suffix = 'flowchart LR\nA["', '"]'
    source = prefix + "😀" * (length - len(prefix) - len(suffix)) + suffix
    doc = _page({"type": "Mermaid", "props": {"source": source, "caption": "A diagram"}})
    assert len(source) == length
    assert VALIDATOR.is_valid(doc) is (length <= 4000)
    checked = check_document(doc)
    assert checked.ok is (length <= 4000)


@pytest.mark.parametrize(
    ("url", "as_of"),
    [
        ("https://a.example/\n", None),
        ("https://a.example/", "2026-09-30\n"),
        ("https://a.example/", "٢٠٢٦-٠٩-٣٠"),
    ],
)
def test_python_regex_quirks_do_not_widen_what_is_accepted(url: str, as_of: str | None) -> None:
    item: dict[str, Any] = {"title": "x", "url": url}
    if as_of:
        item["asOf"] = as_of
    assert not VALIDATOR.is_valid(_page({"type": "Sources", "props": {"items": [item]}}))


def _chain(kind: str, levels: int) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "Markdown", "props": {"text": "x"}}
    for _ in range(levels):
        node = {"type": kind, "props": {}, "children": [node]}
    return _page(node)


def test_a_hostile_chain_is_refused_fast_and_never_overflows_the_stack() -> None:
    # `anyOf` over every component is exponential in depth and recurses without
    # bound. `if`/`then` stops at the first mismatch and the nesting is unrolled
    # to the depth cap, so depth is bounded.
    for levels in (9, 12, 60, 400):
        started = time.monotonic()
        assert not VALIDATOR.is_valid(_chain("Card", levels))
        assert time.monotonic() - started < 2.0, f"depth {levels} took too long"


def test_a_wide_document_at_the_depth_cap_validates_in_linear_time() -> None:
    leaf = {"type": "Badge", "props": {"text": "x"}}
    node: dict[str, Any] = leaf
    for _ in range(6):
        node = {"type": "Card", "props": {}, "children": [node, leaf, leaf, leaf]}
    started = time.monotonic()
    assert VALIDATOR.is_valid(_page(node))
    assert time.monotonic() - started < 2.0


def test_a_non_finite_number_is_refused() -> None:
    # Python's json.loads reads 1e400 as inf, which has no place in a Chart, and a
    # bare `type: number` accepts it. The schema bounds the value instead.
    text = (
        '{"version":1,"root":{"type":"Page","props":{"title":"T"},"children":['
        '{"type":"Chart","props":{"kind":"bar","caption":"c","data":'
        '[{"label":"a","value":1e400},{"label":"b","value":2}]}}]}}'
    )
    assert not VALIDATOR.is_valid(json.loads(text))


def test_the_node_cap_is_a_rule_the_schema_cannot_state() -> None:
    # JSON Schema cannot count nodes. The app refuses more than 500 (parseDocument),
    # so a 502-node document is refused there and accepted here. This pins the gap
    # so nobody assumes the schema alone bounds size: the checker must count.
    leaf = {"type": "Badge", "props": {"text": "x"}}
    grids = [{"type": "Grid", "props": {}, "children": [leaf] * 100} for _ in range(5)]
    doc = _page(*grids)
    nodes = 1 + sum(1 + len(g["children"]) for g in grids)
    assert nodes > 500
    assert VALIDATOR.is_valid(doc)
