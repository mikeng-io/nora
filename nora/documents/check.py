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

1. **Shape.** Whatever arrives, dict included, is written to JSON first, so one path
   applies to all input: size, UTF-8, JSON (no NaN or Infinity, no duplicate key, no
   unpaired surrogate), then node count, depth and width measured with an explicit
   stack before anything recursive touches the tree, then no control character in
   any string (Postgres refuses NUL).
2. **Schema.** `document.schema.json`, generated from the catalog: closed vocabulary,
   strict props, allowlisted Mermaid source, depth capped at 8. JSON Schema cannot
   count nodes, which is why step 1 does. Its messages are rewritten for a model: a
   plain sentence per rule, in document terms, never the offending content at length.
3. **Rules the schema cannot state:** a Card inside a Card, a Stat inside a Card,
   a table row of the wrong width, an `asOf` that is not a real date.

On success it also returns what the rest of the system needs, computed ONCE here
and never re-derived by a reader: the title, the plain text the search indexes,
the canonical serialisation and its sha256. The canonical form is normalised first
(NFC on every string and key, `2.0` written `2`, `-0.0` written `0`) so one document
has one hash.
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
from jsonschema.exceptions import ValidationError

MAX_BYTES = 256 * 1024
MAX_NODES = 500
MAX_DEPTH = 8
MAX_CHILDREN = 200
MAX_TEXT_CHARS = 100_000
MAX_FINDINGS = 10
_MESSAGE_CHARS = 320  # our own sentences; content echoed from a document is capped at _ECHO_CHARS
_ECHO_CHARS = 40  # how much of the document a finding may repeat back to the model

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


def _clip(value: object, limit: int = _ECHO_CHARS) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


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
            raise ValueError(f"the key {_clip(key)!r} appears twice in one object")
        seen[key] = value
    return seen


def _to_bytes(raw: bytes | str | dict[str, Any]) -> tuple[bytes | None, Finding | None]:
    """Everything is written to JSON bytes first, a dict included, so one path
    judges all input. A dict skipped the size cap, NaN, duplicate and recursion
    guards of the bytes path, and a dict subclass could lie to `.get` (review,
    2026-10-01); written out and read back, it is plain JSON."""
    if isinstance(raw, bytes):
        return raw, None
    try:
        text = raw if isinstance(raw, str) else json.dumps(raw, allow_nan=False, ensure_ascii=False)
        return text.encode("utf-8"), None
    except UnicodeEncodeError:
        return None, Finding("", "encoding", "a string holds an unpaired surrogate")
    except (TypeError, ValueError, RecursionError) as exc:
        return None, Finding("", "json", f"cannot be written as JSON ({type(exc).__name__})")


def _parse(data: bytes) -> tuple[Any, Finding | None]:
    if len(data) > MAX_BYTES:
        return None, Finding("", "size", f"{len(data)} bytes; the limit is {MAX_BYTES}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, Finding("", "encoding", "the file is not valid UTF-8")
    try:
        return json.loads(text, parse_constant=_no_constant, object_pairs_hook=_no_duplicates), None
    except (ValueError, RecursionError) as exc:
        return None, Finding("", "json", _clip(str(exc), _MESSAGE_CHARS) or "not valid JSON")


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


# C0 controls except tab, newline and carriage return; DEL; lone surrogates (a JSON
# `\ud800` escape decodes to one, and cannot be written as UTF-8 or stored).
_BAD_CHAR = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def _pointer(path: str, key: object) -> str:
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


def _characters(document: Any) -> Finding | None:
    """No NUL or other control character, and no unpaired surrogate, in any string
    or key. Postgres `text` refuses NUL and `jsonb` refuses \\u0000, and a control
    character in a document is never something a person meant. Iterative."""
    stack: list[tuple[Any, str]] = [(document, "")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, str):
            match = _BAD_CHAR.search(value)
            if match:
                bad = match.group()
                rule = "unpaired_surrogate" if "\ud800" <= bad <= "\udfff" else "control_character"
                return Finding(path, rule, f"contains U+{ord(bad):04X}, which is not allowed")
        elif isinstance(value, dict):
            for key, item in value.items():
                if _BAD_CHAR.search(key):
                    return Finding(path, "control_character", "a key contains a control character")
                stack.append((item, _pointer(path, key)))
        elif isinstance(value, list):
            stack.extend((item, _pointer(path, index)) for index, item in enumerate(value))
    return None


# ----------------------------------------------------------------- 2. schema
_PATTERNS = {
    "url": "must be an http(s) address of printable ASCII, with no spaces",
    "asOf": "must be a date written YYYY-MM-DD",
    "media": "must be a media code (6 to 64 lowercase hex characters), never a web address",
    "source": (
        "must start with a diagram keyword (flowchart, graph, sequenceDiagram, stateDiagram-v2, "
        "stateDiagram, classDiagram, erDiagram, gantt, timeline, mindmap) and hold plain drawing "
        "instructions: no frontmatter, %%{, @{, click/link/callback statement, backslash, url() "
        "or unusual whitespace"
    ),
}


def _describe(error: ValidationError) -> str:
    """A rule in document terms. The offending content is named, never repeated at
    length: a finding goes back to a model, and a long echo of a hostile value is a
    way to hand it instructions (review, 2026-10-01)."""
    rule, want, got = str(error.validator), error.validator_value, error.instance
    last = str(error.absolute_path[-1]) if error.absolute_path else ""
    if rule == "required":
        return "missing " + ", ".join(sorted(set(want) - set(got)))
    if rule == "additionalProperties":
        known = set((error.schema or {}).get("properties", {}))
        extra = sorted(_clip(k) for k in got if k not in known)[:5]
        return f"unknown {'key' if len(extra) == 1 else 'keys'}: {', '.join(extra)}; remove"
    if rule == "enum":
        allowed = ", ".join(str(v) for v in want[:25])
        return f"{_clip(repr(got))} is not allowed; use one of: {allowed}"
    if rule == "const":
        return f"must be {want!r}"
    if rule == "type":
        return f"must be {want}"
    if rule == "not":
        kind = got.get("type", "this component") if isinstance(got, dict) else "this component"
        return f"a {_clip(kind)} cannot have children"
    if rule in ("minItems", "maxItems"):
        size = len(got) if hasattr(got, "__len__") else "?"
        return f"needs {'at least' if rule == 'minItems' else 'at most'} {want} items (has {size})"
    if rule in ("minLength", "maxLength"):
        size = len(got) if hasattr(got, "__len__") else "?"
        bound = "at least" if rule == "minLength" else "at most"
        return f"must be {bound} {want} characters (is {size})"
    if rule in ("minimum", "maximum"):
        return f"must be {'at least' if rule == 'minimum' else 'at most'} {want}"
    if rule == "pattern":
        return _PATTERNS.get(last, "does not have the required form")
    return _clip(f"breaks the schema rule {rule}", _MESSAGE_CHARS)


def _schema_findings(document: Any) -> list[Finding]:
    # Most useful first. A wrong `type` (a Markdown where a Tab belongs) is the
    # finding that explains several below it, so it leads; then a missing or an
    # unknown key; then the detail. Within a rank, document order.
    def order(error: ValidationError) -> tuple[int, list[str]]:
        rule = str(error.validator)
        last = str(error.absolute_path[-1]) if error.absolute_path else ""
        if rule in {"const", "enum"} and last == "type":
            rank = 0
        elif rule in {"required", "additionalProperties", "not", "const", "enum"}:
            rank = 1
        else:
            rank = 2
        return rank, [str(p) for p in error.absolute_path]

    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for error in sorted(_validator().iter_errors(document), key=order):
        path = "".join(_pointer("", part) for part in error.absolute_path)
        key = (path, str(error.validator))
        if key in seen:
            continue
        seen.add(key)
        out.append(Finding(path, str(error.validator), _clip(_describe(error), _MESSAGE_CHARS)))
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
                                "is not a real date",
                            )
                        )
        for index, child in enumerate(node.get("children") or []):
            visit(child, f"{where}/children/{index}", in_card or kind == "Card")

    # Recursion here is bounded: the schema has already capped depth at MAX_DEPTH.
    visit(document["root"], "/root", False)
    return out


# ------------------------------------------------------------- normalisation
def _normalise(value: Any) -> Any:
    """The form that is hashed and stored: NFC on every string and key, an integral
    float written as an int, `-0.0` as `0`. Without it the same document had several
    hashes (`2` and `2.0`, an NFD and an NFC title). Bounded recursion: the schema
    has already fixed the structure's depth."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float) and value == int(value) and abs(value) < 2**53:
        return int(value)
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", k): _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


# ---------------------------------------------------------------- extraction
# Each pattern is linear: a class that excludes the delimiter it is scanning for, so
# no start rescans to the end (the first `_LINK` took 9.5 s on twelve nodes of `[`).
_LINK = re.compile(r"\[([^\[\]]*)\]\([^()]*\)")
_REFERENCE = re.compile(r"(?m)^[ \t]*\[[^\[\]]*\]:[ \t]*\S+.*$")
_AUTOLINK = re.compile(r"<[a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]*>|\b(?:https?|ftp)://\S+")
_MARKDOWN_NOISE = re.compile(r"(?m)^[ \t]*(?:[#>]+|[-*+]|\d+\.)[ \t]+|[*_`~|]+")
_DIAGRAM_STYLE = re.compile(r"(?m)^[ \t]*(?:style|classDef|linkStyle|class)\b.*$")
_DIAGRAM_HEAD = re.compile(
    r"^(?:flowchart|graph|sequenceDiagram|stateDiagram-v2|stateDiagram|classDiagram|erDiagram|gantt"
    r"|timeline|mindmap)(?:[ \t]+(?:TB|TD|BT|RL|LR)\b)?"
)
_DIAGRAM_NOISE = re.compile(r"[-=.<>|\[\](){}:;\"'#]+")
_SPACE = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _markdown(text: str) -> str:
    text = _REFERENCE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _AUTOLINK.sub(" ", text)
    return _squash(_MARKDOWN_NOISE.sub(" ", text))


def _diagram(text: str) -> str:
    text = _DIAGRAM_STYLE.sub(" ", text)
    text = _DIAGRAM_HEAD.sub(" ", text, count=1)
    return _squash(_DIAGRAM_NOISE.sub(" ", text))


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
            return [_markdown(p["text"])]
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
            return [s for i in items for s in (i["title"], _markdown(i["text"]))]
        case "Code":
            return [p.get("language", ""), p["text"]]
        case "Tab":
            return [p["label"]]
        case "Chart":
            unit = p.get("unit", "")
            return [p.get("title", ""), unit, p["caption"], *(d["label"] for d in p["data"])]
        case "Mermaid":
            return [p["caption"], _diagram(p["source"])]
        case "Map":
            return [p["caption"], *(pl["name"] for pl in p["places"])]
        case "Image":
            return [p["alt"], p.get("caption", "")]
        case _:
            return []


def _text(document: dict[str, Any]) -> str | None:
    """The plain text, or None once it passes the cap (checked as it grows, so a
    hostile document is refused early instead of being extracted in full)."""
    parts: list[str] = []
    total = 0
    for node, _, _ in _walk(document["root"]):
        for raw in _strings(str(node.get("type")), node.get("props") or {}):
            part = _squash(raw)
            if part:
                total += len(part) + 1
                if total > MAX_TEXT_CHARS:
                    return None
                parts.append(part)
    return "\n".join(parts)


# --------------------------------------------------------------------- entry
def check_document(raw: bytes | str | dict[str, Any]) -> Checked:
    """Judge one document. Never raises: a failure of the checker itself is
    `status="error"`, which no caller may read as `ok`."""
    try:
        data, problem = _to_bytes(raw)
        if data is None or problem:
            return _refused(problem or Finding("", "json", "unreadable"))
        document, problem = _parse(data)
        if problem:
            return _refused(problem)
        problem = _shape(document) or _characters(document)
        if problem:
            return _refused(problem)
        findings = _schema_findings(document)
        if findings:
            return Checked(status="refused", findings=tuple(findings))
        findings = _rule_findings(document)
        if findings:
            return Checked(status="refused", findings=tuple(findings[:MAX_FINDINGS]))
        document = _normalise(document)
        text = _text(document)
        if text is None:
            limit = f"more than {MAX_TEXT_CHARS} characters of text"
            return _refused(Finding("", "text", limit))
        canonical = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
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
