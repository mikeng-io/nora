#!/usr/bin/env bash
set -euo pipefail

# ── Install Nora's Firecrawl egress persistence (DEC-69) ──────────────────────
#
# Makes the egress cage survive `docker compose up` automatically, without ever
# weakening the root gate on the firewall. Installs three units:
#   nora-egress.service        one-shot: egress.sh reapply (apply + canary-verify)
#   nora-egress.timer          backstop: reapply every 2 min
#   nora-egress-watch.service  fast path: reapply the instant a container starts
#
# Run ONCE as root; safe to re-run (idempotent):
#   sudo ./deploy/install-egress-units.sh
#
# Optional: to publish the canary metric, uncomment the Environment= line in
# nora-egress.service to point at your node_exporter textfile collector dir.

[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo $0" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
EGRESS="$HERE/egress.sh"
WATCH="$HERE/nora-egress-watch.sh"
[ -x "$EGRESS" ] || chmod +x "$EGRESS"
[ -x "$WATCH" ]  || chmod +x "$WATCH"

for u in nora-egress.service nora-egress.timer nora-egress-watch.service; do
  # Copy, rewriting the canonical ExecStart paths to THIS checkout so the units
  # work regardless of where the repo lives.
  sed -e "s#/home/norty/workspace/nora/deploy/egress.sh#$EGRESS#g" \
      -e "s#/home/norty/workspace/nora/deploy/nora-egress-watch.sh#$WATCH#g" \
      "$HERE/systemd/$u" >"/etc/systemd/system/$u"
  echo "installed /etc/systemd/system/$u"
done

systemctl daemon-reload
systemctl enable --now nora-egress.timer nora-egress-watch.service
systemctl start nora-egress.service   # apply + verify right now

echo
echo "done. the cage should be closed now — confirm:"
echo "  $EGRESS canary          # exit 0 = intact"
echo "  systemctl status nora-egress.service nora-egress.timer nora-egress-watch.service"
