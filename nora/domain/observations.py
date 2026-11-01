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

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from nora.domain.models import EditTarget

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
            object.__setattr__(self, "selected", tuple(self.selected))
        if self.voters is not None:
            object.__setattr__(
                self, "voters",
                tuple((str(v), tuple(o)) for v, o in dict(self.voters).items()))

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
    """An `Observation` as persisted: its identity, resolution and arrival."""

    id: int
    event_id: str
    source_digest: str
    observation: Observation
    target_message_id: int | None = None


@dataclass(frozen=True)
class ObservationHead:
    """One object's folded current state — `platform_object_state`'s shape.

    `position`/`scope` name the newest comparable source position applied.
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
    observed_ms: int | None = None        # newest source time applied
    ordering: str = "arrival"             # ordered | arrival | gap
    coverage: dict[str, Any] = field(default_factory=dict)


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
        head = ObservationHead(kind=obs.object_kind, object_ref=obs.object_ref,
                               state=_initial_state(obs.object_kind))
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
        if obs.source_position is not None:
            ordering = "ordered"
        elif ordering == "arrival" and (head.position is not None
                                        or obs.source_position is not None):
            coverage["unordered"] = True
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

    observed = head.observed_ms
    if obs.occurred_ms is not None and (
        observed is None or obs.occurred_ms > observed
    ):
        observed = obs.occurred_ms
    position, scope = head.position, head.scope
    if obs.source_position is not None and obs.revision_scope and (
        position is None or cmp > 0
    ) and (position is None or obs.revision_scope == scope):
        position, scope = obs.source_position, obs.revision_scope
    return ObservationHead(
        kind=head.kind, object_ref=head.object_ref, state=state,
        applied=head.applied + (1 if applied else 0),
        head_id=row.id if applied else head.head_id,
        position=position, scope=scope, observed_ms=observed,
        ordering=ordering, coverage=coverage)


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
        if p.option_counts:
            counts = dict(state["option_counts"])
            for ref, n in p.option_counts:
                counts[ref] = n
            state["option_counts"] = counts
        if p.voter_count is not None:
            state["voter_count"] = p.voter_count
        state["counts_complete"] = bool(obs.coverage.complete)
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
        emojis = p.covered_emojis or tuple(standing.keys())
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
    if op == "add" and p.message is not None:
        pins[_target_key(p.message)] = {
            "actor": obs.actor_id or None,
            "pinned_ms": obs.occurred_ms,
            "time_quality": obs.time_quality}
    elif op == "remove" and p.message is not None:
        pins.pop(_target_key(p.message), None)
    elif op == "snapshot":
        if obs.coverage.complete:
            pins = {}
        for t in p.pins:
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
        md.update(p.metadata)
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
    state: str = ""                 # head summary when a detail read asks
    ordering: str = ""              # head's ordered|arrival|gap, when shown


def object_handle(kind: str, object_ref: str) -> str:
    """The model-safe name of an object: `<kind>:<12 hex of its ref>`.
    Deterministic, so a handle a tool handed out resolves on the next read
    of the same scope — and an opaque digest, so a platform's own object id
    never reaches a prompt or a tool result (R-18)."""
    import hashlib
    digest = hashlib.sha256(f"{kind}:{object_ref}".encode()).hexdigest()[:12]
    return f"{kind}:{digest}"


_SUMMARY_MAX = 160


def _clip(text: str, limit: int = _SUMMARY_MAX) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fields_summary(fields: dict[str, Any]) -> str:
    out = []
    for name in sorted(fields):
        value = fields[name]
        if value is None:
            out.append(f"{name} cleared")
        else:
            out.append(f"{name} set to {_clip(value, 60)}")
    return "; ".join(out)


def observation_summary(obs: Observation) -> str:
    """The change one observation reports, as one bounded fact line — pure
    (a summary of the payload, never a conclusion about it)."""
    p = obs.payload
    op = obs.operation
    if isinstance(p, PollPayload):
        if p.question:
            tail = f"“{_clip(p.question, 80)}”"
            opts = f" with {len(p.options)} options" if p.options else ""
            return _clip(f"poll {op}: {tail}{opts}")
        if p.voter_ref or p.option_ref or p.selected is not None:
            choice = ", ".join(p.selected) if p.selected else (p.option_ref or "")
            who = "a voter" if p.anonymous else (p.voter_ref or "a voter")
            return _clip(f"poll vote {op}: {who} on {choice or 'an option'}")
        if p.state:
            return _clip(f"poll {op}: state {p.state}"
                         + (f", {p.voter_count} votes"
                            if p.voter_count is not None else ""))
        if p.option_counts:
            return _clip(f"poll {op}: totals "
                         + ", ".join(f"{k}={v}" for k, v in p.option_counts))
        return f"poll {op}"
    if isinstance(p, RoomPayload):
        return _clip(f"room {op}: {_fields_summary(p.fields)}")
    if isinstance(p, MembershipPayload):
        if p.members:
            return _clip(f"membership {op}: {len(p.members)} members "
                         f"reported ({p.covered or 'set'})")
        role = f" as {p.role}" if p.role else ""
        state = f" ({p.state})" if p.state else ""
        return _clip(f"membership {op}: {obs.subject_ref or 'a member'}"
                     f"{role}{state}")
    if isinstance(p, PinPayload):
        if p.pins:
            return _clip(f"pin set {op}: {len(p.pins)} pins reported")
        return f"pin {op}: a message pinned" if op == "add" else \
            f"pin {op}: a message unpinned"
    if isinstance(p, ArtifactPayload):
        return _clip(f"artifact {op}: {p.artifact_kind or 'object'} "
                     f"({p.custody or 'custody unknown'})")
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
        return _clip(f"reaction {op}: {p.emoji or '?'}")
    return f"{obs.object_kind} {op}"


def head_summary(head: ObservationHead) -> str:
    """The object's folded CURRENT state as one bounded fact line —
    a poll's open/closed and recorded tallies, a room's named fields, a
    member count, the pin count. Reads the head the same fold wrote;
    `coverage`/`ordering` ride beside it so a partial or gapped state is
    never presented as settled truth."""
    s = head.state
    if head.kind == "poll":
        state = s.get("state") or "unknown"
        votes = s.get("votes") or {}
        counts = s.get("option_counts") or {}
        q = s.get("question")
        parts = [f"poll {state}"]
        if q:
            parts.append(f"“{_clip(q, 60)}”")
        if votes:
            parts.append(f"{len(votes)} recorded vote(s)")
        if counts:
            parts.append("totals "
                         + ", ".join(f"{k}={v}" for k, v in counts.items()))
        return _clip("; ".join(parts))
    if head.kind == "room":
        return _clip("room: " + (_fields_summary(s.get("fields") or {})
                                 or "no fields recorded"))
    if head.kind == "membership":
        members = s.get("members") or {}
        return _clip(f"membership: {len(members)} member(s) recorded")
    if head.kind == "pin":
        return _clip(f"pin set: {len(s.get('pins') or ())} pin(s)")
    if head.kind == "artifact":
        return _clip(f"artifacts: {len(s.get('artifacts') or {})} recorded")
    if head.kind == "message_metadata":
        return _clip("metadata: "
                     + ", ".join(sorted(s.get("fields") or {})) or "none")
    if head.kind == "reaction":
        counts = s.get("counts") or {}
        return _clip("reactions: "
                     + ", ".join(f"{k}={v}" for k, v in counts.items())
                     or "none recorded")
    return _clip(f"{head.kind}: {head.applied} observation(s) applied")
