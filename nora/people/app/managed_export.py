"""Managed-to-file export — the operator-controlled preparation half of the
file-backend handoff (`design/managed-identity-access/identity.md` «Backend
selection and lifecycle», `docs/maintainers/managed-identity-export.md`).

This is the inverse boundary of `managed_admission`: that command admitted
a validated file mapping into an EMPTY managed target; this one reads the
current managed authority BACK OUT into a fresh `identity.toml`/
`policy.toml` pair the operator can validate, then adopt through the
runbook's separate writer-stop + approval path. It never activates
anything, never mutates managed state and never runs a runtime path — an
installation-only, read-only export under the same `bindings.mutation()`
gate every managed writer serialises on, so the bundle is one coherent cut
of bindings + Kratos traits + Keto authority.

`ManagedExport` below is the composition: `managed_export_prepare.py` owns
the complete-inventory model and the pure preparation phases (what the
export MAY emit), `managed_export_verify.py` the live-authority proof
(verdict matrix, real-loader validation, predicate comparison, manifest).
The emitted pair must answer the same grant predicate managed answers and
anything file mode cannot represent exactly is a named refusal, never a
dropped fact — the modules' docstrings spell the refusal list out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from nora.access import IdentityAccess
from nora.domain.managed import (
    CheckResult,
    Outcome,
    OutcomeStatus,
    complete,
    denied,
    room_scope_id,
    unavailable,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    IdentityRegistry,
    TopologySource,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.app.managed_export_prepare import (
    _PROBE_ID,
    ExportInventory,
    ExportProbe,
    _build_identity,
    _build_policy,
    _classify_relations,
    _digest,
    _emit_people,
    _inventory_refusals,
    _read_policy,
    _read_traits,
    _topology_refusals,
)
from nora.people.app.managed_export_verify import (
    _compare,
    _derive_rooms,
    _manifest,
    _matrix,
    _validate_bundle,
)
from nora.policy import Policy

log = logging.getLogger("nora.people.managed")


# -- the report the command prints / publishes --------------------------------


@dataclass(frozen=True)
class ExportBundle:
    """The generated private bundle — text only; the caller owns the write."""
    identity_toml: str
    policy_toml: str
    manifest_json: str


@dataclass(frozen=True)
class ExportReport:
    """The three-state export verdict: `complete` (the bundle is faithful),
    `denied` (authoritative refusal — `refusals` names every limit found),
    `unavailable` (a store could not answer — retryable)."""
    outcome: Outcome
    refusals: tuple[str, ...] = ()
    counts: MappingProxyType[str, int] = field(
        default_factory=lambda: MappingProxyType({}))
    digest: str = ""
    bundle: ExportBundle | None = None


# -- the service ----------------------------------------------------------------


@dataclass(frozen=True)
class ManagedExport:
    """The installation-only export service. `bindings`/`backend`/
    `topology`/`registry`/`access` are the managed backend's own objects —
    the same graph `managed_services` composes — and `probe` the SQL-aware
    inventory seam."""
    bindings: BindingStore
    backend: AuthorityBackend
    topology: TopologySource
    registry: IdentityRegistry
    access: IdentityAccess
    probe: ExportProbe

    async def assess(self, policy_path: Path) -> ExportReport:
        """The read-only plan/validation path — the whole pipeline except
        the bundle hand-off."""
        return await self._run(policy_path, publish=False)

    async def export(self, policy_path: Path) -> ExportReport:
        return await self._run(policy_path, publish=True)

    async def _run(self, policy_path: Path, *, publish: bool) -> ExportReport:
        loaded = _read_policy(policy_path)
        if isinstance(loaded, Outcome):
            return ExportReport(outcome=loaded)
        policy, source = loaded
        refused = _topology_refusals(policy)
        if refused:
            return ExportReport(outcome=denied(";".join(refused)),
                                refusals=tuple(refused))
        try:
            async with self.bindings.mutation():
                return await self._export(policy, source, publish=publish)
        except Exception:
            log.exception("managed export failed")
            return ExportReport(outcome=unavailable("store-failure"))

    async def _export(
        self, policy: Policy, source: str, *, publish: bool
    ) -> ExportReport:
        refused: list[str] = []
        inv: ExportInventory = await self.probe.inventory()
        refused += _inventory_refusals(inv)

        gathered = await _read_traits(self.registry, inv.persons)
        if isinstance(gathered, Outcome):
            return ExportReport(outcome=gathered)
        traits, kratos_refusals = gathered
        refused += kratos_refusals

        relations: dict[Scope, frozenset[tuple[str, str]]] = {}
        for scope in await self.topology.scopes():
            rows = await self.backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return ExportReport(outcome=unavailable(rows.detail))
            relations[scope] = rows
        rel_refusals, folded = _classify_relations(relations)
        refused += rel_refusals

        workspace_names = frozenset(policy.unit_workspaces().values()) | {"default"}
        (people, revoked_accounts, emit_refusals,
         dropped, nonportable, workspaces) = _emit_people(
            inv, traits, workspace_names)
        refused += emit_refusals
        if refused:
            return ExportReport(outcome=denied(";".join(refused)),
                                refusals=tuple(refused))

        live_ids = frozenset(p.person_id for p in inv.persons)
        active_ids = frozenset(
            p.person_id for p in inv.persons if p.lifecycle == "active")
        former_ids = frozenset(f for f, _ in inv.aliases)
        known_ids = live_ids | former_ids
        probe = _PROBE_ID
        while probe in known_ids:
            probe += "-x"
        person_ids = tuple(sorted(known_ids | {probe}))

        convos = policy.rooms()
        rooms = tuple(
            Scope(ScopeKind.ROOM, room_scope_id(convo.platform, cid))
            for convo in convos for cid in sorted(convo.ids))
        matrix = await _matrix(self.access, rooms, person_ids)
        if isinstance(matrix, Outcome):
            return ExportReport(outcome=matrix)
        derived, derive_refusals = _derive_rooms(
            policy=policy, matrix=matrix, person_ids=person_ids,
            active_ids=active_ids, known_ids=known_ids)
        if derive_refusals:
            return ExportReport(
                outcome=denied(";".join(derive_refusals)),
                refusals=tuple(derive_refusals))

        identity_toml = _build_identity(tuple(people), tuple(workspaces))
        policy_toml = _build_policy(source, derived)
        loaded_pair = _validate_bundle(identity_toml, policy_toml)
        if isinstance(loaded_pair, Outcome):
            return ExportReport(outcome=loaded_pair)
        persons, generated_policy = loaded_pair

        open_scope_ids = frozenset(
            room_scope_id(convo.platform, cid)
            for convo in convos if convo.persons is None for cid in convo.ids)
        outcome = await _compare(
            managed=self.access, persons=persons, policy=generated_policy,
            matrix=matrix, convos=convos, person_ids=person_ids,
            open_scope_ids=open_scope_ids,
            emit_identifiers=tuple(
                i for p in people for a in p.accounts for i in a.identifiers),
            suspended_identifiers=tuple(
                (i.platform, i.value)
                for p in inv.persons if p.lifecycle == "suspended"
                for a in inv.accounts
                if a.state == "bound" and a.person_id == p.person_id
                for i in inv.identifiers if i.account_id == a.account_id),
            revoked_identifiers=tuple(
                (i.platform, i.value)
                for a in inv.accounts if a.state == "revoked"
                for i in inv.identifiers if i.account_id == a.account_id),
            active_ids=active_ids)
        if outcome.status is not OutcomeStatus.COMPLETE:
            return ExportReport(outcome=outcome)

        manifest = _manifest(
            identity_toml=identity_toml, policy_toml=policy_toml,
            topology_source=source, inv=inv, people=tuple(people),
            revoked_accounts=revoked_accounts, folded=folded,
            dropped=dropped, nonportable=nonportable)
        digest = f"export:{_digest((identity_toml + policy_toml).encode())[:24]}"
        counts = MappingProxyType({
            "people": len(people),
            "suspended": sum(1 for p in people if p.suspended),
            "accounts": sum(len(p.accounts) for p in people),
            "identifiers": sum(
                len(a.identifiers) for p in people for a in p.accounts),
            "aliases": sum(len(p.former_ids) for p in people),
            "revoked_accounts": len(revoked_accounts),
            "rooms": len(rooms),
            "folded_relations": len(folded),
        })
        return ExportReport(
            outcome=complete(), refusals=(), counts=counts, digest=digest,
            bundle=ExportBundle(
                identity_toml=identity_toml, policy_toml=policy_toml,
                manifest_json=manifest) if publish else None)
