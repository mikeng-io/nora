"""Send real signals to the production serving wrapper in a subprocess."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEADLOCK_GUARD = 90.0


class _Probe:
    def __init__(self, *, block_shutdown: bool, without_handler: bool) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tests.integration.sigterm_probe"],
            cwd=ROOT,
            env={
                **os.environ,
                "PROBE_BLOCK_SHUTDOWN": str(int(block_shutdown)),
                "PROBE_WITHOUT_HANDLER": str(int(without_handler)),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.seen: list[str] = []
        self.pump = threading.Thread(target=self._pump, daemon=True)
        self.pump.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line.strip())
        self.lines.put(None)

    def wait_for(self, marker: str) -> None:
        while True:
            line = self.lines.get(timeout=DEADLOCK_GUARD)
            assert line is not None, f"process exited before {marker}: {self.seen}"
            self.seen.append(line)
            if line == marker:
                return

    def term(self) -> None:
        self.proc.send_signal(signal.SIGTERM)

    def wait(self) -> int:
        status = self.proc.wait(timeout=DEADLOCK_GUARD)
        self.pump.join(timeout=DEADLOCK_GUARD)
        assert not self.pump.is_alive()
        while not self.lines.empty():
            line = self.lines.get_nowait()
            if line is not None:
                self.seen.append(line)
        return status

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.wait()
        assert self.proc.stdout is not None
        self.proc.stdout.close()


@pytest.fixture
def start_probe():
    probes: list[_Probe] = []

    def start(*, block_shutdown: bool = False, without_handler: bool = False) -> _Probe:
        probe = _Probe(block_shutdown=block_shutdown, without_handler=without_handler)
        probes.append(probe)
        probe.wait_for("READY")
        return probe

    yield start
    for probe in probes:
        probe.close()


def test_sigterm_stops_serving_and_runs_shutdown(start_probe):
    probe = start_probe()
    probe.term()
    assert probe.wait() == 0, probe.seen
    assert probe.seen.index("SERVING_STOPPED") < probe.seen.index("STOPPED")


def test_a_second_sigterm_ends_the_process_during_shutdown(start_probe):
    probe = start_probe(block_shutdown=True)
    probe.term()
    probe.wait_for("DRAINING")
    probe.term()
    assert probe.wait() == -signal.SIGTERM, probe.seen
    assert "SERVING_STOPPED" in probe.seen and "STOPPED" not in probe.seen


def test_without_the_handler_sigterm_ends_the_process_before_shutdown(start_probe):
    probe = start_probe(without_handler=True)
    probe.term()
    assert probe.wait() == -signal.SIGTERM, probe.seen
    assert "SERVING_STOPPED" not in probe.seen and "DRAINING" not in probe.seen
