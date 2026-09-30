"""The document checker: the one place a model-written document is judged.

It runs in Nora's container, never in the sandbox VM: the VM is untrusted, and a
second copy of the check inside it would be a second implementation to keep in
step (design/artifact/00-design.md §23). `computer_export(to="document")` calls
`check_document` and, on a refusal, hands the model the `Finding`s so it can fix
the file and export again.

Three outcomes, never collapsed: `ok` (every rule held), `refused` (the document
breaks a rule; the findings say which, and where), `error` (the checker itself
failed; nothing is known about the document, and it is never treated as `ok`).

What it checks, in order, stopping at the first group that fails:

1. **Shape.** Size, UTF-8, JSON (no NaN or Infinity, no duplicate key), then node
   count, depth and width measured with an explicit stack before anything
   recursive touches the tree.
2. **Schema.** `document.schema.json`, generated from the catalog: closed
   vocabulary, strict props, allowlisted Mermaid source, depth capped at 8. JSON
   Schema cannot count nodes, which is why step 1 does.
3. **Rules the schema cannot state:** a Card inside a Card, a Stat inside a Card,
   a table row of the wrong width, an `asOf` that is not a real date.

On success it also returns what the rest of the system needs, computed ONCE here
and never re-derived by a reader: the title, the plain text the search indexes,
the canonical serialisation and its sha256.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

MAX_BYTES = 256 * 1024
MAX_NODES = 500
MAX_DEPTH = 8
MAX_CHILDREN = 200
MAX_TEXT_CHARS = 100_000
MAX_FINDINGS = 10
_MESSAGE_CHARS = 200

_SCHEMA_PATH = Path(__file__).with_name("document.schema.json")


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing wrong with a document. `path` is a JSON Pointer into it ("" for the
    whole document), `rule` names the rule, `message` says what to change."""

    path: str
    rule: str
    message: str


@dataclass(frozen=True, slots=True)
class Checked:
    status: Literal["ok", "refused", "error"]
    findings: tuple[Finding, ...] = ()
    document: dict[str, Any] | None = None
    title: str = ""
    text: str = ""
    canonical: str = ""
    sha256: str = ""
    nodes: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def summary(self) -> str:
        """The findings as lines the model can act on, capped."""
        lines = [f"{f.path or '(document)'}  {f.rule}: {f.message}" for f in self.findings]
        return "\n".join(lines)


def _refused(*findings: Finding) -> Checked:
    return Checked(status="refused", findings=findings)


@functools.cache
def _validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ------------------------------------------------------------------ 1. shape
def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON number")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"the key {key!r} appears twice in one object")
        seen[key] = value
    return seen


def _parse(raw: bytes | str) -> tuple[Any, Finding | None]:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_BYTES:
        return None, Finding("", "size", f"{len(data)} bytes; the limit is {MAX_BYTES}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, Finding("", "encoding", "the file is not valid UTF-8")
    try:
        return json.loads(text, parse_constant=_no_constant, object_pairs_hook=_no_duplicates), None
    except (ValueError, RecursionError) as exc:
        return None, Finding("", "json", str(exc)[:_MESSAGE_CHARS] or "not valid JSON")


def _walk(node: Any, path: str = "") -> Iterator[tuple[dict[str, Any], str, int]]:
    """Every node below `node`, depth first, with its JSON Pointer and depth (the
    root is 0). An explicit stack: nothing here recurses on the document's depth."""
    stack: list[tuple[Any, str, int]] = [(node, path, 0)]
    while stack:
        current, where, depth = stack.pop()
        if not isinstance(current, dict):
            continue
        yield current, where, depth
        kids = current.get("children")
        if isinstance(kids, list):
            for index in range(len(kids) - 1, -1, -1):
                stack.append((kids[index], f"{where}/children/{index}", depth + 1))


def _shape(document: Any) -> Finding | None:
    root = document.get("root") if isinstance(document, dict) else None
    for count, (node, where, depth) in enumerate(_walk(root, "/root"), start=1):
        if count > MAX_NODES:
            return Finding("/root", "nodes", f"more than {MAX_NODES} nodes; split the document")
        if depth > MAX_DEPTH:
            return Finding(where, "depth", f"nested deeper than {MAX_DEPTH} levels")
        kids = node.get("children")
        if isinstance(kids, list) and len(kids) > MAX_CHILDREN:
            return Finding(where, "width", f"more than {MAX_CHILDREN} children in one node")
    return None


# ----------------------------------------------------------------- 2. schema
def _schema_findings(document: Any) -> list[Finding]:
    errors = sorted(
        _validator().iter_errors(document), key=lambda e: [str(p) for p in e.absolute_path]
    )
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for error in errors:
        path = "".join(f"/{part}" for part in error.absolute_path)
        key = (path, str(error.validator))
        if key in seen:
            continue
        seen.add(key)
        out.append(Finding(path, str(error.validator), error.message[:_MESSAGE_CHARS]))
        if len(out) == MAX_FINDINGS:
            break
    return out


# ------------------------------------------------------------------ 3. rules
def _rule_findings(document: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []

    def visit(node: dict[str, Any], where: str, in_card: bool) -> None:
        kind = node.get("type")
        props = node.get("props") or {}
        if kind == "Card" and in_card:
            out.append(
                Finding(
                    where,
                    "card_in_card",
                    "a Card inside a Card; use rows and dividers inside a panel",
                )
            )
        if kind == "Stat" and in_card:
            out.append(
                Finding(
                    where, "stat_in_card", "a Stat inside a Card; put Stats in a Grid beside it"
                )
            )
        if kind == "Table":
            width = len(props.get("columns", []))
            for index, row in enumerate(props.get("rows", [])):
                if len(row) != width:
                    out.append(
                        Finding(
                            f"{where}/props/rows/{index}",
                            "table_row_width",
                            f"{len(row)} cells but the table has {width} columns",
                        )
                    )
        if kind == "Sources":
            for index, item in enumerate(props.get("items", [])):
                as_of = item.get("asOf")
                if as_of is not None:
                    try:
                        date.fromisoformat(as_of)
                    except ValueError:
                        out.append(
                            Finding(
                                f"{where}/props/items/{index}/asOf",
                                "bad_date",
                                f"{as_of} is not a date",
                            )
                        )
        for index, child in enumerate(node.get("children") or []):
            visit(child, f"{where}/children/{index}", in_card or kind == "Card")

    # Recursion here is bounded: the schema has already capped depth at MAX_DEPTH.
    visit(document["root"], "/root", False)
    return out


# ---------------------------------------------------------------- extraction
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_NOISE = re.compile(r"(?m)^[ \t]*(?:[#>]+|[-*+]|\d+\.)[ \t]+|[*_`~|]+")
_DIAGRAM_NOISE = re.compile(r"[-=.<>|\[\](){}:;\"'#]+")
_SPACE = re.compile(r"\s+")


def _plain(text: str, pattern: re.Pattern[str] | None = None) -> str:
    text = _LINK.sub(r"\1", text)
    text = (pattern or _MARKDOWN_NOISE).sub(" ", text)
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _strings(kind: str, p: dict[str, Any]) -> list[str]:
    """The human-readable strings of one node, in reading order. A URL, a media
    code, a colour word or an enum is not text a person searches for."""
    items = p.get("items", [])
    match kind:
        case "Page":
            return [p["title"], p.get("subtitle", "")]
        case "Section" | "Card":
            return [p.get("title", ""), p.get("description", "")]
        case "Markdown":
            return [_plain(p["text"])]
        case "Table":
            return [
                p.get("caption", ""),
                *p["columns"],
                *(cell for row in p["rows"] for cell in row),
            ]
        case "Badge":
            return [p["text"]]
        case "KeyValue":
            return [s for i in items for s in (i["key"], i["value"])]
        case "Callout":
            return [p["title"], p.get("text", "")]
        case "Timeline":
            return [s for i in items for s in (i["label"], i["title"], i.get("text", ""))]
        case "Sources":
            return [s for i in items for s in (i["title"], i.get("note", ""))]
        case "Checklist":
            return [s for i in items for s in (i["text"], i.get("note", ""))]
        case "Stat":
            return [p["label"], p["value"], p.get("detail", "")]
        case "Accordion":
            return [s for i in items for s in (i["title"], _plain(i["text"]))]
        case "Code":
            return [p.get("language", ""), p["text"]]
        case "Tab":
            return [p["label"]]
        case "Chart":
            return [p.get("title", ""), p["caption"], *(d["label"] for d in p["data"])]
        case "Mermaid":
            return [p["caption"], _plain(p["source"], _DIAGRAM_NOISE)]
        case "Map":
            return [p["caption"], *(pl["name"] for pl in p["places"])]
        case "Image":
            return [p["alt"], p.get("caption", "")]
        case _:
            return []


def _text(document: dict[str, Any]) -> str:
    parts: list[str] = []
    for node, _, _ in _walk(document["root"]):
        for s in _strings(str(node.get("type")), node.get("props") or {}):
            s = _SPACE.sub(" ", unicodedata.normalize("NFC", s)).strip()
            if s:
                parts.append(s)
    return "\n".join(parts)


# --------------------------------------------------------------------- entry
def check_document(raw: bytes | str | dict[str, Any]) -> Checked:
    """Judge one document. Never raises: a failure of the checker itself is
    `status="error"`, which no caller may read as `ok`."""
    try:
        if isinstance(raw, (bytes, str)):
            document, problem = _parse(raw)
            if problem:
                return _refused(problem)
        else:
            document = raw
        problem = _shape(document)
        if problem:
            return _refused(problem)
        findings = _schema_findings(document)
        if findings:
            return Checked(status="refused", findings=tuple(findings))
        findings = _rule_findings(document)
        text = _text(document)
        if len(text) > MAX_TEXT_CHARS:
            findings.append(
                Finding(
                    "", "text", f"{len(text)} characters of text; the limit is {MAX_TEXT_CHARS}"
                )
            )
        if findings:
            return Checked(status="refused", findings=tuple(findings[:MAX_FINDINGS]))
        canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return Checked(
            status="ok",
            document=document,
            title=str(document["root"]["props"]["title"]),
            text=text,
            canonical=canonical,
            sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            nodes=sum(1 for _ in _walk(document["root"])),
        )
    except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
        return Checked(
            status="error", findings=(Finding("", "checker_error", f"{type(exc).__name__}"),)
        )
