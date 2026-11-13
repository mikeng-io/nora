"""The authenticated portal's identity roster and person profiles."""

from __future__ import annotations

from typing import Any

from nora.persons import Persons


def build_persons_roster_payload(*, persons: Persons) -> list[dict[str, Any]]:
    """Everyone identity.toml knows, sorted by display name."""
    return sorted(
        (
            {"personId": ref.person_id, "displayName": ref.name}
            for ref in persons.by_person.values()
        ),
        key=lambda entry: entry["displayName"],
    )


async def build_person_profile_payload(
    *, persons: Persons, person_id: str,
) -> dict[str, Any] | None:
    """Return a known person's profile, or None for an unknown person."""
    ref = persons.by_person.get(person_id)
    if ref is None:
        return None

    return {
        "personId": ref.person_id,
        "displayName": ref.name,
    }
