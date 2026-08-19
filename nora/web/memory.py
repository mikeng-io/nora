"""GET /app/v1/memory — the memory showcase, user-scoped (operator, 2026-08-19,
reversing 2026-08-17-nora-web-design.md §2's original "no per-person opt-in/
opt-out" decision).

Every whitelisted person's full Honcho representation used to be visible to
every OTHER whitelisted person — a deliberate v1 choice for a small trusted
friend group. It is reversed here: a caller now sees ONLY their own
identity_card + episodes, resolved from the session's own person_id (the
Next.js proxy route already resolves this via checkSession() for the
allowlist gate; this endpoint requires the same person_id explicitly rather
than re-deriving it, so the web layer stays the one place session identity is
read). A missing or unrecognized person_id returns an empty list — fail-closed
(R-5's shape), never every other person's data as a fallback.

UI copy built on top of this must never say "Honcho" (spec §5.2); this module
doesn't render copy, so that rule lives in the frontend, not here.
"""

from __future__ import annotations

from nora.domain.ports import MemoryStore
from nora.persons import Persons

# Generous relative to the tagger/self cards' small caps (nora/engagement/
# context.py uses 3-5) because this is the ONE place a person's whole
# showcased history renders at once, not a per-turn prefill budget.
_MAX_CONCLUSIONS = 20


async def build_memory_payload(
    *, honcho: MemoryStore, persons: Persons, ai_peer: str, person_id: str | None,
) -> list[dict]:
    """The caller's own entry only — a list of at most one, so the wire shape
    stays what the frontend already expects (see the module docstring for why
    this used to be everyone's and no longer is).

    Carries BOTH of Honcho's two distinct reads of the person
    (nora/domain/models.py's `TagContext` docstring is the authority on why
    these never collapse into one list — read live 2026-08-15, they
    overlapped in zero items):

    - `identity` (`MemoryStore.peer_card`) — IDENTITY claims distilled from
      the person's own output ("Name: Miles Novak", "Aliases: …"). Self-scoped:
      called with THEIR OWN honcho_peer_id, no observer/observed pair.
    - `episodes` (`MemoryStore.representation`, observer=ai_peer,
      observed=that person) — dated EPISODES ("asked what to eat for
      dinner"), Nora's own model of them. Same verb and shape as
      nora/engagement/context.py's `_recall_tagger_card`, just with no
      message to scope `search_query` to (empty string: the general, not
      turn-relevant, view).

    `MemoryStore.peer_card` returns `[]` both when Honcho is down and when
    it genuinely knows nothing (`ports.py`'s own documented ambiguity) — so
    an empty `identity` list is never surfaced as an error here, only
    `reachable` (sourced from `representation`'s own Recollection) is."""
    if person_id is None:
        return []
    ref = persons.by_person.get(person_id)
    if ref is None:
        return []

    recall = await honcho.representation(
        peer_id=ai_peer, target=person_id, search_query="",
        max_conclusions=_MAX_CONCLUSIONS,
    )
    identity = await honcho.peer_card(peer_id=ref.honcho_peer_id)
    return [{
        "person_id": person_id,
        "display_name": ref.name,
        "reachable": recall.reachable,
        "identity": list(identity),
        "episodes": list(recall.conclusions),
    }]
