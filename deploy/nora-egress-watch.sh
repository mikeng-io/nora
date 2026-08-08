#!/usr/bin/env bash
set -euo pipefail

# ── Nora Firecrawl egress cage — the docker-event fast path (DEC-69) ──────────
#
# Watches for a Firecrawl SURFACE container starting and reapplies the egress
# cage immediately. Container IPs change on `docker compose up`, and the
# raw/PREROUTING rules in egress.sh match those IPs, so a recreate silently
# reopens the cage. The 2-min nora-egress.timer is the backstop; this closes the
# gap between the compose up and the next tick.
#
# Run as a system service (nora-egress-watch.service) so `systemctl start
# nora-egress.service` below runs as root without a password — the root gate on
# the firewall is preserved, only the reapplication is automated.

# `docker events` blocks until a matching event, then prints the container name.
# Multiple --filter container=… are OR'd. A short settle lets the container's
# network attach before egress.sh reads its fresh IP.
docker events \
  --filter 'event=start' \
  --filter 'container=firecrawl-api-1' \
  --filter 'container=firecrawl-playwright-service-1' \
  --format '{{.Actor.Attributes.name}}' \
| while read -r name; do
    logger -t nora-egress "surface container ${name} started — reapplying egress cage"
    sleep 2
    systemctl start nora-egress.service || true
  done
