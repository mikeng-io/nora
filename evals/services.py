"""Private per-corpus, per-draw external recordings for protocol rehearsal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nora.subject.recorded_services import RecordedServices

#: A manifest entry without `kind` stays the positional recording;
#: `journey_world` is the declarative plan the live-clock drive opens.
JOURNEY_KIND = "journey_world"
_JOURNEY_KEYS = frozenset({
    "kind", "policy", "room", "identity", "overrides", "judges",
})


@dataclass(frozen=True)
class ServiceInputs:
    corpora: dict[str, dict[str, Any]]
    raw: bytes

    def record(self, evidence: Any) -> None:
        evidence.retain("external-services.json", self.raw)
        evidence.record("external_service_inputs", ref="external-services.json",
                        sha256=hashlib.sha256(self.raw).hexdigest())


def _journey_plan(name: str, entry: dict[str, Any]) -> Any:
    """One declarative `JourneyServices`, or a refusal of its plan."""
    from nora.subject.journey_world import JourneyServices

    if set(entry) - _JOURNEY_KEYS or not {"kind", "policy", "judges"} <= set(entry):
        raise ValueError(f"journey service plan for {name!r} is malformed")
    judges = entry["judges"]
    if (not isinstance(judges, list) or len(judges) != 2
            or not all(isinstance(m, str) and m for m in judges)
            or len(set(judges)) != 2):
        raise ValueError(
            f"journey service plan for {name!r} declares not exactly two "
            "distinct judge aliases")
    if not isinstance(entry["policy"], str) or not entry["policy"]:
        raise ValueError(f"journey service plan for {name!r} declares no policy")
    room = entry.get("room", ["signal", "g1"])
    if (not isinstance(room, (list, tuple)) or len(room) != 2
            or not all(isinstance(r, str) and r for r in room)):
        raise ValueError(f"journey service plan for {name!r} declares no room")
    identity = entry.get("identity", "")
    if not isinstance(identity, str):
        raise ValueError(f"journey service plan for {name!r} declares no identity")
    overrides = entry.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError(
            f"journey service plan for {name!r} declares no overrides object")
    return JourneyServices(
        policy=entry["policy"], room=(str(room[0]), str(room[1])),
        identity=identity, overrides=dict(overrides),
        judge_models=(judges[0], judges[1]))


def _plan_for(name: str, entry: Any) -> Any:
    """One case's services plan: the recording it always was, or a declared kind."""
    if not isinstance(entry, dict):
        raise ValueError(f"service plan for {name!r} is malformed")
    kind = entry.get("kind", "recorded")
    if kind == "recorded":
        return RecordedServices({k: v for k, v in entry.items() if k != "kind"})
    if kind == JOURNEY_KIND:
        return _journey_plan(name, entry)
    raise ValueError(f"unknown service kind {kind!r} for {name!r}")


def load_services(path: Path) -> ServiceInputs:
    raw = path.read_bytes()
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"version", "corpora"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["corpora"], dict) or not data["corpora"]):
        raise ValueError("invalid case service manifest")
    result = {}
    for corpus, cases in data["corpora"].items():
        if (not isinstance(corpus, str) or not corpus or not isinstance(cases, dict)
                or not cases or any(not isinstance(name, str) or not name for name in cases)):
            raise ValueError("invalid case service names")
        result[corpus] = {name: _plan_for(name, entry) for name, entry in cases.items()}
    return ServiceInputs(result, raw)
