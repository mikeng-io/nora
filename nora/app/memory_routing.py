"""Where each unit's memory lives: workspace > unit > room, resolved once
(DEC-214).

Three files each hold one part, and this is the one place they meet:

- `policy.toml` holds the STRUCTURE — which Nora workspace each unit belongs
  to (`[units.<unit>] workspace = "..."`), and which unit each room belongs
  to (`[[conversations]] unit = "..."`).
- `identity.toml` holds the external BINDING — which Honcho workspace a Nora
  workspace's person models live in (`[workspaces.<name>] honcho_workspace
  = "..."`). A Nora workspace with no binding uses its own name.
- `config.toml`'s `memory.workspace_id` is only the back-compat default: the
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

from collections.abc import Mapping

from nora.infra.honcho import WorkspaceRouter
from nora.policy import DEFAULT_WORKSPACE, Policy


class MemoryRoutingError(Exception):
    """The three files disagree about where some unit's memory lives. A
    startup error, never a guess: a guessed Honcho workspace is a person's
    speech written into another group's person models, which no later edit
    can take back (Honcho keeps conclusions, not text)."""


def honcho_workspaces(
    *, policy: Policy, bindings: Mapping[str, str], default_workspace: str,
) -> dict[str, str]:
    """Nora workspace -> Honcho workspace id, for every workspace a unit of
    this policy belongs to.

    Without `[units]` there is one workspace, the implicit one, and it is
    bound to `default_workspace` (`memory.workspace_id`) so a deployment that
    has not adopted the hierarchy reads and writes exactly where it did. A
    binding in `identity.toml` then has nothing to bind and is refused, since
    it would read as though it did something.

    With `[units]` each workspace is bound by `identity.toml`, or to its own
    name. Refused: a binding for a workspace no unit uses (a typo, or a
    stale line), and two Nora workspaces on ONE Honcho workspace — that
    would merge their people into one set of models, which is the exact
    thing keeping them separate workspaces exists to prevent.
    """
    if not policy.units_declared:
        if bindings:
            raise MemoryRoutingError(
                "identity.toml binds [workspaces."
                f"{sorted(bindings)[0]}], but policy.toml declares no [units], so "
                "every unit is in the one implicit workspace, bound to "
                "config.toml's memory.workspace_id. Add [units] to policy.toml, "
                "or remove the binding."
            )
        return {DEFAULT_WORKSPACE: default_workspace}
    named = set(policy.unit_workspaces().values())
    stray = sorted(set(bindings) - named)
    if stray:
        raise MemoryRoutingError(
            f"identity.toml binds [workspaces.{stray[0]}], but no unit in "
            f"policy.toml belongs to a workspace named {stray[0]!r}. Fix the "
            "name, or remove the binding."
        )
    resolved = {name: bindings.get(name, name) for name in sorted(named)}
    seen: dict[str, str] = {}
    for name, honcho in resolved.items():
        other = seen.get(honcho)
        if other is not None:
            raise MemoryRoutingError(
                f"workspaces {other!r} and {name!r} are both bound to the Honcho "
                f"workspace {honcho!r}. Honcho peers are per Honcho workspace, so "
                "that would merge both workspaces' people into one set of person "
                "models. Bind each to its own."
            )
        seen[honcho] = name
    return resolved


def build_workspace_router(
    *, policy: Policy, bindings: Mapping[str, str], default_workspace: str,
) -> WorkspaceRouter:
    """The router `HonchoClient` sends every call through.

    conversation id -> unit comes from every id a room answers to (`id` and
    `also_ids`). The client routes `feed` by conversation id alone, so an id
    that two rooms in DIFFERENT units both answer to (possible only across
    platforms) cannot be routed and is refused here rather than sent to
    whichever was listed last.
    """
    by_workspace = honcho_workspaces(
        policy=policy, bindings=bindings, default_workspace=default_workspace,
    )
    unit_workspaces = {
        unit: by_workspace[workspace]
        for unit, workspace in policy.unit_workspaces().items()
    }
    conversation_units: dict[str, str] = {}
    for convo in policy.conversations:
        for cid in sorted(convo.ids):
            other = conversation_units.get(cid)
            if other is not None and other != convo.unit:
                raise MemoryRoutingError(
                    f"conversation id {cid!r} belongs to rooms in two units "
                    f"({other!r} and {convo.unit!r}); memory is routed by the "
                    "conversation id alone, so it cannot say which unit a "
                    "message there belongs to."
                )
            conversation_units[cid] = convo.unit
    return WorkspaceRouter(
        default_workspace,
        unit_workspaces=unit_workspaces,
        conversation_units=conversation_units,
        workspace_routes=by_workspace,
        strict=policy.units_declared,
    )


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
    return tuple(sorted({
        policy.workspace_of(convo.unit) or ""
        for convo in policy.conversations
        if convo.persons is not None and person_id in convo.persons
    } - {""}))
