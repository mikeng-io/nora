"""computerd — the API Nora's computer serves.

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

- **What the computer holds, read fresh** (`/v1/state`): a
  scope's newest files (path, size, mtime; never through a link) and its
  tmux sessions, listed as the scope's own user on the scope's own socket,
  which is where every `run` puts a `tmux new`. A session is `running` when
  a pane runs a non-shell command OR its process has any child: a `bash
  train.sh`, a `bash -c` chain or a backgrounded server all show `bash` as
  the foreground command. And every process the scope's user owns, read
  from `/proc` without following a link (pid, command cut to 80 characters,
  start and elapsed seconds, the newest 20): a `nohup … &` job has no tmux
  session and is still running. Nothing here kills a session or a process,
  or deletes a file.

Dependencies: the standard library and aiohttp, nothing from `nora`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import errno
import fcntl
import fnmatch
import hashlib
import heapq
import hmac
import os
import pwd
import re
import secrets
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import IO, Any, TypeGuard

from aiohttp import WSMsgType, web

SCOPE_RE = re.compile(r"^[0-9a-f]{16,64}$")

#: The root-owned file `nora-computer restore` takes EXCLUSIVE while it
#: replaces room directories. Every room operation takes it SHARED: a restore
#: waits for a command to finish, and no command starts under a directory
#: being renamed away. It lives at the root of `/workspace`, which no scope
#: user can write, and is opened `O_NOFOLLOW` below.
RESTORE_LOCK = ".restore.lock"

# Server-side ceilings. A request may ask for less, never more.
MAX_OUTPUT_CHARS = 100_000
MAX_READ_CHARS = 200_000
MAX_READ_FILE_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BYTES = 50 * 1024 * 1024
MAX_LIST_ENTRIES = 1000
MAX_RUN_SECONDS = 900
MATCH_ALL = frozenset({"", "*", "**", "**/*"})
# The observed-state read (`/v1/state`): the
# newest files it returns, and how many entries it will walk to find them.
MAX_STATE_FILES = 50
MAX_STATE_WALK = 20_000
# `/v1/state` processes: at most this many, newest first; each command cut.
# The tmux server's process name (`/proc/<pid>/stat`), exactly: tmux sets it.
TMUX_SERVER_COMM = "tmux: server"
MAX_STATE_PROCESSES = 20
PROCESS_COMMAND_CHARS = 80
# Directories the state read never walks into: tool caches and environments,
# not work anyone would name. Dot-directories (`.venv`, `.git`, `.cache`,
# `.tmp`) are skipped by their leading dot.
STATE_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
TMUX_SECONDS = 10.0
TMUX_SESSIONS_FORMAT = "#{session_name}\t#{session_created}\t#{session_attached}"
TMUX_PANES_FORMAT = (
    "#{session_name}\t#{pane_dead}\t#{pane_current_command}\t#{pane_pid}"
)
# A pane whose foreground command is one of these is waiting at a prompt:
# nothing is running in it.
IDLE_COMMANDS = frozenset({"bash", "sh", "dash", "zsh", "fish", "login"})
# What tmux prints when there is no server on the socket: nothing is running.
TMUX_NO_SERVER = ("no server running", "error connecting", "no such file or directory",
                  "connection refused")

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
    def __init__(
        self, *, root: Path, token: str, isolate_scopes: bool = True,
        tmux_root: Path | None = None, tmux_bin: str = "tmux",
        proc_root: Path = Path("/proc"),
    ) -> None:
        if not token:
            raise ValueError("computerd needs a non-empty token")
        self.root = root
        self._token = token
        self.owner = Owner(isolate=isolate_scopes)
        # The directory the API serves; the CLI's default exists in the image,
        # and a test root may not until its first write.
        self.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        # Where each scope's tmux server keeps its socket: `TMUX_TMPDIR` is
        # `<tmux_root>/<scope>` for every command and every tmux read, so a
        # room's `tmux new -d` and the state read find the same server, and
        # one room's server is never another's (the socket is also under the
        # scope user's own `tmux-<uid>`). Under `root` by default, which is
        # root-owned, so no command can plant it; a test passes a short path,
        # because a Unix socket path is capped near 104 bytes.
        self.tmux_root = tmux_root if tmux_root is not None else root / ".tmux"
        self.tmux_bin = tmux_bin
        self.proc_root = proc_root
        self.tmux_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        if stat.S_ISLNK(os.lstat(self.tmux_root).st_mode):
            raise ValueError(f"{self.tmux_root} is a link; refusing to start")
        # Traversable by every scope user (each one's own directory under it
        # is `0o700`), writable by computerd alone; set explicitly, because
        # `mkdir`'s mode is cut by the umask and an existing one keeps its own.
        os.chmod(self.tmux_root, 0o755)  # noqa: S103 - a directory, root-writable only
        self._restore_lock = root / RESTORE_LOCK
        try:
            fd = os.open(
                self._restore_lock,
                os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
        except OSError as exc:
            raise ValueError(
                f"{self._restore_lock}: cannot open the restore lock: {exc}",
            ) from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
                raise ValueError(
                    f"{self._restore_lock}: the restore lock must be a regular "
                    "file owned by computerd's user",
                )
        finally:
            os.close(fd)

    @contextlib.asynccontextmanager
    async def room_lease(self) -> AsyncIterator[None]:
        """Hold the shared half of the restore lease for one room operation.
        `nora-computer restore` holds the exclusive half while it swaps a room
        directory; `asyncio.to_thread` keeps the blocking flock off the loop."""
        fd = await asyncio.to_thread(self._lease)
        try:
            yield
        finally:
            await asyncio.to_thread(self._release, fd)

    def _lease(self) -> int:
        fd = os.open(self._restore_lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
        except OSError:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _release(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # -- request plumbing -------------------------------------------------

    def authorised(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {self._token}")

    @staticmethod
    def valid_scope(scope: Any) -> TypeGuard[str]:
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
        # one, and it is deliberately NOT applied here. The computer is
        # semi-trusted, so no per-command resource limit is enforced; the slot
        # exists so limits can be enabled with config plus one change here
        # (setrlimit in a preexec).
        await asyncio.to_thread(self._ensure_scope, scope)
        workdir = self.root / scope
        env = self._env(scope)
        user = self._user(scope)
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

    def _env(self, scope: str) -> dict[str, str]:
        """A command's environment: computerd's own minus its token. HOME is
        the room's own directory, so a profile or cache one room's command
        writes is not read by every other room's; TMUX_TMPDIR is the room's
        tmux directory (see `__init__`). SHELL is bash because a scope user's
        login shell is `nologin`, and tmux runs a pane's command through
        SHELL, falling back to that login shell: without it every `tmux new`
        exits at once and takes its server with it."""
        workdir = self.root / scope
        env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
        env.update(HOME=str(workdir), PWD=str(workdir), TMPDIR=str(workdir / ".tmp"),
                   TMUX_TMPDIR=str(self.tmux_root / scope), SHELL="/bin/bash")
        return env

    def _user(self, scope: str) -> dict[str, Any]:
        ids = self.owner.ids(scope)
        if ids is None:
            return {}
        return {"user": ids[0], "group": ids[1], "extra_groups": []}

    def _ensure_scope(self, scope: str) -> None:
        """The scope directory and its `.tmp`, both `0o700` and the scope
        user's. Re-asserted on every run, so a pre-existing directory is closed
        and handed over too. Files inside it keep their owner."""
        with self._dir(scope, [".tmp"], create=True) as tmp_fd:
            os.fchmod(tmp_fd, 0o700)
            # Handed over as well: a `.tmp` owned by another user would leave
            # TMPDIR unwritable for the room's user.
            self.owner.chown_fd(tmp_fd, scope)
        with self._dir(scope, [], create=True) as fd:
            os.fchmod(fd, 0o700)
            self.owner.chown_fd(fd, scope)
        # The room's tmux directory, walked like a scope directory: opened
        # from `tmux_root` without following a link, `0o700`, the scope
        # user's. `tmux_root` is root's, so no command can pre-create it.
        root_fd = os.open(self.tmux_root, _DIR_FLAGS)
        try:
            with contextlib.suppress(FileExistsError):
                os.mkdir(scope, 0o700, dir_fd=root_fd)
            fd = os.open(scope, _DIR_FLAGS, dir_fd=root_fd)
            try:
                os.fchmod(fd, 0o700)
                self.owner.chown_fd(fd, scope)
            finally:
                os.close(fd)
        finally:
            os.close(root_fd)

    # -- observed state ---------------------

    def recent_files(self, scope: str) -> dict[str, Any]:
        """The newest regular files under the scope, newest first, capped at
        `MAX_STATE_FILES`: path, size and mtime (epoch seconds). Links are
        listed never and followed never (`lstat`, `followlinks=False`);
        dot-files, dot-directories and `STATE_SKIP_DIRS` are not walked. At
        most `MAX_STATE_WALK` entries are looked at; `truncated` says the
        walk stopped there, so the list may miss a newer file."""
        base = self.root / scope
        try:
            if not stat.S_ISDIR(os.lstat(base).st_mode):
                return {"files": [], "files_truncated": False}
        except FileNotFoundError:
            return {"files": [], "files_truncated": False}
        newest: list[tuple[float, str, int]] = []
        walked, truncated = 0, False
        for current, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and d not in STATE_SKIP_DIRS
            )
            for name in names:
                if name.startswith("."):
                    continue
                walked += 1
                if walked > MAX_STATE_WALK:
                    truncated = True
                    break
                try:
                    info = os.lstat(Path(current) / name)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                rel = str((Path(current) / name).relative_to(base))
                item = (info.st_mtime, rel, info.st_size)
                if len(newest) < MAX_STATE_FILES:
                    heapq.heappush(newest, item)
                else:
                    heapq.heappushpop(newest, item)
            if truncated:
                break
        ordered = sorted(newest, reverse=True)
        return {
            "files": [{"path": p, "size": size, "mtime": mtime} for mtime, p, size in ordered],
            "files_truncated": truncated,
        }

    async def _tmux(self, scope: str, *args: str) -> tuple[int, str, str]:
        """`tmux ARGS` as the scope's own user, on the scope's own socket.
        Raises `FileNotFoundError` when there is no tmux binary."""
        proc = await asyncio.create_subprocess_exec(
            self.tmux_bin, *args, env=self._env(scope), cwd="/",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, **self._user(scope),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), TMUX_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            raise
        return (proc.returncode if proc.returncode is not None else -1,
                out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"))

    async def tmux_sessions(self, scope: str) -> dict[str, Any]:
        """The scope's tmux sessions: name, created (epoch seconds), attached,
        and `running`, which is true when any pane is alive with a
        foreground command that is not a shell at its prompt. `tmux` is None
        (with `tmux_reason`) when they could not be listed, which a reader
        must treat as unknown, never as none."""
        try:
            rc, out, err = await self._tmux(scope, "list-sessions", "-F", TMUX_SESSIONS_FORMAT)
            if rc != 0:
                if any(m in err.lower() for m in TMUX_NO_SERVER):
                    return {"tmux": []}
                return {"tmux": None, "tmux_reason": f"tmux exited {rc}: {err.strip()[:200]}"}
            prc, pout, perr = await self._tmux(scope, "list-panes", "-a", "-F", TMUX_PANES_FORMAT)
        except FileNotFoundError:
            return {"tmux": None, "tmux_reason": "tmux is not installed"}
        except (TimeoutError, OSError) as exc:
            return {"tmux": None, "tmux_reason": f"{type(exc).__name__}: {exc}"}
        if prc != 0:
            return {"tmux": None, "tmux_reason": f"tmux exited {prc}: {perr.strip()[:200]}"}
        running: set[str] = set()
        pane_pids: set[int] = set()
        parents = await asyncio.to_thread(child_parents, self.proc_root)
        for line in pout.splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                pane_pids.add(_int(parts[3], 0))
            if len(parts) == 4 and pane_running(
                dead=parts[1] == "1", command=parts[2], pid=_int(parts[3], 0), parents=parents,
            ):
                running.add(parts[0])
        sessions: list[dict[str, Any]] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            name, created, attached = parts
            sessions.append({"name": name, "created": _int(created, 0),
                             "attached": _int(attached, 0) > 0, "running": name in running})
        return {"tmux": sessions, "_pane_pids": frozenset(pane_pids)}

    def processes(self, scope: str, *, panes: frozenset[int] = frozenset()) -> dict[str, Any]:
        """Every process the scope's user owns, from `/proc`: a job started
        any way (`nohup … &` has no tmux session). Only when scopes are
        isolated: then that user is the scope's own. Not isolated (tests, a
        non-root computerd), every room's commands run as computerd's own
        user, so its processes are every room's, and computerd itself: they
        are not this room's to see, and `processes` is None. It is None too
        (with `processes_reason`) when `/proc` cannot be read: unknown,
        never none.

        tmux is judged by its own rule, never here: a
        tmux server and a pane's own process (`panes`, from `list-panes`) are
        left out, so an idle session is not running; a pane's CHILD is an
        ordinary process and counts, which is the pane-with-a-child rule."""
        ids = self.owner.ids(scope)
        if ids is None:
            return {"processes": None,
                    "processes_reason": "scopes are not isolated, so no process is one "
                                        "room's own"}
        uid = ids[0]
        try:
            found = scope_processes(self.proc_root, uid, time.time(), exclude=panes)
        except OSError as exc:
            return {"processes": None,
                    "processes_reason": f"{type(exc).__name__}: {exc}"[:200]}
        found.sort(key=lambda p: p["started"], reverse=True)
        return {"processes": found[:MAX_STATE_PROCESSES],
                "processes_truncated": len(found) > MAX_STATE_PROCESSES}

    async def state(self, scope: str, body: dict[str, Any]) -> dict[str, Any]:
        files = await asyncio.to_thread(self.recent_files, scope)
        tmux = await self.tmux_sessions(scope)
        panes = tmux.pop("_pane_pids", frozenset())
        procs = await asyncio.to_thread(self.processes, scope, panes=panes)
        return {"status": "ok", **files, **tmux, **procs}


def xauthority_entry(cookie: bytes) -> bytes:
    """One Xauthority record with an MIT-MAGIC-COOKIE-1, matching any host
    (FamilyWild) and any display (an empty number, which libXau's best-match
    accepts): Xvfb picks the display itself (`-displayfd`), after the file is
    written. The format is four length-prefixed fields after a 16-bit
    family, all big-endian (Xau's `XauWriteAuth`)."""
    def field(data: bytes) -> bytes:
        return len(data).to_bytes(2, "big") + data
    return (0xFFFF).to_bytes(2, "big") + field(b"") + field(b"") \
        + field(b"MIT-MAGIC-COOKIE-1") + field(cookie)


class Browser:
    """One room's Chrome: headed on its own Xvfb display, both running as the
    room's user, driven over `--remote-debugging-pipe`. There is no debugging
    TCP port, so no other room's command, and no command at all, can attach:
    the pipe's two file descriptors are held by computerd alone. The display
    needs the cookie in the room's own `browser/.Xauthority` (`0o600`),
    so another room cannot read the screen.

    Chrome speaks CDP on the pipe as NUL-terminated JSON: commands in on fd 3,
    answers and events out on fd 4. One websocket client at a time; the
    reader runs whether or not one is attached, so Chrome never blocks on a
    full pipe."""

    def __init__(self, scope: str, display: int) -> None:
        self.scope = scope
        self.display = display
        self.chrome: asyncio.subprocess.Process | None = None
        self.xvfb: asyncio.subprocess.Process | None = None
        self.client: web.WebSocketResponse | None = None
        self.last_used = time.monotonic()
        self.started = time.time()
        self._to_chrome: int | None = None
        self._reader: asyncio.Task[None] | None = None
        # One writer at a time: two frames written at once could interleave
        # on the pipe and break its NUL framing.
        self._write_lock = asyncio.Lock()
        # Only used before the browser is published to a websocket client.
        self._startup_id = 0
        self._startup_session: str | None = None
        self._startup_reply: asyncio.Future[dict[str, Any]] | None = None

    @property
    def alive(self) -> bool:
        return self.chrome is not None and self.chrome.returncode is None

    async def send(self, message: str) -> None:
        self.last_used = time.monotonic()
        if self._to_chrome is None:
            raise OSError("the browser's pipe is closed")
        data = message.encode("utf-8") + b"\0"
        async with self._write_lock:
            await asyncio.to_thread(_write_all, self._to_chrome, data)

    async def _read(self, fd: int) -> None:
        """Forward every message Chrome writes to the attached client, and
        drop it when there is none."""
        buffer = bytearray()
        try:
            while True:
                chunk = await asyncio.to_thread(os.read, fd, 1 << 16)
                if not chunk:
                    return
                scan = len(buffer)  # a NUL can only be in what just arrived
                buffer += chunk
                if len(buffer) > MAX_TRANSFER_BYTES:
                    # A room's command runs as Chrome's user and can write to
                    # this pipe itself; an unframed flood must not grow root's
                    # memory without end. Chrome is killed, and the reaper
                    # frees its slot.
                    if self.chrome is not None and self.chrome.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(self.chrome.pid, signal.SIGKILL)
                    return
                while (end := buffer.find(b"\0", scan)) != -1:
                    message = bytes(buffer[:end])
                    del buffer[:end + 1]
                    scan = 0
                    self.last_used = time.monotonic()
                    if self._startup_reply is not None:
                        try:
                            reply = json.loads(message)
                        except (ValueError, UnicodeDecodeError):
                            return
                        if not isinstance(reply, dict):
                            return
                        if (reply.get("id") == self._startup_id
                                and reply.get("sessionId") == self._startup_session):
                            if not self._startup_reply.done():
                                self._startup_reply.set_result(reply)
                            continue
                    client = self.client
                    if client is not None and not client.closed:
                        with contextlib.suppress(ConnectionResetError, RuntimeError):
                            await client.send_str(message.decode("utf-8", errors="replace"))
        finally:
            os.close(fd)
            if self._startup_reply is not None and not self._startup_reply.done():
                self._startup_reply.set_exception(OSError("Chrome closed its startup pipe"))

    async def _startup_command(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None,
    ) -> dict[str, Any]:
        """One private CDP request at a time, under the activation deadline."""
        if self._reader is None or self._reader.done():
            raise OSError("Chrome's startup pipe is not running")
        self._startup_id += 1
        self._startup_session = session
        self._startup_reply = asyncio.get_running_loop().create_future()
        request = {"id": self._startup_id, "method": method, "params": params or {}}
        if session is not None:
            request["sessionId"] = session
        try:
            await self.send(json.dumps(request))
            reply = await self._startup_reply
            if "error" in reply:
                raise OSError(f"Chrome TLS activation: {method} failed")
            return reply["result"]
        finally:
            self._startup_reply = None

    async def activate_tls(self) -> None:
        """Require this Chrome's live CRLSet enforcement before exposing CDP.

        A downloaded component (even the UI's current version) precedes its
        asynchronous verifier load. Only the affected TLS rejection counts.
        Probe bodies are never executed and no profile credentials are sent.
        """
        command = self._startup_command

        async def fetch(url: str) -> dict[str, Any]:
            # A fresh network context also discards pre-update TLS connections
            # and session resumption: disabling the HTTP cache alone cannot.
            context = (await command("Target.createBrowserContext"))["browserContextId"]
            target = (await command("Target.createTarget", {
                "url": "about:blank", "browserContextId": context,
            }))["targetId"]
            session = (await command("Target.attachToTarget", {
                "targetId": target, "flatten": True,
            }))["sessionId"]
            frame = (await command("Page.getFrameTree", session=session))["frameTree"]["frame"]["id"]
            resource = (await command("Network.loadNetworkResource", {
                "frameId": frame, "url": url,
                "options": {"disableCache": True, "includeCredentials": False},
            }, session))["resource"]
            if "stream" in resource:
                await command("IO.close", {"handle": resource["stream"]}, session)
            await command("Target.disposeBrowserContext", {"browserContextId": context})
            return resource

        component_target = None
        while True:
            negative = await fetch(CHROME_REVOKED_URL)
            if (negative.get("success") is False
                    and negative.get("netError") == -206
                    and negative.get("netErrorName") == "net::ERR_CERT_REVOKED"):
                break
            if negative.get("success") is not True or negative.get("httpStatusCode") != 200:
                raise OSError("Chrome TLS activation: revocation probe unavailable")
            if component_target is None:
                component_target = (await command("Target.createTarget", {
                    "url": "chrome://components/",
                }))["targetId"]
                component_session = (await command("Target.attachToTarget", {
                    "targetId": component_target, "flatten": True,
                }))["sessionId"]
                # Chrome authenticates and installs its own signed CRX. No
                # custom trust material, version threshold or fixed warmup.
                while True:
                    clicked = await command("Runtime.evaluate", {
                        "expression": "(() => { const b = document.querySelector("
                                      "'button.button-check-update#hfnkpimlhhgieaddgfemjhofmfblmnib');"
                                      " if (!b) return false; b.click(); return true; })()",
                        "returnByValue": True,
                    }, component_session)
                    if "exceptionDetails" in clicked:
                        raise OSError("Chrome TLS activation: component update failed")
                    if clicked["result"].get("value") is True:
                        break
                    await asyncio.sleep(0.1)
            await asyncio.sleep(1)

        positive = await fetch("https://pypi.org/")
        if (positive.get("success") is not True or positive.get("httpStatusCode") != 200
                or positive.get("netError", 0) != 0):
            raise OSError("Chrome TLS activation: positive TLS probe unavailable")
        if component_target is not None:
            closed = await command("Target.closeTarget", {"targetId": component_target})
            if closed.get("success") is not True:
                raise OSError("Chrome TLS activation: component tab did not close")

    async def stop(self) -> None:
        for proc in (self.chrome, self.xvfb):
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        if self._to_chrome is not None:
            with contextlib.suppress(OSError):
                os.close(self._to_chrome)
            self._to_chrome = None
        if self._reader is not None:
            self._reader.cancel()
        if self.client is not None and not self.client.closed:
            await self.client.close()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


# Browsers: at most this many rooms have one running at once (each Chrome with
# a few tabs is about 0.6-0.9 GiB in the 3 GiB VM);
# one idle this long, with no client attached, is stopped.
MAX_BROWSERS = 2
BROWSER_IDLE_SECONDS = 600.0
CHROME_START_SECONDS = 10.0
CHROME_TLS_SECONDS = 60.0
# ponytail: this incident's public canary is an availability dependency. Replace
# after a measured Chrome root-store fix; authority errors alone are not proof.
CHROME_REVOKED_URL = "https://dv-g3b-emudhra.we-have-a-problem.com/"
# X's own socket directory: a fixed path every X client looks in, so not a
# temp file; the image makes it, root's and sticky (Browsers.ensure_x11_dir).
X11_DIR = Path("/tmp/.X11-unix")  # noqa: S108
# Chrome's own flags: headed (no --headless), the pipe and never a port, a
# profile per room, no first-run UI. `--proxy-server` is added from the VM's
# `https_proxy` when there is one (sbx's proxy). Driven over the
# pipe, Chrome sets `navigator.webdriver` to true, the plainest bot signal
# there is; AutomationControlled off clears it.
CHROME_FLAGS = (
    "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
    "--password-store=basic", "--window-size=1280,800",
    "--disable-blink-features=AutomationControlled",
)


class Browsers:
    """Every room's browser, started on a room's first CDP connection."""

    def __init__(
        self, computer: Computer, *, chrome_bin: str = "google-chrome", xvfb_bin: str = "Xvfb",
        max_browsers: int = MAX_BROWSERS, idle_seconds: float = BROWSER_IDLE_SECONDS,
        x11_dir: Path = X11_DIR,
    ) -> None:
        self.computer = computer
        self.x11_dir = x11_dir
        self.chrome_bin = chrome_bin
        self.xvfb_bin = xvfb_bin
        self.max_browsers = max_browsers
        self.idle_seconds = idle_seconds
        self.running: dict[str, Browser] = {}
        self._lock = asyncio.Lock()

    async def reap(self) -> None:
        """Stop every browser that died, or sat idle past the limit with no
        client attached."""
        async with self._lock:
            await self._reap()

    async def _reap(self) -> None:
        now = time.monotonic()
        for scope, browser in list(self.running.items()):
            idle = browser.client is None and now - browser.last_used > self.idle_seconds
            if not browser.alive or idle:
                await browser.stop()
                self.running.pop(scope, None)

    async def get(self, scope: str) -> Browser:
        """The room's running browser, started if there is none. Raises
        `RefusedError` when every slot holds a browser with a client attached
        (nothing can be stopped for this one), `OSError` when it cannot start."""
        async with self._lock:
            await self._reap()
            browser = self.running.get(scope)
            if browser is not None:
                browser.last_used = time.monotonic()
                return browser
            if len(self.running) >= self.max_browsers:
                idle = [b for b in self.running.values() if b.client is None]
                if not idle:
                    raise RefusedError(
                        f"{self.max_browsers} rooms are browsing now; try again shortly")
                oldest = min(idle, key=lambda b: b.last_used)
                await oldest.stop()
                self.running.pop(oldest.scope, None)
            browser = await self._start(scope)
            self.running[scope] = browser
            return browser

    def ensure_x11_dir(self) -> None:
        """The X socket directory, `1777` and root's: Xvfb runs as a room's
        user and cannot make it. Nothing here follows a link: `mkdir` fails on
        an existing name, the directory is opened `O_NOFOLLOW` and changed
        through that descriptor. As root, one that is not root's own (made by
        a room's command before computerd) is refused, never adopted: a room
        could otherwise point it anywhere and have root open it to everyone.
        The image makes it (Dockerfile) and computerd checks it at start."""
        with contextlib.suppress(FileExistsError):
            os.mkdir(self.x11_dir, 0o700)
        fd = os.open(self.x11_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.geteuid() == 0:
                if os.fstat(fd).st_uid != 0:
                    raise OSError(f"{self.x11_dir} is not root's; refusing to use it")
                os.fchmod(fd, 0o1777)
        finally:
            os.close(fd)

    async def _read_display(self, fd: int, xvfb: asyncio.subprocess.Process) -> int:
        """The display number Xvfb wrote to `-displayfd` once it was listening.
        Xvfb picks it: it skips any number whose socket, lock or abstract
        name is already taken, and then holds all of them itself, so a room
        cannot predict the number, block it, or answer in its place. Raises
        `OSError` when none comes before the deadline."""
        os.set_blocking(fd, False)
        data = b""
        deadline = time.monotonic() + CHROME_START_SECONDS
        while time.monotonic() < deadline and b"\n" not in data:
            try:
                chunk = os.read(fd, 64)
            except BlockingIOError:
                if xvfb.returncode is not None:
                    break
                await asyncio.sleep(0.05)
                continue
            if not chunk:
                break
            data += chunk
        line = data.split(b"\n", 1)[0].strip()
        if not line.isdigit():
            raise OSError("Xvfb did not report a display")
        return int(line)

    def _display_is_ours(self, display: int, uid: int | None) -> bool:
        """The display's socket is a socket and the room user's (when rooms
        are isolated): what Chrome will connect to is our Xvfb's."""
        try:
            info = os.lstat(self.x11_dir / f"X{display}")
        except FileNotFoundError:
            return False
        return stat.S_ISSOCK(info.st_mode) and (uid is None or info.st_uid == uid)

    async def _start(self, scope: str) -> Browser:
        await asyncio.to_thread(self.computer._ensure_scope, scope)
        await asyncio.to_thread(self.ensure_x11_dir)
        cookie = secrets.token_bytes(16)
        auth = await asyncio.to_thread(self._write_xauthority, scope, cookie)
        profile = self.computer.root / scope / "browser" / "default"
        env = self.computer._env(scope)
        env.update(XAUTHORITY=str(auth))
        user = self.computer._user(scope)
        browser = Browser(scope, display=-1)
        display_r, display_w = os.pipe()
        try:
            browser.xvfb = await asyncio.create_subprocess_exec(
                self.xvfb_bin, "-displayfd", str(display_w), "-screen", "0", "1280x800x24",
                "-nolisten", "tcp", "-auth", str(auth), env=env, cwd="/",
                pass_fds=(display_w,), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True, **user,
            )
        finally:
            os.close(display_w)
        ids = self.computer.owner.ids(scope)
        opened: list[int] = [display_r]
        try:
            browser.display = await self._read_display(display_r, browser.xvfb)
            if not self._display_is_ours(browser.display, ids[0] if ids else None):
                raise OSError(f"the display :{browser.display} is not our Xvfb's")
            env.update(DISPLAY=f":{browser.display}")
            log_fd = await asyncio.to_thread(self._open_log, scope)
            opened.append(log_fd)
            # fd 3 is what Chrome reads (our commands), fd 4 what it writes.
            cmd_r, cmd_w = os.pipe()
            opened += [cmd_r, cmd_w]
            out_r, out_w = os.pipe()
            opened += [out_r, out_w]
            flags = [*CHROME_FLAGS, f"--user-data-dir={profile}"]
            proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
            if proxy:
                flags.append(f"--proxy-server={proxy}")
            # bash, not sh: the pipe's descriptors are usually above 9, and
            # dash (the VM's /bin/sh) only redirects single-digit ones, so it
            # exits 2 before Chrome starts.
            browser.chrome = await asyncio.create_subprocess_exec(
                "/bin/bash", "-c", f'exec "$0" "$@" 3<&{cmd_r} 4>&{out_w}',
                self.chrome_bin, *flags, "about:blank",
                env=env, cwd=str(self.computer.root / scope), pass_fds=(cmd_r, out_w),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=log_fd, start_new_session=True, **user,
            )
        except BaseException:
            for fd in opened:
                with contextlib.suppress(OSError):
                    os.close(fd)
            await browser.stop()
            raise
        # Ours to keep: the write end to Chrome and the read end from it.
        for fd in (display_r, log_fd, cmd_r, out_w):
            os.close(fd)
        browser._to_chrome = cmd_w
        browser._reader = asyncio.create_task(browser._read(out_r))
        try:
            async with asyncio.timeout(CHROME_TLS_SECONDS):
                await browser.activate_tls()
        except BaseException as exc:
            await browser.stop()
            if isinstance(exc, (TimeoutError, ValueError, KeyError, TypeError)):
                raise OSError("Chrome TLS activation did not complete") from exc
            raise
        return browser

    def _open_log(self, scope: str) -> int:
        """`<scope>/browser/chrome.log`, emptied at each start and the room
        user's: Chrome's stderr, so a browser that dies leaves a reason.
        A fresh file every time (`O_EXCL` under a temp name, then renamed over
        the old one), so whatever a room's command left at that name, a FIFO
        that would block the open or a link to another file, is replaced and
        never opened."""
        with self.computer._dir(scope, ["browser"], create=True) as dfd:
            tmp = f".chrome.log.{secrets.token_hex(6)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=dfd)
            try:
                self.computer.owner.chown_fd(fd, scope)
                os.replace(tmp, "chrome.log", src_dir_fd=dfd, dst_dir_fd=dfd)
            except BaseException:
                os.close(fd)
                with contextlib.suppress(OSError):
                    os.unlink(tmp, dir_fd=dfd)
                raise
        return fd

    def _write_xauthority(self, scope: str, cookie: bytes) -> Path:
        """The room's display cookie, in `<scope>/browser/.Xauthority`, `0o600`
        and the room user's, written through the no-follow walk."""
        with self.computer._dir(scope, ["browser", "default"], create=True):
            pass
        with self.computer._dir(scope, ["browser"], create=True) as dfd:
            tmp = f".Xauthority.{secrets.token_hex(6)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=dfd)
            try:
                os.write(fd, xauthority_entry(cookie))
                self.computer.owner.chown_fd(fd, scope)
            finally:
                os.close(fd)
            os.replace(tmp, ".Xauthority", src_dir_fd=dfd, dst_dir_fd=dfd)
        return self.computer.root / scope / "browser" / ".Xauthority"

    async def stop(self, scope: str) -> bool:
        async with self._lock:
            browser = self.running.pop(scope, None)
        if browser is None:
            return False
        await browser.stop()
        return True

    async def stop_all(self) -> None:
        for scope in list(self.running):
            await self.stop(scope)

    def status(self, scope: str) -> dict[str, Any]:
        browser = self.running.get(scope)
        if browser is None or not browser.alive:
            return {"browser": {"running": False}}
        return {"browser": {"running": True, "started": int(browser.started),
                            "attached": browser.client is not None}}


def _read_proc_file(pid_fd: int, name: str, limit: int = 65536) -> bytes:
    """One file under an opened `/proc/<pid>`, never through a link."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pid_fd)
    try:
        return os.read(fd, limit)
    finally:
        os.close(fd)


def scope_processes(
    proc_root: Path, uid: int, now: float, *, exclude: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Every process whose real uid is `uid`: pid, command (its argv, cut to
    `PROCESS_COMMAND_CHARS`), start (epoch seconds) and elapsed seconds.
    Left out: kernel threads (no argv; pid 2 and its children), and tmux's
    own processes, the server (named exactly `TMUX_SERVER_COMM`) and each pid
    in `exclude` (the panes), which tmux's pane rule judges instead. A user
    job whose name merely starts with `tmux` counts like any other. Nothing
    is followed through a link: each `/proc/<pid>` is opened as a directory
    with `O_NOFOLLOW` and its files read relative to it. A process that
    exits mid-read is skipped. Raises `OSError` when `proc_root` itself
    cannot be listed."""
    boot = _boot_time(proc_root)
    ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    out: list[dict[str, Any]] = []
    for entry in os.scandir(proc_root):
        if not entry.name.isdigit():
            continue
        try:
            pid_fd = os.open(entry.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            continue
        try:
            status = _read_proc_file(pid_fd, "status").decode("utf-8", "replace")
            real_uid = next((line.split()[1] for line in status.splitlines()
                             if line.startswith("Uid:") and len(line.split()) > 1), None)
            if real_uid is None or _int(real_uid, -1) != uid:
                continue
            raw = _read_proc_file(pid_fd, "stat").decode("utf-8", "replace")
            argv = _read_proc_file(pid_fd, "cmdline")
        except OSError:
            continue
        finally:
            os.close(pid_fd)
        pid = int(entry.name)
        # `pid (comm) state ppid ... starttime(22nd field)`, split after the
        # LAST `)`: fields[0] is state, [1] ppid, [19] starttime.
        fields = raw[raw.rfind(")") + 1:].split()
        ppid = _int(fields[1], -1) if len(fields) > 1 else -1
        if not argv or pid == 2 or ppid == 2:
            continue  # a kernel thread
        comm = raw[raw.find("(") + 1:raw.rfind(")")]
        if pid in exclude or comm == TMUX_SERVER_COMM:
            continue  # tmux's own: judged by the pane-with-a-child rule
        command = " ".join(a.decode("utf-8", "replace") for a in argv.split(b"\0") if a)
        start_ticks = _int(fields[19], 0) if len(fields) > 19 else 0
        started = boot + start_ticks / ticks if boot is not None else now
        out.append({"pid": pid, "command": command[:PROCESS_COMMAND_CHARS],
                    "started": int(started), "elapsed": max(0, int(now - started))})
    return out


def _boot_time(proc_root: Path) -> float | None:
    """`btime` from `/proc/stat`, the epoch second the machine booted."""
    try:
        with open(proc_root / "stat", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except OSError:
        return None
    return None


def child_parents(proc_root: Path) -> frozenset[int]:
    """Every pid that has at least one child, read from `/proc/<pid>/stat`
    (field 4 is the parent). Empty when there is no `/proc` (not Linux): a
    pane is then running only by its foreground command."""
    parents: set[int] = set()
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return frozenset()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            with open(os.path.join(entry.path, "stat"), encoding="utf-8",
                      errors="replace") as f:
                raw = f.read()
        except OSError:
            continue
        # `pid (comm) state ppid ...`; comm may hold spaces and parentheses,
        # so split after the LAST `)`.
        fields = raw[raw.rfind(")") + 1:].split()
        if len(fields) >= 2 and fields[1].isdigit():
            parents.add(int(fields[1]))
    return frozenset(parents)


def pane_running(*, dead: bool, command: str, pid: int, parents: frozenset[int]) -> bool:
    """A pane is running a job when it is alive and either its foreground
    command is not a shell at a prompt, or its process has ANY child. The
    second catches what the first cannot: `bash train.sh`, `bash -c 'a && b'`
    and a server backgrounded from the shell all show `bash` in front."""
    if dead:
        return False
    return command not in IDLE_COMMANDS or pid in parents


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


def create_app(
    *, root: Path, token: str, isolate_scopes: bool = True,
    tmux_root: Path | None = None, tmux_bin: str = "tmux", proc_root: Path = Path("/proc"),
    chrome_bin: str = "google-chrome", xvfb_bin: str = "Xvfb",
    x11_dir: Path = X11_DIR, max_browsers: int = MAX_BROWSERS,
    browser_idle_seconds: float = BROWSER_IDLE_SECONDS,
) -> web.Application:
    computer = Computer(root=root, token=token, isolate_scopes=isolate_scopes,
                        tmux_root=tmux_root, tmux_bin=tmux_bin, proc_root=proc_root)
    browsers = Browsers(computer, chrome_bin=chrome_bin, xvfb_bin=xvfb_bin, x11_dir=x11_dir,
                        max_browsers=max_browsers, idle_seconds=browser_idle_seconds)
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
        async with computer.room_lease():
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
                if op == "state":
                    return answer({**await computer.state(scope, body), **browsers.status(scope)})
                if op == "browser_stop":
                    stopped = await browsers.stop(scope)
                    return answer({"status": "ok" if stopped else "nothing_found"})
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

    async def browser_cdp(request: web.Request) -> web.StreamResponse:
        """The room's browser, as a CDP websocket: each text frame is one CDP
        message, relayed to and from Chrome's pipe. The room's Chrome starts on
        the first connection. Refusals happen before the upgrade, as JSON with
        the usual statuses; a bad token is 401."""
        if not computer.authorised(request):
            return web.json_response({"status": "unauthorised"}, status=401)
        scope = request.query.get("scope")
        if not computer.valid_scope(scope):
            return web.json_response({"status": "denied",
                                      "reason": "scope must be a lowercase hex key"})
        try:
            # Starting Chrome writes the room; it must not land under a
            # directory the installer is about to rename away.
            async with computer.room_lease():
                browser = await browsers.get(scope)
        except RefusedError as exc:
            return web.json_response({"status": "denied", "reason": str(exc)})
        except OSError as exc:
            return web.json_response({"status": "unavailable",
                                      "reason": f"the browser did not start: {exc}"})
        if browser.client is not None and not browser.client.closed:
            return web.json_response({"status": "denied",
                                      "reason": "this room's browser already has a client"})
        ws = web.WebSocketResponse(max_msg_size=MAX_TRANSFER_BYTES)
        # Claimed before the first await, so two connects at once cannot
        # both pass the check above.
        browser.client = ws
        try:
            await ws.prepare(request)
            async for msg in ws:
                if msg.type is not WSMsgType.TEXT:
                    await ws.close(code=1003, message=b"CDP messages are text frames")
                    break
                try:
                    await browser.send(msg.data)
                except OSError:
                    break
        finally:
            if browser.client is ws:
                browser.client = None
            browser.last_used = time.monotonic()
        return ws

    async def reap_idle(app: web.Application) -> AsyncIterator[None]:
        # The X directory exists and is root's before any room's command can
        # make its own (Browsers.ensure_x11_dir); a failure is logged, and
        # every browser start reports it as unavailable.
        try:
            await asyncio.to_thread(browsers.ensure_x11_dir)
        except OSError as exc:
            print(f"computerd: browsers unavailable: {exc}", flush=True)

        async def loop() -> None:
            while True:
                await asyncio.sleep(30)
                await browsers.reap()
        task = asyncio.create_task(loop())
        yield
        task.cancel()
        await browsers.stop_all()

    app = web.Application(client_max_size=MAX_TRANSFER_BYTES + 1024)
    app["browsers"] = browsers
    app.cleanup_ctx.append(reap_idle)
    app.router.add_get("/v1/health", health)
    app.router.add_get("/v1/browser", browser_cdp)
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
