"""Pass-owned Cognitive Graph tools with a fixed reading room."""

from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any

from pydantic import PlainSerializer, WrapValidator

from nora.agent.catalog.entries.agenda import TOOLS as AGENDA_TOOLS
from nora.agent.catalog.model import NativeTool
from nora.agent.ports import adapt
from nora.agent.room import Room
from nora.agent.toolkit.spec import _spec
from nora.memory.adapters.tools.intentions import submit_operations
from nora.memory.adapters.tools.search import GraphReadTools, _make_memory_read, _make_memory_search
from nora.memory.app.admission import AdmissionService
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import PassContext, Proposal
from nora.memory.domain.graph_operations import (
    EntityInput,
    GraphOperation,
    GraphOperationName,
    parse_graph_operation,
    topic_refs,
)
from nora.memory.domain.intention_operations import (
    WakeIntentionOperation,
    parse_intention_operation,
)
from nora.memory.domain.pass_inputs import PassScope
from nora.memory.domain.retrieval import RetrievalAnchors
from nora.obs.metrics import Metrics

WAKE_VERB_NAMES = {
    "graph_propose": NativeTool(
        4, side_effect_state="confirmed", idempotency="non_idempotent",
        retry="never", output_contract="three_state", level=3,
    ),
    "intention_commit": AGENDA_TOOLS["intention_commit"],
}
WAKE_READ_TOOLS = frozenset({"memory_search", "memory_read"})
WAKE_TOOLS = WAKE_READ_TOOLS | frozenset(WAKE_VERB_NAMES)

_ProposalInput = Annotated[
    GraphOperation, WrapValidator(lambda raw, _validate: raw),
    PlainSerializer(lambda operation: operation, return_type=Any),
]
_TOPIC_OPERATION: tuple[GraphOperationName, ...] = ("create_entity",)
_CommitInput = Annotated[
    WakeIntentionOperation | EntityInput, WrapValidator(lambda raw, _validate: raw),
    PlainSerializer(lambda operation: operation, return_type=Any),
]


class WakeToolSurface:
    def __init__(
        self, admission: AdmissionService, graph: GraphReadTools,
        room_for: Callable[[str, str], Room], metrics: Metrics, *,
        content_checks: bool, offered: Collection[str],
        allowed_operations: Collection[GraphOperationName],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not set(offered).issubset(WAKE_TOOLS):
            raise ValueError("Unknown tool on reflection surface")
        self._admission = admission
        self._graph = graph
        self._room_for = room_for
        self._metrics = metrics
        self._content_checks = content_checks
        self._offered = frozenset(offered)
        self._clock = clock
        self._allowed_operations = frozenset(allowed_operations)

    def bind(
        self, scope: PassScope, now: datetime, room_busy: Callable[[], None],
    ) -> list[Any]:
        reading = scope.room
        room = self._room_for(reading.platform, reading.conversation_id)
        def read_now() -> datetime:
            return max(now, self._clock())

        deps = SimpleNamespace(
            graph_retrieval=self._graph, metrics=self._metrics, room=room, pass_scope=scope,
            bound_conversation_id=reading.conversation_id, now_fn=read_now,
            content_checks=self._content_checks,
        )

        async def rediscover(id_: str) -> dict:
            return await self._graph.read(
                room, reading.conversation_id, scope, read_now(),
                anchors=RetrievalAnchors(graph_ids=(id_,)), depth=1,
                content_checks=self._content_checks,
            )

        async def graph_propose(operations: list[_ProposalInput]) -> dict:
            """Propose graph changes and read their neighbours. Call this when
            evidence supports a new entity, claim or connection, or changes a claim.

            Use a batch-local entity ref as an about target on an evidenced sibling.
            Guess claims carry a prediction and window end.
            Returns `status`: ok, refused or unavailable, with a `note`.

            Args:
                operations: Evidence-backed graph operations. Use shown graph IDs for
                    references and cite observed or computed evidence.
            """
            return await submit_operations(
                operations, self._admission, self._metrics, "graph_propose", self._parse_graph,
                rediscover=rediscover, room_busy=room_busy,
            )

        async def intention_commit(operations: list[_CommitInput]) -> dict:
            """Commit intentions and read their neighbours. Call this when an
            intention needs to be opened, revised, dropped or closed.

            Use about to link an inferred subject, with reasoning and confidence.
            A new topic can be an evidenced sibling using a batch-local entity ref.
            Returns `status`: ok, refused or unavailable, with a `note`.

            Args:
                operations: Intention operations in order. Give person IDs or nora for
                    owner and owed_to. Use shown graph IDs for references.
            """
            return await submit_operations(
                operations, self._admission, self._metrics, "intention_commit", _parse_wake_commit,
                rediscover=rediscover, room_busy=room_busy,
            )

        calls = {
            "memory_read": _make_memory_read(deps),
            "memory_search": _make_memory_search(deps),
            "graph_propose": graph_propose,
            "intention_commit": intention_commit,
        }
        tools = {
            name: adapt(_spec(name, calls[name], declared=WAKE_VERB_NAMES.get(name)))
            for name in sorted(self._offered)
        }
        if (commit := tools.get("intention_commit")) is not None:
            definitions = commit.tool_spec["inputSchema"]["json"].get("$defs", {})
            if "Recurrence" in definitions:
                definitions["Recurrence"]["description"] = "How often the delivery repeats."
        return list(tools.values())

    def _parse_graph(self, operations: list, context: PassContext) -> list[Proposal | Refusal]:
        topics = topic_refs(operations)
        return [
            parse_graph_operation(raw, context, self._allowed_operations, topics)
            for raw in operations
        ]


def _parse_wake_commit(operations: list, context: PassContext) -> list[Proposal | Refusal]:
    topics = topic_refs(operations)
    return [
        parse_graph_operation(raw, context, _TOPIC_OPERATION, topics)
        if (context.occasion == "wake" and isinstance(raw, Mapping)
            and raw.get("op") == "create_entity")
        else parse_intention_operation(raw, context, topics=topics)
        for raw in operations
    ]
