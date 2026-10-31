"""Bound local pytest jobs and run timing contracts without competing workers."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

MAX_WORKERS = 2
# ponytail: one advisory per-user/host lease, including pure tests; no cross-host scheduler.
LOCK_PATH = Path.home() / ".cache/nora/pytest.lock"
_GENERATION_ENV = "_NORA_PYTEST_GENERATION"
_LEASE = pytest.StashKey[Any]()
TIMING_TESTS = (
    "tests/integration/test_latency.py",
    "tests/unit/test_engagement_pipeline.py",
    "tests/integration/test_bridge_queue_telemetry.py",
)


@contextmanager
def host_lease(path: Path, generation: str | None = None) -> Iterator[str]:
    """Admit one job; its controllers/workers retain shared leases until exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as stream:
        admission = None
        try:
            try:
                if generation is None:
                    admission = os.open(
                        path.with_suffix(".admission"),
                        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
                    )
                    fcntl.flock(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, (fcntl.LOCK_SH if generation else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Nora test host busy; wait for the current job to finish"
                ) from exc
            if generation is None:
                generation = uuid.uuid4().hex
                stream.seek(0)
                stream.truncate()
                stream.write(generation)
                stream.flush()
                # flock conversion need not be atomic; admission excludes other controllers.
                fcntl.flock(fd, fcntl.LOCK_SH)
            elif stream.read() != generation:
                raise RuntimeError("Nora test lease changed; refusing a stale worker/job")
        finally:
            if admission is not None:
                os.close(admission)
        yield generation


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    worker = getattr(config, "workerinput", None)
    if worker is None:
        tx = getattr(config.option, "tx", []) or []
        if len(tx) > MAX_WORKERS or any(spec != "popen" for spec in tx):
            raise pytest.UsageError(f"Nora test jobs allow at most {MAX_WORKERS} local workers")
        ignored = [Path(path).resolve() for path in (config.option.ignore or [])]
        _, serial = partition(config.args)
        if tx and any(
            not any(file == path or path in file.parents for path in ignored)
            for file in (Path(target.split("::", 1)[0]).resolve() for target in serial)
        ):
            raise pytest.UsageError(
                "Timing contracts require a serial phase; use python -m scripts.pytest_gate"
            )
        generation = os.environ.pop(_GENERATION_ENV, None)
    else:
        generation = worker["nora_test_generation"]
    lease = host_lease(LOCK_PATH, generation)
    try:
        token = lease.__enter__()
    except RuntimeError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.stash[_LEASE] = lease, token


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    node.workerinput["nora_test_generation"] = node.config.stash[_LEASE][1]


def pytest_unconfigure(config: pytest.Config) -> None:
    held = config.stash.get(_LEASE, None)
    if held is not None:
        held[0].__exit__(None, None, None)


def partition(targets: Sequence[str]) -> tuple[list[str], list[str]]:
    """Keep selectors intact, moving only the selected timing files/nodes to serial."""
    serial: list[str] = []
    parallel: list[str] = []
    timing = {Path(path).resolve(): path for path in TIMING_TESTS}
    for target in targets:
        path = Path(target.split("::", 1)[0]).resolve()
        if path in timing:
            serial.append(target)
        else:
            parallel.append(target)
            if path.is_dir():
                serial.extend(
                    name for file, name in timing.items() if path in file.parents and file.is_file()
                )
    if not targets:
        serial.extend(TIMING_TESTS)
    return parallel, list(dict.fromkeys(serial))


def phases(targets: Sequence[str], workers: int) -> list[list[str]]:
    parallel, serial = partition(targets)
    commands = []
    if parallel or not targets:
        commands.append([
            sys.executable, "-m", "pytest", "-q", "-n", str(workers), "--dist", "loadfile",
            *(f"--ignore={path}" for path in TIMING_TESTS), *parallel,
        ])
    if serial:
        commands.append([sys.executable, "-m", "pytest", "-q", *serial])
    return commands


def _run(command: list[str], env: dict[str, str]) -> int:
    with subprocess.Popen(command, env=env) as process:  # noqa: S603 — fixed Python/pytest argv
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return 130


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(MAX_WORKERS + 1), default=MAX_WORKERS)
    parser.add_argument("targets", nargs="*")
    args = parser.parse_args(argv)
    generation = os.environ.pop(_GENERATION_ENV, None)
    try:
        with host_lease(LOCK_PATH, generation) as token:
            env = {**os.environ, _GENERATION_ENV: token}
            commands = phases(args.targets, args.workers)
            for command in commands:
                print("test phase:", " ".join(command[3:]), flush=True)
                rc = _run(command, env)
                if rc != 0 and not (rc == 5 and len(commands) > 1 and command is commands[0]):
                    return rc if rc > 0 else 128 - rc
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
