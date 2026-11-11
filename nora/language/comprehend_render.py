"""Declared occasions for comprehend and shadow reshape, with item-local clocks."""

from __future__ import annotations

import json
from collections import Counter

from nora.domain.blocks import BlockKey, ContextBlock, assemble, restamp
from nora.domain.composed import compose_body
from nora.domain.comprehend import Preview
from nora.domain.prompt import escape, one_line
from nora.language.computer_render import estimate_tokens

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("memory", 40, "turn"),
    ("cognition_orient", 60, "global"),
    ("cognition_act", 70, "global"),
)
_ROW = {name: (order, key) for name, order, key in _BLOCKS}


RESHAPE = """Read this entity's committed subgraph. Propose traits and unconfirmed guesses
for a person, culture baselines for a room or unit, or lessons for yourself from the closed
response episode. For a topic, propose its internal topic account (part="topic"),
about only this topic, with no links. Use the supplied per-person activity evidence
counts and first/last asserted times; absence of activity is not proof of no interest.
Cite Record/Rollup evidence only. Facts are rendered by code, never proposed.
Call profile_stage once
with proposals_in: each has part, text, relation_ids and evidence source keys. Each line
must draw on current relations and cite their evidence. These are SHADOW would-writes;
no live Character, culture, Lessons or entry store changes. Empty proposals means no change.
"""


def preview_text(preview: Preview, *, tokens: int = 1500,
                 shown_keys: set[tuple[str, str]] | None = None,
                 shown_relations: set[str] | None = None) -> str:
    """Whole labelled rows within the budget; an omitted row always says more.

    `shown_keys`, when handed a set, collects the node keys of the rows the
    budget actually emitted — never an omitted, truncated or merely-cited key.
    `shown_relations` collects the relation ids of the emitted edge rows the
    same way — the correction catalogue, which cannot out-live the prompt."""
    graphs = (preview.graph, *preview.candidates)
    rows: list[str] = []
    row_keys: dict[str, tuple[tuple[str, str], ...]] = {}
    row_relations: dict[str, str] = {}
    seen: set[str] = set()
    for graph in graphs:
        for node in graph.nodes:
            entity = node.entity
            fields = node.fields
            revision = fields.get("revision")
            revision = revision if isinstance(revision, dict) else {}
            state = (
                fields.get("status")
                or revision.get("state")
                or ("retired" if fields.get("retired_at") else "open")
                if fields
                else node.indexed.state
                if node.indexed
                else "unavailable"
            )
            if entity:
                label = (
                    f"{one_line(entity.name)} · state={entity.state} · "
                    f"created={entity.created_at or 'unavailable'} · "
                    f"{json.dumps(dict(entity.attributes), ensure_ascii=False, default=str)}"
                )
            elif node.indexed:
                label = (
                    f"{one_line(node.indexed.text[:200])} · time={node.indexed.at_ms} · "
                    f"state={state}"
                )
            else:
                label = "label=unavailable · time=unavailable · state=unavailable"
            if node.key[0] == "web_page":
                label = "found · " + label
            row = f"@{node.key[0]}:{node.key[1]} {label}"
            rows.append(row)
            row_keys.setdefault(row, (node.key,))
        for edge in graph.relations:
            r = edge.relation
            layer = {"record": "said", "rollup": "computed", "notebook": "judgement"}[r.layer]
            if "web_page" in (r.src[0], r.dst[0]):
                layer = "found"
            counts = Counter(e.source[0] for e in edge.evidence)
            counted = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
            row = (
                f"{r.id}: {r.src[0]}:{r.src[1]} ─{r.rel}─▶ {r.dst[0]}:{r.dst[1]} "
                f"{layer} · {r.status} · valid={r.valid_from}/{r.valid_to} · "
                f"asserted={r.asserted_at.isoformat()} · evidence={edge.evidence_count} {counted}"
            )
            rows.append(row)
            row_keys.setdefault(row, (r.src, r.dst))
            row_relations.setdefault(row, r.id)
    kept: list[str] = []
    partial = any(g.status == "partial" for g in graphs)
    for row in rows:
        if row in seen:
            continue
        seen.add(row)
        candidate = "\n".join([*kept, row])
        if estimate_tokens(escape(candidate)) > max(0, tokens - 100):
            partial = True
            continue
        kept.append(row)
        if shown_keys is not None:
            shown_keys.update(row_keys.get(row, ()))
        if shown_relations is not None and row in row_relations:
            shown_relations.add(row_relations[row])
    return "more=" + ("available" if partial else "none") + "\n" + "\n".join(kept)


def memory_result_text(text: str, *, query: str, depth: int) -> str:
    """The graph tool's labelled data uses the canonical block serializer too."""
    more, _, rows = text.partition("\n")
    return assemble(
        [
            restamp(
                ContextBlock(
                    name="memory",
                    body=compose_body(escape(rows)),
                    when_empty="render",
                    attrs=(
                        ("query", query),
                        ("depth", str(depth)),
                        ("more", more.removeprefix("more=")),
                    ),
                ),
                _ROW,
            )
        ]
    )


MEMORY_CRITERIA = (
    "Write to the memory graph with `memory_propose`: a relation when one of its "
    "types fits, a reading for anything else worth keeping, and a correction for a "
    "shown relation the rows contradict. likes/dislikes concerns a specific thing; "
    "interested_in a subject."
)


# Tool-gated stage guidance belongs to this declared context occasion.
_ORIENT_GUIDANCE = (
    (frozenset({"memory_search", "memory_read"}),
     "Use {tools} to find and inspect held memory before forming a new entity or relation."),
    (frozenset({"memory_propose"}), MEMORY_CRITERIA),
    (frozenset({"memory_propose"}),
     "Examples (synthetic): Ada says in row 5201, 'I love Lantern Game': propose Ada "
     "likes Lantern Game, citing 5201. Row 5230 says 'lol nice' with a bare link: "
     "nothing to keep."),
    (frozenset({"profile_read", "profile_revise"}),
     "Use {tools} for profiles: read the account before revising it, and change a "
     "line only when new evidence bears on it; a single instance is a guess, not a trait."),
    (frozenset({"standing_revise"}),
     "Use {tools} only to correct your Standing when it is wrong or missing something: "
     "i_owe is what you took on; hold_open is what you are keeping open and what would "
     "resolve it."),
)
_ACT_GUIDANCE = (
    (frozenset({"document_revise"}),
     "Use {tools} to carry a settled change into a shared document, preserving "
     "unrelated content."),
)
_NOTEBOOK_VERBS = (
    ("notes", ("note_create", "note_revise", "note_set_status", "note_merge")),
    ("memos", ("memo_create", "memo_revise", "memo_set_status")),
    ("events", ("event_propose", "event_revise", "event_set_status")),
)
_HISTORICAL_ORIENT = (
    "This session replays the past in source-time order: the clock line is the time of "
    "what you are shown, and nothing after it is known. Keep what you learn with the "
    "tools offered here.")
_HISTORICAL_ACT = (
    "History does not act: this replay offers no reminder, wake or present-day notebook "
    "entry, so decide nothing for today and end when your understanding is kept.")
_REMINDER_WORTH = (
    "A reminder earns its place when a moment is near, something is still open, and a "
    "word at the right hour helps the people involved: where to meet before dinner, a "
    "booking someone owes before its deadline, asking how an interview went that "
    "evening. Something settled, already raised, or weeks away is not one."
)
_REMIND_TOOLS = frozenset({"remind", "remind_move", "remind_cancel"})
_WAKE_TOOLS = frozenset({"wake_at", "wake_move", "wake_cancel"})
_NOTEBOOK_TOOLS = frozenset({
    "note_create", "note_revise", "note_set_status", "note_merge",
    "memo_create", "memo_revise", "memo_set_status",
    "event_propose", "event_revise", "event_set_status",
})


def _tool_list(tool_names: frozenset[str]) -> str:
    return ", ".join(f"`{name}`" for name in sorted(tool_names))


def _guidance(sections: tuple, tool_names: frozenset[str]) -> list[str]:
    return [text.format(tools=_tool_list(required & tool_names))
            for required, text in sections if required & tool_names]


def _remind_and_wake_guidance(tool_names: frozenset[str]) -> str | None:
    reminders = _REMIND_TOOLS & tool_names
    wakes = _WAKE_TOOLS & tool_names
    if not reminders and not wakes:
        return None
    reminder = "`remind`" if "remind" in tool_names else _tool_list(reminders)
    wake = "`wake_at`" if "wake_at" in tool_names else _tool_list(wakes)
    if reminders and wakes:
        return (f"Use {reminder} for a later room turn and {wake} for your own later look; "
                f"move or cancel either as the matter changes. {_REMINDER_WORTH}")
    if reminders:
        return (f"Use {reminder} for a later room turn; move or cancel it as the matter "
                f"changes. {_REMINDER_WORTH}")
    return f"Use {wake} for your own later look; move or cancel it as its reason changes."


def _notebook_verbs(tool_names: frozenset[str]) -> str:
    kinds = [f"{kind} with {_tool_list(frozenset(verbs) & tool_names)}"
             for kind, verbs in _NOTEBOOK_VERBS if frozenset(verbs) & tool_names]
    return "Write " + "; ".join(kinds) + "."


def render_session_system(doctrine: str, skills: str, *, tool_names: frozenset[str],
                          historical: bool = False) -> str:
    """Assemble each stage from the session's actual offered tools."""
    sections = [_HISTORICAL_ORIENT] if historical else []
    sections += _guidance(_ORIENT_GUIDANCE, tool_names)
    if tool_names & _NOTEBOOK_TOOLS:
        sections += [skills, _notebook_verbs(tool_names)]
    orient = assemble([restamp(ContextBlock(
        name="cognition_orient", body=compose_body(
            "\n\n".join(sections))), _ROW)])
    actions = [_HISTORICAL_ACT] if historical else []
    pace = _remind_and_wake_guidance(tool_names)
    if pace is not None:
        actions.append(pace)
    actions.extend(_guidance(_ACT_GUIDANCE, tool_names))
    act = assemble([restamp(ContextBlock(
        name="cognition_act", body=compose_body(
            "\n\n".join(actions))), _ROW)])
    orient_marker, act_marker = "<!-- cognition-orient -->", "<!-- cognition-act-skill -->"
    doctrine = (doctrine.replace(orient_marker, orient) if orient_marker in doctrine
                else doctrine + orient)
    return (doctrine.replace(act_marker, act) if act_marker in doctrine
            else doctrine + act)
