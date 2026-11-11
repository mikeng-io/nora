"""Reading the rendered agenda back: the emitted items, rows and bindings.

`parse_rendered_agenda` projects an emitted agenda (or a stored request's
user-role content) to its item structure, retaining exact bytes;
`shown_source_rows`, `ledger_source_row` and `rendered_new_sources` project the
source rows a request showed, for judges and for new-source recording. Pure:
strings in, typed rows out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from xml.parsers import expat

from nora.domain.prompt import escape_attr


@dataclass(frozen=True)
class RenderedAgendaRow:
    attributes: dict[str, str]
    text: str
    rendered: str


@dataclass(frozen=True)
class RenderedAgendaItem:
    attributes: dict[str, str]
    rendered: str
    rows: tuple[RenderedAgendaRow, ...]


@dataclass(frozen=True)
class RenderedAgendaBinding:
    attributes: dict[str, str]
    rendered: str


def parse_rendered_agenda(agenda: str | Mapping[str, Any]) -> list[RenderedAgendaItem]:
    """Read emitted item structure, retaining exact bytes and only user-role request content."""
    return _parse_shown(agenda)[0]


def _parse_shown(
    agenda: str | Mapping[str, Any],
) -> tuple[list[RenderedAgendaItem], list[RenderedAgendaBinding]]:
    if isinstance(agenda, Mapping):
        items: list[RenderedAgendaItem] = []
        bindings: list[RenderedAgendaBinding] = []
        for message in agenda.get("messages", []):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                content = "\n".join(p.get("text", "") for p in content or [])
            shown_items, shown_bindings = _parse_shown(content)
            items += shown_items
            bindings += shown_bindings
        return items, bindings
    payload = ("<root>" + agenda + "</root>").encode("utf-8")
    parser = expat.ParserCreate()
    stack: list[dict[str, Any]] = []
    items = []
    bindings = []

    def start(tag: str, attributes: dict[str, str]) -> None:
        if stack:
            stack[-1]["children"] += 1
        stack.append({"tag": tag, "attributes": attributes, "start": parser.CurrentByteIndex,
                      "text": [], "rows": [], "children": 0})

    def text(value: str) -> None:
        stack[-1]["text"].append(value)

    def end(tag: str) -> None:
        node = stack.pop()
        stop = parser.CurrentByteIndex
        self_closed = (not node["children"] and not node["text"]
                       and payload[stop - 2:stop] == b"/>")
        if not self_closed:
            stop = payload.index(b">", stop) + 1
        rendered = payload[node["start"]:stop].decode("utf-8")
        if tag == "item":
            items.append(RenderedAgendaItem(node["attributes"], rendered, tuple(node["rows"])))
        elif stack and stack[-1]["tag"] == "item" and "n" in node["attributes"]:
            stack[-1]["rows"].append(RenderedAgendaRow(
                node["attributes"], "".join(node["text"]), rendered))
        elif tag == "binding" and len(stack) == 2 and stack[1]["tag"] == "reads":
            # Only the renderer's own top-level `<reads>` block carries bindings;
            # one nested inside an item or row is text a message put there.
            bindings.append(RenderedAgendaBinding(node["attributes"], rendered))

    parser.StartElementHandler = start
    parser.CharacterDataHandler = text
    parser.EndElementHandler = end
    parser.Parse(payload, True)
    return items, bindings


def shown_source_rows(agenda: str | Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """The non-reaction source rows a request shows, with provenance, for judges.

    Each row carries its rendered bytes, its item's room label and the `<reads>`
    carrier binding that room's reads resolve through — how the row reached the
    writer, not where the event physically is. Only what the request itself
    shows is projected; a room with no shown binding, or a label shared by two
    bindings, is `unavailable` rather than guessed or looked up elsewhere.
    """
    items, bindings = _parse_shown(agenda)
    by_label: dict[str, list[RenderedAgendaBinding]] = {}
    for binding in bindings:
        label = binding.attributes.get("label")
        if label:
            by_label.setdefault(label, []).append(binding)
    rows = {}
    for item in items:
        room = item.attributes.get("room")
        label = room[1:-1] if room and room.startswith("«") and room.endswith("»") else room
        bound = by_label.get(label, []) if label else []
        carrier = bound[0].rendered if len(bound) == 1 else "unavailable"
        for row in item.rows:
            if row.attributes.get("reaction"):
                continue
            rows[row.attributes["n"]] = {
                "rendered": row.rendered,
                "room": f'room="{escape_attr(room)}"' if room else "unavailable",
                "carrier": carrier,
            }
    return rows


def ledger_source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A recorded source row as a judge may see it: its key and text only.

    A row the request never showed has no carrier binding to give; nothing else
    the ledger holds is projected beside it.
    """
    return {k: row[k] for k in ("key", "text") if k in row}


def rendered_new_sources(agenda: str) -> dict[str, Any]:
    """Fresh citable rows from the emitted agenda, after source expansion and clipping."""
    new_sources = {}
    for item in parse_rendered_agenda(agenda):
        for row in item.rows:
            n = row.attributes.get("n")
            if (n is None or row.attributes.get("own") or row.attributes.get("target")
                    or row.attributes.get("reaction")):
                continue
            source = row.attributes.get("source")
            # Context message rows carry source=; fresh messages do not.
            # Other fresh record families carry their typed source key.
            if source and source.split(":", 1)[0] != item.attributes.get("kind"):
                continue
            if source and source.startswith("message:"):
                continue
            key = source.split(":", 1) if source else ["message", n]
            new_sources[n] = {"key": key, "text": row.text}
    return new_sources



__all__ = [
    "RenderedAgendaItem", "RenderedAgendaRow", "ledger_source_row",
    "parse_rendered_agenda", "rendered_new_sources", "shown_source_rows",
]
