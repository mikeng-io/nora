"""Initial file-to-managed admission — the installation authority's one-time
import of an established `identity.toml`/`policy.toml` mapping
(`design/managed-identity-access/identity.md` «Backend selection and
lifecycle», `authorization.md` «installation administration»).

Selecting the managed backend admits a validated mapping of existing people
and grants; it never infers links and never broadens access. This module is
that admission:

- `plan` is a non-mutating preflight — it runs the REAL file loaders
  (`nora.persons.load_persons`, `nora.policy.load_policy`), refuses any input
  the managed model cannot represent exactly (guild-expanded conversations,
  DM admission lists), and binds source content + the intended effect to a
  durable import identity derived from both files' digests.
- `FileAdmission.apply` claims an EMPTY-or-this-import's target under the
  shared mutation gate, journals the effect BEFORE any write, then runs
  journaled steps: people -> holds -> identities -> aliases -> memberships
  -> authority -> read-back. Identity holds keep every imported person
  `unavailable` to dependent managed access until read-back proves the whole
  published state; only this import's holds are released.
- An interrupted import resumes on an EXACT retry (same files -> same import
  identity -> same journaled steps). Changed files name a different import
  and are refused by the target probe, as is any managed row the intended
  effect does not own — suspended people, revoked accounts, foreign journals
  and link state are all non-empty evidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Any, Protocol

from nora.domain.identity import BoundAccount
from nora.domain.managed import (
    AccountState,
    CheckResult,
    ManagedAccount,
    ManagedPerson,
    MembershipState,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    complete,
    denied,
    room_scope_id,
    unavailable,
)
from nora.domain.managed_ports import AuthorityBackend, BindingStore, IdentityRegistry
from nora.domain.management import Scope, ScopeKind
from nora.people.app.managed_ops import _finish, _pending, _step_done
from nora.persons import Persons, PersonsError, load_persons
from nora.policy import Policy, PolicyError, load_policy

log = logging.getLogger("nora.people.managed")

IMPORT_KIND = "file_admission"

#: The membership support an admission publishes — provenance "the operator's
#: established file mapping", distinct from `registration`/`sync:`/`delegation`.
ADMISSION_SOURCE = "admission"

_STEPS = ("people", "holds", "identities", "aliases", "memberships", "authority", "readback")


@dataclass(frozen=True)
class AdmissionFacts:
    """Every managed row the target holds, projected for the emptiness/resume
    proof — including rows the port reads hide (suspended people, revoked
    accounts, holds, foreign journals, link state)."""
    persons: frozenset[tuple[str, str, bool]]      # (id, lifecycle, kratos-correlated)
    accounts: frozenset[tuple[str, str, str]]      # (account_id, person_id, state)
    identifiers: frozenset[tuple[str, str, str]]   # (platform, kind, value)
    aliases: frozenset[tuple[str, str]]            # (former_id, person_id)
    memberships: frozenset[tuple[str, str, str, str]]  # (person, kind, scope_id, state)
    supports: frozenset[tuple[str, str, str, str]]     # (person, kind, scope_id, source)
    operations: frozenset[tuple[str, str, str]]        # (operation_id, kind, state)
    holds: frozenset[tuple[str, str]]                  # (person_id, operation_id)
    blocks: int
    link_rows: int


class AdmissionProbe(Protocol):
    """The concrete installation-target seam — the empty-or-owned proof no
    port read can give, plus the person-only insert `BindingStore.register`
    cannot express (a listed person may hold no account yet)."""

    async def facts(self) -> AdmissionFacts:
        """The target's full managed row projection."""

    async def insert_person(self, person: ManagedPerson) -> Outcome:
        """An account-less person row, committed independently like every
        other binding write in this flow."""


@dataclass(frozen=True)
class AdmissionPlan:
    """The non-mutating preflight result. `refused` non-empty means the
    source mapping cannot be admitted exactly and nothing may be written."""
    import_id: str
    refused: tuple[str, ...] = ()
    counts: MappingProxyType[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class _PersonPlan:
    person_id: str
    display_name: str
    social: bool


@dataclass(frozen=True)
class _Prepared:
    """The exact intended effect — computed once per call, bound by digest."""
    import_id: str
    snapshot: MappingProxyType[str, Any]
    people: dict[str, _PersonPlan]
    accounts: dict[str, tuple[BoundAccount, ...]]      # person_id -> accounts
    aliases: dict[str, tuple[str, ...]]               # person_id -> former ids
    memberships: frozenset[tuple[str, Scope]]         # (person_id, room scope)
    room_scopes: dict[Scope, tuple[str, str]]         # scope -> (platform, conversation_id)
    containment: frozenset[tuple[Scope, Scope]]       # (child, parent)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_ACCOUNT_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "nora.local/managed-account")


def _account_id(person_id: str, account: BoundAccount) -> str:
    """A uuid5 of the binding itself — same file content names the same account."""
    raw = json.dumps(
        [person_id, account.platform, account.label or "",
         sorted([i.platform, i.kind, i.value] for i in account.identifiers)],
        separators=(",", ":"),
    )
    return str(uuid.uuid5(_ACCOUNT_NS, raw))


def _import_id(identity_sha: str, policy_sha: str) -> str:
    return f"file-admission:{_digest(f'{identity_sha}:{policy_sha}'.encode())[:24]}"


def _scope_key(scope: Scope) -> str:
    return f"{scope.kind.value}:{scope.id}"


def _read_sources(
    identity_path: Path, policy_path: Path,
) -> tuple[Persons, Policy, bytes, bytes] | Outcome:
    """Parse immutable private copies of the exact source bytes retained by the import."""
    try:
        identity_source = identity_path.read_bytes()
        policy_source = policy_path.read_bytes()
    except OSError as error:
        return denied(f"unreadable-source:{type(error).__name__}")
    with TemporaryDirectory(prefix="nora-admission-") as directory:
        identity_copy = Path(directory) / "identity.toml"
        policy_copy = Path(directory) / "policy.toml"
        identity_copy.write_bytes(identity_source)
        policy_copy.write_bytes(policy_source)
        try:
            persons = load_persons(identity_copy)
        except (PersonsError, OSError, UnicodeError) as error:
            return denied(f"invalid-identity-file:{type(error).__name__}")
        try:
            policy = load_policy(policy_copy)
        except (PolicyError, OSError, UnicodeError) as error:
            return denied(f"invalid-policy-file:{type(error).__name__}")
    return persons, policy, identity_source, policy_source


def _prepare(identity_path: Path, policy_path: Path) -> _Prepared | Outcome:
    """Load + validate the sources and compute the effect — refusals end here, pre-write."""
    sources = _read_sources(identity_path, policy_path)
    if isinstance(sources, Outcome):
        return sources
    persons, policy, identity_source, policy_source = sources
    refused = _unsupported(policy)
    if refused:
        return denied(";".join(refused))
    identity_sha = _digest(identity_source)
    policy_sha = _digest(policy_source)

    listed: set[str] = set()
    for convo in policy.conversations:
        if convo.persons is not None:
            listed |= {persons.subject_id_for(pid) for pid in convo.persons}
    people: dict[str, _PersonPlan] = {}
    accounts: dict[str, tuple[BoundAccount, ...]] = {}
    aliases: dict[str, tuple[str, ...]] = {}
    for person_id in sorted(set(persons.by_person) | listed):
        ref = persons.by_person.get(person_id)
        people[person_id] = _PersonPlan(
            person_id=person_id,
            display_name=ref.name if ref is not None else "",
            social=not persons.social_off(person_id),
        )
        accounts[person_id] = persons.accounts_for(person_id)
        aliases[person_id] = persons.former_ids_for(person_id)

    room_scopes: dict[Scope, tuple[str, str]] = {}
    pairs: set[tuple[str, Scope]] = set()
    for convo in policy.rooms():
        for cid in convo.ids:
            scope = Scope(ScopeKind.ROOM, room_scope_id(convo.platform, cid))
            room_scopes[scope] = (convo.platform, cid)
        admitted = (
            set(people) if convo.persons is None
            else {persons.subject_id_for(pid) for pid in convo.persons}
        )
        for person_id in admitted:
            for cid in convo.ids:
                pairs.add((person_id, Scope(
                    ScopeKind.ROOM, room_scope_id(convo.platform, cid),
                )))

    containment: set[tuple[Scope, Scope]] = set()
    for scope in room_scopes:
        owner = policy.conversation(*room_scopes[scope])
        if owner is not None and owner.unit.strip():
            containment.add((scope, Scope(ScopeKind.UNIT, owner.unit)))
    for unit in policy.unit_workspaces():
        workspace = policy.workspace_of(unit)
        if workspace is not None:
            containment.add((
                Scope(ScopeKind.UNIT, unit), Scope(ScopeKind.WORKSPACE, workspace),
            ))

    snapshot = _snapshot(
        identity_sha, policy_sha, people, accounts, aliases,
        pairs, room_scopes, containment,
        identity_source=identity_source.decode("utf-8"),
        policy_source=policy_source.decode("utf-8"),
    )
    return _Prepared(
        import_id=_import_id(identity_sha, policy_sha),
        snapshot=snapshot,
        people=people,
        accounts=accounts,
        aliases=aliases,
        memberships=frozenset(pairs),
        room_scopes=room_scopes,
        containment=frozenset(containment),
    )


def _unsupported(policy: Policy) -> list[str]:
    """Inputs the managed model cannot represent exactly — named, never silently subset."""
    refused: list[str] = []
    for convo in policy.conversations:
        if convo.guild:
            refused.append(
                f"unsupported-input:guild:{convo.platform}"
                f"/{convo.label or convo.guild}"
            )
    if policy.dm_persons:
        refused.append(
            f"unsupported-input:dm-admission:{len(policy.dm_persons)}-persons"
        )
    return refused


def _snapshot(
    identity_sha: str, policy_sha: str,
    people: dict[str, _PersonPlan], accounts: dict[str, tuple[BoundAccount, ...]],
    aliases: dict[str, tuple[str, ...]],
    memberships: set[tuple[str, Scope]] | frozenset[tuple[str, Scope]],
    room_scopes: dict[Scope, tuple[str, str]],
    containment: set[tuple[Scope, Scope]] | frozenset[tuple[Scope, Scope]],
    *, identity_source: str, policy_source: str,
) -> MappingProxyType[str, Any]:
    """The durable effect: journaled rows digested, plus the bounded counts the plan reports."""
    rows: list[list[Any]] = []
    for person_id, plan in sorted(people.items()):
        rows.append(["person", person_id, plan.display_name, plan.social])
    account_rows = [
        account for person_id in sorted(accounts) for account in accounts[person_id]
    ]
    for account in sorted(account_rows, key=lambda a: _account_id(a.person_id, a)):
        rows.append([
            "account", account.person_id, account.platform,
            account.label or "", _account_id(account.person_id, account),
            sorted([i.platform, i.kind, i.value] for i in account.identifiers),
        ])
    for person_id in sorted(aliases):
        for former in sorted(aliases[person_id]):
            rows.append(["alias", former, person_id])
    for person_id, scope in sorted(
        memberships, key=lambda m: (m[0], _scope_key(m[1]))
    ):
        rows.append(["member", person_id, _scope_key(scope)])
    for child, parent in sorted(
        containment, key=lambda pair: (_scope_key(pair[0]), _scope_key(pair[1]))
    ):
        rows.append(["containment", _scope_key(child), _scope_key(parent)])
    effect_sha = _digest(json.dumps(rows, separators=(",", ":")).encode())
    return MappingProxyType({
        "identity_sha256": identity_sha,
        "policy_sha256": policy_sha,
        "effect_sha256": effect_sha,
        "sources": {"identity_toml": identity_source, "policy_toml": policy_source},
        "people": sorted(people),
        "counts": {
            "people": len(people),
            "accounts": len(account_rows),
            "identifiers": sum(len(a.identifiers) for a in account_rows),
            "aliases": sum(len(a) for a in aliases.values()),
            "memberships": len(memberships),
            "room_scopes": len(room_scopes),
            "containment": len(containment),
        },
    })


def plan(identity_path: Path, policy_path: Path) -> AdmissionPlan:
    """Non-mutating preflight: the loaders' verdict and the bounded counts."""
    prepared = _prepare(identity_path, policy_path)
    if isinstance(prepared, Outcome):
        return AdmissionPlan(import_id="", refused=(prepared.detail,))
    return AdmissionPlan(
        import_id=prepared.import_id,
        counts=MappingProxyType(dict(prepared.snapshot["counts"])),
    )


@dataclass(frozen=True)
class FileAdmission:
    """The installation-only admission service. `bindings`/`backend`/
    `registry` are the managed backend's own ports — the same objects
    `managed_services` composes — and `probe` the SQL-aware target seam."""
    bindings: BindingStore
    backend: AuthorityBackend
    registry: IdentityRegistry
    probe: AdmissionProbe

    async def assess(self, identity_path: Path, policy_path: Path) -> Outcome:
        """Read-only target assessment for `plan` against a target: fresh,
        resumable, replayed or refused — never a write."""
        prepared = _prepare(identity_path, policy_path)
        if isinstance(prepared, Outcome):
            return prepared
        facts = await self.probe.facts()
        claim = self._claim(facts, prepared)
        if isinstance(claim, Outcome):
            return claim
        if claim == "replayed":
            record = await self.bindings.operation(prepared.import_id)
            if record is None:
                return unavailable("journal-missing")
            return Outcome(
                OutcomeStatus(record.outcome or "complete"), record.detail)
        residual = await self._authority_residual(
            prepared, fresh=(claim == "fresh"))
        if residual is not None:
            return residual
        return complete(claim)

    async def apply(self, identity_path: Path, policy_path: Path) -> Outcome:
        """Claim + journal + journaled effect, under the shared mutation gate."""
        prepared = _prepare(identity_path, policy_path)
        if isinstance(prepared, Outcome):
            return prepared
        try:
            async with self.bindings.mutation():
                return await self._apply(prepared)
        except Exception:
            log.exception("file admission failed")
            return unavailable("store-failure")

    async def _apply(self, prepared: _Prepared) -> Outcome:
        facts = await self.probe.facts()
        claim = self._claim(facts, prepared)
        if isinstance(claim, Outcome):
            return claim
        authority = await self._authority_residual(
            prepared, fresh=(claim == "fresh"))
        if authority is not None:
            return authority
        record = await self.bindings.operation(prepared.import_id)
        if claim == "replayed":
            if record is None:
                return unavailable("journal-missing")
            return Outcome(
                OutcomeStatus(record.outcome or "complete"), record.detail)
        if record is None:
            if claim == "fresh":
                collision = await self._kratos_fresh(prepared)
                if collision is not None:
                    return collision
            record = OperationRecord(
                operation_id=prepared.import_id,
                kind=IMPORT_KIND,
                state=OperationJournalState.PENDING,
                steps=MappingProxyType(dict.fromkeys(_STEPS, "pending")),
                snapshot=prepared.snapshot,
            )
            recorded = await self.bindings.record_operation(record)
            if recorded.status is not OutcomeStatus.COMPLETE:
                return recorded
        elif record.snapshot != prepared.snapshot:
            return denied("effect-conflict")
        outcome = await self._run_steps(record, prepared)
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self.bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        released = await self._release(record, prepared)
        if released.status is not OutcomeStatus.COMPLETE:
            await _finish(self.bindings, record, OperationJournalState.INCOMPLETE,
                          released.status.value, released.detail)
            return released
        counts = prepared.snapshot["counts"]
        detail = " ".join(f"{k}={counts[k]}" for k in sorted(counts))
        await _finish(self.bindings, record, OperationJournalState.COMPLETE,
                      "complete", detail)
        return complete(detail)

    def _claim(self, facts: AdmissionFacts, prepared: _Prepared) -> str | Outcome:
        """The installation-only claim: "fresh", "resume", "replayed" — or a refusal."""
        imports = [o for o, kind, _ in facts.operations if kind == IMPORT_KIND]
        if [o for o in imports if o != prepared.import_id]:
            return denied("different-import")
        if [o for o, _, _ in facts.operations if o != prepared.import_id]:
            return denied("foreign-journal")
        if prepared.import_id not in imports:
            if (facts.persons or facts.accounts or facts.identifiers
                    or facts.aliases or facts.memberships or facts.supports
                    or facts.holds or facts.blocks or facts.link_rows):
                return denied("foreign-target-state")
            return "fresh"
        state = next(
            state for o, _, state in facts.operations
            if o == prepared.import_id
        )
        if state == OperationJournalState.COMPLETE.value:
            return "replayed"
        return self._residual(facts, prepared) or "resume"

    def _residual(
        self, facts: AdmissionFacts, prepared: _Prepared
    ) -> Outcome | None:
        """On resume every managed row must be a subset of this import's intended effect."""
        people = set(prepared.people)
        if any(pid not in people or lifecycle != "active"
               for pid, lifecycle, _ in facts.persons):
            return denied("foreign-target-state:person")
        intended_accounts = {
            _account_id(pid, account)
            for pid, rows in prepared.accounts.items() for account in rows
        }
        if any(account_id not in intended_accounts or pid not in people
               or state != "bound"
               for account_id, pid, state in facts.accounts):
            return denied("foreign-target-state:account")
        intended_identifiers = {
            (i.platform, i.kind, i.value)
            for rows in prepared.accounts.values()
            for account in rows for i in account.identifiers
        }
        if not facts.identifiers <= intended_identifiers:
            return denied("foreign-target-state:identifier")
        intended_aliases = {
            (former, pid)
            for pid, formers in prepared.aliases.items() for former in formers
        }
        if not facts.aliases <= intended_aliases:
            return denied("foreign-target-state:alias")
        intended_members = {
            (pid, scope.kind.value, scope.id)
            for pid, scope in prepared.memberships
        }
        if any((pid, kind, scope_id) not in intended_members or state != "member"
               for pid, kind, scope_id, state in facts.memberships):
            return denied("foreign-target-state:membership")
        intended_supports = {
            (pid, kind, scope_id, ADMISSION_SOURCE)
            for pid, kind, scope_id in intended_members
        }
        if not facts.supports <= intended_supports:
            return denied("foreign-target-state:membership-support")
        if any(operation_id != prepared.import_id or pid not in people
               for pid, operation_id in facts.holds):
            return denied("foreign-target-state:hold")
        if facts.blocks or facts.link_rows:
            return denied("foreign-target-state")
        return None

    async def _authority_residual(
        self, prepared: _Prepared, *, fresh: bool
    ) -> Outcome | None:
        """Fresh claims need empty scopes; resume allows only this import's member rows."""
        wanted: dict[Scope, set[str]] = {}
        for person_id, scope in prepared.memberships:
            wanted.setdefault(scope, set()).add(person_id)
        scopes = set(prepared.room_scopes)
        for child, parent in prepared.containment:
            scopes |= {child, parent}
        for scope in sorted(scopes, key=_scope_key):
            rows = await self.backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            allowed = {("member", pid) for pid in wanted.get(scope, ())}
            if (fresh and rows) or not set(rows) <= allowed:
                return denied("foreign-authority-state")
        return None

    async def _kratos_fresh(self, prepared: _Prepared) -> Outcome | None:
        """A fresh claim refuses an identity already holding an imported person's external_id."""
        for person_id in sorted(prepared.people):
            state = await self.registry.identity_state(external_id=person_id)
            if state.verdict is Verdict.UNKNOWN_SCOPE:
                continue
            if state.verdict is Verdict.UNAVAILABLE:
                return unavailable(state.detail)
            return denied("kratos-correlation-present")
        return None

    # -- the journaled steps ----------------------------------------------------

    async def _run_steps(self, record: OperationRecord, prepared: _Prepared) -> Outcome:
        for step, effect in (
            ("people", lambda: self._people_step(prepared)),
            ("holds", lambda: self._holds_step(record, prepared)),
            ("identities", lambda: self._identities_step(prepared)),
            ("aliases", lambda: self._aliases_step(prepared)),
            ("memberships", lambda: self._memberships_step(prepared)),
            ("authority", lambda: self._authority_step(prepared)),
            ("readback", lambda: self._readback_step(prepared)),
        ):
            if not _pending(record, step):
                continue
            outcome = await effect()
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
            await _step_done(self.bindings, record, step)
        return complete()

    async def _people_step(self, prepared: _Prepared) -> Outcome:
        for person_id in sorted(prepared.people):
            plan = prepared.people[person_id]
            person = ManagedPerson(
                person_id=person_id,
                lifecycle=PersonLifecycle.ACTIVE,
                display_name=plan.display_name,
            )
            rows = prepared.accounts[person_id]
            if not rows:
                outcome = await self.probe.insert_person(person)
                if outcome.status is not OutcomeStatus.COMPLETE:
                    return outcome
                continue
            for account in rows:
                outcome = await self.bindings.register(
                    person=person,
                    account=ManagedAccount(
                        account_id=_account_id(person_id, account),
                        person_id=person_id,
                        platform=account.platform,
                        identifiers=account.identifiers,
                        state=AccountState.BOUND,
                        label=account.label,
                    ),
                )
                if outcome.status is not OutcomeStatus.COMPLETE:
                    return outcome
        return complete()

    async def _holds_step(self, record: OperationRecord, prepared: _Prepared) -> Outcome:
        """Hold every imported person — dependent access stays `unavailable` until read-back."""
        for person_id in sorted(prepared.people):
            outcome = await self.bindings.hold_identity(
                person_id=person_id, operation_id=record.operation_id)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _identities_step(self, prepared: _Prepared) -> Outcome:
        for person_id, plan in sorted(prepared.people.items()):
            outcome, identity_id = await self.registry.ensure_identity(
                external_id=person_id,
                traits={"display_name": plan.display_name, "social": plan.social},
            )
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
            if identity_id is None:
                return unavailable("identity-unconfirmed")
            outcome = await self.bindings.correlate_kratos(
                person_id=person_id, kratos_identity_id=identity_id)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _aliases_step(self, prepared: _Prepared) -> Outcome:
        for person_id in sorted(prepared.aliases):
            for former in sorted(prepared.aliases[person_id]):
                outcome = await self.bindings.add_alias(
                    former_id=former, person_id=person_id)
                if outcome.status is not OutcomeStatus.COMPLETE:
                    return outcome
        return complete()

    async def _memberships_step(self, prepared: _Prepared) -> Outcome:
        for person_id, scope in sorted(
            prepared.memberships, key=lambda m: (m[0], _scope_key(m[1]))
        ):
            outcome = await self.bindings.set_membership(
                person_id=person_id, scope=scope,
                member=True, source=ADMISSION_SOURCE)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _authority_step(self, prepared: _Prepared) -> Outcome:
        """Containment first, then room `member` facts — the file's speak/view grants."""
        for child, parent in sorted(
            prepared.containment,
            key=lambda pair: (_scope_key(pair[0]), _scope_key(pair[1])),
        ):
            outcome = await self.backend.publish_containment(
                scope=child, parent=parent)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        wanted: dict[Scope, set[str]] = {}
        for person_id, scope in prepared.memberships:
            wanted.setdefault(scope, set()).add(person_id)
        for scope, members in sorted(wanted.items(), key=lambda kv: _scope_key(kv[0])):
            rows = await self.backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            have = {subject for relation, subject in rows if relation == "member"}
            for person_id in sorted(members - have):
                outcome = await self.backend.put_relation(
                    scope=scope, relation="member", person_id=person_id)
                if outcome.status is not OutcomeStatus.COMPLETE:
                    return outcome
        return complete()

    async def _readback_step(self, prepared: _Prepared) -> Outcome:
        """Fail-closed agreement: bindings, Kratos, memberships, Keto facts, verdict matrix."""
        for person_id in sorted(prepared.people):
            person = await self.bindings.person(person_id)
            if (person is None or person.lifecycle is not PersonLifecycle.ACTIVE
                    or person.kratos_identity_id is None):
                return unavailable("read-back-mismatch")
            state = await self.registry.identity_state(external_id=person_id)
            if state.verdict is not Verdict.ALLOWED:
                return unavailable("identity-unconfirmed")
            member_scopes = {
                scope for pid, scope in prepared.memberships if pid == person_id
            }
            held = {
                membership.scope
                for membership in await self.bindings.memberships_for(person_id)
                if membership.state is MembershipState.MEMBER
                and ADMISSION_SOURCE in membership.sources
            }
            if member_scopes != held:
                return unavailable("read-back-mismatch")
        for person_id in sorted(prepared.accounts):
            for account in prepared.accounts[person_id]:
                for ident in account.identifiers:
                    record = await self.bindings.record_for(account=ident)
                    if (record is None or record.person_id != person_id
                            or record.account_id != _account_id(person_id, account)
                            or record.label != account.label
                            or record.identifiers != account.identifiers):
                        return unavailable("read-back-mismatch")
        for person_id in sorted(prepared.aliases):
            for former in prepared.aliases[person_id]:
                if await self.bindings.canonical_person(former) != person_id:
                    return unavailable("read-back-mismatch")
        wanted: dict[Scope, set[str]] = {}
        for person_id, scope in prepared.memberships:
            wanted.setdefault(scope, set()).add(person_id)
        for scope in prepared.room_scopes:
            rows = await self.backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            if set(rows) != {("member", pid) for pid in wanted.get(scope, ())}:
                return unavailable("read-back-mismatch")
        for person_id in sorted(prepared.people):
            for scope in prepared.room_scopes:
                expected = (person_id, scope) in prepared.memberships
                for permit in ("speak", "view"):
                    verdict = await self.backend.check(
                        scope=scope, person_id=person_id, permit=permit)
                    if verdict.verdict is Verdict.UNAVAILABLE:
                        return unavailable(verdict.detail)
                    if (verdict.verdict is Verdict.ALLOWED) != expected:
                        return unavailable("read-back-mismatch")
        return complete()

    async def _release(self, record: OperationRecord, prepared: _Prepared) -> Outcome:
        """Release ONLY this import's holds, at the proven-complete boundary."""
        for person_id in sorted(prepared.people):
            outcome = await self.bindings.release_identity_hold(
                person_id=person_id, operation_id=record.operation_id)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()
