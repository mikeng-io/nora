"""A case: one unit's ordered recorded wakes plus expectations.

The format is JSON. Recorded wake ids and any expectation that quotes
production content live only in the private host-side case directory;
`generated_case` builds the synthetic fixtures tests use — generated
rows, never production ones.

Two measurement versions share the file. A version-1 case is the legacy
state/replay diagnostic: every expectation anchors a recorded wake and
names the store kind its claim is judged against. A version-2 case is
the behavior measure: every expectation anchors a declared later input
(`turns`) and names `behavior`, claiming what the produced turn recalls,
does or refrains from — never which store carries it. A file with no
declared version, or one neither 1 nor 2, is refused: an ambiguous
measurement never silently becomes another.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nora.platform.bridge.envelope import Envelope
from nora.platform.bridge.rehydrate import message_from_dict
from nora.subject.eval_evidence import EvidenceError
from nora.subject.journey import declared_envelope, declared_source

#: The stores a version-1 expectation's `kind` names; the diff kinds it is
#: judged against (`evals.cognition_loop.state`).
STATE_KINDS = (
    "graph", "profile", "note", "memo", "event", "topic",
    "lesson", "standing", "reminder", "wake",
)

#: The only kind a version-2 expectation names: the claim is about the
#: turn's observable behavior — the state diff and after-state stay in
#: the digest as provenance, all kinds, never as the oracle.
BEHAVIOR_KIND = "behavior"


@dataclass(frozen=True, slots=True)
class Expectation:
    """One plain-words claim. A version-1 claim anchors `wake` (position
    in `Case.wakes`, 0-based) and names a state kind. A version-2 claim
    anchors `turn` (position in `Case.turns`) and names `behavior`;
    `check` is the declared token a finite generated verifier can bind,
    or None. `polarity` is `must` or `must_not`."""

    wake: int | None
    polarity: str  # "must" | "must_not"
    kind: str
    text: str
    turn: int | None = None
    check: str | None = None

    def __post_init__(self) -> None:
        if self.polarity not in ("must", "must_not"):
            raise EvidenceError(
                "expectation polarity must be must|must_not, "
                f"got {self.polarity!r}")
        if self.kind not in (*STATE_KINDS, BEHAVIOR_KIND):
            raise EvidenceError(
                "expectation kind must name a state kind or "
                f"{BEHAVIOR_KIND!r}, got {self.kind!r}")
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
class DeclaredTurn:
    """A version-2 later input declared as a production `Envelope`'s wire
    fields. Construction proves the declaration parses; `declared_input`
    is the one observed projection the frozen artifact and the report row
    each answer to — never to each other."""

    envelope: dict[str, Any]

    def __post_init__(self) -> None:
        try:
            declared_envelope(self.envelope)
        except (TypeError, ValueError) as exc:
            raise EvidenceError(str(exc)) from exc

    def parsed(self) -> Envelope:
        return declared_envelope(self.envelope)

    def declared_input(self) -> dict[str, Any]:
        declared = self.parsed()
        return {
            "text": message_from_dict(declared.message or {}).body,
            "event_id": declared.event_id,
            "at_ms": int(declared.sent_ms),
            "source": declared_source(declared),
        }

    def same_envelope(self, fields: Any) -> bool:
        """`fields` parses to this exact declared envelope, else False."""
        try:
            return declared_envelope(fields) == self.parsed()
        except (TypeError, ValueError):
            return False


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    unit: str
    wakes: tuple[int, ...]
    expectations: tuple[Expectation, ...] = field(default_factory=tuple)
    read_probes: tuple[ReadProbe, ...] = ()
    turns: tuple[str | DeclaredTurn, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        if self.version == 1:
            if not self.wakes:
                raise EvidenceError("a case holds at least one recorded wake")
            if self.turns:
                raise EvidenceError("a version-1 case declares no turns")
            for e in (*self.expectations, *self.read_probes):
                wake = getattr(e, "wake", None)
                if wake is None or not 0 <= wake < len(self.wakes):
                    raise EvidenceError(
                        f"expectation anchors at wake index {wake}; the case "
                        f"holds {len(self.wakes)} wake(s)")
                if getattr(e, "turn", None) is not None or getattr(e, "check", None):
                    raise EvidenceError(
                        "a version-1 expectation names a wake and a state kind")
                if isinstance(e, Expectation) and e.kind not in STATE_KINDS:
                    raise EvidenceError(
                        "a version-1 expectation kind names a state kind, "
                        f"got {e.kind!r}")
            return
        if self.version == 2:
            if self.read_probes:
                raise EvidenceError("a version-2 case declares no read probes")
            for turn in self.turns:
                if not isinstance(turn, (str, DeclaredTurn)):
                    raise EvidenceError(
                        "a version-2 turn is generated text or a declared "
                        "envelope")
            if self.expectations and not self.turns:
                raise EvidenceError(
                    "a version-2 case declares the later inputs its "
                    "expectations anchor")
            for e in self.expectations:
                if e.wake is not None:
                    raise EvidenceError(
                        "a version-2 expectation anchors a turn, not a wake")
                if e.turn is None or not 0 <= e.turn < len(self.turns):
                    raise EvidenceError(
                        f"expectation anchors at turn index {e.turn}; the case "
                        f"declares {len(self.turns)} turn(s)")
                if e.kind != BEHAVIOR_KIND:
                    raise EvidenceError(
                        "a version-2 expectation kind is 'behavior', got "
                        f"{e.kind!r}")
            return
        raise EvidenceError(f"unknown case version {self.version!r}")


def load_case(path: Path) -> Case:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or raw.get("version") not in (1, 2):
        raise EvidenceError(f"{path}: not a versioned cognition-loop case")
    version = raw["version"]

    def expectation(e: dict[str, Any]) -> Expectation:
        if version == 1:
            return Expectation(
                wake=int(e["wake"]), polarity=str(e["polarity"]),
                kind=str(e["kind"]), text=str(e["text"]))
        if not isinstance(e, dict) or "wake" in e \
                or type(e.get("turn")) is not int:
            raise EvidenceError(
                f"{path}: a version-2 expectation anchors a turn index, "
                "never a wake")
        if e.get("check") is not None and not isinstance(e["check"], str):
            raise EvidenceError(
                f"{path}: a version-2 expectation's check is text or absent")
        return Expectation(
            wake=None, turn=e["turn"], polarity=str(e["polarity"]),
            kind=str(e["kind"]), text=str(e["text"]),
            check=e.get("check"))

    def turn(raw_turn: Any) -> str | DeclaredTurn:
        if isinstance(raw_turn, str):
            return raw_turn
        if isinstance(raw_turn, dict):
            try:
                return DeclaredTurn(**raw_turn)
            except (TypeError, EvidenceError) as exc:
                raise EvidenceError(
                    f"{path}: a declared case turn is not a declared "
                    f"envelope: {exc}") from exc
        raise EvidenceError(
            f"{path}: a case turn is generated text or a declared envelope")

    return Case(
        name=str(raw["name"]), unit=str(raw["unit"]),
        wakes=tuple(int(w) for w in raw.get("wakes") or ()),
        expectations=tuple(expectation(e) for e in raw.get("expectations", [])),
        read_probes=tuple(ReadProbe(**p) for p in raw.get("read_probes", [])),
        turns=tuple(turn(t) for t in raw.get("turns") or ()),
        version=version,
    )


def write_case(case: Case, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": case.version, "name": case.name, "unit": case.unit,
        "wakes": list(case.wakes), "turns": [
            asdict(t) if isinstance(t, DeclaredTurn) else t
            for t in case.turns],
        "expectations": [
            {k: v for k, v in {
                "wake": e.wake, "turn": e.turn, "polarity": e.polarity,
                "kind": e.kind, "text": e.text, "check": e.check,
            }.items() if v is not None}
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


def generated_behavior_case() -> Case:
    """The synthetic version-2 case: generated later inputs, behavior claims."""
    return Case(
        name="generated-two-turns", unit="generated-unit",
        wakes=(), turns=(
            "the generated venue for the outing is Cedar Hall",
            "where was the generated outing held?",
        ),
        expectations=(
            Expectation(wake=None, turn=1, polarity="must",
                        kind=BEHAVIOR_KIND, check="Cedar Hall",
                        text="the reply recalls the generated venue"),
        ),
        version=2,
    )
