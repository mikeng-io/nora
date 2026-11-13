from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nora.engagement.subagents_context import SubagentsContextReader
from nora.subagents.adapters.tools import build as _build

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


@dataclass(frozen=True)
class SubjectsWiring:
    subagents_context: Any


def subjects(cfg, compute_jobs, document_service, metrics) -> SubjectsWiring:
    # `<subagents>`: this room's sub-agent jobs, tag turn only. `None`
    # while `[subagent]` is off. The worker is attached where the desk's is, once built.
    subagents_context: SubagentsContextReader | None = None
    if cfg.subagent.enabled:
        subagents_context = SubagentsContextReader(
            jobs=compute_jobs,
            clock=lambda: datetime.now(UTC),
            metrics=metrics,
            documents=document_service if cfg.documents.enabled else None,
        )
    return SubjectsWiring(subagents_context=subagents_context)
