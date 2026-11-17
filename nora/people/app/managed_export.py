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
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from nora.access import IdentityAccess
from nora.domain.managed import (
    CONTAINMENT_RELATIONS,
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
    TopologySource,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.app.managed_export_prepare import (
    _PROBE_ID,
    ExportInventory,
    ExportProbe,
    ExportRefusal,
    _build_identity,
    _build_policy,
    _classify_relations,
    _digest,
    _emit_people,
    _inventory_refusals,
    _read_identities,
    _read_policy,
    _scope_key,
    _source_unchanged,
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
    `denied` (authoritative refusal — `refusals` names every limit found
    by bounded `code`, with identifying specifics kept in `detail`),
    `unavailable` (a store could not answer — retryable)."""
    outcome: Outcome
    refusals: tuple[ExportRefusal, ...] = ()
    counts: MappingProxyType[str, int] = field(
        default_factory=lambda: MappingProxyType({}))
    digest: str = ""
    bundle: ExportBundle | None = None


def _denied(refused: tuple[ExportRefusal, ...] | list[ExportRefusal]) -> Outcome:
    return denied(";".join(dict.fromkeys(r.code for r in refused)))


# -- the service ----------------------------------------------------------------


@dataclass(frozen=True)
class ManagedExport:
    """The installation-only export service. `bindings`/`backend`/
    `topology`/`access` are the managed backend's own objects — the same
    graph `managed_services` composes — and `probe` the SQL-aware
    inventory plus read-only correlated-identity seam.

    `source_snapshot` optionally carries the command's already-parsed
    `(policy, source_text)` — when set, the SAME immutable topology the
    wiring consumed drives derivation, comparison and publication rather
    than a second read of the path. Either way the source is re-read once
    more before a bundle may appear: a changed file refuses."""
    bindings: BindingStore
    backend: AuthorityBackend
    topology: TopologySource
    access: IdentityAccess
    probe: ExportProbe
    source_snapshot: tuple[Policy, str] | None = None

    async def assess(self, policy_path: Path) -> ExportReport:
        """The read-only plan/validation path — the whole pipeline except
        the bundle hand-off."""
        return await self._run(policy_path, publish=False)

    async def export(self, policy_path: Path) -> ExportReport:
        return await self._run(policy_path, publish=True)

    async def _run(self, policy_path: Path, *, publish: bool) -> ExportReport:
        async with self.prepared(policy_path, publish=publish) as report:
            return report

    @asynccontextmanager
    async def prepared(
        self, policy_path: Path, *, publish: bool = True
    ) -> AsyncIterator[ExportReport]:
        """Hold the managed mutation gate through the caller's publication decision."""
        if self.source_snapshot is not None:
            policy, source = self.source_snapshot
        else:
            loaded = _read_policy(policy_path)
            if isinstance(loaded, Outcome):
                yield ExportReport(outcome=loaded)
                return
            policy, source = loaded
        refused = _topology_refusals(policy)
        if refused:
            yield ExportReport(outcome=_denied(refused), refusals=tuple(refused))
            return
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(self.bindings.mutation())
                report = await self._export(
                    policy, source, policy_path, publish=publish)
            except Exception as error:  # noqa: BLE001 — bounded private diagnostics
                log.error("managed export failed: %s", type(error).__name__)
                report = ExportReport(outcome=unavailable("store-failure"))
            yield report

    async def _relations(self) -> Outcome | tuple[
        dict[Scope, frozenset[tuple[str, str]]],
        list[ExportRefusal], list[tuple[str, str, str]],
    ]:
        """Inventory every subject shape, preserving only configured containment."""
        people: dict[Scope, frozenset[tuple[str, str]]] = {}
        refused: list[ExportRefusal] = []
        containment: list[tuple[str, str, str]] = []
        for scope in await self.topology.scopes():
            rows = await self.backend.scope_relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable("authority-read")
            parent = await self.topology.parent_of(scope)
            people[scope] = frozenset((r.relation, r.subject) for r in rows
                                      if isinstance(r.subject, str))
            for row in rows:
                if isinstance(row.subject, str):
                    continue
                if (row.relation != CONTAINMENT_RELATIONS.get(scope.kind)
                        or row.subject != parent):
                    refused.append(ExportRefusal("unfaithful:subject-set-relation",
                        f"{_scope_key(scope)}:{row.relation}"))
                else:
                    containment.append((_scope_key(scope), row.relation, _scope_key(row.subject)))
        return people, refused, containment

    async def _export(
        self, policy: Policy, source: str, policy_path: Path, *, publish: bool
    ) -> ExportReport:
        refused: list[ExportRefusal] = []
        inv: ExportInventory = await self.probe.inventory()
        refused += _inventory_refusals(inv)

        gathered = await _read_identities(self.probe, inv.persons)
        if isinstance(gathered, Outcome):
            return ExportReport(outcome=gathered)
        traits, kratos_refusals = gathered
        refused += kratos_refusals

        gathered_relations = await self._relations()
        if isinstance(gathered_relations, Outcome):
            return ExportReport(outcome=gathered_relations)
        relations, typed_refusals, containment = gathered_relations
        rel_refusals, folded = _classify_relations(relations)
        refused += typed_refusals + rel_refusals
        folded = sorted([*folded, *containment])

        workspace_names = frozenset(policy.unit_workspaces().values()) | {"default"}
        (people, revoked_accounts, suspended_accounts, emit_refusals,
         dropped, nonportable, workspaces) = _emit_people(
            inv, traits, workspace_names)
        refused += emit_refusals
        if refused:
            return ExportReport(outcome=_denied(refused),
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
                outcome=_denied(derive_refusals),
                refusals=tuple(derive_refusals))

        identity_toml = _build_identity(tuple(people), tuple(workspaces))
        policy_toml = _build_policy(source, derived)
        loaded_pair = _validate_bundle(identity_toml, policy_toml)
        if isinstance(loaded_pair, Outcome):
            return ExportReport(outcome=loaded_pair)
        persons, generated_policy = loaded_pair

        outcome = await _compare(
            managed=self.access, persons=persons, policy=generated_policy,
            matrix=matrix, convos=convos, person_ids=person_ids,
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

        # The immutable snapshot carried every phase — one last read of the
        # source before a usable bundle may appear: a file that changed
        # mid-run refuses rather than publishing a stale cut.
        unchanged = _source_unchanged(policy_path, source)
        if isinstance(unchanged, Outcome):
            return ExportReport(outcome=unchanged)
        if not unchanged:
            refusal = ExportRefusal("untrustworthy:source-changed")
            return ExportReport(
                outcome=_denied([refusal]), refusals=(refusal,))

        manifest = _manifest(
            identity_toml=identity_toml, policy_toml=policy_toml,
            topology_source=source, inv=inv, people=tuple(people),
            revoked_accounts=revoked_accounts,
            suspended_accounts=suspended_accounts, folded=folded,
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
            "suspended_accounts": len(suspended_accounts),
            "rooms": len(rooms),
            "folded_relations": len(folded),
        })
        return ExportReport(
            outcome=complete(), refusals=(), counts=counts, digest=digest,
            bundle=ExportBundle(
                identity_toml=identity_toml, policy_toml=policy_toml,
                manifest_json=manifest) if publish else None)
