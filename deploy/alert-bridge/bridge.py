"""Alertmanager webhook -> signal-cli JSON-RPC. Nothing else.

**Why it is its own container rather than an endpoint on Nora.** Nora already
talks to signal-cli and already runs an aiohttp app, so adding `/alerts` there
would have been three lines. It would also have made the single most important
alert this stack can send — "Nora is down" — depend on Nora being up. An alerting
path that shares a failure domain with the thing it watches is decoration.

**Stdlib only, no dependencies.** This process exists to work on the worst day
the deployment has. Every dependency it does not have is a dependency that
cannot fail during an incident, and the whole job is one POST reshaped into
another.

It is deliberately dumb: no retry queue, no state, no dedup. Alertmanager
already does grouping, throttling and repeat, and re-implementing any of that
here would mean two components disagreeing about whether you have been told.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}',
)
log = logging.getLogger("alert-bridge")

SIGNAL_RPC = os.environ.get("SIGNAL_RPC_URL", "http://signal-cli:18080/api/v1/rpc")
RECIPIENT = os.environ.get("ALERT_SIGNAL_RECIPIENT", "")
PORT = int(os.environ.get("ALERT_BRIDGE_PORT", "9110"))
#: How long to wait on signal-cli. Short: a bridge blocked on a wedged
#: transport stops accepting the NEXT alert, which is how one broken
#: notification silences the rest.
TIMEOUT = float(os.environ.get("ALERT_SIGNAL_TIMEOUT", "10"))


def render(payload: dict) -> str:
    """One Alertmanager group -> one message a human can act on.

    `status` first and in caps, because the difference between FIRING and
    RESOLVED is the only thing that matters on a phone screen at 3am. The
    summary carries the rest; the description is included only when it says
    something the summary does not.
    """
    status = str(payload.get("status", "unknown")).upper()
    alerts = payload.get("alerts") or []
    lines = [f"[{status}] {len(alerts)} alert(s)"]
    for alert in alerts[:10]:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        name = labels.get("alertname", "unnamed")
        severity = labels.get("severity", "")
        where = labels.get("job") or labels.get("instance") or ""
        head = " ".join(part for part in (name, f"({severity})" if severity else "", where) if part)
        lines.append(f"• {head}")
        summary = (annotations.get("summary") or "").strip()
        if summary:
            lines.append(f"  {summary}")
        description = (annotations.get("description") or "").strip()
        if description and description != summary:
            lines.append(f"  {description}")
    if len(alerts) > 10:
        # TRUNCATED, and it says so. A silently clipped list is a message that
        # lies about how bad the incident is.
        lines.append(f"… and {len(alerts) - 10} more")
    return "\n".join(lines)


def send(message: str) -> bool:
    """POST to signal-cli's JSON-RPC. Never raises — a bridge that dies on a
    bad response stops delivering every LATER alert too."""
    if not RECIPIENT:
        log.error("ALERT_SIGNAL_RECIPIENT is unset; alert not delivered")
        return False
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": "alert",
        "method": "send",
        "params": {"recipient": [RECIPIENT], "message": message},
    }).encode()
    request = urllib.request.Request(  # noqa: S310 — fixed http scheme from env
        SIGNAL_RPC, data=body, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            raw = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        log.error("signal-cli unreachable: %s", exc)
        return False
    # A JSON-RPC 200 can still carry an error object. Reading it is the
    # difference between "delivered" and "the transport accepted the bytes".
    try:
        answer = json.loads(raw)
    except ValueError:
        log.error("signal-cli returned non-JSON: %s", raw[:200])
        return False
    if isinstance(answer, dict) and answer.get("error"):
        log.error("signal-cli refused the send: %s", answer["error"])
        return False
    return True


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
        if self.path.rstrip("/") != "/alert":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            log.error("alertmanager sent non-JSON")
            self.send_response(400)
            self.end_headers()
            return
        delivered = send(render(payload))
        # 200 EVEN WHEN THE SEND FAILED, and on purpose: a non-2xx makes
        # Alertmanager retry, and a retry against a wedged signal-cli produces
        # a queue of duplicate messages that all arrive at once when it
        # recovers. The failure is logged and visible as a gap; the loop is
        # worse than the gap.
        self.send_response(200 if delivered else 202)
        self.end_headers()
        self.wfile.write(b"ok" if delivered else b"logged")

    def do_GET(self) -> None:  # noqa: N802
        # For the container healthcheck. It answers whether the BRIDGE is up,
        # never whether signal-cli is — conflating the two would make a Signal
        # outage restart this container pointlessly.
        self.send_response(200 if self.path.rstrip("/") in ("", "/health") else 404)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        # aiohttp's access log was 53% of Nora's own log file. Not repeating it.
        return


if __name__ == "__main__":
    log.info("alert-bridge listening on :%d -> %s", PORT, SIGNAL_RPC)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
