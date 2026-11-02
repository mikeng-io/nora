"""Small per-run cost receipts; these never attest that a gate may be reused."""

from __future__ import annotations

import ctypes
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_COST = pytest.StashKey[dict[str, Any]]()
_FIXTURES = frozenset({
    "_pg_container", "pg_template", "pg_module_url", "pg_url", "clean_pg_url",
    "record_pg_url", "empty_pg_url",
})


def cost(config: pytest.Config) -> dict[str, Any]:
    value = config.stash.get(_COST, None)
    if value is None:
        value = {"fixture_setups": {}, "servers": [], "selected_nodes": [], "outcomes": {},
                 "started_at": datetime.now(UTC).isoformat()}
        config.stash[_COST] = value
    return value


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    outcome = yield
    if outcome.excinfo is not None or fixturedef.argname not in _FIXTURES:
        return
    counts = cost(request.config)["fixture_setups"]
    name = fixturedef.argname
    counts[name] = counts.get(name, 0) + 1
    if name != "_pg_container":
        return
    pg = outcome.get_result()

    def capture() -> None:
        import psycopg

        row: dict[str, Any] = {"container_id": pg.get_wrapped_container().id,
                               "status": "unavailable"}
        try:
            with psycopg.connect(
                host=pg.get_container_host_ip(), port=int(pg.get_exposed_port(5432)),
                user=pg.username, password=pg.password, dbname=pg.dbname,
                connect_timeout=2, options="-c statement_timeout=2000",
            ) as conn:
                wal, checkpoints = conn.execute(
                    "SELECT (SELECT wal_bytes FROM pg_stat_wal), "
                    "(SELECT num_requested FROM pg_stat_checkpointer)"
                ).fetchone()
            stats = pg.get_wrapped_container().stats(stream=False, one_shot=True)
            entries = stats.get("blkio_stats", {}).get("io_service_bytes_recursive")
            if entries is None:
                raise ValueError("Docker block counters unavailable")
            writes = [e["value"] for e in entries if e["op"].lower() == "write"]
            if not writes:
                raise ValueError("Docker write counter unavailable")
            row.update(
                status="ok", wal_bytes=int(wal), requested_checkpoints=checkpoints,
                block_write_bytes=sum(writes),
            )
        except (psycopg.Error, OSError, ValueError, KeyError, TypeError) as exc:
            row["error"] = type(exc).__name__
        cost(request.config)["servers"].append(row)

    # Registered after generator teardown: observe the server BEFORE it stops.
    request.addfinalizer(capture)


def pytest_collection_finish(session: pytest.Session) -> None:
    if not session.config.option.collectonly:
        cost(session.config)["selected_nodes"] = [item.nodeid for item in session.items]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.when == "call" or report.failed or report.skipped:
        outcomes = cost(item.config)["outcomes"]
        if outcomes.get(report.nodeid) != "failed":
            outcomes[report.nodeid] = "xfailed" if hasattr(report, "wasxfail") else report.outcome


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    remote = node.workeroutput.get("nora_cost")
    if remote is None:
        cost(node.config)["servers"].append({"status": "unavailable", "error": "worker_lost"})
        return
    local = cost(node.config)
    for name, count in remote["fixture_setups"].items():
        local["fixture_setups"][name] = local["fixture_setups"].get(name, 0) + count
    local["servers"].extend(remote["servers"])
    local["outcomes"].update(remote["outcomes"])
    # xdist workers collect the same population; count that population once.
    local["selected_nodes"] = sorted(set(local["selected_nodes"]) | set(remote["selected_nodes"]))


def finish(config: pytest.Config, exitstatus: int, generation: str) -> Path | None:
    if config.option.collectonly:
        return None
    value = cost(config)
    if hasattr(config, "workerinput"):
        config.workeroutput["nora_cost"] = value
        return None
    value.update(rc=int(exitstatus), ended_at=datetime.now(UTC).isoformat())
    resolved = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],  # noqa: S607
        cwd=config.rootpath, capture_output=True, text=True, check=False,
    )
    if resolved.returncode:
        return None  # Generated non-repository pytest projects have no git-local receipt home.
    git_dir = Path(resolved.stdout.strip())
    nodes = value.pop("selected_nodes")
    value["selected_count"] = len(nodes)
    value["selected_sha256"] = hashlib.sha256("\n".join(sorted(nodes)).encode()).hexdigest()
    outcomes = value.pop("outcomes")
    value["outcome_counts"] = {name: list(outcomes.values()).count(name)
                               for name in sorted(set(outcomes.values()))}
    path = git_dir / f"nora-test-cost-{generation}.json"
    document = json.loads(path.read_text()) if path.exists() else {
        "scope": "PG pre-teardown counters include startup; VM includes other daemon activity",
        "fixture_scope": "Named shared fixtures only; custom database owners are not clone counts",
        "phases": [], "vm_writes": {"status": "unavailable"},
    }
    document["phases"].append(value)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2))
    temporary.replace(path)
    return path


def vm_snapshot() -> dict[str, Any]:
    """Read Docker's macOS VM process, not SSD lifetime or physical NAND bytes."""
    if sys.platform != "darwin":
        return {"status": "unavailable", "reason": "not_macos"}
    try:
        rows = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True)  # noqa: S607
        candidates = []
        for line in rows.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and "Virtualization.VirtualMachine" in parts[1]:
                pid = int(parts[0])
                # Fixed tool; pid is an integer discovered above, never a shell command.
                files = subprocess.check_output(  # noqa: S603
                    ["lsof", "-p", str(pid), "-Fn"], text=True,  # noqa: S607
                )
                if any("com.docker.docker/" in f and f.endswith("/Docker.raw")
                       for f in files.splitlines()):
                    candidates.append(pid)
        if len(candidates) != 1:
            raise ValueError("Docker VM identity unavailable")

        class Usage(ctypes.Structure):
            _fields_ = [("uuid", ctypes.c_uint8 * 16), ("prefix", ctypes.c_uint64 * 17),
                        ("written", ctypes.c_uint64)]

        value = Usage()
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        if lib.proc_pid_rusage(candidates[0], 2, ctypes.byref(value)):
            raise OSError(ctypes.get_errno())
        return {"status": "ok", "pid": candidates[0], "bytes": value.written}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}


def attach_vm(repo: Path, generation: str, before: dict[str, Any]) -> None:
    after = vm_snapshot()
    resolved = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if resolved.returncode:
        return
    git_dir = Path(resolved.stdout.strip())
    path = git_dir / f"nora-test-cost-{generation}.json"
    if not path.exists():
        return  # No completed pytest phase; no measured cost receipt.
    document = json.loads(path.read_text())
    result: dict[str, Any] = {"status": "unavailable", "before": before, "after": after}
    if (before.get("status") == after.get("status") == "ok"
            and before["pid"] == after["pid"] and after["bytes"] >= before["bytes"]):
        result.update(status="ok", bytes=after["bytes"] - before["bytes"])
    document["vm_writes"] = result
    path.write_text(json.dumps(document, indent=2))
    print(f"test cost receipt: {path}", flush=True)
