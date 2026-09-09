"""`deploy/alert-bridge/bridge.py` — the last hop, and the one that decides
whether any of the monitoring stack is real.

`deploy/watchdog.sh` has existed since August with an alert channel of
`echo >&2` plus a non-zero exit; even scheduled, it would have produced a failed
systemd unit, which is exactly what nobody noticed for sixteen days. So this
file's subject is not "does it format nicely" but "does a failure anywhere on
this path stay visible instead of turning into silence".

Imported by PATH rather than as a package: the bridge is a deployment artifact
with no dependencies on `nora`, deliberately (see its docstring), and importing
it as one would create the coupling it exists to avoid.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parents[2] / "deploy" / "alert-bridge" / "bridge.py"


@pytest.fixture
def bridge():
    spec = importlib.util.spec_from_file_location("alert_bridge", _BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(*alerts: dict, status: str = "firing") -> dict:
    return {"status": status, "alerts": list(alerts)}


def _alert(name: str, **annotations: str) -> dict:
    return {
        "labels": {"alertname": name, "severity": "critical", "job": "nora"},
        "annotations": annotations,
    }


def test_the_message_leads_with_firing_or_resolved(bridge):
    """On a phone screen the only thing that matters first is which of the two
    this is. A message that buries it is one the operator has to read twice at
    3am."""
    text = bridge.render(_payload(_alert("TargetDown", summary="nora is down")))
    assert text.startswith("[FIRING]")
    resolved = bridge.render(
        _payload(_alert("TargetDown", summary="nora is down"), status="resolved"),
    )
    assert resolved.startswith("[RESOLVED]")


def test_it_carries_the_alert_name_and_summary(bridge):
    text = bridge.render(_payload(_alert("HeartbeatStalled", summary="no tick for 6m")))
    assert "HeartbeatStalled" in text
    assert "no tick for 6m" in text


def test_a_description_identical_to_the_summary_is_not_repeated(bridge):
    """Half the rules set both and some set them the same. Printing it twice
    makes every message look padded, which is how people stop reading them."""
    text = bridge.render(
        _payload(_alert("X", summary="the same words", description="the same words")),
    )
    assert text.count("the same words") == 1


def test_a_long_burst_is_truncated_and_says_so(bridge):
    """A silently clipped list is a message that lies about how bad it is."""
    alerts = [_alert(f"Alert{i}", summary=f"s{i}") for i in range(25)]
    text = bridge.render(_payload(*alerts))
    assert "… and 15 more" in text
    assert "Alert0" in text and "Alert24" not in text


def test_no_recipient_is_reported_as_a_failure_not_a_success(bridge, monkeypatch, caplog):
    """An unset recipient must not read as delivered. This is the difference
    between "you have alerting" and "you believe you have alerting", which is
    the state this whole branch exists to end."""
    monkeypatch.setattr(bridge, "RECIPIENT", "")
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    assert any("RECIPIENT" in r.message for r in caplog.records)


def test_an_unreachable_signal_cli_returns_false_and_never_raises(bridge, monkeypatch):
    """A bridge that dies on a bad response stops delivering every LATER alert
    too — the failure would spread from one notification to all of them."""
    monkeypatch.setattr(bridge, "RECIPIENT", "+85200000000")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(bridge.urllib.request, "urlopen", boom)
    assert bridge.send("anything") is False


def test_a_jsonrpc_error_object_is_not_a_delivery(bridge, monkeypatch):
    """signal-cli answers 200 with an `error` member when it refuses a send —
    an unregistered recipient, a rate limit. Reading only the status code would
    report every one of those as delivered, which is the worst possible lie for
    this component to tell."""
    monkeypatch.setattr(bridge, "RECIPIENT", "+85200000000")

    class _Response:
        def read(self) -> bytes:
            return json.dumps({"jsonrpc": "2.0", "error": {"message": "unregistered"}}).encode()

        def __enter__(self): return self
        def __exit__(self, *_a): return False

    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *_a, **_k: _Response())
    assert bridge.send("anything") is False


def test_a_clean_jsonrpc_result_is_a_delivery(bridge, monkeypatch):
    """The POSITIVE CONTROL. Every assertion above is satisfied by a `send`
    that always returns False; this is the one that proves it can succeed."""
    monkeypatch.setattr(bridge, "RECIPIENT", "+85200000000")

    class _Response:
        def read(self) -> bytes:
            return json.dumps({"jsonrpc": "2.0", "id": "alert", "result": {}}).encode()

        def __enter__(self): return self
        def __exit__(self, *_a): return False

    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *_a, **_k: _Response())
    assert bridge.send("anything") is True
