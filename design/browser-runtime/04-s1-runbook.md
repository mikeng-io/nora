# S1 runbook — measure before building (norty)

Status (2026-10-07): **written, not run.** Every step below names its
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
```

PASS: `RC=0` and a `Google Chrome <version>` line. Record the version: the
Dockerfile pins it later. (If the default `shell` template has no `sudo`,
run as root through `sbx exec -u root` and note it.)

## 2. RSS at 1, 3 and 5 tabs

Chrome is launched headed on Xvfb, as the design runs it, with a fresh
profile and the proxy given explicitly.

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
  sleep 5; curl -s localhost:9222/json/version'
```

Then for N in 1, 3, 5, open the first N of these URLs (one tab each) with
`curl -s -X PUT "localhost:9222/json/new?<url>"`, wait 20 s, and read:

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

```bash
sbx policy allow network --sandbox browser-s1 "tls.peet.ws:443"
# In the VM, through the proxy, the same headed Chrome:
sbx exec browser-s1 sh -c '
  curl -s -X PUT "localhost:9222/json/new?https://tls.peet.ws/api/all"; sleep 5
  curl -sv https://tls.peet.ws/api/all -o /dev/null 2>&1 | grep -i issuer'
# Read the page body back over CDP (Runtime.evaluate document.body.innerText),
# or open it once over noVNC and copy the JSON.
# On the host, the same Chrome version, direct:
google-chrome --headless=new --user-data-dir="$(mktemp -d)" \
  --dump-dom https://tls.peet.ws/api/all > 3-host.json
```

Reading: `tls.ja4` and the HTTP/2 Akamai fingerprint from both, and the
certificate issuer the VM saw (`curl -v`).

- PASS: the issuer is a public CA (not the sbx proxy CA), and the two JA4
  strings are equal. JA3 is randomised per connection by Chrome's
  extension order, so compare JA4, not JA3.
- FAIL: the issuer is the proxy's CA, or the JA4 strings differ (the proxy
  re-originates TLS); browsing needs a different egress path.
- ERROR: a version mismatch between host and VM Chrome; fix and re-run. A
  headless host read differs from headed only in the HTTP layer, not in JA4.

## 4. Rebinding under a `**` rule

This is the reading ADR 0022 Decision 2 waits on. It needs a `**` rule
(temporary, `browser-s1` only) and the private-range denies the draft
proposed, so it tests exactly what the draft would have shipped.

```bash
LAN_IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
sbx policy allow network --sandbox browser-s1 "**"
sbx policy deny network --sandbox browser-s1 \
  "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,100.64.0.0/10,169.254.0.0/16"
# The host's resolver first: Control D may filter these names (memory:
# a blocked name resolves to 0.0.0.0 and looks like a refusal).
for n in 172-17-0-1.sslip.io 127-0-0-1.sslip.io "$(echo $LAN_IP | tr . -).sslip.io"; do
  getent ahosts "$n" | head -1; dig +short @1.1.1.1 "$n"
done > 4-dns.log
# Then each through the proxy, from the VM, by body:
sbx exec browser-s1 sh -c '
  for h in 172-17-0-1.sslip.io 127-0-0-1.sslip.io; do
    for p in 19826/health 11434/api/version 9090/-/healthy; do
      printf "%s:%s -> " $h $p; curl -s -m 10 -o /tmp/b -w "%{http_code} " "http://$h:$p"; head -c 80 /tmp/b; echo
    done
  done' > 4-rebind.log 2>&1
# And the IP literals, for comparison (the deny must hold for these):
sbx exec browser-s1 sh -c 'curl -s -m 10 -w "%{http_code}\n" http://172.17.0.1:19826/health' >> 4-rebind.log
sbx policy log --json > 4-policy.json
```

Also probe the LAN IP's name with the same ports.

Reading: for each name and port, the HTTP status and the first 80 bytes.

- PASS: every name gets the proxy's 403 (or an empty reply) and
  `sbx policy log` records it as denied. The draft's deny list holds.
- FAIL: any body from a service (`{"ok": true}` from the bridge, an Ollama
  version JSON, Prometheus's `Healthy`). Wide egress then waits for a
  host-side fence (`00-design.md` §3), and this log is the reason.
- ERROR: the host's resolver answers `0.0.0.0` or nothing for a name (the
  filter, not sbx, refused it). Repeat with another wildcard-DNS service
  or a name of our own; a filtered resolver is not evidence that sbx holds.

Also record which user and network namespace the proxy connects from
(`ss -tnp` on the host during a fetch); a host firewall fence depends on it.

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
sbx policy ls | grep nora-computer        # expect "network: 6 allow", unchanged
```

PASS: no `browser-s1` sandbox or rule is left, and `nora-computer`'s rule
count is what it was before S1. If `sbx rm` leaves scoped rules behind,
remove each with `sbx policy rm` and note the behaviour.

## Results

Not run. Fill in per step: date, the reading, PASS / FAIL / ERROR, and
the log file it came from.

| step | reading | verdict | log |
|---|---|---|---|
| 1 Chrome version | | | |
| 2 RSS / MemAvailable at 1, 3, 5 tabs | | | |
| 3 JA4 VM vs host, issuer | | | |
| 4 rebinding, per name and port | | | |
| 5 Flash image, 5 runs, token delta | | | |
| 6 exit IP, ASN, reputation | | | |
| 7 cleanup | | | |
