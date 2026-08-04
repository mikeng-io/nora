"""Stage 3 — resolve the sender to a `PersonRef`.

Identity mapping is manual (`identity.toml`, see `nora.persons`), so an
unmapped sender is an expected outcome, not an error: it resolves to `None`
and ingest continues (D6). The audit write in stage 2 already happened and is
never conditional on identity.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage, PersonRef
from nora.persons import Persons


async def run(*, message: NormalizedMessage, persons: Persons) -> PersonRef | None:
    """Return the mapped person, or `None` if the sender is unmapped."""
    return persons.resolve(message.platform, message.sender_id)
