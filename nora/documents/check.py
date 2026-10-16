"""The document checker: the one place a model-written document is judged.

It runs in Nora's container, never in the sandbox VM: the VM is untrusted, and a
second copy of the check inside it would be a second implementation to keep in
step. `computer_export(to="document")` calls
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
import math
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, cast

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
    guards of the bytes path, and a dict subclass could lie to `.get`; written out
    and read back, it is plain JSON. (An object that lies
    while it is written is judged on what it wrote: callers store `Checked.canonical`,
    never the object they passed in.)"""
    if isinstance(raw, bytes):
        return raw, None
    try:
        text = raw if isinstance(raw, str) else json.dumps(raw, allow_nan=False, ensure_ascii=False)
        return text.encode("utf-8"), None
    except UnicodeEncodeError:
        return None, Finding("", "encoding", "a string holds an unpaired surrogate")
    except Exception as exc:  # noqa: BLE001 - a hostile object can raise anything while being written
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
    except json.JSONDecodeError as exc:
        return None, Finding("", "json", _where_it_broke(text, exc))
    except (ValueError, RecursionError) as exc:
        return None, Finding("", "json", _clip(str(exc), _MESSAGE_CHARS) or "not valid JSON")


_CONTEXT_CHARS = 28


def _where_it_broke(text: str, exc: json.JSONDecodeError) -> str:
    """The parser's own message plus the characters around the break, marked, so a writer who sent
    a long spec as text can find the one bad character. A bare «Expecting ',' delimiter: line 1
    column 7248» gives a model nothing to fix in a long text. The usual cause is a straight double
    quote inside a string that was not escaped, so the hint names it."""
    start = max(0, exc.pos - _CONTEXT_CHARS)
    before = text[start : exc.pos].replace("\n", "\\n")
    after = text[exc.pos : exc.pos + _CONTEXT_CHARS].replace("\n", "\\n")
    return _clip(
        f"{exc.msg} at character {exc.pos}: ...{before}<<HERE>>{after}... "
        "Usual causes: an unescaped straight double quote inside a string, or a missing or "
        "extra comma.",
        _MESSAGE_CHARS,
    )


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
# Also the bidi overrides and isolates (U+202A-202E, U+2066-2069): a title or a code
# line that displays reversed is a way to show a reader something the text does not say.
_BAD_CHAR = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\u202a-\u202e\u2066-\u2069]")
# A terminal colour or cursor sequence, which a runbook's pasted output carries.
_ANSI = re.compile("\x1b\\[[0-9;?]*[ -/]*[@-~]")


def _pointer(path: str, key: object) -> str:
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


def _characters(document: Any) -> Finding | None:
    """No NUL or other control character, and no unpaired surrogate, in any string
    or key. Postgres `text` refuses NUL and `jsonb` refuses \\u0000, and a control
    character in a document is never something a person meant. ESC alone is let
    through in a Code block's `text` (pasted terminal output); `_strip_ansi` removes
    the colour sequences and refuses what is left. Iterative."""
    stack: list[tuple[Any, str, bool]] = [(document, "", False)]
    while stack:
        value, path, code_text = stack.pop()
        if isinstance(value, str):
            # Only the `text` string itself may carry ESC, never its siblings.
            match = _BAD_CHAR.search(
                value.replace("\x1b", "") if code_text and path.endswith("/text") else value
            )
            if match:
                bad = match.group()
                rule = "unpaired_surrogate" if "\ud800" <= bad <= "\udfff" else "control_character"
                return Finding(path, rule, f"contains U+{ord(bad):04X}, which is not allowed")
        elif isinstance(value, dict):
            is_code = value.get("type") == "Code"
            for key, item in value.items():
                if _BAD_CHAR.search(key):
                    return Finding(path, "control_character", "a key contains a control character")
                # `code_text` marks a Code node's props dict, then its `text` inside it.
                next_flag = (is_code and key == "props") or (code_text and key == "text")
                stack.append((item, _pointer(path, key), next_flag))
        elif isinstance(value, list):
            stack.extend((item, _pointer(path, index), False) for index, item in enumerate(value))
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
    way to hand it instructions."""
    # The stubs type these as `Any | Unset`; a ValidationError raised by iter_errors always
    # carries all three, so they are read as plain values.
    rule = str(error.validator)
    want = cast(Any, error.validator_value)
    got = cast(Any, error.instance)
    last = str(error.absolute_path[-1]) if error.absolute_path else ""
    if rule == "required":
        return "missing " + ", ".join(sorted(set(want) - set(got)))
    if rule == "additionalProperties":
        known = set(cast(Mapping[str, Any], error.schema).get("properties", {}))
        unknown = sorted(k for k in got if k not in known)
        shown = ", ".join(_clip(k) for k in unknown[:5])
        more = f" (and {len(unknown) - 5} more)" if len(unknown) > 5 else ""
        return f"unknown {'key' if len(unknown) == 1 else 'keys'}: {shown}{more}; remove"
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
    # Most useful first. A wrong `type` (a Markdown where a Section belongs) is the
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
def _minutes(clock: str) -> int:
    return int(clock[:2]) * 60 + int(clock[3:])


def _schedule_findings(props: dict[str, Any], where: str) -> list[Finding]:
    """A Schedule's blocks are placed by their times, so a block that runs backwards,
    leaves the axis or overlaps its neighbour in the same row draws wrongly, and the
    schema cannot compare two fields."""
    out: list[Finding] = []
    start, end = _minutes(props["start"]), _minutes(props["end"])
    if end <= start:
        out.append(Finding(f"{where}/props/end", "schedule_axis", "end is not after start"))
        return out
    for lane_index, lane in enumerate(props["lanes"]):
        spans: list[tuple[int, int, int]] = []
        for index, block in enumerate(lane["blocks"]):
            at = f"{where}/props/lanes/{lane_index}/blocks/{index}"
            a, b = _minutes(block["from"]), _minutes(block["to"])
            if b <= a:
                out.append(Finding(at, "schedule_block", "to is not after from"))
            elif a < start or b > end:
                out.append(Finding(at, "schedule_block", "falls outside the start to end axis"))
            else:
                spans.append((a, b, index))
        spans.sort()
        for (_, prev_end, _), (next_start, _, index) in pairwise(spans):
            if next_start < prev_end:
                at = f"{where}/props/lanes/{lane_index}/blocks/{index}"
                out.append(
                    Finding(at, "schedule_overlap", "overlaps the block before it in the same row")
                )
    return out


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
            footer = props.get("footer")
            if footer is not None and len(footer) != width:
                out.append(
                    Finding(
                        f"{where}/props/footer",
                        "table_footer_width",
                        f"{len(footer)} cells but the table has {width} columns",
                    )
                )
            align = props.get("align")
            if align is not None and len(align) != width:
                out.append(
                    Finding(
                        f"{where}/props/align",
                        "table_align_width",
                        f"{len(align)} alignments but the table has {width} columns",
                    )
                )
        if kind == "Progress" and props["value"] > props["max"]:
            out.append(
                Finding(f"{where}/props/value", "progress_over_max", "value is more than max")
            )
        if kind == "Chart" and props["kind"] == "donut":
            values = [d["value"] for d in props["data"]]
            if not 2 <= len(values) <= 8 or any(v <= 0 for v in values):
                out.append(
                    Finding(
                        f"{where}/props/data",
                        "donut_slices",
                        "a donut takes 2 to 8 slices, every value above zero",
                    )
                )
        if kind == "Countdown":
            try:
                date.fromisoformat(props["date"])
            except ValueError:
                out.append(Finding(f"{where}/props/date", "bad_date", "is not a real date"))
        if kind == "Schedule":
            out.extend(_schedule_findings(props, where))
        if kind == "Seating":
            capacity = props.get("capacity", 10)
            for index, table in enumerate(props["tables"]):
                if len(table["seats"]) > capacity:
                    out.append(
                        Finding(
                            f"{where}/props/tables/{index}/seats",
                            "table_over_capacity",
                            f"{len(table['seats'])} guests at a table of {capacity}",
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
def _normalise(document: Any) -> tuple[Any, Finding | None]:
    """The form that is judged, hashed and stored: NFC on every string and key, an
    integral float written as an int, `-0.0` as `0`. It runs BEFORE validation: run
    after, a value that passed could grow past its limit or turn into a statement the
    Mermaid allowlist refuses (U+0958 x 40 becomes 80 characters, U+212A becomes `K`).
    Without it the same document would have several hashes.
    Iterative, because it runs on structure the schema has not yet bounded."""
    box: dict[str, Any] = {}
    stack: list[tuple[Any, Any, Any, str]] = [(document, box, "root", "")]
    while stack:
        value, parent, key, path = stack.pop()
        if isinstance(value, str):
            parent[key] = unicodedata.normalize("NFC", value)
        elif (
            isinstance(value, float)
            and math.isfinite(value)
            and value == int(value)
            and abs(value) < 2**53
        ):
            parent[key] = int(value)
        elif isinstance(value, dict):
            made: dict[str, Any] = {}
            parent[key] = made
            for k, v in value.items():
                nk = unicodedata.normalize("NFC", k)
                if nk in made:
                    return None, Finding(
                        path,
                        "duplicate_key",
                        f"two keys are the same once normalised: {_clip(nk)!r}",
                    )
                made[nk] = None
                stack.append((v, made, nk, _pointer(path, nk)))
        elif isinstance(value, list):
            items: list[Any] = [None] * len(value)
            parent[key] = items
            stack.extend((v, items, i, _pointer(path, i)) for i, v in enumerate(value))
        else:
            parent[key] = value
    return box["root"], None


def _strip_ansi(document: Any) -> Finding | None:
    """Terminal colour and cursor sequences are removed from a Code block's text;
    an ESC that is not part of one is refused. Anywhere else ESC was refused already.
    It runs BEFORE normalisation and the schema, so the text that is validated,
    normalised and hashed is the text that is stored: run after them, stripping left
    an empty string that passed `minLength`, and joined characters that NFC had not
    seen side by side, so the same document hashed two ways.
    Nothing is assumed about the tree yet: the schema has not run."""
    root = document.get("root") if isinstance(document, dict) else None
    for node, where, _ in _walk(root, "/root"):
        props = node.get("props")
        if node.get("type") != "Code" or not isinstance(props, dict):
            continue
        text = props.get("text")
        if not isinstance(text, str):
            continue
        cleaned = _ANSI.sub("", text)
        if "\x1b" in cleaned:
            return Finding(
                f"{where}/props/text",
                "control_character",
                "contains U+001B outside a colour sequence",
            )
        props["text"] = cleaned
    return None


# ---------------------------------------------------------------- extraction
# Each pattern is linear: a class that excludes the delimiter it is scanning for, so
# no start rescans to the end (a quadratic pattern on a long run of `[` is a denial of service).
_LINK = re.compile(r"\[([^\[\]]*)\]\([^()]*\)")
_REFERENCE = re.compile(r"(?m)^[ \t]*\[[^\[\]]*\]:[ \t]*\S+.*$")
_AUTOLINK = re.compile(r"<[a-zA-Z][a-zA-Z0-9+.-]*:[^<>\s]*>|\b(?:https?|ftp)://\S+")
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
                *p.get("footer", []),
            ]
        case "Badge":
            return [p["text"]]
        case "KeyValue":
            return [s for i in items for s in (i["key"], i["value"])]
        case "Callout":
            return [p["title"], p.get("text", "")]
        case "Timeline":
            return [
                s
                for i in items
                for s in (
                    i["label"],
                    i["title"],
                    _markdown(i.get("text", "")),
                    *i.get("people", []),
                )
            ]
        case "Sources":
            return [s for i in items for s in (i["title"], i.get("note", ""))]
        case "Checklist":
            return [
                p.get("title", ""),
                *(s for i in items for s in (i["text"], i.get("note", ""), i.get("owner", ""))),
            ]
        case "Stat":
            return [p["label"], p["value"], p.get("detail", "")]
        case "Accordion":
            return [s for i in items for s in (i["title"], _markdown(i["text"]))]
        case "Code":
            return [p.get("language", ""), p["text"]]
        case "Chart":
            unit = p.get("unit", "")
            return [p.get("title", ""), unit, p["caption"], *(d["label"] for d in p["data"])]
        case "Mermaid":
            return [p["caption"], _diagram(p["source"])]
        case "Map":
            return [p["caption"], *(pl["name"] for pl in p["places"])]
        case "Image":
            return [p["alt"], p.get("caption", "")]
        case "Progress":
            return [p["label"], p.get("detail", "")]
        case "Countdown":
            return [p["label"], p["date"], p.get("time", ""), p.get("detail", "")]
        case "Contact":
            return [
                s
                for i in items
                for s in (i["name"], i.get("role", ""), i.get("phone", ""), i.get("note", ""))
            ]
        case "Seating":
            return [
                p["caption"],
                *(
                    s
                    for t in p["tables"]
                    for s in (
                        t["name"],
                        t.get("label", ""),
                        *(
                            x
                            for g in t["seats"]
                            for x in (g["name"], g.get("role", ""), g.get("note", ""))
                        ),
                    )
                ),
            ]
        case "Schedule":
            # The times are searchable like a Timeline's labels: "who is on at 11:00".
            return [
                p["caption"],
                p["start"],
                p["end"],
                *(
                    s
                    for lane in p["lanes"]
                    for s in (
                        lane["name"],
                        *(
                            x
                            for b in lane["blocks"]
                            for x in (b["from"], b["to"], b["title"], b.get("note", ""))
                        ),
                    )
                ),
            ]
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
        problem = _shape(document) or _characters(document) or _strip_ansi(document)
        if problem:
            return _refused(problem)
        document, problem = _normalise(document)
        if problem:
            return _refused(problem)
        findings = _schema_findings(document)
        if findings:
            return Checked(status="refused", findings=tuple(findings))
        findings = _rule_findings(document)
        if findings:
            return Checked(status="refused", findings=tuple(findings[:MAX_FINDINGS]))
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
