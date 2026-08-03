#!/usr/bin/env bash
set -euo pipefail

# Restrict the nora system user's outbound traffic to exactly the three
# localhost service ports it needs (signal-cli, Honcho, OmniRoute).
# Idempotent — safe to re-run at deploy time.

for port in 18080 8000 20128; do
    iptables -A OUTPUT -m owner --uid-owner nora -p tcp --dport "$port" -d 127.0.0.1 -j ACCEPT
done

iptables -A OUTPUT -m owner --uid-owner nora -j REJECT

echo "Egress restricted for uid-owner nora to ports 18080, 8000, 20128 on localhost."
