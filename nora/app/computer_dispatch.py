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
from nora.agent.surface import ToolScope
from nora.domain.computer_events import DREAMER_ACTOR_PREFIX
from nora.domain.computer_tasks import ComputerTask

log = logging.getLogger("nora.computer.dispatch")

__all__ = ["ComputerToolDispatch"]

#: The tools a durable row may name — exactly the tools the queue wraps.
EXECUTABLE = frozenset({
    "computer_run", "computer_write", "computer_edit", "computer_import",
    "computer_browse", "computer_capture",
})


def _refused(reason: str, note: str) -> dict[str, Any]:
    """A pre-admission refusal: the row never reached the guest, which the
    queue records (`no_guest_effect`) so it terminalizes without a
    guest-quiet wait — nothing ran, so nothing must be confirmed stopped."""
    return {"status": "unavailable", "reason": reason, "note": note,
            "no_guest_effect": True}


class ComputerToolDispatch:
    """Rebuilds a queued segment's tool call for its origin room.

    Holds the same process-lifetime collaborators `SubagentToolSource`
    holds; `__call__` is per row."""

    def __init__(
        self, *, registry: Any, store: Any, persons: Any, honcho: Any,
        metrics: Any, rosters: Mapping[str, Any] | None = None,
        self_ids: frozenset[str] = frozenset(),
        policy: Any, scope_for: Any,
    ) -> None:
        self._registry = registry
        self._store = store
        self._persons = persons
        self._honcho = honcho
        self._metrics = metrics
        self._rosters = dict(rosters or {})
        self._self_ids = frozenset(self_ids)
        # The CURRENT policy and scope resolver: admission is re-decided at
        # execution time, not inherited from when the row was written.
        self._policy = policy
        self._scope_for = scope_for

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
        its origin room — under the CURRENT admission, never the one the
        row was written under. The reply is the segment's result."""
        tool = str(task.payload.get("tool") or "")
        if tool not in EXECUTABLE:
            return _refused(
                f"the task names no runnable tool ({tool or 'empty'})",
                "That queued computer task could not be rebuilt; "
                "it was never run.")
        args = task.payload.get("args") or {}
        if not isinstance(args, Mapping):
            args = {}
        origin = dict(task.origin or {})
        actor = str(origin.get("actor") or "")
        platform = str(origin.get("platform") or "")
        conversation_id = str(origin.get("conversation_id") or "")
        conversation_type = str(origin.get("conversation_type") or "group")
        requester = str(origin.get("requester_person_id") or "")
        dream_adaptor = (actor[len(DREAMER_ACTOR_PREFIX):]
                         if actor.startswith(DREAMER_ACTOR_PREFIX) else "")
        worker = str(origin.get("worker") or "")
        grant = frozenset(str(g) for g in origin.get("grant") or ())
        # Re-admission against CURRENT state, before any tool is rebuilt:
        # a revoked requester, a removed room, a moved unit/DM anchor and a
        # grant or occasion that no longer offers the tool all refuse the
        # segment — never a guest effect on stale authority.
        convo = self._policy.conversation(platform, conversation_id) \
            if platform and conversation_id else None
        if convo is None:
            return _refused(
                "the task's room is no longer admitted",
                "That queued computer task could not run: the room it came "
                "from is not admitted any more; it was never run.")
        if requester and not self._policy.is_allowed(
                platform=platform, conversation_id=conversation_id,
                person_id=requester):
            return _refused(
                "the task's requester is no longer allowed",
                "That queued computer task could not run: the person who "
                "asked for it is not allowed in that room any more; it was "
                "never run.")
        current_scope = self._scope_for(
            platform, conversation_id, conversation_type)
        if current_scope != task.scope:
            return _refused(
                "the task's scope moved while it was queued",
                "That computer task could not run: the unit or DM it "
                "belonged to changed while it waited, and it is never run "
                "against a different computer.")
        scope = ToolScope(
            platform=platform or None,
            dreamer=bool(dream_adaptor), grant=grant or None,
            tag_turn=(actor == "tag"), worker=bool(worker),
            relay=bool(origin.get("relay")))
        if not scope.admits(tool):
            return _refused(
                f"{tool} is not admitted for this task now",
                "That queued computer task named a tool its occasion can "
                "no longer use; it was never run.")
        try:
            specs = self._registry.specs(
                store=self._store,
                conversation_id=conversation_id,
                conversation_type=conversation_type,
                persons=self._persons, honcho=self._honcho,
                # No deliverer, ever: a deferred segment speaks through its
                # terminal row and the delivery path, never live.
                deliverer=None, metrics=self._metrics,
                room=self._room(origin),
                # The deferred call keeps the origin's own provenance: a
                # Dreamer-launched segment audits as the Dreamer again,
                # and the origin's grant ceiling rides into the rebuilt
                # surface — never a wider one.
                dream_adaptor=dream_adaptor,
                subagent_job=worker,
                grant=grant or None,
            )
        except Exception as exc:
            log.exception("computer task %s could not rebuild its room", task.id)
            return {"status": "unavailable", "reason": type(exc).__name__,
                    "note": "That queued computer task could not be rebuilt; "
                            "it was never run.",
                    "no_guest_effect": True}
        spec = next((s for s in specs if s.name == tool), None)
        if spec is None:
            return _refused(
                f"{tool} is not offered now",
                "That queued computer task named a tool that is not "
                "offered any more; it was never run.")
        try:
            reply = await spec.call(**dict(args))
        except Exception as exc:
            log.exception("computer task %s deferred call failed", task.id)
            return {"status": "unavailable", "reason": type(exc).__name__,
                    "note": "The queued computer task failed while it ran."}
        return dict(reply) if isinstance(reply, Mapping) else {
            "status": "unavailable", "reason": "the tool answered no result"}
