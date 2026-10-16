"""Nora's computer as a metric (DEC-261).

What these pin is the three things an alert on the computer series needs to be
true:

- `nora_computer_up` is 1 only when computerd ANSWERED, and 0 for a refused
  connection AND for a 401 (a token rotation that did not reach the container:
  `/v1/health` stays green while every real call fails, which is the outage);
- a call that computerd answered `unavailable` is counted, but does not flip
  `up`: computerd said something, it is there;
- with the computer off there is no `nora_computer_up` sample at all, because
  an unlabelled or pre-set gauge reads 0, and 0 is "down".

Real computerd, in process, with a real `Metrics`: a fake that answered every
call the same way would pass these however the wiring was broken.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.app.computer_metrics import COMPUTER_TARGET, ComputerMetrics
from nora.domain.computer import COMPUTER_OPS, SandboxStatus, computer_scope
from nora.infra.computer.client import ComputerdRuntime
from nora.infra.computer.probe import PROBE_SCOPE, ComputerdProbe
from nora.obs.metrics import Metrics
from tests.contract.test_sandbox_runtime_contract import TOKEN, load_computerd

SCOPE = computer_scope("signal", "room-a")
DEAD = "http://127.0.0.1:9"

TOOLS = frozenset({"computer_run", "computer_read"})


def _up(metrics: Metrics) -> float | None:
    """`nora_computer_up`'s value, or None when there is no sample (absent)."""
    samples = [v for (name, labels), v in metrics.snapshot().items()
               if name == "nora_computer_up" and ("target", COMPUTER_TARGET) in labels]
    return samples[0] if samples else None


def _observed_ops(metrics: Metrics) -> set[str]:
    return {dict(labels)["op"] for (name, labels), v in metrics.snapshot().items()
            if name == "nora_computer_calls_total" and v > 0}


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def observer(metrics: Metrics) -> ComputerMetrics:
    return ComputerMetrics(metrics, audit_tools=TOOLS)


@pytest.fixture
async def served(tmp_path: Path) -> AsyncIterator[tuple[TestServer, Path]]:
    server = TestServer(load_computerd().create_app(root=tmp_path, token=TOKEN))
    await server.start_server()
    try:
        yield server, tmp_path
    finally:
        await server.close()


async def _stub(handler) -> TestServer:
    app = web.Application()
    app.router.add_post("/v1/{op}", handler)
    server = TestServer(app)
    await server.start_server()
    return server


# -- the runtime's own report ----------------------------------------------


async def test_an_answered_call_counts_by_op_and_status_and_sets_up_to_one(
    served, metrics, observer,
):
    server, _ = served
    runtime = ComputerdRuntime(
        base_url=str(server.make_url("")), token=TOKEN, observer=observer,
    )
    listed = await runtime.files(scope=SCOPE)
    assert listed.status is SandboxStatus.NOTHING_FOUND
    assert metrics.get("nora_computer_calls_total", op="files", status="nothing_found") == 1
    assert _up(metrics) == 1.0


async def test_every_port_method_is_counted_under_an_op_the_series_were_created_for(
    served, metrics, observer,
):
    """`COMPUTER_OPS` is what `prime()` creates at zero, so an op the client
    records that is not listed there is a lazily-created series, the trap the
    zero baseline exists to avoid. Drive every port method, read the ops back."""
    server, _ = served
    runtime = ComputerdRuntime(
        base_url=str(server.make_url("")), token=TOKEN, observer=observer,
    )
    await runtime.run(scope=SCOPE, command="true", timeout_s=5)
    await runtime.write_text(scope=SCOPE, path="a.txt", content="x")
    await runtime.read(scope=SCOPE, path="a.txt")
    await runtime.edit(scope=SCOPE, path="a.txt", old="x", new="y")
    await runtime.files(scope=SCOPE)
    await runtime.fetch(scope=SCOPE, path="a.txt", max_bytes=100)
    await runtime.put(scope=SCOPE, path="b.bin", data=b"z")
    await runtime.state(scope=SCOPE)
    assert _observed_ops(metrics) == set(COMPUTER_OPS)


async def test_a_wrong_token_is_counted_unavailable_and_up_goes_to_zero(
    served, metrics, observer,
):
    """The post-rotation outage. computerd is reachable and `/v1/health` is
    green; every call is a 401. From Nora's side the computer is down."""
    server, _ = served
    wrong = ComputerdRuntime(
        base_url=str(server.make_url("")), token=TOKEN + "-wrong", observer=observer,
    )
    result = await wrong.write_text(scope=SCOPE, path="a.txt", content="x")
    assert result.status is SandboxStatus.UNAVAILABLE
    assert metrics.get("nora_computer_calls_total", op="write", status="unavailable") == 1
    assert _up(metrics) == 0.0


async def test_an_unreachable_computer_is_counted_and_up_is_zero(metrics, observer):
    runtime = ComputerdRuntime(
        base_url=DEAD, token=TOKEN, timeout_seconds=2, observer=observer,
    )
    result = await runtime.run(scope=SCOPE, command="true", timeout_s=1)
    assert result.status is SandboxStatus.UNAVAILABLE
    assert metrics.get("nora_computer_calls_total", op="run", status="unavailable") == 1
    assert _up(metrics) == 0.0


async def test_computerd_saying_unavailable_is_counted_but_does_not_take_up_to_zero(
    metrics, observer,
):
    """computerd ANSWERED, with a body that says it could not do the thing (an
    OS error). That is a status to count, and it is not an outage: the process
    is there and Nora can reach it, so `up` stays 1. Reading it as down would
    send the operator to restart a unit that is fine."""
    async def handler(_: web.Request) -> web.Response:
        return web.json_response({"status": "unavailable", "reason": "OSError: disk"})

    server = await _stub(handler)
    try:
        runtime = ComputerdRuntime(
            base_url=str(server.make_url("")), token=TOKEN, observer=observer,
        )
        result = await runtime.read(scope=SCOPE, path="a.txt")
    finally:
        await server.close()
    assert result.status is SandboxStatus.UNAVAILABLE
    assert metrics.get("nora_computer_calls_total", op="read", status="unavailable") == 1
    assert _up(metrics) == 1.0


async def test_a_server_error_is_not_an_answer(metrics, observer):
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=502, text="bad gateway")

    server = await _stub(handler)
    try:
        runtime = ComputerdRuntime(
            base_url=str(server.make_url("")), token=TOKEN, observer=observer,
        )
        await runtime.files(scope=SCOPE)
    finally:
        await server.close()
    assert _up(metrics) == 0.0


async def test_a_broken_observer_never_breaks_a_call(served):
    class _Boom:
        def call(self, op: str, status: str, answered: bool) -> None:
            raise RuntimeError("the metrics layer is down")

        def probe(self, answered: bool) -> None:
            raise RuntimeError("the metrics layer is down")

    server, _ = served
    runtime = ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN, observer=_Boom())
    result = await runtime.files(scope=SCOPE)
    assert result.status is SandboxStatus.NOTHING_FOUND


# -- the timed probe --------------------------------------------------------


async def test_the_probe_sets_up_to_one_against_a_healthy_computerd_and_counts_no_call(
    served, metrics, observer,
):
    server, root = served
    before = sorted(p.name for p in root.iterdir())   # computerd's own `.tmux`
    probe = ComputerdProbe(
        base_url=str(server.make_url("")), token=TOKEN, observer=observer,
    )
    assert await probe.probe_once() is True
    assert _up(metrics) == 1.0
    # Not a computer call: the calls series is Nora's use of her computer.
    assert metrics.get("nora_computer_calls_total") == 0
    # Read-only: it created nothing, not the scope it asked about.
    assert sorted(p.name for p in root.iterdir()) == before
    assert not (root / PROBE_SCOPE).exists()


async def test_the_probe_reads_a_wrong_token_as_down_though_health_is_green(
    served, metrics, observer,
):
    """The reason the probe is authenticated: `/v1/health` answers without a
    token, so a probe of it would say up through exactly this failure."""
    server, _ = served
    probe = ComputerdProbe(
        base_url=str(server.make_url("")), token=TOKEN + "-wrong", observer=observer,
    )
    assert await probe.probe_once() is False
    assert _up(metrics) == 0.0


async def test_the_probe_reads_an_unreachable_computerd_as_down(metrics, observer):
    probe = ComputerdProbe(base_url=DEAD, token=TOKEN, observer=observer, timeout_seconds=2)
    assert await probe.probe_once() is False
    assert _up(metrics) == 0.0


@pytest.mark.parametrize(
    "body",
    ["<html>welcome</html>", '{"service": "something-else"}', '{"status": "unauthorised"}', "[]"],
)
async def test_the_probe_reads_a_200_that_is_not_computerds_answer_as_down(
    metrics, observer, body,
):
    """A captive page or a proxy answering 200 with something else. The JSON
    bodies are the ones a parse alone does not reject: only a status in the
    port's own words makes it an answer."""
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text=body)

    server = await _stub(handler)
    try:
        probe = ComputerdProbe(
            base_url=str(server.make_url("")), token=TOKEN, observer=observer,
        )
        assert await probe.probe_once() is False
    finally:
        await server.close()
    assert _up(metrics) == 0.0


# -- not applicable, and the zero baseline ----------------------------------


def test_with_the_computer_off_there_is_no_up_sample_so_it_cannot_read_as_down(metrics):
    """`[computer] enabled = false`: nothing builds the observer, nothing sets
    the gauge. The declared series must have no SAMPLE (a HELP line is not one),
    or `nora_computer_up == 0` would fire on a deployment with no computer."""
    sample_lines = [
        line for line in metrics.render().splitlines()
        if line.startswith("nora_computer_up") or line.startswith("nora_computer_calls_total")
    ]
    assert sample_lines == []
    # And the series is declared, so a positive control: render() DOES carry it
    # once a sample exists.
    ComputerMetrics(metrics, audit_tools=TOOLS).probe(True)
    assert any(line.startswith("nora_computer_up{") for line in metrics.render().splitlines())


def test_prime_creates_every_op_and_status_at_zero_and_leaves_up_absent(metrics, observer):
    observer.prime()
    snap = metrics.snapshot()
    for op in COMPUTER_OPS:
        for status in SandboxStatus:
            key = ("nora_computer_calls_total", (("op", op), ("status", status.value)))
            assert snap.get(key) == 0.0, key
    # Up has no value until something measured it: a 0 here would be a lie.
    assert _up(metrics) is None


def test_prime_creates_the_audit_failure_series_at_zero_for_every_tool_and_stage(
    metrics, observer,
):
    observer.prime()
    snap = metrics.snapshot()
    from nora.domain.computer import COMPUTER_AUDIT_STAGES

    for tool in (*sorted(TOOLS), "computer_session"):
        for stage in COMPUTER_AUDIT_STAGES:
            key = ("nora_computer_audit_failures_total", (("stage", stage), ("tool", tool)))
            assert snap.get(key) == 0.0, key


def test_the_first_unavailable_after_a_restart_has_a_zero_before_it(metrics, observer):
    """The lazy-label trap, stated as the property the alert needs: the sample
    BEFORE the first increment exists, so `increase()` sees 0 to 1."""
    observer.prime()
    before = metrics.snapshot()[
        ("nora_computer_calls_total", (("op", "run"), ("status", "unavailable")))
    ]
    observer.call("run", "unavailable", False)
    after = metrics.snapshot()[
        ("nora_computer_calls_total", (("op", "run"), ("status", "unavailable")))
    ]
    assert (before, after) == (0.0, 1.0)
