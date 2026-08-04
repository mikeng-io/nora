"""identity.toml → reverse index (spec C.6, D.2). Startup-only; no hot reload in v2 (Q4)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .domain.models import NormalizedMessage, PersonRef

# Honcho requires ^[a-zA-Z0-9_-]+$ for peer ids, and honcho_peer_id == person_id.
_PERSON_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class PersonsError(Exception):
    """A person mapping problem that must stop the process at startup."""


def sanitize_unmapped(sender_id: str) -> str:
    """'+85212345678' → '852…678'. Never used as a Honcho peer id — display only."""
    digits = sender_id.lstrip("+")
    if len(digits) <= 6:
        return sender_id.lstrip("+") or sender_id
    return f"{digits[:3]}…{digits[-3:]}"


@dataclass(frozen=True)
class Persons:
    by_person: dict[str, PersonRef]
    index: dict[tuple[str, str], str] = field(repr=False)   # (platform, sender_id) → person_id

    def resolve(self, platform: str, sender_id: str) -> PersonRef | None:
        person_id = self.index.get((platform, sender_id))
        return self.by_person[person_id] if person_id else None

    def display_name(self, msg: NormalizedMessage) -> str:
        ref = self.resolve(msg.platform, msg.sender_id)
        if ref:
            return ref.name
        return msg.sender_name or sanitize_unmapped(msg.sender_id)


class _PersonEntry(BaseModel):
    """Shape validation only — the coercion of `sender_id` to `str` (needed for TOML's
    real integer types, e.g. Telegram numeric IDs) stays down in the reverse-index build
    below, alongside the domain logic it's part of.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    platforms: dict[str, str | int] = Field(default_factory=dict)


class _PersonsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    persons: dict[str, _PersonEntry] = Field(default_factory=dict)

    @field_validator("persons")
    @classmethod
    def _check_person_ids(
        cls, value: dict[str, _PersonEntry]
    ) -> dict[str, _PersonEntry]:
        for person_id in value:
            if not _PERSON_ID_RE.match(person_id):
                raise ValueError(
                    f"person_id {person_id!r} must match ^[a-zA-Z0-9_-]+$ "
                    "(it is used verbatim as the Honcho peer id)"
                )
        return value


def load_persons(path: Path) -> Persons:
    path = Path(path).expanduser()
    if not path.exists():
        raise PersonsError(f"persons file not found: {path}")
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise PersonsError(f"invalid TOML in {path}: {exc}") from exc
    try:
        parsed = _PersonsFile.model_validate(data)
    except ValidationError as exc:
        raise PersonsError(str(exc)) from exc

    by_person: dict[str, PersonRef] = {}
    index: dict[tuple[str, str], str] = {}
    for person_id, entry in parsed.persons.items():
        name = entry.name or person_id
        by_person[person_id] = PersonRef(
            person_id=person_id, name=name, honcho_peer_id=person_id
        )
        for platform, sender_id in entry.platforms.items():
            key = (platform, str(sender_id))
            if key in index:
                raise PersonsError(
                    f"duplicate ({platform}, {sender_id}) mapped to both "
                    f"{index[key]!r} and {person_id!r} — an ambiguous identity would "
                    "silently misattribute Honcho memory"
                )
            index[key] = person_id
    return Persons(by_person=by_person, index=index)
