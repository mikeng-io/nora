"""Witness references and truth values shared by observe and verify."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Truth = Literal["true", "false", "unknown"]
Ending = Literal["achieved", "dropped", "ceased"]
Scalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ActEvent:
    act_id: str


@dataclass(frozen=True, slots=True)
class OwnDelivery:
    """The intention's latest authorised delivery."""


@dataclass(frozen=True, slots=True)
class PersonEvent:
    person_id: str
    platform: str
    room: str
    text: str


@dataclass(frozen=True, slots=True)
class Predicate:
    graph_id: str
    path: tuple[str, ...]
    equals: Scalar

    def measure(self, value: Mapping[str, object]) -> Truth:
        field: object = value
        for key in self.path:
            if not isinstance(field, Mapping) or key not in field:
                return "unknown"
            field = field[key]
        if field is not None and type(field) not in (bool, str, int, float):
            return "unknown"
        if isinstance(field, bool) != isinstance(self.equals, bool):
            return "false"
        return "true" if field == self.equals else "false"


ConditionValue = ActEvent | OwnDelivery | PersonEvent | Predicate


def _strings(value: Mapping[str, object], *keys: str) -> tuple[str, ...] | None:
    vals = tuple(value.get(key) for key in keys)
    if all(isinstance(val, str) and val.strip() for val in vals):
        return tuple(val for val in vals if isinstance(val, str))
    return None


def decode_condition(form: str | None, value: object) -> ConditionValue | None:
    """Decode an act id, an explicit room message, or an aggregate field comparison."""
    if not isinstance(value, Mapping):
        return None
    if form == "act_event" and (parts := _strings(value, "act_id")):
        return ActEvent(parts[0])
    if form == "act_event" and value == {"own_delivery": True} and value["own_delivery"] is True:
        return OwnDelivery()
    if form == "person_event" and (
        parts := _strings(value, "person_id", "platform", "room", "text")
    ):
        person, platform, room, text = parts
        return PersonEvent(person, platform, room, text)
    if form == "predicate" and (parts := _strings(value, "graph_id")):
        path = value.get("path")
        target = value.get("equals")
        if (
            isinstance(path, list) and path and all(isinstance(key, str) and key for key in path)
            and "equals" in value and (target is None or type(target) in (bool, str, int, float))
        ):
            return Predicate(parts[0], tuple(path), target)
    return None


def decode_condition_trigger(trigger: object) -> ConditionValue | None:
    if not isinstance(trigger, Mapping) or trigger.get("kind") != "condition":
        return None
    form = trigger.get("form")
    return decode_condition(form if isinstance(form, str) else None, trigger.get("value"))


def ending_for(
    done_when: Truth, holds_while: Truth, drop_when: Truth,
) -> Ending | None:
    if done_when == "true":
        return "achieved"
    if drop_when == "true":
        return "dropped"
    if holds_while == "false":
        return "ceased"
    return None
