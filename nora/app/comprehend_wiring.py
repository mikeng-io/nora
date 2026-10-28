"""Compose graph Cognition against the port; phase one supplies its store."""

from __future__ import annotations

from typing import Any

from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.app.comprehend import ComprehendRunner
from nora.domain.memory_graph import MemoryGraph


def build_comprehend_runner(
    *,
    graph: MemoryGraph,
    records: Any,
    sessions: Any,
    tools: Any,
    models: Any,
    retry: Any,
    metrics: Any,
    cfg: Any,
    private_names: tuple[str, ...],
    model_id: str,
    model_route: str,
    ai_peer: str,
    clock: Any,
    rooms_by_person: Any = None,
) -> ComprehendRunner:
    return ComprehendRunner(
        graph=graph,
        records=records,
        sessions=sessions,
        tools=tools,
        agent=LiveCognitionAgentRunner(models=models, retry=retry, metrics=metrics),
        config=cfg.memory.graph.comprehend,
        graph_enabled=cfg.memory.graph.enabled,
        metrics=metrics,
        private_names=private_names,
        model_id=model_id,
        model_route=model_route,
        ai_peer=ai_peer,
        clock=clock,
        rooms_by_person=rooms_by_person,
    )
