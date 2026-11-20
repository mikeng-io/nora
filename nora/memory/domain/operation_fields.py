"""Fields shared by the model's proposal parsers."""

from collections.abc import Mapping
from datetime import datetime
from typing import TypedDict

from nora.memory.domain.admission_proposals import Link
from nora.memory.domain.graph import Confidence, Reasoning
from nora.memory.domain.time_trigger import aware_instant


class AboutInput(TypedDict):
    target: str
    reasoning: Reasoning
    confidence: Confidence


class InvalidFieldError(ValueError):
    pass


def _string(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidFieldError(field)
    return value


def _ids(raw: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise InvalidFieldError(field)
    return tuple(dict.fromkeys(value))


def _instant(raw: Mapping[str, object], field: str) -> datetime | None:
    if field not in raw:
        return None
    value = aware_instant(raw[field])
    if value is None:
        raise InvalidFieldError(field)
    return value


def about_links(
    raw: Mapping[str, object], source: str, topics: Mapping[str, str],
) -> tuple[Link, ...]:
    values = raw.get("about", [])
    if not isinstance(values, list):
        raise InvalidFieldError("about")
    links = []
    targets = set()
    for value in values:
        if not isinstance(value, Mapping) or value.keys() != AboutInput.__annotations__.keys():
            raise InvalidFieldError("about")
        ref = _string(value, "target")
        target = topics.get(ref, ref)
        if target in targets:
            raise InvalidFieldError("about.target")
        targets.add(target)
        links.append(Link(
            type="about", source=source, target=target, basis="inferred",
            reasoning=_string(value, "reasoning"), confidence=_string(value, "confidence"),
        ))
    return tuple(links)
