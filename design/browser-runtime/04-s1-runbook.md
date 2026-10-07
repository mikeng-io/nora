# S1 runbook — measure before building (norty)

Status (2026-10-07): **run on norty 2026-10-07; results in §Results.**
Step 4 FAILED, and that decides the egress design. Every step below names its
reading and what counts as PASS, FAIL or ERROR. ERROR means the probe
itself did not run cleanly, and it is never read as PASS. Results go in
§Results with the date and the command's own output, nothing paraphrased.

Already read on 2026-10-07 (`00-design.md` §0): norty is x86-64 (Google
Chrome stable installs); the proxy tunnels TLS for `pypi.org` and
`archive.ubuntu.com`; the policy language and its rebinding caveat.

## 0. Ground rules

- **Never touch `nora-computer`.** S1 runs in a throwaway sandbox,
  `browser-s1`, with the same size (`-m 3g --cpus 2`). Every policy rule
  is `--sandbox browser-s1`, so `nora-computer`'s DEC-260 allowlist is not
  changed. A rule without `--sandbox` is global: never add one.
- Run as `norty`, over `ssh norty`, from a directory outside the deploy
  checkout (`~/s1/`), not inside `~/workspace/nora`.
- Each step captures its exit code (`RC=$?`) and writes to
  `~/s1/<step>.log`; read the log, never a pipe through `tail`.
- Health and reachability are read from an HTTP body, never a TCP connect
  (sandbox §9: every connect "succeeds" under sbx).
- Cleanup (§7) is part of the run. S1 is not done while `browser-s1` or its
  rules exist.
- **Baseline first:** `sbx policy ls > ~/s1/0-before.txt` and
  `sbx ls --json > ~/s1/0-sandboxes.json` before step 1. §7 diffs them.
- The live social Chrome on the host (`nora-social-chrome.service`, DEC-201)
  is never launched, upgraded or reused by S1. The host reference in §3 is a
  separate Chrome for Testing build under `~/s1/`.

## Corrections from the run (2026-10-07)

Running the runbook as written hit four things; the steps below are
correct as written only with these:

- **`sbx create` auto-stops** a local sandbox 30 s after its last session
  disconnects, killing Xvfb and Chrome between steps (`deploy/computer/
  nora-computer` `prepare()` documents it). Create it detached:
  `sbx run -d --name browser-s1 -m 3g --cpus 2 --pull never -t
  nora-computer:latest shell ~/s1/ws` (an empty, mode-700 workspace).
- **The Docker Hub template could not be pulled** ("docker login service
  unavailable"). The local `nora-computer:latest` template needs no pull,
  and matches production's base (Ubuntu 26.04.1, user `computer`). Root is
  `sbx exec -u root`.
- **Removing a rule** is `sbx policy rm network --sandbox browser-s1 --id
  <id> --force`; without `--sandbox` it looks in the global policy, and
  without `--force` it wants a terminal. Read the rule's effect back after
  removing it.
- **`sbx rm` needs** `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` (the
  keychain workaround from `nora-computer`), or it fails on "the OS keychain
  is locked".
- Chrome for Testing on the host needs `--no-sandbox` (Ubuntu 26.04 gives an
  unprivileged user no usable Chrome sandbox); TLS is unaffected.

## 1. The sandbox and Chrome

```bash
mkdir -p ~/s1 && cd ~/s1
sbx create --name browser-s1 -m 3g --cpus 2 -q shell </dev/null; echo "RC=$?"
sbx policy allow network --sandbox browser-s1 \
  "archive.ubuntu.com:80,archive.ubuntu.com:443,security.ubuntu.com:80,security.ubuntu.com:443,dl.google.com:443"
sbx exec browser-s1 sh -c '
  curl -fsSLo /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb &&
  sudo apt-get update && sudo apt-get install -y /tmp/chrome.deb xvfb x11-utils jq &&
  google-chrome --version' > 1-install.log 2>&1; echo "RC=$?"
V=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' 1-install.log | tail -1); echo "V=$V" > 1-version.txt
```

PASS: `RC=0` and a `Google Chrome <version>` line. Record the version: the
Dockerfile pins it later. If the default `shell` template has no `sudo`,
install as root through `sbx exec -u root` and note it. Chrome itself runs
as the unprivileged user; if it has to run as root, it needs `--no-sandbox`,
which is recorded as a deviation, since production never runs it that way.
Every `curl localhost:9222` below takes `--noproxy '*'`: the VM's
`http_proxy` must not route the debugging port.

## 2. RSS at 1, 3 and 5 tabs

Chrome is launched headed on Xvfb, as the design runs it, with a fresh
profile and the proxy given explicitly. S1 uses a TCP debugging port for convenience in a
throwaway sandbox; production uses `--remote-debugging-pipe` (`00-design.md`
§4), and B1 measures that RSS is unchanged by it.

```bash
sbx policy allow network --sandbox browser-s1 \
  "en.wikipedia.org:443,*.wikimedia.org:443,www.bbc.com:443,**.bbci.co.uk:443,github.com:443,**.githubassets.com:443,www.youtube.com:443,**.ytimg.com:443,**.googlevideo.com:443"
sbx exec browser-s1 sh -c '
  Xvfb :99 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
  sleep 1
  DISPLAY=:99 nohup google-chrome --user-data-dir=/tmp/s1-profile \
    --proxy-server=http://gateway.docker.internal:3128 \
    --remote-debugging-port=9222 --no-first-run --no-default-browser-check \
    about:blank >/tmp/chrome.log 2>&1 &
  sleep 5; curl -s --noproxy "*" localhost:9222/json/version'
```

Then for N in 1, 3, 5, open the first N of these URLs (one tab each) with
`curl -s --noproxy '*' -X PUT "localhost:9222/json/new?<url>"`, wait 20 s, and read:

```bash
sbx exec browser-s1 sh -c '
  ps -C chrome,Xvfb -o rss= | awk "{s+=\$1} END {print s/1024 \" MiB\"}";
  grep MemAvailable /proc/meminfo'
```

URLs, in order: `https://en.wikipedia.org/wiki/Hong_Kong`,
`https://www.bbc.com/news`, `https://github.com/trending`,
`https://www.youtube.com/`, `https://en.wikipedia.org/wiki/Cantonese`.

Reading: Chrome + Xvfb RSS in MiB and `MemAvailable` at each N. RSS
double-counts shared pages, so `MemAvailable` is the number that decides.
PASS for the design: `MemAvailable` above 500 MiB at 5 tabs (the room left
for a room's `computer_run` work beside the browser). FAIL: below that, or
the OOM killer in `dmesg`. ERROR: a tab that did not load (check each tab's
title over `localhost:9222/json`).

## 3. TLS fingerprint through the proxy

Three readings of `https://tls.peet.ws/api/all`, so that each difference
has one cause:

| arm | where | mode | path |
|---|---|---|---|
| A | VM | headed (the design) | through the proxy |
| B | VM | `--headless=new`, same binary | through the proxy |
| C | host | Chrome for Testing, same version, `--headless=new` | direct |

A vs B measures headed against headless. B vs C measures the proxy.

```bash
sbx policy allow network --sandbox browser-s1 \
  "tls.peet.ws:443,pypi.org:443,files.pythonhosted.org:443"
# A: the headed Chrome from §2, read back over CDP with a tiny client
sbx exec browser-s1 sh -c '
  python3 -m venv /tmp/cdp && /tmp/cdp/bin/pip -q install websocket-client &&
  curl -sv https://tls.peet.ws/api/all -o /dev/null 2>&1 | grep -i issuer
  WS=$(curl -s --noproxy "*" -X PUT "localhost:9222/json/new?https://tls.peet.ws/api/all" | python3 -c "import json,sys; print(json.load(sys.stdin)[\"webSocketDebuggerUrl\"])")
  sleep 6
  /tmp/cdp/bin/python - "$WS" <<PY
import json, sys, websocket
ws = websocket.create_connection(sys.argv[1], suppress_origin=True)
ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": "document.body.innerText", "returnByValue": True}}))
print(json.loads(ws.recv())["result"]["result"]["value"])
PY' > 3-A.json 2> 3-A.err; echo "A RC=$?"
# B: same binary, headless, through the proxy
sbx exec browser-s1 sh -c 'google-chrome --headless=new --user-data-dir=$(mktemp -d) \
  --proxy-server=http://gateway.docker.internal:3128 --dump-dom https://tls.peet.ws/api/all' > 3-B.html; echo "B RC=$?"
# C: on the host, Chrome for Testing of the version step 1 recorded
cd ~/s1 && . ./1-version.txt && curl -fsSLO "https://storage.googleapis.com/chrome-for-testing-public/$V/linux64/chrome-linux64.zip" &&
  unzip -qo chrome-linux64.zip && ./chrome-linux64/chrome --headless=new --user-data-dir="$(mktemp -d)" \
  --dump-dom https://tls.peet.ws/api/all > 3-C.html; echo "C RC=$?"
```

Reading: `tls.ja4` and the HTTP/2 Akamai fingerprint from each arm, and the
certificate issuer the VM saw (`curl -v`).

- PASS: the issuer is a public CA (not the sbx proxy CA), and B's JA4
  equals C's. JA3 is randomised per connection by Chrome's extension
  order, so compare JA4, not JA3. A against B is recorded as its own
  reading, not assumed equal.
- FAIL: the issuer is the proxy's CA, or B and C differ (the proxy
  re-originates TLS). Browsing then needs a different egress path.
- ERROR: an arm did not produce JSON, or the versions differ (Chrome for
  Testing publishes every stable version; fetch the exact one).

## 4. Rebinding under a `**` rule

This is the reading ADR 0022 Decision 2 waits on. It needs a `**` rule
(temporary, `browser-s1` only; `sbx policy deny network --help` lists
`--sandbox`) and the private-range denies the draft proposed, so it tests
exactly what the draft would have shipped. The targets are services that
answer an unauthenticated GET with a recognisable body, at their real
bindings (`ss -ltn`, 2026-10-07; `docker-compose.yml`):

| target | binding | body that proves a reach |
|---|---|---|
| social bridge `/health` | `172.17.0.1:19826` | `{"ok": true}` |
| Ollama `/api/version` | `*:11434` | `{"version": ...}` |
| Prometheus `/-/healthy` | `127.0.0.1:9190` | `Healthy` |
| OmniRoute `/v1/models` | `*:20128` | JSON (401 also proves a reach) |

```bash
cd ~/s1
LAN_IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
NAMES="172-17-0-1.sslip.io 127-0-0-1.sslip.io $(echo "$LAN_IP" | tr . -).sslip.io"
# 4a. Positive control on the host, by IP: each target answers (else the
# probe below cannot tell a deny from a dead service).
for t in 172.17.0.1:19826/health 127.0.0.1:11434/api/version 127.0.0.1:9190/-/healthy 127.0.0.1:20128/v1/models; do
  printf "%s -> " "$t"; curl -s -m 5 -o /tmp/b -w "%{http_code} " "http://$t"; head -c 60 /tmp/b; echo
done > 4a-host.log
# 4b. The resolver: Control D may filter these names (a blocked name
# resolves to 0.0.0.0 and looks like a refusal).
for n in $NAMES; do echo "$n host=$(getent ahosts "$n" | head -1) public=$(dig +short @1.1.1.1 "$n")"; done > 4b-dns.log
# 4c. The rules, then a public positive control under them.
sbx policy allow network --sandbox browser-s1 "**"
sbx policy deny network --sandbox browser-s1 \
  "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,100.64.0.0/10,169.254.0.0/16"
sbx exec browser-s1 sh -c 'curl -s -m 10 -o /dev/null -w "%{http_code}\n" https://example.com/' > 4c-control.log
# 4d. Every name x target, by plain HTTP through the proxy AND by CONNECT (-p).
sbx exec browser-s1 sh -c '
  for h in '"$NAMES"'; do
    for p in 19826/health 11434/api/version 9190/-/healthy 20128/v1/models; do
      for mode in http connect; do
        f=""; [ $mode = connect ] && f="-p"
        printf "%s %s:%s -> " $mode $h $p
        curl -s -m 10 $f -o /tmp/b -w "%{http_code} " "http://$h:$p"; head -c 80 /tmp/b; echo
      done
    done
  done
  for ip in 172.17.0.1:19826/health 127.0.0.1:9190/-/healthy; do
    printf "literal %s -> " $ip; curl -s -m 10 -o /tmp/b -w "%{http_code} " "http://$ip"; head -c 80 /tmp/b; echo
  done' > 4d-rebind.log 2>&1
sbx policy log --json > 4e-policy.json
ss -tnp | grep -E ':(19826|11434|9190|20128) ' > 4f-proxy-owner.log   # run during 4d
```

Reading: for each name, target and mode, the HTTP status, the first 80
bytes, and the matching `sbx policy log` entry.

- PASS, per probe: status 403 from the proxy **and** a `denied` entry for
  that host and port in `4e-policy.json`. All probes must pass, and both
  controls (4a: every target answers on the host; 4c: `200` from
  example.com) must hold. Without the controls a dead service or a broken
  rule set reads as a pass.
- FAIL: any body from a target (the bodies in the table above), or any
  `allowed` log entry for a private address.
- ERROR: status `000` (no answer: DNS, timeout, or the proxy dropped it,
  which is not the same as a deny), a control that fails, or a name the
  host's resolver answers with `0.0.0.0` or nothing (4b). An ERROR is
  repeated with another wildcard-DNS service or a name of our own; it is
  never read as PASS.

Even a full PASS shows only that these names were refused today. The
vendor's help text says otherwise for allowed names in general, so a host
fence stays the expected answer (`03-open-questions.md` §1.2). `4f` records
which user and namespace the proxy connects from, which decides how the
fence is built.

## 5. Flash image passthrough

On norty, against OmniRoute with the deployed alias (`model = "nora"` in
`config.toml`), with a PNG holding a number the model cannot guess:

```bash
cd ~/s1
python3 - <<'PY' > 5-img.b64
import base64, io, random
from PIL import Image, ImageDraw, ImageFont  # host python3 has Pillow 12.1 (read 2026-10-07)
n = random.randint(100000, 999999); open("5-expected.txt","w").write(str(n))
img = Image.new("RGB", (400, 120), "white")
font = ImageFont.truetype("/usr/share/fonts/truetype/lato/Lato-Medium.ttf", 64)
ImageDraw.Draw(img).text((20, 20), str(n), fill="black", font=font)
buf = io.BytesIO(); img.save(buf, "PNG")
print(base64.b64encode(buf.getvalue()).decode())
PY
KEY=$(grep '^OMNIROUTE_API_KEY=' ~/workspace/nora/.env | cut -d= -f2-)
for arm in image noimage; do
  python3 - "$arm" > "5-req-$arm.json" <<'PY'
import json, sys
img = open("5-img.b64").read().strip()
content = [{"type":"text","text":"What number is written in the image? Reply with the digits only."}]
if sys.argv[1] == "image":
    content.append({"type":"image_url","image_url":{"url":"data:image/png;base64,"+img}})
print(json.dumps({"model":"nora","messages":[{"role":"user","content":content}],"max_tokens":64}))
PY
  curl -s http://127.0.0.1:20128/v1/chat/completions -H "Authorization: Bearer $KEY" \
    -H 'Content-Type: application/json' -d @"5-req-$arm.json" > "5-resp-$arm.json"; echo "$arm RC=$?"
done
cat 5-expected.txt; jq -r '.choices[0].message.content, .usage.prompt_tokens, .model' 5-resp-*.json
```

Reading: the answer, `usage.prompt_tokens` for both arms, and the model
that answered.

- PASS: the image arm answers the expected digits, AND its prompt tokens
  exceed the no-image arm's by an image's worth (hundreds). This second
  equation separates "saw the image" from a lucky guess.
- FAIL: a wrong number, or "I can't see an image". That is a 200, so
  OmniRoute will not fall back to another model (memory: combos fall back
  only on failure).
- ERROR: a non-200, or a `model` field naming a different upstream than
  the configured one. Record which model actually answered.

Run the image arm 5 times, each with a freshly generated number; one answer
decides nothing.

## 6. Exit IP

```bash
sbx policy allow network --sandbox browser-s1 "ipinfo.io:443"
sbx exec browser-s1 sh -c 'curl -s https://ipinfo.io/json' > 6-ip.json
curl -s https://ipinfo.io/json > 6-ip-host.json   # host direct, should match
```

Reading: `ip`, `org` (the ASN and its name), and whether VM and host exit
the same way. A consumer ISP's ASN is residential; a hosting ASN is
datacenter. Reputation: the operator looks the IP up on AbuseIPDB and
Scamalytics in a browser (no API key here), and records the score and
date. Then open `https://www.google.com/search?q=test` in the VM's Chrome
(allow `www.google.com`) and record whether a CAPTCHA appears. One page
is an anecdote, so record it as one.

## 7. Cleanup and read-back

```bash
sbx rm -f browser-s1; echo "RC=$?"
sbx policy ls | grep -c browser-s1        # expect 0
sbx ls --json | jq -r '.sandboxes[].name' # expect nora-computer only
sbx policy ls > 7-after.txt; diff 0-before.txt 7-after.txt; echo "DIFF_RC=$?"   # 0 = unchanged
```

PASS: no `browser-s1` sandbox or rule is left, and `DIFF_RC=0`: the policy
list reads exactly as it did before S1. If `sbx rm` leaves scoped rules behind,
remove each with `sbx policy rm` and note the behaviour.

## Results

Run 2026-10-07 on norty, sbx 0.46.0, in `browser-s1` built from
`nora-computer:latest`. Logs in `~/s1/` on norty.

| step | reading | verdict | log |
|---|---|---|---|
| 1 Chrome | Google Chrome 155.0.8059.39 from `dl.google.com`, with Xvfb, in the computer image | PASS | `1-install.log` |
| 2 memory | before Chrome 2,775 MiB available. 1 tab: PSS 574 MiB, 2,605 available; 3 tabs: 679 / 2,510; 5 tabs (Wikipedia ×2, BBC, GitHub, YouTube, all titled): **885 MiB PSS, 2,294 MiB available**; no OOM. RSS sums (1,307–2,271) double-count shared pages | PASS | `2-tabs.log` |
| 3 TLS | VM saw Let's Encrypt (real issuer). Chrome stable headed (A) = headless (B) = `t13d1517h2_8daaf6152771_cb7bf5808d99`. **Chrome for Testing 155.0.8059.39 gives `t13d1518h2_8daaf6152771_4980c97edce0` both in the VM through the sbx proxy (E) and on the host directly (C)**; a plain local CONNECT proxy changes nothing either (D). The proxy leaves the ClientHello intact; the stable-vs-CfT difference is one extension (4832) the build sends, not the path | PASS | `3-*.json/html` |
| 4 rebinding | controls held (every target answers on the host; `example.com` 200 under the rules; norty's resolver returns the private answers). Under `**` + every private CIDR denied: **`127-0-0-1.sslip.io` reached the social bridge (`{"ok": true}`), Ollama (`0.35.0`), Prometheus (`127.0.0.1`-only, "Healthy") and OmniRoute (401)**; `172-17-0-1` and `192-168-1-219` names reached what listens there, by plain HTTP and by CONNECT alike. IP literals were denied (`403 Blocked by local rule`). The proxy dials from the host's own network namespace, loopback included | **FAIL** | `4d-rebind.log` |
| 5 Flash vision | 5/5 random 6-digit numbers read exactly through OmniRoute's `nora` alias; prompt tokens 363 with the image vs 161 without, every run; upstream reports `deepseek-v4.1-flash` | PASS | `5-resp-*.json` |
| 6 exit IP | `116.49.51.47`, AS4760 HKT Limited, NETVIGATOR "Mass Internet" (residential), Hong Kong; VM and host exit the same. **One Google search from the VM's fresh profile got `/sorry/` (CAPTCHA)**: an anecdote, not a rate | residential; challenged once | `6-*.json`, `6-google.log` |
| 7 cleanup | `browser-s1` removed; `sbx policy ls` identical to the baseline (`DIFF_RC=0`); no pending approvals | PASS | `7-after.txt` |

**What S1 decides:**

- **Wide egress through sbx is ruled out** as designed: sbx's CIDR deny does
  not apply to an allowed name, exactly as its help says, and the reach
  includes loopback-only services. Under the operator's ruling ("open
  after S1's probe, else fence first"), the fence comes first. Its shape is
  in `00-design.md` §3.
- Chrome fits: 5 tabs leave 2.2 GiB of the 3 GiB VM. A second, logged-in
  Chrome beside room work is the next memory reading (B4).
- The proxy is not a fingerprint problem. Exit IP is residential, and Google
  still challenged a fresh CDP-driven profile once. B3's Patchright, nodriver
  and Camoufox arms are where that gets measured.
- Flash sees images through Nora's own route, so tier 4's vision needs no new
  provider.
