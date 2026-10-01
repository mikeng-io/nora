#!/usr/bin/env bash
# Is Honcho closed to everything except the callers that should reach it?
# (#633, DEC-259). Run ON the deploy host, as a user in the docker group.
# READ-ONLY: it starts throwaway containers and makes GET /health requests; it
# changes no stack, no config, no network.
#
#   deploy/honcho-closed-probe.sh
#
# Honcho answers with no authentication, so what protects it is WHERE it
# listens. This measures that, not what the compose file says:
#
#   1. CONTROL  a throwaway container on the default bridge reaches something
#               that IS published on every interface (Langfuse's port). If it
#               cannot, the probe cannot see out and a "refused" below would be
#               the probe's blindness, not Honcho's closure: ERROR.
#   2. CLOSED   the same container must be REFUSED on port 8000 at every IPv4
#               address the host owns (docker0, the LAN, the tailnet, every
#               compose bridge). Any HTTP answer at all is an open door: FAIL.
#   3. ALIVE    Honcho answers /health on the host's own loopback. A closed
#               port and a dead Honcho look the same from outside, so this is
#               what makes "refused" mean "closed" and not "down": ERROR if not.
#   4. CALLER   the nora container reaches it by name, `http://honcho:8000`.
#               Closing the door on everyone is not a fix if nora is shut out.
#   5. TAILNET  if `tailscale` is here, the forward Miles's other machines use
#               still answers. SKIP is printed when there is none.
#
# Exit 0 = every check passed. 1 = at least one FAIL (open, or a caller shut
# out). 2 = at least one ERROR and no FAIL (the probe cannot tell). A FAIL wins
# over an ERROR: an open door is the finding, whatever else could not be read.
set -uo pipefail

IMAGE=${PROBE_IMAGE:-nora:latest}          # has curl; already on the host
PORT=${HONCHO_PORT:-8000}
CONTROL_PORT=${PROBE_CONTROL_PORT:-3100}   # Langfuse web, published 0.0.0.0
NORA_CONTAINER=${NORA_CONTAINER:-nora}
HONCHO_URL_FROM_NORA=${HONCHO_URL_FROM_NORA:-http://honcho:$PORT}
TAILNET_PORT=${HONCHO_TAILNET_PORT:-18000}
TIMEOUT=${PROBE_TIMEOUT:-4}

fails=0
errors=0
say() { printf '%s\n' "$*"; }
pass() { say "PASS   $*"; }
fail() { say "FAIL   $*"; fails=$((fails + 1)); }
err()  { say "ERROR  $*"; errors=$((errors + 1)); }

# The HTTP status a throwaway container on the DEFAULT bridge gets from a URL;
# 000 is curl's "no HTTP answer" (refused, reset, timed out).
from_bridge() {
    docker run --rm --network bridge --entrypoint curl "$IMAGE" \
        -s -m "$TIMEOUT" -o /dev/null -w '%{http_code}' "$1" </dev/null 2>/dev/null
}

http_from_host() {
    curl -s -m "$TIMEOUT" -o /dev/null -w '%{http_code}' "$1" </dev/null 2>/dev/null
}

# Every IPv4 address the host owns: docker0's gateway is how a container
# reaches the host, the LAN address is how a neighbour does, and each compose
# bridge's gateway is how a container on that network does.
host_addresses() {
    hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort -u
}

addresses=$(host_addresses)
if [ -z "$addresses" ]; then
    err "no host address found (hostname -I printed nothing): nothing to probe"
fi
gateway=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null)

# 1. CONTROL
if [ -z "$gateway" ]; then
    err "CONTROL: could not read the default bridge's gateway"
else
    code=$(from_bridge "http://$gateway:$CONTROL_PORT/")
    if [ -z "$code" ] || [ "$code" = 000 ]; then
        err "CONTROL: a default-bridge container got no HTTP answer from $gateway:$CONTROL_PORT, a port that is published on every interface, so a refusal below would prove nothing"
    else
        pass "CONTROL: a default-bridge container reaches $gateway:$CONTROL_PORT (HTTP $code), so a refusal below is real"
    fi
fi

# 2. CLOSED, at every address
for ip in $addresses; do
    code=$(from_bridge "http://$ip:$PORT/health")
    if [ -z "$code" ] || [ "$code" = 000 ]; then
        pass "CLOSED: $ip:$PORT refuses a default-bridge container"
    else
        fail "CLOSED: $ip:$PORT ANSWERED a default-bridge container (HTTP $code); Honcho is open on that address"
    fi
done

# 3. ALIVE
code=$(http_from_host "http://127.0.0.1:$PORT/health")
if [ "$code" = 200 ]; then
    pass "ALIVE: Honcho answers /health on 127.0.0.1:$PORT (HTTP 200)"
else
    err "ALIVE: Honcho did not answer /health on 127.0.0.1:$PORT (HTTP ${code:-none}); a refusal above may be a dead Honcho, not a closed one"
fi

# 4. CALLER
code=$(docker exec "$NORA_CONTAINER" curl -s -m "$TIMEOUT" -o /dev/null -w '%{http_code}' \
    "$HONCHO_URL_FROM_NORA/health" </dev/null 2>/dev/null)
if [ "$code" = 200 ]; then
    pass "CALLER: $NORA_CONTAINER reaches $HONCHO_URL_FROM_NORA/health (HTTP 200)"
else
    fail "CALLER: $NORA_CONTAINER could not reach $HONCHO_URL_FROM_NORA/health (HTTP ${code:-none}); closing Honcho shut its caller out"
fi

# 5. TAILNET
if command -v tailscale >/dev/null 2>&1; then
    ts_ip=$(tailscale ip -4 2>/dev/null | head -n 1)
    if [ -n "$ts_ip" ]; then
        code=$(http_from_host "http://$ts_ip:$TAILNET_PORT/health")
        if [ "$code" = 200 ]; then
            pass "TAILNET: $ts_ip:$TAILNET_PORT still forwards to Honcho (HTTP 200)"
        else
            fail "TAILNET: $ts_ip:$TAILNET_PORT did not answer (HTTP ${code:-none}); the tailnet forward lost Honcho"
        fi
    else
        say "SKIP   TAILNET: tailscale is installed but reports no IPv4 address"
    fi
else
    say "SKIP   TAILNET: no tailscale here"
fi

say "---"
say "fails=$fails errors=$errors"
if [ "$fails" -gt 0 ]; then
    exit 1
fi
if [ "$errors" -gt 0 ]; then
    exit 2
fi
exit 0
