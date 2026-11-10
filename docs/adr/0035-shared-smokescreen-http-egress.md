# 0035 · Smokescreen is the shared HTTP egress destination firewall

- **Status:** accepted (operator, 2026-11-05); implementation and rollout pending.
- **Date:** 2026-11-05
- **Reverses, in part:** [0022](0022-noras-browser-is-headed-chrome-with-a-vault-fill-broker.md)
  Decision 2's “No extra proxy”, for the target HTTP egress architecture.
  Existing deployed behavior and historical measurements remain unchanged.
- **Design:** [browser runtime](../../design/browser-runtime/README.md),
  [sandbox runtime](../../design/sandbox-runtime/00-design.md#8-credentials-and-network-from-sbx).
- **Tracking:** [issue #520](https://github.com/mikeng-io/nora/issues/520).
- **Index:** none; this ADR ships no runtime behavior. Allocate a DEC row when
  implementation lands, and reconcile DEC-289's enforcement claim then.

## Context

The browser S1 experiment recorded that sbx's private CIDR denies block IP
literals but permit a public hostname resolving to a private destination. That
limit was accepted in ADR 0022. The November security audit separately found
DNS check/use gaps in preview/media fetches and downloader paths that could
follow redirects or extractor requests outside the initial URL check.
[PR #591](https://github.com/mikeng-io/nora/pull/591) proposes connection validation
and a confined downloader with a custom proxy; it is an unmerged candidate,
not evidence of deployed remediation.

The operator accepted Smokescreen as the initial HTTP egress firewall on
2026-11-05, after asking whether an existing solution could serve downloads
and VM/browser traffic together. One maintained destination-policy engine is
preferable to extending a custom HTTP/CONNECT implementation across clients.
Host compromise and ordinary same-host service HTTP remain outside this threat
model; this is protection against untrusted URLs and sandbox web traffic reaching
private destinations.

Evidence from the 2026-11-05 investigation:

- Inspected [Smokescreen source](https://github.com/stripe/smokescreen/tree/5b4b7aabca9bc70a8435358d648edd03294c299e):
  resolution selects an allowed numeric address and connection uses that address.
- The official [v0.1.0 binary](https://github.com/stripe/smokescreen/releases/tag/v0.1.0)
  passed a local smoke probe: public HTTP/HTTPS succeeded; private HTTP and
  CONNECT were refused; a synthetic private origin's hit count did not increase.
  This was a macOS probe, not the candidate Linux image or a Nora integration.
- Installed sbx v0.46.0 exposes sandbox-specific upstream settings. Docker's
  [upstream proxy documentation](https://docs.docker.com/ai/sandboxes/configuration/upstream-proxy/)
  describes experimental support, separate from daemon traffic, and a daemon
  restart to update existing sandbox network proxies.

The inspected main revision and release binary differ. These observations do
not prove DNS-rebinding resistance, confinement or browser compatibility in the
complete Nora chain. The exact Linux artifact remains to be selected and tested.

## Decision

### One service and one policy owner

Add one private Compose service named **`http-egress`**, running **Smokescreen**.
It owns public destination validation for HTTP forward requests and HTTPS
CONNECT. The abstraction is the standard proxy protocol and its endpoint;
no new generic security framework, client SDK or policy-controller layer.

```text
Portal yt-dlp / ffmpeg -> confined launcher + relay -> http-egress -> public destination
VM Chrome / HTTP clients -> existing sbx proxy -> http-egress -> public destination
```

Initially replace the downloader's custom filtering proxy with a fixed relay
while retaining the per-job network boundary. Smokescreen alone does not remove
PR #591's user-namespace/seccomp prerequisite. A separate downloader worker
would be another design choice, not an implied extra service in this decision.
Preview/media connection validation remains required until those callers are
explicitly migrated and tested; adding a service does not route them automatically.

Retain sbx's policy, control and credential handling. Use its sandbox upstream;
do not send host/daemon traffic or normal internal Nora service calls through a
public-only gateway. Each scoped VM must retain its own admission, workspace,
browser/tab ownership and network policy. A shared gateway does not merge scopes.
Nora's evidenced browser is Chrome; a separate Comet installation is unverified.

### Policy and exposure

- Validate the actual connected IP, including IPv6 and mapped forms; refuse
  private, loopback, link-local and other non-public destinations. A mixed DNS
  answer may select a validated public IP; the requirement is the connected
  destination, not rejection of every mixed answer set.
- Public domains remain usable without a curated domain allowlist. Review
  destination ports, exemptions and onward-proxy headers on the pinned version;
  no private-range bypass or arbitrary client-selected upstream proxy by default.
- No TLS interception or replacement CA. Preserve hostname, SNI and certificate
  verification through CONNECT. This does not inspect malware, prevent prompt
  injection, authorize website actions or stop exfiltration to public sites.
- No publicly reachable proxy listener. App clients use the private Compose
  endpoint `http-egress:4750`. Restricted host access for sbx must be verified;
  loopback-only publication is a candidate, not an established deployment fact.
  No new internal mTLS requirement is introduced.
- Clients cannot dial around the gateway. Environment variables and browser
  proxy settings alone are insufficient. Proxy failure must refuse protected
  fetches; no DIRECT fallback. Explicitly handle NO_PROXY, browser bypasses and
  direct IPv4/IPv6 sockets in the confinement boundary.
- This is an **HTTP egress firewall**, not a firewall for every VM protocol.
  DNS/control traffic has its existing owner; UDP, QUIC/HTTP3 and WebRTC need
  explicit treatment. Do not claim they are filtered by an HTTP proxy.

### Implementation sequence and acceptance

1. Select a pinned, reviewed Linux image digest or build a pinned source revision
   through the existing release/cohort and Komodo path. Check supported options,
   port policy, onward-proxy behavior, timeouts and non-secret logging there.
2. Exercise one disposable downloader and sbx/browser integration. Preserve
   original authority, TLS verification and sbx policy/credential behavior.
   Keep PR #584 packaging/install/rollout held; this document authorizes no host
   restart, profile installation, Stack edit or production deployment.
3. Prove public HTTP/HTTPS success and private destination refusal with a
   positive origin control. Cover DNS changes, mixed answers, redirects,
   extractor/HLS/DASH secondary requests, ffmpeg, long transfers and WS/WSS.
4. Prove confinement with the proxy unavailable, ignored proxy variables,
   NO_PROXY, browser loopback/PAC bypass, direct IPv4/IPv6 and sbx's transparent
   path. Observe destination hits/data, not only successful TCP handshakes.
   Measure UDP/QUIC behavior separately; preserve legitimate local control paths.
5. Check cancellation, resource bounds, sanitized logs, restart behavior and
   cross-scope refusal. Prepare exact host/Stack changes and rollback before
   requesting rollout approval; existing-sandbox proxy updates may require a
   disruptive daemon restart. Read back deployed versions and real fetches.

Implementation is complete only when those checks bind to the shipped candidate;
local product smoke results are not production acceptance. Telemetry ingress,
resource-exhaustion findings and unrelated advisory dispositions remain separate.

## Consequences

**Better:** one maintained destination gate can serve downloads and multiple
VMs; standard clients need no Nora-specific filtering library. A resolved-IP
boundary addresses the hostname-to-private gap the earlier design accepted.

**Worse:** one extra container becomes a shared availability and capacity
dependency. Proxy chaining and experimental sbx upstream support require real
integration evidence. Downloader confinement still has host prerequisites;
long downloads, streaming sockets and browser behavior may need tuning.

This decision holds unless the measured chain loses destination authority,
permits bypass, breaks required sbx/browser/credential traffic or costs more to
operate than the validated smaller boundary. Such evidence reopens the design;
it does not justify silently falling back to unrestricted egress.

## Rejected

- Extend the custom proxy into a shared networking product: duplicates maintained
  protocol and policy machinery without a demonstrated Nora-specific need.
- Set HTTP_PROXY everywhere and call it a firewall: cooperative configuration
  does not constrain programs that ignore it.
- Add a service mesh or general firewall platform now: the demonstrated need is
  public HTTP destination enforcement, with existing isolation retained.
- Remove the old boundary before integration passes: product smoke results do
  not prove that Nora's processes cannot bypass it.
