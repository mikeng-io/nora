"""`ComputerToolDispatch` — the queue's way to run a durable segment again.

A `computer_tasks` row stores a TYPED request (`payload.tool` +
`payload.args`) and the room facts it came from (`origin`) — never a
callable. When the runner grants a queued row, this dispatcher rebuilds
the turn's bindings for the ORIGIN room (a fresh `Room`, the same registry
surface construction the tag path uses, `deliverer=None` like the
worker's) and calls the named tool with the stored arguments — the same
`spec.call` the model's own call would have taken, so the deferred run
re-enters the tool's REAL admission path: the audit rows, the session, the
policy checks and the lease all happen then, under `current_segment`, so
its nested computer calls inherit the segment instead of queueing behind
it. Nothing is replayed as a captured closure and nothing runs under a
stale surface: a tool no longer offered answers `unavailable`.

Kinds the runner executes (`native`, `browse`, `capture`) all land here;
`worker` rows never do — they are claimed and executed by their own
submitter.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from nora.agent.room import Room
from nora.domain.computer_events import DREAMER_ACTOR_PREFIX
from nora.domain.computer_tasks import ComputerTask

log = logging.getLogger("nora.computer.dispatch")

__all__ = ["ComputerToolDispatch"]

#: The tools a durable row may name — exactly the tools the queue wraps.
EXECUTABLE = frozenset({
    "computer_run", "computer_write", "computer_edit", "computer_import",
    "computer_browse", "computer_capture",
})


class ComputerToolDispatch:
    """Rebuilds a queued segment's tool call for its origin room.

    Holds the same process-lifetime collaborators `SubagentToolSource`
    holds; `__call__` is per row."""

    def __init__(
        self, *, registry: Any, store: Any, persons: Any, honcho: Any,
        metrics: Any, rosters: Mapping[str, Any] | None = None,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._store = store
        self._persons = persons
        self._honcho = honcho
        self._metrics = metrics
        self._rosters = dict(rosters or {})
        self._self_ids = frozenset(self_ids)

    def _room(self, origin: Mapping[str, Any]) -> Room:
        platform = str(origin.get("platform") or "")
        requester = str(origin.get("requester_person_id") or "")
        tagger = self._persons.by_person.get(requester) if requester else None
        return Room(
            platform=platform,
            conversation_id=str(origin.get("conversation_id") or ""),
            roster_source=self._rosters.get(platform),
            persons=self._persons,
            tagger=tagger,
            self_ids=self._self_ids,
        )

    async def __call__(self, task: ComputerTask) -> Mapping[str, Any]:
        """Run the row's durable request: the named tool, its stored args,
        its origin room. The reply is the segment's result."""
        tool = str(task.payload.get("tool") or "")
        if tool not in EXECUTABLE:
            return {"status": "unavailable",
                    "reason": f"the task names no runnable tool ({tool or 'empty'})",
                    "note": "That queued computer task could not be rebuilt; "
                            "it was never run."}
        args = task.payload.get("args") or {}
        if not isinstance(args, Mapping):
            args = {}
        origin = dict(task.origin or {})
        actor = str(origin.get("actor") or "")
        try:
            specs = self._registry.specs(
                store=self._store,
                conversation_id=str(origin.get("conversation_id") or ""),
                conversation_type=str(origin.get("conversation_type") or "group"),
                persons=self._persons, honcho=self._honcho,
                # No deliverer, ever: a deferred segment speaks through its
                # terminal row and the delivery path, never live.
                deliverer=None, metrics=self._metrics,
                room=self._room(origin),
                # The deferred call keeps the origin's own provenance: a
                # Dreamer-launched segment audits as the Dreamer again.
                dream_adaptor=(actor[len(DREAMER_ACTOR_PREFIX):]
                               if actor.startswith(DREAMER_ACTOR_PREFIX) else ""),
            )
        except Exception as exc:
            log.exception("computer task %s could not rebuild its room", task.id)
            return {"status": "unavailable", "reason": type(exc).__name__,
                    "note": "That queued computer task could not be rebuilt; "
                            "it was never run."}
        spec = next((s for s in specs if s.name == tool), None)
        if spec is None:
            return {"status": "unavailable",
                    "reason": f"{tool} is not offered now",
                    "note": "That queued computer task named a tool that is "
                            "not offered any more; it was never run."}
        try:
            reply = await spec.call(**dict(args))
        except Exception as exc:
            log.exception("computer task %s deferred call failed", task.id)
            return {"status": "unavailable", "reason": type(exc).__name__,
                    "note": "The queued computer task failed while it ran."}
        return dict(reply) if isinstance(reply, Mapping) else {
            "status": "unavailable", "reason": "the tool answered no result"}
