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
import json
import logging
import os
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
    body = json.dumps({"content": message}).encode()
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


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # BaseHTTPRequestHandler's contract
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
    log.info(
        "alert-bridge listening on :%d -> %s/channels/%s",
        PORT, API, CHANNEL or "<unset>",
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
