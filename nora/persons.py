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

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .domain.identity import ACCOUNT_LABEL_KEY, metadata_keys
from .domain.models import NormalizedMessage, PersonRef
from .persons_file import PLATFORM_FIELDS, _PersonsFile


class PersonsError(Exception):
    """A person mapping problem that must stop the process at startup."""


@dataclass(frozen=True)
class Life360Membership:
    """A person's Life360 account as the presence collector needs it: the
    `member_id` (the matchable id) and the `circle_id` it belongs to (metadata,
    optional at parse — a life360 block with no circle_id is valid identity but
    uncollectable, and presence composition refuses to start on one). This is
    what lets circle membership live in identity.toml, not the tracked config."""

    person_id: str
    member_id: str
    circle_id: str | None


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
    # Every person's Life360 membership(s), the source the presence collector
    # derives its circles from (design/19 §3) — so config.toml carries no ids.
    life360: tuple[Life360Membership, ...] = ()

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
    life360: list[Life360Membership] = []
    for person_id, entry in parsed.persons.items():
        # `name` is what Nora calls them out loud. Fall back through the
        # structured parts before the person_id, so a file that gives only
        # first/last still addresses them like a person rather than a key.
        name = entry.name or " ".join(
            p for p in (entry.first_name, entry.last_name) if p
        ) or person_id
        by_person[person_id] = PersonRef(
            person_id=person_id, name=name, honcho_peer_id=person_id,
            first_name=entry.first_name or "", last_name=entry.last_name or "",
            aliases=tuple(entry.aliases),
        )
        # Every kind of every account (design/10 §3.2) flattens into one
        # index, which is what makes all of a person's accounts land on a
        # single `honcho_peer_id` (A2).
        for platform in PLATFORM_FIELDS:
            # (platform, value) → the index of the account block that claimed
            # it, so the two duplicate cases can be told apart below.
            # `tuple[str, ...]`, not `tuple[str, str]`: the life360/member_id branch
            # below widens the key with `circle_id` to a 3-tuple (a member_id
            # legitimately repeats across circles), so the dedup key is variable-length.
            claimed_by: dict[tuple[str, ...], int] = {}
            metadata = metadata_keys(platform)
            for account_no, account in enumerate(getattr(entry, platform) or ()):
                if platform == "life360" and "member_id" in account:
                    # Retain the membership so presence can derive its circles
                    # from identity (design/19 §3). circle_id is optional at
                    # parse; composition refuses to start on a tracked one that
                    # lacks it.
                    circle = account.get("circle_id")
                    life360.append(Life360Membership(
                        person_id=person_id,
                        member_id=str(account["member_id"]),
                        circle_id=str(circle) if circle is not None else None,
                    ))
                for kind, id_value in account.items():
                    if kind == ACCOUNT_LABEL_KEY or kind in metadata:
                        continue          # non-matchable: label / life360 circle_id
                    key = (platform, str(id_value))

                    # Same id in two of ONE person's blocks: the
                    # copy-paste-and-forget-to-edit mistake. Silent, because
                    # both blocks map to this person so the cross-person check
                    # never fires, while the id that was never pasted resolves
                    # to nobody — and under sealed sender that is often the
                    # only id Nora sees. Tolerated WITHIN one block, where the
                    # same literal under two kinds is harmless.
                    #
                    # life360's `member_id` is the one legitimate exception
                    # (design/19 §3): it names an ACCOUNT, not a circle, so
                    # ONE account genuinely repeats it across several
                    # `[[...life360]]` blocks — one per circle it belongs to.
                    # The dedup key for JUST this platform/kind also carries
                    # `circle_id`, so two blocks for different circles never
                    # collide, while two blocks for the SAME circle (a real
                    # copy-paste) still do.
                    dedup_key: tuple[str, ...] = key
                    if platform == "life360" and kind == "member_id":
                        circle = account.get("circle_id")
                        dedup_key = (*key, str(circle) if circle is not None else "")

                    previous = claimed_by.get(dedup_key)
                    if previous is not None and previous != account_no:
                        raise PersonsError(
                            f"{person_id!r} lists ({platform}, {id_value}) in "
                            f"two different account blocks (#{previous + 1} and "
                            f"#{account_no + 1}) — did you copy the block and "
                            "forget to edit this id? The account whose real id "
                            "is missing would silently resolve to nobody."
                        )
                    claimed_by[dedup_key] = account_no

                    existing = index.get(key)
                    if existing is not None and existing != person_id:
                        raise PersonsError(
                            f"duplicate ({platform}, {id_value}) mapped to both "
                            f"{existing!r} and {person_id!r} — an ambiguous identity "
                            "would silently misattribute Honcho memory"
                        )
                    index[key] = person_id
    return Persons(by_person=by_person, index=index, life360=tuple(life360))
