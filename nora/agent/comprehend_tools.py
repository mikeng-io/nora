"""Step one's tool-local egress screen, paid lookup caps and fetch provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote

from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolSpec, ToolUse

from nora.agent.dream_screen import text_offence
from nora.agent.egress import _canonical
from nora.agent.fetch_watch import GraphFetched, _result_of
from nora.domain.comprehend import EGRESS_REASONS
from nora.domain.memory_graph import Source

READ_NAMES = frozenset(
    {
        "memory_search",
        "memory_read",
        "media_fetch",
        "instagram_read",
        "instagram_search",
        "x_read",
        "x_search",
        "web_search",
        "web_fetch",
    }
)


def egress_reason(text: str, private_names: Sequence[str]) -> str | None:
    # Decode before checking; percent-encoding is not an egress permission.
    while True:
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    reason = text_offence(text, content_checks=True)
    if reason:
        return reason
    folded = _canonical(text).casefold()
    for name in private_names:
        name = _canonical(name).strip().casefold()
        if not name:
            continue
        # Unicode names match as substrings. Latin names use word boundaries
        # so a private name such as Al does not block a venue called Palace.
        pattern = re.escape(name).replace(r"\ ", r"[\s+]+")
        if name.isascii():
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        if re.search(pattern, folded):
            return "private_person"
    return None


@dataclass
class LookupState:
    fetched: GraphFetched
    unit: str
    session_id: int
    records: Any
    metrics: Any
    private_names: tuple[str, ...]
    persons: tuple[str, ...] = ()
    shown_keys: set = field(default_factory=set)
    shown_relations: set = field(default_factory=set)
    limits: dict[str, int] = field(default_factory=lambda: {"web_search": 3, "web_fetch": 3})
    used: dict[str, int] = field(default_factory=dict)


#: The outbound arguments `egress_reason` screens before a provider is called,
#: per tool name. The session's mandatory private/identifier screen applied to
#: the web pair's `query`/`url` from the start; the newly reachable provider
#: reads carry the same outbound text on different argument names, and a
#: handle, query or link is the same disclosure on any provider. A screen
#: refusal happens before `inner` runs — the provider is never called.
#: `state.limits` (the per-call COUNT cap) still binds only the web pair.
SCREENED_ARGS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "web_search": ("query",),
    "web_fetch": ("url",),
    "place_info": ("name", "address"),
    "media_fetch": ("url", "question"),
    "file_fetch": ("link", "url"),
    "instagram_read": ("person", "handle", "link", "url"),
    "instagram_search": ("query",),
    "x_read": ("handle", "link", "url"),
    "x_search": ("query",),
    "linkedin_read": ("handle", "link", "url"),
    "linkedin_search": ("query",),
})


class ComprehendRead(AgentTool):
    def __init__(self, inner: Any, state: LookupState) -> None:
        super().__init__()
        self._inner = inner
        self._state = state

    @property
    def tool_name(self) -> str:
        return str(self._inner.tool_name)

    @property
    def tool_spec(self) -> ToolSpec:
        return self._inner.tool_spec

    @property
    def tool_type(self) -> str:
        return "python"

    @staticmethod
    def _event(use: ToolUse, answer: dict[str, Any]) -> ToolResultEvent:
        envelope: dict[str, Any] = {"toolUseId": use["toolUseId"], "content": [{"json": answer}]}
        envelope["status"] = "success"
        return ToolResultEvent(envelope)  # type: ignore[arg-type]

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        state, name = self._state, self.tool_name
        args = tool_use.get("input") or {}
        if name in SCREENED_ARGS:
            reason = None
            for arg in SCREENED_ARGS[name]:
                value = args.get(arg)
                if not isinstance(value, str) or not value.strip():
                    continue
                reason = egress_reason(value, state.private_names)
                if reason is not None:
                    break
            if reason is None and name in state.limits:
                value = args.get("query" if name == "web_search" else "url")
                if not isinstance(value, str):
                    reason = "bad_arguments"
                elif state.used.get(name, 0) >= state.limits[name]:
                    reason = "lookup_cap"
            if reason:
                if reason not in EGRESS_REASONS:
                    raise ValueError("unknown egress refusal")
                await state.records.record_refusal(state.session_id, "egress", reason)
                state.metrics.inc("nora_comprehend_egress_refusals_total", reason=reason)
                yield self._event(
                    tool_use,
                    {
                        "status": "refused",
                        "reason": reason,
                        "note": "This lookup was refused before egress.",
                    },
                )
                return
            # No await between checking and increment: parallel tool calls share the cap.
            if name in state.limits:
                state.used[name] = state.used.get(name, 0) + 1
            state.metrics.inc("nora_comprehend_web_lookups_total", tool=name)
        async for event in self._inner.stream(tool_use, invocation_state, **kwargs):
            answer = _result_of(event)
            if name in ("memory_search", "memory_read"):
                # A graph preview includes notebook judgements. It is not a new
                # Record source, and cannot launder those judgements into evidence.
                # `keys` is the renderer's own emitted-node list — the only
                # authority on which graph keys the session was shown.
                # `relations` is the same authority one axis over: the edge
                # ids the read actually emitted, which a correction may name.
                if isinstance(answer, dict) and answer.get("status") in ("ok", "partial"):
                    state.shown_keys.update(
                        tuple(k) for k in answer.get("keys") or ()
                        if isinstance(k, (list, tuple)) and len(k) == 2)
                    state.shown_relations.update(
                        r for r in answer.get("relations") or ()
                        if isinstance(r, str))
                yield event
                continue
            if isinstance(answer, dict) and answer.get("status") == "ok":
                if name == "web_fetch":
                    # Preserve exactly the bounded text the tool actually returns.
                    text = answer.get("text") or answer.get("content")
                    if not isinstance(text, str):
                        # A changed provider shape must never create false provenance.
                        raise ValueError("web page result has no text")
                    truncated = len(text) > 32000
                    text = text[:32000]
                    fetched_at = datetime.now(UTC)
                    key = await state.records.web_page(
                        unit=state.unit,
                        session_id=state.session_id,
                        url=args["url"],
                        fetched_at=fetched_at,
                        text=text,
                        tool_call=dict(tool_use),
                    )
                    answer = {
                        **answer,
                        "text": text,
                        "source": list(key),
                        "fetched_at": fetched_at.isoformat(),
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "truncated": truncated or bool(answer.get("truncated")),
                    }
                    answer.pop("content", None)
                else:
                    key = ("tool_result", f"{state.session_id}:{tool_use['toolUseId']}")
                    text = json.dumps(answer, ensure_ascii=False, sort_keys=True)
                    answer = {**answer, "source": list(key)}
                state.fetched.show(Source(key, "record", state.unit, state.persons, text))
                yield self._event(tool_use, answer)
            else:
                yield event


#: The reads a session replaying an earlier instant cannot answer honestly:
#: each answers from a fold of the CURRENT state — a notebook or roster head,
#: a live index, a built view — and no evidence-time seam exists for it to
#: read that state back as it then stood. The tool stays on the surface, so
#: its schema and its catalogue entry still hold, and answers `unavailable`
#: with the limitation, never `nothing_found`: a fold that cannot be read
#: as-of is not a fold that was empty. What the session was already shown —
#: its grounding — carries the as-of view these reads cannot give.
HISTORICAL_READ_UNAVAILABLE: Mapping[str, str] = MappingProxyType({
    "about": "This reads a person's profile as it stands now, not as of this "
             "time; what was already gathered shows what was known then.",
    "ask_memory": "This reasons over memory as it stands now, not as of this "
                  "time; what was already gathered shows what was known then.",
    "search_memory": "This searches memory as it stands now, not as of this "
                     "time; what was already gathered shows what was known then.",
    "search_memory_workspace": "This searches memory as it stands now, not as "
                               "of this time; what was already gathered shows "
                               "what was known then.",
    "memory_search": "This searches the memory graph's current heads; it "
                     "cannot be read back as of this time. What was already "
                     "gathered shows the graph as of it.",
    "memory_read": "This reads the memory graph's current heads; it cannot be "
                   "read back as of this time. What was already gathered "
                   "shows the graph as of it.",
    "who_said": "This checks the transcript in front of a live turn; a reading "
                "of an earlier time has no such window. `record_query` and "
                "`load_transcript` read the record instead.",
    "lookup_person": "This reads who is known as it stands now; who was known "
                     "then cannot be read back.",
    "group_members": "This reads the member list as it stands now, not as of "
                     "this time.",
    "room_state_read": "This reads the room's folded objects as they stand "
                       "now, not as of this time.",
    "poll_list": "This reads polls' folded state as it stands now, not as of "
                 "this time.",
    "poll_read": "This reads a poll's folded state as it stands now, not as "
                 "of this time.",
    "event_search": "This reads gatherings' open folds as they stand now, "
                    "not what was open then.",
    "rollup_read": "This reads a rollup built now; what it would have read "
                   "then cannot be rebuilt here.",
    "standing_read": "This reads standing as it stands now, not as of this "
                     "time.",
    "character_read": "This reads culture as it stands now, not as of this "
                      "time.",
    "routine_read": "This reads a routine computed on the current state, not "
                    "as of this time.",
    "where_been": "This reads presence derived from the current state, not "
                  "as of this time.",
    "note_read": "This reads the notebook as it stands now, not as of this "
                 "time.",
    "note_search": "This searches the notebook as it stands now, not as of "
                   "this time.",
    "memo_read": "This reads a memo as it stands now, not as of this time.",
    "social_read": "This reads the social stream's items as they stand now; "
                   "an interval reaching past this time would show items not "
                   "yet posted.",
    "media_search": "This searches the image library as it stands now, "
                    "including work made after this time.",
    "file_fetch": "This reads a file's content as it stands now — a shared "
                  "document's body is whatever it says at fetch time, and "
                  "what it said at this time cannot be read back.",
})

#: `record_query` branches a replay cannot answer honestly: the typed
#: source-object read (`kind`/`object`/`history`/`at_id`) folds and lists as
#: they stand now, and `scope="all_rooms"` checks today's member lists —
#: neither can be read back as of the evidence instant. The messages branch
#: stays: its interval is already bounded at the instant the session's clock
#: is bound to.
_RECORD_OBJECT_UNAVAILABLE = (
    "A source object's fold and event history are read as they stand now; "
    "this time has no anchor for them."
)
_RECORD_ALL_ROOMS_UNAVAILABLE = (
    "Reading every room checks the member list as it stands now; who was in "
    "each room then cannot be checked, so this read stays to this room."
)


class HistoricalRead(AgentTool):
    """`inner` under the historical lane's temporal rule: same schema, same
    answers, with every read bound to the session's evidence instant.

    The BOUND itself is not here: it is the session's clock, which every
    built read tool derives its as-of ceiling from (`ToolRegistry.with_clock`
    binds `now_fn`, and `load_transcript`/`room_read`/`record_query` fold
    rows, edits, reactions, deletes and source events at it — so a `before`
    at or after the instant, or absent, can never mean «the live newest»).
    What this wrapper adds is the honest boundary the record cannot give:
    the tools in `HISTORICAL_READ_UNAVAILABLE` answer `unavailable` with
    their limitation before `inner` runs — nothing was read, so nothing is
    recorded as fetched. Everything else passes through untouched: public
    fetches keep their real fetch time and the stored-object reads the
    immutable artifact."""

    def __init__(self, inner: Any) -> None:
        super().__init__()
        self._inner = inner

    @property
    def tool_name(self) -> str:
        return str(self._inner.tool_name)

    @property
    def tool_spec(self) -> ToolSpec:
        return self._inner.tool_spec

    @property
    def tool_type(self) -> str:
        return "python"

    @staticmethod
    def _event(use: ToolUse, answer: dict[str, Any]) -> ToolResultEvent:
        envelope: dict[str, Any] = {"toolUseId": use["toolUseId"],
                                    "content": [{"json": answer}],
                                    "status": "success"}
        return ToolResultEvent(envelope)  # type: ignore[arg-type]

    def _unavailable(self, name: str, args: Mapping[str, Any]) -> str | None:
        note = HISTORICAL_READ_UNAVAILABLE.get(name)
        if note is not None:
            return note
        if name == "record_query":
            if args.get("kind") is not None or args.get("object") is not None:
                return _RECORD_OBJECT_UNAVAILABLE
            if args.get("scope") == "all_rooms":
                return _RECORD_ALL_ROOMS_UNAVAILABLE
        return None

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        name = self.tool_name
        args = tool_use.get("input") or {}
        note = self._unavailable(name, args)
        if note is not None:
            yield self._event(tool_use, {"status": "unavailable", "note": note})
            return
        async for event in self._inner.stream(tool_use, invocation_state, **kwargs):
            yield event


def as_of_reads(tools: list[Any]) -> list[Any]:
    """A session's read tools under the historical lane's temporal rule
    (`HistoricalRead`). Tools that are not Strands tool objects pass through
    as they are — a caller's hand-built stand-in keeps its own behavior."""
    return [
        HistoricalRead(tool)
        if hasattr(tool, "stream") and hasattr(tool, "tool_spec") else tool
        for tool in tools
    ]
