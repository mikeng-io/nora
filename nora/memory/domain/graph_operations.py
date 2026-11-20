"""Parse reflection's inferred proposals before admission checks graph facts."""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from typing import Literal, NotRequired, TypedDict, get_args
from uuid import uuid4

from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import (
    AddEdge,
    CreateClaim,
    CreateEntity,
    PassContext,
    Retire,
    Revise,
    Scope,
)
from nora.memory.domain.graph import Confidence, Reasoning
from nora.memory.domain.operation_fields import (
    AboutInput,
    InvalidFieldError,
    _ids,
    _instant,
    _string,
    about_links,
)

GraphOperationName = Literal["create_entity", "create_claim", "add_edge", "revise", "retire"]
WAKE_OPERATIONS: frozenset[GraphOperationName] = frozenset(get_args(GraphOperationName))


class ProposalEvidence(TypedDict):
    basis: str
    scope: Scope
    citations: tuple[str, ...]
    reasoning: str
    confidence: str


class EvidenceInput(TypedDict):
    citations: list[str]
    reasoning: Reasoning
    confidence: Confidence


class EntityInput(EvidenceInput):
    op: Literal["create_entity"]
    name: str
    aliases: NotRequired[list[str]]
    ref: NotRequired[str]


class ClaimFields(EvidenceInput):
    part: Literal["fact", "trait", "guess", "situation"]
    content: str
    prediction: NotRequired[str]
    window_end: NotRequired[str]
    valid_from: NotRequired[str]
    valid_to: NotRequired[str]
    about: NotRequired[list[AboutInput]]


class ClaimInput(ClaimFields):
    op: Literal["create_claim"]


class ReviseClaimInput(ClaimFields):
    op: Literal["revise"]
    base: str


class RetireClaimInput(EvidenceInput):
    op: Literal["retire"]
    base: str


class EdgeInput(EvidenceInput):
    op: Literal["add_edge"]
    type: Literal["about", "draws_on", "cites"]
    source: str
    target: str


GraphOperation = EntityInput | ClaimInput | ReviseClaimInput | RetireClaimInput | EdgeInput
GraphProposal = CreateEntity | CreateClaim | AddEdge | Revise | Retire
_KEYS = {
    "create_entity": EntityInput.__annotations__.keys(),
    "create_claim": ClaimInput.__annotations__.keys(),
    "revise": ReviseClaimInput.__annotations__.keys(),
    "retire": RetireClaimInput.__annotations__.keys(),
    "add_edge": EdgeInput.__annotations__.keys(),
}


def topic_refs(operations: Iterable[object]) -> dict[str, str]:
    counts: Counter[str] = Counter()
    for operation in operations:
        if isinstance(operation, Mapping) and operation.get("op") == "create_entity":
            ref = operation.get("ref")
            if isinstance(ref, str) and ref.strip():
                counts[ref] += 1
    return {ref: f"entity:{uuid4().hex}" for ref, count in counts.items() if count == 1}


def parse_graph_operation(
    raw: object, context: PassContext, allowed: Collection[GraphOperationName],
    topics: Mapping[str, str],
) -> GraphProposal | Refusal:
    if not isinstance(raw, Mapping):
        return Refusal("invalid_field", "op")
    op = raw.get("op")
    if not isinstance(op, str) or op not in allowed:
        return Refusal("invalid_field", "op")
    unexpected = next((key for key in raw if key not in _KEYS[op]), None)
    if unexpected is not None:
        return Refusal("invalid_field", str(unexpected))

    try:
        shared: ProposalEvidence = {
            "basis": "inferred",
            "scope": Scope(context.unit, exposure=None),
            "citations": _ids(raw, "citations"),
            "reasoning": _string(raw, "reasoning"),
            "confidence": _string(raw, "confidence"),
        }
        if op == "create_entity":
            ref = _string(raw, "ref") if "ref" in raw else None
            if ref is not None and ref not in topics:
                raise InvalidFieldError("ref")
            return CreateEntity(
                **shared, id=topics[ref] if ref is not None else f"entity:{uuid4().hex}",
                name=_string(raw, "name"), aliases=_ids(raw, "aliases"),
            )
        if op == "add_edge":
            kind = _string(raw, "type")
            if kind not in ("about", "draws_on", "cites"):
                raise InvalidFieldError("type")
            target = _string(raw, "target")
            return AddEdge(
                **shared, id=f"edge:{uuid4().hex}", type=kind,
                source=_string(raw, "source"), target=topics.get(target, target),
            )
        if op == "retire":
            return Retire(
                **shared, id=f"claim:{uuid4().hex}", base=_string(raw, "base"),
                expected_kind="claim",
            )
        part = _string(raw, "part")
        if part not in ("fact", "trait", "guess", "situation"):
            raise InvalidFieldError("part")
        graph_id = f"claim:{uuid4().hex}"
        claim = CreateClaim(
            **shared, id=graph_id, part=part, content=_string(raw, "content"),
            prediction=_string(raw, "prediction") if "prediction" in raw else None,
            window_end=_instant(raw, "window_end"),
            valid_from=_instant(raw, "valid_from"), valid_to=_instant(raw, "valid_to"),
            links=about_links(raw, graph_id, topics), replace_about="about" in raw,
        )
        return Revise(base=_string(raw, "base"), replacement=claim) if op == "revise" else claim
    except InvalidFieldError as exc:
        return Refusal("invalid_field", str(exc))
