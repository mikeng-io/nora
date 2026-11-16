"""Managed-to-file export, the preparation phases — the complete inventory
model, the refusals each source produces and the emitted `identity.toml`/
`policy.toml` documents (`managed_export.py` holds the service that
composes these; `managed_export_verify.py` holds the live-authority proof).

The inventory covers what active-only port reads hide — suspended people,
revoked accounts, `left` memberships, holds, unfinished journals, authority
blocks and link state (`AdmissionFacts`'s inverse: not "is the target
empty" but "exactly what is in it"). The archived `file_admission` source
supplies authored metadata only — never ownership or grant authority.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import tomlkit

from nora.domain.identity import (
    ACCOUNT_LABEL_KEY,
    Identifier,
    id_kinds,
    metadata_keys,
)
from nora.domain.managed import (
    Outcome,
    OutcomeStatus,
    Verdict,
    denied,
    unavailable,
)
from nora.domain.managed_ports import IdentityRegistry
from nora.domain.management import MANAGEMENT_PERMITS, PERMITS, Scope
from nora.persons import PLATFORM_FIELDS
from nora.policy import Policy, PolicyError, load_policy

#: `nora/persons_file.py::_PERSON_ID_RE` — the person-id alphabet the file
#: backend validates (it is used verbatim as the Honcho peer id).
_PERSON_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

#: Terminal `LinkState` values — a link journal at rest. Anything else is
#: an unfinished bilateral consent the export must not certify past.
_TERMINAL_LINK_STATES = frozenset({"refused", "cancelled", "expired", "committed"})

#: The managed person schema's declared trait keys — anything else means
#: the store handed back shapes it never wrote: untrustworthy, not extra data.
_TRAIT_KEYS = frozenset({"display_name", "social", "labels"})

#: Never-registered matrix probe id — a closed room denies it in both
#: backends; an OPEN room's `"*"` admits it by contract, so that cell is
#: skipped (the open-room rule already refuses denied KNOWN people).
_PROBE_ID = "nora-export-probe"
_PROBE_VALUE = "nora-export-probe-value"

_ACCOUNT_STATES = frozenset({"bound", "revoked"})
_LIFECYCLES = frozenset({"active", "suspended"})
_MEMBERSHIP_STATES = frozenset({"member", "left"})
_OPERATION_STATES = frozenset({"pending", "incomplete", "complete", "blocked"})
_OPEN_OPERATIONS = frozenset({"pending", "incomplete", "blocked"})

#: `member`/`restricted` and access grant_/deny_ fold into `persons` lists
#: through the matrix; deny_ of a management permit is the file default —
#: `admin` and grant_ of management authority refuse, never fold.
_FOLDABLE_STRUCTURAL = frozenset({"member", "restricted"})


# -- the inventory (what the SQL-aware probe returns) -------------------------


@dataclass(frozen=True)
class InventoryPerson:
    person_id: str
    lifecycle: str
    kratos_identity_id: str | None
    display_name: str


@dataclass(frozen=True)
class InventoryAccount:
    account_id: str
    person_id: str
    platform: str
    state: str
    label: str | None


@dataclass(frozen=True)
class InventoryIdentifier:
    platform: str
    kind: str
    value: str
    account_id: str


@dataclass(frozen=True)
class InventoryMembership:
    person_id: str
    scope: Scope
    state: str
    sources: frozenset[str]


@dataclass(frozen=True)
class InventoryOperation:
    operation_id: str
    kind: str
    state: str
    created_at: str


@dataclass(frozen=True)
class InventoryBlock:
    person_id: str
    scope: Scope
    reason: str


@dataclass(frozen=True)
class ExportInventory:
    """The COMPLETE managed inventory under the gate — including the rows
    the active-only port reads hide: suspended people, revoked accounts,
    `left` memberships, holds, unfinished journals, authority blocks and
    link state (`AdmissionFacts`'s inverse — not "is the target empty" but
    "exactly what is in it")."""
    persons: tuple[InventoryPerson, ...]
    accounts: tuple[InventoryAccount, ...]
    identifiers: tuple[InventoryIdentifier, ...]
    aliases: tuple[tuple[str, str], ...]               # (former_id, person_id)
    memberships: tuple[InventoryMembership, ...]
    operations: tuple[InventoryOperation, ...]
    holds: tuple[tuple[str, str], ...]                 # (person_id, operation_id)
    blocks: tuple[InventoryBlock, ...]
    link_states: tuple[tuple[str, str], ...]           # (request_id, state)
    # The newest COMPLETE `file_admission` journal's archived sources —
    # (operation_id, identity_toml, policy_toml) — the authored-metadata
    # archive, never ownership or grant authority.
    archive: tuple[str, str, str] | None


class ExportProbe(Protocol):
    """The concrete installation-state seam — the complete inventory no
    public port read can give (`AdmissionProbe`'s export counterpart)."""

    async def inventory(self) -> ExportInventory:
        """Every managed row the export must reason over."""


# -- the emit model (pure) ----------------------------------------------------


@dataclass(frozen=True)
class _EmitAccount:
    account_id: str                        # provenance only — never emitted
    platform: str
    label: str | None
    identifiers: tuple[Identifier, ...]    # matchable, sorted
    metadata: tuple[tuple[str, str], ...]  # archived non-matchable, sorted


@dataclass(frozen=True)
class _EmitPerson:
    person_id: str
    name: str                              # empty → file falls back to the id
    first_name: str
    last_name: str
    aliases: tuple[str, ...]
    former_ids: tuple[str, ...]
    social: bool                           # False emits `social = false`
    suspended: bool
    accounts: tuple[_EmitAccount, ...]
    kratos_identity_id: str | None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scope_key(scope: Scope) -> str:
    return f"{scope.kind.value}:{scope.id}"


# -- source loading ------------------------------------------------------------


def _read_policy(policy_path: Path) -> tuple[Policy, str] | Outcome:
    """The topology via the real loader, kept verbatim for the tomlkit overlay."""
    try:
        source = policy_path.read_bytes()
    except OSError as error:
        return denied(f"unreadable-source:{type(error).__name__}")
    try:
        policy = load_policy(policy_path)
    except (PolicyError, OSError, UnicodeError) as error:
        return denied(f"invalid-policy-file:{type(error).__name__}")
    return policy, source.decode("utf-8")


def _topology_refusals(policy: Policy) -> list[str]:
    """Topology shapes the export cannot evaluate — guild rooms and `[dm]`."""
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


# -- phase 1: the SQL inventory's own refusals ---------------------------------


def _inventory_refusals(inv: ExportInventory) -> list[str]:
    """Incomplete publication and ambiguity the binding rows alone prove."""
    refused: list[str] = []
    live_ids = {p.person_id for p in inv.persons}

    if any(p.lifecycle not in _LIFECYCLES for p in inv.persons):
        refused.append("untrustworthy:unknown-lifecycle")
    kratos_ids = [p.kratos_identity_id for p in inv.persons
                  if p.kratos_identity_id is not None]
    if len(set(kratos_ids)) != len(kratos_ids):
        refused.append("ambiguous-ownership:shared-kratos-identity")
    for pid in live_ids:
        if not _PERSON_ID_RE.match(pid):
            refused.append(f"unrepresentable:person-id:{pid}")
    for former, _target in inv.aliases:
        if former in live_ids:
            refused.append(f"ambiguous-ownership:alias-is-live:{former}")

    if any(a.state not in _ACCOUNT_STATES for a in inv.accounts):
        refused.append("untrustworthy:unknown-account-state")
    if any(a.person_id not in live_ids for a in inv.accounts):
        refused.append("untrustworthy:dangling-account")

    # The file index flattens kind away and managed's own resolve() answers
    # .limit(1): the same value on two accounts is ambiguous EVERYWHERE, so
    # it refuses rather than picking an owner.
    account_state = {a.account_id: a.state for a in inv.accounts}
    account_owner = {a.account_id: a.person_id for a in inv.accounts}
    claimants: dict[tuple[str, str], set[str]] = {}
    for ident in inv.identifiers:
        if account_state.get(ident.account_id) != "bound":
            continue
        claimants.setdefault((ident.platform, ident.value), set()).add(
            ident.account_id)
    for (platform, _value), account_ids in sorted(claimants.items()):
        if len(account_ids) < 2:
            continue
        if len({account_owner[a] for a in account_ids}) > 1:
            refused.append(f"conflicting-ownership:{platform}")
        else:
            refused.append(
                f"ambiguous-ownership:cross-account-identifier:{platform}")

    for op in inv.operations:
        if op.state not in _OPERATION_STATES:
            refused.append("untrustworthy:unknown-operation-state")
        elif op.state in _OPEN_OPERATIONS:
            refused.append(f"incomplete-publication:operation:{op.kind}")
    if inv.holds:
        refused.append(f"incomplete-publication:holds:{len(inv.holds)}")
    if inv.blocks:
        refused.append(
            f"incomplete-publication:authority-blocks:{len(inv.blocks)}")
    for _request_id, state in inv.link_states:
        if state not in _TERMINAL_LINK_STATES:
            refused.append(f"incomplete-publication:link:{state or 'malformed'}")
    if any(m.state not in _MEMBERSHIP_STATES for m in inv.memberships):
        refused.append("untrustworthy:unknown-membership-state")
    return refused


# -- phase 2: Kratos (descriptive truth, fail-closed) --------------------------


async def _read_traits(
    registry: IdentityRegistry, people: tuple[InventoryPerson, ...],
) -> tuple[dict[str, dict[str, Any]], list[str]] | Outcome:
    """Current traits per person — active fail closed, suspended best-effort."""
    traits: dict[str, dict[str, Any]] = {}
    refused: list[str] = []
    for person in people:
        pid = person.person_id
        if person.lifecycle == "active":
            if person.kratos_identity_id is None:
                refused.append(f"untrustworthy:uncorrelated-person:{pid}")
                continue
            state = await registry.identity_state(external_id=pid)
            if state.verdict is Verdict.UNAVAILABLE:
                return unavailable(state.detail)
            if state.verdict is Verdict.UNKNOWN_SCOPE:
                refused.append(f"untrustworthy:kratos-correlation-missing:{pid}")
                continue
            if state.verdict is not Verdict.ALLOWED:
                refused.append(f"untrustworthy:state-disagreement:{pid}")
                continue
        outcome, raw = await registry.identity_traits(external_id=pid)
        if raw is None:
            if outcome.status is OutcomeStatus.UNAVAILABLE:
                return unavailable(outcome.detail)
            if person.lifecycle == "active":
                refused.append(f"untrustworthy:kratos-correlation-missing:{pid}")
            continue  # suspended + absent: the SQL display name carries them
        if (not isinstance(raw, dict) or not set(raw) <= _TRAIT_KEYS
                or not isinstance(raw.get("display_name"), str)
                or not isinstance(raw.get("social"), bool)
                or ("labels" in raw and (
                    not isinstance(raw["labels"], dict)
                    or not all(isinstance(k, str) and isinstance(v, str)
                               for k, v in raw["labels"].items())))):
            refused.append(f"untrustworthy:malformed-traits:{pid}")
            continue
        traits[pid] = raw
    return traits, refused


# -- phase 3: Keto relation classification -------------------------------------


def _classify_relations(
    relations: dict[Scope, frozenset[tuple[str, str]]],
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Management authority and unknown relations refuse; the rest fold."""
    refused: list[str] = []
    folded: list[tuple[str, str, str]] = []
    permit_relations = {
        f"{direction}_{permit}"
        for direction in ("grant", "deny") for permit in PERMITS
    }
    granted_management = {
        f"grant_{permit}" for permit in MANAGEMENT_PERMITS
    }
    for scope, rows in relations.items():
        for relation, subject in sorted(rows):
            if relation == "admin" or relation in granted_management:
                refused.append(
                    f"unfaithful:management-authority:{_scope_key(scope)}:{relation}")
            elif relation not in _FOLDABLE_STRUCTURAL | permit_relations:
                refused.append(
                    f"unfaithful:unknown-relation:{_scope_key(scope)}:{relation}")
            else:
                folded.append((_scope_key(scope), relation, subject))
    return refused, folded


# -- phase 4: the people model -------------------------------------------------


def _parse_archive(inv: ExportInventory) -> dict[str, Any] | str | None:
    """The archived admission source — metadata only; malformed is untrustworthy."""
    if inv.archive is None:
        return None
    try:
        return tomllib.loads(inv.archive[1])
    except tomllib.TOMLDecodeError:
        return "malformed"


def _archive_index(
    archive_persons: dict[str, Any], inv: ExportInventory,
) -> dict[tuple[str, frozenset[tuple[str, str]]], tuple[str, list[tuple[str, str]]]]:
    """(platform, identifier-set) -> (owner, metadata) — the overlay key."""
    index: dict[tuple[str, frozenset[tuple[str, str]]],
                tuple[str, list[tuple[str, str]]]] = {}
    alias_map = dict(inv.aliases)
    for archived_pid, entry in archive_persons.items():
        if not isinstance(entry, dict):
            continue
        canonical = alias_map.get(archived_pid, archived_pid)
        for platform in PLATFORM_FIELDS:
            for block in entry.get(platform) or ():
                if not isinstance(block, dict):
                    continue
                matchable = frozenset(
                    (kind, str(value)) for kind, value in block.items()
                    if kind in id_kinds(platform))
                if not matchable:
                    continue
                metadata = sorted(
                    (kind, str(value)) for kind, value in block.items()
                    if kind in metadata_keys(platform))
                index[(platform, matchable)] = (canonical, metadata)
    return index


def _emit_account(
    account: InventoryAccount, rows: list[InventoryIdentifier],
    account_index: dict[tuple[str, frozenset[tuple[str, str]]],
                        tuple[str, list[tuple[str, str]]]],
    owner: str,
) -> tuple[_EmitAccount | None, str | None]:
    """One bound account's emitted identifiers plus applicable archived metadata."""
    platform = account.platform
    if platform not in PLATFORM_FIELDS:
        return None, f"unrepresentable:platform:{platform}"
    if not rows:
        return None, f"unfaithful:unidentifiable-account:{account.account_id}"
    bad = [i.kind for i in rows if i.kind not in id_kinds(platform)]
    if bad:
        return None, f"unrepresentable:identifier-kind:{platform}.{bad[0]}"
    emitted = tuple(sorted(
        (Identifier(platform=platform, kind=i.kind, value=i.value)
         for i in rows),
        key=lambda i: (i.kind, i.value)))
    overlay = account_index.get(
        (platform, frozenset((i.kind, i.value) for i in emitted)))
    if overlay is not None and overlay[0] != owner:
        return None, f"ambiguous-ownership:archived-binding-conflict:{platform}"
    return _EmitAccount(
        account_id=account.account_id, platform=platform,
        label=account.label, identifiers=emitted,
        metadata=tuple(overlay[1] if overlay else ())), None


def _emit_people(
    inv: ExportInventory,
    traits: dict[str, dict[str, Any]],
    workspace_names: frozenset[str],
) -> tuple[list[_EmitPerson], list[tuple[str, str, str | None,
            list[tuple[str, str]]]], list[str], list[str], list[str],
           list[tuple[str, str]]]:
    """The `identity.toml` model — live ownership, Kratos fields, archive overlay."""
    refused: list[str] = []
    dropped: list[str] = []
    nonportable: list[str] = []
    workspace_rows: list[tuple[str, str]] = []

    archived = _parse_archive(inv)
    if isinstance(archived, str):
        refused.append("untrustworthy:malformed-archive")
        archived = None
    archive_persons = (archived or {}).get("persons", {})
    archive_workspaces = (archived or {}).get("workspaces", {})
    account_index = _archive_index(archive_persons, inv) if archive_persons else {}

    live_ids = {p.person_id for p in inv.persons}
    for former, target in inv.aliases:
        if target not in live_ids:
            dropped.append(f"former_person_ids:{former}")

    identifiers_by_account: dict[str, list[InventoryIdentifier]] = {}
    for ident in inv.identifiers:
        identifiers_by_account.setdefault(ident.account_id, []).append(ident)

    accounts_by_person: dict[str, list[InventoryAccount]] = {}
    revoked_accounts: list[tuple[str, str, str | None, list[tuple[str, str]]]] = []
    for account in inv.accounts:
        if account.state == "revoked":
            revoked_accounts.append((
                account.account_id, account.platform, account.label,
                sorted((i.kind, i.value)
                       for i in identifiers_by_account.get(account.account_id, ())),
            ))
            continue
        accounts_by_person.setdefault(account.person_id, []).append(account)

    people: list[_EmitPerson] = []
    for person in sorted(inv.persons, key=lambda p: p.person_id):
        pid = person.person_id
        suspended = person.lifecycle == "suspended"
        trait = traits.get(pid, {})
        if trait.get("labels"):
            nonportable.append(f"labels:{pid}:{len(trait['labels'])}")
        name = trait.get("display_name", "") or person.display_name

        former_ids = tuple(sorted(f for f, t in inv.aliases if t == pid))
        authored = _archived_person_fields(archive_persons, pid, former_ids)

        emit_accounts: list[_EmitAccount] = []
        if not suspended:  # a suspended person holds no accounts in file mode
            for account in sorted(
                accounts_by_person.get(pid, []),
                key=lambda a: (a.platform, a.account_id),
            ):
                emitted, refusal = _emit_account(
                    account, identifiers_by_account.get(account.account_id, []),
                    account_index, pid)
                if refusal is not None:
                    refused.append(refusal)
                elif emitted is not None:
                    emit_accounts.append(emitted)
        # The file validator needs a distinct non-blank `label` per block
        # once a person holds two accounts on one platform.
        labels_by_platform: dict[str, list[str]] = {}
        for a in emit_accounts:
            labels_by_platform.setdefault(a.platform, []).append(a.label or "")
        for platform, labels in labels_by_platform.items():
            if len(labels) > 1 and (
                any(not label.strip() for label in labels)
                or len(set(labels)) != len(labels)
            ):
                refused.append(
                    f"unrepresentable:unlabeled-accounts:{pid}:{platform}")

        people.append(_EmitPerson(
            person_id=pid,
            name=name,
            first_name=authored.get("first_name", ""),
            last_name=authored.get("last_name", ""),
            aliases=tuple(authored.get("aliases", ())),
            former_ids=former_ids,
            social=bool(trait.get("social")) and not suspended,
            suspended=suspended,
            accounts=tuple(sorted(
                emit_accounts,
                key=lambda a: (a.platform,
                               [(i.kind, i.value) for i in a.identifiers]))),
            kratos_identity_id=person.kratos_identity_id,
        ))

    for name in sorted(archive_workspaces):
        binding = archive_workspaces[name]
        value = binding.get("honcho_workspace") if isinstance(binding, dict) else None
        if not isinstance(value, str) or not value.strip():
            dropped.append(f"workspaces:{name}")
            continue
        if name not in workspace_names:
            dropped.append(f"workspaces:{name}")
            continue
        workspace_rows.append((name, value))
    return (people, revoked_accounts, refused, dropped, nonportable,
            workspace_rows)


def _archived_person_fields(
    archive_persons: dict[str, Any], person_id: str, former_ids: tuple[str, ...],
) -> dict[str, Any]:
    """The person's archived block, filling from a former id's block when renamed."""
    out: dict[str, Any] = {}
    candidates = [archive_persons.get(person_id)]
    candidates += [archive_persons.get(f) for f in former_ids]
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        for key in ("first_name", "last_name"):
            if key not in out and isinstance(entry.get(key), str) and entry[key]:
                out[key] = entry[key]
        if "aliases" not in out and isinstance(entry.get("aliases"), list):
            out["aliases"] = sorted(str(a) for a in entry["aliases"])
    return out


# -- phase 6: document building (tomlkit — the installed TOML writer) ----------


def _build_identity(people: tuple[_EmitPerson, ...],
                    workspaces: tuple[tuple[str, str], ...]) -> str:
    doc = tomlkit.document()
    persons_table = tomlkit.table(is_super_table=True)
    for person in people:
        entry = tomlkit.table()
        if person.name:
            entry["name"] = person.name
        if person.first_name:
            entry["first_name"] = person.first_name
        if person.last_name:
            entry["last_name"] = person.last_name
        if person.aliases:
            entry["aliases"] = list(person.aliases)
        if person.former_ids:
            entry["former_person_ids"] = list(person.former_ids)
        if not person.social:
            entry["social"] = False
        for account in person.accounts:
            block = tomlkit.table()
            for ident in account.identifiers:
                block[ident.kind] = ident.value
            for key, value in account.metadata:
                block[key] = value
            if account.label is not None:
                block[ACCOUNT_LABEL_KEY] = account.label
            existing = entry.get(account.platform)
            if existing is None:
                array = tomlkit.aot()
                array.append(block)
                entry[account.platform] = array
            else:
                existing.append(block)
        persons_table[person.person_id] = entry
    doc["persons"] = persons_table
    if workspaces:
        table = tomlkit.table(is_super_table=True)
        for name, binding in workspaces:
            row = tomlkit.table()
            row["honcho_workspace"] = binding
            table[name] = row
        doc["workspaces"] = table
    return tomlkit.dumps(doc)


def _build_policy(source: str, derived: dict[tuple[str, str], Any]) -> str:
    """The topology document verbatim — only `persons` is re-derived."""
    doc = tomlkit.parse(source)
    for convo in doc.get("conversations", ()):
        key = (convo.get("platform"), convo.get("id"))
        if key in derived:
            convo["persons"] = derived[key]
    return tomlkit.dumps(doc)
