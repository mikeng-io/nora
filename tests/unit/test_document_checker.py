"""The document checker (`nora.documents.check`): what it accepts, what it refuses and
why, and that it never raises and never reads a failure of its own as a pass.

The corpus is the one generated from the TypeScript catalog (`nora/documents/corpus.json`),
the same documents that Zod and ajv are tested on, so the three judges are compared on
identical inputs. Design: design/artifact/00-design.md §22, §23.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from nora.documents import check as checker
from nora.documents.check import MAX_BYTES, MAX_FINDINGS, Checked, check_document

CORPUS: dict[str, Any] = json.loads(
    (Path(checker.__file__).with_name("corpus.json")).read_text(encoding="utf-8")
)


def _page(*children: Any, title: str = "T") -> dict[str, Any]:
    root = {"type": "Page", "props": {"title": title}, "children": list(children)}
    return {"version": 1, "root": root}


def _rules(result: Checked) -> set[str]:
    return {f.rule for f in result.findings}


# ------------------------------------------------------------------ the corpus
@pytest.mark.parametrize("label", sorted(CORPUS["valid"]))
def test_a_valid_document_is_accepted(label: str) -> None:
    result = check_document(CORPUS["valid"][label])
    assert result.ok, result.summary()
    assert result.title
    assert result.text
    assert len(result.sha256) == 64


@pytest.mark.parametrize("case", CORPUS["invalid"], ids=[c["label"] for c in CORPUS["invalid"]])
def test_an_invalid_document_is_refused_with_findings(case: dict[str, Any]) -> None:
    result = check_document(case["doc"])
    assert result.status == "refused", (result.status, result.summary())
    assert result.findings
    for f in result.findings:
        assert f.rule
        assert f.message
        assert len(f.message) <= 200
        assert f.path == "" or f.path.startswith("/")


# ----------------------------------------------------- shape, before any schema
def test_a_document_over_the_size_limit_is_refused_by_size() -> None:
    result = check_document(b" " * (MAX_BYTES + 1))
    assert _rules(result) == {"size"}


def test_bytes_that_are_not_utf8_are_refused_by_encoding() -> None:
    assert _rules(check_document(b'{"a": "\xff"}')) == {"encoding"}


@pytest.mark.parametrize(
    "text",
    ["", "not json", "{", '{"version": 1,}', "[1, 2"],
)
def test_text_that_is_not_json_is_refused_by_json(text: str) -> None:
    assert _rules(check_document(text)) == {"json"}


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_constant_is_refused_by_json(constant: str) -> None:
    text = (
        '{"version":1,"root":{"type":"Page","props":{"title":"T"},"children":['
        f'{{"type":"Chart","props":{{"kind":"bar","caption":"c","data":[{{"label":"a","value":{constant}}},'
        '{"label":"b","value":2}]}}]}}'
    )
    result = check_document(text)
    assert _rules(result) == {"json"}
    assert constant in result.findings[0].message


def test_a_duplicate_key_is_refused_by_json_not_silently_resolved() -> None:
    text = '{"version":1,"version":2,"root":{"type":"Page","props":{"title":"T"},"children":[]}}'
    result = check_document(text)
    assert _rules(result) == {"json"}
    assert "version" in result.findings[0].message


def test_more_than_500_nodes_is_refused_even_though_the_schema_allows_it() -> None:
    # JSON Schema cannot count nodes (tests/unit/test_document_schema.py pins that),
    # so this is the rule that bounds size.
    leaf = {"type": "Badge", "props": {"text": "x"}}
    grids = [{"type": "Grid", "props": {}, "children": [leaf] * 100} for _ in range(5)]
    result = check_document(_page(*grids))
    assert _rules(result) == {"nodes"}
    assert result.findings[0].path == "/root"


def test_nesting_past_the_cap_is_refused_by_depth_with_its_path() -> None:
    node: dict[str, Any] = {"type": "Markdown", "props": {"text": "x"}}
    for _ in range(9):
        node = {"type": "Card", "props": {}, "children": [node]}
    result = check_document(_page(node))
    assert _rules(result) == {"depth"}
    assert result.findings[0].path.count("/children/") >= 9


def test_more_than_200_children_in_one_node_is_refused_by_width() -> None:
    leaf = {"type": "Badge", "props": {"text": "x"}}
    assert _rules(check_document(_page(*([leaf] * 201)))) == {"width"}


def test_a_hostile_chain_is_refused_fast_and_never_raises() -> None:
    node: dict[str, Any] = {"type": "Markdown", "props": {"text": "x"}}
    for _ in range(3000):
        node = {"type": "Card", "props": {}, "children": [node]}
    result = check_document(_page(node))
    assert result.status == "refused"
    assert _rules(result) == {"nodes"} or _rules(result) == {"depth"}


# ------------------------------------------------------------- the schema group
def test_a_finding_points_at_the_offending_prop() -> None:
    doc = _page({"type": "Section", "props": {"className": "fixed inset-0"}})
    result = check_document(doc)
    assert result.status == "refused"
    assert any(f.path == "/root/children/0/props" for f in result.findings), result.summary()


def test_the_number_of_findings_is_capped() -> None:
    bad = {"type": "Badge", "props": {"text": "x", "tone": "neon"}}
    result = check_document(_page(*([bad] * 40)))
    assert result.status == "refused"
    assert len(result.findings) == MAX_FINDINGS


# ---------------------------------------------------- rules the schema cannot state
def test_a_card_inside_a_card_is_refused() -> None:
    inner = {"type": "Card", "props": {}, "children": []}
    outer = {
        "type": "Card",
        "props": {},
        "children": [{"type": "Section", "props": {}, "children": [inner]}],
    }
    result = check_document(_page(outer))
    assert _rules(result) == {"card_in_card"}
    assert result.findings[0].path == "/root/children/0/children/0/children/0"


def test_a_stat_inside_a_card_is_refused_but_beside_one_is_fine() -> None:
    stat = {"type": "Stat", "props": {"label": "a", "value": "1"}}
    card = {"type": "Card", "props": {}, "children": [stat]}
    assert _rules(check_document(_page(card))) == {"stat_in_card"}
    assert check_document(
        _page({"type": "Grid", "props": {}, "children": [stat, {"type": "Card", "props": {}}]})
    ).ok


def test_a_table_row_of_the_wrong_width_is_refused() -> None:
    table = {"type": "Table", "props": {"columns": ["a", "b"], "rows": [["1", "2"], ["3"]]}}
    result = check_document(_page(table))
    assert _rules(result) == {"table_row_width"}
    assert result.findings[0].path == "/root/children/0/props/rows/1"


@pytest.mark.parametrize("as_of", ["2026-02-30", "2026-13-01", "0000-00-00"])
def test_an_as_of_that_is_not_a_real_date_is_refused(as_of: str) -> None:
    sources = {
        "type": "Sources",
        "props": {"items": [{"title": "t", "url": "https://a.example/", "asOf": as_of}]},
    }
    assert _rules(check_document(_page(sources))) == {"bad_date"}


def test_too_much_text_is_refused() -> None:
    chunk = {"type": "Markdown", "props": {"text": "word " * 4000}}
    result = check_document(_page(*([chunk] * 7)))
    assert _rules(result) == {"text"}


# --------------------------------------------------------- never raises, never ok
@pytest.mark.parametrize(
    "junk",
    [
        None,
        0,
        1.5,
        True,
        [],
        {},
        "",
        b"",
        [[[[]]]],
        {"version": 1},
        {"version": 1, "root": None},
        {"version": 1, "root": {"type": "Page"}},
        {"root": {"children": "x"}},
        b"\x00\x01\x02",
        "{" * 100_000,
    ],
)
def test_junk_is_refused_and_never_raises(junk: Any) -> None:
    result = check_document(junk)
    assert not result.ok
    assert result.status in {"refused", "error"}
    assert result.findings


def test_a_failure_of_the_checker_itself_is_error_and_never_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise RuntimeError("the schema file is gone")

    monkeypatch.setattr(checker, "_validator", boom)
    result = check_document(_page())
    assert result.status == "error"
    assert not result.ok
    assert _rules(result) == {"checker_error"}
    # The message names the kind of failure and leaks nothing else.
    assert "schema file" not in result.findings[0].message


# -------------------------------------------------- what an accepted document yields
def test_the_hash_is_of_the_canonical_form_and_ignores_key_order() -> None:
    a = check_document('{"version":1,"root":{"type":"Page","props":{"title":"T"},"children":[]}}')
    b = check_document('{"root":{"children":[],"props":{"title":"T"},"type":"Page"},"version":1}')
    c = check_document(_page(title="Other"))
    assert a.ok and b.ok and c.ok
    assert a.sha256 == b.sha256 != c.sha256
    import hashlib

    assert a.sha256 == hashlib.sha256(a.canonical.encode("utf-8")).hexdigest()
    assert json.loads(a.canonical) == a.document


def test_cantonese_is_kept_as_written_not_escaped() -> None:
    result = check_document(
        _page(
            {"type": "Markdown", "props": {"text": "我哋星期六去**尖沙咀**食飯"}}, title="旅行計劃"
        )
    )
    assert result.ok
    assert result.title == "旅行計劃"
    assert "尖沙咀" in result.text
    assert "尖沙咀" in result.canonical
    assert unicodedata.is_normalized("NFC", result.text)


def test_text_is_nfc_normalised_so_a_search_matches() -> None:
    decomposed = "Café"  # e + combining acute
    result = check_document(_page({"type": "Badge", "props": {"text": decomposed}}))
    assert "Café" in result.text


def test_the_node_count_is_reported() -> None:
    leaf = {"type": "Badge", "props": {"text": "x"}}
    assert check_document(_page(leaf, leaf)).nodes == 3


# ----------------------------------------------------------------- the search text
_SKIP = {"url", "media", "asOf", "kind", "tone", "columns_count"}
_PROSE = {
    "text_md": ("Markdown", "text"),
    "accordion": ("Accordion", "text"),
    "mermaid": ("Mermaid", "source"),
}


def _props(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    stack = [doc["root"]]
    while stack:
        n = stack.pop()
        out.append((n["type"], n.get("props") or {}))
        stack.extend(n.get("children") or [])
    return out


def _leaf_strings(value: Any, key: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, list):
        return [pair for v in value for pair in _leaf_strings(v, key)]
    if isinstance(value, dict):
        return [pair for k, v in value.items() for pair in _leaf_strings(v, k)]
    return []


@pytest.mark.parametrize("label", sorted(k for k in CORPUS["valid"] if k.startswith("demo ")))
def test_every_readable_string_of_a_demo_reaches_the_search_text(label: str) -> None:
    # The text is computed once and read by both the portal and the agent. A
    # component whose strings are missing from it is a document search cannot find.
    result = check_document(CORPUS["valid"][label])
    assert result.ok
    flat = " ".join(result.text.split())
    missing: list[str] = []
    for kind, props in _props(CORPUS["valid"][label]):
        for key, value in _leaf_strings(props):
            if key in _SKIP:
                continue
            if (kind, key) in _PROSE.values():
                # Markdown and Mermaid are reduced to words; compare the words.
                words = re.findall(r"[0-9A-Za-z一-鿿]{3,}", value)
                missing += [f"{kind}.{key}:{w}" for w in words if w not in flat]
            elif " ".join(value.split()) not in flat:
                missing.append(f"{kind}.{key}:{value}")
    assert not missing, missing[:8]


def test_a_url_a_media_code_and_a_tone_are_not_in_the_search_text() -> None:
    result = check_document(CORPUS["valid"]["demo trip"])
    assert "https://" not in result.text
    assert "a1b2c3d4e5f6" not in result.text
    assert "warning" not in result.text.split()
