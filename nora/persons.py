"""identity.toml → reverse index (spec C.6, D.2; design/10 §3, §7.3).

Startup-only; no hot reload in v2 (Q4).

Two independent axes, which design/10 §3 originally conflated:

  * many ACCOUNTS per platform — a dual-SIM personal/work pair, a second
    Discord. One `[[persons.<id>.<platform>]]` block each. This is the
    baseline case, not an edge case.
  * many ID FORMS per account — phone / uuid / username all naming the same
    registration. Keys inside one block.

Both flatten into one reverse index, so every id a person presents resolves to
the same `person_id` and therefore to one Honcho peer card (A2). A hidden
Signal phone number still resolves as long as the operator pasted in the ACI
Nora saw.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .domain.identity import ACCOUNT_LABEL_KEY, DISPLAY_NAME_KIND, id_kinds
from .domain.models import NormalizedMessage, PersonRef

# Honcho requires ^[a-zA-Z0-9_-]+$ for peer ids, and honcho_peer_id == person_id.
_PERSON_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# The platform sub-table names `_PersonEntry` accepts, in the order the
# reverse index is built — every platform design/10 §3.1 declares a kind
# vocabulary for.
_PLATFORM_FIELDS: tuple[str, ...] = ("signal", "telegram", "discord", "whatsapp")


def _check_account(platform: str, account: dict[str, str | int]) -> None:
    """Validate one account table's keys against `platform`'s vocabulary.

    Called from `_PersonEntry`'s single `model_validator`, which walks
    `_PLATFORM_FIELDS` — so the platform name comes from the loop variable and
    this never needs pydantic's `ValidationInfo.field_name` (typed
    `str | None`, which would need an `assert` and trip ruff S101).
    """
    allowed = id_kinds(platform)
    matchable = 0
    for kind in account:
        if kind == ACCOUNT_LABEL_KEY:
            continue                      # human-only, never matched (§1.3)
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
        matchable += 1
    if not matchable:
        # An account table holding only a `label` contributes nothing to the
        # reverse index, so it would sit in the file looking like a mapping
        # while resolving no one — silent, and invisible under R-5.
        raise ValueError(
            f"the {platform} account {account.get(ACCOUNT_LABEL_KEY, '')!r} "
            f"lists no matchable id — give it at least one of "
            f"{sorted(allowed)}, or remove the block"
        )


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
    # One dict per ACCOUNT (the baseline case, not an edge case); the keys
    # inside are that account's id KINDS plus the never-matched `label`.
    #
    # The leaf type stays `str | int` ON PURPOSE: it is what makes pydantic
    # reject `phone = ["+852…", "+852…"]` at startup instead of letting
    # `str(list)` reach the reverse index and build a key no sender can ever
    # present — a mute Nora with no error, invisible under R-5. Multiplicity
    # lives at the table level, never at the leaf.
    signal: list[dict[str, str | int]] | None = None
    telegram: list[dict[str, str | int]] | None = None
    discord: list[dict[str, str | int]] | None = None
    whatsapp: list[dict[str, str | int]] | None = None
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

    @model_validator(mode="before")
    @classmethod
    def _reject_single_table_form(cls, data: Any) -> Any:
        """Hard break on the pre-multi-account `[persons.<id>.signal]` form.

        pydantic's own "Input should be a valid list" names neither the new
        shape nor why it changed. `_reject_old_shorthand` set the precedent
        (§7.3) that this file's breaks get a bespoke error; same reasoning —
        identity.toml is gitignored and single-operator, so supporting two
        shapes forever buys nothing.
        """
        if not isinstance(data, dict):
            return data
        for platform in _PLATFORM_FIELDS:
            if isinstance(data.get(platform), dict):
                raise ValueError(
                    f"`[persons.<id>.{platform}]` is now an array of tables, "
                    f"one per ACCOUNT — write `[[persons.<id>.{platform}]]` "
                    "(double brackets). A person may hold several accounts on "
                    "one platform (a dual-SIM personal/work pair, a second "
                    "Discord), and they all resolve to the same person_id and "
                    "share one Honcho peer card (A2). List that account's id "
                    "forms inside the block; see "
                    "design/10-preview-identity.toml."
                )
        return data

    @model_validator(mode="after")
    def _check_accounts(self) -> _PersonEntry:
        """Validate every account on every platform (design/10 §3.1).

        One validator walking `_PLATFORM_FIELDS` replaces the four
        near-identical `field_validator`s this class used to carry, so a fifth
        platform costs one field instead of three edits.
        """
        for platform in _PLATFORM_FIELDS:
            accounts = getattr(self, platform) or ()
            for account in accounts:
                _check_account(platform, account)
            if len(accounts) < 2:
                continue
            # A second, independent tripwire on the silent copy-paste failure
            # in `load_persons`: it makes the operator look at the block he
            # just duplicated.
            labels = [str(a.get(ACCOUNT_LABEL_KEY, "")).strip() for a in accounts]
            if not all(labels):
                raise ValueError(
                    f"{platform} has {len(accounts)} accounts, so each needs a "
                    f'`{ACCOUNT_LABEL_KEY} = "…"` naming it (e.g. "personal", '
                    '"business"). It is never matched on — it is what '
                    "`nora persons pending` prints back to you."
                )
            if len(set(labels)) != len(labels):
                raise ValueError(
                    f"{platform} accounts have duplicate "
                    f"{ACCOUNT_LABEL_KEY}s {sorted(labels)} — give each one a "
                    "distinct name, or you cannot tell them apart in "
                    "`nora persons pending`"
                )
        return self


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
        # Every kind of every account (design/10 §3.2) flattens into one
        # index, which is what makes all of a person's accounts land on a
        # single `honcho_peer_id` (A2).
        for platform in _PLATFORM_FIELDS:
            # (platform, value) → the index of the account block that claimed
            # it, so the two duplicate cases can be told apart below.
            claimed_by: dict[tuple[str, str], int] = {}
            for account_no, account in enumerate(getattr(entry, platform) or ()):
                for kind, id_value in account.items():
                    if kind == ACCOUNT_LABEL_KEY:
                        continue          # human-only, never matched (§1.3)
                    key = (platform, str(id_value))

                    # Same id in two of ONE person's blocks: the
                    # copy-paste-and-forget-to-edit mistake. Silent, because
                    # both blocks map to this person so the cross-person check
                    # never fires, while the id that was never pasted resolves
                    # to nobody — and under sealed sender that is often the
                    # only id Nora sees. Tolerated WITHIN one block, where the
                    # same literal under two kinds is harmless.
                    previous = claimed_by.get(key)
                    if previous is not None and previous != account_no:
                        raise PersonsError(
                            f"{person_id!r} lists ({platform}, {id_value}) in "
                            f"two different account blocks (#{previous + 1} and "
                            f"#{account_no + 1}) — did you copy the block and "
                            "forget to edit this id? The account whose real id "
                            "is missing would silently resolve to nobody."
                        )
                    claimed_by[key] = account_no

                    existing = index.get(key)
                    if existing is not None and existing != person_id:
                        raise PersonsError(
                            f"duplicate ({platform}, {id_value}) mapped to both "
                            f"{existing!r} and {person_id!r} — an ambiguous identity "
                            "would silently misattribute Honcho memory"
                        )
                    index[key] = person_id
    return Persons(by_person=by_person, index=index)
