"""Graph Cognition test collaborators; SQL receipts share the contract suite."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from typing import Any

from nora.domain.memory_graph import Admission, Key, WorkItem


class InMemoryComprehendRecords:
    def __init__(self, items: dict[Key, Any] | None = None) -> None:
        self.items = dict(items or {})
        self.refusals: list[tuple[int, str, str]] = []
        self.pages: list[dict[str, Any]] = []
        self.graphs: list[tuple[WorkItem, int, Admission]] = []
        self.claims: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.shadows: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.fail_on: set[str] = set()

    async def load(self, work: WorkItem) -> Any:
        if "load" in self.fail_on:
            raise RuntimeError("source unavailable")
        return replace(self.items[work.source], work=work)

    async def record_refusal(self, session: int, part: str, reason: str) -> None:
        self.refusals.append((session, part, reason))

    async def web_page(self, **page: Any) -> Key:
        self.pages.append(
            {**page, "content_hash": hashlib.sha256(page["text"].encode()).hexdigest()}
        )
        return "web_page", str(len(self.pages))

    async def readings(
        self, work: WorkItem, session: int, item: Any, claims: list[dict[str, Any]]
    ) -> None:
        if "readings" in self.fail_on:
            raise RuntimeError("writer unavailable")
        self.claims.setdefault((work.id, work.input_hash), claims)

    async def graph_write(self, work: WorkItem, session: int, admission: Admission) -> None:
        if "graph_write" in self.fail_on:
            raise RuntimeError("receipt unavailable")
        self.graphs.append((work, session, admission))

    async def changed_graph_keys(self, work: WorkItem) -> list[Key]:
        return [
            tuple(k)
            for r in self.receipts
            if r["work"] == work.id and r["part"] == "corrected_entities"
            for k in r["payload"]["keys"]
        ] + list(
            {
                k
                for w, _, a in self.graphs
                if (w.id, w.input_hash) == (work.id, work.input_hash)
                for r in a.relations
                for k in (r.src, r.dst)
            }
        )

    async def shadow(
        self,
        work: WorkItem,
        session: int,
        entity: Key,
        proposals: list[dict[str, Any]],
        parent: int,
    ) -> None:
        if "shadow" in self.fail_on:
            raise RuntimeError("shadow unavailable")
        self.shadows.append(
            {
                "work": work,
                "session": session,
                "entity": entity,
                "would_writes": proposals,
                "parent": parent,
            }
        )

    async def receipt(
        self, work: WorkItem, session: int, part: str, payload: dict[str, Any]
    ) -> None:
        self.receipts.append(
            {"work": work.id, "session": session, "part": part, "payload": payload}
        )

    async def prior_evidence(self, work: WorkItem) -> tuple[Any, ...]:
        return tuple(
            e
            for w, _, a in self.graphs
            if (w.id, w.input_hash) == (work.id, work.input_hash)
            for e in a.evidence
        )


class ScriptedCognitionRunner:
    """Named calls and malformed-call errors, not one answer to every request.

    A script entry is one session's tool-use trajectory. A closure's wrong
    argument shape yields a tool error and the script may correct it, as the
    real Strands loop does. Nested graph envelopes go through production's
    Pydantic admission, not a permissive fake parser.
    """

    def __init__(self, scripts: list[list[tuple[str, dict[str, Any]]]]) -> None:
        self.scripts = list(scripts)
        self.prompts: list[str] = []
        self.answers: list[Any] = []
        self.budgets: list[Any] = []

    async def run(
        self,
        *,
        system_prompt: str,
        prompt: str,
        tools: list[Any],
        budget: Any,
        trace_attributes: Any,
    ) -> Any:
        from types import SimpleNamespace

        self.prompts.append(prompt)
        self.budgets.append(budget)
        by_name = {getattr(t, "tool_name", None) or t.__name__: t for t in tools}
        if not self.scripts:
            raise RuntimeError("script exhausted")
        for name, args in self.scripts.pop(0):
            target = by_name.get(name)
            if target is None:
                self.answers.append({"status": "error", "reason": "unknown_tool"})
                continue
            try:
                inspect.signature(target).bind(**args)
            except TypeError:
                self.answers.append({"status": "error", "reason": "bad_arguments"})
                continue
            self.answers.append(await target(**args))
        return SimpleNamespace(
            stop_reason="end_turn", total_tokens=100, messages=(), requirements={}
        )
