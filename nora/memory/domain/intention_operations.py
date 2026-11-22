"""Parse the intention operations shared by engagement and debrief."""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from nora.domain.reminders import Recurrence
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import (
    CloseIntention,
    Condition,
    CreateIntention,
    Link,
    PassContext,
    Retire,
    Revise,
    Scope,
)
from nora.memory.domain.conditions import Scalar, decode_condition
from nora.memory.domain.graph import DeliveryMode, IntentionForm
from nora.memory.domain.time_trigger import aware_instant, decode_time_trigger


class ActById(TypedDict):
    act_id: str


class DeliveryOfThisIntention(TypedDict):
    own_delivery: Literal[True]


class ActEventInput(TypedDict):
    form: Literal["act_event"]
    value: ActById | DeliveryOfThisIntention


class PersonEventValue(TypedDict):
    person_id: str
    platform: str
    room: str
    text: str


class PersonEventInput(TypedDict):
    form: Literal["person_event"]
    value: PersonEventValue


class PredicateValue(TypedDict):
    graph_id: str
    path: list[str]
    equals: Scalar


class PredicateInput(TypedDict):
    form: Literal["predicate"]
    value: PredicateValue


class DeadlineValue(TypedDict):
    at: str


class DeadlineInput(TypedDict):
    form: Literal["deadline"]
    value: str | DeadlineValue


class UntypedInput(TypedDict):
    form: Literal["untyped"]
    value: str


ConditionInput = ActEventInput | PersonEventInput | PredicateInput | DeadlineInput | UntypedInput


class RecurrenceInput(TypedDict):
    every: Recurrence
    count: int
    interval: NotRequired[int]
    until: NotRequired[str]


class TimeTriggerInput(TypedDict):
    kind: Literal["time"]
    at: str
    zone: str
    recurrence: NotRequired[RecurrenceInput]


class ActEventTriggerInput(ActEventInput):
    kind: Literal["condition"]


class PersonEventTriggerInput(PersonEventInput):
    kind: Literal["condition"]


class PredicateTriggerInput(PredicateInput):
    kind: Literal["condition"]


class IntentionFields(TypedDict):
    form: IntentionForm
    condition: ConditionInput
    drop_when: NotRequired[ConditionInput]
    deadline: NotRequired[str]
    review_time: NotRequired[str]
    delivery: DeliveryMode
    trigger: NotRequired[
        TimeTriggerInput | ActEventTriggerInput | PersonEventTriggerInput | PredicateTriggerInput
    ]
    owner: str
    owed_to: str
    rests_on: NotRequired[list[str]]
    serves: NotRequired[list[str]]
    citations: NotRequired[list[str]]


class OpenOperation(IntentionFields):
    op: Literal["open"]
    rooms: NotRequired[list[str]]


class ReviseOperation(IntentionFields):
    op: Literal["revise"]
    base: str


class DropOperation(TypedDict):
    op: Literal["drop"]
    base: str
    citations: NotRequired[list[str]]
    ending: NotRequired[Literal["dropped"]]


class CloseOperation(TypedDict):
    op: Literal["close"]
    base: str
    witness: str


IntentionOperation = OpenOperation | ReviseOperation | DropOperation | CloseOperation
IntentionProposal = CreateIntention | Revise | Retire | CloseIntention

_KEYS = {
    "open": frozenset(OpenOperation.__annotations__),
    "revise": frozenset(ReviseOperation.__annotations__),
    "drop": frozenset(DropOperation.__annotations__),
    "close": frozenset(CloseOperation.__annotations__),
}


class _InvalidFieldError(ValueError):
    pass


def parse_intention_operation(raw: object, context: PassContext) -> IntentionProposal | Refusal:
    """Return a proposal or the field the model needs to repair."""
    if not isinstance(raw, Mapping):
        return Refusal("invalid_field", "op")
    op = raw.get("op")
    if not isinstance(op, str) or op not in _KEYS:
        return Refusal("invalid_field", "op")
    if raw.get("ending", "dropped") != "dropped":
        return Refusal("invalid_field", "ending")
    unexpected = next((key for key in raw if key not in _KEYS[op]), None)
    if unexpected is not None:
        return Refusal("invalid_field", str(unexpected))
    try:
        if op == "close":
            witness = _string(raw, "witness")
            return CloseIntention(
                id=_string(raw, "base"), basis="committed", scope=Scope(context.unit),
                witness=witness, citations=(witness,),
            )
        if op == "drop":
            return Retire(
                id=_id(), basis="committed", scope=Scope(context.unit, exposure=None),
                base=_string(raw, "base"), ending="dropped", citations=_ids(raw, "citations"),
            )
        intention = _intention(raw, context)
        if op == "revise":
            return Revise(base=_string(raw, "base"), replacement=intention)
        return intention
    except _InvalidFieldError as exc:
        return Refusal("invalid_field", str(exc))


def limit_to_delivery(proposal: IntentionProposal, intention: str) -> Refusal | None:
    """Restrict a proactive pass to changing the intention it delivered."""
    if isinstance(proposal, CreateIntention):
        return Refusal("not_this_delivery", "op")
    base = proposal.id if isinstance(proposal, CloseIntention) else proposal.base
    return None if base == intention else Refusal("not_this_delivery", "base")


def _intention(raw: Mapping[str, object], context: PassContext) -> CreateIntention:
    form = raw.get("form")
    if form not in ("achieve", "maintain"):
        raise _InvalidFieldError("form")
    condition = _condition(raw.get("condition"), "condition")
    if form == "achieve" and condition.form == "deadline":
        raise _InvalidFieldError("condition.form")
    drop = _condition(raw["drop_when"], "drop_when") if "drop_when" in raw else None
    deadline = _instant(raw, "deadline")
    if condition.form == "untyped":
        if form == "achieve" and deadline is None:
            raise _InvalidFieldError("deadline")
        if form == "maintain" and (drop is None or drop.form != "deadline"):
            raise _InvalidFieldError("drop_when")
    mode, trigger = raw.get("delivery"), raw.get("trigger")
    if mode not in ("when_it_comes_up", "choose_moment"):
        raise _InvalidFieldError("delivery")
    has_trigger = (
        isinstance(trigger, Mapping) and trigger.get("kind") == "condition"
    ) or decode_time_trigger(trigger) is not None
    if (mode == "choose_moment" and not has_trigger) or (
        mode == "when_it_comes_up" and "trigger" in raw
    ):
        raise _InvalidFieldError("trigger")
    rooms = _ids(raw, "rooms")
    if "rooms" in raw and (not rooms or mode != "choose_moment"):
        raise _InvalidFieldError("rooms")
    owner, owed_to = _string(raw, "owner"), _string(raw, "owed_to")
    graph_id = _id()
    people = tuple(dict.fromkeys(person for person in (owner, owed_to) if person != "nora"))
    links = tuple(
        Link(type=field, source=graph_id, target=target, basis="committed")
        for field in ("rests_on", "serves") for target in _ids(raw, field)
    )
    return CreateIntention(
        id=graph_id, basis="committed", scope=Scope(context.unit, persons=people, exposure=None),
        owner=owner, owed_to=owed_to, form=str(form), condition=condition, drop_when=drop,
        deadline=deadline, review_time=_instant(raw, "review_time"),
        delivery_mode=str(mode), trigger=trigger, citations=_ids(raw, "citations"), links=links,
        rooms=rooms,
    )


def _id() -> str:
    return f"intention:{uuid4().hex}"


def _string(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidFieldError(field)
    return value


def _ids(raw: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise _InvalidFieldError(field)
    return tuple(dict.fromkeys(value))


def _instant(raw: Mapping[str, object], field: str) -> datetime | None:
    if field not in raw:
        return None
    value = aware_instant(raw[field])
    if value is None:
        raise _InvalidFieldError(field)
    return value


def _condition(raw: object, field: str) -> Condition:
    if not isinstance(raw, Mapping):
        raise _InvalidFieldError(field)
    form, value = raw.get("form"), raw.get("value")
    if form in ("act_event", "person_event", "predicate"):
        if decode_condition(form, value) is None:
            raise _InvalidFieldError(f"{field}.value")
    elif form == "deadline":
        if aware_instant(value.get("at") if isinstance(value, Mapping) else value) is None:
            raise _InvalidFieldError(f"{field}.value")
    elif form == "untyped":
        if not isinstance(value, str) or not value.strip():
            raise _InvalidFieldError(f"{field}.value")
    else:
        raise _InvalidFieldError(f"{field}.form")
    return Condition(str(form), value)
