"""The host-side READ-ONLY bridge between Nora's container and OpenCLI
(DEC-151).

Nora runs in a Docker container with host networking; `opencli` exists only on
the host (daemon on localhost:19825, driving an authenticated Chrome via the
Browser Bridge extension). This process is the narrow seam between them: the
`social_fetch` tool POSTs `{platform, what, target, limit}` to `/read`, and
this bridge turns that into `opencli <site> <command> <target> -f json` — or
refuses to.

**This process is the security boundary, and the allowlist below is why.** The
tool's own validation is layer one, but the tool runs in the container beside
a model that can be prompt-injected by the very content it fetches — so the
mapping from a request to an `opencli` argv is re-enforced HERE, host-side, and
a write verb (`twitter post`/`like`/`follow`/`delete`, `instagram post`/`like`/
`follow`/`comment`, …) is UNREACHABLE: the command string only ever comes from
`READ_ONLY_ALLOWLIST`'s values, never from the request, and there is no
free-form command passthrough anywhere in this module. A refused request
spawns NO subprocess.

**This process reads NO `nora.config`** — it is not a composition root. It is
configured by environment variables alone (`OPENCLI_BIN`,
`NORA_SOCIAL_BRIDGE_PORT`) so it can run as a plain systemd user service on the
host, tracking whichever checkout the unit points at.

Exit-code mapping follows sysexits, which OpenCLI uses: 0 is a read with a
JSON body on stdout, 66 (EX_NOINPUT) is «nothing there» → `nothing_found`,
69 (EX_UNAVAILABLE) / 77 (EX_NOPERM, not logged in) are «could not check» →
`unavailable`, and anything else — including a timeout — is `unavailable`
(R-27: an error is never a pass). stderr is logged, NEVER echoed into a
response body: it can carry session detail, and the response is read by a
model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any

from aiohttp import web

log = logging.getLogger("nora.social.bridge")

# The ONE mapping a request can resolve to an argv through (DEC-151) —
# `(platform, what) -> (site, command)`, frozen so nothing at runtime can add
# a row. Every command it can produce is one OpenCLI 1.8.7 marks `access:
# "read"` in `opencli list -f json` (verified 2026-08-30).
#
# **X only, and no Instagram row at all** (`test_r86_…` asserts it). Every
# Instagram read goes through the instagram-bridge (design/instagram-adoption/
# 15, /18), and a verb's OpenCLI row is deleted in the same change that gives
# it a bridge route. `("instagram", "profile")` and `("instagram", "posts")`
# left 2026-09-24 (P5; measured 2026-09-22, profile answered 429 all day and
# `user` returned Instagram's HTML shell, so nothing working was lost).
# `("instagram", "search")` left 2026-09-24 with P11, when `instagram_search`
# landed on the bridge's `GET /accounts?query=`.
READ_ONLY_ALLOWLIST: MappingProxyType[tuple[str, str], tuple[str, str]] = (
    MappingProxyType({
        ("x", "post"): ("twitter", "thread"),
        ("x", "profile"): ("twitter", "profile"),
        ("x", "posts"): ("twitter", "tweets"),
        ("x", "search"): ("twitter", "search"),
    })
)

# OpenCLI's write verbs (1.8.7, `access: "write"` in `opencli list -f json`,
# verified 2026-08-30) — the set the allowlist must stay disjoint from. The
# R-86 acceptance test asserts that disjointness so an edit adding a write
# verb to the mapping above goes red here even without re-reading OpenCLI's
# catalogue. This list being incomplete would NOT open a hole — the argv's
# command slot is only ever filled from the frozen mapping — it would only
# weaken the tripwire, so extend it when OpenCLI grows verbs.
OPENCLI_WRITE_VERBS = frozenset({
    "accept", "block", "bookmark", "comment", "delete", "follow",
    "follow-batch", "hide-reply", "like", "list-add", "list-add-batch",
    "list-create", "list-delete", "list-remove", "list-remove-batch",
    "login", "mute-word", "note", "post", "quote", "reel", "reply",
    "reply-dm", "retweet", "save", "story", "unblock", "unbookmark",
    "unfollow", "unlike", "unretweet", "unsave", "collection-create",
    "collection-delete",
})

# The commands that accept a `--limit <n>` flag (verified against
# `opencli list -f json`, 1.8.7, 2026-08-30). `twitter thread` takes one too
# but its `limit` bounds THREAD LENGTH, not «how many posts» — the tool's
# `limit` semantic — so it is deliberately not passed there. When in doubt
# about a flag name, omit the limit rather than guess one.
_LIMIT_COMMANDS = frozenset({
    ("twitter", "tweets"),
    ("twitter", "search"),
})

# Re-validated HERE, host-side, even though the tool validates too — the
# bridge trusts nothing about its caller. Usernames (X and Instagram share one
# charset), tweet ids (a bare id, or one parsed out of an x.com/twitter.com
# status URL with any query junk stripped), and search queries (non-empty,
# length-capped).
_USERNAME_RE = re.compile(r"[A-Za-z0-9_.]{1,30}")
_TWEET_ID_RE = re.compile(r"[0-9]{1,25}")
_TWEET_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]{1,20}"
    r"/status/([0-9]{1,25})(?:[?#]\S*)?"
)
_QUERY_MAX_CHARS = 200
_LIMIT_MIN, _LIMIT_MAX = 1, 20

# The subprocess runner seam: argv in, (returncode, stdout, stderr) out,
# returncode None on timeout or spawn failure. Injected through `__init__`
# (CLAUDE.md's test-seam policy), never a module-level hook.
Runner = Callable[[list[str], float], Awaitable[tuple[int | None, str, str]]]


async def _spawn_opencli(argv: list[str], timeout_seconds: float) -> tuple[int | None, str, str]:
    """Run `opencli` as an argv vector — NEVER a shell, never string
    interpolation into a command line. Never raises: a missing binary or a
    timeout is `(None, "", "")`, which the handler maps to `unavailable`.

    OpenCLI's own browser-command timeout is 60s; the default 75s here leaves
    it room to answer rather than being killed mid-read."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        log.exception("could not spawn %r", argv[0])
        return None, "", ""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("opencli timed out after %.0fs: %s", timeout_seconds, argv[1:3])
        return None, "", ""
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _extract_tweet_id(target: str) -> str | None:
    """The tweet id out of a status URL or a bare numeric id, else None."""
    match = _TWEET_URL_RE.fullmatch(target.strip())
    if match is not None:
        return match.group(1)
    if _TWEET_ID_RE.fullmatch(target.strip()) is not None:
        return target.strip()
    return None


def _validate_target(site: str, command: str, target: str) -> str | None:
    """The argument to hand `opencli`, or None to refuse WITHOUT spawning."""
    if (site, command) == ("twitter", "thread"):
        return _extract_tweet_id(target)
    if command in ("profile", "tweets", "user"):
        stripped = target.strip()
        return stripped if _USERNAME_RE.fullmatch(stripped) is not None else None
    # search
    query = target.strip()
    return query if 0 < len(query) <= _QUERY_MAX_CHARS else None


def _source_url(site: str, command: str, target_arg: str) -> str | None:
    """Where the read came from, for provenance on the wrapped content.

    `x.com/i/status/<id>` is X's own canonical id-only status URL. A command
    this function does not name carries None, and the client falls back to
    naming the platform as the provenance."""
    if site == "twitter":
        if command == "thread":
            return f"https://x.com/i/status/{target_arg}"
        if command in ("profile", "tweets"):
            return f"https://x.com/{target_arg}"
        return "https://x.com/search?q=" + urllib.parse.quote(target_arg)
    return None


class SocialBridge:
    """The `/health` + `/read` request handlers, with the OpenCLI subprocess
    spawn behind an injected `runner` (test-seam policy: collaborators through
    `__init__` only). Built once per process by `__main__`."""

    def __init__(
        self,
        *,
        opencli_bin: str = "opencli",
        timeout_seconds: float = 75.0,
        runner: Runner | None = None,
    ) -> None:
        self._opencli_bin = opencli_bin
        self._timeout_seconds = timeout_seconds
        self._runner: Runner = runner if runner is not None else _spawn_opencli

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_post("/read", self.read)
        return app

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def read(self, request: web.Request) -> web.Response:
        """`{platform, what, target, limit}` → one allowlisted OpenCLI read.

        Every refusal is a 400 with an `unavailable` body and NO subprocess:
        a request this rejects could only have been constructed around the
        tool's own validation, which is exactly the case to fail closed on.
        """
        try:
            body: Any = await request.json()
        except json.JSONDecodeError:
            return self._refuse("body is not JSON")
        if not isinstance(body, dict):
            return self._refuse("body is not an object")
        platform, what, target = body.get("platform"), body.get("what"), body.get("target")
        if not isinstance(platform, str) or not isinstance(what, str) \
                or not isinstance(target, str):
            return self._refuse("platform/what/target must be strings")
        site_command = READ_ONLY_ALLOWLIST.get((platform, what))
        if site_command is None:
            # The boundary itself: the pair names no READ command, so no argv
            # is ever built from it. Logged WITHOUT the values — they are
            # model-influenced strings and this log is an exit.
            log.warning("read refused: (platform, what) outside the allowlist")
            return self._refuse("that read is not supported")
        site, command = site_command
        target_arg = _validate_target(site, command, target)
        if target_arg is None:
            log.warning("read refused: bad target for %s %s", site, command)
            return self._refuse("bad target")
        limit = self._clamp_limit(body.get("limit", 5))
        argv = [self._opencli_bin, site, command, target_arg]
        if (site, command) in _LIMIT_COMMANDS:
            argv += ["--limit", str(limit)]
        argv += ["-f", "json"]
        returncode, stdout, stderr = await self._runner(argv, self._timeout_seconds)
        if stderr:
            # stderr can carry session detail: logged for the operator, NEVER
            # echoed into the response a model reads.
            log.warning("opencli %s %s stderr: %s", site, command, stderr.strip()[:500])
        if returncode == 66:  # EX_NOINPUT — the read ran and found nothing
            return web.json_response({"status": "nothing_found"})
        if returncode != 0:  # 69/77/None/anything else: could not check (R-27)
            log.warning("opencli %s %s exited %s", site, command, returncode)
            return web.json_response({"status": "unavailable"})
        try:
            data: Any = json.loads(stdout)
        except ValueError:
            log.warning("opencli %s %s returned non-JSON on exit 0", site, command)
            return web.json_response({"status": "unavailable"})
        payload: dict[str, Any] = {"status": "ok", "data": data}
        url = _source_url(site, command, target_arg)
        if url is not None:
            payload["source_url"] = url
        return web.json_response(payload)

    @staticmethod
    def _clamp_limit(raw: Any) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 5
        return max(_LIMIT_MIN, min(value, _LIMIT_MAX))

    @staticmethod
    def _refuse(reason: str) -> web.Response:
        return web.json_response({"status": "unavailable", "note": reason}, status=400)
