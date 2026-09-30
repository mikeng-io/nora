"""computerd — the API Nora's computer serves (design/sandbox-runtime §2, §3).

It runs INSIDE the microVM, the less-trusted side, and Nora is its only
client. `nora.infra.computer.client.ComputerdRuntime` is the other half of
this protocol. Calls only go one way: Nora calls in, and nothing here calls
Nora. The VM's egress policy (sbx deny-all) is what makes that true; this file
has no client credentials to misuse.

What it guarantees, whatever the caller sends:

- **One directory per scope.** A scope is a lowercase hex key (Nora derives it
  by hashing the room), never a raw platform id. Every path is resolved,
  symlinks included, and refused if it lands outside `<root>/<scope>/`. This
  is disclosure hygiene between rooms, not a security boundary: everything
  shares one VM.
- **Bounded answers.** Command output keeps a head and a tail and says how
  much it dropped. Reads, listings and fetches are capped. A command that
  prints gigabytes costs this process a counter, not memory.
- **Four statuses, the R-27 shape.** `ok`, `nothing_found`, `denied` (refused
  as asked: an escaping path, an ambiguous edit, too large) and `unavailable`
  (it could not be done: a timeout, an OS error). A non-zero exit code is an
  `ok` run that carries its exit code.

Dependencies: the standard library and aiohttp, nothing from `nora`. The
image installs only what this file imports.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fnmatch
import hashlib
import hmac
import os
import re
import signal
import time
from pathlib import Path
from typing import Any

from aiohttp import web

SCOPE_RE = re.compile(r"^[0-9a-f]{16,64}$")

# Server-side ceilings. A request may ask for less, never more.
MAX_OUTPUT_CHARS = 100_000
MAX_READ_CHARS = 200_000
MAX_FETCH_BYTES = 50 * 1024 * 1024
MAX_PUT_BYTES = 50 * 1024 * 1024
MAX_LIST_ENTRIES = 1000
MAX_RUN_SECONDS = 900

# The token this process was started with never reaches a child process.
TOKEN_ENV = "COMPUTERD_TOKEN"  # noqa: S105 - the variable NAME, not a secret


def _status(status: str, **fields: Any) -> web.Response:
    return web.json_response({"status": status, **fields})


class _Bounded:
    """Keeps the first `head` and the last `tail` characters of a stream and
    counts the rest, so a runaway command cannot grow this process."""

    def __init__(self, limit: int) -> None:
        self._head_limit = limit * 3 // 4
        self._tail_limit = limit - self._head_limit
        self._head = ""
        self._tail = ""
        self.total = 0

    def feed(self, text: str) -> None:
        self.total += len(text)
        room = self._head_limit - len(self._head)
        if room > 0:
            self._head += text[:room]
            text = text[room:]
        if text and self._tail_limit:
            self._tail = (self._tail + text)[-self._tail_limit:]

    def render(self) -> str:
        dropped = self.total - len(self._head) - len(self._tail)
        if dropped <= 0:
            return self._head + self._tail
        return f"{self._head}\n…[{dropped} characters omitted]…\n{self._tail}"

    @property
    def truncated(self) -> bool:
        return self.total > len(self._head) + len(self._tail)


class Computer:
    def __init__(self, *, root: Path, token: str) -> None:
        if not token:
            raise ValueError("computerd needs a non-empty token")
        self.root = root
        self._token = token

    # -- request plumbing -------------------------------------------------

    def authorised(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {self._token}")

    def scope_dir(self, scope: Any) -> Path | None:
        if not isinstance(scope, str) or not SCOPE_RE.match(scope):
            return None
        return self.root / scope

    def resolve(self, base: Path, raw: Any) -> Path | None:
        """`raw` relative to `base`, or None when it escapes after symlinks
        and `..` are resolved. Absolute paths are refused outright."""
        if not isinstance(raw, str) or not raw or raw.startswith("/") or "\x00" in raw:
            return None
        base_real = base.resolve()
        target = (base / raw).resolve()
        if target != base_real and not target.is_relative_to(base_real):
            return None
        return target

    # -- operations -------------------------------------------------------

    async def run(self, scope_dir: Path, body: dict[str, Any]) -> web.Response:
        command = body.get("command")
        if not isinstance(command, str) or not command.strip():
            return _status("denied", reason="command must be a non-empty string")
        timeout = min(float(body.get("timeout_s") or 120), MAX_RUN_SECONDS)
        limit = min(int(body.get("max_output_chars") or 8000), MAX_OUTPUT_CHARS)
        scope_dir.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-lc", command, cwd=scope_dir, env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return _status("unavailable", reason=f"could not start bash: {exc}")
        out, err = _Bounded(limit), _Bounded(limit // 4 or 1)

        async def pump(stream: asyncio.StreamReader | None, sink: _Bounded) -> None:
            if stream is None:
                return
            while chunk := await stream.read(65536):
                sink.feed(chunk.decode("utf-8", errors="replace"))

        pumps = asyncio.gather(pump(proc.stdout, out), pump(proc.stderr, err))
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout)
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        await pumps
        fields = {
            "exit_code": proc.returncode,
            "stdout": out.render(),
            "stderr": err.render(),
            "truncated": out.truncated or err.truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if timed_out:
            return _status("unavailable", reason=f"timed out after {timeout:g}s", **fields)
        return _status("ok", **fields)

    async def read(self, scope_dir: Path, body: dict[str, Any]) -> web.Response:
        path = self.resolve(scope_dir, body.get("path"))
        if path is None:
            return _status("denied", reason="path escapes the workspace")
        if not path.is_file():
            return _status("nothing_found", reason="no such file")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _status("ok", binary=True, size=len(data), sha256=digest, content="")
        offset = max(int(body.get("offset") or 0), 0)
        limit = min(int(body.get("limit") or MAX_READ_CHARS), MAX_READ_CHARS)
        lines = text.splitlines(keepends=True)
        chosen = "".join(lines[offset:])
        truncated = len(chosen) > limit
        return _status(
            "ok", binary=False, size=len(data), sha256=digest,
            content=chosen[:limit], truncated=truncated, lines_total=len(lines),
        )

    async def write(self, scope_dir: Path, body: dict[str, Any]) -> web.Response:
        path = self.resolve(scope_dir, body.get("path"))
        if path is None:
            return _status("denied", reason="path escapes the workspace")
        content = body.get("content")
        if not isinstance(content, str):
            return _status("denied", reason="content must be a string")
        data = content.encode("utf-8")
        if len(data) > MAX_PUT_BYTES:
            return _status("denied", reason="content is too large")
        return self._store(path, data)

    async def edit(self, scope_dir: Path, body: dict[str, Any]) -> web.Response:
        path = self.resolve(scope_dir, body.get("path"))
        if path is None:
            return _status("denied", reason="path escapes the workspace")
        old, new = body.get("old"), body.get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            return _status("denied", reason="old must be a non-empty string, new a string")
        if not path.is_file():
            return _status("nothing_found", reason="no such file")
        text = path.read_text("utf-8", errors="strict")
        count = text.count(old)
        if count == 0:
            return _status("nothing_found", reason="old text not found in the file")
        if count > 1:
            return _status("denied", reason=f"old text matches {count} places; make it unique")
        return self._store(path, text.replace(old, new, 1).encode("utf-8"))

    async def files(self, scope_dir: Path, body: dict[str, Any]) -> web.Response:
        pattern = body.get("pattern") or "**/*"
        if not isinstance(pattern, str):
            return _status("denied", reason="pattern must be a string")
        if not scope_dir.is_dir():
            return _status("nothing_found", entries=[], truncated=False)
        base = scope_dir.resolve()
        entries = []
        truncated = False
        for current, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                rel = str((Path(current) / name).relative_to(base))
                if not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern)):
                    continue
                if len(entries) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                try:
                    size = (Path(current) / name).stat().st_size
                except OSError:
                    continue
                entries.append({"path": rel, "size": size})
            if truncated:
                break
        status = "ok" if entries else "nothing_found"
        return _status(status, entries=entries, truncated=truncated)

    async def fetch(self, scope_dir: Path, body: dict[str, Any]) -> web.Response:
        path = self.resolve(scope_dir, body.get("path"))
        if path is None:
            return _status("denied", reason="path escapes the workspace")
        if not path.is_file():
            return _status("nothing_found", reason="no such file")
        cap = min(int(body.get("max_bytes") or MAX_FETCH_BYTES), MAX_FETCH_BYTES)
        size = path.stat().st_size
        if size > cap:
            return _status("denied", reason=f"file is {size} bytes, over the {cap} cap", size=size)
        data = path.read_bytes()
        return web.Response(
            body=data, content_type="application/octet-stream",
            headers={"X-Computerd-Sha256": hashlib.sha256(data).hexdigest()},
        )

    async def put(self, request: web.Request, scope_dir: Path) -> web.Response:
        path = self.resolve(scope_dir, request.query.get("path"))
        if path is None:
            return _status("denied", reason="path escapes the workspace")
        data = await request.content.read(MAX_PUT_BYTES + 1)
        if len(data) > MAX_PUT_BYTES:
            return _status("denied", reason="body is too large")
        return self._store(path, data)

    def _store(self, path: Path, data: bytes) -> web.Response:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.computerd-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError as exc:
            return _status("unavailable", reason=f"could not write: {exc}")
        return _status("ok", size=len(data), sha256=hashlib.sha256(data).hexdigest())


def create_app(*, root: Path, token: str) -> web.Application:
    computer = Computer(root=root, token=token)
    json_ops = {
        "run": computer.run, "read": computer.read, "write": computer.write,
        "edit": computer.edit, "files": computer.files, "fetch": computer.fetch,
    }

    async def health(_: web.Request) -> web.Response:
        # No auth and no detail: this is what the lifecycle unit reads to know
        # computerd itself answers (a TCP connect proves nothing under sbx).
        return web.json_response({"service": "computerd", "status": "ok"})

    async def dispatch(request: web.Request) -> web.Response:
        if not computer.authorised(request):
            return web.json_response({"status": "denied", "reason": "unauthorised"}, status=401)
        op = request.match_info["op"]
        if op == "put":
            scope_dir = computer.scope_dir(request.query.get("scope"))
            if scope_dir is None:
                return _status("denied", reason="scope must be a lowercase hex key")
            return await computer.put(request, scope_dir)
        handler = json_ops.get(op)
        if handler is None:
            return web.json_response({"status": "denied", "reason": "unknown operation"}, status=404)
        try:
            body = await request.json()
        except ValueError:
            return _status("denied", reason="body must be JSON")
        if not isinstance(body, dict):
            return _status("denied", reason="body must be a JSON object")
        scope_dir = computer.scope_dir(body.get("scope"))
        if scope_dir is None:
            return _status("denied", reason="scope must be a lowercase hex key")
        try:
            return await handler(scope_dir, body)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return _status("unavailable", reason=f"{type(exc).__name__}: {exc}")

    app = web.Application(client_max_size=MAX_PUT_BYTES + 1024)
    app.router.add_get("/v1/health", health)
    app.router.add_post("/v1/{op}", dispatch)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="computerd, the API of Nora's computer")
    parser.add_argument("--root", default="/workspace")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - inside the VM; sbx publishes it
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    token = os.environ.get(TOKEN_ENV, "")
    web.run_app(create_app(root=Path(args.root), token=token), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
