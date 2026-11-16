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
- `prepare` reads each source file ONCE into an immutable `PreparedImport`:
  the validated loaders' model, the journaled effect snapshot and the
  retained private source text. One invocation plans, wires and applies
  from that single capture — a later file change names another import and
  can never swap the in-flight effect behind the plan it reported.
- `FileAdmission.apply` claims an EMPTY-or-this-import's target under the
  shared mutation gate, journals the effect BEFORE any write, then runs
  journaled steps: people -> holds -> identities -> aliases -> memberships
  -> authority -> read-back. Each imported person publishes WITH this
  import's identity hold in one durable commit — `unavailable` to
  dependent managed access from its first readable row — and the
  proven-complete journal outcome commits together with the release of
  all and only this import's holds.
- An interrupted import resumes on an EXACT retry (same files -> same import
  identity -> same journaled steps). Changed files name a different import
  and are refused by the target probe, as is any managed row the intended
  effect does not own — suspended people, revoked accounts, foreign journals
  and link state are all non-empty evidence.
- A journaled row under this import's identity is bound by kind and the
  complete effect snapshot: a mismatch refuses as a conflict, and an EXACT
  completed match returns the recorded historical receipt as-is — it
  asserts that this import completed, never that current state still
  equals it, so it neither revalidates the target nor reasserts a thing.
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
from typing import Any

from nora.domain.identity import BoundAccount
from nora.domain.managed import (
    CONTAINMENT_RELATIONS,
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
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    ScopedRelation,
)
from nora.domain.management import MANAGEMENT_PERMITS, Scope, ScopeKind
from nora.people.app.managed_admission_capture import PreparedImport, _PersonPlan
from nora.people.app.managed_admission_identity import (
    AdmissionKratos,
    admission_marker,
    ensure_owned,
    identity_owned,
    initial_traits_match,
)
from nora.people.app.managed_admission_probe import (
    AdmissionFacts,
    AdmissionProbe,
)
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
class AdmissionPlan:
    """The non-mutating preflight result. `refused` non-empty means the
    source mapping cannot be admitted exactly and nothing may be written."""
    import_id: str
    refused: tuple[str, ...] = ()
    counts: MappingProxyType[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )



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


def prepare(identity_path: Path, policy_path: Path) -> PreparedImport | Outcome:
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
            else {
                # The file predicate answers on the CHECKED person's
                # canonical id against the AUTHORED list — a retired-only
                # entry therefore denies the survivor, never grants it.
                pid for pid in people
                if policy.is_allowed(
                    platform=convo.platform,
                    conversation_id=sorted(convo.ids)[0],
                    person_id=pid,
                )
            }
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
    return PreparedImport(
        import_id=_import_id(identity_sha, policy_sha),
        _snapshot_json=json.dumps(dict(snapshot), separators=(",", ":")),
        persons=persons,
        policy=policy,
        people=MappingProxyType(people),
        accounts=MappingProxyType(accounts),
        aliases=MappingProxyType(aliases),
        memberships=frozenset(pairs),
        room_scopes=MappingProxyType(room_scopes),
        containment=frozenset(containment),
    )


def _unsupported(policy: Policy) -> list[str]:
    """Inputs the managed model cannot represent exactly — named, never silently subset."""
    refused: list[str] = []
    for convo in policy.conversations:
        if convo.persons is None and not convo.guild:
            refused.append(
                f"unsupported-input:dynamic-membership:{convo.platform}"
                f"/{convo.label or convo.primary_id}"
            )
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
    prepared = prepare(identity_path, policy_path)
    if isinstance(prepared, Outcome):
        return AdmissionPlan(import_id="", refused=(prepared.detail,))
    return AdmissionPlan(
        import_id=prepared.import_id,
        counts=MappingProxyType(dict(prepared.snapshot["counts"])),
    )


def _receipt(record: OperationRecord) -> Outcome:
    """The journaled outcome of an exact completed match — a receipt, not revalidation."""
    try:
        status = OutcomeStatus(record.outcome)
    except (ValueError, TypeError):
        return unavailable("receipt-unconfirmed")
    return Outcome(status, record.detail)


@dataclass(frozen=True)
class FileAdmission:
    """The installation-only admission service. `bindings`/`backend` are the
    managed backend's own ports — the same objects `managed_services`
    composes — `kratos` the concrete owned-create/read seam, and `probe`
    the SQL-aware target seam."""
    bindings: BindingStore
    backend: AuthorityBackend
    kratos: AdmissionKratos
    probe: AdmissionProbe

    async def assess(self, identity_path: Path, policy_path: Path) -> Outcome:
        """Path wrapper: prepares once, then assesses that one capture."""
        prepared = prepare(identity_path, policy_path)
        if isinstance(prepared, Outcome):
            return prepared
        return await self.assess_prepared(prepared)

    async def assess_prepared(self, prepared: PreparedImport) -> Outcome:
        """Read-only target assessment for `plan` against a target: fresh,
        resumable, replayed or refused — never a write. The same attribution
        rule as `apply_prepared`: the durable journal is bound by kind and
        full snapshot, and an exact completed match is its own receipt."""
        try:
            async with self.bindings.mutation():
                return await self._assess(prepared)
        except Exception:  # noqa: BLE001 — port errors must stay unavailable
            return unavailable("store-failure")

    async def _assess(self, prepared: PreparedImport) -> Outcome:
        record = await self.bindings.operation(prepared.import_id)
        recorded = self._recorded(prepared, record)
        if recorded is not None:
            return recorded
        facts = await self.probe.facts()
        claim = self._claim(facts, prepared, record)
        if isinstance(claim, Outcome):
            return claim
        residual = await self._authority_residual(
            prepared, fresh=(claim == "fresh"))
        if residual is not None:
            return residual
        return complete(claim)

    async def apply(self, identity_path: Path, policy_path: Path) -> Outcome:
        """Path wrapper: prepares once, then applies that one capture."""
        prepared = prepare(identity_path, policy_path)
        if isinstance(prepared, Outcome):
            return prepared
        return await self.apply_prepared(prepared)

    async def apply_prepared(self, prepared: PreparedImport) -> Outcome:
        """Claim + journal + journaled effect, under the shared mutation gate."""
        try:
            async with self.bindings.mutation():
                return await self._apply(prepared)
        except Exception:
            log.exception("file admission failed")
            return unavailable("store-failure")

    async def _apply(self, prepared: PreparedImport) -> Outcome:
        record = await self.bindings.operation(prepared.import_id)
        recorded = self._recorded(prepared, record)
        if recorded is not None:
            return recorded
        facts = await self.probe.facts()
        claim = self._claim(facts, prepared, record)
        if isinstance(claim, Outcome):
            return claim
        authority = await self._authority_residual(
            prepared, fresh=(claim == "fresh"))
        if authority is not None:
            return authority
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
        outcome = await self._run_steps(record, prepared)
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self.bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        counts = prepared.snapshot["counts"]
        detail = " ".join(f"{k}={counts[k]}" for k in sorted(counts))
        finished = await self.probe.complete_import(
            OperationRecord(
                operation_id=record.operation_id,
                kind=record.kind,
                state=OperationJournalState.COMPLETE,
                steps=record.steps,
                outcome="complete",
                detail=detail,
                snapshot=record.snapshot,
            ),
            person_ids=tuple(sorted(prepared.people)),
        )
        if finished.status is not OutcomeStatus.COMPLETE:
            await _finish(self.bindings, record, OperationJournalState.INCOMPLETE,
                          finished.status.value, finished.detail)
            return finished
        return complete(detail)

    def _recorded(
        self, prepared: PreparedImport, record: OperationRecord | None,
    ) -> Outcome | None:
        if record is not None:
            if record.kind != IMPORT_KIND or record.snapshot != prepared.snapshot:
                return denied("effect-conflict")
            if record.state is OperationJournalState.COMPLETE:
                return _receipt(record)
        return None

    def _claim(
        self, facts: AdmissionFacts, prepared: PreparedImport,
        record: OperationRecord | None,
    ) -> str | Outcome:
        """The fresh/resume target claim after durable effect attribution."""
        if record is None and any(o == prepared.import_id for o, _, _ in facts.operations):
            return unavailable("journal-missing")
        imports = [o for o, kind, _ in facts.operations if kind == IMPORT_KIND]
        if [o for o in imports if o != prepared.import_id]:
            return denied("different-import")
        if [o for o, _, _ in facts.operations if o != prepared.import_id]:
            return denied("foreign-journal")
        if record is None:
            if (facts.persons or facts.accounts or facts.identifiers
                    or facts.aliases or facts.memberships or facts.supports
                    or facts.holds or facts.blocks or facts.link_rows):
                return denied("foreign-target-state")
            return "fresh"
        return self._residual(facts, prepared) or "resume"

    def _residual(
        self, facts: AdmissionFacts, prepared: PreparedImport
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

    def _authority_allowed(
        self, prepared: PreparedImport
    ) -> dict[Scope, set[ScopedRelation]]:
        """This import's own authority rows per scope: members + containment."""
        wanted: dict[Scope, set[ScopedRelation]] = {}
        for person_id, scope in prepared.memberships:
            wanted.setdefault(scope, set()).add(ScopedRelation("member", person_id))
        for child, parent in prepared.containment:
            wanted.setdefault(child, set()).add(
                ScopedRelation(CONTAINMENT_RELATIONS[child.kind], parent))
            wanted.setdefault(parent, set())
        for scope in prepared.room_scopes:
            wanted.setdefault(scope, set())
        return wanted

    async def _authority_residual(
        self, prepared: PreparedImport, *, fresh: bool
    ) -> Outcome | None:
        """Fresh claims need empty scopes; resume allows only this import's own rows."""
        wanted = self._authority_allowed(prepared)
        for scope in sorted(wanted, key=_scope_key):
            rows = await self.backend.scope_relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            if (fresh and rows) or not set(rows) <= wanted[scope]:
                return denied("foreign-authority-state")
        return None

    async def _kratos_fresh(self, prepared: PreparedImport) -> Outcome | None:
        """A fresh claim refuses a held external_id — every record there is foreign."""
        for person_id in sorted(prepared.people):
            outcome, view = await self.kratos.read(external_id=person_id)
            if view is not None:
                return denied("kratos-correlation-present")
            if outcome.status is not OutcomeStatus.DENIED:
                return unavailable(outcome.detail or "identity-unconfirmed")
        return None

    # -- the journaled steps ----------------------------------------------------

    async def _run_steps(self, record: OperationRecord, prepared: PreparedImport) -> Outcome:
        for step, effect in (
            ("people", lambda: self._people_step(record, prepared)),
            ("holds", lambda: self._holds_step(record, prepared)),
            ("identities", lambda: self._identities_step(prepared)),
            ("aliases", lambda: self._aliases_step(prepared)),
            ("memberships", lambda: self._memberships_step(prepared)),
            ("authority", lambda: self._authority_step(prepared)),
            ("readback", lambda: self._readback_step(prepared)),
        ):
            if step != "holds" and not _pending(record, step):
                continue
            outcome = await effect()
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
            await _step_done(self.bindings, record, step)
        return complete()

    async def _people_step(self, record: OperationRecord, prepared: PreparedImport) -> Outcome:
        """Each person commits WITH this import's hold — never readable unprotected."""
        for person_id in sorted(prepared.people):
            plan = prepared.people[person_id]
            outcome = await self.probe.publish_held_person(
                person=ManagedPerson(
                    person_id=person_id,
                    lifecycle=PersonLifecycle.ACTIVE,
                    display_name=plan.display_name,
                ),
                accounts=tuple(
                    ManagedAccount(
                        account_id=_account_id(person_id, account),
                        person_id=person_id,
                        platform=account.platform,
                        identifiers=account.identifiers,
                        state=AccountState.BOUND,
                        label=account.label,
                    )
                    for account in prepared.accounts[person_id]
                ),
                operation_id=record.operation_id,
            )
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _holds_step(self, record: OperationRecord, prepared: PreparedImport) -> Outcome:
        """Re-assert each hold — a resume re-protects rows older sequencing left exposed."""
        for person_id in sorted(prepared.people):
            outcome = await self.bindings.hold_identity(
                person_id=person_id, operation_id=record.operation_id)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _identities_step(self, prepared: PreparedImport) -> Outcome:
        """Create each identity stamped with this import's durable marker."""
        marker = admission_marker(
            import_id=prepared.import_id,
            effect_sha256=prepared.snapshot["effect_sha256"],
        )
        for person_id, plan in sorted(prepared.people.items()):
            outcome, identity_id = await ensure_owned(
                self.kratos,
                external_id=person_id,
                traits={"display_name": plan.display_name, "social": plan.social},
                marker=marker,
            )
            if identity_id is None:
                return outcome
            outcome = await self.bindings.correlate_kratos(
                person_id=person_id, kratos_identity_id=identity_id)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _aliases_step(self, prepared: PreparedImport) -> Outcome:
        for person_id in sorted(prepared.aliases):
            for former in sorted(prepared.aliases[person_id]):
                outcome = await self.bindings.add_alias(
                    former_id=former, person_id=person_id)
                if outcome.status is not OutcomeStatus.COMPLETE:
                    return outcome
        return complete()

    async def _memberships_step(self, prepared: PreparedImport) -> Outcome:
        for person_id, scope in sorted(
            prepared.memberships, key=lambda m: (m[0], _scope_key(m[1]))
        ):
            outcome = await self.bindings.set_membership(
                person_id=person_id, scope=scope,
                member=True, source=ADMISSION_SOURCE)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _authority_step(self, prepared: PreparedImport) -> Outcome:
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

    async def _readback_step(self, prepared: PreparedImport) -> Outcome:
        """Fail-closed agreement: bindings, owned Kratos records, memberships, Keto."""
        marker = admission_marker(
            import_id=prepared.import_id,
            effect_sha256=prepared.snapshot["effect_sha256"],
        )
        for person_id in sorted(prepared.people):
            plan = prepared.people[person_id]
            person = await self.bindings.person(person_id)
            if (person is None or person.lifecycle is not PersonLifecycle.ACTIVE
                    or person.kratos_identity_id is None):
                return unavailable("read-back-mismatch")
            outcome, view = await self.kratos.read(external_id=person_id)
            if view is None:
                if outcome.status is OutcomeStatus.DENIED:
                    return unavailable("identity-absent")
                return unavailable(outcome.detail)
            if (view.external_id != person_id
                    or view.identity_id != person.kratos_identity_id
                    or view.state != "active"
                    or not identity_owned(view, marker)
                    or not initial_traits_match(
                        view,
                        display_name=plan.display_name, social=plan.social)):
                return unavailable("read-back-mismatch")
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
        wanted = self._authority_allowed(prepared)
        for scope in sorted(wanted, key=_scope_key):
            rows = await self.backend.scope_relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            if set(rows) != wanted[scope]:
                return unavailable("read-back-mismatch")
        # The verdict matrix must equal the FILE predicate — canonicalize the
        # checked id (retired ids fold through the binding alias map), then
        # `is_allowed` on the authored list. Anything else is self-agreement.
        checked = set(prepared.people)
        for formers in prepared.aliases.values():
            checked.update(formers)
        unknown = f"{prepared.import_id}:unknown"
        while unknown in checked:
            unknown += ":unknown"
        checked.add(unknown)
        for checked_id in sorted(checked):
            person_id = prepared.persons.subject_id_for(checked_id)
            for scope in sorted(wanted, key=_scope_key):
                room = prepared.room_scopes.get(scope)
                expected = room is not None and prepared.policy.is_allowed(
                    platform=room[0], conversation_id=room[1], person_id=person_id)
                for permit in ("speak", "view"):
                    verdict = await self.backend.check(
                        scope=scope, person_id=person_id, permit=permit)
                    if verdict.verdict is Verdict.UNAVAILABLE:
                        return unavailable(verdict.detail)
                    if (verdict.verdict is Verdict.ALLOWED) != expected:
                        return unavailable("read-back-mismatch")
            for scope in sorted(wanted, key=_scope_key):
                for permit in MANAGEMENT_PERMITS:
                    verdict = await self.backend.check(
                        scope=scope, person_id=person_id, permit=permit)
                    if verdict.verdict is Verdict.UNAVAILABLE:
                        return unavailable(verdict.detail)
                    if verdict.verdict is Verdict.ALLOWED:
                        # File mode delegates no management — a grant here is
                        # authority the source never authorized.
                        return unavailable("read-back-mismatch")
        return complete()
