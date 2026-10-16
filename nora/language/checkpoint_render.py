"""`render_checkpoint`: the job checkpoint as compact markdown, ONE renderer for every reader.
The `<subagents>` block (running jobs), `subagent_read`, the worker's own
re-anchoring after its history is trimmed, and the delivery to the room all call it, so the
four sections cannot be written four ways.

Two kinds of line, kept apart on purpose. **Done / Doing / Next / Decisions are the worker's
words** and are rendered as such, unchecked. **Outputs are code's**: the documents and kept
files it recorded from tool results, and the rounds it counted. Outputs are rendered from
`Outputs` alone and never from the checkpoint, so a checkpoint that names a document nobody
published produces no output line: the claim stays a claim in the worker's own list.

`mode` says who reads and how much:

- `block`: a running job in the `<subagents>` block. Doing and Next only, a few items, each cut.
  No Outputs (the line above it already names the job), no tag description repeated beyond
  the block type's.
- `read`: `subagent_read`, everything, with the rounds.
- `anchor`: the worker's own, after trimming: everything, WITHOUT the rounds (nothing it reads
  states how much of the run is left or spent).
- `delivery`: the room's, as plain markdown for a carrier that escapes once (the compute body
  does), so no tag and no escaping here.

Pure: values in, a string out. The three tagged modes wrap the body in a `subagent_checkpoint`
`ContextBlock`, which carries its own description and escapes every value it is given.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from nora.domain.blocks import ContextBlock
from nora.domain.checkpoint import SECTIONS, Checkpoint, Outputs
from nora.domain.prompt import ago, escape, one_line

Mode = Literal["block", "read", "anchor", "delivery"]

#: In the `<subagents>` block a running job carries at most this many items per list, each cut.
BLOCK_ITEMS = 3
BLOCK_ITEM_CHARS = 80

_HEADINGS = {"done": "Done", "doing": "Doing", "next": "Next", "decisions": "Decisions"}


def _cut(text: str, limit: int) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def render_checkpoint(
    checkpoint: Checkpoint | None, outputs: Outputs | None, *, title: str, now: datetime,
    mode: Mode,
) -> str:
    """The checkpoint and outputs as markdown, or `""` when there is nothing to say (a job
    with no checkpoint that has made nothing)."""
    esc: Callable[[str], str] = (lambda s: s) if mode == "delivery" else (
        lambda s: escape(one_line(s))
    )
    lines: list[str] = []
    sections = ("doing", "next") if mode == "block" else SECTIONS
    if checkpoint is not None and not checkpoint.empty:
        for name in sections:
            items = checkpoint.section(name)
            if mode == "block":
                items = tuple(_cut(i, BLOCK_ITEM_CHARS) for i in items[:BLOCK_ITEMS])
            if items:
                lines.append(f"**{_HEADINGS[name]}**")
                lines += [f"- {esc(i)}" for i in items]
    if mode != "block" and outputs is not None and not outputs.empty:
        made: list[str] = []
        for doc_id, doc_title in outputs.documents:
            made.append(
                f"- document {esc(doc_title)} (id {esc(doc_id)})" if doc_title
                else f"- document id {esc(doc_id)}"
            )
        made += [f"- kept file {esc(f)}" for f in outputs.kept_files]
        if mode == "read" and outputs.rounds:
            made.append(f"- {outputs.rounds} model rounds so far")
        if made:
            lines.append("**Outputs** (recorded by code)")
            lines += made
    if not lines:
        return ""
    body = "\n".join(lines)
    if mode == "delivery":
        return body
    attrs: tuple[tuple[str, str], ...] = (("job", title),)
    if checkpoint is not None and not checkpoint.empty and checkpoint.at is not None:
        attrs += (("updated", ago(int((now - checkpoint.at).total_seconds() * 1000))),)
    return ContextBlock(name="subagent_checkpoint", body=body, attrs=attrs).render()
