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
#   2. CLOSED   the same container must be REFUSED on port 8000 at every address
#               the host owns, IPv4 AND IPv6 (docker0, the LAN, the tailnet,
#               every compose bridge). Any HTTP answer at all is an open door:
#               FAIL. Only curl's exit 7 (connection refused) is "closed": a
#               timeout (28), a reset, an empty result or a docker failure is
#               ERROR, because a dropped packet and a closed port are not the
#               same claim. An address the control cannot reach (typically an
#               IPv6 one: the default bridge often has no IPv6 route) is not
#               probed over the network; the listener check covers it.
#   3. LISTEN   the host's own listening sockets on :8000 are loopback only, in
#               both families. This reads the thing itself and does not depend
#               on any route, so it is what covers IPv6.
#   4. ALIVE    Honcho answers /health on the host's own loopback. A closed
#               port and a dead Honcho look the same from outside, so this is
#               what makes "refused" mean "closed" and not "down": ERROR if not.
#   5. CALLER   the nora container reaches it by name, `http://honcho:8000`.
#               Closing the door on everyone is not a fix if nora is shut out.
#   6. TAILNET  if `tailscale` is here, the forward Miles's other machines use
#               still answers. SKIP is printed when there is none.
#   7. ENGINE   the Docker Engine is version 28 or newer. Older engines let a
#               port published on 127.0.0.1 be reached from the LAN, and a probe
#               run ON the host cannot see that (it needs a machine on the LAN):
#               WARN, and a non-zero exit, so the gap is loud instead of green.
#
# Exit 0 = every check passed. 1 = at least one FAIL (open, or a caller shut
# out). 2 = at least one ERROR and no FAIL (the probe cannot tell). 3 = nothing
# failed or errored but there is a WARN (the engine is too old for this probe
# to vouch for the LAN). A FAIL wins over an ERROR, and an ERROR over a WARN.
set -uo pipefail

IMAGE=${PROBE_IMAGE:-nora:latest}          # has curl; already on the host
PORT=${HONCHO_PORT:-8000}
CONTROL_PORT=${PROBE_CONTROL_PORT:-3100}   # Langfuse web, published 0.0.0.0
NORA_CONTAINER=${NORA_CONTAINER:-nora}
HONCHO_URL_FROM_NORA=${HONCHO_URL_FROM_NORA:-http://honcho:$PORT}
TAILNET_PORT=${HONCHO_TAILNET_PORT:-18000}
TIMEOUT=${PROBE_TIMEOUT:-4}
MIN_ENGINE_MAJOR=${PROBE_MIN_ENGINE_MAJOR:-28}

fails=0
errors=0
warns=0
say() { printf '%s\n' "$*"; }
pass() { say "PASS   $*"; }
fail() { say "FAIL   $*"; fails=$((fails + 1)); }
err()  { say "ERROR  $*"; errors=$((errors + 1)); }
warn() { say "WARN   $*"; warns=$((warns + 1)); }

# What a throwaway container on the DEFAULT bridge gets from a URL: prints
# "<http code> <curl exit code>". 000 with exit 7 is a refused connection; an
# empty line is docker itself failing.
from_bridge() {
    local out rc
    out=$(docker run --rm --network bridge --entrypoint curl "$IMAGE" \
        -g -s -m "$TIMEOUT" -o /dev/null -w '%{http_code}' "$1" </dev/null 2>/dev/null)
    rc=$?
    [ -n "$out" ] || { echo ""; return; }
    echo "$out $rc"
}

http_from_host() {
    curl -g -s -m "$TIMEOUT" -o /dev/null -w '%{http_code}' "$1" </dev/null 2>/dev/null
}

# A URL host part for an address: IPv6 literals need brackets.
url_host() {
    case "$1" in *:*) printf '[%s]' "$1" ;; *) printf '%s' "$1" ;; esac
}

# Every address the host owns, both families, loopback and link-local excluded:
# docker0's gateway is how a container reaches the host, the LAN address is how
# a neighbour does, and each compose bridge's gateway is how a container on that
# network does.
host_addresses() {
    hostname -I 2>/dev/null | tr ' ' '\n' \
        | grep -E '^([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|[0-9a-fA-F]*:[0-9a-fA-F:]+)$' \
        | grep -viE '^fe80:' | sort -u
}

addresses=$(host_addresses)
if [ -z "$addresses" ]; then
    err "no host address found (hostname -I printed nothing): nothing to probe"
fi
gateway=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null)

# 1. CONTROL (the default bridge's own gateway; each address gets its own below)
if [ -z "$gateway" ]; then
    err "CONTROL: could not read the default bridge's gateway"
else
    got=$(from_bridge "http://$gateway:$CONTROL_PORT/")
    code=${got%% *}
    if [ -z "$got" ] || [ "$code" = 000 ]; then
        err "CONTROL: a default-bridge container got no HTTP answer from $gateway:$CONTROL_PORT, a port that is published on every interface, so a refusal below would prove nothing"
    else
        pass "CONTROL: a default-bridge container reaches $gateway:$CONTROL_PORT (HTTP $code), so a refusal below is real"
    fi
fi

# 2. CLOSED, at every address, with a control at that same address
for ip in $addresses; do
    host=$(url_host "$ip")
    ctl=$(from_bridge "http://$host:$CONTROL_PORT/")
    ctl_code=${ctl%% *}
    if [ -z "$ctl" ] || [ "$ctl_code" = 000 ]; then
        case "$ip" in
            *:*) say "SKIP   CLOSED: $ip is not reachable from the default bridge even on the control port (no IPv6 route there), so no network probe of it; LISTEN covers it" ;;
            *) err "CLOSED: the control port does not answer at $ip either, so a refusal at $ip:$PORT proves nothing" ;;
        esac
        continue
    fi
    got=$(from_bridge "http://$host:$PORT/health")
    code=${got%% *}
    rc=${got##* }
    if [ -z "$got" ]; then
        err "CLOSED: no result at all for $ip:$PORT (docker run failed); a missing answer is not a refusal"
    elif [ "$code" != 000 ]; then
        fail "CLOSED: $ip:$PORT ANSWERED a default-bridge container (HTTP $code); Honcho is open on that address"
    elif [ "$rc" = 7 ]; then
        pass "CLOSED: $ip:$PORT refuses a default-bridge container (connection refused)"
    else
        err "CLOSED: $ip:$PORT gave no HTTP answer but curl exited $rc, not 7 (refused): a timeout or a reset is not proof of a closed port"
    fi
done

# 3. LISTEN: the sockets themselves
if command -v ss >/dev/null 2>&1; then
    listeners=$(ss -H -ltn "sport = :$PORT" 2>/dev/null | awk '{print $4}')
    if [ -z "$listeners" ]; then
        err "LISTEN: no listening socket on :$PORT at all (ss shows none): Honcho is not up, or ss cannot see it"
    else
        open=$(printf '%s\n' "$listeners" | grep -vE '^(127\.[0-9.]+|\[::1\]):' || true)
        if [ -n "$open" ]; then
            fail "LISTEN: :$PORT is listening on a non-loopback address: $(printf '%s' "$open" | tr '\n' ' ')"
        else
            pass "LISTEN: every listener on :$PORT is loopback: $(printf '%s' "$listeners" | tr '\n' ' ')"
        fi
    fi
else
    err "LISTEN: ss is not installed, so the sockets cannot be read (IPv6 exposure is unchecked)"
fi

# 4. ALIVE
code=$(http_from_host "http://127.0.0.1:$PORT/health")
if [ "$code" = 200 ]; then
    pass "ALIVE: Honcho answers /health on 127.0.0.1:$PORT (HTTP 200)"
else
    err "ALIVE: Honcho did not answer /health on 127.0.0.1:$PORT (HTTP ${code:-none}); a refusal above may be a dead Honcho, not a closed one"
fi

# 5. CALLER
code=$(docker exec "$NORA_CONTAINER" curl -s -m "$TIMEOUT" -o /dev/null -w '%{http_code}' \
    "$HONCHO_URL_FROM_NORA/health" </dev/null 2>/dev/null)
if [ "$code" = 200 ]; then
    pass "CALLER: $NORA_CONTAINER reaches $HONCHO_URL_FROM_NORA/health (HTTP 200)"
else
    fail "CALLER: $NORA_CONTAINER could not reach $HONCHO_URL_FROM_NORA/health (HTTP ${code:-none}); closing Honcho shut its caller out"
fi

# 6. TAILNET
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

# 7. ENGINE
version=$(docker version --format '{{.Server.Version}}' 2>/dev/null)
major=${version%%.*}
case "$major" in
    ''|*[!0-9]*) err "ENGINE: could not read the Docker Engine version (got '${version:-nothing}')" ;;
    *)
        if [ "$major" -lt "$MIN_ENGINE_MAJOR" ]; then
            warn "ENGINE: Docker Engine $version is older than $MIN_ENGINE_MAJOR: a port published on 127.0.0.1 can be reached from the LAN on it, and this probe runs on the host and cannot see that. From a machine on the LAN: curl -m 4 http://<this host's LAN address>:$PORT/health must fail"
        else
            pass "ENGINE: Docker Engine $version is $MIN_ENGINE_MAJOR or newer"
        fi ;;
esac

say "---"
say "fails=$fails errors=$errors warns=$warns"
if [ "$fails" -gt 0 ]; then
    exit 1
fi
if [ "$errors" -gt 0 ]; then
    exit 2
fi
if [ "$warns" -gt 0 ]; then
    exit 3
fi
exit 0
