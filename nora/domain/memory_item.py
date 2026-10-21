"""One source-grounded memory envelope, independent of storage and models."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MemoryItem:
    subject: str
    part: str
    kind: str
    content: Mapping[str, object]
    mode: str
    citations: tuple[str, ...]
    said_at: int | None
    happened_at: int | None
    source_medium: tuple[str, ...] = ()
    location: bool = False
    strength: Mapping[str, object] = field(default_factory=dict)


TRAIT_SLOTS = {
    "topic": ("interest", "depth", "stance"),
    "habit": ("action", "rhythm", "setting"),
    "skill": ("capability", "level"),
    "preference": ("object", "polarity"),
    "disposition": ("trigger", "response"),
    "style": ("language", "register", "length", "timing", "medium"),
    "value": ("priority", "tradeoff"),
    "social": ("counterpart", "role", "pattern"),
    "goal": ("aim", "horizon", "pursuit"),
}


def content_text(content: Mapping[str, str]) -> str:
    """A projection, never a second model-authored claim."""
    order = tuple(dict.fromkeys(("statement", "context", *(slot
        for slots in TRAIT_SLOTS.values() for slot in slots),
        "proposed_cause", "alternative", "exceptions")))
    keys = [key for key in order if key in content]
    keys.extend(sorted(set(content) - set(keys)))
    return "; ".join(f"{key}: {' '.join(content[key].split())}" for key in keys)


LOCATION_ATTRIBUTES = ("workplace", "home_area", "commute_route", "regular_places")


def is_location_fact(key: str | None, value: object) -> bool:
    """Known spatial profile slots carry the existing location read setting."""
    return key == "residence" or (key == "attribute" and isinstance(value, dict)
                                  and value.get("name") in LOCATION_ATTRIBUTES)
