"""The sub-agent worker's prompt: its brief as the model reads it.

The worker starts blank, so this prompt is everything it knows about the request, and it is
three context blocks, each saying what it is and who wrote it (AGENTS.md: a model's input has
to explain itself):

- `subagent_launch`: written by code (when, in which timezone, for whom, in which folder);
- `subagent_brief`: written by Nora (objective, deliverable, inputs, documents to revise);
- `subagent_decisions`: what was settled before the launch, each `decision` carrying `by`,
  so "the operator decided" is distinguishable from "Nora assumed".

ONE formatter. Pure: a brief and its launch facts in, a string out. Every free-text value is
escaped by the shared renderers, so a decision's answer that holds a tag cannot write a block.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from nora.domain.blocks import BlockKey, ContextBlock, assemble, render_elements, restamp
from nora.domain.subagent import Decision, SubagentBrief, SubagentLaunch

_SEP = "\n\n"

#: The prompt's blocks: `(name, order, key)`. `key="turn"` because the worker's prompt is its own
#: call pattern with no established cache boundary (as the Cognition agenda's, whose shape this
#: is). Order is data here, not the order of the constructor calls below.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("subagent_launch", 10, "turn"),
    ("subagent_brief", 20, "turn"),
    ("subagent_decisions", 30, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {name: (order, key) for name, order, key in _BLOCKS}


def _launched(launch: SubagentLaunch) -> str:
    return launch.launched_at.astimezone(ZoneInfo(launch.tz)).strftime("%Y-%m-%d %H:%M")


def _brief_body(brief: SubagentBrief) -> str:
    parts: list[tuple[str, str]] = []
    if brief.title:
        parts.append(("title", brief.title))
    parts.append(("objective", brief.objective))
    if brief.deliverable:
        parts.append(("deliverable", brief.deliverable))
    return "\n".join(p for p in (
        render_elements(
            parts, tag="part", attrs=lambda part, _i: (("is", part[0]),),
            body=lambda part: part[1],
        ),
        render_elements(
            brief.inputs, tag="input", attrs=lambda path, _i: (("path", path),),
            body=lambda _path: None,
        ),
        render_elements(
            brief.document_ids, tag="document", attrs=lambda doc, _i: (("id", doc),),
            body=lambda _doc: None,
        ),
    ) if p)


def _decisions_body(decisions: tuple[Decision, ...]) -> str:
    return render_elements(
        decisions, tag="decision",
        attrs=lambda d, _i: (("question", d.question), ("answer", d.answer), ("by", d.by)),
        body=lambda _d: None,
    )


def render_brief(brief: SubagentBrief, launch: SubagentLaunch) -> str:
    """The worker's prompt."""
    return assemble([
        restamp(ContextBlock(
            name="subagent_launch", when_empty="self_close", body="",
            attrs=(
                ("launched", _launched(launch)), ("timezone", launch.tz),
                ("requester", launch.requester_name), ("folder", launch.folder),
            ),
        ), _ROW),
        restamp(ContextBlock(name="subagent_brief", body=_brief_body(brief)), _ROW),
        restamp(
            ContextBlock(name="subagent_decisions", body=_decisions_body(brief.decisions)),
            _ROW,
        ),
    ], sep=_SEP)
