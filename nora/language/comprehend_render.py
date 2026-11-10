"""Declared occasions for comprehend and shadow reshape, with item-local clocks."""

from __future__ import annotations

import json
from collections import Counter

from nora.domain.blocks import BlockKey, ContextBlock, assemble, restamp
from nora.domain.composed import compose_body
from nora.domain.comprehend import Preview
from nora.domain.memory_graph import (
    ENTRY,
    PROPOSED_KINDS,
    RELATION_RULES,
)
from nora.domain.prompt import escape, one_line
from nora.language.computer_render import estimate_tokens

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("memory", 40, "turn"),
    ("cognition_orient", 60, "global"),
    ("cognition_act", 70, "global"),
)
_ROW = {name: (order, key) for name, order, key in _BLOCKS}


def _step_one(sources: tuple[str, ...]) -> tuple[str, ...]:
    """Sources step one may write from; an entry writes its own links (27 §7a)."""
    return tuple(k for k in sources if k not in ENTRY)


# The admission check's own vocabulary: a name outside it is refused, so the
# model is shown exactly the list code admits against — rendered, never copied.
_ADMITTED_RELATIONS = tuple(sorted(
    rel for rel, rule in RELATION_RULES.items()
    if rule.layer == "notebook" and _step_one(rule.sources)
))
_VOCABULARY = (
    "Entity kinds you may create: " + ", ".join(PROPOSED_KINDS) + ". "
    "Relations, the only names admitted: " + ", ".join(_ADMITTED_RELATIONS) + " — "
    "likes/dislikes is a preference for one specific thing; interested_in is a"
    " subject. Who said, shared, asked or replied is already recorded by code;"
    " propose only what the item establishes about the world."
)
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


MEMORY_CRITERIA = """# Orient

Keep claims about people and things, not a recap of chat events. If an item adds
nothing beyond who said, shared, asked, replied or reacted, mark it nothing_here.
Bare links, promo text and vague replies are nothing_here unless the rows state
a claim beyond that act. Preserve attribution and uncertainty; shared material
is not the sharer's endorsement, and public material cannot establish private
facts about a person.

Relations are the primary output: try the admitted relations against the people
and entities shown in the input, matching supplied entities before creating one.
Use a reading only when no admitted relation can hold the fact. Correct a
contradicted shown relation by its shown id; do not guess unseen state. People
are supplied identities, never new entities; a new entity needs an admitted
relation. For a reading, happened_at quotes the cited row's own time words or
null, never its send timestamp or a date you computed.

Call memory_propose with the item indices, statements citing their source rows,
and nothing_here for empty items. Cover each distinct claim once, including
other claims in a batched item. Read refusals and repair still-grounded claims;
a refusal is not nothing_here.
"""

_WORKED_EXAMPLES = """# Examples (synthetic)

Input: Ada (shown person ada), row 5201: “I love Lantern Game.”
Output: relation ada likes Lantern Game (reuse the shown work, or create it),
citing 5201. No reading is needed to repeat the preference.

Input: item 2, row 5230: “lol nice” with a bare link and no other claim.
Output: memory_propose(items=[2], statements=[], nothing_here=[2]);
close the item no_change.
"""

SESSION_INSTRUCTION = MEMORY_CRITERIA + "\n" + _VOCABULARY + "\n\n" + _WORKED_EXAMPLES

# Each section requires the tools it teaches, including loadable tools.
_TOOL_GUIDANCE = (
    (frozenset({"memory_propose", "memory_search", "memory_read"}),
     """Before proposing a new entity or relation, search memory with memory_search
and inspect the relevant connections with memory_read.
"""),
    (frozenset({"close"}),
     """After deciding and completing any actions, close understood items as written,
covered, no_change or waiting according to what happened. Missing evidence is
uncertainty, not an empty result.
"""),
    (frozenset({"profile_read", "profile_revise"}),
     """Reshape profiles only for entities whose relations changed this session:
profile_read before profile_revise. Connect the new evidence to the held account,
keeping facts, traits and tentative guesses distinct.
"""),
)
_ACT_GUIDANCE = (
    (frozenset({"standing_revise"}),
     """Standing carries your own position: use i_owe for responsibilities you actually
accepted and hold_open for questions you are keeping open, with what would resolve
them. Revise that position as the situation changes; noticing work is not accepting it.
"""),
    (frozenset({"wake_at", "wake_move", "wake_cancel"}),
     """A wake earns another look when a concrete change or time could resolve
something that matters. Its why is your reason; move or cancel it as that reason changes.
"""),
    (frozenset({"remind", "remind_move", "remind_cancel"}),
     """A reminder prepares a later room turn, not an immediate message. Write its text
for your later self to reconsider; an inference remains a question to the room.
Reminders set by people are theirs to change. Ground dates in the supplied clock.
"""),
    (frozenset({"document_read", "document_revise"}),
     """Shared documents are an index, never evidence of what a person said. Read the
current revision before revising from cited replacements; preserve unrelated content
and uncertainty, and do not retry a conflict automatically.
"""),
)
_NOTEBOOK_TOOLS = frozenset({
    "note_create", "note_revise", "note_set_status", "note_merge",
    "memo_create", "memo_revise", "memo_set_status",
    "event_propose", "event_revise", "event_set_status",
    "remind", "remind_move", "remind_cancel",
})


def render_session_system(doctrine: str, skills: str, *, tool_names: frozenset[str]) -> str:
    """Compose guidance from the same admitted surface the session can load."""
    sections = [SESSION_INSTRUCTION] if "memory_propose" in tool_names else []
    sections.extend(text for required, text in _TOOL_GUIDANCE if required <= tool_names)
    orient = assemble([restamp(ContextBlock(
        name="cognition_orient", body=compose_body("\n".join(sections))), _ROW)])
    skills = skills if tool_names >= _NOTEBOOK_TOOLS else ""
    actions = [text for required, text in _ACT_GUIDANCE if required <= tool_names]
    act = assemble([restamp(ContextBlock(
        name="cognition_act", body=compose_body("# Act\n\n" + "\n".join(actions)
                                                if actions else "")), _ROW)])
    if "<!-- cognition-orient -->" in doctrine:
        return doctrine.replace("<!-- cognition-orient -->", orient).replace(
            "<!-- cognition-act-skill -->", skills + act)
    return doctrine + orient + skills + act
