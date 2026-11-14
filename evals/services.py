"""Private per-corpus, per-draw external recordings for protocol rehearsal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nora.subject.recorded_services import RecordedServices


@dataclass(frozen=True)
class ServiceInputs:
    corpora: dict[str, dict[str, RecordedServices]]
    raw: bytes

    def record(self, evidence: Any) -> None:
        evidence.retain("external-services.json", self.raw)
        evidence.record("external_service_inputs", ref="external-services.json",
                        sha256=hashlib.sha256(self.raw).hexdigest())


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
        result[corpus] = {name: RecordedServices(recording) for name, recording in cases.items()}
    return ServiceInputs(result, raw)
