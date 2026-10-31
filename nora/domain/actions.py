"""Bridge action ownership vocabulary (design/platform-bridges §02-wire).

`ActionOwner` is domain — it names WHO may run a durable outbound action,
and that name travels everywhere: the Deliverer binds a live invocation,
the bridge services fence execution against it, the store persists it.
It sits below every consumer (agent, platform, infra) so all of them may
import the same type without crossing layers. The wire REQUEST/RESPONSE
shapes stay in `nora.platform.bridge.wire`, which re-exports this type so
existing `wire.ActionOwner` references keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The four authorities an action may cite. `work` carries the fenced
#: bridge_work id + generation; `event_receipt` the journaled receipt
#: intent; `otp_challenge` a live OTP row; `live_invocation` the pending
#: outbound `messages` row the reply path reserved.
OWNER_KINDS = frozenset(
    {"work", "event_receipt", "otp_challenge", "live_invocation"})


@dataclass(frozen=True)
class ActionOwner:
    """Who may run this action. `work` owners carry the fenced work id +
    generation the bridge re-validates against bridge_work before executing."""
    kind: str
    id: str
    generation: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "id": self.id}
        if self.generation is not None:
            d["generation"] = self.generation
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionOwner:
        if d.get("kind") not in OWNER_KINDS:
            raise ValueError(f"unknown owner kind {d.get('kind')!r}")
        return cls(kind=d["kind"], id=str(d["id"]),
                   generation=d.get("generation"))
