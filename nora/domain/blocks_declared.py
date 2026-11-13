"""Declared-occasion assembly: a `_BLOCKS` table of BlockDeclarations and the
one `assemble_declared` walk that renders it, fail-closed on every undeclared
or failed read (the no-ad-hoc-prompt rule). Imported back by `nora.domain.blocks`,
whose import surface the declared-occasion test matches.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from nora.domain.block_types import BLOCK_TYPES
from nora.domain.composed import compose_body

if TYPE_CHECKING:
    from nora.domain.blocks import BlockKey, ContextBlock


@dataclass(frozen=True)
class BlockDeclaration:
    """One occasion's decision and the shared data type that grounds it."""

    name: str
    order: int
    decision: str
    key: BlockKey = "turn"

    def __post_init__(self) -> None:
        if not self.decision.strip():
            raise ValueError("a declared block must ground a decision")
        if not BLOCK_TYPES[self.name].store:
            raise ValueError(f"{self.name}: no shared store ownership declared")


def assemble_declared(
    declaration: Sequence[BlockDeclaration],
    blocks: Sequence[ContextBlock],
    *,
    unavailable: Iterable[str] = (),
    sep: str = "\n\n",
) -> str:
    """Render every declared type, including successful empty and failed reads.

    A caller must classify missing reads explicitly. Undeclared data and failed
    reads with content are rejected rather than silently smuggled into a prompt.
    """
    from nora.domain.blocks import ContextBlock, assemble, render_group

    rows = {d.name: d for d in declaration}
    if len(rows) != len(declaration):
        raise ValueError("duplicate context declaration")
    failed = frozenset(unavailable)
    if failed - rows.keys():
        raise ValueError("undeclared failed read")
    if {b.name for b in blocks} - rows.keys():
        raise ValueError("undeclared context data")
    chosen: list[ContextBlock] = []
    for d in declaration:
        instances = [b for b in blocks if b.name == d.name]
        if d.name in failed:
            if any(b.body for b in instances):
                raise ValueError(f"{d.name}: failed read carries content")
            instances = [
                ContextBlock(
                    name=d.name,
                    body=compose_body(""),
                    attrs=(("status", "unavailable"),),
                    when_empty="self_close",
                )
            ]
        elif not instances:
            instances = [ContextBlock(name=d.name, body=compose_body(""), when_empty="self_close")]
        for block in instances:
            if dict(block.attrs).get("status") == "unavailable" and block.body:
                raise ValueError("unavailable context carries content")
            from xml.etree import ElementTree

            tree = ElementTree.fromstring(render_group("body", (), block.body.text))  # noqa: S314 - own escaped output
            explained = {name for name, _ in BLOCK_TYPES[block.name].annotations}
            unexplained = set(dict(block.attrs)) - explained - {"status"}
            for element in tree.iter():
                unexplained.update(set(element.attrib) - explained)
            if unexplained:
                raise ValueError(f"{block.name}: undescribed fields {sorted(unexplained)}")
            chosen.append(replace(block, order=d.order, key=d.key, when_empty="self_close"))
    return assemble(chosen, sep=sep)

