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
`app/scripts/public-download.py`. This stdlib launcher starts a fixed byte
relay on a private Unix socket and puts yt-dlp/ffmpeg in fresh Linux user,
network and PID namespaces. Only loopback exists there; its local relay reaches
the Unix socket. The outer relay connects only to `http-egress:4750`. Downloader children receive
only named runtime environment variables (path, home, temp, locale and CA paths),
plus the fixed proxy settings; app credentials are not inherited. Independent
copy pumps preserve bidirectional traffic under backpressure. The relay does not
interpret HTTP, resolve destinations or accept a client-selected upstream.
All descendants inherit the boundary, including clients ignoring proxy settings.

The shared Smokescreen service selects a vetted public numeric address and dials
that address. Mixed DNS may select a public answer. HTTP Host and HTTPS tunnel
TLS/SNI/certificate verification remain end-to-end. Only destination ports 80
and 443 are supported. Client onward-proxy headers are refused. yt-dlp ignores
ambient config; no direct fallback exists. Preview/media retain their stricter
whole-answer-set connection guard; they do not automatically use the service.

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

`tests/integration/test_public_download.py` builds the exact pinned gateway
recipe and a disposable Linux launcher fixture on an internet-disconnected
network with an owned synthetic public-address origin. It runs real yt-dlp and
ffmpeg as UID 1000, observes destination hits and TLS hostname verification,
and checks namespace cancellation and direct bypass. The test relaxes seccomp
only in the disposable fixture. That is not production profile acceptance.

See [shared gateway operations](../../deploy/http-egress/README.md) for packaging,
sbx preparation, limits and rollback. DNS/stream/browser obligations not measured
by this check remain rollout prerequisites; a green source test is not adoption.
