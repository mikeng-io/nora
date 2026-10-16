"""GET /app/v1/memory — the memory showcase, user-scoped.

Since DEC-214 a person can have one entry per Nora workspace they belong to,
each labelled `workspace` (see `build_memory_payload`).

A caller sees ONLY their own identity_card + episodes, resolved from the
session's own person_id (the Next.js proxy route already resolves this via
checkSession() for the allowlist gate; this endpoint requires the same person_id explicitly rather
than re-deriving it, so the web layer stays the one place session identity is
read). A missing or unrecognized person_id returns an empty list — fail-closed
(R-5's shape), never every other person's data as a fallback.

UI copy built on top of this must never say "Honcho"; this module
doesn't render copy, so that rule lives in the frontend, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nora.domain.models import PersonRef
from nora.domain.ports import MemoryStore
from nora.persons import Persons

# Generous relative to the tagger card's small cap (nora/engagement/
# context.py uses 3-5) because this is the ONE place a person's whole
# showcased history renders at once, not a per-turn prefill budget.
_MAX_CONCLUSIONS = 20


async def build_memory_payload(
    *, honcho: MemoryStore, persons: Persons, ai_peer: str, person_id: str | None,
    workspaces_of: Callable[[str], tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """The caller's own entries only — one per Nora WORKSPACE they belong to
    (DEC-214), each labelled `workspace`, in workspace-name order.

    Carries BOTH of Honcho's two distinct reads of the person
    (nora/domain/models.py's `TagContext` docstring is the authority on why
    these never collapse into one list):

    - `identity` (`MemoryStore.peer_card`) — IDENTITY claims distilled from
      the person's own output ("Name: Miles Novak", "Aliases: …"). Self-scoped:
      called with THEIR OWN honcho_peer_id, no observer/observed pair.
    - `episodes` (`MemoryStore.representation`, observer=ai_peer,
      observed=that person) — dated EPISODES ("asked what to eat for
      dinner"), Nora's own model of them. Same verb and shape as
      nora/engagement/context.py's `_recall_tagger_card`, just with no
      message to scope `search_query` to (empty string: the general, not
      turn-relevant, view).

    **Per workspace, unscoped within it.** A person is one Honcho peer per
    Honcho workspace, so someone in units of two Nora workspaces has two
    models of them, and they are shown apart, never merged: each read names
    the WORKSPACE (`workspace=`), which the memory client resolves to that
    workspace's Honcho workspace and reads across all its units. Membership
    is `workspaces_of` — the workspaces holding a room whose `persons` list
    names them (`nora.app.memory_routing.workspaces_of_person`). Only the
    caller's own model is read here, so no other person's memory is routed
    through anything. `workspaces_of=None` is the unrouted caller: one
    entry, which a deployment with no `[units]` answers from its one workspace
    (and one with `[units]` refuses).

    `MemoryStore.peer_card` returns `[]` both when Honcho is down and when
    it genuinely knows nothing (`ports.py`'s own documented ambiguity) — so
    an empty `identity` list is never surfaced as an error here, only
    `reachable` (sourced from `representation`'s own Recollection) is."""
    if person_id is None:
        return []
    ref = persons.by_person.get(person_id)
    if ref is None:
        return []
    if workspaces_of is None:
        return [await _entry(honcho, ref, ai_peer, workspace=None)]
    return [
        await _entry(honcho, ref, ai_peer, workspace=name)
        for name in workspaces_of(person_id)
    ]


async def _entry(
    honcho: MemoryStore, ref: PersonRef, ai_peer: str, *, workspace: str | None,
) -> dict[str, Any]:
    recall = await honcho.representation(
        peer_id=ai_peer, target=ref.person_id, search_query="",
        max_conclusions=_MAX_CONCLUSIONS, workspace=workspace,
    )
    identity = await honcho.peer_card(peer_id=ref.honcho_peer_id, workspace=workspace)
    entry: dict[str, Any] = {
        "person_id": ref.person_id,
        "display_name": ref.name,
        "reachable": recall.reachable,
        "identity": list(identity),
        "episodes": list(recall.conclusions),
    }
    if workspace is not None:
        entry["workspace"] = workspace
    return entry
