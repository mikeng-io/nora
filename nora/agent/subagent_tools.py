"""The sub-agent worker's tool source: a CLOSED list.

Neither precedent builds it. The Dreamer's `RegistryToolSource` keeps only
`read_only_tools()`, which drops `computer_run` and `computer_write`, and a tag
turn's surface holds every send path. The worker needs the computer and
documents (acts) and the room's lookups (reads), and nothing that speaks, remembers
or schedules. So the list is one allowlist, derived where the registry can answer:

    allowed = (read_only_tools() - NOT_LOOKUPS) | ACTS

- `ACTS` is the short, named set of tools the worker may CHANGE something with:
  the computer's four writing tools and `computer_export` (refused for any
  destination but `keep` by the tool itself, for this actor), and
  `document_publish`. `skills` is the model's on-demand know-how and is wired by
  the agent factory, not built here, so it is in `granted_names` for the guard only.
- `NOT_LOOKUPS` is what is read-only and still not the worker's: every read that
  leaves the room (`room_list`, `room_read`, `search_memory_workspace`), the
  social reads, and the reads that only make sense to a live turn.
- `NEVER` is what must never be held, stated so a test can walk it and so `build`
  can refuse a surface that contains one. It is a CHECK on the allowlist, not the
  list itself: a tool added to the registry later is absent here until someone
  puts it in `ACTS` or it is a new read-only lookup.

The room is a `Room` built from the job's RECORDED facts with the recorded
requester as tagger and no deliverer (nothing it holds can speak), through the
same `ToolRegistry.surface` every loop takes its tools from, so the room's
platform rules and the disclosure guards run as on a tag. `subagent_job` makes
the computer actor `subagent` and its lease its own.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics

from .registry import ToolRegistry
from .room import Room

log = logging.getLogger("nora.agent.subagent_tools")

#: The tools the worker may change something with. Everything else it holds is a read.
ACTS: frozenset[str] = frozenset({
    "computer_run", "computer_write", "computer_edit", "computer_import",
    "computer_export", "document_publish",
    # the worker's own place-keeper: not a lookup, not an effect outside
    "checkpoint_update",
})

#: Read-only tools that are still not the worker's.
NOT_LOOKUPS: frozenset[str] = frozenset({
    # reads that leave the launching room: the worker's reach is one room
    "room_list", "room_read", "search_memory_workspace",
    # `memory_search` (DEC-316) reads the memory index across the unit's
    # rooms, so it is not the worker's either
    "memory_search",
    # the social reads stay on the tag path (a person asked for that account, now)
    "instagram_read", "instagram_search", "x_read", "x_search",
    "linkedin_read", "linkedin_search",
    # reads that belong to a live turn or to a Dreamer pass
    "load_tools", "recall_lookups", "list_reminders", "music_status", "search_gifs",
    "computer_session_list", "rollup_read", "standing_read",
    # Nora's own handle on background jobs: a worker that held it would recurse
    "subagent_list", "subagent_read",
})

#: Held by nobody on a worker, whatever the registry grows: every send path, every
#: schedule, every note, event and memory write, and the document state change.
NEVER: frozenset[str] = frozenset({
    "send_reply", "send_to", "send_gif", "media_send", "generate_image", "video_create",
    "remind", "cancel_reminder",
    "note_create", "note_update",
    "event_propose", "event_revise", "event_rsvp", "event_set_status",
    "remember", "document_set_status",
    "play_music", "skip_music", "stop_music", "top_music", "set_music_volume",
    # no recursion: a worker never starts, reads or stops a worker
    "subagent_create", "subagent_list", "subagent_read", "subagent_cancel",
})

#: The activation tool the agent factory wires (`AgentSkills`); the guard must admit it.
SKILLS_TOOL = "skills"


class SubagentToolsError(RuntimeError):
    """The worker's tools could not be built as the closed list says. The job fails;
    it never runs with a tool it should not hold, or without the requester."""


def granted_names(registry: ToolRegistry) -> frozenset[str]:
    """The allowlist, derived from the registry's own read-only query."""
    return (registry.read_only_tools() - NOT_LOOKUPS) | ACTS


class SubagentToolSource:
    """Builds one job's tools. Holds the process-lifetime collaborators; `build` is per job."""

    def __init__(
        self, *, registry: ToolRegistry, store: Any, persons: Any, honcho: Any,
        metrics: Metrics, rosters: Mapping[str, Any] | None = None,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._store = store
        self._persons = persons
        self._honcho = honcho
        self._metrics = metrics
        self._rosters = dict(rosters or {})
        self._self_ids = frozenset(self_ids)

    def tool_param_names(self) -> Mapping[str, frozenset[str]]:
        """R-99's table, for the guard."""
        return self._registry.tool_param_names()

    def _room(self, *, platform: str, conversation_id: str, requester_person_id: str) -> Room:
        tagger: PersonRef | None = self._persons.by_person.get(requester_person_id)
        if tagger is None:
            raise SubagentToolsError(
                f"requester {requester_person_id!r} is not in identity.toml: the worker "
                "would hold no requester and document_publish would refuse every call"
            )
        return Room(
            platform=platform, conversation_id=conversation_id,
            roster_source=self._rosters.get(platform), persons=self._persons,
            tagger=tagger, self_ids=self._self_ids,
        )

    async def run_computer(
        self, *, job_id: str, platform: str, conversation_id: str, conversation_type: str,
        requester_person_id: str, command: str,
    ) -> dict[str, Any]:
        """One `computer_run` call as the worker's own actor: the runner's way to create the
        job's folder and copy its inputs in, audited like any call the worker makes (actor
        `subagent`, path `subagent`, the job's lease key). Not a model-facing tool."""
        room = self._room(
            platform=platform, conversation_id=conversation_id,
            requester_person_id=requester_person_id,
        )
        specs = self._registry.specs(
            store=self._store, conversation_id=conversation_id,
            conversation_type=conversation_type, persons=self._persons, honcho=self._honcho,
            deliverer=None, metrics=self._metrics, room=room, subagent_job=job_id,
        )
        spec = next(s for s in specs if s.name == "computer_run")
        return await spec.call(command=command)

    def build(
        self, *, job_id: str, platform: str, conversation_id: str, conversation_type: str,
        requester_person_id: str, checkpoint_sink: Any = None,
    ) -> list[Any]:
        """The worker's tools for one job, from the job's recorded room facts.
        `checkpoint_sink` is the job's `SubagentProgress`, where `checkpoint_update` writes."""
        room = self._room(
            platform=platform, conversation_id=conversation_id,
            requester_person_id=requester_person_id,
        )
        grant = granted_names(self._registry)
        surface = self._registry.surface(
            grant=grant, store=self._store, conversation_id=conversation_id,
            conversation_type=conversation_type, persons=self._persons,
            honcho=self._honcho,
            # No deliverer, ever: nothing the worker holds speaks to anybody.
            deliverer=None, metrics=self._metrics, room=room,
            subagent_job=job_id, checkpoint_sink=checkpoint_sink,
        )
        names = {getattr(t, "tool_name", "") for t in surface.tools}
        held = names & NEVER
        outside = names - grant
        if held or outside:
            raise SubagentToolsError(
                f"the worker's surface holds {sorted(held | outside)}, which the closed "
                "list does not allow"
            )
        return list(surface.tools)
