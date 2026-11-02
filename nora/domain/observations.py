"""Typed platform observations: the shared header, payload union and fold.

Design/platform-observations 02 made concrete. An **observation** is what a
source reported about an OBJECT (a reaction's target message, a poll, a room
or thread, a member set, a pin set, a shared artifact, a message's own
metadata) — created, revised, withdrawn, added/removed, cleared or observed
as a snapshot. It is never a `NormalizedMessage`: no fabricated utterance,
no invented speaker. A poll creation that arrives AS a message rides the
envelope's message; the observation is its typed sidecar.

Identity, per 02:

- EVENT identity (`event_id`/`source_digest`, bridge-owned) distinguishes
  transitions — add/remove/add is three events, never one.
- OBJECT identity (`scope` + `object_kind` + `object_ref`) is stable across
  revisions; it is what the current-state fold keys on.
- A `(target, actor, emoji)` triple is a STATE key, not event identity.
- `source_revision`/`source_position` exist only where the upstream proves
  an order, inside `revision_scope`; absent means unordered, never a guessed
  position. Arrival order (`id`) is retained separately and is the honest
  fallback, labelled `arrival`.

Every payload is a typed frozen dataclass validated against its operation —
a discriminated union, not a generic JSON/EAV bag.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from nora.domain.external_files import render_file_links
from nora.domain.models import (
    REACTION_PAIRS_MAX,
    EditTarget,
    ReactionRow,
    RowReactions,
    display_reactions,
    emoji_display,
)

#: The observation payload contract version the wire `contract` field carries.
#: Bumped only when a payload shape changes incompatibly; an unrecognized
#: contract is a retained failure, never a best-effort parse.
OBSERVATION_CONTRACT = 1

ObjectKind = Literal[
    "reaction", "message_metadata", "poll", "room", "membership", "pin",
    "artifact",
]
OBJECT_KINDS: tuple[str, ...] = (
    "reaction", "message_metadata", "poll", "room", "membership", "pin",
    "artifact",
)

Operation = Literal[
    "create", "revise", "withdraw", "add", "remove", "clear", "snapshot",
]
OPERATIONS: tuple[str, ...] = (
    "create", "revise", "withdraw", "add", "remove", "clear", "snapshot",
)

#: Who acted, per the source — distinct from the SUBJECT an observation is
#: about (a moderator removes a member: actor known-or-not, subject the member).
ActorStatus = Literal["known", "anonymous", "not_supplied", "not_applicable"]
ACTOR_STATUSES: tuple[str, ...] = (
    "known", "anonymous", "not_supplied", "not_applicable",
)

#: Kinds a platform's PINNED bridge cannot supply at all — the coverage
#: matrix's explicit "unavailable" cells (design/platform-observations 01:
#: Instagram polls at mautrix-meta v0.2609.0, where a poll read must answer
#: `unavailable`, never a confident `nothing_found`). A kind absent from a
#: platform's set is NOT claimed supported — it is simply not known to be
#: unsupported. Read tools consult this; it is a fact of the frozen matrix,
#: never a capability service.
UNSUPPORTED_KINDS: dict[str, frozenset[str]] = {
    "instagram": frozenset({"poll"}),
}

#: Where `occurred_ms` came from. `unknown`: the source gave no time and the
#: observation carries no fabricated one.
TimeQuality = Literal["upstream", "bridge_local", "unknown"]

#: The legal operations per object kind — the discriminated half of the
#: union's validation. An op outside this table fails closed at the boundary.
KIND_OPERATIONS: dict[str, frozenset[str]] = {
    # add/remove are per-actor deltas; clear empties a covered emoji/actor
    # scope; snapshot carries counts/known reactors, never fabricated history.
    "reaction": frozenset({"add", "remove", "clear", "snapshot"}),
    # A message's own metadata (forward origin, expiry, view-once, shared
    # refs). create on first sight, revise on a changed report, withdraw when
    # the source retracts it.
    "message_metadata": frozenset({"create", "revise", "withdraw"}),
    # create/revise/withdraw carry definition fields; add/remove carry one
    # voter's option delta; revise+voter_ref replaces a voter's whole
    # selection; snapshot carries aggregate counts or a covered voter set;
    # clear empties recorded votes in covered scope.
    "poll": frozenset(
        {"create", "revise", "withdraw", "add", "remove", "clear", "snapshot"}
    ),
    # Changed typed fields (title/topic/permissions/thread lifecycle); a
    # snapshot replaces only its covered field set.
    "room": frozenset({"create", "revise", "withdraw", "snapshot"}),
    # add/remove/revise are subject deltas; snapshot is a member-set read in
    # the scope the source declares (`covered`, e.g. "channel" vs "guild").
    "membership": frozenset({"add", "remove", "revise", "snapshot"}),
    # add/remove pin or unpin a target; snapshot reads the room's pin set.
    "pin": frozenset({"add", "remove", "snapshot"}),
    # A shared post/artifact reference: custody and metadata, never the bytes.
    "artifact": frozenset({"create", "revise", "withdraw"}),
}


@dataclass(frozen=True)
class SourceScope:
    """The private platform/account/room the observation belongs to.

    `thread` is part of the ref where the source scopes an event inside a
    thread (`ConversationTarget.thread`'s rule: a thread is a span inside its
    room, never a room of its own).
    """

    platform: str
    account_key: str
    conversation_id: str
    thread: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.platform, self.account_key, self.conversation_id, self.thread,
        )


@dataclass(frozen=True)
class Coverage:
    """What the observation's own claim covers — never an inferred whole.

    `complete` marks a covered set/window the source reports fully;
    `covered` names that set ("members", "option_counts", "pin_set",
    "fields"); `gaps` names the fields the source did not supply; `reason`
    is the gap's cause ("" when none). An unobserved field is ABSENT, not
    a zero.
    """

    complete: bool = True
    covered: str = "event"
    gaps: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"complete": self.complete, "covered": self.covered,
                "gaps": list(self.gaps), "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Coverage:
        return cls(complete=bool(d.get("complete", True)),
                   covered=str(d.get("covered") or "event"),
                   gaps=tuple(str(g) for g in d.get("gaps") or ()),
                   reason=str(d.get("reason") or ""))


# ── Payload union ─────────────────────────────────────────────────────────
# Each variant validates its operation's required fields in `validate`; the
# shared header rules live in `Observation.__post_init__`.


class ObservationPayload:
    """The union's base: `validate` enforces the op's field shape,
    `to_dict`/`from_dict` the wire form."""

    kind: str = ""

    def validate(self, operation: Operation) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObservationPayload:
        raise NotImplementedError


def _pairs(d: dict[str, int] | None) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((str(k), int(v)) for k, v in (d or {}).items()))


def _pairs_to_dict(
    pairs: tuple[tuple[str, int], ...] | dict[str, int],
) -> dict[str, int]:
    return dict(pairs)


def _target_dict(t: EditTarget | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {"message_id": t.message_id, "sent_ts": t.sent_ts,
            "mentions_known": t.mentions_known}


def _target_of(d: dict[str, Any] | None) -> EditTarget | None:
    if not d:
        return None
    return EditTarget(message_id=str(d.get("message_id") or ""),
                      sent_ts=d.get("sent_ts"),
                      mentions_known=bool(d.get("mentions_known", True)))


def _target_key(t: EditTarget) -> str:
    """The canonical stored form of a typed target — the same two schemes
    `EditTarget` names, as one string for indexes and unresolved re-linking."""
    if t.message_id:
        return f"id:{t.message_id}"
    return f"ts:{t.sent_ts}"


def target_key(target: EditTarget | None, *, author_id: str = "") -> str | None:
    if target is None:
        return None
    key = _target_key(target)
    return f"{key}:author:{author_id}" if not target.message_id and author_id else key


def target_from_key(key: str | None) -> EditTarget | None:
    """`target_key`'s inverse — the same two schemes back to an
    `EditTarget`. Anything else is malformed stored data, not a guess."""
    if not key:
        return None
    scheme, _, rest = key.partition(":")
    if scheme == "id" and rest:
        return EditTarget(message_id=rest)
    if scheme == "ts" and rest:
        return EditTarget(sent_ts=int(rest.partition(":author:")[0]))
    raise ValueError(f"malformed target_key {key!r}")


@dataclass(frozen=True)
class ReactionPayload(ObservationPayload):
    """Target, emoji and actor live in the header (object_ref is the target
    message's handle; `target` names the message it reacts to; `actor_id`
    the reactor — an actor_status that is not `known` records no identity).

    add/remove: `emoji` required. clear: the covered emoji/actor scopes —
    empty scope means "all observed", per the source's claim. snapshot:
    `counts` (emoji -> n) and `reactors` (known actor ids; None = the source
    did not supply identities — an aggregate never invents them).
    """

    emoji: str = ""
    covered_emojis: tuple[str, ...] = ()
    covered_actors: tuple[str, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()
    reactors: tuple[str, ...] | None = None
    kind: str = field(default="reaction", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", _pairs(dict(self.counts)))
        if self.reactors is not None:
            object.__setattr__(self, "reactors", tuple(self.reactors))

    def validate(self, operation: Operation) -> None:
        if operation in ("add", "remove") and not self.emoji:
            raise ValueError("reaction delta requires emoji")
        if operation == "snapshot" and not (
            self.counts or self.reactors is not None
        ):
            raise ValueError("reaction snapshot requires counts or reactors")
        if operation in ("add", "remove") and (
            self.counts or self.reactors is not None
        ):
            raise ValueError("reaction delta cannot carry snapshot fields")

    def to_dict(self) -> dict[str, Any]:
        return {"emoji": self.emoji,
                "covered_emojis": list(self.covered_emojis),
                "covered_actors": list(self.covered_actors),
                "counts": _pairs_to_dict(self.counts),
                "reactors": (None if self.reactors is None
                             else list(self.reactors))}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReactionPayload:
        reactors = d.get("reactors")
        return cls(emoji=str(d.get("emoji") or ""),
                   covered_emojis=tuple(d.get("covered_emojis") or ()),
                   covered_actors=tuple(d.get("covered_actors") or ()),
                   counts=_pairs(d.get("counts")),
                   reactors=(None if reactors is None else tuple(reactors)))


@dataclass(frozen=True)
class MessageMetadataPayload(ObservationPayload):
    """The message's own source constraints: forward origin, expiry and
    view-once flags as reported, shared-object refs. Flags are source
    constraints; they authorize no durable media capture beyond policy."""

    forward_origin: str | None = None
    expires_in_ms: int | None = None
    view_once: bool | None = None
    shared_refs: tuple[str, ...] = ()
    kind: str = field(default="message_metadata", init=False)

    def validate(self, operation: Operation) -> None:
        if operation == "create" and not (
            self.forward_origin is not None or self.expires_in_ms is not None
            or self.view_once is not None or self.shared_refs
        ):
            raise ValueError("metadata create must carry at least one field")

    def to_dict(self) -> dict[str, Any]:
        return {"forward_origin": self.forward_origin,
                "expires_in_ms": self.expires_in_ms,
                "view_once": self.view_once,
                "shared_refs": list(self.shared_refs)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MessageMetadataPayload:
        return cls(forward_origin=d.get("forward_origin"),
                   expires_in_ms=d.get("expires_in_ms"),
                   view_once=d.get("view_once"),
                   shared_refs=tuple(d.get("shared_refs") or ()))


@dataclass(frozen=True)
class PollPayload(ObservationPayload):
    """One poll object's definition AND response observations.

    Definition ops (create/revise/withdraw): `question`, `options`
    ((ref, label) — refs are stable, a changed label is the same option,
    never a positional re-key), `multi_select`, `anonymous`, `state`
    (open/closed/withdrawn) and `close_ms`. `parent_ref` is the poll's parent
    message handle.

    Response ops: add/remove carry `voter_ref` + `option_ref` for a single
    option delta; revise with `voter_ref` + `selected` replaces that voter's
    COMPLETE selection — an empty tuple is a withdrawn vote, None is not a
    legal voter revise. `snapshot` carries `option_counts` and/or
    `voter_count` plus a `voters` map ({voter: [option refs]}) when the
    source supplies identities. Anonymous aggregates and option counts NEVER
    synthesize voter identities, and a multi-select total is not a
    unique-person count. `clear` empties the recorded vote set in covered
    scope.
    """

    question: str | None = None
    options: tuple[tuple[str, str], ...] = ()
    multi_select: bool | None = None
    anonymous: bool | None = None
    state: str = ""  # "" | "open" | "closed" | "withdrawn"
    close_ms: int | None = None
    parent_ref: str = ""
    voter_ref: str = ""
    option_ref: str = ""
    selected: tuple[str, ...] | None = None
    option_counts: tuple[tuple[str, int], ...] = ()
    voter_count: int | None = None
    voters: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    kind: str = field(default="poll", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "option_counts",
                         _pairs(dict(self.option_counts)))
        if self.selected is not None:
            object.__setattr__(self, "selected", tuple(dict.fromkeys(self.selected)))
        if self.voters is not None:
            object.__setattr__(
                self, "voters",
                tuple((str(v), tuple(dict.fromkeys(o))) for v, o in dict(self.voters).items()))

    def validate(self, operation: Operation) -> None:
        if operation == "create" and not self.options:
            raise ValueError("poll create requires options")
        if operation in ("add", "remove") and not (
            self.voter_ref and self.option_ref
        ):
            raise ValueError("poll delta requires voter_ref and option_ref")
        if operation == "revise" and self.voter_ref and self.selected is None:
            raise ValueError("voter revise requires selected ([] = withdrawn)")
        if operation == "revise" and self.voter_ref and (
            self.question is not None or self.options or self.state
        ):
            raise ValueError("voter revise cannot carry definition fields")
        if operation == "snapshot" and not (
            self.option_counts or self.voter_count is not None
            or self.voters is not None
        ):
            raise ValueError("poll snapshot requires counts or a voter set")
        if self.state and self.state not in ("open", "closed", "withdrawn"):
            raise ValueError(f"unknown poll state {self.state!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "options": [{"ref": r, "label": lbl} for r, lbl in self.options],
            "multi_select": self.multi_select, "anonymous": self.anonymous,
            "state": self.state, "close_ms": self.close_ms,
            "parent_ref": self.parent_ref,
            "voter_ref": self.voter_ref, "option_ref": self.option_ref,
            "selected": (None if self.selected is None
                         else list(self.selected)),
            "option_counts": _pairs_to_dict(self.option_counts),
            "voter_count": self.voter_count,
            "voters": (None if self.voters is None
                       else {v: list(o) for v, o in self.voters}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PollPayload:
        voters = d.get("voters")
        selected = d.get("selected")
        return cls(
            question=d.get("question"),
            options=tuple((str(o["ref"]), str(o.get("label") or ""))
                          for o in d.get("options") or ()),
            multi_select=d.get("multi_select"), anonymous=d.get("anonymous"),
            state=str(d.get("state") or ""), close_ms=d.get("close_ms"),
            parent_ref=str(d.get("parent_ref") or ""),
            voter_ref=str(d.get("voter_ref") or ""),
            option_ref=str(d.get("option_ref") or ""),
            selected=None if selected is None else tuple(selected),
            option_counts=_pairs(d.get("option_counts")),
            voter_count=d.get("voter_count"),
            voters=(None if voters is None else tuple(
                (str(v), tuple(o for o in opts))
                for v, opts in voters.items())),
        )


@dataclass(frozen=True)
class RoomPayload(ObservationPayload):
    """Changed typed room/thread fields. `fields` carries only what the source
    reported changed — an absent field is unchanged; a field explicitly nulled
    is cleared. A snapshot replaces its covered field set only."""

    fields: dict[str, Any] = field(default_factory=dict)
    kind: str = field(default="room", init=False)

    def validate(self, operation: Operation) -> None:
        if operation in ("create", "revise", "snapshot") and not self.fields:
            raise ValueError(f"room {operation} requires fields")

    def to_dict(self) -> dict[str, Any]:
        return {"fields": dict(self.fields)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RoomPayload:
        return cls(fields=dict(d.get("fields") or {}))


@dataclass(frozen=True)
class MembershipEntry:
    subject: str
    role: str = ""
    state: str = "member"  # member | left | invited | banned | unknown

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "role": self.role,
                "state": self.state}


@dataclass(frozen=True)
class MembershipPayload(ObservationPayload):
    """Member-set observations. The OBJECT is the member collection of the
    scope `object_ref` names (e.g. `channel:C1` vs `guild:G1` — a guild set
    is a different object than a channel's audience and can never overwrite
    it). To snapshot a subset, the source names the subset its own
    collection.

    add/remove/revise carry the header's `subject_ref` plus `role`/`state`
    here. snapshot carries `members` and `covered` — the scope the source
    declares; a complete snapshot replaces the collection's member set, a
    partial one merges and never removes omitted accounts. Actor and cause
    stay in the header; a removal establishes no voluntary departure.
    """

    role: str = ""
    state: str = ""
    members: tuple[MembershipEntry, ...] = ()
    covered: str = ""
    kind: str = field(default="membership", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "members",
            tuple(m if isinstance(m, MembershipEntry)
                  else MembershipEntry(**m) for m in self.members))

    def validate(self, operation: Operation) -> None:
        if operation == "snapshot" and not self.covered:
            raise ValueError("member snapshot requires covered scope")
        if operation in ("add", "remove", "revise") and self.members:
            raise ValueError("member delta carries no member set")

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "state": self.state,
                "covered": self.covered,
                "members": [m.to_dict() for m in self.members]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MembershipPayload:
        return cls(
            role=str(d.get("role") or ""), state=str(d.get("state") or ""),
            covered=str(d.get("covered") or ""),
            members=tuple(
                MembershipEntry(subject=str(m["subject"]),
                                role=str(m.get("role") or ""),
                                state=str(m.get("state") or "member"))
                for m in d.get("members") or ()))


@dataclass(frozen=True)
class PinPayload(ObservationPayload):
    """The room's pin set (object_ref is the set's scope). add/remove carry
    `message` — the pinned/unpinned target as the platform addresses it.
    snapshot carries the pin set the source reports in its covered scope."""

    message: EditTarget | None = None
    pins: tuple[EditTarget, ...] = ()
    kind: str = field(default="pin", init=False)

    def validate(self, operation: Operation) -> None:
        if operation in ("add", "remove") and self.message is None:
            raise ValueError("pin delta requires message")
        if operation == "snapshot" and self.message is not None:
            raise ValueError("pin snapshot carries pins, not message")

    def to_dict(self) -> dict[str, Any]:
        return {"message": _target_dict(self.message),
                "pins": [_target_dict(p) for p in self.pins]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PinPayload:
        return cls(message=_target_of(d.get("message")),
                   pins=tuple(t for t in (
                       _target_of(p) for p in d.get("pins") or ())
                       if t is not None))


@dataclass(frozen=True)
class ArtifactPayload(ObservationPayload):
    """A shared post/artifact reference: kind, source metadata, custody and
    perception as SEPARATE facts. `custody` is "declared" (source describes
    bytes Nora does not hold), "held" (existing media custody), or
    "unavailable"; `perception_ref` points at an existing perception/read
    only when one exists. `preview` marks a thumbnail — a cover is never
    the full content."""

    artifact_kind: str = ""  # post | reel | file | contact | location | ...
    metadata: dict[str, Any] = field(default_factory=dict)
    custody: str = "declared"
    perception_ref: str = ""
    preview: bool = False
    kind: str = field(default="artifact", init=False)

    def validate(self, operation: Operation) -> None:
        if not self.artifact_kind:
            raise ValueError("artifact requires artifact_kind")
        if self.custody not in ("declared", "held", "unavailable"):
            raise ValueError(f"unknown custody {self.custody!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"artifact_kind": self.artifact_kind,
                "metadata": dict(self.metadata), "custody": self.custody,
                "perception_ref": self.perception_ref,
                "preview": self.preview}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactPayload:
        return cls(artifact_kind=str(d.get("artifact_kind") or ""),
                   metadata=dict(d.get("metadata") or {}),
                   custody=str(d.get("custody") or "declared"),
                   perception_ref=str(d.get("perception_ref") or ""),
                   preview=bool(d.get("preview")))


PAYLOAD_TYPES: dict[str, type[ObservationPayload]] = {
    "reaction": ReactionPayload,
    "message_metadata": MessageMetadataPayload,
    "poll": PollPayload,
    "room": RoomPayload,
    "membership": MembershipPayload,
    "pin": PinPayload,
    "artifact": ArtifactPayload,
}


def payload_from_dict(kind: str, d: dict[str, Any]) -> ObservationPayload:
    try:
        cls = PAYLOAD_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown observation kind {kind!r}") from None
    return cls.from_dict(d)


@dataclass(frozen=True)
class Observation:
    """One typed source observation — the domain half of the wire record.

    `object_ref` is the source's own handle for the object, required and
    scoped (a poll's id, a channel/thread's id, a member set's collection
    ref, a pin set's room, an artifact's ref, a metadata-bearing message's
    handle, a reaction's target message). `target` names the affected
    parent/message the way `EditTarget` does — the platform's own handle
    (`message_id`) or Signal's (author, sent_ts); None only when the source
    supplies none. `subject_ref` is the account an observation is ABOUT (the
    member joined/left), when it differs from the actor.

    `source_revision` is the opaque transition identity the source carries;
    `source_position` is its comparable position when the source genuinely
    orders — a gateway sequence, a group revision number — inside
    `revision_scope`. None where the source gives none; never manufactured.
    """

    object_kind: ObjectKind
    scope: SourceScope
    object_ref: str
    operation: Operation
    payload: ObservationPayload
    target: EditTarget | None = None
    target_author_id: str = ""
    subject_ref: str = ""
    actor_id: str = ""
    actor_status: ActorStatus = "not_supplied"
    occurred_ms: int | None = None
    received_ms: int = 0
    time_quality: TimeQuality = "unknown"
    source_revision: str | None = None
    source_position: int | None = None
    revision_scope: str = ""
    coverage: Coverage = field(default_factory=Coverage)

    def __post_init__(self) -> None:
        if self.object_kind not in OBJECT_KINDS:
            raise ValueError(f"unknown object_kind {self.object_kind!r}")
        if self.operation not in KIND_OPERATIONS[self.object_kind]:
            raise ValueError(
                f"operation {self.operation!r} invalid for {self.object_kind}")
        if self.actor_status not in ACTOR_STATUSES:
            raise ValueError(f"unknown actor_status {self.actor_status!r}")
        if self.time_quality not in ("upstream", "bridge_local", "unknown"):
            raise ValueError(f"unknown time_quality {self.time_quality!r}")
        if not self.object_ref:
            raise ValueError("object_ref is required")
        if self.actor_status == "known" and not self.actor_id:
            raise ValueError("actor_status known requires actor_id")
        if self.actor_id and self.actor_status == "not_applicable":
            raise ValueError("actor_id contradicts actor_status not_applicable")
        if self.occurred_ms is None and self.time_quality == "upstream":
            raise ValueError("upstream time_quality requires occurred_ms")
        if self.source_position is not None and not self.revision_scope:
            raise ValueError("source_position requires revision_scope")
        self.payload.validate(self.operation)


# ── The fold ──────────────────────────────────────────────────────────────
# One deterministic definition shared by the SQL store's transactional head
# update, the as-of replay and the fake — the same arrangement
# `fold_reactions`/`states_from_standing` keep for reaction counts. `state`
# is JSON-shaped so the stored head round-trips losslessly.

_UNCOMPARABLE = 2


@dataclass(frozen=True)
class StoredObservation:
    """An `Observation` as persisted: its identity, resolution and arrival.

    `recorded_at` is the database's insertion clock, not a commit timestamp.
    The union fold interleaves it with legacy `created_at` recording stamps.
    This defines the recorded-arrival fallback across stores; it proves no
    upstream ordering. Missing stamps or cross-family ties flag a gap.
    """

    id: int
    event_id: str
    source_digest: str
    observation: Observation
    target_message_id: int | None = None
    recorded_at: Any = None
    delivered_message_id: int | None = None


@dataclass(frozen=True)
class ObservationHead:
    """One object's folded current state — `platform_object_state`'s shape.

    `position`/`scope` name the latest applied event's source position.
    `ordering` is "ordered" (positions compared), "arrival" (no comparable
    order — the honest fallback) or "gap" (a known-stale transition was
    retained rather than applied out of order; `rebuild_head` resolves it).
    `coverage` keeps the fold's own flags (awaiting_definition, unordered,
    superseded snapshots, partial reads) — never guessed completeness.
    """

    kind: str
    object_ref: str
    state: dict[str, Any]
    applied: int = 0                      # observations folded into `state`
    head_id: int = 0                      # newest applied observation id
    position: int | None = None           # newest comparable source position
    scope: str = ""                       # revision_scope `position` lives in
    observed_ms: int | None = None        # latest applied event's source time
    ordering: str = "arrival"             # ordered | arrival | gap
    coverage: dict[str, Any] = field(default_factory=dict)
    #: The object's account and thread, carried from its scope so a
    #: room-wide read (empty `account_key`) can still give each head its
    #: own scoped handle: two accounts' same-named `p1` never merge under
    #: one `poll:<digest>`.
    account_key: str = ""
    thread: str = ""

    @property
    def observed_quality(self) -> str:
        """Which clock `observed_ms` reads on — the `time_quality` of the
        observation that supplied the newest applied source time, kept in
        `coverage` so the SQL row round-trips it. `unknown` when the head
        has no source time at all; a bridge's own clock is `bridge_local`,
        never promoted to `upstream`."""
        if self.observed_ms is None:
            return "unknown"
        return str(self.coverage.get("observed_quality") or "unknown")

    @property
    def received_ms(self) -> int:
        """The newest bridge-receipt clock folded into this head — the
        head's own availability time when the source's clock is unknown,
        stated as receipt, never as occurrence."""
        return int(self.coverage.get("received_ms") or 0)


#: `coverage` keys the fold writes for its own bookkeeping rather than as a
#: coverage CLAIM — lifted to named fields in `head_detail`, never rendered
#: inside the coverage label.
_FOLD_CLOCK_KEYS = ("observed_quality", "received_ms")


def _compare(head: ObservationHead, obs: Observation) -> int:
    """`obs` against the head's position: >0 newer, 0 equal, <0 stale —
    `_UNCOMPARABLE` when no honest order exists (either side positionless,
    or the positions live in different scopes; opaque revisions are never
    coerced into one)."""
    if obs.source_position is None or not obs.revision_scope:
        return _UNCOMPARABLE
    if head.position is None:
        return 1  # first positioned observation establishes the scope
    if obs.revision_scope != head.scope:
        return _UNCOMPARABLE
    return ((obs.source_position > head.position)
            - (obs.source_position < head.position))


def fold_observation(
    head: ObservationHead | None, row: StoredObservation,
) -> ObservationHead:
    """Apply one stored observation to `head` — THE one fold.

    Order rules (design 02): deltas apply once, in source order when a
    position exists; a complete snapshot replaces only its covered set and
    only at its source position — an old snapshot never overlays a newer
    applied position; a partial snapshot merges, never clears; a transition
    that precedes the head's position is retained and flagged `gap`, not
    applied out of order (a full replay applies it at its own position).
    With no comparable positions anywhere, arrival order stands and
    `ordering` says so.
    """
    obs = row.observation
    if head is None:
        head = ObservationHead(
            kind=obs.object_kind, object_ref=obs.object_ref,
            state=_initial_state(obs.object_kind),
            account_key=obs.scope.account_key, thread=obs.scope.thread)
    cmp = _compare(head, obs)
    coverage = dict(head.coverage)
    ordering = head.ordering
    state, applied = head.state, False

    if cmp < 0:
        if obs.operation == "snapshot":
            # Provably old snapshot: retained as history, never overlaying
            # a newer applied position.
            coverage["superseded_snapshots"] = (
                coverage.get("superseded_snapshots", 0) + 1)
        else:
            coverage["ordering_gap"] = True
            ordering = "gap"
    else:
        state = _apply(dict(head.state), obs, cmp)
        applied = True
        if head.applied and cmp == _UNCOMPARABLE and (
            head.position is not None or obs.source_position is not None
        ):
            coverage["unordered"] = True
        if head.ordering != "gap":
            ordering = (
                "ordered" if obs.source_position is not None
                and obs.revision_scope and not coverage.get("unordered")
                else "arrival")
        if obs.coverage.covered != "event":
            coverage["covered"] = obs.coverage.covered
        if obs.coverage.gaps or not obs.coverage.complete:
            coverage["gaps"] = sorted(
                set(coverage.get("gaps", ())) | set(obs.coverage.gaps))
            if not obs.coverage.complete:
                coverage["partial"] = True
        if obs.object_kind == "poll" and not state.get("options") and (
            state.get("question") is None
        ):
            # Responses/lifecycle landed but no definition yet — the object
            # exists but is UNRESOLVED: empty options + no question is not an
            # empty poll, it is a poll whose definition has not arrived. The
            # flag clears when the definition folds in.
            coverage["awaiting_definition"] = True
        else:
            coverage.pop("awaiting_definition", None)

    observed = obs.occurred_ms if applied else head.observed_ms
    if applied:
        coverage["observed_quality"] = obs.time_quality
    # The newest receipt the fold has seen — the honest availability clock
    # for a head whose source clock is unknown; `received_ms` states it as
    # receipt, never as occurrence.
    coverage["received_ms"] = max(
        head.received_ms, obs.received_ms)
    position, scope = head.position, head.scope
    if applied:
        position, scope = obs.source_position, obs.revision_scope
    return ObservationHead(
        kind=head.kind, object_ref=head.object_ref, state=state,
        applied=head.applied + (1 if applied else 0),
        head_id=row.id if applied else head.head_id,
        position=position, scope=scope, observed_ms=observed,
        ordering=ordering, coverage=coverage,
        account_key=head.account_key, thread=head.thread)


def rebuild_head(
    rows: list[StoredObservation], *, kind: str, object_ref: str,
) -> ObservationHead | None:
    """The supported full replay: fold retained observations of one object
    with the same `fold_observation` the incremental head update uses.

    When EVERY row carries a position in one `revision_scope`, rows replay
    in that source order — the bounded reconstruction a late arrival asks
    for (`as_of` never claims more order than the source proved: a stream
    with even one positionless or foreign-scoped row replays in arrival
    order, honestly labelled)."""
    scopes = {r.observation.revision_scope for r in rows
              if r.observation.revision_scope}
    if rows and len(scopes) == 1 and all(
        r.observation.source_position is not None for r in rows
    ):
        rows = sorted(rows, key=lambda r: (
            r.observation.source_position, r.id))
    head: ObservationHead | None = None
    for row in rows:
        head = fold_observation(head, row)
    return head


# ── The reaction union fold ─────────────────────────────────────────────
# One target's reactions are recorded on TWO append-only tables: per-actor
# `message_reaction` deltas and typed `platform_observation` reaction rows
# (clear/clear-emoji/snapshot, plus typed deltas where a platform emits
# them). A read that consults only one disagrees with a read that consults
# the other, so `fold_target_reactions` is the ONE fold every reader's
# answer derives from — the SQL annotation path, the typed head surfaces
# and the fake all call it.

_RECORD_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_RECORD_US = timedelta(microseconds=1)


def _record_us(value: Any) -> int | None:
    """A database recording-clock stamp as comparable epoch microseconds —
    `recorded_at`/`created_at` may arrive as a `datetime` or as an already-
    extracted integer. None or an unrecognized form means the store supplied
    no clock, which the fold surfaces as an unproven order, never a guessed
    position."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return int((value - _RECORD_EPOCH) // _RECORD_US)
    if isinstance(value, (int, float)):
        return int(value)
    return None


@dataclass(frozen=True)
class TargetReactions:
    """One reaction target's folded state across BOTH append-only stores —
    `fold_target_reactions`' single answer, projected to a `RowReactions`
    annotation or an `ObservationHead` depending on which reader asked.

    `coverage` is the annotation-scale claim: "recorded" when every recorded
    transition folded with its order proven, "partial" when the fold is
    honest-but-incomplete (the read's bound cut transitions, a removal or a
    covered-actor clear could not be attributed, or the source declared its
    snapshot incomplete), "gap" when the ORDER of recorded transitions
    could not be proven (incomparable recording clocks, out-of-scope source
    positions, retained stale rows). `standing`/`counts`/`reactors` carry
    the recorded state regardless — projections apply the withholding rule
    (`merged_reaction_counts`), never the fold.
    """

    standing: tuple[tuple[str, tuple[str, ...]], ...] = ()
    counts: tuple[tuple[str, int], ...] = ()
    reactors: tuple[str, ...] | None = None
    snapshot: bool = False
    applied: int = 0
    recorded: int = 0
    observed_at: int | None = None
    coverage: str = "recorded"
    ordering: str = "arrival"
    position: int | None = None
    scope: str = ""
    head_id: int = 0
    flags: dict[str, Any] = field(default_factory=dict)


def merged_reaction_counts(
    tr: TargetReactions,
) -> tuple[tuple[str, int], ...]:
    """Standing actors plus unattributed counts as per-emoji totals — the
    one counting rule the annotation and the head render share. Withheld
    entirely unless the fold's coverage is `recorded`: a truncated,
    ambiguous or order-unproven set never presents as an exact count."""
    if tr.coverage != "recorded":
        return ()
    merged: dict[str, int] = {e: len(a) for e, a in tr.standing}
    for emoji, n in tr.counts:
        merged[emoji] = merged.get(emoji, 0) + n
    return tuple(sorted(merged.items()))


def to_row_reactions(tr: TargetReactions) -> RowReactions:
    """The union fold's answer as the transcript/tool annotation every
    `message_reaction` reader has always consumed — same `RowReactions`
    shape, same `recorded`/`observed_at`/`coverage` semantics, now covering
    typed clear/snapshot transitions too."""
    return RowReactions(counts=merged_reaction_counts(tr),
                        observed_at=tr.observed_at, recorded=tr.recorded,
                        coverage=tr.coverage)


def union_head(
    tr: TargetReactions, *, object_ref: str, account_key: str = "",
    thread: str = "",
) -> ObservationHead:
    """The union fold's answer as a typed `ObservationHead` — the current/
    heads/detail surfaces' shape. `state` keeps `_apply_reaction`'s keys;
    `standing`/`counts` withhold under non-"recorded" coverage, the same
    rule `merged_reaction_counts` applies to the annotation, so a partial
    or order-unproven state never shows as exact on one surface while
    withheld on another. `observed_ms` is the newest recorded source-event
    time across both stores — `RowReactions.observed_at`'s semantic, which
    unlike the plain fold's counts retained-but-unapplied rows too."""
    withhold = tr.coverage != "recorded"
    state = {
        "standing": ({} if withhold
                     else {e: list(a) for e, a in tr.standing}),
        "counts": {} if withhold else dict(tr.counts),
        "reactors": list(tr.reactors or ()),
        "snapshot": tr.snapshot,
    }
    return ObservationHead(
        kind="reaction", object_ref=object_ref, state=state,
        applied=tr.applied, head_id=tr.head_id, position=tr.position,
        scope=tr.scope, observed_ms=tr.observed_at, ordering=tr.ordering,
        coverage=dict(tr.flags), account_key=account_key, thread=thread)


def fold_target_reactions(
    legacy: Iterable[ReactionRow],
    stored: Iterable[StoredObservation],
    *,
    recorded: int | None = None,
    observed_at: int | None = None,
    partial: bool = False,
    gap: bool = False,
) -> TargetReactions | None:
    """Fold one reaction target across BOTH append-only tables.

    `legacy` is the target's `message_reaction` rows — the fold reduces
    them to latest-per-(actor, emoji) exactly as `fold_reactions` does, so
    a caller may pass the whole set or the already-reduced standing tuples
    (the reduction is idempotent). `stored` is the object's/target's typed
    reaction `platform_observation` rows — including unresolved-target
    rows a head-level fold legitimately owns.

    ORDER: legacy `created_at` and typed `recorded_at` use the database's
    insertion clock, never transaction-start or source time. Their merge
    defines recorded arrival, not transaction commit order or an upstream
    revision. Missing stamps and cross-family ties flag `unordered`.
    Within the typed stream, comparable source positions still reject stale
    rows and flag ordering gaps; incomparable positions remain labelled.

    `recorded`/`observed_at` let a bounded read supply the REAL totals
    (database-side, before its cap) so the projection reports the whole
    recorded set's reach rather than the window's. `partial` marks a read
    whose own bound cut transitions — the coverage the annotation has
    always used. `gap` lets a caller assert the order itself could not be
    proven (an as-of bound that resolved to nothing): coverage degrades to
    "gap" even when the rows it did fold compared cleanly. Returns None
    when nothing at all was recorded, so the caller's `reactions=None`
    ("nothing recorded") stays distinct from an empty standing set.
    """
    legacy = list(legacy)
    stored = list(stored)
    if not legacy and not stored and not recorded:
        return None

    latest: dict[tuple[str, str], ReactionRow] = {}
    for r in legacy:
        prev = latest.get((r.sender_id, r.emoji))
        if prev is None or r.id > prev.id:
            latest[(r.sender_id, r.emoji)] = r

    # (recorded_at μs | None, family 0=legacy 1=typed, seq, row). On a tie the
    # legacy delta applies first — deterministic, and the fail-closed side:
    # a clear that cannot be proven LATER still wins. Unorderable clocks
    # matter only across families: same-family events order by `id` anyway,
    # and distinct (actor, emoji) legacy deltas commute.
    events: list[tuple[int | None, int, int, Any]] = []
    for r in latest.values():
        events.append((_record_us(r.created_at), 0, r.id, r))
    for s in stored:
        events.append((_record_us(s.recorded_at), 1, s.id, s))
    mixed = len({fam for _a, fam, _s, _r in events}) > 1
    unordered_clock = mixed and any(
        a is None for a, _f, _s, _r in events)
    seen_at: dict[int, int] = {}
    for at, fam, _seq, _row in events:
        if at is None:
            continue
        if at in seen_at and seen_at[at] != fam:
            unordered_clock = True
        else:
            seen_at[at] = fam
    events.sort(key=lambda e: (e[0] is None, e[0] or 0, e[1], e[2]))

    state = _initial_state("reaction")
    position: int | None = None
    scope = ""
    ordering = "arrival"
    cov: dict[str, Any] = {}
    applied = 0
    head_id = 0
    received_ms = 0
    observed = 0
    observed_quality = "unknown"
    ambiguous = False

    for _at, fam, _seq, row in events:
        if fam == 0:
            r = cast(ReactionRow, row)
            standing = state["standing"]
            actors = standing.setdefault(r.emoji, [])
            if r.removed:
                if r.sender_id not in actors and state["counts"].get(r.emoji):
                    ambiguous = True
                if r.sender_id in actors:
                    actors.remove(r.sender_id)
                if not actors:
                    standing.pop(r.emoji, None)
            else:
                if r.sender_id not in actors:
                    actors.append(r.sender_id)
                # A recorded delta lands after any snapshot: the state is a
                # fold again, never a verbatim snapshot.
                state["snapshot"] = False
            applied += 1
            if r.ts >= observed:
                observed = r.ts
                observed_quality = "unknown"
            continue

        srow = cast(StoredObservation, row)
        obs = srow.observation
        # `_compare` against the union's running typed position — the same
        # position rules `fold_observation` applies, factored to a running
        # (position, scope) pair rather than a carried head.
        if obs.source_position is None or not obs.revision_scope:
            cmp = _UNCOMPARABLE
        elif position is None:
            cmp = 1
        elif obs.revision_scope != scope:
            cmp = _UNCOMPARABLE
        else:
            cmp = (obs.source_position > position) - (
                obs.source_position < position)
        received_ms = max(received_ms, obs.received_ms)
        ev_ms = (obs.occurred_ms if obs.occurred_ms is not None
                 else obs.received_ms)
        if cmp < 0:
            # Provably-stale row: retained and read into `observed`,
            # never applied over a newer position.
            if obs.operation == "snapshot":
                cov["superseded_snapshots"] = (
                    cov.get("superseded_snapshots", 0) + 1)
            else:
                cov["ordering_gap"] = True
                ordering = "gap"
            if ev_ms >= observed:
                observed = ev_ms
                observed_quality = obs.time_quality
            continue
        p = cast(ReactionPayload, obs.payload)
        if obs.operation == "remove" and obs.actor_id and (
            obs.actor_id not in state["standing"].get(p.emoji, ())
            and state["counts"].get(p.emoji)
        ):
            ambiguous = True
        if obs.operation == "remove" and not obs.actor_id and (
            not state["counts"].get(p.emoji)
            and state["standing"].get(p.emoji)
        ):
            # An unattributed removal with no unattributed count left may
            # name a recorded standing actor's reaction — the state cannot
            # say which, so it cannot claim an exact count.
            ambiguous = True
        if obs.operation == "clear" and p.covered_actors and any(
            state["counts"].get(e)
            for e in (p.covered_emojis or tuple(state["counts"]))
        ):
            # An actor-scoped clear can overlap unattributed counts whose
            # actors the source never supplied.
            ambiguous = True
        prior = applied
        state = _apply_reaction(state, obs, cmp)
        applied += 1
        head_id = srow.id
        if prior and cmp == _UNCOMPARABLE and (
            position is not None or obs.source_position is not None
        ):
            cov["unordered"] = True
        if ordering != "gap":
            ordering = ("ordered"
                        if obs.source_position is not None
                        and obs.revision_scope
                        and not cov.get("unordered") else "arrival")
        if obs.coverage.covered != "event":
            cov["covered"] = obs.coverage.covered
        if obs.coverage.gaps or not obs.coverage.complete:
            cov["gaps"] = sorted(
                set(cov.get("gaps", ())) | set(obs.coverage.gaps))
            if not obs.coverage.complete:
                cov["partial"] = True
        position, scope = obs.source_position, obs.revision_scope
        if ev_ms >= observed:
            observed = ev_ms
            observed_quality = obs.time_quality

    if ambiguous:
        cov["ambiguous"] = True
    if partial:
        cov["partial"] = True
    if unordered_clock or gap:
        cov["unordered"] = True
    gapped = (gap or unordered_clock or ordering == "gap"
              or bool(cov.get("unordered")))
    coverage = ("gap" if gapped else
                "partial" if (partial or ambiguous or cov.get("partial")
                              or cov.get("gaps")) else "recorded")
    if ordering == "gap" or unordered_clock or gap:
        ordering = "gap"
    obs_ms = int(observed_at) if observed_at is not None else observed
    # A caller-supplied (real) newest time ahead of the folded one means the
    # bound cut newer rows — their source clock is not claimed.
    cov["observed_quality"] = (
        "unknown" if obs_ms > observed else observed_quality)
    cov["received_ms"] = received_ms
    rec = int(recorded) if recorded is not None else len(legacy) + len(stored)
    return TargetReactions(
        standing=tuple(sorted(
            (e, tuple(a)) for e, a in state["standing"].items())),
        counts=tuple(sorted(state["counts"].items())),
        reactors=(tuple(sorted(state["reactors"]))
                  if state["reactors"] is not None else None),
        snapshot=bool(state["snapshot"]),
        applied=applied, recorded=rec, observed_at=obs_ms or None,
        coverage=coverage, ordering=ordering, position=position,
        scope=scope, head_id=head_id, flags=cov)


def _initial_state(kind: str) -> dict[str, Any]:
    if kind == "poll":
        return {"question": None, "options": {}, "state": "unknown",
                "multi_select": None, "anonymous": None, "close_ms": None,
                "parent_ref": "", "votes": {}, "option_counts": {},
                "voter_count": None, "counts_complete": False}
    if kind == "reaction":
        return {"standing": {}, "counts": {}, "reactors": [],
                "snapshot": False}
    if kind == "room":
        return {"fields": {}}
    if kind == "membership":
        return {"members": {}, "covered": ""}
    if kind == "pin":
        return {"pins": {}}
    if kind == "artifact":
        return {"artifacts": {}}
    if kind == "message_metadata":
        return {"fields": {}}
    return {}


def _apply(state: dict[str, Any], obs: Observation, cmp: int) -> dict[str, Any]:
    """The per-kind state transition. `cmp` is the position comparison
    against the head before this observation: a SNAPSHOT applies only when
    not provably stale (checked inside each snapshot branch)."""
    kind = obs.object_kind
    if kind == "poll":
        return _apply_poll(state, obs, cmp)
    if kind == "reaction":
        return _apply_reaction(state, obs, cmp)
    if kind == "room":
        return _apply_room(state, obs)
    if kind == "membership":
        return _apply_membership(state, obs)
    if kind == "pin":
        return _apply_pin(state, obs)
    if kind == "artifact":
        return _apply_artifact(state, obs)
    if kind == "message_metadata":
        return _apply_metadata(state, obs)
    return state


def _apply_poll(
    state: dict[str, Any], obs: Observation, cmp: int,
) -> dict[str, Any]:
    p = cast(PollPayload, obs.payload)
    op = obs.operation
    if op in ("create", "revise", "withdraw") and not p.voter_ref:
        if p.question is not None:
            state["question"] = p.question
        for ref, label in p.options:
            # Option refs are stable identity; a changed label revises the
            # SAME option, never a positional replacement.
            entry = dict(state["options"].get(ref) or {})
            entry["label"] = label
            state["options"][ref] = entry
        if p.multi_select is not None:
            state["multi_select"] = p.multi_select
        if p.anonymous is not None:
            state["anonymous"] = p.anonymous
        if p.state:
            state["state"] = p.state
        if p.close_ms is not None:
            state["close_ms"] = p.close_ms
        if p.parent_ref:
            state["parent_ref"] = p.parent_ref
        if op == "withdraw":
            state["state"] = "withdrawn"
        return state
    # Response observations.
    votes: dict[str, list[str]] = {
        k: list(v) for k, v in state["votes"].items()}
    if op in ("add", "remove"):
        sel = list(votes.get(p.voter_ref) or [])
        if op == "add" and p.option_ref not in sel:
            sel.append(p.option_ref)
        if op == "remove" and p.option_ref in sel:
            sel.remove(p.option_ref)
        if sel:
            votes[p.voter_ref] = sel
        else:
            votes.pop(p.voter_ref, None)
    elif op == "revise" and p.voter_ref:
        # Complete voter-selection replacement: [] is a withdrawn vote, not
        # an empty poll and not "unknown".
        if p.selected:
            votes[p.voter_ref] = list(p.selected)
        else:
            votes.pop(p.voter_ref, None)
    elif op == "clear":
        votes = {}
        state["option_counts"] = {}
        state["voter_count"] = None
    elif op == "snapshot":
        if p.voters is not None:
            if obs.coverage.complete:
                votes = {v: list(o) for v, o in p.voters}
            else:
                for v, o in p.voters:
                    votes[v] = list(o)
        if obs.coverage.complete and "option_counts" in (
                obs.coverage.covered or ""):
            # The covered count table replaces EXACTLY — including the
            # covered empty table a source reports for "no recorded
            # votes" — never a merge that resurrects counts it omitted.
            # A field the covered table does not carry is unknown inside
            # the same set, not the older snapshot's leftover.
            state["option_counts"] = dict(p.option_counts)
            state["voter_count"] = p.voter_count
            state["counts_complete"] = True
        else:
            if p.option_counts:
                # Counts outside a complete covered claim are partial
                # evidence: they merge, never erase omitted options, and
                # a voters-only coverage leaves the whole table alone.
                counts = dict(state["option_counts"])
                for ref, n in p.option_counts:
                    counts[ref] = n
                state["option_counts"] = counts
                state["counts_complete"] = False
            if p.voter_count is not None:
                state["voter_count"] = p.voter_count
    state["votes"] = votes
    return state


def _apply_reaction(
    state: dict[str, Any], obs: Observation, cmp: int,
) -> dict[str, Any]:
    p = cast(ReactionPayload, obs.payload)
    op = obs.operation
    standing: dict[str, list[str]] = {
        e: list(a) for e, a in state["standing"].items()}
    if op == "add":
        if obs.actor_id:
            actors = standing.setdefault(p.emoji, [])
            if obs.actor_id not in actors:
                actors.append(obs.actor_id)
        else:
            # Actor unknown/not supplied: the reaction is recorded as an
            # unattributed count — never an invented identity.
            state["counts"][p.emoji] = state["counts"].get(p.emoji, 0) + 1
        state["snapshot"] = False
    elif op == "remove":
        if obs.actor_id:
            actors = standing.get(p.emoji) or []
            if obs.actor_id in actors:
                actors.remove(obs.actor_id)
            if not actors:
                standing.pop(p.emoji, None)
        else:
            n = state["counts"].get(p.emoji, 0)
            if n > 1:
                state["counts"][p.emoji] = n - 1
            else:
                state["counts"].pop(p.emoji, None)
    elif op == "clear":
        emojis = p.covered_emojis or tuple(sorted(
            set(standing) | set(state["counts"])))
        state["snapshot"] = False
        for emoji in emojis:
            if not p.covered_actors:
                standing.pop(emoji, None)
                state["counts"].pop(emoji, None)
            else:
                left = [a for a in standing.get(emoji, ())
                        if a not in p.covered_actors]
                if left:
                    standing[emoji] = left
                else:
                    standing.pop(emoji, None)
    elif op == "snapshot":
        state["counts"] = _pairs_to_dict(p.counts)
        if p.reactors is not None:
            # Identities the source supplied, verbatim — never synthesized
            # from counts.
            state["reactors"] = sorted(p.reactors)
        if obs.coverage.complete:
            # The covered set replaces standing deltas: actors are the
            # reactors the source named (per-emoji standing is unknown from
            # counts alone — kept out rather than guessed).
            standing = {}
        state["snapshot"] = True
    state["standing"] = standing
    return state


def _apply_room(state: dict[str, Any], obs: Observation) -> dict[str, Any]:
    p = cast(RoomPayload, obs.payload)
    fields = dict(state["fields"])
    if obs.operation == "snapshot":
        if not obs.coverage.complete:
            # Partial snapshot: merge reported fields, never clear the rest.
            fields.update(p.fields)
        else:
            covered = obs.coverage.covered
            if covered and covered != "fields":
                # Replaces only its covered set: drop fields under the
                # covered prefix, keep everything else.
                prefix = covered + "."
                fields = {k: v for k, v in fields.items()
                          if not (k == covered or k.startswith(prefix))}
                fields.update(p.fields)
            else:
                fields = dict(p.fields)
    elif obs.operation in ("create", "revise"):
        for k, v in p.fields.items():
            if v is None:
                fields.pop(k, None)
            else:
                fields[k] = v
    elif obs.operation == "withdraw":
        fields = {}
    state["fields"] = fields
    return state


def _apply_membership(
    state: dict[str, Any], obs: Observation,
) -> dict[str, Any]:
    p = cast(MembershipPayload, obs.payload)
    members = {s: dict(m) for s, m in state["members"].items()}
    op = obs.operation
    if op in ("add", "revise"):
        if obs.subject_ref:
            entry = dict(members.get(obs.subject_ref) or {})
            if p.role:
                entry["role"] = p.role
            entry["state"] = p.state or "member"
            members[obs.subject_ref] = entry
    elif op == "remove":
        if obs.subject_ref:
            entry = dict(members.get(obs.subject_ref) or {})
            entry["state"] = p.state or "left"
            if p.role:
                entry["role"] = p.role
            members[obs.subject_ref] = entry
    elif op == "snapshot":
        state["covered"] = p.covered
        if obs.coverage.complete:
            # The covered collection IS the object — a complete snapshot
            # replaces its member set; a partial one merges.
            members = {m.subject: {"role": m.role, "state": m.state}
                       for m in p.members}
        else:
            for m in p.members:
                members[m.subject] = {"role": m.role, "state": m.state}
    state["members"] = members
    return state


def _apply_pin(
    state: dict[str, Any], obs: Observation,
) -> dict[str, Any]:
    p = cast(PinPayload, obs.payload)
    pins = dict(state["pins"])
    op = obs.operation
    # Timestamp-addressed targets need their author too: two authors can
    # share a sent timestamp, and `target_key` is the ONE typed addressing
    # — the same key an observation stores, so add and remove meet on it.
    key = target_key(p.message, author_id=obs.target_author_id)
    if op == "add" and p.message is not None:
        pins[key] = {
            "actor": obs.actor_id or None,
            "pinned_ms": obs.occurred_ms,
            "time_quality": obs.time_quality}
    elif op == "remove" and p.message is not None:
        pins.pop(key, None)
    elif op == "snapshot":
        if obs.coverage.complete:
            pins = {}
        for t in p.pins:
            # Snapshot targets carry no author pair of their own; a
            # timestamp-addressed pin without one keeps the bare `ts:` key,
            # honestly unresolvable rather than co-claimed by an author.
            pins.setdefault(_target_key(t), {"actor": None,
                                             "pinned_ms": None,
                                             "time_quality": "unknown"})
    state["pins"] = pins
    return state


def _apply_artifact(state: dict[str, Any], obs: Observation) -> dict[str, Any]:
    p = cast(ArtifactPayload, obs.payload)
    arts = dict(state["artifacts"])
    if obs.operation == "withdraw":
        arts.pop(obs.object_ref, None)
    else:
        entry = dict(arts.get(obs.object_ref) or {})
        entry.update({"artifact_kind": p.artifact_kind,
                      "custody": p.custody,
                      "perception_ref": p.perception_ref,
                      "preview": p.preview})
        md = dict(entry.get("metadata") or {})
        incoming = dict(p.metadata)
        # A responder's RSVP is PER-RESPONDER evidence, not a scalar field:
        # keyed under the acting subject so an earlier responder's answer is
        # never overwritten by a later one into a misleading last-response.
        # An unattributed response keys on its source revision so two of
        # them still cannot merge into one. A falsy `response` removes the
        # responder's recorded answer (a withdrawn RSVP).
        response = incoming.pop("response", None)
        extra_guests = incoming.pop("extra_guest_count", None)
        for k, v in incoming.items():
            # An explicit null is a source-declared CLEAR — a full-after
            # update reporting a field absent drops it, never keeps stale.
            if v is None:
                md.pop(k, None)
            else:
                md[k] = v
        if response is not None:
            key = obs.actor_id or f"unattributed:{obs.source_revision or obs.object_ref}"
            responses = dict(md.get("responses") or {})
            if response:
                responses[key] = str(response)
            else:
                responses.pop(key, None)
            md["responses"] = responses
            guests = dict(md.get("response_guests") or {})
            if isinstance(extra_guests, int) and not isinstance(extra_guests, bool):
                guests[key] = extra_guests
            else:
                guests.pop(key, None)
            md["response_guests"] = guests
        entry["metadata"] = md
        arts[obs.object_ref] = entry
    state["artifacts"] = arts
    return state


def _apply_metadata(
    state: dict[str, Any], obs: Observation,
) -> dict[str, Any]:
    p = cast(MessageMetadataPayload, obs.payload)
    fields = dict(state["fields"])
    if obs.operation == "withdraw":
        fields = {}
    else:
        for k, v in (("forward_origin", p.forward_origin),
                     ("expires_in_ms", p.expires_in_ms),
                     ("view_once", p.view_once)):
            if v is not None:
                fields[k] = v
        if p.shared_refs:
            fields["shared_refs"] = list(p.shared_refs)
    state["fields"] = fields
    return state


# ── Model-facing source-event view ────────────────────────────────────────
# ONE read model shared by the transcript's typed source events, the agenda
# row an `observation` stimulus renders, and the detail tools: what happened
# (kind + operation), when — and WHICH clock, who when the source genuinely
# names them, the object's model-safe handle, a bounded summary, and the
# head's current state for detail reads. No speaker, no fabricated
# utterance: an observation is something the source DID to an object, never
# a line somebody said.


@dataclass(frozen=True, slots=True)
class SourceEvent:
    """One platform source event as a model or a tool result reads it.

    `actor` is a NAME the caller already resolved (roster/persons), or None —
    a source that names no actor shows none, and `actor_status`/`anonymous`
    says why. `at_ms` pairs with `time_quality` so "unknown" never reads as
    a source time; `received_ms` is the bridge's own clock, stated as such.
    `handle` is `object_handle`'s — the only form of `object_ref` a model
    ever sees or hands back.
    """

    ref: str                        # "platform_observation:<row id>"
    kind: str                       # object kind
    operation: str
    at_ms: int
    time_quality: str               # upstream | bridge_local | unknown
    actor: str | None
    actor_status: str
    handle: str                     # object_handle(kind, object_ref)
    summary: str                    # bounded pure summary of the change
    target_message_id: int | None = None
    delivered_message_id: int | None = None
    state: str = ""                 # head summary when a detail read asks
    ordering: str = ""              # head's ordered|arrival|gap, when shown
    #: The bridge's own receive clock for this row — shown beside `at_ms`
    #: when the source's time is unknown or bridge-local, never silently
    #: substituted for it. 0 means no received time was recorded.
    received_ms: int = 0
    #: The source's own revision label (`revision_scope:position`), when
    #: the source supplies one — ordering evidence, never parsed.
    revision: str = ""
    #: The source header's coverage, labelled (`coverage_label`): the
    #: covered grain ("guild", "pin_set"), its gaps and its partiality —
    #: never smoothed into a complete-looking fact.
    coverage: str = ""
    #: The recorded message this event affected, resolved at read time
    #: (`target_context`) — None while `target_message_id` is unset OR the
    #: target cannot be shown (hidden, pruned, another room). Renderers
    #: distinguish «named no target» (id None) from «could not be shown»
    #: (id set, target None) and say the latter, never a guessed quote.
    target: TargetContext | None = None


#: The most text of one affected message a surface carries: a bounded quote,
#: not a retransmission of the row.
_TARGET_TEXT_MAX = 240


@dataclass(frozen=True, slots=True)
class TargetContext:
    """The recorded message a source event affected, resolved and bounded.

    `author` is the roster/profile name the caller already resolved — never a
    raw id, and never the event's own actor (a reactor is not the target's
    author). `at_ms` is the row's own time. `text` is the CURRENT words — the
    newest edit folded in — or None when they may not be shown (a deleted
    row). `edited_ms`/`was` say the row was edited and what it first said;
    `deleted` says its words were taken back. Every field is a Record fact:
    an unresolved target is simply absent, never reconstructed.
    """

    author: str
    at_ms: int
    text: str | None = None
    edited_ms: int | None = None
    was: str | None = None
    deleted: bool = False


def target_context(row: Any, *, deleted: bool, author: str) -> TargetContext:
    """One resolved target's `TargetContext` — the Record row's own facts,
    clipped, with a deleted row's words withheld rather than resurrected."""
    return TargetContext(
        author=author, at_ms=row.ts,
        text=None if deleted else _clip(render_file_links(row.body or ""), _TARGET_TEXT_MAX),
        edited_ms=row.edit.ts if row.edit is not None else None,
        was=(None if deleted or row.edit is None
             else _clip(render_file_links(row.edit.was or ""), _TARGET_TEXT_MAX)),
        deleted=deleted)


def _short_digest(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _grain_label(covered: Any) -> str:
    """A coverage grain as a model-safe token: `guild` passes through, a
    scoped grain (`guild:123…`, `channel abc`) keeps its NAME and digests
    whatever identified it — a guild or channel id is a platform identifier
    (R-18), never model-facing."""
    text = str(covered or "")[:80]
    name, sep, ident = text.partition(":")
    if sep and ident:
        return f"{name}:{_short_digest(ident)}"
    name, _, ident = text.partition(" ")
    if ident:
        return f"{name}:{_short_digest(ident)}"
    return text


def coverage_label(coverage: Coverage | dict[str, Any] | None) -> str:
    """A coverage header/dict as one compact label, or "" for the default
    full-event coverage. The covered grain is always named first when it
    is not the plain event (`guild`, `pin_set`): a guild-scoped read must
    not read as the room's membership."""
    if coverage is None:
        return ""
    get = coverage.get if isinstance(coverage, dict) else (
        lambda k, d=None: getattr(coverage, k, d))
    covered = get("covered") or ""
    gaps = get("gaps") or ()
    complete = bool(get("complete", True)) and not get("partial", False)
    reason = get("reason") or ""
    parts: list[str] = []
    if covered and covered != "event":
        parts.append(_grain_label(covered))
    if not complete:
        parts.append("partial")
    for flag in ("ordering_gap", "unordered", "awaiting_definition"):
        if get(flag, False):
            parts.append(flag)
    if gaps:
        parts.append("gaps:" + "+".join(
            _grain_label(g) for g in list(gaps)[:8]))
    if reason and (not complete or gaps):
        parts.append(f"reason:{_clip(str(reason), 60)}")
    return ";".join(parts)


def object_handle(
    kind: str, object_ref: str, *, account_key: str = "", thread: str = "",
) -> str:
    """The model-safe name of an object: `<kind>:<12 hex of its ref>`.
    Deterministic, so a handle a tool handed out resolves on the next read
    of the same scope — and an opaque digest, so a platform's own object id
    never reaches a prompt or a tool result (R-18).

    The object's scope (`account_key`, `thread`) joins the digest: a
    room-wide read that spans accounts or threads must never merge two
    scopes' same-named objects under one handle."""
    import hashlib
    digest = hashlib.sha256(
        f"{account_key}|{thread}|{kind}:{object_ref}".encode()
    ).hexdigest()[:12]
    return f"{kind}:{digest}"


def handle_of(head: ObservationHead) -> str:
    """A folded head's model-safe handle, scoped as `object_handle` is."""
    return object_handle(head.kind, head.object_ref,
                         account_key=head.account_key, thread=head.thread)


def parse_handle(handle: str) -> tuple[str, str]:
    """`(kind, digest)` of a handle, or `("", "")` when it is not one.

    A handle is `<kind>:<digest>`; the kind prefix is what lets a bare
    `object` argument name its own kind rather than guessing one."""
    kind, sep, digest = str(handle or "").partition(":")
    if not sep or not kind or not digest:
        return "", ""
    return kind, digest


_SUMMARY_MAX = 160


def _clip(text: str, limit: int = _SUMMARY_MAX) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def revision_label(position: int | None, scope: str) -> str:
    """One source revision as the model reads it — `<scope digest>:<pos>`.

    `revision_scope` is a source-side identifier (a Discord session id, a
    poll's revision token): ordering evidence, never a value the model
    reads raw (R-18). The scope keeps a stable opaque digest — two events
    in one scope share one label prefix, positions still order them."""
    if position is None or not scope:
        return ""
    return f"{_short_digest(str(scope))}:{position}"


def _option_handle(
    ref: str, *, account_key: str = "", thread: str = "",
) -> str:
    """A poll option ref as a scoped opaque handle — never a tail of the
    platform's own option id (R-18; `option <last4>` leaked exactly that)."""
    return object_handle("option", ref,
                         account_key=account_key, thread=thread)


def _field_value(value: Any) -> str:
    """One room field's rendered value: scalars only, bounded. A dict,
    list or tuple (a permission set, a participant map) is a structured
    value that can carry member ids — it reports `changed`, never its
    contents, so no platform identifier reaches a summary."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f"“{_clip(value, 60)}”"
    return "changed"


def _field_projection(name: str, value: Any, *, name_of: Any = None) -> Any:
    """Source reference fields keep their raw value only inside Record."""
    if value is None or not (name.endswith("_id") or name == "forward_origin"):
        return value
    if name == "owner_id" and name_of is not None:
        owner = name_of(str(value))
        if owner:
            return owner
    return _grain_label(f"{name}:{value}")


def _fields_summary(fields: dict[str, Any], *, name_of: Any = None) -> str:
    out = []
    for name in sorted(fields):
        value = _field_projection(name, fields[name], name_of=name_of)
        if value is None:
            out.append(f"{name} cleared")
        else:
            out.append(f"{name} {_field_value(value)}")
    return "; ".join(out)


def _who(ref: str, name_of: Any) -> str:
    """A subject's display name: the caller's resolved roster name, or an
    anonymous placeholder — never the raw platform `ref` itself (R-18)."""
    if not ref:
        return ""
    name = name_of(ref) if name_of is not None else None
    return name or "a member"


# ── The artifact metadata projection ─────────────────────────────────────
# Stored `ArtifactPayload.metadata` is a Record of what the source's own
# fields said; the projection below is the ONE bounded, source-safe view of
# it every surface renders (`observation_summary`, `head_summary`,
# `head_detail`). It is a WHITELIST of user-authored content — names,
# descriptions, times, counts and contact fields a person shared — so a
# platform identifier, coordinate or join credential stored under an
# unrecognized key can never reach a prompt. Anything outside the declared
# fields is counted, never dumped.


#: Metadata keys whose scalar value is user-authored content — rendered
#: bounded and verbatim-clipped (they carry names/text somebody wrote).
_ARTIFACT_TEXT_FIELDS = (
    "name", "display_name", "formatted_name", "title", "description",
    "organization", "url", "address", "caption", "location_name",
    "channel_name", "status", "entity_type",
)
#: Metadata keys holding a list of user-authored values (contact phones,
#: emails, urls) — each entry clipped, the list bounded.
_ARTIFACT_LIST_FIELDS = ("phones", "emails", "urls")
#: Metadata keys holding a plain integer fact.
_ARTIFACT_INT_FIELDS = ("contact_count", "extra_guest_count",
                        "interested_count", "unparsed")
#: Metadata keys holding epoch-SECONDS event times — rendered ISO, never
#: as a raw number a model could misread.
_ARTIFACT_TIME_FIELDS = ("start_time_s", "end_time_s")
#: Metadata keys holding a boolean fact.
_ARTIFACT_BOOL_FIELDS = ("is_canceled", "extra_guests_allowed", "invalidated")
#: Structured keys the projection renders by shape, not verbatim.
_ARTIFACT_STRUCTURED_KEYS = (
    "contacts", "contacts_more", "responses", "response_guests")
_ARTIFACT_LIST_MAX = 6
_ARTIFACT_CONTACTS_MAX = 8
_ARTIFACT_ENTRIES_MAX = 8


def _iso_s(value: Any) -> str:
    """An epoch-seconds field as `YYYY-MM-DD HH:MMZ`, or the raw value
    clipped when it is not a usable number — never a guessed time."""
    try:
        return datetime.fromtimestamp(
            int(value), tz=UTC).strftime("%Y-%m-%d %H:%MZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return _clip(str(value), 32)


def _contact_label(c: Any) -> str:
    """One shared-contact entry's name — the field a reader needs first.
    A malformed entry degrades to its own clipped form, never a crash."""
    if not isinstance(c, dict):
        return _clip(str(c), 40)
    return _clip(str(
        c.get("display_name") or c.get("formatted_name") or c.get("name")
        or c.get("organization") or "a contact"), 40)


def _contact_summary(c: Any) -> str:
    """One shared contact as a short label — name plus the bounded nested
    fields the card carried (`organization`, first `phones`/`emails`),
    each clipped; the rest stays in the detail projection."""
    if not isinstance(c, dict):
        return _contact_label(c)
    bits = [_contact_label(c)]
    org = c.get("organization")
    if isinstance(org, str) and org:
        bits.append(f"org “{_clip(org, 24)}”")
    for key in ("phones", "emails"):
        vals = c.get(key)
        if isinstance(vals, list):
            shown = [v for v in vals if isinstance(v, str) and v]
            if shown:
                tail = ", ".join(_clip(v, 24) for v in shown[:2])
                rest = len(shown) - 2
                bits.append(f"{key} {tail}" + (f" +{rest}" if rest > 0 else ""))
    return "; ".join(bits)


def _artifact_fields(
    md: dict[str, Any], *, name_of: Any = None,
) -> dict[str, Any]:
    """The ONE normalized view of an artifact's declared metadata —
    the whitelist both `artifact_field_summary` and `_artifact_meta_detail`
    render, values already bounded so no surface re-truncates. Per-responder
    responses keep WHO said what: `responders` maps each roster-named
    responder to their recorded response; `responders_unnamed` counts the
    rest, so a response is never collapsed into a bare tally."""
    out: dict[str, Any] = {}
    for key in _ARTIFACT_TEXT_FIELDS:
        v = md.get(key)
        if isinstance(v, str) and v:
            out[key] = _clip(v, 120)
    for key in _ARTIFACT_TIME_FIELDS:
        if key in md and md[key] is not None:
            out[key[:-2]] = _iso_s(md[key])
    for key in _ARTIFACT_BOOL_FIELDS:
        if isinstance(md.get(key), bool):
            out[key] = md[key]
    for key in _ARTIFACT_LIST_FIELDS:
        vals = [v for v in (md.get(key) or []) if isinstance(v, str) and v]
        if vals:
            out[key] = [_clip(v, 80) for v in vals[:_ARTIFACT_LIST_MAX]]
            if len(vals) > _ARTIFACT_LIST_MAX:
                out[f"{key}_more"] = len(vals) - _ARTIFACT_LIST_MAX
    contacts = [
        c if isinstance(c, dict) else {"display_name": _clip(str(c), 40)}
        for c in (md.get("contacts") or []) if c]
    if contacts:
        out["contacts"] = [
            {k: ([_clip(x, 40) for x in v[:_ARTIFACT_LIST_MAX]
                  if isinstance(x, str) and x]
                 if isinstance(v, list)
                 else _clip(str(v), 60))
             for k, v in c.items()
             if k in _ARTIFACT_TEXT_FIELDS + _ARTIFACT_LIST_FIELDS
             and ((isinstance(v, str) and v) or (isinstance(v, list) and v))}
            for c in contacts[:_ARTIFACT_CONTACTS_MAX]
        ]
        more = md.get("contacts_more")
        if not isinstance(more, int) or isinstance(more, bool):
            more = max(0, len(contacts) - _ARTIFACT_CONTACTS_MAX)
        if more > 0:
            out["contacts_more"] = more
    for key in _ARTIFACT_INT_FIELDS:
        if isinstance(md.get(key), int) and not isinstance(md[key], bool):
            out[key] = md[key]
    responses = md.get("responses") or {}
    if isinstance(responses, dict) and responses:
        tallies: dict[str, int] = {}
        named: dict[str, str] = {}
        for actor, r in responses.items():
            tallies[str(r)] = tallies.get(str(r), 0) + 1
            if str(actor).startswith("unattributed:"):
                continue
            n = name_of(actor) if name_of is not None else None
            if n:
                named[n] = _clip(str(r), 40)
        out["recorded_responses"] = dict(sorted(tallies.items())[:12])
        out["recorded_responders"] = len(responses)
        if named:
            out["responders"] = dict(list(named.items())[:_NAMED_MAX])
        out["responders_unnamed"] = len(responses) - len(named)
        out["recorded_subset"] = True
    guests = md.get("response_guests") or {}
    if isinstance(guests, dict) and guests:
        total = sum(int(v) for v in guests.values()
                    if isinstance(v, int) and not isinstance(v, bool))
        if total:
            out["recorded_extra_guests"] = total
    return out


def artifact_field_summary(
    artifact_kind: str, metadata: dict[str, Any], *, name_of: Any = None,
) -> str:
    """The declared content fields of one artifact's stored metadata, as
    one bounded `;`-joined summary — `""` when nothing declared is stored.
    Internal identifiers, coordinates and credentials are never in the
    declared set, so they cannot be rendered here."""
    md = metadata if isinstance(metadata, dict) else {}
    f = _artifact_fields(md, name_of=name_of)
    parts: list[str] = []
    for key in _ARTIFACT_TEXT_FIELDS:
        if key in f:
            parts.append(f"{key} “{_clip(f[key], 60)}”")
    for key in _ARTIFACT_TIME_FIELDS:
        if key[:-2] in f:
            parts.append(f"{key[:-2]} {f[key[:-2]]}")
    for key in _ARTIFACT_BOOL_FIELDS:
        if key in f:
            parts.append(key.replace("_", " ") if f[key]
                         else f"not {key.replace('_', ' ')}")
    for key in _ARTIFACT_LIST_FIELDS:
        vals = f.get(key)
        if vals:
            shown = ", ".join(_clip(v, 40) for v in vals)
            more = (f" (+{f[f'{key}_more']})"
                    if f.get(f"{key}_more") else "")
            parts.append(f"{key} {shown}{more}")
    if f.get("contacts"):
        shown = ", ".join(_contact_summary(c) for c in f["contacts"])
        more = (f" (+{f['contacts_more']})"
                if f.get("contacts_more") else "")
        parts.append(f"contacts {shown}{more}")
    for key in _ARTIFACT_INT_FIELDS:
        if key in f:
            parts.append(f"{key.replace('_', ' ')} {f[key]}")
    if f.get("recorded_responses"):
        tail = ", ".join(f"{k} x{v}"
                         for k, v in list(f["recorded_responses"].items())[:6])
        resp = f.get("responders") or {}
        shown = ", ".join(f"{n} {r}" for n, r in list(resp.items())[:6])
        hidden = len(resp) - min(len(resp), 6)
        unnamed = f.get("responders_unnamed", 0) + hidden
        who = f"; {shown}" if shown else ""
        who += f" (+{unnamed} unnamed)" if unnamed else ""
        parts.append(
            f"recorded responses {tail} "
            f"({f['recorded_responders']} responder(s){who})")
    if f.get("recorded_extra_guests"):
        parts.append(f"recorded extra guests {f['recorded_extra_guests']}")
    return "; ".join(parts)


def _artifact_entry_label(
    artifact_kind: str, entry: dict[str, Any], *, name_of: Any = None,
) -> str:
    """One stored artifact as a short label for `head_summary` — kind plus
    the identifying declared fields (name, time, lifecycle status, who
    answered, contact names) through the SAME normalized projection the
    detail surface renders, never a parallel lossy pick."""
    md = entry.get("metadata") or {}
    f = _artifact_fields(md, name_of=name_of)
    bits = [str(entry.get("artifact_kind") or artifact_kind or "object")]
    name = next((f[k] for k in ("name", "display_name", "formatted_name",
                                "title", "organization", "url") if f.get(k)),
                "")
    if entry.get("artifact_kind") == "contact" and not f.get("contacts"):
        bits.append(_contact_summary(f))
    elif name:
        bits.append(f"“{_clip(name, 40)}”")
    if f.get("start_time"):
        bits.append(f"at {f['start_time']}")
    status = str(f.get("status") or "")
    if f.get("is_canceled") or status == "canceled":
        bits.append("canceled")
    elif status and status != "scheduled":
        bits.append(f"status {status}")
    resp = f.get("responders") or {}
    if resp:
        shown = ", ".join(f"{n} {r}" for n, r in list(resp.items())[:4])
        rest = (f.get("recorded_responders") or 0) - min(len(resp), 4)
        bits.append(f"responded {shown}" + (f" (+{rest})" if rest > 0 else ""))
    elif f.get("recorded_responders"):
        bits.append(f"{f['recorded_responders']} recorded response(s)")
    if f.get("recorded_extra_guests"):
        bits.append(f"{f['recorded_extra_guests']} recorded extra guest(s)")
    if f.get("contacts"):
        shown = ", ".join(_contact_summary(c) for c in f["contacts"][:4])
        more = (f.get("contacts_more") or 0) + max(0, len(f["contacts"]) - 4)
        bits.append(f"contacts {shown}" + (f" (+{more})" if more else ""))
    custody = entry.get("custody")
    if custody and custody != "declared":
        bits.append(str(custody))
    return " ".join(bits)


def _artifact_meta_detail(
    entry: dict[str, Any], *, name_of: Any = None,
) -> dict[str, Any]:
    """One artifact's stored metadata as bounded structured detail —
    the SAME normalized projection `artifact_field_summary` renders.
    Whatever the whitelist did not project is reported as a COUNT, so a
    stored key is never silently absent and never dumped raw."""
    md = entry.get("metadata") or {}
    out = _artifact_fields(md, name_of=name_of)
    stored = len(md)
    shown_keys = (sum(1 for k in _ARTIFACT_TEXT_FIELDS + _ARTIFACT_TIME_FIELDS
                      + _ARTIFACT_BOOL_FIELDS + _ARTIFACT_LIST_FIELDS
                      + _ARTIFACT_INT_FIELDS + _ARTIFACT_STRUCTURED_KEYS
                      if k in md))
    if stored > shown_keys:
        out["unprojected_fields"] = stored - shown_keys
    return out


def observation_summary(
    obs: Observation, *, name_of: Any = None, option_label: Any = None,
) -> str:
    """The change one observation reports, as one bounded fact line — pure
    (a summary of the payload, never a conclusion about it).

    `name_of` resolves a person ref to a display name; `option_label`
    resolves a poll option ref to its label. Neither is guessed: an
    unresolved person is `a member`/`a voter`, an unresolved option an
    opaque `option` handle — a raw voter, subject or option ref never
    reaches a prompt or a tool result."""
    p = obs.payload
    op = obs.operation
    def option_ref(ref: str) -> str:
        return ((option_label(ref) if option_label is not None else None)
                or _option_handle(ref, account_key=obs.scope.account_key,
                                  thread=obs.scope.thread))
    if isinstance(p, PollPayload):
        if p.question:
            tail = f"“{_clip(p.question, 80)}”"
            opts = f" with {len(p.options)} options" if p.options else ""
            return _clip(f"poll {op}: {tail}{opts}")
        if p.voter_ref or p.option_ref or p.selected is not None:
            if p.selected:
                choice = ", ".join(
                    option_ref(o) for o in p.selected[:4]) or "an option"
            elif p.option_ref:
                choice = option_ref(p.option_ref)
            else:
                choice = "an option"
            who = ("an anonymous voter" if p.anonymous
                   else (_who(p.voter_ref, name_of) or "a voter"))
            return _clip(f"poll vote {op}: {who} on {choice}")
        if p.state:
            return _clip(f"poll {op}: state {p.state}"
                         + (f", {p.voter_count} votes"
                            if p.voter_count is not None else ""))
        if p.option_counts:
            return _clip(f"poll {op}: totals " + ", ".join(
                f"{option_ref(k)}={v}"
                for k, v in list(p.option_counts)[:8]))
        return f"poll {op}"
    if isinstance(p, RoomPayload):
        return _clip(f"room {op}: {_fields_summary(p.fields, name_of=name_of)}")
    if isinstance(p, MembershipPayload):
        if p.members:
            return _clip(f"membership {op}: {len(p.members)} members "
                         f"reported ({p.covered or 'set'})")
        role = f" as {p.role}" if p.role else ""
        state = f" ({p.state})" if p.state else ""
        return _clip(f"membership {op}: {_who(obs.subject_ref, name_of) or 'a member'}"
                     f"{role}{state}")
    if isinstance(p, PinPayload):
        if p.pins:
            return _clip(f"pin set {op}: {len(p.pins)} pins reported")
        return f"pin {op}: a message pinned" if op == "add" else \
            f"pin {op}: a message unpinned"
    if isinstance(p, ArtifactPayload):
        md = p.metadata if isinstance(p.metadata, dict) else {}
        if "response" in md:
            # A per-responder RSVP delta IS the observation — actor, their
            # answer (and extra guests), a withdrawal, or a response the
            # source marked unreadable. Never a bare "artifact revise".
            who = _who(obs.actor_id or "", name_of) or "a member"
            response = md.get("response")
            gaps = obs.coverage.gaps if obs.coverage else ()
            if response:
                tail = f"responded “{_clip(str(response), 40)}”"
                guests = md.get("extra_guest_count")
                if (isinstance(guests, int) and not isinstance(guests, bool)
                        and guests > 0):
                    tail += f" with {guests} extra guest(s)"
            elif "response" in gaps:
                tail = "answer is unknown"
            else:
                tail = "withdrew their response"
            return _clip(
                f"artifact {op}: {p.artifact_kind or 'object'} — {who} {tail}")
        fields = artifact_field_summary(
            p.artifact_kind, p.metadata, name_of=name_of)
        return _clip(f"artifact {op}: {p.artifact_kind or 'object'}"
                     + (f" — {fields}" if fields else "")
                     + f" ({p.custody or 'custody unknown'})")
    if isinstance(p, MessageMetadataPayload):
        bits = []
        if p.forward_origin is not None:
            bits.append("forwarded")
        if p.view_once:
            bits.append("view-once")
        if p.expires_in_ms is not None:
            bits.append("expiring")
        if p.shared_refs:
            bits.append(f"{len(p.shared_refs)} shared refs")
        return _clip(f"message metadata {op}: {', '.join(bits) or 'fields'}")
    if isinstance(p, ReactionPayload):
        return _clip(f"reaction {op}: {emoji_display(p.emoji or '?')}")
    return f"{obs.object_kind} {op}"


#: The folded member states that still mean "is a member now" — the
#: native vocabulary's only active claim. Everything else a delta or
#: snapshot recorded is HISTORY under its own label, never membership.
_MEMBER_ACTIVE_STATES = frozenset({"member"})
#: Not-yet-member states the source vocabulary carries — a join request
#: awaiting approval, an invite not yet accepted.
_MEMBER_PENDING_STATES = frozenset({"requested", "invited", "pending"})
#: Terminal non-member states — a departure/removal or a closed request.
_MEMBER_DEPARTED_STATES = frozenset(
    {"left", "removed", "kicked", "banned", "withdrawn", "rejected"})


def _member_standing(members: dict[str, Any]) -> dict[str, list[str]]:
    """Split the folded member collection by CURRENT standing. Only a
    native active state counts as a member; pending and terminal states
    report under their own labels, and an unrecognized state is
    `unknown` — counted as neither present nor departed."""
    standing: dict[str, list[str]] = {
        "member": [], "pending": [], "departed": [], "unknown": []}
    for subject, entry in members.items():
        state = str((entry or {}).get("state") or "")
        if state in _MEMBER_ACTIVE_STATES:
            standing["member"].append(subject)
        elif state in _MEMBER_PENDING_STATES:
            standing["pending"].append(subject)
        elif state in _MEMBER_DEPARTED_STATES:
            standing["departed"].append(subject)
        else:
            standing["unknown"].append(subject)
    return standing


def head_summary(head: ObservationHead, *, name_of: Any = None) -> str:
    """The object's folded CURRENT state as one bounded fact line —
    a poll's open/closed and recorded tallies, a room's named fields, a
    member count, the pin count. Reads the head the same fold wrote;
    `coverage`/`ordering` ride beside it so a partial or gapped state is
    never presented as settled truth."""
    s = head.state
    def opt(ref: str) -> str:
        return ((s.get("options") or {}).get(ref, {}).get("label")
                or _option_handle(ref, account_key=head.account_key,
                                  thread=head.thread))
    if head.kind == "poll":
        state = s.get("state") or "unknown"
        ballots = _poll_ballots(s)
        counts = s.get("option_counts") or {}
        q = s.get("question")
        parts = [f"poll {state}"]
        if q:
            parts.append(f"“{_clip(q, 60)}”")
        if counts:
            parts.append("totals " + ", ".join(
                f"{opt(k)}={v}" for k, v in list(counts.items())[:8]))
        if ballots:
            tally = _poll_subset_tally(ballots)
            parts.append("recorded subset " + ", ".join(
                f"{opt(k)}={v}" for k, v in list(tally.items())[:8])
                + f" ({len(ballots)} voter(s))")
        return _clip("; ".join(parts))
    if head.kind == "room":
        return _clip("room: " + (_fields_summary(s.get("fields") or {})
                                 or "no fields recorded"))
    if head.kind == "membership":
        members = s.get("members") or {}
        standing = _member_standing(members)
        covered = f" ({_grain_label(s['covered'])})" if s.get("covered") else ""
        parts = [f"{len(standing['member'])} member(s)"]
        if standing["pending"]:
            parts.append(f"{len(standing['pending'])} pending")
        if standing["departed"]:
            parts.append(f"{len(standing['departed'])} departed")
        if standing["unknown"]:
            parts.append(f"{len(standing['unknown'])} unknown-state")
        return _clip("membership: " + "; ".join(parts) + covered)
    if head.kind == "pin":
        return _clip(f"pin set: {len(s.get('pins') or ())} pin(s)")
    if head.kind == "artifact":
        arts = s.get("artifacts") or {}
        labels = [_artifact_entry_label("", a, name_of=name_of)
                  for a in list(arts.values())[:_ARTIFACT_ENTRIES_MAX]]
        more = (f" (+{len(arts) - len(labels)})"
                if len(arts) > len(labels) else "")
        return _clip("artifacts: " + (
            ", ".join(labels) + more if labels else "none recorded"))
    if head.kind == "message_metadata":
        return _clip("metadata: "
                     + ", ".join(sorted(s.get("fields") or {})) or "none")
    if head.kind == "reaction":
        counts = display_reactions(_merged_counts(s).items())
        shown = list(counts.items())[:REACTION_PAIRS_MAX]
        return _clip("reactions: " + (
            ", ".join(f"{k}={v}" for k, v in shown)
            + (" …" if len(counts) > len(shown) else "")
            or "none recorded"))
    return _clip(f"{head.kind}: {head.applied} observation(s) applied")


_NAMED_MAX = 24
_OPTIONS_MAX = 16


def _poll_ballots(state: dict[str, Any]) -> dict[str, list[str]]:
    """The retained per-voter selections that still cast a vote — a voter
    whose recorded selection is empty (a withdrawal, or a snapshot's
    covered empty selection) contributes no tally entry."""
    return {v: list(sel) for v, sel in (state.get("votes") or {}).items()
            if sel}


def _poll_subset_tally(ballots: dict[str, list[str]]) -> dict[str, int]:
    """The RECORDED-SUBSET option tally: one count per option per recorded
    voter — a multi-select voter counts toward each option it selected.
    This is the recorded subset only: never a source aggregate, never a
    sum of an aggregate and named voters."""
    tally: dict[str, int] = {}
    for selected in ballots.values():
        for ref in selected:
            tally[ref] = tally.get(ref, 0) + 1
    return dict(sorted(tally.items()))


def _merged_counts(state: dict[str, Any]) -> dict[str, int]:
    """A reaction head's per-emoji totals: attributed `standing` actors plus
    unattributed `counts` — the same accounting `merged_reaction_counts`
    gives the annotation, so both renders report ONE number. Under
    withheld coverage the union state carries empty standing/counts, so
    this renders nothing."""
    merged = {e: len(a) for e, a in (state.get("standing") or {}).items()}
    for emoji, n in (state.get("counts") or {}).items():
        merged[emoji] = merged.get(emoji, 0) + int(n)
    return merged


def head_detail(head: ObservationHead, *, name_of: Any = None) -> dict[str, Any]:
    """The object's folded state as a tool result's structured fields —
    the readable detail `head_summary`'s one line cannot carry: a poll's
    question and option labels, its lifecycle state and its recorded
    tally; a room's named fields; the member, pin and artifact counts.

    Person refs resolve through `name_of` the same way
    `observation_summary`'s do — a roster name or nothing, never a raw
    platform id. `coverage`/`ordering`/`observed_ms` ride beside it so a
    partial or gapped head never reads as settled truth."""
    s = head.state
    out: dict[str, Any] = {
        "kind": head.kind,
        "handle": handle_of(head),
        "ordering": head.ordering,
        "applied": head.applied,
        "summary": head_summary(head, name_of=name_of),
        #: Which clock `observed_ms` reads — a bridge-local or unknown
        #: source clock is stated, never read as an upstream time.
        "time_quality": head.observed_quality,
    }
    if head.observed_ms is not None:
        out["observed_ms"] = head.observed_ms
    received = head.received_ms
    if received and head.observed_quality != "upstream":
        out["received_ms"] = received
    rev = revision_label(head.position, head.scope)
    if rev:
        out["revision"] = rev
    coverage = {k: v for k, v in head.coverage.items()
                if k not in _FOLD_CLOCK_KEYS}
    if coverage.get("covered"):
        coverage["covered"] = _grain_label(coverage["covered"])
    if coverage:
        out["coverage"] = coverage
        label = coverage_label(coverage)
        if label:
            out["coverage_label"] = label
    if head.kind == "poll":
        out["state"] = s.get("state") or "unknown"
        if s.get("question"):
            out["question"] = _clip(s["question"], 240)
        options = s.get("options") or {}
        if options:
            # Option refs stay scoped opaque handles; labels are the
            # model-facing name. The list itself is bounded — a tall
            # option list cannot bypass the result's own budget.
            out["options"] = [
                {"option": _option_handle(
                    ref, account_key=head.account_key, thread=head.thread),
                 "label": _clip((e or {}).get("label") or "", 80)}
                for ref, e in list(options.items())[:_OPTIONS_MAX]
            ]
            if len(options) > _OPTIONS_MAX:
                out["options_more"] = len(options) - _OPTIONS_MAX
        counts = s.get("option_counts") or {}
        if counts:
            out["tally"] = [
                {"option": _option_handle(
                    ref, account_key=head.account_key, thread=head.thread),
                 "label": (s.get("options") or {}).get(ref, {}).get("label")
                          or "",
                 "count": n}
                for ref, n in list(counts.items())[:_OPTIONS_MAX]
            ]
            if len(counts) > _OPTIONS_MAX:
                out["tally_more"] = len(counts) - _OPTIONS_MAX
        if s.get("voter_count") is not None:
            out["voter_count"] = s["voter_count"]
        ballots = _poll_ballots(s)
        if ballots:
            subset = _poll_subset_tally(ballots)
            # The recorded subset: named-voter selections as their own
            # labelled tally — never the upstream total and never summed
            # with a source aggregate.
            out["recorded_tally"] = [
                {"option": _option_handle(
                    ref, account_key=head.account_key, thread=head.thread),
                 "label": (s.get("options") or {}).get(ref, {}).get("label")
                          or "",
                 "count": n}
                for ref, n in list(subset.items())[:_OPTIONS_MAX]]
            if len(subset) > _OPTIONS_MAX:
                out["recorded_tally_more"] = len(subset) - _OPTIONS_MAX
            out["recorded_voters"] = len(ballots)
            out["recorded_selections"] = sum(
                len(v) for v in ballots.values())
            named = [n for v in ballots
                     if (n := name_of(v) if name_of is not None else None)]
            if named:
                out["voters_named"] = named[:_NAMED_MAX]
                if len(named) > _NAMED_MAX:
                    out["voters_named_more"] = len(named) - _NAMED_MAX
            out["voters_unnamed"] = len(ballots) - len(named)
        if s.get("anonymous") is not None:
            out["anonymous"] = s["anonymous"]
        if s.get("counts_complete") is not None:
            out["counts_complete"] = bool(s.get("counts_complete"))
        return out
    if head.kind == "room":
        fields = s.get("fields") or {}
        out["fields"] = {
            k: _field_projection(k, v, name_of=name_of)
            for k, v in list(fields.items())[:32]
            if isinstance(v, (str, int, float, bool)) or v is None
        }
        # Structured field values (permission sets, participant maps) are
        # reported by name only — their contents can carry member ids.
        complex_fields = [k for k, v in fields.items()
                          if not isinstance(v, (str, int, float, bool, type(None)))]
        if complex_fields:
            out["fields_structured"] = sorted(complex_fields)[:32]
        return out
    if head.kind == "membership":
        members = s.get("members") or {}
        standing = _member_standing(members)
        active = standing["member"]
        out["member_count"] = len(active)
        named = [n for m in active
                 if (n := name_of(m) if name_of is not None else None)]
        if named:
            out["members_named"] = named[:_NAMED_MAX]
            if len(named) > _NAMED_MAX:
                out["members_named_more"] = len(named) - _NAMED_MAX
        out["members_unnamed"] = len(active) - len(named)
        for bucket in ("pending", "departed"):
            subjects = standing[bucket]
            if not subjects:
                continue
            out[f"{bucket}_count"] = len(subjects)
            names = [n for m in subjects
                     if (n := name_of(m) if name_of is not None else None)]
            if names:
                out[f"{bucket}_named"] = names[:_NAMED_MAX]
        if standing["unknown"]:
            out["unknown_state_count"] = len(standing["unknown"])
        if s.get("covered"):
            out["covered"] = _grain_label(s["covered"])
        return out
    if head.kind == "pin":
        out["pin_count"] = len(s.get("pins") or {})
        return out
    if head.kind == "artifact":
        arts = s.get("artifacts") or {}
        out["artifact_count"] = len(arts)
        kinds = sorted({(a or {}).get("artifact_kind") or ""
                        for a in arts.values()} - {""})
        if kinds:
            out["artifact_kinds"] = kinds[:16]
        entries = [
            {"object": object_handle(
                "artifact", ref, account_key=head.account_key,
                thread=head.thread),
             **({"kind": (e or {}).get("artifact_kind")}
                if (e or {}).get("artifact_kind") else {}),
             **({"custody": (e or {}).get("custody")}
                if (e or {}).get("custody") else {}),
             **({"preview": True} if (e or {}).get("preview") else {}),
             **({"perception": (e or {}).get("perception_ref")}
                if (e or {}).get("perception_ref") else {}),
             **_artifact_meta_detail(e or {}, name_of=name_of)}
            for ref, e in list(arts.items())[:_ARTIFACT_ENTRIES_MAX]
        ]
        if entries:
            out["artifacts"] = entries
            if len(arts) > _ARTIFACT_ENTRIES_MAX:
                out["artifacts_more"] = len(arts) - _ARTIFACT_ENTRIES_MAX
        return out
    if head.kind == "reaction":
        counts = display_reactions(_merged_counts(s).items())
        shown = dict(list(counts.items())[:REACTION_PAIRS_MAX])
        if shown:
            out["counts"] = shown
            if len(counts) > len(shown):
                out["counts_more"] = len(counts) - len(shown)
        if s.get("snapshot"):
            out["snapshot"] = True
        reactors = [r for r in (s.get("reactors") or []) if r]
        named = [n for r in reactors
                 if (n := name_of(r) if name_of is not None else None)]
        if named:
            out["reactors_named"] = named[:_NAMED_MAX]
            if len(named) > _NAMED_MAX:
                out["reactors_named_more"] = len(named) - _NAMED_MAX
        unnamed = len(reactors) - len(named)
        if unnamed:
            out["reactors_unnamed"] = unnamed
        return out
    if head.kind == "message_metadata":
        fields = s.get("fields") or {}
        out["fields"] = {
            k: _field_projection(k, v, name_of=name_of)
            for k, v in list(fields.items())[:16]
            if k != "shared_refs" and
            isinstance(v, (str, int, float, bool, type(None)))
        }
        if fields.get("shared_refs"):
            # Shared-object refs are platform identities — counted, never
            # listed raw.
            out["shared_refs"] = len(fields["shared_refs"])
        return out
    return out
