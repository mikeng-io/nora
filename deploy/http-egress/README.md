# Shared HTTP egress

ADR 0035 implementation candidate. **Blocked on plain WS compatibility.**
No rollout or sbx activation has occurred. The real Linux amd64 integration
passes direct plain WS and proxied WSS bidirectional Upgrade controls, but
plain WS through this pinned gateway times out. Its goproxy HTTP path removes
`Connection` and copies an ordinary response body without a bidirectional
Upgrade tunnel. The integrated test intentionally remains failing. Do not adopt
this candidate or waive the ADR requirement; upstream compatibility/design
resolution belongs to the parent. No custom protocol fork was added.
The maintained Smokescreen module is pinned to source
`5b4b7aabca9bc70a8435358d648edd03294c299e` in go.mod/go.sum. No fork or
vendored protocol implementation. Both builder and nonroot distroless runtime
are digest-pinned. The normal release publisher builds `http-egress`, records
its digest/provenance and renders it into Komodo's cohort. The context and
reviewed recipe hash participate in reuse. Previous cohorts without the service
remain readable; no Stack edit is performed here.

The small Go entry point sets fixed policy: public connected IP (Smokescreen's
built-in deny ranges), no private exemptions, ports 80/443, no client-selected
onward proxy, no TLS interception. No external ACL means public domains are
usable without per-domain enrollment. No upstream proxy environment is read by
this entry point. Upstream request logs may contain secrets, so they are
suppressed entirely; only a fixed process-stop message is emitted. No authority,
URL, credential, scope or query is logged. Failure visibility is the client's
failed job and container process state; no per-request operational metric exists.

Compose has no published port, host network, Docker socket or added capability.
The service is read-only, UID 65532, all capabilities dropped, no-new-privileges,
128 PIDs and 256 MiB. It shares the existing private Nora network for the app;
ordinary internal HTTP is unchanged. Five-minute inactivity bounds relay/tunnel
connections; active streams have no total gateway timeout. The downloader's
existing job timeout/concurrency/byte limits remain. Other authenticated internal
clients can use this shared service; it does not replace per-scope authorization.

## Installed ownership prerequisite

The host's trusted `komodo_deploy.py` is outside the fetched release clone.
It must receive the reviewed cohort update under the existing deploy lock before
this cohort can apply; the previous installed wrapper rejects the new service.
Back up the installed script, preserve sibling helpers/control state, compare
its hash with the candidate and validate both old/new manifests before enabling
adoption. Application releases do not install it. This PR makes no host edits.

## Downloader prerequisite and rollback

Keep the [namespace/seccomp prerequisite](../../docs/security/outbound-fetches.md).
No app capability is added. Until an operator installs and validates the narrowly
extended default seccomp profile and user-namespace/LSM permissions, downloads
refuse rather than run unconfined. Test as the deployed UID against the actual
profile before rollout. An internet-disconnected fixture using unconfined
seccomp proves mechanism only. Do not use that setting in production.

Rollback selects the previous complete release cohort through the existing
release procedure, restores previous operator Compose inputs/profile if changed,
and verifies failed jobs remain explicit. Never bypass the launcher or turn off
public destination validation to restore availability. Rollback is an operator
operation, not performed by this PR.

## sbx preparation only (PR584 remains held)

Installed research target: sbx 0.46.0. The optional `sbx.override.yml` publishes
only `127.0.0.1:4750`. Do not apply it until the actual sandbox proxy's route to
host loopback has been verified. Never substitute 0.0.0.0 or a public listener.
After approved disposable integration and a maintenance window, use the installed
CLI's sandbox-specific `proxy.sandbox` setting with that verified HTTP endpoint,
and review `proxy.sandbox.no_proxy` for bypasses. Preserve `proxy.daemon`, host
proxy configuration and existing sbx admission/domain/credential/control policy.
Docker documents upstream support as experimental; existing sandbox network
proxies require daemon restart. No helper here changes settings or restarts it.
Record prior settings privately for rollback, restore those settings and rebuild
proxies only with operator authorization. No production values belong in Git.

Before activation measure explicit AND transparent paths, original authority,
remote DNS, TLS/certificates, browser loopback/PAC/DIRECT bypass, credential
injection and local control traffic. A header/environment setting alone proves
none of these. HTTP/CONNECT does not filter DNS, UDP, QUIC or WebRTC; inspect and
measure sbx's separate policy. Do not claim all-network confinement from this
service. Cross-scope VM/browser ownership remains sbx/Nora's responsibility.

## Acceptance boundary

The integrated Linux fixture is a focused minimum, not full ADR acceptance.
Before adoption bind DNS-change/mixed DNS, HLS/DASH/thumbnail/secondary fetches,
WS/WSS, long transfers, ignored environment/NO_PROXY/direct IPv4/IPv6/proxy-dead,
restart/cancellation and sbx/browser checks to the exact candidate. Use synthetic
owned origins with positive destination hit controls. Record unmet or unavailable
rows explicitly; host activation and release ownership do not waive them.
