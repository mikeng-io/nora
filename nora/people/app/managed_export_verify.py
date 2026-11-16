"""Managed-to-file export, the live-authority proof —
`managed_export_prepare.py` decides what MAY be emitted; these phases read
the production `access.check` verdicts, derive the `persons` lists from
them, run the generated pair through the REAL file loaders and compare the
file predicate verdict-for-verdict before the manifest is written.

Any cell where the generated file disagrees with managed authority —
a speak/view divergence, an also_id split, an open-room denial, a resolve
or social mismatch — refuses the WHOLE export rather than publishing a
bundle that would widen or narrow access.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from nora.access import IdentityAccess, SocialPreferenceUnavailableError
from nora.domain.identity import Identifier
from nora.domain.managed import (
    Outcome,
    Verdict,
    complete,
    denied,
    room_scope_id,
    unavailable,
)
from nora.domain.management import PERMITS, Scope, ScopeKind
from nora.people.app.managed_export_prepare import (
    _PROBE_VALUE,
    ExportInventory,
    ExportRefusal,
    _digest,
    _EmitPerson,
    _scope_key,
)
from nora.persons import Persons, PersonsError, load_persons
from nora.policy import Policy, PolicyError, load_policy

# -- phase 5: the verdict matrix + room derivation ------------------------------


async def _matrix(
    access: IdentityAccess,
    rooms: tuple[Scope, ...], person_ids: tuple[str, ...],
) -> dict[tuple[Scope, str, str], Verdict] | Outcome:
    """Live managed verdicts for every (room, person, permit) via `access.check`."""
    verdicts: dict[tuple[Scope, str, str], Verdict] = {}
    for scope in rooms:
        for person_id in person_ids:
            for permit in sorted(PERMITS):
                result = await access.check(
                    scope=scope, person_id=person_id, permit=permit)
                if result.verdict is Verdict.UNAVAILABLE:
                    return unavailable(result.detail)
                if result.verdict is Verdict.UNKNOWN_SCOPE:
                    return denied("unfaithful:unknown-scope")
                verdicts[(scope, person_id, permit)] = result.verdict
    return verdicts


def _derive_rooms(
    *,
    policy: Policy,
    matrix: dict[tuple[Scope, str, str], Verdict],
    person_ids: tuple[str, ...],
    active_ids: frozenset[str],
    known_ids: frozenset[str],
) -> tuple[dict[tuple[str, str], Any], list[ExportRefusal]]:
    """Each convo's emitted `persons` — the allowed list — plus divergences.

    A `persons = "*"` convo is always refused, never frozen: file `*`
    admits every presented identifier, including identities managed has
    never registered — a widening no allowed-list can express, and listing
    the currently-allowed would close the room to senders managed admits
    dynamically. Neither shape is faithful, so the whole export refuses."""
    derived: dict[tuple[str, str], Any] = {}
    refused: list[ExportRefusal] = []
    for convo in policy.rooms():
        allowed_per_id: dict[str, frozenset[str]] = {}
        for cid in sorted(convo.ids):
            scope = Scope(ScopeKind.ROOM, room_scope_id(convo.platform, cid))
            for person_id in person_ids:
                speak = matrix[(scope, person_id, "speak")]
                view = matrix[(scope, person_id, "view")]
                if speak is not view:
                    refused.append(ExportRefusal(
                        "unfaithful:speak-view-divergence",
                        f"{scope.id}:{person_id}"))
            allowed_per_id[cid] = frozenset(
                pid for pid in active_ids
                if matrix[(scope, pid, "speak")] is Verdict.ALLOWED)
        if len({frozenset(ids) for ids in allowed_per_id.values()}) > 1:
            refused.append(ExportRefusal(
                "unfaithful:also-id-divergence",
                f"{convo.platform}:{sorted(convo.ids)}"))
            continue
        if convo.persons is None:
            scope0 = Scope(ScopeKind.ROOM,
                           room_scope_id(convo.platform, sorted(convo.ids)[0]))
            denied_known = [
                pid for pid in sorted(known_ids)
                if matrix[(scope0, pid, "speak")] is not Verdict.ALLOWED]
            if denied_known:
                refused.append(ExportRefusal(
                    "unfaithful:open-room-denial",
                    f"{scope0.id}:{','.join(denied_known)}"))
            else:
                refused.append(ExportRefusal(
                    "unfaithful:open-room-dynamic", scope0.id))
            continue
        derived[(convo.platform, convo.primary_id)] = sorted(
            allowed_per_id[sorted(convo.ids)[0]])
    return derived, refused


# -- phase 7: real-loader validation + predicate comparison ---------------------


def _validate_bundle(
    identity_toml: str, policy_toml: str,
) -> tuple[Persons, Policy] | Outcome:
    """The generated pair through the REAL file loaders."""
    with TemporaryDirectory(prefix="nora-export-") as directory:
        identity_path = Path(directory) / "identity.toml"
        policy_path = Path(directory) / "policy.toml"
        identity_path.write_text(identity_toml)
        policy_path.write_text(policy_toml)
        try:
            persons = load_persons(identity_path)
        except (PersonsError, OSError, UnicodeError) as error:
            return denied(f"generated-invalid-identity:{type(error).__name__}")
        try:
            policy = load_policy(policy_path)
        except (PolicyError, OSError, UnicodeError) as error:
            return denied(f"generated-invalid-policy:{type(error).__name__}")
    return persons, policy


async def _compare(
    *,
    managed: IdentityAccess,
    persons: Persons, policy: Policy,
    matrix: dict[tuple[Scope, str, str], Verdict],
    convos: tuple[Any, ...],
    person_ids: tuple[str, ...],
    emit_identifiers: tuple[Identifier, ...],
    suspended_identifiers: tuple[tuple[str, str], ...],
    revoked_identifiers: tuple[tuple[str, str], ...],
    active_ids: frozenset[str],
) -> Outcome:
    """The generated file predicate against the live managed verdicts —
    every matrix cell, known person and never-registered probe alike. The
    identifying specifics stay in the refusal detail; the verdict carries
    only the bounded category."""
    file_access = IdentityAccess.for_files(persons, policy)
    for (scope, person_id, permit), verdict in matrix.items():
        result = await file_access.check(
            scope=scope, person_id=person_id, permit=permit)
        if result.verdict is not verdict:
            return denied("unfaithful:verdict-mismatch")
    for convo in convos:
        for cid in convo.ids:
            managed_result = await managed.permitted(
                platform=convo.platform, conversation_id=cid, person_id=None)
            if managed_result.verdict is Verdict.UNAVAILABLE:
                return unavailable(managed_result.detail)
            file_result = await file_access.permitted(
                platform=convo.platform, conversation_id=cid, person_id=None)
            if file_result.verdict is not managed_result.verdict:
                return denied("unfaithful:open-admission-mismatch")
    for ident in emit_identifiers:
        file_account = await file_access.resolve_account(
            ident.platform, ident.value)
        managed_account = await managed.resolve_account(
            ident.platform, ident.value)
        if (file_account is None or managed_account is None
                or file_account.person_id != managed_account.person_id):
            return denied("unfaithful:resolve-mismatch")
    for platform, value in suspended_identifiers + revoked_identifiers:
        if await file_access.resolve_account(platform, value) is not None:
            return denied("unfaithful:dead-identifier-emitted")
    for platform, value in revoked_identifiers:
        if await managed.resolve_account(platform, value) is not None:
            return denied("unfaithful:revoked-identifier-resolves")
    if (emit_identifiers and _PROBE_VALUE not in {
        i.value for i in emit_identifiers
    } and await managed.resolve_account("signal", _PROBE_VALUE) is not None):
        return denied("unfaithful:probe-identifier-resolves")
    for person_id in sorted(active_ids):
        try:
            managed_opted = await managed.social_off(person_id)
        except SocialPreferenceUnavailableError:
            return unavailable("social-preference")
        if bool(persons.social_off(person_id)) != bool(managed_opted):
            return denied("unfaithful:social-mismatch")
    return complete()


# -- the manifest ---------------------------------------------------------------


def _manifest(
    *,
    identity_toml: str, policy_toml: str, topology_source: str,
    inv: ExportInventory,
    people: tuple[_EmitPerson, ...],
    revoked_accounts: list[tuple[str, str, str | None, list[tuple[str, str]]]],
    suspended_accounts: list[tuple[str, str, str | None, str,
                                  list[tuple[str, str]]]],
    folded: list[tuple[str, str, str]],
    dropped: list[str], nonportable: list[str],
) -> str:
    """The private provenance record — digests plus every fact the file
    pair cannot carry: Kratos labels, suspended/revoked bindings with
    their identifiers, folded relations, memberships. This is the one
    artifact identifying specifics may live in."""
    archive = inv.archive
    return json.dumps({
        "format": "nora-managed-export/1",
        "identity_sha256": _digest(identity_toml.encode()),
        "policy_sha256": _digest(policy_toml.encode()),
        "topology_sha256": _digest(topology_source.encode()),
        "archive_operation_id": archive[0] if archive else None,
        "archive_identity_sha256": _digest(archive[1].encode()) if archive else None,
        "archive_policy_sha256": _digest(archive[2].encode()) if archive else None,
        "people": [{
            "person_id": p.person_id,
            "suspended": p.suspended,
            "kratos_identity_id": p.kratos_identity_id,
            "former_person_ids": list(p.former_ids),
            "labels": dict(p.labels),
            "accounts": len(p.accounts),
        } for p in people],
        "revoked_accounts": [{
            "account_id": account_id, "platform": platform, "label": label,
            "identifiers": [[k, v] for k, v in identifiers],
        } for account_id, platform, label, identifiers in revoked_accounts],
        "suspended_accounts": [{
            "account_id": account_id, "person_id": person_id,
            "platform": platform, "label": label,
            "identifiers": [[k, v] for k, v in identifiers],
        } for account_id, platform, label, person_id, identifiers
            in suspended_accounts],
        "folded_relations": [
            {"scope": scope, "relation": relation, "subject": subject}
            for scope, relation, subject in folded
        ],
        "memberships": [{
            "person_id": m.person_id, "scope": _scope_key(m.scope),
            "state": m.state, "sources": sorted(m.sources),
        } for m in inv.memberships],
        "dropped": sorted(dropped),
        "nonportable": sorted(nonportable),
    }, indent=2, sort_keys=True) + "\n"
