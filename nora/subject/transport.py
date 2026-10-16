"""The transport tier, held for a sandbox — what each network's connection is
doing, which no anchor can reconstruct.

`<presence>` renders two things read LIVE on every turn: each network's
`health()`, and her own handle wherever an adapter can report one (Instagram's
bridge `/health`). Production reads both off running transports. A sandbox has
none running, and unheld it says so:

- Signal's and Instagram's adapters are composed but their listeners never
  start, so `health()` reads `connected=False` — «reconnecting right now»;
- Discord's and WhatsApp's are forced OFF (`evals/subject_config.FORCE_OFF`),
  so they are absent from the reader's map — «not running in this deployment»;
- Instagram's bridge answers on a docker network the host cannot resolve, so
  the live handle read fails and her Instagram handle is simply missing.

Production renders «Every one of them is up right now» and her handles. So an
unheld sandbox sends a prompt the deployment does not send — every presence
case would be measuring a Nora who believes every network she is on is down.

WHY A HELD VALUE AND NOT A RECONSTRUCTION. Health at the anchor's instant is
not recorded anywhere: it is the same class of tier as `presence.` (the Life360
feed), which `SKIP_PREFIX` already declares no anchor can reconstruct. So the
sandbox states ONE value for it, uniformly — every network the DEPLOYMENT runs
is `up`, which is what production renders on an ordinary turn — and a case
that needs anything else varies it as a COUNTERFACTUAL (`varies: transport`),
counted apart by `census()`. Neither is ever a per-case default.

The held adapters answer `health()` and nothing else. They are placed in the
PRESENCE READER's map only, never in the adapter registry, so a send still goes
wherever it went before — to nothing, for a network the sandbox turned off.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from nora.domain.ports import AdapterHealth

#: `nora.domain.self_presence.TransportState`, spelled out rather than
#: imported so a subject composed from a build without `<presence>` (the
#: "before" arm of a presence measurement) can still load this module.
TRANSPORT_STATES = frozenset({
    "up", "reconnecting", "needs_a_person", "not_running", "unknown",
})


class _UncheckedError(RuntimeError):
    """What a held `unknown` raises — the reader renders a raising `health()`
    as «could not be checked» (R-27), which is exactly the state asked for."""


@dataclass(frozen=True, slots=True)
class HeldTransport:
    """One network's connection, as the sandbox holds it. Answers `health()`
    the way a running adapter in that state would."""

    platform: str
    state: str

    async def health(self) -> AdapterHealth:
        if self.state == "unknown":
            raise _UncheckedError(f"{self.platform}: held as unchecked")
        return AdapterHealth(
            platform=self.platform,
            connected=self.state == "up",
            degraded=self.state == "needs_a_person",
        )


def held_states(
    networks: Iterable[str], varied: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Every network the deployment runs, `up`, with `varied` over it.

    Refuses a state outside `TRANSPORT_STATES`: a typo'd state would render
    nothing the case meant, and read as a clean measurement of it.
    """
    states = dict.fromkeys(networks, "up")
    for network, state in (varied or {}).items():
        if state not in TRANSPORT_STATES:
            raise ValueError(
                f"transport state {state!r} for {network!r} is not one of "
                f"{sorted(TRANSPORT_STATES)}"
            )
        states[network] = state
    return states


def hold_transports(
    subject: Any,
    *,
    networks: Iterable[str],
    varied: Mapping[str, str] | None = None,
    live_handles: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Hold the transport tier on `subject`'s one `<presence>` reader.

    Returns the states held, or `None` when the graph has no reader — a build
    from before `<presence>` existed, where there is nothing to hold and the
    prompt carries no transport state at all. `None` is reported rather than
    raised so one harness can drive both arms of a before/after measurement.

    `live_handles` stands in for the handle an adapter would report live; a
    network absent from it keeps the reader's DECLARED value (`[agent.handles]`)
    exactly as a failed live read does in production.
    """
    reader = getattr(subject.deps, "self_presence", None)
    if reader is None:
        return None
    states = held_states(networks, varied)
    # The reader's map, not the registry's: a held network answers `health()`
    # and is never something a send can be routed to.
    reader._adapters = {
        network: HeldTransport(network, state)
        for network, state in states.items() if state != "not_running"
    }
    reader._live = {
        network: _answering(handle) for network, handle in (live_handles or {}).items()
    }
    return states


def _answering(handle: str) -> Any:
    async def read() -> str:
        return handle

    return read
