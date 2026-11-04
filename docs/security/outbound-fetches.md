# Public destination boundary for previews, media and portal downloads

External preview/media URLs and the portal's generic downloader may connect only
to public destinations. This is a connection property, independent of content
filter settings. Internal service HTTP and the separately accepted computer
sandbox limitations remain unchanged.

## Preview and media

`nora.infra.public_http.PublicTCPConnector` checks every numeric answer returned
by aiohttp's connection resolver, including cached answers and IP literals. An
empty answer set or any non-global, multicast or reserved address refuses the
connection. IPv4-mapped IPv6 addresses are judged by their embedded IPv4 address.
The connector passes the vetted numeric addresses to aiohttp without rewriting
the URL: Host, TLS SNI and certificate hostname verification remain intact.

Both `OpenGraphPreviewSource` and the generic vision downloader use this
connector for the complete session. Their existing URL/hop checks and byte/time
ceilings still apply. Redirect destinations and preview images use the same
connection policy. A failed connection yields no preview or unavailable media;
it never supplies guessed metadata.

## Portal downloader

Every probe, download and conversion goes through
`app/scripts/public-download.py`. This stdlib launcher starts a per-invocation
HTTP proxy on a private Unix socket and puts yt-dlp/ffmpeg in fresh Linux user,
network and PID namespaces. The network namespace has only loopback. A local
relay reaches the Unix proxy; there is no direct IP route out of the namespace.
All descendants inherit that boundary, including downloader or ffmpeg processes
that ignore proxy settings or use another protocol. Those attempts fail closed.

The proxy resolves each destination once, rejects the whole answer set if any
address is non-public, then connects to a numeric sockaddr from that exact set.
HTTP requests retain the target Host. HTTPS CONNECT establishes a tunnel only to
a vetted public address; TLS remains end-to-end and the downloader performs its
normal certificate and hostname verification. Redirects, extractor requests,
manifests, media and thumbnails each go through this boundary. yt-dlp's ambient
configuration is ignored so it cannot replace the required proxy.

The original submission and queued-job checks remain early error feedback. They
are not the connection boundary. The generic downloader remains available for
public sites, with its existing byte/time/concurrency limits.

## Deployment prerequisite: operator approval required

The image installs Python and util-linux and copies the launcher. No host
setting is changed by this source change. Docker's [default seccomp profile](https://docs.docker.com/engine/security/seccomp/)
usually refuses `unshare`; until the prerequisite is installed, jobs fail with an
unavailable process result rather than falling back to an unconfined download.
The namespace path also requires enabled unprivileged user namespaces and host
LSM permission to create them. No host capability is granted to the portal.

Prepare the profile from the **installed Docker version's default deny profile**:

```sh
python3 deploy/download-egress/prepare-profile.py docker-default.json nora-download-seccomp.json
```

The script preserves the default profile and adds only `unshare`. Review the
result, then have the operator install it as
`/etc/docker/nora-download-seccomp.json` and include
`deploy/download-egress/compose.override.yml` in the installed Stack's Compose
inputs. Preserve all other Stack inputs. Do not set `privileged`, add host
capabilities, or use an unconfined production seccomp/AppArmor profile. If the
host's AppArmor/user-namespace policy denies this, that host policy is a further
explicit deployment dependency; do not disable it globally to make a job work.
The ordinary image release alone does not install this host prerequisite.

Before applying to production, run the synthetic check below with the candidate
profile and production UID on a disposable local container. Require public
success, private refusal, HTTPS hostname verification and direct-bypass refusal.
Also verify cancellation leaves no namespace descendants. No successful source
check establishes that the installed Stack uses the reviewed profile.

## Focused regression evidence

`tests/integration/test_public_fetches.py` exercises real aiohttp preview/media
requests with controlled DNS and a socket-level route for a synthetic public
address. It observes server hits, including public success, denied DNS changes,
redirect/image destinations, literals/mixed answers, Host/SNI and certificate
hostname mismatch. It never sends to a real public host.

`tests/integration/probe_public_download.py` exercises real yt-dlp and ffmpeg
through the launcher, a synthetic origin and controlled DNS. Run it in a
disposable Linux container with no external network, ffmpeg/openssl/iproute2 and
the repository's locked yt-dlp package available on PYTHONPATH. Assign the
synthetic public address locally with
`ip addr add 93.184.216.34/32 dev lo`, then run
`python3 tests/integration/probe_public_download.py`. NET_ADMIN is needed only
for that outer fixture address, never by the application. The child downloader
uses the ordinary unprivileged namespace path. Local fixture permissions are
not evidence of production namespace permission. Neither check is a live exploit
or a claim about aggregate storage quotas.

The ordinary suite includes `tests/integration/test_public_download.py`, which
builds this disposable fixture from `tests/fixtures/download-egress/Dockerfile`
and the installed locked yt-dlp package. It deliberately relaxes seccomp only
inside that disconnected test container to exercise the namespace mechanism;
validation of the actual candidate production profile is a separate named check.
