"""computerd — the API Nora's computer serves (design/sandbox-runtime §2, §3).

It runs INSIDE the microVM, the less-trusted side, and Nora is its only
client. `nora.infra.computer.client.ComputerdRuntime` is the other half of
this protocol. Calls only go one way: Nora calls in, and nothing here calls
Nora. The VM's egress policy (sbx deny-all) is what makes that true; this file
has no client credentials to misuse.

Who runs as whom (ADR 0019):

- **computerd runs as root, and every command runs as its scope's own user.**
  Each scope gets a system user `c-<first 12 hex of the scope>`, created on
  first use (`useradd --system --no-create-home`); its directory is `0o700`
  and owned by that user, and its commands run as it, with `HOME` the scope
  directory and `TMPDIR` its `.tmp`. So one room's command cannot read
  another room's files, and no command can read computerd's token
  (`/proc/<pid>/environ` is owner-only), kill it, or bind its port. The image
  listens on port 80, which only root may bind, so a command cannot put an
  impostor in its place either. The image's `computer` user is for an
  operator's interactive `sbx exec`, not for commands. Run as a normal user
  (the tests do), computerd runs commands as itself.

What it guarantees, whatever the caller sends:

- **One directory per scope.** A scope is a lowercase hex key (Nora derives it
  by hashing the room), never a raw platform id.
- **The file operations never follow a symlink.** Every path is walked one
  component at a time from the scope directory with `O_NOFOLLOW`, and writes
  go to a fresh temp file in that same directory handle. Neither a planted
  link nor a directory swapped mid-call can redirect a read or a root-owned
  write. A command can still create links; the file operations refuse to
  cross them, and `computer_run` is the tool that can.
  Between rooms the boundary is the per-scope user and the `0o700`
  directory: one kernel, so a kernel or root escalation crosses it.
- **Bounded answers.** Command output goes to temp files, not pipes, so
  computerd waits on the process alone. A backgrounded or escaped child can
  neither hang a call nor grow this process. Reads, listings and transfers
  are capped.
- **Four statuses, the R-27 shape.** `ok`, `nothing_found`, `denied` (refused
  as asked: an escaping path, an ambiguous edit, too large) and `unavailable`
  (could not be done: a timeout, an OS error). A non-zero exit code is an `ok`
  run that carries its exit code. A bad token is HTTP 401, not a `denied`: it
  is a configuration fault, never an answer.

Dependencies: the standard library and aiohttp, nothing from `nora`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import fnmatch
import hashlib
import hmac
import os
import pwd
import re
import secrets
import signal
import subprocess
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

from aiohttp import web

SCOPE_RE = re.compile(r"^[0-9a-f]{16,64}$")

# Server-side ceilings. A request may ask for less, never more.
MAX_OUTPUT_CHARS = 100_000
MAX_READ_CHARS = 200_000
MAX_READ_FILE_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BYTES = 50 * 1024 * 1024
MAX_LIST_ENTRIES = 1000
MAX_RUN_SECONDS = 900
MATCH_ALL = frozenset({"", "*", "**", "**/*"})

# The token this process was started with never reaches a child process.
TOKEN_ENV = "COMPUTERD_TOKEN"  # noqa: S105 - the variable NAME, not a secret

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


class RefusedError(Exception):
    """A request refused as asked; becomes `denied`."""


class MissingError(Exception):
    """The thing asked about is not there; becomes `nothing_found`."""


def _components(raw: Any) -> list[str]:
    """A relative path as its components, refusing absolute paths, `..`,
    NULs and anything that names the scope directory itself."""
    if not isinstance(raw, str) or raw.startswith("/") or "\x00" in raw:
        raise RefusedError("path must be relative to the workspace")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise RefusedError("path escapes the workspace")
    return parts


def scope_user(scope: str) -> str:
    """The system user a scope's commands run as: `c-` and the scope's first
    12 hex characters (48 bits, and a Linux user name stays under 32)."""
    return f"c-{scope[:12]}"


class Owner:
    """Who owns a scope's files and runs its commands.

    As root with `isolate` set (the image), each scope resolves to its own
    system user, created on first use; otherwise (the tests, computerd run
    as a normal user) nothing is changed hands and commands run as
    computerd's own user."""

    def __init__(self, *, isolate: bool) -> None:
        self.isolate = isolate and os.geteuid() == 0
        self._ids: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def ids(self, scope: str) -> tuple[int, int] | None:
        if not self.isolate:
            return None
        with self._lock:
            cached = self._ids.get(scope)
            if cached is not None:
                return cached
            name = scope_user(scope)
            try:
                entry = pwd.getpwnam(name)
            except KeyError:
                try:
                    subprocess.run(  # noqa: S603 - fixed argv, the name is hex
                        ["useradd", "--system", "--no-create-home", "--user-group",  # noqa: S607
                         "--shell", "/usr/sbin/nologin", "--home-dir", "/nonexistent", name],
                        check=True, capture_output=True,
                    )
                except subprocess.CalledProcessError:
                    # Another computerd process created it first: the user now
                    # exists, which is the state wanted. Anything else raises
                    # KeyError below and the call answers unavailable.
                    pass
                entry = pwd.getpwnam(name)
            self._ids[scope] = (entry.pw_uid, entry.pw_gid)
            return self._ids[scope]

    def chown_fd(self, fd: int, scope: str) -> None:
        ids = self.ids(scope)
        if ids is not None:
            os.fchown(fd, *ids)


class Computer:
    def __init__(self, *, root: Path, token: str, isolate_scopes: bool = True) -> None:
        if not token:
            raise ValueError("computerd needs a non-empty token")
        self.root = root
        self._token = token
        self.owner = Owner(isolate=isolate_scopes)

    # -- request plumbing -------------------------------------------------

    def authorised(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {self._token}")

    @staticmethod
    def valid_scope(scope: Any) -> bool:
        return isinstance(scope, str) and SCOPE_RE.match(scope) is not None

    @contextlib.contextmanager
    def _dir(self, scope: str, parts: list[str], *, create: bool) -> Iterator[int]:
        """A directory fd for `parts` under the scope, walked without
        following any link. `create` makes missing directories, owned by the
        scope's user; the scope directory itself is `0o700`. Yields the fd;
        closes everything it opened."""
        opened: list[int] = []
        try:
            fd = os.open(self.root, _DIR_FLAGS)
            opened.append(fd)
            for name in (scope, *parts):
                try:
                    fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise MissingError("no such file or directory") from None
                    os.mkdir(name, 0o700 if len(opened) == 1 else 0o755, dir_fd=fd)
                    fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    self.owner.chown_fd(fd, scope)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        msg = "a path component is a link or not a directory"
                        raise RefusedError(msg) from None
                    raise
                opened.append(fd)
            yield fd
        finally:
            for f in reversed(opened):
                os.close(f)

    def _open_file(self, scope: str, raw: Any) -> tuple[int, os.stat_result]:
        """An fd to an existing regular file, read-only, links refused."""
        *parents, name = _components(raw)
        with self._dir(scope, parents, create=False) as dfd:
            try:
                fd = os.open(name, _READ_FLAGS, dir_fd=dfd)
            except FileNotFoundError:
                raise MissingError("no such file") from None
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    msg = "the path is a link; use computer_run to follow it"
                    raise RefusedError(msg) from None
                raise
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise RefusedError("not a regular file")
        return fd, info

    def _read_bytes(self, scope: str, raw: Any, cap: int) -> bytes:
        fd, info = self._open_file(scope, raw)
        try:
            if info.st_size > cap:
                raise RefusedError(
                    f"file is {info.st_size} bytes, over the {cap} cap; "
                    "use computer_run with head, tail or grep",
                )
            with os.fdopen(fd, "rb", closefd=False) as f:
                return f.read(cap + 1)[:cap]
        finally:
            os.close(fd)

    def _store(self, scope: str, raw: Any, data: bytes) -> dict[str, Any]:
        *parents, name = _components(raw)
        with self._dir(scope, parents, create=True) as dfd:
            tmp = f".{name}.{secrets.token_hex(6)}.computerd-tmp"
            fd = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd,
            )
            try:
                with os.fdopen(fd, "wb", closefd=False) as f:
                    f.write(data)
                self.owner.chown_fd(fd, scope)
            finally:
                os.close(fd)
            # The audit's "before": what the target held just before it is
            # replaced, hashed through the same no-follow walk. None when there
            # was no regular file there (new, a link, or a directory).
            previous = _previous_sha256(dfd, name)
            try:
                os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(tmp, dir_fd=dfd)
                raise
        return {"status": "ok", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "previous_sha256": previous}

    # -- file operations (blocking; run in a worker thread) ---------------

    def read(self, scope: str, body: dict[str, Any]) -> dict[str, Any]:
        data = self._read_bytes(scope, body.get("path"), MAX_READ_FILE_BYTES)
        digest = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"status": "ok", "binary": True, "size": len(data), "sha256": digest,
                    "content": ""}
        offset = max(_int(body.get("offset"), 0), 0)
        limit = _limit(body.get("limit"), MAX_READ_CHARS)
        lines = text.splitlines(keepends=True)
        chosen = "".join(lines[offset:])
        return {"status": "ok", "binary": False, "size": len(data), "sha256": digest,
                "content": chosen[:limit], "truncated": len(chosen) > limit,
                "lines_total": len(lines)}

    def write(self, scope: str, body: dict[str, Any]) -> dict[str, Any]:
        content = body.get("content")
        if not isinstance(content, str):
            raise RefusedError("content must be a string")
        data = content.encode("utf-8")
        if len(data) > MAX_TRANSFER_BYTES:
            raise RefusedError("content is too large")
        return self._store(scope, body.get("path"), data)

    def edit(self, scope: str, body: dict[str, Any]) -> dict[str, Any]:
        old, new = body.get("old"), body.get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise RefusedError("old must be a non-empty string, new a string")
        text = self._read_bytes(scope, body.get("path"), MAX_READ_FILE_BYTES).decode("utf-8")
        count = text.count(old)
        if count == 0:
            raise MissingError("old text not found in the file")
        if count > 1:
            raise RefusedError(f"old text matches {count} places; make it unique")
        return self._store(scope, body.get("path"), text.replace(old, new, 1).encode("utf-8"))

    def files(self, scope: str, body: dict[str, Any]) -> dict[str, Any]:
        pattern = body.get("pattern") or ""
        if not isinstance(pattern, str):
            raise RefusedError("pattern must be a string")
        base = self.root / scope
        try:
            if not stat.S_ISDIR(os.lstat(base).st_mode):
                return {"status": "nothing_found", "entries": [], "truncated": False}
        except FileNotFoundError:
            return {"status": "nothing_found", "entries": [], "truncated": False}
        entries: list[dict[str, Any]] = []
        truncated = False
        for current, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                rel = str((Path(current) / name).relative_to(base))
                if not _matches(rel, name, pattern):
                    continue
                if len(entries) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                try:
                    info = os.lstat(Path(current) / name)
                except OSError:
                    continue
                entry: dict[str, Any] = {"path": rel, "size": info.st_size}
                if stat.S_ISLNK(info.st_mode):
                    entry["link"] = True
                entries.append(entry)
            if truncated:
                break
        return {"status": "ok" if entries else "nothing_found", "entries": entries,
                "truncated": truncated}

    def fetch(self, scope: str, body: dict[str, Any]) -> bytes:
        cap = _limit(body.get("max_bytes"), MAX_TRANSFER_BYTES)
        return self._read_bytes(scope, body.get("path"), cap)

    # -- commands ---------------------------------------------------------

    async def run(self, scope: str, body: dict[str, Any]) -> dict[str, Any]:
        command = body.get("command")
        if not isinstance(command, str) or not command.strip():
            raise RefusedError("command must be a non-empty string")
        timeout = min(max(_float(body.get("timeout_s"), 120.0), 1.0), MAX_RUN_SECONDS)
        limit = _limit(body.get("max_output_chars"), MAX_OUTPUT_CHARS, default=8000)
        # `body["limits"]` (cpu_seconds, max_processes, max_file_bytes) is a
        # RESERVED field: Nora sends it only when `[computer] limits` names
        # one, and it is deliberately NOT applied here. The operator ruled on
        # 2026-09-30 that the computer is semi-trusted, so no per-command
        # resource limit is enforced; the slot exists so that turning limits
        # on later is config plus one change here (setrlimit in a preexec).
        await asyncio.to_thread(self._ensure_scope, scope)
        workdir = self.root / scope
        env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
        # HOME is the room's own directory, so a profile or cache one room's
        # command writes is not read by every other room's.
        env.update(HOME=str(workdir), PWD=str(workdir), TMPDIR=str(workdir / ".tmp"))
        user: dict[str, Any] = {}
        ids = self.owner.ids(scope)
        if ids is not None:
            user = {"user": ids[0], "group": ids[1], "extra_groups": []}
        started = time.monotonic()
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "bash", "-c", command, cwd=workdir, env=env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=err,
                    start_new_session=True, **user,
                )
            except OSError as exc:
                return {"status": "unavailable", "reason": f"could not start bash: {exc}"}
            timed_out = False
            try:
                # Files, not pipes: this waits for the process alone, never for
                # a backgrounded child that inherited the output.
                await asyncio.wait_for(proc.wait(), timeout)
            except TimeoutError:
                timed_out = True
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            stdout, out_cut = _bounded(out, limit)
            stderr, err_cut = _bounded(err, max(limit // 4, 200))
        fields = {
            "exit_code": proc.returncode, "stdout": stdout, "stderr": stderr,
            "truncated": out_cut or err_cut,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if timed_out:
            return {"status": "unavailable", "reason": f"timed out after {timeout:g}s", **fields}
        return {"status": "ok", **fields}

    def _ensure_scope(self, scope: str) -> None:
        """The scope directory and its `.tmp`, both `0o700` and the scope
        user's. Re-asserted on every run, so a directory made before per-scope
        users existed is closed and handed over too. Files INSIDE such a
        directory keep their old owner: the deploy recreates the VM, so none
        exist (deploy/computer/README.md)."""
        with self._dir(scope, [".tmp"], create=True) as tmp_fd:
            os.fchmod(tmp_fd, 0o700)
            # Handed over as well: a `.tmp` made before per-scope users (owned
            # by `computer`) would leave TMPDIR unwritable for the room's user.
            self.owner.chown_fd(tmp_fd, scope)
        with self._dir(scope, [], create=True) as fd:
            os.fchmod(fd, 0o700)
            self.owner.chown_fd(fd, scope)


def _previous_sha256(dfd: int, name: str) -> str | None:
    """sha256 of the regular file `name` in directory `dfd`, opened without
    following a link, or None when there is none to hash."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dfd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1 << 20):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _bounded(f: IO[bytes], limit: int) -> tuple[str, bool]:
    """The first three quarters and the last quarter of `limit` characters of
    a command's output, with a count of what was dropped between them."""
    size = f.seek(0, os.SEEK_END)
    head_n = limit * 3 // 4
    tail_n = limit - head_n
    f.seek(0)
    if size <= limit:
        return f.read().decode("utf-8", errors="replace"), False
    head = f.read(head_n).decode("utf-8", errors="replace")
    f.seek(size - tail_n)
    tail = f.read(tail_n).decode("utf-8", errors="replace")
    return f"{head}\n…[{size - head_n - tail_n} bytes omitted]…\n{tail}", True


def _matches(rel: str, name: str, pattern: str) -> bool:
    if pattern in MATCH_ALL:
        return True
    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:])


def _int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _limit(raw: Any, ceiling: int, *, default: int | None = None) -> int:
    """A caller's cap, clamped into [1, ceiling]. Absent or not positive means
    `default` (the ceiling itself when no default is given)."""
    value = _int(raw, 0)
    if value <= 0:
        value = default if default is not None else ceiling
    return min(value, ceiling)


def create_app(*, root: Path, token: str, isolate_scopes: bool = True) -> web.Application:
    computer = Computer(root=root, token=token, isolate_scopes=isolate_scopes)
    file_ops: dict[str, Callable[[str, dict[str, Any]], Any]] = {
        "read": computer.read, "write": computer.write, "edit": computer.edit,
        "files": computer.files, "fetch": computer.fetch,
    }

    async def health(_: web.Request) -> web.Response:
        # No auth and no detail: what the lifecycle unit reads to know that
        # computerd itself answers (under sbx a TCP connect proves nothing).
        return web.json_response({"service": "computerd", "status": "ok"})

    def answer(result: Any) -> web.Response:
        if isinstance(result, bytes):
            return web.Response(
                body=result, content_type="application/octet-stream",
                headers={"X-Computerd-Sha256": hashlib.sha256(result).hexdigest()},
            )
        return web.json_response(result)

    async def dispatch(request: web.Request) -> web.Response:
        if not computer.authorised(request):
            return web.json_response({"status": "unauthorised"}, status=401)
        op = request.match_info["op"]
        try:
            if op == "put":
                scope, raw = request.query.get("scope"), request.query.get("path")
                try:
                    data = await request.read()
                except web.HTTPRequestEntityTooLarge:
                    raise RefusedError("body is too large") from None
                if not computer.valid_scope(scope):
                    raise RefusedError("scope must be a lowercase hex key")
                return answer(await asyncio.to_thread(computer._store, scope, raw, data))
            try:
                body = await request.json()
            except ValueError:
                raise RefusedError("body must be JSON") from None
            if not isinstance(body, dict):
                raise RefusedError("body must be a JSON object")
            scope = body.get("scope")
            if not computer.valid_scope(scope):
                raise RefusedError("scope must be a lowercase hex key")
            if op == "run":
                return answer(await computer.run(scope, body))
            handler = file_ops.get(op)
            if handler is None:
                return web.json_response({"status": "unknown operation"}, status=404)
            return answer(await asyncio.to_thread(handler, scope, body))
        except RefusedError as exc:
            return web.json_response({"status": "denied", "reason": str(exc)})
        except MissingError as exc:
            return web.json_response({"status": "nothing_found", "reason": str(exc),
                                      "entries": []})
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return web.json_response({"status": "unavailable",
                                      "reason": f"{type(exc).__name__}: {exc}"})

    app = web.Application(client_max_size=MAX_TRANSFER_BYTES + 1024)
    app.router.add_get("/v1/health", health)
    app.router.add_post("/v1/{op}", dispatch)
    return app


def build_parser() -> argparse.ArgumentParser:
    """computerd's command line. A function so a test can check that the
    flags `deploy/computer/nora-computer` passes are ones this accepts: a
    removed flag there makes computerd exit at start, and the computer is
    down until someone reads its log."""
    parser = argparse.ArgumentParser(description="computerd, the API of Nora's computer")
    parser.add_argument("--root", default="/workspace")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - inside the VM; sbx publishes it
    parser.add_argument("--port", type=int, default=80)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    token = os.environ.get(TOKEN_ENV, "")
    app = create_app(root=Path(args.root), token=token)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
