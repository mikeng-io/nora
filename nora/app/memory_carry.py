"""`nora memory carry-conclusions --workspace <workspace>` — bring the facts
Nora was TOLD before unit scopes existed into every unit scope of a
workspace (DEC-214). One-time; dry run unless `--apply`.

WHAT IS STRANDED WITHOUT IT. Since DEC-214 every memory read for a room or
unit passes that unit's named scope, which reads the `(scope.<unit>, person)`
collection. The facts written before that — `remember` in a turn and the
note curator's person-claim discharge — went through `conclude` with Nora as
observer and NO session, so they are in `(nora, person)` only. A scope's own
backfill copies what was derived from its member SESSIONS; a conclusion with
no session belongs to no session and is copied into no scope. So every one
of them fell out of every unit's recall at once.

WHAT IT COPIES. In the workspace's Honcho workspace: each conclusion with
observer `ai_peer`, no session, level `explicit` — exactly the shape those
two writers produce (Honcho stamps every manual conclusion `explicit`, and
everything its deriver writes from a message carries that message's
session). Each is written into EVERY unit scope of the workspace, observer
`scope.<unit>`, observed and content unchanged. Deductive/inductive rows with
no session are dreams, reasoned across sessions; a scope's own dreams rebuild
its versions, so they are not copied.

THE EXPOSURE IS THE ONE THAT EXISTED. Before DEC-214 these rows were read by
every unit of the one workspace — recall was workspace-wide. Copying each
into every unit scope of ITS workspace restores exactly that, and nothing
wider: a workspace's rows are never written into another workspace's scopes
(each Nora workspace is its own Honcho workspace, and the command takes one).

IDEMPOTENT. A row whose `(observed, content)` the scope already holds is
skipped, so a second run, or a run after a scope's own reads were written by
`conclude`, adds nothing twice. It deletes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from nora.infra.honcho import WorkspaceRouter
from nora.infra.honcho.client import SCOPE_PEER_PREFIX
from nora.policy import Policy


class CarryRefusedError(Exception):
    """The run cannot be made as asked. Nothing has been written."""


class _Memory(Protocol):
    async def list_conclusions(
        self, *, workspace: str, observer: str,
    ) -> list[dict[str, Any]] | None: ...

    async def carry_to_scope(self, *, unit: str, rows: list[tuple[str, str]]) -> bool: ...


@dataclass
class UnitCarry:
    unit: str
    scope: str
    already: int = 0
    carried: int = 0
    failed: bool = False


@dataclass
class CarryReport:
    workspace: str
    honcho_workspace: str
    applied: bool
    sources: int = 0
    units: list[UnitCarry] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(u.failed for u in self.units)


async def carry_conclusions(
    *,
    workspace: str,
    policy: Policy,
    router: WorkspaceRouter,
    memory: _Memory,
    ai_peer: str,
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
    sources = list(dict.fromkeys(
        (str(row["observed_id"]), str(row["content"]))
        for row in found
        if row.get("session_id") is None
        and row.get("level", "explicit") == "explicit"
        and row.get("observed_id") and row.get("content")
    ))
    report = CarryReport(
        workspace=workspace, honcho_workspace=whole.workspace, applied=apply,
        sources=len(sources),
    )
    for unit in units:
        route = router.for_unit(unit)
        if route is None or route.scope is None:
            raise CarryRefusedError(f"unit {unit!r} has no scope to carry into")
        held = await memory.list_conclusions(
            workspace=workspace, observer=f"{SCOPE_PEER_PREFIX}{route.scope}",
        )
        if held is None:
            raise CarryRefusedError(
                f"could not read what unit {unit!r}'s scope already holds; nothing "
                "more was written"
            )
        have = {(str(r.get("observed_id")), str(r.get("content"))) for r in held}
        todo = [row for row in sources if row not in have]
        counts = UnitCarry(unit=unit, scope=route.scope, already=len(sources) - len(todo))
        report.units.append(counts)
        if not todo:
            continue
        if not apply:
            counts.carried = len(todo)
            continue
        if not await memory.carry_to_scope(unit=unit, rows=todo):
            counts.failed = True
            break
        counts.carried = len(todo)
    return report


def render(report: CarryReport) -> list[str]:
    verb = "carried" if report.applied else "would carry"
    lines = [
        f"workspace {report.workspace!r} -> Honcho workspace {report.honcho_workspace!r}"
        f" ({'APPLIED' if report.applied else 'dry run: nothing written'}): "
        f"{report.sources} session-less conclusion(s) of Nora's",
    ]
    for unit in report.units:
        lines.append(
            f"  unit {unit.unit!r} (scope {unit.scope!r}): {verb} {unit.carried}, "
            f"already held {unit.already}"
            + (" — FAILED; nothing after this unit was written" if unit.failed else "")
        )
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
