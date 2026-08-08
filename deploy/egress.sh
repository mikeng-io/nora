#!/usr/bin/env bash
set -euo pipefail

# ── Nora egress governance ───────────────────────────────────────────────────
#
# Container-era outbound-egress policy, versioned here so it is auditable and
# reproducible rather than living in an operator's shell history (governance,
# operator ask 2026-08-07). Root-only (iptables); idempotent; reversible.
#
# WHAT THIS CLOSES. Firecrawl (web_fetch's scraper) fetches a MODEL-CHOSEN URL,
# so a page that redirects or DNS-rebinds to an internal address would make
# Firecrawl fetch Nora's own internals and hand the body back to the group —
# server-side request forgery (SSRF). Measured 2026-08-07: from inside
# firecrawl-api, OmniRoute (172.17.0.1:20128) answered 307 and Honcho (:8000)
# answered 404 — real, sensitive, reachable. Nora's code blocks the direct and
# post-redirect paths (is_global + sourceURL re-check + the allowed_urls fence),
# but the airtight closure is to stop Firecrawl reaching private ranges AT THE
# NETWORK, so even a rebind cannot route there. That is this script.
#
# NOTE (2026-08-08, DEC-69): `fetch_enabled` was turned ON WITHOUT this script
# being the persistent gate it was designed to be — the operator accepted the
# blind-SSRF residual for the alpha, so today the live SSRF control is Nora's
# code alone (R-34's fence + is_global precheck), NOT this network isolation.
# This script still works when applied; the open problem is PERSISTENCE — the
# "RE-RUN after any compose up" note below is manual and has no canary, so the
# rules silently vanish. Making that automatic (a root systemd/docker-event
# reapply, NOT a NOPASSWD sudoers that would weaken this root gate) is the one
# live governance follow-up left.
#
# WHAT FIRECRAWL STILL NEEDS, and this preserves: its own backend services
# (redis / postgres / rabbitmq / playwright, all on the firecrawl_backend
# subnet) and the whole PUBLIC internet (that is the point of a scraper). Only
# the private/host ranges are denied.
#
#   sudo ./deploy/egress.sh apply     # install the Firecrawl egress policy
#   sudo ./deploy/egress.sh down      # remove it
#   sudo ./deploy/egress.sh verify    # print the three checks to run
#
# RE-RUN after any `docker compose up` of Firecrawl OR Nora: container IPs can
# change, and the DOCKER-USER hooks match the SSRF-surface containers by IP.

# Filtering happens in the `raw` table's PREROUTING chain, matched by the
# Firecrawl containers' source IPs — NOT DOCKER-USER. DOCKER-USER lives in
# FORWARD and never sees a container→HOST packet (dest 172.17.0.1 is a local
# address, delivered via INPUT), so it missed the exact path that matters:
# firecrawl → OmniRoute/Honcho on the docker gateway. raw/PREROUTING is the
# first hook every packet from the container hits, before the routing decision,
# so it catches host-bound AND forwarded traffic uniformly (verified: this is
# what the DOCKER-USER version could not block).
TAG="nora-fc-egress"
FC_NET="firecrawl_backend"
# The SSRF-surface containers: the API and the headless browser both fetch
# model-influenced URLs. The backend stores (redis/postgres/rabbitmq) are not
# hooked — they have no business egressing at all, but they are not a fetch
# surface, and over-restricting them risks breaking Firecrawl's internals.
FC_CONTAINERS=("firecrawl-api-1" "firecrawl-playwright-service-1")

# Private + host ranges Firecrawl must NOT reach. 172.16/12 covers the docker
# default bridge (172.17, the host gateway → OmniRoute/Honcho) AND firecrawl's
# own 172.19 — so the own-subnet ACCEPT below is evaluated FIRST.
BLOCK_NETS=(
  10.0.0.0/8
  172.16.0.0/12
  192.168.0.0/16
  169.254.0.0/16        # link-local + cloud metadata
  127.0.0.0/8
  100.64.0.0/10         # CGNAT
)

need_root() { [ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }; }

fc_subnet() {
  docker network inspect "$FC_NET" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
}

fc_ips() {
  local name ip
  for name in "${FC_CONTAINERS[@]}"; do
    ip=$(docker inspect "$name" \
      -f "{{(index .NetworkSettings.Networks \"$FC_NET\").IPAddress}}" 2>/dev/null || true)
    [ -n "$ip" ] && echo "$ip"
  done
}

remove_rules() {
  # Idempotent teardown: delete every raw/PREROUTING rule we tagged.
  iptables -t raw -S PREROUTING 2>/dev/null | grep -- "--comment \"$TAG" \
    | sed 's/^-A/-D/' | while read -r rule; do
        # shellcheck disable=SC2086
        iptables -t raw $rule 2>/dev/null || true
      done
}

apply() {
  need_root
  local subnet; subnet=$(fc_subnet)
  [ -n "$subnet" ] || { echo "could not read $FC_NET subnet — is Firecrawl up?" >&2; exit 1; }

  remove_rules
  local ip found=0
  while read -r ip; do
    [ -n "$ip" ] || continue
    # Per Firecrawl surface IP, in raw/PREROUTING (first hook, before routing —
    # catches host-bound AND forwarded packets, which DOCKER-USER could not).
    # ACCEPT its own backend subnet FIRST (inserted LAST so it sits on top),
    # then DROP every private/host range, letting the public internet through.
    local net
    for net in "${BLOCK_NETS[@]}"; do
      iptables -t raw -I PREROUTING -s "$ip" -d "$net" -j DROP \
        -m comment --comment "$TAG: deny private/host"
    done
    iptables -t raw -I PREROUTING -s "$ip" -d "$subnet" -j ACCEPT \
      -m comment --comment "$TAG: allow own backend"
    found=$((found + 1))
    echo "  filtered $ip (own subnet $subnet allowed, private/host denied, public allowed)"
  done < <(fc_ips)
  [ "$found" -gt 0 ] || { echo "no Firecrawl container IPs found — nothing filtered" >&2; exit 1; }
  echo "Firecrawl egress policy applied ($found surface container(s))."
  echo "Verify before flipping [search] fetch_enabled = true:  sudo $0 verify"
}

down() {
  need_root
  remove_rules
  echo "Firecrawl egress policy removed."
}

verify() {
  cat <<'CHECKS'
Run these THREE checks (the policy is correct iff all three hold):

  # 1. Firecrawl must NO LONGER reach a host service (was 307 before):
  docker exec firecrawl-api-1 sh -c 'node -e "require(\"http\").get({host:\"172.17.0.1\",port:20128,timeout:3000},r=>{console.log(\"REACHED \"+r.statusCode);process.exit(1)}).on(\"error\",e=>{console.log(\"blocked \"+e.code)}).on(\"timeout\",function(){this.destroy();console.log(\"blocked TIMEOUT\")})"'
  #   → expect: blocked …   (NOT "REACHED")

  # 2. Firecrawl must STILL reach its own backend (redis):
  docker exec firecrawl-api-1 sh -c 'node -e "require(\"net\").connect(6379,\"firecrawl-redis-1\",function(){console.log(\"redis ok\");this.destroy()}).on(\"error\",e=>console.log(\"redis BROKEN \"+e.code))"'
  #   → expect: redis ok

  # 3. Firecrawl must STILL reach the public internet:
  curl -s -m 20 -X POST http://127.0.0.1:3002/v1/scrape \
    -H 'Content-Type: application/json' \
    -d '{"url":"https://example.com/","formats":["markdown"]}' | head -c 80
  #   → expect: {"success":true,...
CHECKS
}

case "${1:-apply}" in
  apply)  apply ;;
  down)   down ;;
  verify) verify ;;
  *) echo "usage: $0 {apply|down|verify}" >&2; exit 2 ;;
esac
