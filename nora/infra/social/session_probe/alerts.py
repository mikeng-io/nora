"""Where the probe's alerts go.

The human channel is the durable log line `LogSink` writes: the SigNoz log
pipeline carries it off the container's stdout into the centralized store,
where it is searchable and a logs-based alert can page on it. The retired
Alertmanager->alert-bridge webhook path went with the stack it delivered to.

Sinks never raise: an alert path that throws would take the probe's own
record with it. A failed delivery is returned as False and logged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

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


class FanOutSink:
    def __init__(self, sinks: list[AlertSink]) -> None:
        self._sinks = sinks

    def send(self, message: AlertMessage) -> bool:
        results = [s.send(message) for s in self._sinks]
        return all(results)
