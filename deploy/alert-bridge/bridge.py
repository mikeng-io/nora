"""Alertmanager webhook -> a Discord channel. Nothing else.

**Why it is its own container rather than an endpoint on Nora.** An endpoint on
Nora would make the most important alert this stack sends — "Nora is down" —
depend on Nora being up. An alerting path that shares a failure domain with the
thing it watches is decoration.

**Discord's REST API, not the Gateway and not a webhook.** The Gateway is a
persistent connection with a reconnect state machine, which is a lot of moving
parts for one POST an hour at most. A webhook would work too and would avoid
handling a token — but it has to be created by hand in the Discord UI and
pasted somewhere, where the bot token this already needs is in `.env`
already. One credential, no manual step, and the REST call does not care
whether Nora's Gateway session is alive.

**Stdlib only, no dependencies.** This process exists to work on the worst day
the deployment has. Every dependency it does not have is a dependency that
cannot fail during an incident, and the whole job is one POST reshaped into
another.

It is deliberately dumb: no retry queue, no state, no dedup. Alertmanager
already does grouping, throttling and repeat, and re-implementing any of that
here would mean two components disagreeing about whether you have been told.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# JSON lines, matching Nora's own log shape (`nora/obs/logging.py`) so one
# `docker logs` habit works on both.
_FORMAT = '{"ts":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}'
logging.basicConfig(level=logging.INFO, format=_FORMAT)
log = logging.getLogger("alert-bridge")

API = os.environ.get("DISCORD_API_BASE", "https://discord.com/api/v10")
CHANNEL = os.environ.get("ALERT_DISCORD_CHANNEL", "")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
PORT = int(os.environ.get("ALERT_BRIDGE_PORT", "9110"))
#: How long to wait on Discord. Short: a bridge blocked on a wedged transport
#: stops accepting the NEXT alert, which is how one broken notification
#: silences the rest.
TIMEOUT = float(os.environ.get("ALERT_DISCORD_TIMEOUT", "10"))
#: Discord rejects a message body over 2000 characters outright. A burst of
#: alerts can exceed it, and a 400 from Discord would turn "many things broke"
#: into "you were told nothing" — the worst possible time to lose the message.
MAX_CONTENT = 1900
#: The bearer every POST must carry: the shared `NORA_BRIDGE_TOKEN`, which
#: compose already requires and Alertmanager sends (deploy/alertmanager).
#: Empty fails closed — `main` refuses to start, and `Handler` refuses every POST.
BRIDGE_TOKEN = os.environ.get("NORA_BRIDGE_TOKEN", "")
#: The largest body accepted. An Alertmanager group is a few KB; 256 KiB is
#: hundreds of alerts with full annotations. Checked against the declared
#: length BEFORE a byte is read, so the bound is on what the bridge buffers.
MAX_BODY = 256 * 1024
#: Seconds one connection may take to deliver its request. Alertmanager sends
#: its body at once; a client that stalls mid-body would otherwise hold a
#: thread for as long as it likes.
INBOUND_TIMEOUT = 10
#: Connections served at once, one thread each. Alertmanager sends one POST
#: per group, so more than a handful in flight means something is wrong. A
#: connection past the bound is answered 503 and closed, not queued.
MAX_CONCURRENT = 8
#: The longest any one label or annotation is rendered. A runaway annotation
#: becomes a truncated one, marked as such, instead of the whole message.
MAX_FIELD = 300


def _field(value: object) -> str:
    """One label or annotation, stripped and bounded. Truncation is marked, like
    the message's own: a silently clipped field reads as a complete one."""
    text = "" if value is None else str(value).strip()
    return text if len(text) <= MAX_FIELD else text[:MAX_FIELD] + "…"


def render(payload: dict) -> str:
    """One Alertmanager group -> one message a human can act on.

    `status` first and in caps, because the difference between FIRING and
    RESOLVED is the only thing that matters on a phone screen at 3am. The
    summary carries the rest; the description is included only when it says
    something the summary does not.
    """
    status = _field(payload.get("status", "unknown")).upper()
    raw_alerts = payload.get("alerts")
    alerts = [a for a in raw_alerts if isinstance(a, dict)] if isinstance(raw_alerts, list) else []
    lines = [f"[{status}] {len(alerts)} alert(s)"]
    for alert in alerts[:10]:
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        name = _field(labels.get("alertname") or "unnamed")
        severity = _field(labels.get("severity"))
        where = _field(labels.get("job") or labels.get("instance"))
        head = " ".join(part for part in (name, f"({severity})" if severity else "", where) if part)
        lines.append(f"• {head}")
        summary = _field(annotations.get("summary"))
        if summary:
            lines.append(f"  {summary}")
        description = _field(annotations.get("description"))
        if description and description != summary:
            lines.append(f"  {description}")
    if len(alerts) > 10:
        # TRUNCATED, and it says so. A silently clipped list is a message that
        # lies about how bad the incident is.
        lines.append(f"… and {len(alerts) - 10} more")
    return "\n".join(lines)


def send(message: str) -> bool:
    """POST one message to the channel. Never raises — a bridge that dies on a
    bad response stops delivering every LATER alert too."""
    if not CHANNEL or not TOKEN:
        missing = " and ".join(
            n for n, v in (("ALERT_DISCORD_CHANNEL", CHANNEL), ("DISCORD_BOT_TOKEN", TOKEN))
            if not v
        )
        log.error("%s is unset; alert not delivered", missing)
        return False
    if len(message) > MAX_CONTENT:
        # TRUNCATED, and it says so — for `render`'s reason one level down: a
        # silently clipped message understates the incident.
        message = message[:MAX_CONTENT] + "\n… truncated"
    # `parse: []` turns off every mention Discord would otherwise act on. The
    # text is copied from rule annotations and label values, and some of those
    # echo input nobody here controls: a bare `@everyone` must not ping a room.
    body = json.dumps({"content": message, "allowed_mentions": {"parse": []}}).encode()
    request = urllib.request.Request(  # noqa: S310 — fixed https base
        f"{API}/channels/{CHANNEL}/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {TOKEN}",
            # Discord asks for one and blocks the default urllib agent.
            "User-Agent": "NoraAlertBridge (https://github.com/mikeng-io/nora, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            response.read()
    except urllib.error.HTTPError as exc:
        # The body carries Discord's own reason — a bad channel id, a bot
        # without permission to post there, a 429. Logging the status alone
        # would leave every one of those looking identical.
        detail = ""
        with contextlib.suppress(Exception):
            # A failed read must not mask the HTTP error that is the point.
            detail = exc.read().decode("utf-8", "replace")[:200]
        log.error("discord refused the send: %s %s", exc.code, detail)
        return False
    except (urllib.error.URLError, OSError) as exc:
        log.error("discord unreachable: %s", exc)
        return False
    # Every other path logs, so without a success line silence would mean both
    # "delivered" and "the webhook never arrived". Alertmanager already groups
    # and throttles, so this is one line per delivered notification.
    log.info("delivered %d chars to channel %s", len(message), CHANNEL)
    return True


class Server(ThreadingHTTPServer):
    """A thread per connection, and no more than MAX_CONCURRENT of them at once.

    The bound is taken in `process_request`, before a thread exists, so a flood
    is refused at the accept rather than queued behind the threads it started.
    """

    def __init__(self, address: tuple[str, int], handler: type) -> None:
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            log.error("connection refused: %d already in flight", MAX_CONCURRENT)
            with contextlib.suppress(OSError):
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n",
                )
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    #: Socket timeout for one connection, so a client that stops sending is
    #: disconnected instead of holding a thread and a slot.
    timeout = INBOUND_TIMEOUT

    def _authorized(self) -> bool:
        # Fail closed on an empty token. Digests on both sides make the compare
        # one length regardless of what the caller sent, which compare_digest
        # on the raw strings would not: it returns early on a length mismatch.
        if not BRIDGE_TOKEN:
            return False
        got = self.headers.get("Authorization", "").encode("utf-8")
        want = f"Bearer {BRIDGE_TOKEN}".encode()
        return hmac.compare_digest(hashlib.sha256(got).digest(), hashlib.sha256(want).digest())

    def _declared_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or "")
        except ValueError:
            return -1

    def _refuse(self, status: int, unread: int, headers: tuple[tuple[str, str], ...] = ()) -> None:
        """Answer without using the body. A body being refused is read off first,
        up to MAX_BODY, because closing over unread bytes resets the connection
        before the client has read the answer. A larger body is not read at all:
        reading it is what refusing it is meant to avoid."""
        if 0 < unread <= MAX_BODY:
            with contextlib.suppress(OSError):
                self.rfile.read(unread)
        self.close_connection = True
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()

    def do_POST(self) -> None:  # BaseHTTPRequestHandler's contract
        if self.path.rstrip("/") != "/alert":
            self.send_response(404)
            self.end_headers()
            return
        length = self._declared_length()
        if not self._authorized():
            log.error("alert refused: bearer missing or wrong")
            self._refuse(401, length, (("WWW-Authenticate", "Bearer"),))
            return
        if length < 0:
            self._refuse(400, 0)
            return
        if length > MAX_BODY:
            log.error("alertmanager sent %d bytes; the limit is %d", length, MAX_BODY)
            self._refuse(413, 0)
            return
        raw = self.rfile.read(length)
        if len(raw) != length:
            # The client went away mid-body. What arrived is not an alert.
            log.error("alertmanager body ended after %d of %d bytes", len(raw), length)
            self.close_connection = True
            self.send_response(400)
            self.end_headers()
            return
        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            log.error("alertmanager sent non-JSON")
            self.send_response(400)
            self.end_headers()
            return
        if not isinstance(payload, dict):
            log.error("alertmanager sent JSON that is not an object")
            self.send_response(400)
            self.end_headers()
            return
        delivered = send(render(payload))
        # 200 EVEN WHEN THE SEND FAILED, and on purpose: a non-2xx makes
        # Alertmanager retry, and a retry against a rate-limited Discord
        # produces a queue of duplicate messages that all arrive at once when
        # the limit clears. The failure is logged and visible as a gap; the
        # loop is worse than the gap.
        self.send_response(200 if delivered else 202)
        self.end_headers()
        self.wfile.write(b"ok" if delivered else b"logged")

    def do_GET(self) -> None:
        # For the container healthcheck. It answers whether the BRIDGE is up,
        # never whether Discord is — conflating the two would make a Discord
        # outage restart this container pointlessly.
        self.send_response(200 if self.path.rstrip("/") in ("", "/health") else 404)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        # Per-request access logging is suppressed: it would drown the delivery lines.
        return


if __name__ == "__main__":
    if not BRIDGE_TOKEN:
        # Refused before the port is bound. A bridge that starts open and warns
        # is one any co-networked container can post to, and nobody reads the warning.
        log.error("NORA_BRIDGE_TOKEN is unset; refusing to start an unauthenticated alert bridge")
        sys.exit(2)
    log.info(
        "alert-bridge listening on :%d -> %s/channels/%s",
        PORT, API, CHANNEL or "<unset>",
    )
    Server(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
