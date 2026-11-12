"""The inertness assertion, run before the first wake.

The subject is COMPOSED so it cannot deliver: the run's reserved
overrides keep every egress loop out of the object graph, and `obtain`
wraps composition in `landed_platform_transports`, so every adapter's
send and react lands nowhere. This guard is the backstop that the
composition actually did that — a check, never the mechanism.

It refuses on:

- a live egress worker (`bridge_runtime`, `fire_path`, `social_poller`,
  `computer_lifecycle`) — each optional, each live when wired;
- an adapter registry that cannot be enumerated, an adapter whose
  transports cannot be identified, or a `*Sender`/`*Reactor` that is
  not `Landed*` — for any platform;
- a background task the composition started that is not known-inert —
  the summons consumer, the heartbeat, the compute poller and every
  future loop all refuse until named here.

`dispatcher` is a required inbound entry point — always present on a
composed subject — so it is reported, never refused: the only send it
could drive goes through the transports checked here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nora.subject.eval_evidence import EvidenceError

#: The egress loops a subject can compose — each optional, each live
#: when wired.
DELIVERY_WORKERS = (
    "bridge_runtime", "fire_path",
    "social_poller", "computer_lifecycle",
)

#: The only spawned tasks an inert subject may hold: the config file
#: watcher, which reads the workdir's own files and nothing else.
INERT_BACKGROUND = ("ReloadFiles.run",)

#: Person-visible transports an adapter can keep: a `*Sender` posts
#: text and comments, a `*Reactor` places reactions.
TRANSPORT_SUFFIXES = ("Sender", "Reactor")


def _adapter_transports(adapter: Any) -> dict[str, str]:
    """Every attribute holding a person-visible transport, by type name."""
    try:
        members = dict(vars(adapter))
    except TypeError:
        members = {
            name: getattr(adapter, name) for name in dir(adapter)
            if "send" in name.lower() or "react" in name.lower()}
    return {
        name: type(value).__name__
        for name, value in members.items()
        if type(value).__name__.endswith(TRANSPORT_SUFFIXES)
    }


def _background_tasks(background: Any) -> list[str] | None:
    tasks = getattr(background, "_tasks", None)
    if not isinstance(tasks, set):
        return None
    names: list[str] = []
    for task in tasks:
        get_coro = getattr(task, "get_coro", None)
        coro = get_coro() if callable(get_coro) else None
        code = getattr(coro, "cr_code", None)
        names.append(getattr(code, "co_qualname", type(coro).__name__))
    return sorted(names)


def egress_report(subject: Any) -> dict[str, Any]:
    deps = subject.deps
    adapters = getattr(getattr(deps, "registry", None), "adapters", None)
    transports: dict[str, dict[str, str]] | None = None
    if isinstance(adapters, Mapping):
        transports = {p: _adapter_transports(a) for p, a in adapters.items()}
    return {
        "workers": {w: getattr(deps, w, None) is not None for w in DELIVERY_WORKERS},
        "transports": transports,
        "background": _background_tasks(getattr(deps, "background", None)),
    }


def assert_no_egress(subject: Any) -> dict[str, Any]:
    """Refuse the run when any egress path is wired live — or cannot be
    shown not to be."""
    report = egress_report(subject)
    wired = sorted(w for w, live in report["workers"].items() if live)
    if wired:
        raise EvidenceError(
            f"delivery components are wired into the subject: "
            f"{', '.join(wired)}; the eval composes a subject that can "
            "never deliver"
        )
    if report["background"] is None:
        raise EvidenceError(
            "the subject's background tasks cannot be enumerated; "
            "a loop the check cannot see is a refusal"
        )
    live_tasks = sorted(
        name for name in report["background"] if name not in INERT_BACKGROUND)
    if live_tasks:
        raise EvidenceError(
            f"the composition started background work: {live_tasks}; an "
            "inert subject holds no task that can reach the world"
        )
    if report["transports"] is None:
        raise EvidenceError(
            "the subject's platform adapters cannot be enumerated; "
            "a send surface the check cannot read is a refusal"
        )
    unidentified = sorted(
        p for p, found in report["transports"].items() if not found)
    if unidentified:
        raise EvidenceError(
            f"adapter transport(s) cannot be identified: {unidentified}; "
            "an adapter keeping its sends where the check cannot see "
            "them is a refusal"
        )
    real = {
        platform: found
        for platform, found in report["transports"].items()
        if any(not name.startswith("Landed") for name in found.values())
    }
    if real:
        raise EvidenceError(
            f"real platform transports are wired: {real}; every adapter's "
            "sends and reacts must land nowhere"
        )
    return report
