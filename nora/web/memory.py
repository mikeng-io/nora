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

**Profile (design/unified-memory/45, the switch).** While `[memory.profile] read`
is on, each entry also carries `profile`: the caller's own Profile account
per unit that lists them — every chain head with its state, the evidence
behind each line, and the account's open disputes (cognition-loop/50 §4: an
account nobody sees cannot be corrected). A line's message and episode
citations resolve to their text only where the viewer is listed in the room
it was said in TODAY (the write checked the rooms of that day); anything
else renders as its bare citation. An account that was never written is
absent, not empty: nothing renders what was never said.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from nora.domain.character_card import format_card, format_line
from nora.domain.culture import RoomMessage, character_key
from nora.domain.models import PersonRef
from nora.domain.ports import MemoryStore
from nora.domain.profile import ProfileDispute, ProfileLine
from nora.persons import Persons

# Generous relative to the tagger card's small cap (nora/engagement/
# context.py uses 3-5) because this is the ONE place a person's whole
# showcased history renders at once, not a per-turn prefill budget.
_MAX_CONCLUSIONS = 20

#: The most open disputes one account shows: the session's own agenda read
#: (`open_disputes`, 45 §4), bounded the same way here.
_MAX_DISPUTES = 20

#: A profile line's citations (nora/domain/profile.py's forms): a Record row
#: id, a `response:<send id>` episode, a `social:<network>:<item id>` item, a
#: `rollup:<kind>:<subject>` aggregate.
_EPISODE = re.compile(r"response:(\d+)")
_SOCIAL = re.compile(r"social:([a-z]+):(\S+)")
_ROLLUP = re.compile(r"rollup:[a-z]+:\S+")


class ProfilePortal(Protocol):
    """The portal's Profile reads, narrowed from `SqlProfileStore` for
    `nora.language.profile_render.ProfileHeads`' reason: a reader wired this
    way can never write a line."""

    def account_heads(self, *, key: str) -> Awaitable[tuple[ProfileLine, ...]]: ...

    def open_disputes(
        self, *, key: str, limit: int,
    ) -> Awaitable[tuple[ProfileDispute, ...]]: ...


class CitedMessages(Protocol):
    """The Record rows a line's message and episode citations name
    (`SqlCharacterEvidence.rows_by_id`; hidden rows are left out there)."""

    def rows_by_id(self, *, ids: Sequence[int]) -> Awaitable[list[RoomMessage]]: ...


async def build_memory_payload(
    *, honcho: MemoryStore, persons: Persons, ai_peer: str, person_id: str | None,
    workspaces_of: Callable[[str], tuple[str, ...]] | None = None,
    profile: ProfilePortal | None = None,
    profile_scopes_of: Callable[[str], tuple[tuple[str, str], ...]] | None = None,
    cited: CitedMessages | None = None,
    rooms_of: Callable[[str], frozenset[tuple[str, str]]] | None = None,
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
    `reachable` (sourced from `representation`'s own Recollection) is.

    With `profile` wired (the switch), each entry also gains the caller's own
    Profile accounts — see `_attach_profile`. `profile_scopes_of` is
    `nora.app.memory_routing.profile_scopes_of_person`, a callable for
    `workspaces_of`'s reason: `nora.web` never sees a `Policy`."""
    if person_id is None:
        return []
    ref = persons.by_person.get(person_id)
    if ref is None:
        return []
    if workspaces_of is None:
        entries = [await _entry(honcho, ref, ai_peer, workspace=None)]
    else:
        entries = [
            await _entry(honcho, ref, ai_peer, workspace=name)
            for name in workspaces_of(person_id)
        ]
    if profile is not None and profile_scopes_of is not None:
        await _attach_profile(
            entries, profile=profile, cited=cited, rooms_of=rooms_of,
            persons=persons, person_id=ref.person_id,
            scopes=profile_scopes_of(ref.person_id), scopes_of=profile_scopes_of,
            unrouted=workspaces_of is None,
        )
    return entries


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


# --- Profile (45, the switch) -------------------------------------------------


async def _attach_profile(
    entries: list[dict[str, Any]],
    *,
    profile: ProfilePortal,
    cited: CitedMessages | None,
    rooms_of: Callable[[str], frozenset[tuple[str, str]]] | None,
    persons: Persons,
    person_id: str,
    scopes: tuple[tuple[str, str], ...],
    scopes_of: Callable[[str], tuple[tuple[str, str], ...]],
    unrouted: bool,
) -> None:
    """Attach the caller's own Profile accounts to their entries: one account
    per unit that lists them, under the entry of the workspace that unit
    belongs to. An account whose workspace has no entry is dropped, never
    re-homed — the same fail-closed shape as the workspace membership above.
    The unrouted caller (no `[units]`) has the one entry, and every account
    lands on it."""
    # The rooms the viewer is listed in TODAY, for the evidence-text rule in
    # `_evidence`. Compared on the bare conversation id: the Record's read
    # (`rows_by_id`) does not carry the platform, and two platforms never
    # share one in practice (DEC-105's one-room-per-platform per unit).
    # ponytail: if they ever do, a cited message of one platform could show
    # its text to a viewer of the other's; the fix is `RoomMessage` carrying
    # the platform.
    viewer_rooms = (
        frozenset(cid for _, cid in rooms_of(person_id))
        if rooms_of is not None else frozenset()
    )
    by_workspace = {entry.get("workspace"): entry for entry in entries}
    for unit, workspace in scopes:
        entry = entries[0] if unrouted and entries else by_workspace.get(workspace)
        if entry is None:
            continue
        def identity_name(pid: str, current_unit: str = unit) -> str:
            return (_name_of(persons, pid) or "someone") if any(
                u == current_unit for u, _ in scopes_of(pid)) else "someone"
        account = await _profile_account(
            profile=profile, cited=cited, persons=persons,
            unit=unit, person_id=person_id, viewer_rooms=viewer_rooms,
            name_of=identity_name,
        )
        if account is not None:
            entry.setdefault("profile", []).append(account)


async def _profile_account(
    *,
    profile: ProfilePortal,
    cited: CitedMessages | None,
    persons: Persons,
    unit: str,
    person_id: str,
    viewer_rooms: frozenset[str],
    name_of: Callable[[str], str],
) -> dict[str, Any] | None:
    """The caller's account in one unit: every chain head with its state, the
    evidence behind each line, and the open disputes. `None` when nothing was
    ever written and no dispute is open — an account that does not exist is
    absent, not empty."""
    key = character_key(unit, person_id)
    heads = await profile.account_heads(key=key)
    disputes = await profile.open_disputes(key=key, limit=_MAX_DISPUTES)
    if not heads and not disputes:
        return None
    rows = await _cited_rows(cited, heads)
    return {
        "unit": unit,
        "card": format_card(heads, now_ms=int(time.time() * 1000), name_of=name_of,
                            location=True),
        "lines": [
            _line_payload(line, persons=persons, rows=rows, viewer_rooms=viewer_rooms,
                          name_of=name_of)
            for line in heads
        ],
        "disputes": [_dispute_payload(d, persons=persons) for d in disputes],
    }


async def _cited_rows(
    cited: CitedMessages | None, heads: tuple[ProfileLine, ...],
) -> Mapping[int, RoomMessage]:
    """The Record rows the heads' message and episode citations name, in one
    read (hidden rows are left out by the store: a withdrawn message shows as
    its bare citation)."""
    if cited is None:
        return {}
    ids = sorted(
        {int(c) for line in heads for c in line.citations if c.isdigit()}
        | {
            int(m.group(1))
            for line in heads for c in line.citations
            if (m := _EPISODE.fullmatch(c)) is not None
        }
    )
    if not ids:
        return {}
    return {r.row.id: r for r in await cited.rows_by_id(ids=ids)}


def _name_of(persons: Persons, person_id: str | None) -> str | None:
    if person_id is None:
        return None
    ref = persons.by_person.get(person_id)
    return ref.name if ref is not None else person_id


def _line_payload(
    line: ProfileLine,
    *,
    persons: Persons,
    rows: Mapping[int, RoomMessage],
    viewer_rooms: frozenset[str],
    name_of: Callable[[str], str],
) -> dict[str, Any]:
    """One chain head: its state verbatim (the portal MARKS a disputed or
    hidden line rather than dropping it — correction is the point of the
    page), and the evidence behind it. A hypothesis carries its prediction
    and verdict, so it reads as the question it is (45 §3)."""
    payload: dict[str, Any] = {
        "part": line.part,
        "kind": line.kind,
        "text": format_line(line, name_of=name_of)
        or "legacy untyped: " + line.text,
        "fact_key": line.fact_key,
        "as_of": line.as_of,
        "valid_from": line.valid_from,
        "valid_until": line.valid_until,
        "mode": line.mode,
        "state": line.state,
        "told_by": _name_of(persons, line.told_by),
        "written_at": line.written_at,
        "evidence": [
            _evidence(c, rows=rows, persons=persons, viewer_rooms=viewer_rooms)
            for c in line.citations
        ],
    }
    if line.fact_key == "attribute":
        # The fallback contains no person references; its text is still the shared formatter.
        payload["fact_value"] = line.fact_value
    if line.part == "guesses":
        payload["prediction"] = line.prediction
        payload["prediction_observable"] = line.prediction_observable
        payload["prediction_window_end"] = line.prediction_window_end
        payload["verdict"] = line.verdict
    return payload


def _evidence(
    citation: str,
    *,
    rows: Mapping[int, RoomMessage],
    persons: Persons,
    viewer_rooms: frozenset[str],
) -> dict[str, Any]:
    """One citation as the viewer reads it. A message or episode resolves to
    its text, when and who — only while the viewer is listed in the room it
    was said in, and only while the row is not hidden; anything else is the
    bare citation. A social item is the viewer's own published post (the
    write path's author-only rule), a Rollup key the aggregate's name: both
    carry their ref and no text (no read of either is wired here).
    ponytail: social and rollup text if the page asks for them."""
    episode = _EPISODE.fullmatch(citation)
    if citation.isdigit() or episode is not None:
        row_id = int(citation) if episode is None else int(episode.group(1))
        found = rows.get(row_id)
        if found is None or found.room not in viewer_rooms:
            return {"ref": citation, "kind": "episode" if episode else "message"}
        return {
            "ref": citation,
            "kind": "episode" if episode else "message",
            "text": found.row.body,
            "said_at": found.row.ts,
            "by": _name_of(persons, found.row.person_id),
        }
    if _SOCIAL.fullmatch(citation):
        return {"ref": citation, "kind": "social"}
    if _ROLLUP.fullmatch(citation):
        return {"ref": citation, "kind": "rollup"}
    # The write path admits no other form; an unreadable one is still shown,
    # never guessed at.
    return {"ref": citation, "kind": "unknown"}


def _dispute_payload(dispute: ProfileDispute, *, persons: Persons) -> dict[str, Any]:
    """One open dispute: the claim as the disputant put it, who, and when.
    The claim was screened at write (35 §2); who is a display name, never an
    id."""
    return {
        "kind": dispute.kind,
        "claim": dispute.claim,
        "disputed_by": _name_of(persons, dispute.disputed_by),
        "written_at": dispute.written_at,
    }
