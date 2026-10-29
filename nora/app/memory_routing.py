"""Where each unit's memory lives: workspace > unit > room, resolved once
(DEC-214).

Three files each hold one part, and this is the one place they meet:

- `policy.toml` holds the STRUCTURE — which Nora workspace each unit belongs
  to (`[units.<unit>] workspace = "..."`), and which unit each room belongs
  to (`[[conversations]] unit = "..."`).
- `identity.toml` holds the external BINDING — which Honcho workspace a Nora
  workspace's person models live in (`[workspaces.<name>] honcho_workspace
  = "..."`). A Nora workspace with no binding uses its own name.
- `config.toml`'s `honcho.workspace_id` is only the back-compat default: the
  Honcho workspace of the IMPLICIT workspace a policy with no `[units]`
  table puts every unit in.

A Nora workspace and a Honcho workspace are different things with different
names (GLOSSARY.md): the first is the operator's grouping of units, the
second is the Honcho tenant whose peers are that grouping's people. This
module is what keeps them from being confused — `nora.infra.honcho` only ever
sees Honcho ids, and the policy never names one.

Here, in the composition layer, because `nora.infra` may not import the
policy (the import-linter contract «the room policy is composed in»).
"""

from __future__ import annotations

from nora.memory_routing import MemoryRoutingError, build_workspace_router, honcho_workspaces
from nora.policy import DEFAULT_WORKSPACE, Policy

__all__ = [
    "MemoryRoutingError",
    "build_workspace_router",
    "honcho_workspaces",
    "profile_scopes_of_person",
    "workspaces_of_person",
]


def workspaces_of_person(policy: Policy, person_id: str) -> tuple[str, ...]:
    """The Nora workspaces `person_id` BELONGS to: those holding a unit with a
    room whose `persons` list names them. Sorted, so a page renders them in
    one order.

    WITHOUT `[units]` there is one workspace and everyone is in it: the
    answer is `(DEFAULT_WORKSPACE,)` for any person, which reads the one
    Honcho workspace — including for a person who is only in `persons = "*"`
    rooms.

    WITH `[units]`, a `persons = "*"` room establishes nobody's membership —
    it admits whoever is in the room, and the policy cannot say who that is —
    so it is not counted, and a person who is ONLY in such rooms belongs to no
    workspace and is shown nothing, rather than a guessed one."""
    if not policy.units_declared:
        return (DEFAULT_WORKSPACE,)
    # ponytail: membership is a FILE fact — a guild entry's `persons` already
    # lists them for every channel it expands into — so `conversations` is the
    # source; `rooms()` would drop them under `_NoChannels` loaders.
    return tuple(sorted({
        policy.workspace_of(convo.unit) or ""
        for convo in policy.conversations
        if convo.persons is not None and person_id in convo.persons
    } - {""}))


def profile_scopes_of_person(policy: Policy, person_id: str) -> tuple[tuple[str, str], ...]:
    """The (unit, workspace) pairs whose rooms list `person_id` by name:
    where their Profile accounts live (`character_key(unit, person_id)`,
    design/unified-memory/45 §1), and which portal workspace entry each
    account belongs under.

    `workspaces_of_person`'s membership rule one level down — a `persons =
    "*"` room lists nobody, and a unit no workspace binds is dropped rather
    than guessed — with one difference: without `[units]` it does NOT answer
    for everybody. A Profile account exists only for a person a room lists
    (`character_person_keys`), so an unlisted person has no scope here, where
    the Honcho read above still has the one implicit workspace. Sorted by
    unit, so a page renders them in one order."""
    # ponytail: same file-fact rule as `workspaces_of_person` — the guild
    # entry carries the listing, so `rooms()` would only hide it.
    return tuple(
        (unit, workspace)
        for unit in sorted({
            convo.unit for convo in policy.conversations
            if convo.persons is not None and person_id in convo.persons and convo.unit
        })
        if (workspace := policy.workspace_of(unit)) is not None
    )
