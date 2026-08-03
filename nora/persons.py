"""persons.yaml → reverse index (spec C.6, D.2). Startup-only; no hot reload in v2 (Q4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import NormalizedMessage, PersonRef

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


def load_persons(path: Path) -> Persons:
    path = Path(path).expanduser()
    if not path.exists():
        raise PersonsError(f"persons file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    raw = data.get("persons") or {}

    by_person: dict[str, PersonRef] = {}
    index: dict[tuple[str, str], str] = {}
    for person_id, entry in raw.items():
        if not _PERSON_ID_RE.match(str(person_id)):
            raise PersonsError(
                f"person_id {person_id!r} must match ^[a-zA-Z0-9_-]+$ "
                "(it is used verbatim as the Honcho peer id)"
            )
        name = (entry or {}).get("name") or person_id
        by_person[person_id] = PersonRef(
            person_id=person_id, name=name, honcho_peer_id=person_id
        )
        for platform, sender_id in ((entry or {}).get("platforms") or {}).items():
            key = (platform, str(sender_id))
            if key in index:
                raise PersonsError(
                    f"duplicate ({platform}, {sender_id}) mapped to both "
                    f"{index[key]!r} and {person_id!r} — an ambiguous identity would "
                    "silently misattribute Honcho memory"
                )
            index[key] = person_id
    return Persons(by_person=by_person, index=index)
