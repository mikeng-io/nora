"""A SIGTERM stops nora gracefully and a session killed by it releases its claim.

**The question: does SIGTERM reach `App.run`?** It reaches the process (`uv
run`, the image's entrypoint, forwards it) and, with no handler, kills it on the
spot: `finally` never runs, so `App.shutdown` and `CognitionShutdown` are
unreachable and a session in flight leaves its wake open. `docker stop` sends
SIGTERM. These tests send a REAL SIGTERM to a REAL process (`sigterm_probe`:
`nora.__main__.run_until_stopped`, the real wake loop, evaluator and session
runner, over real Postgres; only the model is a fake) and read the claim back
from the database.

The control (`test_without_a_handler...`) is `main()` without the handler: same
process, same signal, and the wake is left open, the exit is the signal's, and
`shutdown` never ran.
"""

from __future__ import annotations

import asyncio
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from nora.domain.stimuli import is_restart_detail, unit_in_flight
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_WAKE_OUTCOME
from nora.infra.sql.stimuli import SqlStimulusStore
from tests.integration.sigterm_probe import POLICY, UNIT

ROOT = Path(__file__).resolve().parents[2]
BOOT_DEADLINE = 90.0          # importing nora takes seconds; a deadlock guard, not a measurement


class _Probe:
    """The probe process, its stdout lines on a queue (a pipe read can block)."""

    def __init__(self, url: str, *, mode: str, grace: float, legacy: bool = False) -> None:
        env = {**os.environ, "DATABASE_URL": url, "PROBE_MODE": mode,
               "PROBE_GRACE": str(grace), "PROBE_LEGACY": "1" if legacy else "0"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tests.integration.sigterm_probe"], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.seen: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line.strip())

    def wait_for(self, word: str, *, timeout: float = BOOT_DEADLINE) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    break
                continue
            self.seen.append(line)
            if line == word:
                return
        raise AssertionError(f"never printed {word!r}: {self._diagnosis()}")

    def _diagnosis(self) -> str:
        if self.proc.poll() is None:
            self.proc.kill()
        err = self.proc.stderr.read() if self.proc.stderr else ""
        return f"rc={self.proc.returncode} out={self.seen} err={err[-1500:]}"

    def stop_with(self, sig: signal.Signals) -> float:
        started = time.monotonic()
        self.proc.send_signal(sig)
        return started

    def exit_within(self, started: float, timeout: float) -> float:
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"still running {timeout}s after the signal: {self._diagnosis()}"
            ) from None
        elapsed = time.monotonic() - started
        while not self.lines.empty():
            self.seen.append(self.lines.get())
        return elapsed

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for pipe in (self.proc.stdout, self.proc.stderr):
            if pipe:
                pipe.close()


def _claim(url: str) -> dict[str, Any]:
    """The unit's claim, as the rows say: the outcome, and what the evaluator's own
    read makes of it (in flight, what is pending, the failure streak)."""
    async def read() -> dict[str, Any]:
        engine = create_sql_engine(url)
        try:
            async with engine.connect() as conn:
                row = (await conn.execute(sa.select(
                    COGNITION_WAKE_OUTCOME.c.outcome, COGNITION_WAKE_OUTCOME.c.cause,
                    COGNITION_WAKE_OUTCOME.c.detail,
                ))).first()
                wakes = (await conn.execute(
                    sa.text("select count(*) from cognition_wake"),
                )).scalar_one()
            now = int(time.time() * 1000)
            view = await SqlStimulusStore(engine).view(
                unit=UNIT, mode="live", now_ms=now, limit=10,
                stale_before_ms=now - int(POLICY.session_timeout_seconds * 1000),
            )
            from dataclasses import replace
            tracked = replace(view, tracked=True)
            return {
                "wakes": wakes, "outcome": None if row is None else row.outcome,
                "cause": None if row is None else row.cause,
                "detail": None if row is None else row.detail,
                "in_flight": unit_in_flight(tracked, POLICY, now),
                "pending": len(view.pending), "failures": view.failures,
            }
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_sigterm_cancels_a_running_session_inside_the_grace_and_releases_its_claim(pg_url):
    probe = _Probe(pg_url, mode="block", grace=1.5)
    try:
        probe.wait_for("READY")
        before = _claim(pg_url)
        # CONTROL: a session is running, its wake is open, and it holds its unit.
        assert (before["wakes"], before["outcome"], before["in_flight"]) == (1, None, True)

        started = probe.stop_with(signal.SIGTERM)
        elapsed = probe.exit_within(started, timeout=30)

        assert probe.proc.returncode == 0                       # a handled stop, not a kill
        assert "STOPPED" in probe.seen                          # `shutdown` ran, to the end
        assert 1.3 <= elapsed < 15, elapsed                     # waited its grace, then cancelled
        after = _claim(pg_url)
        assert after["outcome"] == "cancelled" and after["cause"] == "none"
        assert is_restart_detail(after["detail"])
        assert after["in_flight"] is False                      # the claim is RELEASED
        assert (after["pending"], after["failures"]) == (1, 0)  # its stimulus is back; no strike
    finally:
        probe.close()


def test_a_session_that_finishes_inside_the_grace_is_allowed_to(pg_url):
    probe = _Probe(pg_url, mode="finish", grace=20.0)
    try:
        probe.wait_for("READY")
        started = probe.stop_with(signal.SIGTERM)
        elapsed = probe.exit_within(started, timeout=30)
        assert probe.proc.returncode == 0 and "STOPPED" in probe.seen
        assert elapsed < 10, elapsed                            # it did not wait out the 20 s
        after = _claim(pg_url)
        assert after["outcome"] == "closed" and after["cause"] == "none"
        assert after["in_flight"] is False and after["failures"] == 0
    finally:
        probe.close()


def test_a_second_signal_ends_the_process_at_once(pg_url):
    probe = _Probe(pg_url, mode="block", grace=60.0)
    try:
        probe.wait_for("READY")
        probe.stop_with(signal.SIGTERM)
        time.sleep(0.5)
        started = probe.stop_with(signal.SIGTERM)
        elapsed = probe.exit_within(started, timeout=15)
        assert probe.proc.returncode == -signal.SIGTERM
        assert elapsed < 10, elapsed
        assert "STOPPED" not in probe.seen
    finally:
        probe.close()


def test_without_a_handler_sigterm_kills_the_process_and_leaves_the_wake_open(pg_url):
    """THE CONTROL: `main()` without `run_until_stopped`. Same process, same signal."""
    probe = _Probe(pg_url, mode="block", grace=1.5, legacy=True)
    try:
        probe.wait_for("READY")
        started = probe.stop_with(signal.SIGTERM)
        probe.exit_within(started, timeout=30)
        assert probe.proc.returncode == -signal.SIGTERM         # killed by the signal
        assert "STOPPED" not in probe.seen                      # `shutdown` never ran
        after = _claim(pg_url)
        assert after["outcome"] is None and after["in_flight"] is True   # held, with no owner
    finally:
        probe.close()
