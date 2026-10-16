"""Unmapped senders, correlated into ACCOUNTS.

THE UNIT IS AN ACCOUNT, NOT AN ID. Under Signal's sealed sender the same
registration reaches Nora as a phone number on one message and as an ACI uuid
on another — `nora.platform.signal.parser` takes `source or sourceNumber or
sourceUuid`, so `messages.sender_id` holds whichever one happened to be
present. Listing those two rows as two pending entries would invite the
operator to paste them into two `[[persons.x.signal]]` blocks, and that is
exactly the copy-paste duplicate `nora.persons.load_persons` refuses to
start on — the tool built to fix the identity file would be
the thing that broke it.

So ids are merged, by union-find, over three kinds of evidence:

  1. **One envelope.** Every id form on a single stored envelope
     (`source`, `sourceNumber`, `sourceUuid`) named the same sender by
     construction.
  2. **A group roster.** `listGroups`' `members[]` pairs `number` with `uuid`
     for every member. This is the strongest link available: few contacts
     carry both forms, while every group member does.
  3. **A contact.** `listContacts` pairs `number`/`uuid`/`username` and is the
     only source of the display name the operator actually recognises.

2 and 3 need the daemon. Without it, correlation degrades to 1 alone and every
name reads `unknown` — which is reported, never hidden, because a pending list
that silently splits one person in two is worse than one that says it could
not check.

Everything here is a pure function over rows and daemon reads. Nothing writes:
the operator adopts by hand-editing identity.toml and restarting, at which
point `nora.app.adoption` backfills the history (DEC-16).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from nora.domain.models import UnattributedRow
from nora.persons import Persons
from nora.platform.signal.directory import SignalContact, SignalGroup
from nora.platform.signal.parser import extract_envelope

UNKNOWN_NAME = "unknown"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class PendingAccount:
    """One account seen in the audit DB that maps to no person."""

    platform: str
    ids: tuple[str, ...]          # every id form seen or linked, sorted
    name: str                     # UNKNOWN_NAME when the daemon does not know it
    profile_names: tuple[str, ...]  # `sourceName`s seen — spoofable, never matched
    messages: int
    first_ts: int
    last_ts: int

    @property
    def known(self) -> bool:
        return self.name != UNKNOWN_NAME


class _Union:
    """Union-find over id strings. Instantiated per call — never at module
    level, which `tests/architecture/test_no_module_level_mutable_singletons.py`
    would catch."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self._parent.setdefault(value, value)
        root = value
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[value] != root:      # path compression
            self._parent[value], value = root, self._parent[value]
        return root

    def link(self, values: frozenset[str] | tuple[str, ...]) -> None:
        it = iter(values)
        first = next(it, None)
        if first is None:
            return
        root = self.find(first)
        for other in it:
            self._parent[self.find(other)] = root


@dataclass
class _Seen:
    """Mutable accumulator for one root, discarded once `PendingAccount` is
    built. Local to `correlate`, so no shared state survives the call."""

    ids: set[str] = field(default_factory=set)
    profile_names: set[str] = field(default_factory=set)
    messages: int = 0
    first_ts: int = 0
    last_ts: int = 0


def envelope_ids(row: UnattributedRow) -> tuple[frozenset[str], str]:
    """Every id form on one audited row, plus its `sourceName`.

    `sender_id` is always included — it is the one id that is certainly the
    same string the ingest path resolved against, and a row whose `raw_json` is
    missing or unparseable must still be listed rather than silently dropped.
    """
    ids = {row.sender_id} if row.sender_id else set()
    profile_name = ""
    try:
        event = json.loads(row.raw_json) if row.raw_json else None
    except (TypeError, ValueError):
        event = None
    env = extract_envelope(event) if isinstance(event, dict) else None
    if isinstance(env, dict):
        ids |= {
            str(env[key])
            for key in ("source", "sourceNumber", "sourceUuid")
            if env.get(key)
        }
        profile_name = str(env.get("sourceName") or "")
    return frozenset(ids), profile_name


def correlate(
    *,
    rows: list[UnattributedRow],
    persons: Persons,
    contacts: list[SignalContact] | None = None,
    groups: list[SignalGroup] | None = None,
) -> list[PendingAccount]:
    """Group unattributed rows into accounts, dropping any that now resolve.

    `persons` is applied AFTER merging, so a phone number the operator has
    already mapped also retires the ACI that arrived on the same account's
    other messages — the pair is one account, and half an account is not
    pending.

    Two passes over the rows, and the second one is not redundant: linking is
    what decides which ids share a root, and a link made while reading row 3
    can merge the roots row 1 and row 2 were filed under. Grouping in the same
    pass that links would leave those two filed apart under stale roots — the
    split-account bug this module exists to prevent, reintroduced by the code
    meant to prevent it.
    """
    contacts, groups = contacts or [], groups or []
    union = _Union()
    for contact in contacts:
        union.link(contact.ids)
    for group in groups:
        for member in group.members:
            union.link(member)

    observed: list[tuple[UnattributedRow, frozenset[str], str]] = []
    for row in rows:
        ids, profile_name = envelope_ids(row)
        if not ids:
            continue
        union.link(ids)
        observed.append((row, ids, profile_name))

    seen: dict[tuple[str, str], _Seen] = {}
    for row, ids, profile_name in observed:
        key = (row.platform, union.find(min(ids)))
        entry = seen.setdefault(key, _Seen(first_ts=row.ts, last_ts=row.ts))
        entry.ids |= ids
        if profile_name:
            entry.profile_names.add(profile_name)
        entry.messages += 1
        entry.first_ts = min(entry.first_ts, row.ts)
        entry.last_ts = max(entry.last_ts, row.ts)

    # A contact or roster record may carry an id form that never arrived on a
    # message. Pasting it in is the whole point: it is the id Nora has not
    # seen YET, and it costs the operator a second adoption round when it does.
    by_root: dict[str, set[str]] = {}
    for linked in [c.ids for c in contacts] + [m for g in groups for m in g.members]:
        for value in linked:
            by_root.setdefault(union.find(value), set()).update(linked)

    accounts: list[PendingAccount] = []
    for (platform, root), entry in seen.items():
        account_ids: set[str] = entry.ids | by_root.get(root, set())
        if any(persons.resolve(platform, value) for value in account_ids):
            continue                      # mapped since — `nora.app.adoption`'s job
        accounts.append(PendingAccount(
            platform=platform,
            ids=tuple(sorted(account_ids)),
            name=_name_for(account_ids, contacts),
            profile_names=tuple(sorted(entry.profile_names)),
            messages=entry.messages,
            first_ts=entry.first_ts,
            last_ts=entry.last_ts,
        ))
    accounts.sort(key=lambda a: (-a.last_ts, a.ids))
    return accounts


def _name_for(ids: set[str], contacts: list[SignalContact]) -> str:
    """The daemon's name for this account, or `UNKNOWN_NAME`.

    Never falls back to the envelope's `sourceName`. That is a profile name the
    SENDER chose and can change at will; printing it here, in
    the column the operator reads as "who this is", would dress an unverified
    string as an identification. It is reported separately and labelled.
    """
    for contact in contacts:
        if contact.ids & ids and contact.name:
            return contact.name
    return UNKNOWN_NAME


def id_kind(platform: str, value: str) -> str | None:
    """Classify one observed id into `platform`'s vocabulary.

    Signal only. Every other platform returns None — the parser produces
    nothing but Signal rows today, and guessing a Telegram `user_id` from a
    bare string would emit a stanza that resolves the wrong person. The caller
    prints such ids for hand-mapping instead.
    """
    if platform != "signal":
        return None
    if value.startswith("+"):
        return "phone"
    if _UUID_RE.match(value):
        return "uuid"
    # NOT `username`: it is not a matchable kind (a handle its owner can rename
    # must never resolve a person), so a stanza
    # naming one would be refused at startup. `None` means "print it for
    # hand-mapping", which is what the caller already does for every platform
    # it cannot infer a kind for.
    return None
