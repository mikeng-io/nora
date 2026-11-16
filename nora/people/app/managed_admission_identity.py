"""Admission-owned Kratos identities — durable creation evidence and the
correlated concrete read (`design/managed-identity-access/identity.md`:
`external_id` correlates, it never proves ownership).

Ordinary `IdentityRegistry.ensure_identity` resolves a 409 by read-back on
`external_id` alone — right for registration, wrong for the one-time
import: an unrelated identity already holding an imported person's
external id must refuse untouched, and a create that committed remotely
before its local correlation must be recoverable by a NEW process reading
the SAME durable evidence. The marker therefore rides the remote write
itself in `metadata_admin` — admin-only, never a holder-editable
descriptive trait — and binds the record to this exact import and its
intended effect. It is command ownership only: account ownership stays in
the binding store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from nora.domain.managed import Outcome, OutcomeStatus, complete, denied, unavailable


@dataclass(frozen=True)
class AdmissionIdentity:
    """A Kratos identity as the admission reads it — id, correlation key,
    lifecycle state, descriptive traits and the admin metadata the import's
    ownership evidence lives in."""

    identity_id: str
    external_id: str | None
    state: str
    traits: dict[str, Any]
    metadata_admin: dict[str, Any] | None


class AdmissionKratos(Protocol):
    """The narrow concrete Kratos surface the admission needs — create
    carrying the durable import marker, and read the concrete record back
    by `external_id`. Values only; every remote answer is an `Outcome`."""

    async def create(
        self, *, external_id: str, traits: dict[str, Any], owner: dict[str, Any]
    ) -> tuple[Outcome, AdmissionIdentity | None]:
        """Write the identity with `owner` as its admin metadata.
        `denied("external-id-conflict")` when another record already holds
        the key — what it is, the caller proves by `read`."""
        ...

    async def read(
        self, *, external_id: str
    ) -> tuple[Outcome, AdmissionIdentity | None]:
        """The concrete record, `denied("absent")` when none —
        `unavailable` whenever the service cannot answer."""
        ...


#: The `metadata_admin` key the import stamps its creations with.
OWNER_KEY = "nora_admission"


def admission_marker(*, import_id: str, effect_sha256: str) -> dict[str, Any]:
    """The durable command evidence written at the remote create — one
    import, one intended effect; never account ownership."""
    return {
        OWNER_KEY: {"import_id": import_id, "effect_sha256": effect_sha256},
    }


def identity_owned(view: AdmissionIdentity, marker: dict[str, Any]) -> bool:
    """The record carries THIS import's marker — missing, malformed or
    mismatched evidence fails closed; `external_id` equality, an `active`
    flag and descriptive traits are never ownership proof."""
    metadata = view.metadata_admin
    return (
        isinstance(metadata, dict)
        and metadata.get(OWNER_KEY) == marker[OWNER_KEY]
    )


async def ensure_owned(
    kratos: AdmissionKratos, *, external_id: str, traits: dict[str, Any],
    marker: dict[str, Any],
) -> tuple[Outcome, str | None]:
    """Create the identity carrying `marker`, or recover the SAME owned
    record a crash left un-correlated. A collision whose read-back lacks
    this import's marker is foreign: refused, never adopted or altered."""
    outcome, view = await kratos.create(
        external_id=external_id, traits=traits, owner=marker)
    if view is not None:
        if (view.external_id != external_id or not identity_owned(view, marker)
                or view.state != "active"):
            return unavailable("owner-evidence-mismatch"), None
        return complete(), view.identity_id
    if outcome.detail != "external-id-conflict":
        return outcome, None
    # A remote create committed before this process lost its local
    # correlation — or an unrelated record now holds the key. Only the
    # durable marker tells them apart.
    outcome, view = await kratos.read(external_id=external_id)
    if view is None:
        if outcome.status is OutcomeStatus.DENIED:
            return unavailable("conflict-unresolved"), None
        return outcome, None
    if not identity_owned(view, marker):
        return denied("kratos-foreign-identity"), None
    if view.state != "active":
        return unavailable(f"identity-state:{view.state}"), None
    return complete("read-back"), view.identity_id


def initial_traits_match(
    view: AdmissionIdentity, *, display_name: str, social: bool
) -> bool:
    """The intended initial descriptive values landed exactly — `social`
    compared by identity so a missing or non-boolean value fails closed
    rather than inferring a permissive default."""
    return (
        view.traits.get("display_name") == display_name
        and view.traits.get("social") is social
    )
