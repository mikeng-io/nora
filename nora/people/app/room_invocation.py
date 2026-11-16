"""One authenticated in-room management invocation, for both entries.

`design/managed-identity-access/management.md` «In-room management»: a
delegated operator asks Nora, in the room itself, to register one referenced
platform account or to add/remove one person's scoped membership. Two entry
surfaces land on the same invocation here:

- the explicit `!!!member` room command (readable, replyable), and
- the `member_update` model tool — same intent, same enforcement.

Usage:
    !!!member register            — register the account this message
                                    @-mentions, under a new independent person
    !!!member register <account>  — register one exact platform account
    !!!member add @who            — add the referenced person to this room
    !!!member remove @who         — remove the referenced person from this room

A request carries the intent alone. Everything authority-relevant comes off
the verified `NormalizedMessage`: the actor is the transport-verified
sender resolved to a canonical person NOW, the scope is the room the
message actually arrived in, and the operation id is the message's own key,
so a retried delivery replays instead of duplicating. A body/model/quote
can never assert any of those — the model tool cannot even name them.

The invocation authenticates through the one existing gate —
`HumanManagement` — under a `RoomAuthentication`: no session, no login
method, no binding claim (a platform message is not an OAuth or code
login); the gate resolves the current binding, lifecycle, holds and scoped
authority live inside `mutation()` at commitment, so a delegation revoked
between the message and the write refuses there, not here.

Nothing here falls back to file mode: without a managed service graph the
invocation is an authoritative `denied`, matching `IdentityAccess` mode
honesty.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from nora.domain.authentication import RoomAuthentication
from nora.domain.identity import Identifier, UnmatchableIdentifierError, id_kinds
from nora.domain.managed import (
    CheckResult,
    Outcome,
    OutcomeStatus,
    denied,
    room_scope_id,
    unavailable,
)
from nora.domain.management import (
    AccountRegistration,
    Actor,
    ActorKind,
    MembershipStanding,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.domain.models import MENTION_PLACEHOLDER, NormalizedMessage
from nora.domain.ports import RosterSource
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_ops import _scope_authority
from nora.people.app.management_invocation import HumanManagement

#: The room-management command verb — `!!!member <verb> [reference]`.
ROOM_COMMAND = "!!!member"

RequestKind = Literal["register", "add", "remove"]

_USAGE = (
    "try `!!!member register <account or @who>` to register one account, "
    "or `!!!member add @who` / `!!!member remove @who` for room membership"
)

_KIND_VERBS = {"register", "add", "remove"}

#: Authoritative refusals rendered for the room — detail → the reply that
#: says WHY without quoting store internals back.
_DENIED_REPLIES = {
    "file-mode": "I can't do that here — this room isn't on managed identity",
    "not-a-fresh-message": "that message isn't a fresh instruction — send it as a new message",
    "unknown-scope": "I don't know this room's authority scope — nothing applied",
    "sender-unregistered": "your account isn't registered, so I can't take room commands from you",
    "operator-not-authorized": "you don't have room-management authority here",
    "authentication-changed": "your account changed under me — send that again",
    "unscoped-operation": "that command lost its room scope — nothing applied",
    "unsupported-human-invocation": "that isn't a room-management request I can take",
    "account-already-registered": "that account is already registered",
    "identifier-conflict": "that account is already bound — nothing applied",
    "operation-conflict": "that message already did something else — nothing applied",
    "stale-ownership-revision": "the account's binding moved under me — try again",
}

_UNAVAILABLE_REPLIES = {
    "topology-unavailable": "I can't read the room topology — try again",
    "bindings-unavailable": "I couldn't reach the identity store — try again",
    "authority-unavailable": "I couldn't check your authority just now — try again",
    "authentication-unavailable": "I couldn't verify your account just now — try again",
    "store-failure": "the identity store failed — nothing applied, try again",
}


@dataclass(frozen=True)
class RoomRequest:
    """The scoped-management intent both entries submit — never the
    authority context, which only `invoke` derives from the message.

    `kind`: `register` a referenced unbound account as a new independent
    person, `add`/`remove` a person's membership in THIS room.
    `reference`: the target as the caller named it — an @-mention marker
    (`None`), a platform account id, or (membership only) a name the room
    resolves. `reference_kind` says which of those three it is; it is a
    resolution hint, not authority.
    """

    kind: RequestKind
    reference: str | None = None
    reference_kind: Literal["mention", "account", "name"] = "mention"


@dataclass(frozen=True)
class Clarify:
    """No effect was submitted — the request cannot name exactly one
    target, and a consequential scope/target never gets guessed at.

    `status` is the tool result's axis: `ambiguous` when resolution could
    not pick one target (several candidates, or none named yet),
    `nothing_found` when the named target is genuinely absent — the two
    are never interchangeable, same as R-27's reads.
    """

    question: str
    status: Literal["ambiguous", "nothing_found"] = "ambiguous"


def is_room_command(message: NormalizedMessage) -> bool:
    """Whether this fresh transport message IS a `!!!member` command —
    the dispatcher's early predicate, before any engage decision.

    Only a fresh message's own body can invoke: an edit carries the new
    text but is not a fresh instruction, a touch/reaction is not a message,
    and a quote is metadata — its content is never the body checked here.
    """
    if message.edit_of is not None or message.touch is not None:
        return False
    body = message.body.lstrip()
    return (
        body[: len(ROOM_COMMAND)].lower() == ROOM_COMMAND
        and (len(body) == len(ROOM_COMMAND) or body[len(ROOM_COMMAND)].isspace())
    )


def parse_room_command(message: NormalizedMessage) -> RoomRequest | str | None:
    """`None` — not a room command; `str` — malformed (the usage reply);
    `RoomRequest` — the intent `invoke` runs."""
    if not is_room_command(message):
        return None
    tokens = message.body.strip().split()
    if len(tokens) < 2 or tokens[1].lower() not in _KIND_VERBS:
        return _USAGE
    kind = tokens[1].lower()
    reference = " ".join(tokens[2:]).strip() or None
    if reference is None or reference == MENTION_PLACEHOLDER:
        return RoomRequest(kind=kind)  # the message's own @-mention
    if reference.startswith("@"):
        reference = reference[1:].strip()
        if not reference:
            return _USAGE
        # A real platform mention renders as U+FFFC/@-form and rides in
        # `mentions`; a typed "@name" with no mention is a name lookup.
        if message.mentions:
            return RoomRequest(kind=kind, reference=reference,
                               reference_kind="mention")
        return RoomRequest(kind=kind, reference=reference, reference_kind="name")
    if _guess_kind(message.platform, reference) is not None:
        return RoomRequest(kind=kind, reference=reference, reference_kind="account")
    return RoomRequest(kind=kind, reference=reference, reference_kind="name")


def _guess_kind(platform: str, value: str) -> str | None:
    """Which declared id kind `value` looks like on `platform`, or `None`
    when the shape names none — the caller then treats it as a name."""
    kinds = id_kinds(platform)
    if re.fullmatch(r"\+\d{4,}", value) and "phone" in kinds:
        return "phone"
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value,
    ) and "uuid" in kinds:
        return "uuid"
    if value.isdigit():
        for kind in ("user_id", "lid", "phone"):
            if kind in kinds:
                return kind
    if len(kinds) == 1:
        return next(iter(kinds))
    return None


def _typed_forms(platform: str, values: frozenset[str]) -> frozenset[Identifier]:
    """The matchable `Identifier`s one raw id-form set carries — skipping
    shapes no declared kind accepts rather than guessing one."""
    typed = set()
    for value in values:
        kind = _guess_kind(platform, value)
        if kind is None:
            continue
        try:
            typed.add(Identifier(platform=platform, kind=kind, value=value))
        except UnmatchableIdentifierError:
            continue
    return frozenset(typed)


class RoomManagement:
    """The in-room management application: message + intent → the ONE
    authenticated invocation both the `!!!member` command and the
    `member_update` tool submit."""

    def __init__(
        self,
        services: ManagedServices | None,
        *,
        rosters: dict[str, RosterSource] | None = None,
    ) -> None:
        self._services = services
        self._rosters = rosters or {}
        self._human = (
            HumanManagement(
                services.bindings, services.registry,
                services.registration, services.authority,
            )
            if services is not None
            else None
        )

    def is_command(self, message: NormalizedMessage) -> bool:
        """Whether this message IS a `!!!member` command — the predicate
        the dispatcher is handed at composition."""
        return is_room_command(message)

    def parse(self, message: NormalizedMessage) -> RoomRequest | str | None:
        """The message's intent — `None`/`str`/`RoomRequest`, as
        `parse_room_command` defines."""
        return parse_room_command(message)

    @staticmethod
    def render(result: Outcome | Clarify) -> str:
        """The reply one `invoke` result speaks — the command surface's
        human text. The tool renders the same outcome structurally."""
        if isinstance(result, Clarify):
            return result.question
        if result.status is OutcomeStatus.COMPLETE:
            detail = result.detail or ""
            if detail.startswith("person_"):
                return "registered — they're a member of this room now"
            return "done"
        if result.status is OutcomeStatus.DENIED:
            return _DENIED_REPLIES.get(
                result.detail, f"sorry, I can't do that ({result.detail})"
            )
        if result.status is OutcomeStatus.INCOMPLETE:
            return (
                "that only partly applied — the journal will finish it, but "
                "tell the operator"
            )
        return _UNAVAILABLE_REPLIES.get(
            result.detail, "I couldn't reach the identity store — try again"
        )

    async def invoke(
        self, message: NormalizedMessage, request: RoomRequest
    ) -> Outcome | Clarify:
        services = self._services
        if (
            services is None
            or self._human is None
            or services.access.is_file_backend
        ):
            # File mode is an authoritative refusal, never a pretend apply —
            # the same answer `IdentityAccess` gives managed mutators.
            return denied("file-mode")
        if message.edit_of is not None or message.touch is not None:
            return denied("not-a-fresh-message")

        # The scope is the room the message actually arrived in — topology
        # must confirm it exists, never a caller-supplied value.
        scope = Scope(
            ScopeKind.ROOM,
            room_scope_id(message.platform, message.conversation_id),
        )
        try:
            exists = await services.topology.scope_exists(scope)
        except Exception:  # noqa: BLE001 — an unreadable topology fails closed
            return unavailable("topology-unavailable")
        if not exists:
            return denied("unknown-scope")

        # The actor is the transport-verified sender, resolved live to the
        # canonical person — never a name, mention or body assertion.
        try:
            bound = await services.bindings.resolve(
                message.platform, message.sender_id
            )
        except Exception:  # noqa: BLE001
            return unavailable("bindings-unavailable")
        if bound is None:
            return denied("sender-unregistered")
        try:
            person_id = await services.bindings.canonical_person(bound.person_id)
        except Exception:  # noqa: BLE001
            return unavailable("bindings-unavailable")
        if person_id is None:
            return denied("sender-unregistered")
        sender_account = next(
            (i for i in bound.identifiers if i.value == message.sender_id),
            min(bound.identifiers, key=lambda i: (i.kind, i.value)),
        )

        # The honest `authority` observation the operation carries — the
        # live `check` inside the gate is the enforcement; this pins the
        # digest the journal re-reads.
        digest = await _scope_authority(services.backend, scope)
        if isinstance(digest, CheckResult):
            return unavailable(digest.detail or "authority-unavailable")

        actor = Actor(ActorKind.OPERATOR, person_id=person_id, scope=scope)
        key = message.message_id or f"{message.sender_id}:{message.timestamp}"
        operation_id = f"room:{message.platform}:{message.conversation_id}:{key}"

        if request.kind == "register":
            resolved = await self._account_target(message, request)
            if isinstance(resolved, (Outcome, Clarify)):
                return resolved
            identifiers = resolved
            new_person = "person_" + hashlib.sha256(
                "\n".join(sorted(f"{i.platform}:{i.kind}:{i.value}" for i in identifiers))
                .encode()
            ).hexdigest()[:20]
            target_id = next(
                (i for i in identifiers if request.reference
                 and i.value == request.reference),
                min(identifiers, key=lambda i: (i.kind, i.value)),
            )
            operation = Operation(
                operation_id=operation_id,
                kind="register_account",
                actor=actor,
                target=Target(TargetKind.ACCOUNT, account=target_id),
                payload=AccountRegistration(
                    person_id=new_person, scope=scope, identifiers=identifiers,
                ),
                observed_revisions={"ownership": "unbound",
                                    "authority": digest},
            )
        else:
            target_person = await self._person_target(message, request)
            if isinstance(target_person, (Outcome, Clarify)):
                return target_person
            operation = Operation(
                operation_id=operation_id,
                kind="set_membership",
                actor=actor,
                target=Target(TargetKind.MEMBERSHIP,
                              person_id=target_person, scope=scope),
                payload=MembershipStanding(member=request.kind == "add"),
                observed_revisions={"authority": digest},
            )
        authentication = RoomAuthentication(
            issuer=message.platform,
            message_key=operation_id,
            account=sender_account,
            authenticated_at=datetime.fromtimestamp(
                message.timestamp / 1000, tz=UTC
            ),
        )
        return await self._human.apply(operation, authentication)

    # -- target resolution -------------------------------------------------

    async def _account_forms(
        self, message: NormalizedMessage
    ) -> tuple[frozenset[Identifier], ...]:
        """Each `mentions` value widened to its account's full id forms
        when the roster can say which ids are one account."""
        members: tuple[frozenset[str], ...] = ()
        roster = self._rosters.get(message.platform)
        if roster is not None:
            try:
                read = await roster.roster(conversation_id=message.conversation_id)
                if read.available:
                    members = read.members
            except Exception:  # noqa: BLE001 — pairing is best-effort
                members = ()
        forms: list[frozenset[Identifier]] = []
        for value in sorted(message.mentions):
            grouped = next((m for m in members if value in m), frozenset({value}))
            typed = _typed_forms(message.platform, grouped)
            if typed:
                forms.append(typed)
        return tuple(forms)

    async def _account_target(
        self, message: NormalizedMessage, request: RoomRequest
    ) -> frozenset[Identifier] | Outcome | Clarify:
        """The one unbound account a register targets, or why it cannot."""
        if request.reference_kind == "mention":
            forms = await self._account_forms(message)
            if not forms:
                return Clarify(
                    "which account should I register — @-mention them, or "
                    "give me their exact platform id?"
                )
            if len(forms) > 1:
                return Clarify(
                    "that mentions more than one account — one at a time, "
                    "please"
                )
            identifiers = forms[0]
        else:
            typed = _typed_forms(
                message.platform, frozenset({request.reference or ""})
            )
            if not typed:
                return Clarify(
                    "I can't read that as a platform account — @-mention "
                    "them or give the exact platform id"
                )
            identifiers = typed
        try:
            for ident in identifiers:
                if await self._services.bindings.resolve(
                    ident.platform, ident.value
                ) is not None:
                    return denied("account-already-registered")
        except Exception:  # noqa: BLE001
            return unavailable("bindings-unavailable")
        return identifiers

    async def _person_target(
        self, message: NormalizedMessage, request: RoomRequest
    ) -> str | Outcome | Clarify:
        """The one canonical person a membership change targets."""
        services = self._services
        if services is None:  # unreachable — invoke denies file mode first
            return unavailable("bindings-unavailable")
        people: set[str] = set()
        if request.reference_kind in ("mention", "account"):
            values = (
                message.mentions
                if request.reference_kind == "mention"
                else frozenset({request.reference or ""})
            )
            try:
                for value in sorted(values):
                    bound = await services.bindings.resolve(
                        message.platform, value
                    )
                    if bound is None:
                        continue
                    canonical = await services.bindings.canonical_person(
                        bound.person_id
                    )
                    if canonical is not None:
                        people.add(canonical)
            except Exception:  # noqa: BLE001
                return unavailable("bindings-unavailable")
            if not people:
                return Clarify(
                    "I couldn't find that person in the room — @-mention "
                    "them or give their exact platform id (if the account "
                    "is new here, register it first)",
                    status="nothing_found",
                )
        else:
            try:
                refs = await services.access.room_refs(
                    message.platform, message.conversation_id
                )
            except Exception:  # noqa: BLE001
                return unavailable("bindings-unavailable")
            name = (request.reference or "").strip().lower()
            exact = [r for r in refs if (r.name or "").lower() == name]
            partial = exact or [
                r for r in refs
                if name and name in (r.name or "").lower()
            ]
            if len(partial) == 0:
                return Clarify(
                    f"nobody called {request.reference!r} in this room — "
                    "@-mention them or give the exact platform id",
                    status="nothing_found",
                )
            if len(partial) > 1:
                return Clarify(
                    f"{request.reference!r} could be "
                    + " or ".join(r.name or r.person_id for r in partial)
                    + " — which one?"
                )
            people = {partial[0].person_id}
        if len(people) > 1:
            return Clarify(
                "that refers to more than one person — one at a time, please"
            )
        return next(iter(people))
