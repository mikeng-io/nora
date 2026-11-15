"""The selected backend's identity+access read surface.

Every consumer asks ONE object — `ManagedServices.access`, built by
`nora.people.wire` for whichever backend the deployment selected —
instead of reading `identity.toml`/`policy.toml` directly
(`design/managed-identity-access/`).

Identity and grant reads delegate to the selected `BindingStore`,
`AuthorityService` and `TopologySource`, so dynamic registrations,
canonical aliases, current suspension/revocation and a managed outage
behave the same at every protection point: an unavailable authority is
`UNAVAILABLE`, never a file fallback. Descriptive profile facts that have
no managed source yet — social opt-outs, Instagram username metadata,
Life360 circles — answer their file truth only when `file_persons` is
given (file mode) and an honest absence otherwise; a managed deployment
never reads file people or grants as authority. File room topology keeps
its own authority in both modes (`authorization.md`), so `Policy` stays
the source for room shape and the open-admission flag.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.managed import (
    CheckResult,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    PersonLifecycle,
    allowed,
    check_denied,
    room_scope_id,
    split_room_scope_id,
    unknown_scope,
)
from nora.domain.managed_ports import BindingStore, TopologySource
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import (
    ConversationTarget,
    NormalizedMessage,
    PersonRef,
    TranscriptRow,
)
from nora.persons import (
    Life360Membership,
    Persons,
    _target_from_ids,
    direct_account_target,
    sanitize_unmapped,
)
from nora.policy import Policy

if TYPE_CHECKING:
    # Annotations only: this root module must not import `nora.people` —
    # `nora.people.app.managed` already imports THIS module, and the root
    # package init would close a cycle through the adapter layer.
    AuthorityService = Any


def _ref(person: ManagedPerson) -> PersonRef:
    """The name view of a managed person (aliases are file-only data)."""
    return PersonRef(
        person_id=person.person_id,
        name=person.display_name or person.person_id,
        honcho_peer_id=person.person_id,
    )


class _EmptyPersons:
    """The Persons-shaped descriptive surface managed mode answers empty.

    Aliases, handle maps, Life360 membership, `honcho_peer_id` and the
    `persons = [...]` room lists are file identity data the managed schema
    does not carry. A sync consumer (the dream adaptors' name/former-id
    callbacks, the character-key listings) cannot await the binding store,
    so it gets this: every read absent, never a stale file answer. Person
    enumeration being empty also means no person-scoped profile/memory
    writes land under a managed deployment — under-reporting, never a
    wrong owner.
    """

    by_person: Mapping[str, PersonRef] = {}
    life360: tuple[Life360Membership, ...] = ()

    def resolve(self, platform: str, value: str) -> None:
        return None

    def account_for(self, platform: str, kind: str, value: str) -> None:
        return None

    def direct_addresses(self, platform: str) -> frozenset[str]:
        return frozenset()

    def display_name(self, msg: Any) -> str:
        return msg.sender_name or sanitize_unmapped(msg.sender_id)

    def name_transcript(
        self, transcript: Any, *, platform: str
    ) -> Any:
        return tuple(transcript)

    def former_ids_for(self, person_id: str) -> tuple[str, ...]:
        return ()

    def subject_id_for(self, person_id: str) -> str:
        return person_id

    def platform_ids(self, platform: str, person_id: str) -> frozenset[str]:
        return frozenset()

    def room_members(self, listed: Any) -> tuple[PersonRef, ...]:
        return ()

    def social_off(self, person_id: str) -> bool:
        return True  # never volunteered: absent data opts OUT, not in

    def social_off_ids(self, among: Any) -> frozenset[str]:
        return frozenset(among) if among else frozenset()

    def instagram_handle(self, fbid: str) -> None:
        return None

    def instagram_igids(self, person_id: str) -> frozenset[str]:
        return frozenset()

    def instagram_username_owners(self, handle: str) -> frozenset[str]:
        return frozenset()


EMPTY_PERSONS = _EmptyPersons()


class IdentityAccess:
    """One read surface over the selected identity/access backend.

    `file_persons`/`file_policy` are populated by `file_services` only —
    in managed mode they are `None` and the descriptive reads they would
    back answer an honest absence. Identity reads come from `bindings`
    (the selected `BindingStore`); grant reads go through the shared
    `AuthorityService.check` path so canonical folding, `identity_held`,
    revocation blocks, scope existence, lifecycle and the grant engine
    apply identically everywhere.
    """

    def __init__(
        self,
        *,
        bindings: BindingStore | None = None,
        authority: AuthorityService | None = None,
        topology: TopologySource | None = None,
        file_persons: Persons | None = None,
        file_policy: Policy | None = None,
    ) -> None:
        if file_persons is None and any(value is None for value in (bindings, authority, topology)):
            raise ValueError("managed identity reads require all authority collaborators")
        if file_policy is not None and file_persons is None:
            raise ValueError("file authority requires its file identity source")
        self._bindings = cast(BindingStore, bindings)
        self._authority = cast(Any, authority)
        self._topology = cast(TopologySource, topology)
        self._persons = file_persons
        self._policy = file_policy
        # Alias snapshot for the SYNC consumers (`former_ids_for`,
        # `subject_id_for`); `refresh_aliases` reloads on a TTL — file mode
        # reads live `Persons`, and empty is an honest absence.
        self._alias_map: dict[str, tuple[str, ...]] = {}
        self._alias_canonical: dict[str, str] = {}
        self._alias_ts = 0.0

    def persons_view(self) -> Any:
        """The descriptive `Persons`-shaped read for sync consumers —
        the loaded file index under the file backend, `EMPTY_PERSONS`
        under managed (file data is never the managed fallback)."""
        return self._persons if self._persons is not None else EMPTY_PERSONS

    @property
    def is_file_backend(self) -> bool:
        return self._persons is not None

    @classmethod
    def for_files(cls, persons: Persons, policy: Policy | None) -> IdentityAccess:
        """The file backend's access surface — what `file_services` builds
        (and the honest default wherever a caller only holds the loaded
        files). No `nora.people` parts: every file-mode read below calls
        `Persons`/`Policy` directly — the same calls `FileBindingStore`,
        `FileAuthorityBackend` and `FileTopologySource` made — so file
        semantics hold verbatim and this module stays importable by
        `nora.people.app` itself."""
        return cls(file_persons=persons, file_policy=policy)

    # ------------------------------------------------------------------
    # exact-account identity
    # ------------------------------------------------------------------

    async def resolve_account(
        self, platform: str, value: str
    ) -> BoundAccount | None:
        """The bound account any matchable `(platform, value)` names, or None."""
        if self._persons is not None:
            return self._persons.account_for(platform, value)
        return await self._bindings.resolve(platform, value)

    async def account_for(
        self, platform: str, kind: str, value: str
    ) -> BoundAccount | None:
        if self._persons is not None:
            account = self._persons.account_for(platform, value)
            if account is None:
                return None
            # The file's index is flat (platform, value): confirm the
            # account really carries an identifier of THIS kind.
            ident = Identifier(platform=platform, kind=kind, value=value)
            return account if ident in account.identifiers else None
        return await self._bindings.account_for(platform, kind, value)

    async def accounts_for(
        self, person_id: str, platform: str | None = None
    ) -> tuple[BoundAccount, ...]:
        if self._persons is not None:
            accounts = self._persons.accounts_for(person_id)
        else:
            accounts = await self._bindings.accounts_for(person_id)
        return tuple(
            a for a in accounts if platform is None or a.platform == platform
        )

    async def platform_ids(self, platform: str, person_id: str) -> frozenset[str]:
        """Every matchable id value the person presents on `platform`."""
        if self._persons is not None:
            return self._persons.platform_ids(platform, person_id)
        return frozenset(
            i.value
            for a in await self._bindings.accounts_for(person_id)
            if a.platform == platform
            for i in a.identifiers
        )

    async def direct_target(
        self, platform: str, person_id: str
    ) -> ConversationTarget | None:
        """The 1:1 conversation with this person on `platform` — the same
        id-ranking the file backend applies, over bound accounts."""
        return _target_from_ids(
            platform, await self.platform_ids(platform, person_id)
        )

    async def direct_addresses(self, platform: str) -> frozenset[str]:
        """Every direct-message address on `platform` belonging to a known
        ACTIVE person — the outbound DM allowlist."""
        if self._persons is not None:
            return self._persons.direct_addresses(platform)
        out: set[str] = set()
        for person in await self._bindings.persons():
            # `persons()` is the ACTIVE roster — a suspended person's direct
            # address is not a sendable target.
            for account in await self._bindings.accounts_for(person.person_id):
                if account.platform != platform:
                    continue
                if (t := direct_account_target(account)) is not None:
                    out.add(t.conversation_id)
        return frozenset(out)

    async def dm_address_allowed(self, platform: str, address: str) -> bool:
        """Whether a 1:1 to `address` may leave — the per-call read the
        senders' `dm_recipients` set stood in for. An outage raises (the
        send fails), never an allow."""
        if self._persons is not None:
            return address in self._persons.direct_addresses(platform)
        account = await self._bindings.resolve(platform, address)
        if account is None:
            return False
        person = await self.person(account.person_id)
        return person is not None and person.lifecycle is PersonLifecycle.ACTIVE

    # ------------------------------------------------------------------
    # person views
    # ------------------------------------------------------------------

    async def person(self, person_id: str) -> ManagedPerson | None:
        if self._persons is not None:
            ref = self._persons.by_person.get(
                self._persons.subject_id_for(person_id)
            )
            if ref is None:
                return None
            return ManagedPerson(
                person_id=ref.person_id,
                lifecycle=PersonLifecycle.ACTIVE,
                display_name=ref.name,
            )
        canonical = await self._bindings.canonical_person(person_id)
        if canonical is None:
            return None
        return await self._bindings.person(canonical)

    async def person_ref(self, person_id: str) -> PersonRef | None:
        """The name view of a known person — a retired id reads through
        its canonical survivor; suspension does not hide a name (grants,
        not identity reads, enforce lifecycle)."""
        if self._persons is not None:
            return self._persons.by_person.get(
                self._persons.subject_id_for(person_id)
            )
        person = await self.person(person_id)
        return None if person is None else _ref(person)

    async def resolve_ref(
        self, platform: str, sender_id: str
    ) -> PersonRef | None:
        """The sender's person view — `None` for an unmapped account."""
        if self._persons is not None:
            return self._persons.resolve(platform, sender_id)
        account = await self._bindings.resolve(platform, sender_id)
        if account is None:
            return None
        return await self.person_ref(account.person_id)

    async def person_refs(self) -> tuple[PersonRef, ...]:
        """The ACTIVE-person roster — managed mode's `by_person.values()`."""
        if self._persons is not None:
            return tuple(
                sorted(
                    self._persons.by_person.values(),
                    key=lambda r: (r.name.casefold(), r.person_id),
                )
            )
        await self.refresh_aliases()
        return tuple(_ref(p) for p in await self._bindings.persons())

    async def room_refs(
        self, platform: str, conversation_id: str
    ) -> tuple[PersonRef, ...]:
        """The room's roster by name. File mode reads the room's declared
        `persons` list; managed mode reads recorded membership on the room
        scope — an open room (`persons = "*"`) lists nobody in file mode
        because no live roster exists there."""
        if self._persons is not None:
            if self._policy is None:
                raise RuntimeError("file room authority is unavailable")
            convo = self._policy.conversation(platform, conversation_id)
            listed = convo.persons if convo is not None else frozenset()
            return self._persons.room_members(listed)
        scope = Scope(ScopeKind.ROOM, room_scope_id(platform, conversation_id))
        out = [
            ref
            for m in await self._bindings.members_of(scope)
            if m.state is MembershipState.MEMBER
            if (ref := await self.person_ref(m.person_id)) is not None
        ]
        return tuple(sorted(out, key=lambda r: (r.name.casefold(), r.person_id)))

    async def canonical_person(self, person_id: str) -> str | None:
        if self._persons is not None:
            return self._persons.subject_id_for(person_id)
        return await self._bindings.canonical_person(person_id)

    async def refresh_aliases(self, *, ttl_seconds: float = 60.0) -> None:
        """Reload the managed alias snapshot if stale. File mode returns
        immediately — `Persons` already folds live. A backend outage raises:
        the callers that need it are the same async reads that would fail
        anyway, so nothing answers on stale aliases."""
        if self._persons is not None:
            return
        now = time.monotonic()
        if now - self._alias_ts < ttl_seconds:
            return
        forward: dict[str, tuple[str, ...]] = {}
        reverse: dict[str, str] = {}
        for p in await self._bindings.persons():
            formers = await self._bindings.former_persons(p.person_id)
            forward[p.person_id] = tuple(formers)
            for f in formers:
                reverse[f] = p.person_id
        self._alias_map, self._alias_canonical, self._alias_ts = forward, reverse, now

    def subject_id_for(self, person_id: str) -> str:
        """The canonical person id — `Persons.subject_id_for`'s selected-
        backend form, served from the alias snapshot under managed."""
        if self._persons is not None:
            return self._persons.subject_id_for(person_id)
        return self._alias_canonical.get(person_id, person_id)

    def former_ids_for(self, person_id: str) -> tuple[str, ...]:
        """Retired ids of this person's canonical id — the managed answer to
        `Persons.former_ids_for` for the sync adaptors."""
        if self._persons is not None:
            return self._persons.former_ids_for(person_id)
        return self._alias_map.get(self._alias_canonical.get(person_id, person_id), ())

    async def former_persons(self, person_id: str) -> tuple[str, ...]:
        """Every retired id folding into this person — the reverse read a
        fold of historical references (analytics, retained rows) needs."""
        if self._persons is not None:
            return self._persons.former_ids_for(
                self._persons.subject_id_for(person_id)
            )
        return await self._bindings.former_persons(person_id)

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        if self._persons is not None:
            # The file has no lifecycle: `subject_id_for` folds a retired id
            # and never returns None, so file mode reports ACTIVE — the same
            # answer `FileBindingStore.person_lifecycle` gave.
            return PersonLifecycle.ACTIVE
        return await self._bindings.person_lifecycle(person_id)

    async def identity_held(self, person_id: str) -> bool:
        if self._persons is not None:
            return False
        return await self._bindings.identity_held(person_id)

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        if self._persons is not None:
            return ()  # the file keeps no membership records
        return await self._bindings.members_of(scope)

    # ------------------------------------------------------------------
    # names on transcripts
    # ------------------------------------------------------------------

    async def display_name(self, msg: NormalizedMessage) -> str:
        ref = await self.resolve_ref(msg.platform, msg.sender_id)
        return ref.name if ref else (msg.sender_name or sanitize_unmapped(msg.sender_id))

    async def name_transcript(
        self, transcript: Sequence[TranscriptRow], *, platform: str
    ) -> tuple[TranscriptRow, ...]:
        """Fill each row's `person_name`/`person_id` from the selected
        backend — `Persons.name_transcript`'s semantics over whichever
        directory answers (`DEC-80`)."""
        if self._persons is not None:
            return self._persons.name_transcript(transcript, platform=platform)
        out: list[TranscriptRow] = []
        for row in transcript:
            if row.is_nora:
                out.append(row)
                continue
            ref = await self.resolve_ref(platform, row.sender_id)
            out.append(
                row if ref is None
                else replace(row, person_name=ref.name, person_id=ref.person_id)
            )
        return tuple(out)

    # ------------------------------------------------------------------
    # grants — `Policy.is_allowed`'s selected-backend replacement
    # ------------------------------------------------------------------

    async def check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        """The one authoritative check path, unchanged."""
        if self._policy is not None:
            # `AuthorityService` over the file backend, inlined: canonicalize,
            # no holds, all file people ACTIVE, then `FileAuthorityBackend`'s
            # own `Policy` rules — verbatim.
            person_id = cast(Persons, self._persons).subject_id_for(person_id)
            if scope.kind is not ScopeKind.ROOM:
                return check_denied("file mode has no scoped management")
            platform, conversation_id = split_room_scope_id(scope.id)
            if self._policy.conversation(platform, conversation_id) is None:
                return unknown_scope("no such room in policy.toml")
            if permit in ("speak", "view"):
                return (
                    allowed()
                    if self._policy.is_allowed(
                        platform=platform,
                        conversation_id=conversation_id,
                        person_id=person_id,
                    )
                    else check_denied()
                )
            return check_denied("file mode delegates no management")
        return await self._authority.check(
            scope=scope, person_id=person_id, permit=permit
        )

    async def permitted(
        self, *, platform: str, conversation_id: str,
        person_id: str | None, permit: str = "speak",
    ) -> CheckResult:
        """Whether `person_id` may `permit` in this room — `is_allowed`'s
        selected-backend form. An unmapped sender is admitted only under
        the room's approved open-admission flag; a mapped sender goes
        through `check` so suspension, revocation and outages hold."""
        await self.refresh_aliases()
        if self._policy is not None:
            convo = self._policy.conversation(platform, conversation_id)
            if convo is None:
                return unknown_scope("scope-not-in-topology")
            if person_id is None:
                return allowed() if convo.persons is None else check_denied(
                    "unmapped-sender")
            return await self.check(
                scope=Scope(
                    ScopeKind.ROOM, room_scope_id(platform, conversation_id)),
                person_id=person_id, permit=permit,
            )
        scope = Scope(ScopeKind.ROOM, room_scope_id(platform, conversation_id))
        if person_id is None:
            if not await self._topology.scope_exists(scope):
                return unknown_scope("scope-not-in-topology")
            if await self._topology.is_open_room(scope):
                return allowed()
            return check_denied("unmapped-sender")
        return await self._authority.check(
            scope=scope, person_id=person_id, permit=permit
        )

    async def scope_admitted(
        self, platform: str, conversation_id: str
    ) -> bool:
        """Whether the room itself is admitted — `policy.conversation`'s
        selected-backend form. Topology answers both backends: the file
        topology reads the same policy table, so semantics are identical."""
        if self._policy is not None:
            return self._policy.conversation(platform, conversation_id) is not None
        scope = Scope(ScopeKind.ROOM, room_scope_id(platform, conversation_id))
        return await self._topology.scope_exists(scope)

    # ------------------------------------------------------------------
    # descriptive profile — file-sourced; an honest absence under managed
    # ------------------------------------------------------------------

    async def social_off(self, person_id: str) -> bool:
        if self._persons is None:
            return False  # the managed model has no opt-out flag yet
        return self._persons.social_off(person_id)

    async def social_off_ids(
        self, among: Iterable[str] | None = None
    ) -> frozenset[str]:
        if self._persons is None:
            return frozenset()
        return self._persons.social_off_ids(among)

    async def instagram_handle(self, fbid: str) -> str | None:
        if self._persons is None:
            return None  # per-identifier username metadata is file-only
        return self._persons.instagram_handle(fbid)

    async def instagram_username_owners(self, handle: str) -> frozenset[str]:
        if self._persons is None:
            return frozenset()
        return self._persons.instagram_username_owners(handle)

    async def instagram_igids(self, person_id: str) -> frozenset[str]:
        """`igid` identifiers — derivable from bound accounts under managed."""
        if self._persons is not None:
            return self._persons.instagram_igids(person_id)
        return frozenset(
            i.value
            for a in await self.accounts_for(person_id, platform="instagram")
            for i in a.identifiers
            if i.kind == "igid"
        )

    async def life360_memberships(self) -> tuple[Life360Membership, ...]:
        """Every declared Life360 membership — no managed circle source yet."""
        return self._persons.life360 if self._persons is not None else ()
