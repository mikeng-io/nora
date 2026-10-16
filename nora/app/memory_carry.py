"""`nora memory carry-conclusions --workspace <workspace>` — copy the facts
Nora was TOLD without a unit scope into every unit scope of a workspace
(DEC-214). Dry run unless `--apply`.

WHY. Every memory read for a room or unit passes that unit's named scope,
which reads the `(scope.<unit>, person)` collection. A conclusion written
through `conclude` with Nora as observer and NO session (a `remember` in a turn,
a person-claim discharge) lives in `(nora, person)` only. A scope's own
backfill copies what was derived from its member SESSIONS; a session-less
conclusion belongs to no session and is copied into no scope, so it is
invisible to every unit's recall.

WHAT IT COPIES. In the workspace's Honcho workspace: each conclusion with
observer `ai_peer`, no session, level `explicit` (Honcho stamps every manual
conclusion `explicit`, and everything its deriver writes from a message
carries that message's session), CREATED BEFORE `--before`, the cutover. Each
is written into EVERY unit scope of the workspace, observer `scope.<unit>`,
observed and content unchanged. Deductive/inductive rows with no session are
dreams, reasoned across sessions; a scope's own dreams rebuild its versions,
so they are not copied.

`--before` IS REQUIRED because the shape is not unique to the past:
`conclude` still writes a session-less `(nora, person)` row beside the unit's
own `(scope.<unit>, person)` one, so a fact told in unit A after the cutover
looks exactly like an earlier one; copied, it would reach every other unit of
the workspace, which is the leak scopes exist to stop. Anything already held
by ANY scope of the Honcho workspace is skipped too.

ONLY WHERE THE PERSON BELONGS, AND ONLY UNAMBIGUOUSLY. Such a row has no
provenance: nothing says in which room, or which workspace's room, it was
told. So a row is copied only when the person it is about belongs to EXACTLY
ONE Nora workspace (a listed room's `persons` names them) and it is the one
given. A row about someone in several workspaces, in another one, or in none
(Nora herself included), is never copied automatically: the dry run lists
those rows by conclusion id, never content, for the operator to decide. This
command writes inside one Honcho workspace and never across two.

EXPOSURE. Copying each row into every unit scope of ITS workspace equals the
workspace-wide recall that existed before scopes, and nothing wider: a
workspace's rows are never written into another workspace's scopes (each Nora
workspace is its own Honcho workspace), and a person's row only into the
workspace they belong to.

IDEMPOTENT. A row whose `(observed, content)` any scope already holds is
skipped, and each row goes into all the scopes in one request, so a second
run, or one after a failure, adds nothing twice and leaves no scope short.
It deletes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from nora.app.memory_routing import workspaces_of_person
from nora.infra.honcho import WorkspaceRouter
from nora.infra.honcho.client import SCOPE_PEER_PREFIX
from nora.policy import Policy


class CarryRefusedError(Exception):
    """The run cannot be made as asked. Nothing has been written."""


class _Memory(Protocol):
    async def list_conclusions(
        self, *, workspace: str, observer: str,
    ) -> list[dict[str, Any]] | None: ...

    async def list_scopes(self, *, workspace: str) -> list[str] | None: ...

    async def carry_to_scopes(
        self, *, units: list[str], rows: list[tuple[str, str]],
    ) -> bool: ...


@dataclass
class CarryReport:
    workspace: str
    honcho_workspace: str
    applied: bool
    # The unit scopes every carried row goes into.
    scopes: list[str] = field(default_factory=list)
    # Rows to carry (dry run) or carried (applied).
    carried: int = 0
    # Rows skipped because some scope of the workspace already holds them.
    held: int = 0
    # Conclusion ids held back for the operator: the person they are about
    # does not belong to this workspace alone (several, another, or none).
    # Ids only, never content.
    ambiguous: list[str] = field(default_factory=list)
    failed: bool = False


async def carry_conclusions(
    *,
    workspace: str,
    policy: Policy,
    router: WorkspaceRouter,
    memory: _Memory,
    ai_peer: str,
    before: datetime,
    apply: bool = False,
) -> CarryReport:
    """Copy `workspace`'s session-less explicit Nora conclusions into each of
    its units' scopes. See the module docstring."""
    whole = router.for_workspace(workspace)
    if whole is None:
        raise CarryRefusedError(f"no workspace named {workspace!r} is bound")
    units = sorted(u for u, w in policy.unit_workspaces().items() if w == workspace)
    if not units:
        raise CarryRefusedError(f"workspace {workspace!r} holds no unit")
    found = await memory.list_conclusions(workspace=workspace, observer=ai_peer)
    if found is None:
        raise CarryRefusedError(
            f"could not read Nora's conclusions in {whole.workspace!r} — nothing was "
            "written; is Honcho up, and is that workspace Nora's?"
        )
    # What any scope already holds, across the whole Honcho workspace; read
    # before anything is written, so a failure here writes nothing.
    scopes = await memory.list_scopes(workspace=workspace)
    if scopes is None:
        raise CarryRefusedError("could not list the workspace's scopes; nothing was written")
    anywhere: set[tuple[str, str]] = set()
    for scope in scopes:
        in_scope = await memory.list_conclusions(
            workspace=workspace, observer=f"{SCOPE_PEER_PREFIX}{scope}",
        )
        if in_scope is None:
            raise CarryRefusedError(
                f"could not read what scope {scope!r} holds; nothing was written"
            )
        anywhere |= {(str(r.get("observed_id")), str(r.get("content"))) for r in in_scope}
    sources: list[tuple[str, str]] = []
    ambiguous: list[str] = []
    held = 0
    for row in found:
        if not (
            row.get("session_id") is None
            and row.get("level", "explicit") == "explicit"
            and row.get("observed_id") and row.get("content")
            and _before(row.get("created_at"), before)
        ):
            continue
        key = (str(row["observed_id"]), str(row["content"]))
        if key in anywhere:
            held += 1
            continue
        if key in sources:
            continue
        if workspaces_of_person(policy, key[0]) != (workspace,):
            ambiguous.append(str(row.get("id")))
            continue
        sources.append(key)
    scope_names: list[str] = []
    for unit in units:
        route = router.for_unit(unit)
        if route is None or route.scope is None:
            raise CarryRefusedError(
                f"unit {unit!r} has no scope to carry into (policy.toml declares no "
                "[units], so nothing reads through a scope)"
            )
        scope_names.append(route.scope)
    report = CarryReport(
        workspace=workspace, honcho_workspace=whole.workspace, applied=apply,
        scopes=scope_names, carried=len(sources), held=held, ambiguous=ambiguous,
    )
    # One row goes into every scope together (`carry_to_scopes` never splits a
    # row across requests), so a run that fails part-way leaves each row in all
    # of the workspace's scopes or in none; the «already in any scope» skip
    # above then lets a re-run finish the job.
    if apply and sources and not await memory.carry_to_scopes(units=units, rows=sources):
        report.failed = True
    return report


def _before(created_at: object, cutover: datetime) -> bool:
    """A row's `created_at` (ISO 8601, as Honcho serialises it) is strictly
    before the cutover. Unparseable or absent is NOT before: a row whose age
    cannot be told is not copied."""
    if not isinstance(created_at, str):
        return False
    try:
        stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None or cutover.tzinfo is None:
        return False
    return stamp < cutover


def render(report: CarryReport) -> list[str]:
    verb = "carried" if report.applied and not report.failed else "would carry"
    lines = [
        f"workspace {report.workspace!r} -> Honcho workspace {report.honcho_workspace!r}"
        f" ({'APPLIED' if report.applied else 'dry run: nothing written'})",
        f"  {verb} {report.carried} conclusion(s) into every scope of "
        f"{report.scopes}; already held by a scope: {report.held}"
        + ("; FAILED — re-run to finish, what landed is skipped" if report.failed else ""),
    ]
    if report.ambiguous:
        lines.append(
            f"  NOT copied, for you to decide ({len(report.ambiguous)}): conclusions "
            "about someone who does not belong to this workspace alone — ids "
            + ", ".join(report.ambiguous)
        )
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
