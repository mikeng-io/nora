"""`PresenceReader` — where she is, read from the systems that know it
(design/self-awareness/02).

ONE implementation, three callers: the tag path's assembler
(`TranscriptInjection`), the fire path (`FirePath.build_context`, which
serves participation, reminders and the proactive act turn), and the
proactive decide pass, which reaches it through a callable the composition
root hands it. None of them builds its own idea of which rooms exist.

Two sources, both already in the process:

- **The room table**, `policy.toml` as loaded at startup. It changes only on a
  restart, so within a process it is stable.
- **Adapter health**, `PlatformAdapter.health()`. It changes at any moment, so
  it is read on EVERY call and this class keeps nothing between calls. A
  cached "up" is how she promises a send that then fails.

And her own handles: declared in `[agent.handles]` (composition passes the
mapping; this layer never reads config), overlaid with a LIVE read wherever an
adapter can report one — today Instagram, from the bridge's `/health`. A handle
neither source has is omitted, never guessed.

**Never raises.** A failure anywhere is less text, not a lost turn: a health
read that raises is `unknown` (R-27's «I could not check», never «up»), and a
live handle read that fails falls back to the declared value or to nothing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from nora.domain.ports import AdapterHealth
from nora.domain.self_presence import (
    Presence,
    PresenceRoom,
    TransportState,
    transport_state,
)
from nora.policy import Conversation, Policy

log = logging.getLogger(__name__)

#: How long one `health()` or one live handle read may take. Both are on the
#: front of a turn, gathered beside the transcript and memory reads; a bridge
#: that hangs must cost a sentence, not the turn.
DEFAULT_READ_TIMEOUT_SECONDS = 1.0


class _Reports(Protocol):
    """The one adapter verb this reader needs."""

    async def health(self) -> AdapterHealth: ...


def _as_handle(value: str) -> str:
    """`noraexample` and `@noraexample` are one handle; render it one way."""
    return "@" + value.strip().lstrip("@")


class PresenceReader:
    def __init__(
        self,
        *,
        policy: Policy,
        adapters: Mapping[str, _Reports],
        handles: Mapping[str, str] | None = None,
        live_handles: Mapping[str, Callable[[], Awaitable[str | None]]] | None = None,
        timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> None:
        self._policy = policy
        # The SAME map the tag path sends through (`registry.adapters`): a
        # network with no adapter here is one she cannot hear or speak on in
        # this process, which is `not_running`, not `reconnecting`.
        self._adapters = adapters
        self._declared = {
            network: _as_handle(value)
            for network, value in (handles or {}).items()
            if value and value.strip().lstrip("@")
        }
        self._live = dict(live_handles or {})
        self._timeout = timeout_seconds

    async def read(
        self, *, platform: str, conversation_id: str, direct: bool = False,
    ) -> Presence:
        """Where she is, standing in `(platform, conversation_id)`, now."""
        convo = None if direct else self._policy.conversation(platform, conversation_id)
        unit_convos: tuple[Conversation, ...] | None = None
        other_groups = bool(self._policy.conversations) if direct else False
        if convo is not None:
            unit_convos = tuple(
                c for c in self._policy.conversations
                if c.unit == convo.unit and c is not convo
            )
            # D1 = B: THAT other groups exist, never which. Nothing about them
            # leaves this line — not a label, not a count.
            other_groups = any(c.unit != convo.unit for c in self._policy.conversations)

        networks = {platform, *(c.platform for c in unit_convos or ())}
        states, handles = await asyncio.gather(
            self._states(networks), self._handles(),
        )
        return Presence(
            platform=platform,
            # The operator's label, or nothing — never `primary_id`, which is
            # an identifier (R-18).
            label=convo.label if convo is not None else "",
            direct=direct,
            here_state=states[platform],
            unit_rooms=None if unit_convos is None else tuple(
                PresenceRoom(platform=c.platform, label=c.label, state=states[c.platform])
                for c in unit_convos
            ),
            other_groups=other_groups,
            handles=handles,
        )

    async def _states(self, networks: set[str]) -> dict[str, TransportState]:
        ordered = sorted(networks)
        results = await asyncio.gather(*(self.state(n) for n in ordered))
        return dict(zip(ordered, results, strict=True))

    async def state(self, network: str) -> TransportState:
        """One network's transport, read now, bounded, never raising.

        Public because `send_to` asks the same question at call time
        (`nora.agent.relay`, operator 2026-09-24): whether a relay may be
        attempted is decided by the SAME mapping and the same bound that
        rendered «Discord is down» into `<presence>`, so the block and the
        tool cannot disagree about a network. Nothing is cached, here or
        there: a health read is always of this moment.
        """
        adapter = self._adapters.get(network)
        if adapter is None:
            return "not_running"
        try:
            health = await asyncio.wait_for(adapter.health(), self._timeout)
        except Exception:
            log.warning("health() failed for %s; rendering it as unchecked", network,
                        exc_info=True)
            return "unknown"
        return transport_state(health)

    async def _handles(self) -> tuple[tuple[str, str], ...]:
        handles = dict(self._declared)
        networks = sorted(self._live)
        answers = await asyncio.gather(*(self._live_handle(n) for n in networks))
        for network, answer in zip(networks, answers, strict=True):
            if answer:
                handles[network] = answer
        return tuple(handles.items())

    async def _live_handle(self, network: str) -> str | None:
        try:
            value = await asyncio.wait_for(self._live[network](), self._timeout)
        except Exception:
            log.debug("live handle read failed for %s", network, exc_info=True)
            return None
        if not value or not value.strip().lstrip("@"):
            return None
        return _as_handle(value)
