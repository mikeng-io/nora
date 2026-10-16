"""Where the probe's alerts go.

`deploy/alert-bridge` is the human channel: Alertmanager POSTs a webhook payload
to `alert-bridge:9110/alert` and it renders a Discord message. The probe reuses
it, building the same payload shape Alertmanager would and posting it to that
endpoint.

The bridge publishes no host port (it is on the compose `nora` network), so
the post is made from inside its own container with `docker exec` — stdlib
`urllib` there, payload on stdin, nothing new exposed.

Sinks never raise: an alert path that throws would take the probe's own
record with it. A failed delivery is returned as False and logged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from .runner import Runner

log = logging.getLogger("nora.session_probe")


@dataclass(frozen=True)
class AlertMessage:
    network: str  # instagram | x | linkedin | probe
    reason: str
    summary: str


class AlertSink(Protocol):
    def send(self, message: AlertMessage) -> bool: ...


class LogSink:
    """Always-on floor: a JSON log line the journal keeps."""

    def send(self, message: AlertMessage) -> bool:
        log.warning(
            json.dumps({"alert": message.reason, "network": message.network,
                        "summary": message.summary})
        )
        return True


_POST_SCRIPT = (
    "import sys,urllib.request;"
    "r=urllib.request.Request('http://127.0.0.1:9110/alert',data=sys.stdin.buffer.read(),"
    "headers={'Content-Type':'application/json'});"
    "urllib.request.urlopen(r,timeout=10).read()"
)


class AlertBridgeSink:
    """Post an Alertmanager-shaped payload to deploy/alert-bridge."""

    def __init__(self, run: Runner, container: str = "nora-alert-bridge") -> None:
        self._run = run
        self._container = container

    def send(self, message: AlertMessage) -> bool:
        payload = {
            "status": "firing",
            "alerts": [{
                "labels": {"alertname": "SocialSessionProbe", "severity": "warning",
                           "job": message.network},
                "annotations": {"summary": f"{message.network}: {message.reason}",
                                "description": message.summary},
            }],
        }
        result = self._run(
            ["docker", "exec", "-i", self._container, "python", "-c", _POST_SCRIPT],
            20.0, json.dumps(payload),
        )
        if result.rc != 0:
            log.error("alert-bridge delivery failed rc=%s", result.rc)
        return result.rc == 0


class FanOutSink:
    def __init__(self, sinks: list[AlertSink]) -> None:
        self._sinks = sinks

    def send(self, message: AlertMessage) -> bool:
        results = [s.send(message) for s in self._sinks]
        return all(results)
