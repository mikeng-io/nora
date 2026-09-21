---
type: design
title: "Instagram Bridge — Container, Session, and the Degradation That Must Land First"
description: "The instagram-bridge compose service: a Go daemon wrapping instameow, cookie session login, state resume, and the AdapterRegistry change that stops one Instagram captcha from taking Signal and Discord down with it."
tags: [nora, instagram, instameow, docker, container, session, degradation]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: ../whatsapp-adoption/01-bridge-container.md, rel: precedent, provenance: asserted}
  - {dst: 03-transport-contract.md, rel: details, provenance: asserted}
---

# Instagram Bridge — Container, Session, Degradation

## Service shape (mirrors whatsapp-bridge, which mirrored signal-cli)

A new compose service in the **same project** as Nora, on the **private
`nora` network**, publishing **nothing**. Co-networked containers reach it as
`instagram-bridge:PORT`; no host port map. This is the third instance of a
settled pattern, not a new one.

The difference from `whatsapp-bridge` is the language and the auth story:
Go rather than Node, and a cookie session rather than a QR pairing.

```yaml
services:
  instagram-bridge:
    build:
      context: ./deploy/instagram-bridge
    image: instagram-bridge:0.1.0
    container_name: instagram-bridge
    restart: unless-stopped
    networks: [nora]
    volumes:
      - instagram-session:/session   # cookies + reconnection state
    environment:
      - IG_SESSION_DIR=/session
```

### go.mod must copy the upstream replace

```
require go.mau.fi/mautrix-meta vX.Y.Z
replace github.com/imroc/req/v3 => github.com/beeper/req/v3 v3.0.0-20260808092153-100cef0a2fbd
```

Go ignores `replace` directives from dependencies. Without this line the
build fails on `quic-go` with eleven `undefined: quic.ConnectionTracingID`
errors and no hint as to why. Verified 2026-09-21.

## Session and login

`instameow` offers two login flows (`pkg/igconnector/login.go`):

- **`instagram` — cookies.** Log in at instagram.com in a private window,
  take any `graphql` XHR, "Copy as cURL", extract the cookie header. Required
  cookies are `sessionid`, `csrftoken`, `ds_user_id`.
- **native password** (`login_native.go`) — username + password, handling
  TOTP / email / SMS / WhatsApp 2FA, auto-accepting cookie consent, and
  detecting suspended or scheduled-for-deletion accounts. It explicitly
  **cannot drive CAPTCHA**; that needs a human in a webview.

**Use cookies.** A password login originating from a server is the highest-risk
first contact an account can make. Warm the account through a browser on the
normal residential IP, then hand the bridge the resulting session.

### State resume — and the correction that `LoadState` alone does NOT resume

`Client.DumpState()`/`LoadState()` persist the HTTP/reconnection state
(~6 KB measured), and the client emits `ReconnectionStateUpdate` whenever it
changes. The bridge writes that to `/session` atomically and loads it at boot.

**`LoadState` does not restore the sequence id.** An earlier revision of this
document said that `HasSeqID()` becomes true after `LoadState` and the client
then resumes the stream. It does not: the seq arrives through
`ClientParams.SeqID` / `SetSeqID`, which is why the bridge connector persists
it *separately* in its own database and calls `SetSeqID` before connecting
(`pkg/igconnector/client.go`) — the `LoadState` branch there only works
because that has already happened.

So a bridge must persist **two** things: the reconnection state from
`ReconnectionStateUpdate`, and the sequence id from `SeqIDUpdate`. Persisting
only the first produces a process that looks like it resumes and silently does
a full `LoadIndex` on every boot.

Found by the bridge implementation, 2026-09-22, and it had already bitten:
`~/workspace/ig-soak/igsoak` checks `HasSeqID()` after `LoadState` and has
therefore logged `resumed: false` on every start. That does not invalidate the
soak's uptime figure — it has run as one continuous session since boot and
never restarted — but "resume works" was never among the things it measured.

## Pairing state is a FIELD, never a status code

Inherited verbatim from `whatsapp-bridge` and it matters more here.
`GET /health` returns 200 while the HTTP server serves, and reports session
state in the body. Instagram challenges are routine, so a checkpointed
session is a **normal operating state** that the bridge must describe rather
than crash on:

```json
{"ok": true, "session": "authenticated" | "checkpointed" | "logged_out",
 "connected": true, "seq": 22, "detail": "..."}
```

## Degradation — lands WITH the adapter (M2), not before it

**Ordering corrected 2026-09-22.** An earlier revision of this section, and
`00`'s sequencing, said this must land "before any Go is written". `11` is
right and they were wrong: a degradation path with no adapter to degrade is
machinery with no user, which this repo has an explicit rule against (ADR
0008). The bridge (M1) has shipped and `registry.py` still has no `instagram`
block — the tree already follows `11`.

`nora/platform/registry.py` is fail-fast by design: `from_config` raises
`AdapterRegistryError` for any enabled platform that cannot be built or
cannot resolve its own identity, and `build_dependencies` turns that into a
`StartupError`. That is correct for Signal and Discord, where failing to
resolve identity means a misconfiguration.

It is wrong for Instagram, where it means *a captcha happened overnight*.
Under the current rule, one Instagram checkpoint prevents Nora from starting
**at all** — Signal and Discord included.

Required before any bridge code:

1. An enabled Instagram adapter that cannot authenticate must be **built and
   registered in a degraded state**, not refused. It answers no sends and
   emits no events, and says so.
2. The degraded state must be visible — a log line at startup and a field in
   whatever Nora already exposes for platform health — because a silently
   degraded adapter is indistinguishable from a quiet room.
3. Identity resolution stays fail-fast for Signal and Discord. This is an
   Instagram-shaped exception with a stated reason, not a general loosening.

Note that WhatsApp carries the same latent exposure and has simply never hit
it, because QR pairing is stable. Whether this change should generalise to
WhatsApp is a decision for its own bundle, not a side effect of this one.

## Operational notes

- **Run it where the account already lives.** Datacenter IP ranges are
  blocklisted and a location jump triggers a trust check almost every time.
  norty is on the same residential connection as the browser that will create
  the session, which is the best available answer.
- **Do not run a second bridge instance against the same account.** Untested,
  and the obvious way to provoke a checkpoint.
- **Session longevity is unmeasured.** A soak is running on norty since
  2026-09-21 (`~/workspace/ig-soak/`, `status.sh` for a fail-closed
  read-back). Its number decides the restart and re-auth policy, so that
  policy is deliberately not specified here.
