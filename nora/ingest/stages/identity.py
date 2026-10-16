"""Resolve the sender to a `PersonRef`.

Identity mapping is manual (`identity.toml`, see `nora.persons`), so an
unmapped sender is an expected outcome, not an error: it resolves to `None`
and ingest continues (D6).

It is nonetheless COUNTED. Its symptom set is otherwise empty — the message is
still audited, the gate still runs, and `Persons.display_name` still renders
the sender's platform display name into the prefill, so Nora replies normally
and addresses them by name while nothing at all reaches Honcho. That is the
wrong-silence shape this project keeps shipping, and here it is worse than
usual: the display-name fallback fabricates the appearance that identity
worked.

This runs BEFORE the audit write so the resolved `person_id` lands in that
single INSERT. It is safe to put ahead of it: `Persons.resolve` is two dict
lookups on a frozen mapping built at startup — no I/O, nothing to block on.
Auditing first and UPDATEing the row afterwards (what A9 does for outbound
replies) would add a second unconditional write per ambient message, and
`nora.infra.sql.audit` documents that WAL allows exactly one writer and that the
ingest path's failure behaviour is to DROP the message. Pruning stays off the
write path for the same reason.

The counter is the live tripwire; the `person_id` column is the durable
record. Both are needed: metrics here live only in the process's own registry
and reset on restart, while mapping is startup-only (Q4) — so "I fixed
identity.toml but never restarted" is a real state the file on disk cannot
reveal.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage, PersonRef
from nora.persons import Persons, sanitize_unmapped

log = logging.getLogger("nora.ingest.identity")


async def run(
    *, message: NormalizedMessage, persons: Persons, metrics: Any
) -> PersonRef | None:
    """Return the mapped person, or `None` if the sender is unmapped."""
    person = persons.resolve(message.platform, message.sender_id)
    if person is None:
        metrics.inc("nora_unmapped_senders_total", platform=message.platform)
        # Redacted: the raw id is already in the audit row's `raw_json`, and
        # `nora persons pending` is where the operator gets a pasteable copy.
        # Logs are a separate exposure surface and do not need it.
        log.warning(
            "unmapped sender (platform=%s sender=%s) — no Honcho memory is "
            "being written for them; check identity.toml, and restart if you "
            "have edited it (mapping is startup-only)",
            message.platform,
            sanitize_unmapped(message.sender_id),
        )
    return person
