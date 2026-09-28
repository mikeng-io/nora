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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import ValidationError

from .domain.identity import ACCOUNT_LABEL_KEY, metadata_keys
from .domain.models import NormalizedMessage, PersonRef, TranscriptRow
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
    # former_person_id -> current person_id, built from every
    # `former_person_ids` entry across identity.toml (design/27 §1.2). A
    # person_id absent from this dict resolves to itself. Defaulted (not a
    # bare required field) so every existing direct `Persons(...)` fixture in
    # the test suite that predates this task keeps constructing without it.
    _subject_aliases: dict[str, str] = field(default_factory=dict)
    # fbid -> the Instagram `username` metadata recorded beside it, ONE
    # account's pairing at a time (design/instagram-adoption/02; `username`
    # has been an accepted, never-matchable `instagram` metadata key since
    # that design landed — `nora.domain.identity.metadata_keys("instagram")`
    # — this is the first reader of it). Keyed on the fbid, not the
    # person_id, because a person may hold more than one Instagram account
    # with different handles (a `label`-disambiguated personal/business
    # pair); `nora.agent.outbound._mentions` already knows exactly which
    # fbid THIS room's own roster resolved to, so `instagram_handle` reads
    # off that SAME account rather than guessing among a person's several.
    _instagram_handles: dict[str, str] = field(default_factory=dict)
    # person_id -> every `igid` their Instagram blocks declare, KIND-aware
    # (design/instagram-adoption/17 P2). `index` cannot answer this: it is
    # `(platform, value) -> person_id` and drops the id kind, so
    # `platform_ids("instagram", p)` returns the fbid and the igid mixed —
    # both decimal strings, and handing an fbid to a route that wants an
    # igid names a different account or none, with no error anywhere
    # (design/instagram-adoption/02).
    _instagram_igids: dict[str, frozenset[str]] = field(default_factory=dict)
    # Nora workspace -> the Honcho workspace id it is bound to, from
    # `[workspaces.<name>] honcho_workspace = "..."` (DEC-214). Only the
    # bindings the operator WROTE: resolving an unbound workspace to its own
    # name, and the implicit workspace to `memory.workspace_id`, is
    # `nora.app.memory_routing`'s job, because it needs the policy and the
    # config as well as this file.
    honcho_workspaces: dict[str, str] = field(default_factory=dict)

    def subject_id_for(self, person_id: str) -> str:
        """The stable id to derive analytics under. Resolves a RETIRED
        person_id to whoever currently claims it; an id this dict does not
        mention (including a live, never-renamed person_id, or an id this
        identity.toml has never heard of at all) resolves to itself."""
        return self._subject_aliases.get(person_id, person_id)

    def former_ids_for(self, person_id: str) -> tuple[str, ...]:
        """The reverse of `subject_id_for`: every RETIRED id whose
        `former_person_ids` entry currently resolves to `person_id`.

        `_subject_aliases` only maps former → current (built once by
        `load_persons` from each block's `former_person_ids`), and
        `life360` memberships — by `load_persons`' own fail-closed
        construction (a `former_person_ids` entry colliding with a live
        `[persons.<id>]` block raises `PersonsError`) — can therefore only
        ever carry LIVE ids. Any caller that needs every id a subject has
        ever been observed under (`nora.analytics.composition
        ._subjects_from_identity`, folding a renamed person's historical
        fixes under their former id into the SAME subject) needs this
        reverse lookup; forward-only `subject_id_for` is a no-op over
        `life360`'s own ids and can never surface a former id on its own.

        Built by scanning `_subject_aliases` on each call rather than a
        second cached index: the dict is small (one entry per rename in the
        whole deployment) and this avoids a second copy of the same fact
        that could drift from `_subject_aliases` after a future edit —
        exactly `platform_ids`' own reasoning for computing fresh each call.
        Order is insertion order over `_subject_aliases` (stable, but not
        meaningful — callers should not rely on a particular ordering
        beyond stability)."""
        return tuple(
            former for former, current in self._subject_aliases.items()
            if current == person_id
        )

    def resolve(self, platform: str, sender_id: str) -> PersonRef | None:
        person_id = self.index.get((platform, sender_id))
        return self.by_person[person_id] if person_id else None

    def platform_ids(self, platform: str, person_id: str) -> frozenset[str]:
        """Every id form this person presents on `platform` — the reverse of
        `resolve`, read off the same one index so the two can never disagree.

        Needed by exactly one caller, and only for an OUTBOUND decision:
        `nora.agent.outbound` has a person and needs an id signal-cli will
        accept as a mention recipient, which is the opposite direction from
        every other consumer of this class. Derived from `index` on each call
        rather than kept as a second dict: `index` is built once at startup and
        is small (one entry per id form per account), the call happens at most
        a couple of times per turn, and a cached inverse is a second copy of
        the same fact that a future `load_persons` edit could leave stale — the
        failure this class's own duplicate-detection exists to prevent.

        Returns a SET, not an ordered pick, because choosing WHICH form to put
        on the wire is not this class's decision: `nora.agent.outbound`
        intersects this with the platform's own membership list first, so the
        id it sends is one the PLATFORM just asserted, not merely one
        `identity.toml` claims. An unknown `person_id` or platform is an empty
        set — never a `KeyError`, since a caller in the middle of a live turn
        must not raise over a name it could not place.
        """
        return frozenset(
            value for (plat, value), pid in self.index.items()
            if plat == platform and pid == person_id
        )

    def instagram_handle(self, fbid: str) -> str | None:
        """The Instagram `username` the operator recorded beside `fbid`, or
        `None` — the literal wire text (`nora.platform.instagram.send`)
        Instagram's own mention API requires at a mention's span to render it
        as a chip, and never renders (confirmed against upstream mautrix-meta,
        `igconv/from-matrix.go` + `textfmt/from-matrix.go`: it builds the
        request as `"@" + mention.Name` over the recipient's OWN username, not
        the sender's display name for them) if that span is the display name
        Nora's prose names them by, which is all `identity.toml` guarantees on
        its own.

        `None` — no `username` on file for this exact fbid, or an fbid this
        file never heard of — is the ordinary, EXPECTED answer until an
        operator opts a person in; the caller degrades to today's behaviour
        (the mention metadata still carries the fbid, the rendered text stays
        whatever Nora wrote), never an error.
        """
        return self._instagram_handles.get(fbid)

    def instagram_igids(self, person_id: str) -> frozenset[str]:
        """Every Instagram `igid` (the public/social id) recorded for
        `person_id` — never an `fbid`, which `platform_ids` would mix in.

        A SET, and deliberately not a pick: a person may hold several
        Instagram accounts, and choosing among them is not this class's
        decision (the social subject index answers `ambiguous` for more than
        one). An unknown person, or one with no `igid` on file, is an empty
        set — never a `KeyError`, for `platform_ids`' reason.
        """
        return self._instagram_igids.get(person_id, frozenset())

    def display_name(self, msg: NormalizedMessage) -> str:
        ref = self.resolve(msg.platform, msg.sender_id)
        if ref:
            return ref.name
        return msg.sender_name or sanitize_unmapped(msg.sender_id)

    def name_transcript(
        self, transcript: Sequence[TranscriptRow], *, platform: str
    ) -> tuple[TranscriptRow, ...]:
        """Fill each row's `person_name` from this index (DEC-80).

        `display_name` for a whole transcript, and it lives here for the same
        reason that one does: resolving an account to the name Nora calls it by
        is identity's job, not a renderer's. Both context assemblers
        (`nora.engagement.context` for a tag, `nora.app.fire` for a reminder)
        call it, so the projection cannot drift between the two paths.

        The prefill used to label transcript speakers by their SELF-SET platform
        profile name while `tag_message from=`, `group_members` and
        `lookup_person` all used the canonical one — two name spaces for the same
        people in one prompt. Live consequence (Langfuse `05a48150…`,
        2026-08-10): asked where 「Grant」 was, Nora answered 「呢個 group 冇人叫
        呢個名」 — the roster said `Grant Carver`, the transcript said
        `陳志明 Chi Ming` — then told a DIFFERENT member that he was Grant.

        Keyed on `(platform, sender_id)`, never on the display name: that is the
        `PersonRef.aliases` rule exactly — operator-authored names are for
        comprehension, platform ids are for resolution.

        Nora's own rows are left alone. `_label` renders her from the configured
        `display_name`, and she is deliberately absent from identity.toml, so
        resolving her would only ever produce None.
        """
        return tuple(
            row if row.is_nora or (ref := self.resolve(platform, row.sender_id)) is None
            else replace(row, person_name=ref.name)
            for row in transcript
        )

    def room_members(self, listed: frozenset[str] | None) -> tuple[PersonRef, ...]:
        """The persons a room LISTS (`policy.toml` `persons`) that this file
        maps, by name, for `<room_members>`. Never the whole file: a roster
        fed the address book listed people who had never been in the room
        (design/12 §3). `None` (a `persons = "*"` room) is empty, because
        who is actually there would need a live roster read."""
        if listed is None:
            return ()
        return tuple(sorted(
            (self.by_person[pid] for pid in listed if pid in self.by_person),
            # The id breaks a tie between two people of one name: a frozenset
            # walks in hash order, which changes per process, and the block is
            # a cached, snapshot-compared prefix (review 2026-09-28).
            key=lambda ref: (ref.name, ref.person_id),
        ))


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
    instagram_handles: dict[str, str] = {}
    instagram_igids: dict[str, set[str]] = {}
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
                if (
                    platform == "instagram"
                    and "fbid" in account
                    and "username" in account
                ):
                    # Paired within THIS block, deliberately — `username` is
                    # metadata (never matched on, §3.1), but it names a handle
                    # for ONE account, and a person may hold several with
                    # different ones. Reading it off the same `account` dict
                    # the `fbid` came from is what keeps the pairing correct
                    # for a personal/business pair; a person-level dict could
                    # not tell the two apart. `fbid` is already the unique,
                    # duplicate-checked index key below, so this needs no
                    # collision check of its own.
                    instagram_handles[str(account["fbid"])] = str(account["username"])
                if platform == "instagram" and "igid" in account:
                    # By KIND, read off the block, because the index below
                    # keeps only the value. Uniqueness is the index's own
                    # duplicate check (an igid under two people refuses to
                    # load), so this needs none of its own.
                    instagram_igids.setdefault(person_id, set()).add(str(account["igid"]))
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

    subject_aliases: dict[str, str] = {}
    for person_id, entry in parsed.persons.items():
        for former in entry.former_person_ids:
            if former in parsed.persons:
                raise PersonsError(
                    f"former_person_ids entry {former!r} on "
                    f"[persons.{person_id}] collides with a LIVE "
                    f"[persons.{former}] block — a former id must not also "
                    "be a live person_id; remove one or the other"
                )
            if former in subject_aliases:
                raise PersonsError(
                    f"former_person_ids entry {former!r} is claimed by both "
                    f"[persons.{subject_aliases[former]}] and "
                    f"[persons.{person_id}] — an ambiguous merge is refused "
                    "rather than silently resolved to one of them"
                )
            subject_aliases[former] = person_id

    return Persons(
        by_person=by_person, index=index, life360=tuple(life360),
        _subject_aliases=subject_aliases, _instagram_handles=instagram_handles,
        _instagram_igids={pid: frozenset(ids) for pid, ids in instagram_igids.items()},
        honcho_workspaces={
            name: binding.honcho_workspace
            for name, binding in parsed.workspaces.items()
        },
    )
