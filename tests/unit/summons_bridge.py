"""A recording fake of the instagram-bridge's SUMMONS routes, for
`test_summons_client.py`, `test_summons_occasion.py` and
`test_summons_consumer.py`.

It behaves like the Go handlers (`deploy/instagram-bridge/internal/transport/
http/{comments,posts,errors}.go`), including their wrong-call shapes:
an unmatched path is the mux's own plain-text 404, a failure is the ONE
envelope `{"error": {"code", "message"}}` at the status `errors.go` maps that
code to, and the write answers 201 with `{id, reply_to_comment_id}`. It is a
CATCH-ALL, so any request at all — any method, any path — is recorded before
it is answered, and a request the client should never make still lands in
`calls`.

The post body is the Go-generated contract fixture `posts_carousel_ok.json`.
The comments body is written here from the Go struct, because no generated
fixture exists for it. There is NO `/mentions` route: Nora does not read
the notification feed, so a request for it is the mux's 404 and is still
recorded in `calls`, where a test can see it was never made.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "instagram_bridge"

SHORTCODE = "CmiJBr2LAAB"
MEDIA_ID = "3100000000000000001"
SUMMONS_COMMENT = "17900000000000001"
SUMMONER_IGID = "10000001"
STRANGER_IGID = "10000999"

#: errors.go's status for every code this fake can answer.
STATUS_FOR_CODE: dict[str, int] = {
    "writes_disabled": 403, "writes_blocked": 403, "not_a_summons": 403,
    "already_replied": 409, "needs_human": 409,
    "invalid_text": 400, "invalid_id": 400,
    "daily_cap": 429, "too_soon": 429,
    "ledger_unavailable": 503, "unavailable": 503,
    "not_found": 404, "unsupported": 501, "upstream_rejected": 502,
    "internal": 500,
}


def envelope(code: str, message: str = "refused") -> tuple[int, dict[str, Any]]:
    return STATUS_FOR_CODE[code], {"error": {"code": code, "message": message}}


def comment_item(
    comment_id: str, text: str, *, username: str = "someone_else",
    at: str | None = "2026-09-25T11:00:00Z", igid: str = "18755011",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": comment_id, "author": {"igid": igid, "username": username}, "text": text,
    }
    if at is not None:
        item["at"] = at
    return item


def default_comments() -> list[dict[str, Any]]:
    return [
        comment_item("17900000000000010", "first!", at="2026-09-25T09:00:00Z"),
        comment_item("17900000000000011", "herons are huge", at="2026-09-25T10:00:00Z"),
        comment_item(
            SUMMONS_COMMENT, "@noraexample is this really a heron?",
            username="miles_example", igid=SUMMONER_IGID, at="2026-09-25T11:55:00Z",
        ),
        comment_item("17900000000000012", "lol", at="2026-09-25T11:58:00Z"),
    ]


class FakeSummonsBridge:
    _POST = re.compile(r"/posts/([A-Za-z0-9_-]+)")
    _COMMENTS = re.compile(r"/posts/([A-Za-z0-9_-]+)/comments")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        post = json.loads((FIXTURES / "posts_carousel_ok.json").read_text())
        self.post: tuple[int, Any] = (post["status"], post["body"])
        self.comments: tuple[int, Any] = (200, {
            "items": default_comments(), "next_cursor": None, "skipped_count": 0,
        })
        self.write: tuple[int, Any] = (201, {
            "id": "17900000000000999", "reply_to_comment_id": SUMMONS_COMMENT,
        })

    def posts(self) -> list[dict[str, Any]]:
        """Every write body the bridge received."""
        return list(self.bodies)

    async def handle(self, request: web.Request) -> web.Response:
        path = request.path_qs
        self.calls.append((request.method, path))
        bare = request.path
        if self._COMMENTS.fullmatch(bare):
            if request.method == "GET":
                return self._answer(self.comments)
            if request.method == "POST":
                self.bodies.append(json.loads(await request.text()))
                return self._answer(self.write)
        if request.method == "GET" and self._POST.fullmatch(bare):
            return self._answer(self.post)
        return web.Response(status=404, text="404 page not found")

    @staticmethod
    def _answer(answer: tuple[int, Any]) -> web.Response:
        status, body = answer
        if isinstance(body, str):
            return web.Response(status=status, text=body)
        return web.json_response(body, status=status)


async def serve(fake: FakeSummonsBridge) -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()
