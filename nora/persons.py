"""identity.toml → reverse index (spec C.6, D.2; design/10 §3, §7.3).

Startup-only; no hot reload in v2 (Q4). One person may hold many ids per
platform (design/10-preview-identity.toml) — a hidden Signal phone number
still resolves as long as the operator has pasted in the ACI Nora saw.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .domain.identity import DISPLAY_NAME_KIND, id_kinds
from .domain.models import NormalizedMessage, PersonRef

# Honcho requires ^[a-zA-Z0-9_-]+$ for peer ids, and honcho_peer_id == person_id.
_PERSON_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# The platform sub-table names `_PersonEntry` accepts, in the order the
# reverse index is built — every platform design/10 §3.1 declares a kind
# vocabulary for.
_PLATFORM_FIELDS: tuple[str, ...] = ("signal", "telegram", "discord", "whatsapp")


def _check_id_kinds(
    platform: str, value: dict[str, str | int] | None
) -> dict[str, str | int] | None:
    """Shared body for each platform's `field_validator` below — kept as a
    plain function (rather than one validator bound to all four fields) so
    it never needs pydantic's `ValidationInfo.field_name`, which is typed
    `str | None` and would need an `assert` (ruff S101) to narrow back to the
    `str` `nora.domain.identity.id_kinds` expects."""
    if value is None:
        return value
    allowed = id_kinds(platform)
    for kind in value:
        if kind == DISPLAY_NAME_KIND:
            raise ValueError(
                f"{platform}.{DISPLAY_NAME_KIND} is never matchable "
                "(design/10 §1.3) — display names are user-controlled and "
                "spoofable; they are shown to the operator, never used to "
                "resolve a sender to a person"
            )
        if kind not in allowed:
            raise ValueError(
                f"{platform}.{kind!r} is not a valid id kind (design/10 "
                f"§3.1); expected one of {sorted(allowed)}"
            )
    return value


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
    index: dict[tuple[str, str], str] = field(repr=False)   # (platform, id_value) → person_id

    def resolve(self, platform: str, sender_id: str) -> PersonRef | None:
        person_id = self.index.get((platform, sender_id))
        return self.by_person[person_id] if person_id else None

    def display_name(self, msg: NormalizedMessage) -> str:
        ref = self.resolve(msg.platform, msg.sender_id)
        if ref:
            return ref.name
        return msg.sender_name or sanitize_unmapped(msg.sender_id)


class _PersonEntry(BaseModel):
    """One `[persons.<id>]` table. Each platform sub-table's keys are
    validated against that platform's fixed id-kind vocabulary
    (`nora.domain.identity.id_kinds`, design/10 §3.1) — a typo, or
    `display_name`, is a startup error, not a silently-ignored key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    signal: dict[str, str | int] | None = None
    telegram: dict[str, str | int] | None = None
    discord: dict[str, str | int] | None = None
    whatsapp: dict[str, str | int] | None = None
    # Accepted here ONLY so the retired `[persons.<id>.platforms]` shorthand
    # gets a specific, readable error naming its replacement (design/10 §7.3
    # — a hard break, deliberately: identity.toml is gitignored and
    # single-operator, so there is no installed base to support two shapes
    # for) instead of pydantic's generic "extra fields not permitted".
    platforms: dict[str, str | int] | None = None

    @field_validator("platforms")
    @classmethod
    def _reject_old_shorthand(cls, value: dict[str, str | int] | None) -> None:
        if value is not None:
            raise ValueError(
                "the old `[persons.<id>.platforms]` shorthand (one id per "
                "platform) is no longer supported (design/10 §7.3) — use one "
                "typed table per platform instead, e.g. "
                '`[persons.<id>.signal]` with `phone = "…"` / `uuid = "…"` / '
                "`username = \"…\"` keys; see design/10-preview-identity.toml "
                "for the target shape."
            )
        return None

    @field_validator("signal")
    @classmethod
    def _check_signal_kinds(
        cls, value: dict[str, str | int] | None
    ) -> dict[str, str | int] | None:
        return _check_id_kinds("signal", value)

    @field_validator("telegram")
    @classmethod
    def _check_telegram_kinds(
        cls, value: dict[str, str | int] | None
    ) -> dict[str, str | int] | None:
        return _check_id_kinds("telegram", value)

    @field_validator("discord")
    @classmethod
    def _check_discord_kinds(
        cls, value: dict[str, str | int] | None
    ) -> dict[str, str | int] | None:
        return _check_id_kinds("discord", value)

    @field_validator("whatsapp")
    @classmethod
    def _check_whatsapp_kinds(
        cls, value: dict[str, str | int] | None
    ) -> dict[str, str | int] | None:
        return _check_id_kinds("whatsapp", value)


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
        # Every kind, on every platform (design/10 §3.2): any listed id
        # resolves to the person, so all of them go into the same reverse
        # index regardless of which kind produced the value.
        for platform in _PLATFORM_FIELDS:
            ids = getattr(entry, platform) or {}
            for id_value in ids.values():
                key = (platform, str(id_value))
                existing = index.get(key)
                if existing is not None and existing != person_id:
                    raise PersonsError(
                        f"duplicate ({platform}, {id_value}) mapped to both "
                        f"{existing!r} and {person_id!r} — an ambiguous identity "
                        "would silently misattribute Honcho memory"
                    )
                index[key] = person_id
    return Persons(by_person=by_person, index=index)
