"""Offline test doubles shared across the suite. Grows as the plan progresses."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    PlatformIdentity,
    SendResult,
)


class SinkSender:
    """A PlatformAdapter that records sends instead of performing them (spec E.3)."""

    platform = "signal"

    def __init__(
        self, *, succeed: bool = True, account: str = "+00000000000",
        timestamp: int | None = None,
    ) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.sent_mentions: list[tuple] = []
        self.sent_styles: list[tuple] = []
        self.sent_attachments: list[tuple] = []
        self.events: list[dict] = []
        self._succeed = succeed
        self._account = account
        # The platform's OWN sent-timestamp, which real signal-cli returns and
        # a reply's `quote.id` points at. `None` — the default
        # — is the shape of a daemon that answered without one.
        self._timestamp = timestamp

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        for event in self.events:
            await on_event(event)

    async def parse(self, raw_event: dict) -> NormalizedMessage | None:
        from nora.platform.signal.parser import parse_event

        return parse_event(raw_event, own_account="+00000000000")

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account)

    async def send(
        self, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
        quote: object | None = None,
    ) -> SendResult:
        """Accepts (and records) the port's decoration parameters.

        Not optional politeness: `Deliverer` passes all three on EVERY send, so
        a double that did not accept them would `TypeError` on a plain message —
        which is the point of the port having grown them rather than the
        `Deliverer` passing them conditionally. Recorded rather than dropped so
        an integration test can assert an @-mention or a bold range actually
        reached the transport, not merely that the composer built one.
        """
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
        self.sent_styles.append(tuple(styles))
        self.sent_attachments.append(tuple(attachments))
        return SendResult(ok=self._succeed, timestamp=self._timestamp)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(platform=self.platform, connected=True, last_event_age_seconds=0.0)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FixedModelSource:
    """A `nora.agent.factory.ModelSource` stand-in that hands out ONE given model.

    For the tests that drive the REAL Strands loop against a stub `Model` — the
    turn-ending probe, the stop-reason probe, the scripted-agent fidelity probe
    — where the model is the instrument being read and there is exactly one
    turn, so per-tag freshness is not the property under test.

    **Deliberately the same object every time, and that is not the
    answers-everything-the-same trap**: the property this class does NOT hold
    is the subject of its own probe file, `tests/test_model_per_tag.py`, which
    uses a source that builds a fresh model per `create()` and asserts on what
    each tag's model call actually saw. Reach for this one only when a test
    wants a single model it can inspect afterwards.
    """

    def __init__(self, model: object) -> None:
        self._model = model
        self.creates = 0

    def create(self) -> object:
        self.creates += 1
        return self._model


#: Honcho's own id rule (`WorkspaceCreate.id` in its OpenAPI).
_HONCHO_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


class FakeHoncho:
    """A Honcho v3 server in-process, for the eval side's per-draw workspaces
    and the subject's own memory traffic.

    Shaped after the deploy host's `/openapi.json`, including its wrong-call
    answers, because a fake that answers everything 200 cannot show a leak or a
    mis-ordered delete:

    - a workspace id outside `^[a-zA-Z0-9_-]+$` (or over 512) is a 422;
    - `DELETE /v3/workspaces/{id}` is a 409 while any session in it is active,
      and 202 otherwise;
    - `DELETE .../sessions/{id}` marks the session inactive, 202;
    - list endpoints page, and a `size` over 100 is a 422;
    - any call under a workspace that does not exist is a 404, so a write
      after the delete, or into a workspace nobody created, is visible.

    `requests` records `(method, path, workspace)` for every call, the
    workspace being the path segment after `/v3/workspaces/` (or the created
    id, for the create).

    Messages and named scopes are modelled too (DEC-214), because the memory
    backfill's safety rests on reading back what a session HOLDS: a message
    POST into a session that does not exist is a 404, a list is paged like
    every other list, and a session's scope memberships are recorded both
    from `SessionCreate.scopes` and from `POST /scopes/{id}/sessions` (204,
    404 for a session that does not exist). `fail_messages_after` makes the
    N+1th message POST a 503 — Honcho's outage shape — and every later one.
    """

    def __init__(self) -> None:
        self.workspaces: dict[str, dict] = {}
        self.sessions: dict[str, dict[str, dict]] = {}
        self.writes: dict[str, list[str]] = {}
        self.deleted: list[str] = []
        self.requests: list[tuple[str, str, str | None]] = []
        # (workspace, session) -> the messages it holds, in arrival order.
        self.messages: dict[tuple[str, str], list[dict]] = {}
        # (workspace, scope) -> the sessions that are members of it.
        self.scopes: dict[tuple[str, str], set[str]] = {}
        # workspace -> its conclusions, as Honcho lists them.
        self.conclusions: dict[str, list[dict]] = {}
        self.deleted_sessions: list[tuple[str, str]] = []
        self.dreams: list[tuple[str, dict]] = []
        self.defer_session_deletes = False
        self._delete_queue: list[tuple[str, str]] = []
        self.fail_messages_after: int | None = None
        # (workspace, scope, session) for every `scope_backfill` Honcho would
        # enqueue — each one an omni dream per touched collection, i.e. model
        # spend — and a switch that makes the scope-member list unreadable.
        self.scope_backfills: list[tuple[str, str, str]] = []
        self.fail_member_lists = False
        self._message_posts = 0
        self._server: TestServer | None = None
        self.url = ""

    def seed(self, workspace: str, *, created_at: str, metadata: dict | None = None) -> None:
        self.workspaces[workspace] = {
            "id": workspace, "metadata": dict(metadata or {}), "configuration": {},
            "created_at": created_at,
        }
        self.sessions.setdefault(workspace, {})

    def run_delete_queue(self) -> None:
        """What Honcho's deriver does with each queued session delete: the
        session's messages and every document stored under it go."""
        for workspace, sid in self._delete_queue:
            self.messages.pop((workspace, sid), None)
            self.conclusions[workspace] = [
                c for c in self.conclusions.get(workspace, []) if c.get("session_id") != sid
            ]
        self._delete_queue.clear()

    async def start(self) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._server = TestServer(app)
        await self._server.start_server()
        self.url = str(self._server.make_url("/")).rstrip("/")
        return self.url

    async def close(self) -> None:
        if self._server is not None:
            await self._server.close()

    @staticmethod
    def _page(items: list, request: web.Request) -> web.Response:
        size = int(request.query.get("size", 50))
        page = int(request.query.get("page", 1))
        if size > 100 or size < 1 or page < 1:
            return web.json_response({"detail": "bad page"}, status=422)
        return web.json_response({
            "items": items[(page - 1) * size: page * size], "total": len(items),
            "page": page, "size": size, "pages": (len(items) + size - 1) // size,
        })

    async def _handle(self, request: web.Request) -> web.Response:
        path, method = request.path, request.method
        body = await request.json() if request.can_read_body else None
        parts = path.strip("/").split("/")
        if parts[:2] != ["v3", "workspaces"]:
            self.requests.append((method, path, None))
            return web.json_response({"detail": "not found"}, status=404)
        if len(parts) == 2 and method == "POST":
            workspace = (body or {}).get("id", "")
            self.requests.append((method, path, workspace))
            legal = isinstance(workspace, str) and len(workspace) <= 512
            if not legal or not _HONCHO_ID.match(workspace):
                return web.json_response({"detail": "invalid id"}, status=422)
            if workspace not in self.workspaces:
                self.seed(
                    workspace, created_at=datetime.now(UTC).isoformat(),
                    metadata=(body or {}).get("metadata") or {},
                )
                self.workspaces[workspace]["configuration"] = dict(
                    (body or {}).get("configuration") or {}
                )
            return web.json_response(self.workspaces[workspace])
        if parts[2:] == ["list"] and method == "POST":
            self.requests.append((method, path, None))
            wanted = ((body or {}).get("filters") or {}).get("id")
            return self._page(
                [w for w in self.workspaces.values() if wanted is None or w["id"] == wanted],
                request,
            )

        workspace, rest = parts[2], parts[3:]
        self.requests.append((method, path, workspace))
        if workspace not in self.workspaces:
            return web.json_response({"detail": "workspace not found"}, status=404)
        sessions = self.sessions[workspace]
        if not rest and method == "PUT":
            # `WorkspaceUpdate`: provided metadata REPLACES the current one.
            if (body or {}).get("metadata") is not None:
                self.workspaces[workspace]["metadata"] = dict(body["metadata"])
            return web.json_response(self.workspaces[workspace])
        if rest == ["conclusions"] and method == "POST":
            rows = (body or {}).get("conclusions") or []
            for row in rows:
                observer = row.get("observer_id", "")
                if observer.startswith("scope.") and (
                    (workspace, observer.removeprefix("scope.")) not in self.scopes
                ):
                    return web.json_response({"detail": "peer not found"}, status=404)
            self.writes.setdefault(workspace, []).append(f"{method} /{'/'.join(rest)}")
            held = self.conclusions.setdefault(workspace, [])
            for row in rows:
                held.append({
                    "id": f"c{len(held) + 1}", "content": row.get("content"),
                    "observer_id": row.get("observer_id"),
                    "observed_id": row.get("observed_id"),
                    "session_id": row.get("session_id"), "level": "explicit",
                    "created_at": datetime.now(UTC).isoformat(),
                })
            return web.json_response(held[-len(rows):], status=201)
        if rest == ["conclusions", "list"] and method == "POST":
            filters = (body or {}).get("filters") or {}
            return self._page(
                [c for c in self.conclusions.get(workspace, [])
                 if all(c.get(k) == v for k, v in filters.items())],
                request,
            )
        if not rest and method == "DELETE":
            if any(s["is_active"] for s in sessions.values()):
                return web.json_response({"detail": "active sessions"}, status=409)
            del self.workspaces[workspace]
            del self.sessions[workspace]
            self.deleted.append(workspace)
            return web.json_response({}, status=202)
        if rest == ["sessions"] and method == "POST":
            sid = (body or {}).get("id", "")
            sessions.setdefault(sid, {"id": sid, "is_active": True, "workspace_id": workspace})
            for scope in (body or {}).get("scopes") or ():
                self.scopes.setdefault((workspace, scope), set()).add(sid)
                # `crud.get_or_create_session`: a session with messages gets a
                # backfill per listed scope, member already or not.
                if self.messages.get((workspace, sid)):
                    self.scope_backfills.append((workspace, scope, sid))
            return web.json_response(sessions[sid])
        if rest == ["scopes"] and method == "POST":
            scope = (body or {}).get("id", "")
            created = (workspace, scope) not in self.scopes
            self.scopes.setdefault((workspace, scope), set())
            return web.json_response({"id": scope}, status=201 if created else 200)
        if len(rest) == 3 and rest[0] == "scopes" and rest[2] == "sessions" and method == "POST":
            wanted = (body or {}).get("session_ids") or []
            if (workspace, rest[1]) not in self.scopes:
                return web.json_response({"detail": "scope not found"}, status=404)
            if any(sid not in sessions for sid in wanted):
                return web.json_response({"detail": "session not found"}, status=404)
            self.scopes[(workspace, rest[1])].update(wanted)
            # `crud.add_sessions_to_scope`: EVERY named session that has
            # messages gets a backfill enqueued — members included.
            for sid in wanted:
                if self.messages.get((workspace, sid)):
                    self.scope_backfills.append((workspace, rest[1], sid))
            return web.Response(status=204)
        if (
            len(rest) == 4 and rest[0] == "scopes" and rest[2:] == ["sessions", "list"]
            and method == "POST"
        ):
            if (workspace, rest[1]) not in self.scopes:
                return web.json_response({"detail": "scope not found"}, status=404)
            members = sorted(self.scopes[(workspace, rest[1])])
            if self.fail_member_lists:
                return web.json_response({"detail": "unavailable"}, status=503)
            return self._page(
                [{"id": sid, "workspace_id": workspace} for sid in members], request,
            )
        if (
            len(rest) == 3 and rest[0] == "sessions" and rest[2] == "messages"
            and method == "POST"
        ):
            if rest[1] not in sessions:
                return web.json_response({"detail": "session not found"}, status=404)
            self._message_posts += 1
            if (
                self.fail_messages_after is not None
                and self._message_posts > self.fail_messages_after
            ):
                return web.json_response({"detail": "unavailable"}, status=503)
            self.writes.setdefault(workspace, []).append(f"{method} /{'/'.join(rest)}")
            held = self.messages.setdefault((workspace, rest[1]), [])
            for m in (body or {}).get("messages", []):
                held.append({
                    "id": f"m{len(held) + 1}", "content": m.get("content", ""),
                    "peer_id": m.get("peer_id"), "session_id": rest[1],
                    "metadata": m.get("metadata") or {},
                    "created_at": m.get("created_at") or datetime.now(UTC).isoformat(),
                })
            return web.json_response(held[-len((body or {}).get("messages", [])):] or [])
        if (
            len(rest) == 4 and rest[0] == "sessions" and rest[2:] == ["messages", "list"]
            and method == "POST"
        ):
            if rest[1] not in sessions:
                return web.json_response({"detail": "session not found"}, status=404)
            return self._page(list(self.messages.get((workspace, rest[1]), [])), request)
        if rest == ["sessions", "list"] and method == "POST":
            return self._page(list(sessions.values()), request)
        if len(rest) == 2 and rest[0] == "sessions" and method == "DELETE":
            if rest[1] not in sessions:
                return web.json_response({"detail": "session not found"}, status=404)
            sessions[rest[1]]["is_active"] = False
            # Honcho's cascade (`crud.delete_session`) runs from a QUEUE after
            # the 202; `defer_session_deletes` keeps it queued until
            # `run_delete_queue()`, so a caller that assumes it is instant shows.
            self.deleted_sessions.append((workspace, rest[1]))
            self._delete_queue.append((workspace, rest[1]))
            if not self.defer_session_deletes:
                self.run_delete_queue()
            return web.json_response({}, status=202)
        if len(rest) == 3 and rest[0] == "sessions" and rest[2] == "peers" and method == "GET":
            if rest[1] not in sessions:
                return web.json_response({"detail": "session not found"}, status=404)
            spoke = {m["peer_id"] for m in self.messages.get((workspace, rest[1]), [])}
            return self._page([{"id": p} for p in sorted(spoke)], request)
        if rest == ["schedule_dream"] and method == "POST":
            self.dreams.append((workspace, dict(body or {})))
            return web.Response(status=204)
        if rest == ["scopes", "list"] and method == "POST":
            return self._page(
                [{"id": s} for (w, s) in sorted(self.scopes) if w == workspace], request,
            )

        # Every other verb the subject's client uses, recorded against THIS
        # workspace and answered in the real shape.
        self.writes.setdefault(workspace, []).append(f"{method} /{'/'.join(rest)}")
        if rest[-1:] == ["representation"]:
            return web.json_response({"representation": ""})
        if rest[-1:] == ["card"]:
            return web.json_response({"peer_card": None})
        if rest[-1:] == ["chat"]:
            return web.json_response({"content": ""})
        if rest[-1:] == ["search"]:
            return web.json_response([])
        if rest == ["peers"]:
            return web.json_response({"id": (body or {}).get("id"), "workspace_id": workspace})
        return web.json_response({"ok": True})
