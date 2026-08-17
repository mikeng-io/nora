"""GET /app/v1/memory — the memory showcase (design spec §5.2).

Every identity.toml person's current Honcho representation, shown to every
OTHER whitelisted person with no filtering beyond what Honcho itself already
produced — a deliberate choice (spec §2), not a gap. UI copy built on top of
this must never say "Honcho" (spec §5.2); this module doesn't render copy, so
that rule lives in the frontend, not here.
"""

from __future__ import annotations

from nora.domain.ports import MemoryStore
from nora.persons import Persons

# Generous relative to the tagger/self cards' small caps (nora/engagement/
# context.py uses 3-5) because this is the ONE place a person's whole
# showcased history renders at once, not a per-turn prefill budget.
_MAX_CONCLUSIONS = 20


async def build_memory_payload(
    *, honcho: MemoryStore, persons: Persons, ai_peer: str,
) -> list[dict]:
    """One entry per identity.toml person, each Nora's own model of them
    (observer=ai_peer, observed=that person) — the same `representation`
    verb and (observer, observed) shape as nora/engagement/context.py's
    `_recall_self`/`_recall_tagger_card`, just with no message to scope
    `search_query` to (empty string: the general, not turn-relevant, view)."""
    payload: list[dict] = []
    for person_id, ref in persons.by_person.items():
        recall = await honcho.representation(
            peer_id=ai_peer, target=person_id, search_query="",
            max_conclusions=_MAX_CONCLUSIONS,
        )
        payload.append({
            "person_id": person_id,
            "display_name": ref.name,
            "reachable": recall.reachable,
            "conclusions": list(recall.conclusions),
        })
    return payload
