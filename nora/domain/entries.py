"""Notebook entries. Topic is the only admitted kind; existing stores stay put."""

from dataclasses import dataclass
from typing import Literal, Protocol

from nora.domain.memory_graph import RECORD, ROLLUP, Key


@dataclass(frozen=True)
class EntryContent:
    about: tuple[Key, ...]
    body: str
    citations: tuple[Key, ...]
    confidence: Literal["low", "medium", "high"]
    state: Literal["active", "dormant", "retired"]
    writer: str
    session_id: str
    model: str
    instruction_hash: str
    links: tuple[Key, ...] = ()


@dataclass(frozen=True)
class EntryRevision:
    id: str
    entry_id: str
    base_revision_id: str | None
    seq: int
    content: EntryContent


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    unit: str
    head: EntryRevision

    @property
    def audience(self) -> Literal["internal"]:
        return "internal"


def check_entry(kind: str, content: EntryContent, previous: EntryContent | None = None) -> None:
    """Refuse invalid writes before either implementation changes state."""
    if kind != "topic":
        raise ValueError("unknown_kind")
    if len(content.about) != 1 or content.about[0][0] != "topic" or not content.about[0][1]:
        raise ValueError("topic_about")
    if previous and content.about != previous.about:
        raise ValueError("topic_about")
    if content.links:
        raise ValueError("topic_links")
    if not content.citations or any(
        k not in (*RECORD, *ROLLUP) or not i for k, i in content.citations
    ):
        raise ValueError("citation_layer")
    if content.confidence not in ("low", "medium", "high"):
        raise ValueError("confidence")
    if (
        content.state not in ("active", "dormant", "retired")
        or (previous is None and content.state != "active")
        or (previous is not None and previous.state == "retired")
    ):
        raise ValueError("lifecycle")
    if not all(
        s.strip() for s in (content.body, content.writer, content.session_id, content.model)
    ):
        raise ValueError("empty")
    if len(content.instruction_hash) != 64 or any(
        c not in "0123456789abcdef" for c in content.instruction_hash
    ):
        raise ValueError("instruction_hash")


# ponytail: topic-only storage; add disclosure/search when a live entry writer joins.
class EntryStore(Protocol):
    async def create(self, *, kind: str, unit: str, content: EntryContent) -> Entry: ...
    async def get(self, *, unit: str, entry_id: str) -> Entry | None: ...
    async def revisions(self, *, unit: str, entry_id: str) -> tuple[EntryRevision, ...]: ...
    async def append(
        self, *, unit: str, entry_id: str, base_revision_id: str, content: EntryContent
    ) -> EntryRevision: ...
