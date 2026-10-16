"""A Honcho workspace per eval DRAW: created empty, deleted when the draw ends.

The sandbox DATABASE is per case; Honcho would not be if subjects shared one
`<deployment>-eval` workspace. Ingest feeds every arriving line and her reply
into Honcho, the deriver turns them into conclusions, and the tag path reads
those conclusions back as the tagger card. With a shared workspace, draw 3 of a
case would read what draws 1 and 2 had said, ranked by similarity to the very
question being asked again, and reply «你問咗第三次喇» («third time you've
asked»); the judge would grade that as a fabrication, because the window it
grades against holds no earlier ask. Draws would be correlated, concurrent arms
would read each other's notes, and a case's reading would depend on which runs,
from any session, had happened before it.

A workspace of the draw's own closes this by construction, the same way the
sandbox database closes the room: nothing a draw writes can reach another draw,
because there is no other draw in it.

A fresh workspace has an EMPTY peer card, which is the baseline a case sees.
What a case SEEDS (`know()`'s conclusions)
still lands, because it writes through the subject's own client.

THE GUARD. Delete is permanent, and production's workspace is one prefix away
from every name here. So nothing in this module will create, delete or sweep a
workspace whose id does not start with `<deployment>-eval-`: not the
deployment's own (`nora`), not the shared sibling (`nora-eval`), and not any hashed
per-case workspace (`nora-48b0...`).
`obtain.build_config` holds the matching guard on the subject side.

THE HONCHO API, as its own OpenAPI states it (3.1.2):

- `POST /v3/workspaces` is get-or-create, `{id, metadata}`; the id matches
  `^[a-zA-Z0-9_-]+$`, at most 512 characters.
- `DELETE /v3/workspaces/{id}` answers 202 and deletes in the background, and
  answers **409 while the workspace holds an active session**. So a delete is
  sessions first (`POST .../sessions/list`, `DELETE .../sessions/{id}`, which
  marks the session inactive at once), then the workspace.
- `POST /v3/workspaces/list` pages at most 100 per page.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

log = logging.getLogger("nora.subject.memory_workspace")

#: The suffix `_baseline()` puts on the deployment's workspace. The shared
#: sibling is `<deployment>-eval`; a draw's is `<deployment>-eval-<...>`.
EVAL_SUFFIX = "-eval"

#: Metadata key stamped on every draw workspace at creation. The sweep deletes
#: a workspace only when it carries this key AND the prefix, so a hand-made
#: `nora-eval-something` is never swept.
DRAW_MARKER = "nora_eval_draw"

#: How old a draw workspace must be before the startup sweep treats it as left
#: behind by a killed run. A draw lives for one case's drive, which is minutes;
#: six hours is far outside any draw and inside a day, so another session's
#: LIVE draw is never swept.
STALE_AFTER = timedelta(hours=6)

#: Honcho's background work turned OFF for a draw workspace, at create time
#: (`WorkspaceCreate.configuration`, Honcho 3.1.2's OpenAPI).
#:
#: A draw can never read what the deriver would derive from it: derivation
#: lags minutes to tens of minutes behind a feed, and a draw lives for one
#: turn. So every derivation task a draw enqueues is pure load on the one
#: Honcho that also derives production's memory, and it queues the draw's
#: own delete behind it (a delete is accepted at once but carried out only after
#: the queued backlog).
#:
#: `reasoning.enabled = false` stops the representation tasks, and with them
#: peer cards and dreams (the schema says both are ignored when reasoning is
#: off). `summary.enabled = false` stops the session summaries, which are a
#: separate queue task. What a case SEEDS is unaffected: `conclude` writes a
#: conclusion directly, and nothing derives it.
DRAW_CONFIGURATION: dict[str, Any] = {
    "reasoning": {"enabled": False},
    "summary": {"enabled": False},
}

#: Honcho's own id rule (`WorkspaceCreate.id`), so a name is refused here
#: rather than by a 422 at the start of every draw.
_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")
_ID_MAX = 512
_SLUG_MAX = 80
_TIMEOUT_S = 10.0
_PAGE = 100


class NotADrawWorkspaceError(ValueError):
    """A workspace id outside the per-draw namespace, refused before any call."""


class DrawWorkspaceError(RuntimeError):
    """Honcho did not create a draw's workspace. The draw must not run: a draw
    with no workspace of its own is not isolated, and `ensure_workspace`'s
    best-effort get-or-create would hide that."""


def shared_eval_workspace(deployment: str) -> str:
    """`<deployment>-eval`: the fixed sibling a subject gets when no draw
    workspace is named (a test composing a subject outside a draw)."""
    return f"{deployment}{EVAL_SUFFIX}"


def draw_prefix(deployment: str) -> str:
    """`<deployment>-eval-`: every draw workspace's id starts with this."""
    return f"{shared_eval_workspace(deployment)}-"


def refuse_unless_draw_workspace(workspace: str, deployment: str) -> str:
    """`workspace`, or `NotADrawWorkspaceError`.

    Refused: an empty deployment, anything not starting with the per-draw
    prefix (which includes the deployment's own id and the shared sibling), a
    bare prefix, and an id Honcho would 422.
    """
    if not deployment:
        raise NotADrawWorkspaceError("no deployment workspace to derive the eval prefix from")
    prefix = draw_prefix(deployment)
    if not workspace.startswith(prefix) or len(workspace) <= len(prefix):
        raise NotADrawWorkspaceError(
            f"workspace {workspace!r} is not a draw workspace: it must start with "
            f"{prefix!r}. Refused before any Honcho call, because a draw deletes "
            f"its workspace and {deployment!r} is production's."
        )
    if len(workspace) > _ID_MAX or not _ID_OK.match(workspace):
        raise NotADrawWorkspaceError(
            f"workspace {workspace!r} breaks Honcho's id rule "
            f"(`^[a-zA-Z0-9_-]+$`, at most {_ID_MAX} characters)"
        )
    return workspace


def new_run_id(now: datetime | None = None) -> str:
    """A run's id: when it started, plus four random hex characters so two
    runs started in the same second (two arms) cannot share one."""
    at = now or datetime.now(UTC)
    return f"{at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"


def draw_workspace_id(deployment: str, *, run_id: str, case: str) -> str:
    """`<deployment>-eval-<run_id>-<case>-<nonce>`.

    Traceable (the run and the case, with a sampled draw's `#i` as `-i`) and
    unique: the nonce means two draws with the same run id and case name
    still get two workspaces. That matters because Honcho deletes in the
    BACKGROUND, so a name reused right after its delete could be handed the
    workspace that is still being torn down.
    """
    slug = re.sub(r"[^a-zA-Z0-9-]+", "_", case.replace("#", "-"))
    slug = re.sub(r"_*-_*", "-", slug).strip("_-")[:_SLUG_MAX] or "case"
    run = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_id) or "run"
    workspace = f"{draw_prefix(deployment)}{run}-{slug}-{uuid.uuid4().hex[:6]}"
    return refuse_unless_draw_workspace(workspace, deployment)


class HonchoWorkspaces:
    """Create, list and delete draw workspaces over Honcho's HTTP API.

    Deliberately NOT a method on `nora.infra.honcho.HonchoClient`. Production
    never deletes a workspace, and a delete verb on the client the deployment
    runs is one wrong argument from deleting `nora`. It lives here, in the
    layer only the eval side reaches, and every destructive call passes
    `refuse_unless_draw_workspace` first.
    """

    def __init__(self, base_url: str, *, deployment: str, timeout: float = _TIMEOUT_S) -> None:
        if not deployment:
            raise NotADrawWorkspaceError("no deployment workspace to derive the eval prefix from")
        self._base = base_url.rstrip("/")
        self.deployment = deployment
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _call(
        self, method: str, path: str, *, body: Any = None, params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        async with (
            aiohttp.ClientSession(timeout=self._timeout) as session,
            session.request(method, f"{self._base}{path}", json=body, params=params) as r,
        ):
            try:
                payload = await r.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                payload = None
            return r.status, payload

    async def create(self, workspace: str, *, metadata: dict[str, Any]) -> None:
        refuse_unless_draw_workspace(workspace, self.deployment)
        try:
            status, body = await self._call(
                "POST", "/v3/workspaces",
                body={
                    "id": workspace, "metadata": metadata,
                    "configuration": DRAW_CONFIGURATION,
                },
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DrawWorkspaceError(f"honcho unreachable creating {workspace!r}: {exc}") from exc
        if status >= 400 or not isinstance(body, dict) or body.get("id") != workspace:
            raise DrawWorkspaceError(
                f"honcho did not create {workspace!r}: HTTP {status}, body {body!r}"
            )

    async def list(self) -> list[dict[str, Any]]:
        """Every workspace on the server, all pages."""
        return await self._paged("/v3/workspaces/list")

    async def _paged(self, path: str, *, missing_ok: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            status, body = await self._call(
                "POST", path, body={}, params={"page": page, "size": _PAGE},
            )
            if status == 404 and missing_ok:
                return out
            if status >= 400 or not isinstance(body, dict):
                raise DrawWorkspaceError(f"honcho {path} page {page}: HTTP {status}")
            out.extend(i for i in body.get("items") or () if isinstance(i, dict))
            if page >= int(body.get("pages") or 0):
                return out
            page += 1

    async def delete(self, workspace: str) -> None:
        """Sessions first, then the workspace: Honcho answers 409 to a
        workspace delete while any session in it is active."""
        refuse_unless_draw_workspace(workspace, self.deployment)
        sessions = await self._paged(
            f"/v3/workspaces/{workspace}/sessions/list", missing_ok=True,
        )
        for session in sessions:
            session_id = session.get("id")
            if not isinstance(session_id, str) or session.get("is_active") is False:
                continue
            status, _ = await self._call(
                "DELETE", f"/v3/workspaces/{workspace}/sessions/{session_id}",
            )
            if status >= 400 and status != 404:
                raise DrawWorkspaceError(
                    f"honcho refused to delete session {session_id!r} "
                    f"of {workspace!r}: HTTP {status}"
                )
        for attempt in range(3):
            status, body = await self._call("DELETE", f"/v3/workspaces/{workspace}")
            if status < 400 or status == 404:
                return
            if status != 409:
                break
            await asyncio.sleep(0.5 * (attempt + 1))
        raise DrawWorkspaceError(f"honcho refused to delete {workspace!r}: HTTP {status} {body!r}")


@dataclass
class DrawWorkspace:
    """One draw's workspace and what it cost. `delete_ms` and `deleted` are
    filled when the context exits, so a caller holding this object after the
    draw reads the whole lifecycle (`evals.backbone` keeps them for the run's
    overhead line)."""

    id: str
    create_ms: float
    delete_ms: float | None = None
    deleted: bool = False
    error: str = field(default="")


def _admin(admin: HonchoWorkspaces | None) -> HonchoWorkspaces:
    if admin is not None:
        return admin
    from .obtain import memory_endpoint

    base_url, deployment = memory_endpoint()
    return HonchoWorkspaces(base_url, deployment=deployment)


@contextlib.asynccontextmanager
async def draw_workspace(
    case: str, *, run_id: str, admin: HonchoWorkspaces | None = None,
) -> AsyncGenerator[DrawWorkspace]:
    """A fresh, empty Honcho workspace for one draw, deleted on exit.

    Created BEFORE the subject is composed, so composition's own
    `ensure_workspace` finds it rather than creating it with no marker. A
    create that fails RAISES: a draw with no workspace of its own is not the
    measurement it claims to be, and the task reports it as an ERROR.

    Deleted on every exit, the raising ones included. A delete that fails is
    logged and recorded, not raised: the draw's reading must not depend on
    its cleanup, and `sweep_stale_draw_workspaces` removes what a failed
    delete or a killed run left behind.
    """
    honcho = _admin(admin)
    workspace = draw_workspace_id(honcho.deployment, run_id=run_id, case=case)
    started = time.perf_counter()
    await honcho.create(workspace, metadata={DRAW_MARKER: run_id, "case": case})
    draw = DrawWorkspace(id=workspace, create_ms=(time.perf_counter() - started) * 1000)
    try:
        yield draw
    finally:
        started = time.perf_counter()
        try:
            await honcho.delete(workspace)
            draw.deleted = True
        # A BaseException (KeyboardInterrupt, CancelledError) landing mid-delete
        # is the startup sweep's job, not this block's.
        except Exception as exc:  # noqa: BLE001 — cleanup never decides a reading
            draw.error = f"{type(exc).__name__}: {exc}"
            log.warning("draw workspace %s was not deleted: %s", workspace, draw.error)
        draw.delete_ms = (time.perf_counter() - started) * 1000


def _created_at(row: dict[str, Any]) -> datetime | None:
    raw = row.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


async def sweep_stale_draw_workspaces(
    *, admin: HonchoWorkspaces | None = None, older_than: timedelta = STALE_AFTER,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Delete draw workspaces a killed run left behind; return their ids.

    A workspace is swept only when ALL of these hold: its id starts with
    `<deployment>-eval-`, it carries `DRAW_MARKER` in its metadata, and it is
    older than `older_than`. A live draw of another session is young, the
    shared sibling has no trailing dash, and production has neither, so none
    of them can match. A workspace with no readable `created_at` is left
    alone: its age cannot be shown.
    """
    honcho = _admin(admin)
    cutoff = (now or datetime.now(UTC)) - older_than
    prefix = draw_prefix(honcho.deployment)
    swept: list[str] = []
    for row in await honcho.list():
        workspace = str(row.get("id") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        created = _created_at(row)
        if not workspace.startswith(prefix) or DRAW_MARKER not in metadata:
            continue
        if created is None or created >= cutoff:
            continue
        try:
            await honcho.delete(workspace)
        except Exception as exc:  # noqa: BLE001 — one stuck workspace must not stop the sweep
            log.warning("stale draw workspace %s was not deleted: %s", workspace, exc)
            continue
        swept.append(workspace)
    return tuple(swept)
