"""A case: one unit's ordered recorded wakes plus state expectations.

The format is JSON. Recorded wake ids and any expectation that quotes
production content live only in the private host-side case directory;
`generated_case` builds the synthetic fixtures tests use — generated
rows, never production ones.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from nora.subject.eval_evidence import EvidenceError

#: The stores an expectation's `kind` names; the diff kinds it is judged
#: against (`scripts.cognition_loop.state`).
STATE_KINDS = (
    "graph", "profile", "note", "memo", "event", "topic",
    "lesson", "standing", "reminder", "wake",
)


@dataclass(frozen=True, slots=True)
class Expectation:
    """One plain-words claim about the state after a wake. `wake` is the
    anchor's position in `Case.wakes` (0-based), `polarity` is `must` or
    `must_not`, `kind` the store the claim concerns."""

    wake: int
    polarity: str  # "must" | "must_not"
    kind: str
    text: str

    def __post_init__(self) -> None:
        if self.polarity not in ("must", "must_not"):
            raise EvidenceError(
                "expectation polarity must be must|must_not, "
                f"got {self.polarity!r}")
        if self.kind not in STATE_KINDS:
            raise EvidenceError(
                "expectation kind must name a state kind, "
                f"got {self.kind!r}")
        if not self.text.strip():
            raise EvidenceError("expectation text is empty")


@dataclass(frozen=True, slots=True)
class ReadProbe:
    """A model-free memory read through the session's actual room-bound tools."""

    wake: int
    tool: str
    arguments: dict
    expected: str = "found"

    def __post_init__(self) -> None:
        if self.tool not in ("memory_search", "memory_read"):
            raise EvidenceError("a read probe can only call memory_search or memory_read")
        if self.expected not in ("found", "nothing_found", "refused"):
            raise EvidenceError("a read probe expects found, nothing_found or refused")


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    unit: str
    wakes: tuple[int, ...]
    expectations: tuple[Expectation, ...] = field(default_factory=tuple)
    read_probes: tuple[ReadProbe, ...] = ()

    def __post_init__(self) -> None:
        if not self.wakes:
            raise EvidenceError("a case holds at least one recorded wake")
        for e in (*self.expectations, *self.read_probes):
            if not 0 <= e.wake < len(self.wakes):
                raise EvidenceError(
                    f"expectation anchors at wake index {e.wake}; the case holds "
                    f"{len(self.wakes)} wake(s)"
                )


def load_case(path: Path) -> Case:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise EvidenceError(f"{path}: not a version-1 cognition-loop case")
    expectations = tuple(
        Expectation(
            wake=int(e["wake"]), polarity=str(e["polarity"]),
            kind=str(e["kind"]), text=str(e["text"]),
        )
        for e in raw.get("expectations", [])
    )
    return Case(
        name=str(raw["name"]), unit=str(raw["unit"]),
        wakes=tuple(int(w) for w in raw["wakes"]),
        expectations=expectations,
        read_probes=tuple(ReadProbe(**p) for p in raw.get("read_probes", [])),
    )


def write_case(case: Case, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "name": case.name, "unit": case.unit,
        "wakes": list(case.wakes),
        "expectations": [
            {"wake": e.wake, "polarity": e.polarity, "kind": e.kind, "text": e.text}
            for e in case.expectations
        ],
        "read_probes": [asdict(p) for p in case.read_probes],
    }, indent=2, ensure_ascii=False) + "\n")


def generated_case() -> Case:
    """The synthetic case the tests exercise: generated content only."""
    return Case(
        name="generated-two-wakes", unit="generated-unit",
        wakes=(11, 22),
        expectations=(
            Expectation(wake=0, polarity="must", kind="note",
                        text="a note exists that was not present before the wake"),
            Expectation(wake=1, polarity="must_not", kind="reminder",
                        text="no reminder fires in the past"),
        ),
    )
