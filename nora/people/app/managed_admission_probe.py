"""The admission's installation-target seam — the empty-or-owned proof and
the two durable publication boundaries (`design/managed-identity-access/
identity.md` «Backend selection and lifecycle»).

`facts` reads every managed row the claim must see — including the rows
the port reads hide. `publish_held_person` and `complete_import` are the
transactions the port's own writes cannot express: a person is never
readable unheld, and complete-plus-released never lands half-visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nora.domain.managed import (
    ManagedAccount,
    ManagedPerson,
    OperationRecord,
    Outcome,
)


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
    scoped_names: int   # an import writes none — any row is foreign state


class AdmissionProbe(Protocol):
    """The concrete installation-target seam — the empty-or-owned proof no
    port read can give, plus the two durable publication boundaries no
    port write can express: each imported person committed WITH this
    import's hold, and the COMPLETE journal outcome committed WITH the
    release of all and only this import's holds."""

    async def facts(self) -> AdmissionFacts:
        """The target's full managed row projection."""

    async def publish_held_person(
        self, *, person: ManagedPerson, accounts: tuple[ManagedAccount, ...],
        operation_id: str,
    ) -> Outcome:
        """Publish `person`, its `accounts` and this import's identity hold
        in ONE durable transaction — the person is held from its first
        externally readable commit, and a failure before that commit
        leaves no part of it behind (a listed person may hold no account
        yet; `accounts` may be empty)."""

    async def complete_import(
        self, record: OperationRecord, *, person_ids: tuple[str, ...]
    ) -> Outcome:
        """Commit `record` (the COMPLETE journal) and release all and only
        this import's holds in ONE durable transaction — an observer sees
        complete-plus-unheld together or keeps incomplete-plus-held. A
        journal the update cannot land is refused, never reported
        complete; a commit whose answer is lost stays honestly uncertain
        until a retry reads the recorded outcome."""
