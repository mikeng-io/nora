# Aikido high/critical triage — 2026-10-22

Scope: the 36 open critical/high findings Aikido reports for
`mikeng-io/nora`, triaged against a fresh worktree of
`origin/master` at `9d9223c1fc7ca972bc94b5b0ba7d176adb0c8724`
(branch `mikeng-io/aikido-high-triage`). Every finding was opened
individually; findings that name a file were traced to the code that actually
runs, not judged from the scanner's title.

Findings are identified by Aikido's nine-digit ids, shown grouped as
`8765_44859` (remove the underscore to search) so the repository's phone-shape PII
hook does not read an issue id as a phone number. The public origin is written
defanged for the same reason.

No secret value is reproduced here. Aikido's IDE-plugin detail endpoint returns
`issue_details: []` for this workspace's `surface_monitoring` findings, and the
UI API requires an interactive session cookie. **The scanner's actual target is
therefore UNVERIFIED**: it is not proven to be this repository, this commit, or
the public origin below. Findings that name a file are bound to the call site in
this tree; the five browser-surface verdicts are bound to the observed public
origin and the checked-in server configuration, not to a confirmed Aikido
target.

## Verdict summary

| Verdict | Count | Finding IDs |
|---|---:|---|
| Source fix, attribution verified against the repository call site | 7 | 8764_80683, 8765_44886, 8765_44879, 8765_44824, 8765_44859, 8765_44812, 8764_79878 |
| Conditional source remediation, scanner attribution UNVERIFIED | 4 | 8764_96415, 8764_96419, 8764_96411, 8764_96436 |
| Source reasoning only, scanner attribution UNVERIFIED | 1 | 8764_96431 |
| Already fixed on `origin/master` | 2 | 8764_80796, 8764_82911 |
| False positive | 20 | 8764_82355, 8764_82525, 8764_82516, 8764_82513, 8764_82495, 8764_82489, 8764_82485, 8764_82479, 8764_82477, 8764_82471, 8764_82465, 8764_82460, 8764_82378, 8764_82372, 8764_82366, 8764_82365, 8764_82360, 8764_82704, 8764_82668, 8765_44892 |
| Intentional risk, not changed | 2 | 8765_44787, 8764_79880 |

The 7 verified fixes trace to code that runs in this tree. The 4 conditional
changes really do harden the headers and cookie this repository serves, but
without the scanner target they cannot be called scanner-confirmed fixes. The
locale-cookie (`NEXT_LOCALE`) reasoning is source-based and its attribution is
likewise UNVERIFIED, so it is not counted as a confirmed false positive either.

Deploying the changes is a separate step: the portal and site images must be
rebuilt, and the public edge re-checked. That read-back would show the headers
are live; it would not prove what the scanner scanned.

## Conditional source remediation — scanner attribution UNVERIFIED

These four findings point at browser-surface behaviour. The repository changes
are real, but the scanner's target (origin, deployment, commit) could not be
retrieved, so "confirmed fixed" would overclaim. Each is a conditional
remediation: it removes the unsafe shape from the code this repository ships,
and remains UNVERIFIED against Aikido's actual target.

### 8764_96415 — Content-Security-Policy header not set (critical, surface)

- **Reported:** no CSP on the public surface.
- **Evidence:** `https://nora[.]67[.]zone/` returned `200` with
  `permissions-policy`, `referrer-policy` and `x-content-type-options`, but no
  `content-security-policy` (checked 2026-10-22). The portal's fallback headers
  were absent from `app/next.config.ts`; the site had no baseline either.
- **Conditional remediation:** `app/next.config.ts:11-29` adds one CSP with
  `default-src 'self'`, object/frame/form restrictions and the explicit
  allowances Next hydration, inline styles and the map tiles need.
  `site/Caddyfile:12-23` carries the equivalent baseline for the static site,
  and `:37-40` for its 404 handler. Both Caddy policies also add
  `'wasm-unsafe-eval'` to `script-src` and `worker-src 'self' blob:` so the
  wiki's Pagefind search can compile WASM and start its index worker.
- **Verification:** built `docs-site` with Pagefind; under the new policy the
  search dialog loaded `pagefind-worker.js`, `wasm.en.pagefind` and returned
  **5 results for `security`**, with no WASM/worker CSP violation. Under the old policy the
  same build logged `Failed to load the Pagefind WASM: CompileError ...
  violates ... script-src 'self' 'unsafe-inline'` and stayed on
  `Searching for security...`.
- **Attribution:** UNVERIFIED. The live origin response was observed, but the
  scanner's target is not retrievable.
- **Trust boundary:** browser execution in a visitor's origin. A missing policy
  is not a server-side authorization boundary; it is the last line that limits
  an injected script's reach.
- **Reversal:** if a real page breaks, add the narrowest directive it needs;
  do not remove `default-src`, `object-src`, `frame-ancestors` or `base-uri`.

### 8764_96419 — CSP policy does not define a fallback directive (high, surface)

- **Reported:** CSP has no fallback for unnamed directives.
- **Evidence:** same live response as 8764_96415; the previous policy (where
  present) did not begin with `default-src`.
- **Conditional remediation:** `app/next.config.ts:11-25` and
  `site/Caddyfile:17` both start with `default-src 'self'`, so a future resource
  class is denied until explicitly allowed rather than silently unrestricted.
- **Trust boundary / reversal:** same as 8764_96415. Reversal is a named,
  reviewed directive addition, never dropping the fallback.
- **Attribution:** UNVERIFIED for the same reason: the policy is real, the
  scanner's target is not.

### 8764_96411 — HSTS header is missing (high, surface)

- **Reported:** no `Strict-Transport-Security`.
- **Evidence:** the same live `https://nora[.]67[.]zone/` response had no
  `strict-transport-security`.
- **Conditional remediation:** `app/next.config.ts:29` and
  `site/Caddyfile:18` send `max-age=31536000`. The 404 handler in Caddy sends it
  too (`:39`).
- **Trust boundary:** transport downgrade between browser and origin. The
  public edge terminates TLS; HSTS is what makes the browser refuse a later
  plaintext navigation.
- **Reversal:** keep HSTS while the origin is HTTPS-only. Do not add
  `includeSubDomains` or `preload` without auditing every subdomain; neither is
  required by this finding.
- **Attribution:** UNVERIFIED; the live response was observed, the scanner's
  target was not.

### 8764_96436 — Cookie can be sent over an unencrypted connection (high, surface)

- **Reported:** a cookie lacks `Secure`.
- **Evidence:** live `https://nora[.]67[.]zone/zh-Hant/login` set
  `NEXT_LOCALE=…; Path=/; SameSite=lax` with no `Secure`. The NextAuth session
  cookies (`__Host-authjs.csrf-token`, `__Secure-authjs.callback-url`) already
  carried `HttpOnly; Secure; SameSite=Lax`; this finding is the locale cookie.
- **Conditional remediation:** `app/src/i18n/routing.ts:17` sets
  `localeCookie: { secure: true }`.
- **Attribution:** UNVERIFIED. The live header was observed, but the scanner's
  target and commit are not retrievable (`issue_details: []`).
- **Trust boundary:** transport confidentiality of browser state. The locale
  value is not a credential, so this is a hardening fix, not a session leak.
- **Reversal:** keep `Secure` whenever the portal is served over HTTPS; a
  plaintext-only local development origin is not the deployed surface.

## Source fixes, attribution verified

The seven findings below name code in this tree and were traced to the caller
that runs. Their remediation is testable without the scanner's target.

### 8764_80683 — AIKIDO-2026-934590 in Go (high, open source)

- **Reported:** `deploy/instagram-bridge/go.mod` requested Go `1.27.1`; Aikido
  lists `1.27.3` (and `1.26.10`) as patched.
- **Evidence:** `deploy/instagram-bridge/go.mod:3` and
  `deploy/instagram-bridge/Dockerfile:8`.
- **Fix:** `go 1.27.3` in `go.mod`, `golang:1.27.3-bookworm` in the build
  stage. The toolchain was exercised by `go test ./...`; the Docker tag exists
  (`docker manifest inspect golang:1.27.3-bookworm` succeeds).
- **Trust boundary:** the Go runtime compiled into the bridge. Reachable from
  every HTTP/SSE route; the scanner's version is the input, the built binary is
  the proof.
- **Reversal:** if a future Go release regresses the bridge, pin the newest
  patched 1.27.x, not back to 1.27.1.

### 8765_44886 — Root restore follows room-controlled intermediate symlinks (high, ai_pentest)

- **Reported:** restore extracts as root into `/workspace`, where a room can
  plant a symlink; a later path component can escape the room.
- **Evidence:** `deploy/computer/nora-computer:548-632`. The old flow extracted
  directly under `/workspace`; `deploy/computer/archive_check.py` only validates
  links inside the archive, not links already on the VM.
- **Fix:** extract into a fresh root-owned
  `mktemp -d /workspace/.restore.XXXXXX`, then hold
  `/workspace/.restore.lock` EXCLUSIVELY while replacing each room (every
  computerd `run`/`state`/file operation holds it SHARED). Move the old room
  aside to a private `.restore-old.*` directory, rename the staged tree onto the
  EXACT destination with `mv -T` (a directory a writer recreated there fails the
  rename instead of nesting the tree inside it), `chown -hR` without following
  links, and delete the old copy only after both succeed. A failed move or chown
  puts the old room back; if the destination is occupied, the old data is kept
  and named. A process that outlives its command (`nohup`, a tmux pane) is outside
  the lease, so long-lived room work must stop before a `--force` restore.
- **Test:** `test_a_restore_does_not_follow_a_symlink_already_in_a_room` plants
  `room/escape -> outside` and asserts the outside file is untouched;
  `test_a_restore_holds_the_exclusive_lease_while_it_swaps_a_room` proves the
  lock is held; `test_a_destination_that_reappears_is_not_nested_inside_and_the_old_room_is_kept`
  proves the race fails closed with the old data preserved; and
  `test_a_failed_room_move_puts_the_old_room_back_instead_of_deleting_it` proves
  an injected move failure does not lose the old room.
- **Trust boundary:** root filesystem write from a room-controlled path. The
  room is untrusted; the archive validator is not a substitute for a clean
  extraction root and a recoverable replace.
- **Reversal:** only if restore stops running as root *and* the destination is
  proven not to contain room-controlled intermediate links.

### 8765_44879 — Cognition event access ignores platform and unit scope (high, ai_pentest)

- **Reported:** the cognition agenda reads events by conversation id without a
  unit/platform check, so the same id in another unit can surface.
- **Evidence:** `nora/infra/sql/gatherings.py:250-266` filtered only
  `conversation_id`; `nora/app/cognition_wiring.py:402-417`,
  `nora/agent/tools.py:12864` and `nora/app/composition.py:4527-4528` threaded
  the room but not the unit.
- **Fix:** `list_for_conversation` takes `unit_id` and `include_legacy`; a
  legacy `unit_id=""` row is admitted only when policy assigns its room to
  exactly one `(unit, platform)` owner, and otherwise stays quarantined. Every
  read and every mutation (list, search, update, status, RSVP, cancel, venue,
  and the creation-time preliminary read) passes unit/platform and checks
  through `event_in_scope`, so a row from another unit or platform with the same
  room id is `GatheringNotFoundError`. The reader carries
  `(unit, conversation_id)` through `open_events_reader`, `upcoming_reader`,
  `build_cognition_runner.open_events` and `composition._room_events`.
- **Test:** `tests/unit/test_event_evidence_d.py` covers the foreign-unit read
  and the unambiguous/ambiguous legacy cases; `tests/test_gatherings_service.py`
  covers every event mutation against a foreign event and an ambiguous legacy
  event; `tests/contract/test_gathering_store_contract.py` proves the store
  quarantines legacy rows unless asked.
- **Trust boundary:** DEC-231's unit = read boundary. The conversation id is an
  id space per platform, not a unit-scoped key. Ambiguous legacy ownership fails
  closed instead of belonging to whoever asks.
- **Reversal:** legacy `unit_id=""` rows stay quarantined until a migration
  assigns them an owner; do not relax the ambiguity check while they exist.

### 8765_44824 — Unauthenticated Instagram bridge (high, ai_pentest)

- **Reported:** co-networked containers can call stored Instagram account
  capabilities without authentication.
- **Evidence:** `deploy/instagram-bridge/internal/transport/http/server.go`
  registers account/thread routes with no auth; `cmd/instagram-bridge/main.go`
  served them on `:%d`. The container publishes no port, but the private `nora`
  network is shared by every service.
- **Fix:** `internal/transport/http/auth.go` adds constant-time bearer
  authentication on **every** route, including `GET /health` (its body carries
  the bridge's own fbid/igid/username, which is still an identifier); an empty
  configured token fails closed with `503`. `main.go` reads
  `NORA_BRIDGE_TOKEN`, refuses to start without it, and wraps the handler. The
  compose healthcheck and Nora's identity/username probes send the same bearer;
  Python calls use `nora/infra/service_auth.py`, merged into
  `propagation_headers()` so every existing Instagram bridge call site carries
  it. The session probe's `/health` check now authenticates too: the container's
  own `sh` expands `NORA_BRIDGE_TOKEN`, so the token is never in the probe's
  argv, state file or a diagnostic.
- **Test:** `internal/transport/http/auth_test.go` covers health-protected,
  missing/wrong/right bearer and the unconfigured fail-closed case;
  `tests/unit/test_instagram_self_id.py::test_both_health_probes_carry_the_bridge_token`,
  `tests/unit/test_instagram_trace_propagation.py::test_the_bridge_token_rides_the_same_headers`,
  and
  `tests/infra/social/session_probe/test_probe.py::test_the_health_probe_authenticates_from_the_container_without_the_token_in_argv`.
- **Trust boundary:** caller identity inside the private network. Network
  membership is not authentication; a compromised sidecar is the reversal
  condition for treating the network as sufficient.
- **Deployment:** `docker-compose.yml` and `deploy/instagram-bridge/compose.fragment.yml`
  now require `NORA_BRIDGE_TOKEN`; the same value must be set in the host
  environment before recreating. The bridge will not start without it.

### 8765_44859 — Unauthenticated WhatsApp bridge (high, ai_pentest)

- **Reported:** the WhatsApp bridge exposes private events and send/edit/react
  actions to any co-networked container.
- **Evidence:** `deploy/whatsapp-bridge/bridge.js` served `/events`, `/self`,
  `/send`, `/send-media`, `/edit` and `/react` without auth; only `/health` is
  needed by the compose healthcheck.
- **Fix:** `authorizedBridgeRequest` in `bridge_helpers.js` compares the UTF-8
  BYTE lengths of the supplied `Authorization` header and
  `Bearer <NORA_BRIDGE_TOKEN>` and only then calls `timingSafeEqual`; a malformed
  non-ASCII header is a clean `401`, not a length exception.
  `bridge.js` wraps the whole request handler (auth, URL parse and routing) in
  one `try`, so no request shape rejects the handler. `/health` stays open; an
  empty configured token rejects every other route. `bridge.js` refuses to start
  outside `--pair` without a token and applies the check before routing. Python
  WhatsApp clients send the header on `/events`, `/self`, `/send`,
  `/send-media`, `/edit` and `/react`.
- **Test:** `deploy/whatsapp-bridge/bridge.test.mjs` covers health-open, the
  exact bearer, a non-ASCII bearer (helper returns false; a real HTTP server
  answers `401` and survives), and the Python bridge-client suites pass.
- **Trust boundary / deployment:** same as 8765_44824; the token is required in
  `docker-compose.yml` and `.env.example`.

### 8765_44812 — Cross-platform conversation-ID collision misbinds room authorization (high, ai_pentest)

- **Reported:** `scope_rooms` is keyed by conversation id only; two platforms or
  units using the same id overwrite each other, so a scope-keyed adaptor can be
  granted another room's home context.
- **Evidence:** `nora/app/composition.py` built `scope_rooms` with a dict
  comprehension over `conv.ids`, silently keeping the last room.
- **Fix:** `scope_home_rooms(allowlist)` refuses a duplicate id that maps to a
  different `(unit, platform, primary_id)` with `StartupError`; two id forms of
  the same room remain allowed and share one `HomeRoom`.
- **Test:** `tests/unit/test_composition_scope_rooms.py` covers both branches.
- **Trust boundary:** scope-keyed adaptor authorization. A conversation id is
  not globally unique; the policy must disambiguate before Nora starts.
- **Reversal:** if policy legitimately needs one id to name two rooms, the
  authorization model needs a compound key (platform + id), not a silent
  overwrite.

### 8764_79878 — Docker container runs as default root user (high, iac)

- **Reported:** `site/Dockerfile` has no `USER`.
- **Evidence:** the final stage was `FROM caddy:2-alpine`, copied static files,
  and ran Caddy as root.
- **Fix:** `site/Dockerfile:36-44` creates a system `caddy` account (the official
  image has none), gives it `/data` and `/config`, and `USER caddy`. The site
  listens on 8080, with admin and persist_config off, so no privileged port or
  root-owned state remains.
- **Verification:** the same runtime-stage commands were exercised against
  `caddy:2-alpine`; `id` reports the unprivileged user and both state dirs are
  writable.
- **Trust boundary:** a static-file web server's process uid. Reversal only if
  Caddy needs a privileged port or root-owned certificate path; neither is in
  this image.

## Already fixed on origin/master

### 8764_80796 — AIKIDO-2026-285573 in huggingface-hub (critical, open source)

- **Reported:** `deploy/stt/uv.lock` pinned `huggingface-hub` 1.28.0; patched
  1.32.0.
- **Evidence:** `deploy/stt/uv.lock` now locks `huggingface-hub` 1.32.0. The
  change arrived in `d396646c4` (`security: patch STT dependency, quote snapshot
  identifiers and add CSP`), merged before this triage branch.
- **Verdict:** already fixed. No branch change.
- **Reversal:** a future lockfile regeneration that drops below 1.32.0.

### 8764_82911 — SQL injection via string concatenation (critical, sast)

- **Reported:** `nora/infra/sql/snapshot_at.py` built sequence SQL by
  concatenation.
- **Evidence:** `nora/infra/sql/snapshot_at.py:515-524` now uses
  `sa.table`, `sa.column`, `pg_get_serial_sequence` bind parameters and
  `identifier_preparer.format_table`; `tests/infra/test_snapshot_sequences.py`
  covers the identifier path. Fixed in `d396646c4`.
- **Verdict:** already fixed. No branch change.
- **Reversal:** reintroducing `text()` with an f-string/`+` identifier.

## False positives

### Leaked-secret findings (17)

All seventeen are **high severity, `leaked_secret`**, and were opened individually and the reported value was classified
without printing it. None is an authentication secret. The recurring causes are
SHA-256 checksums, an OpenPGP fingerprint, placeholder identifiers in examples,
base64-encoded Signal conversation ids in eval fixtures, and public model /
tokenizer hashes. Every row below is one finding; its verdict is **false
positive**.

| ID | Reported location | What the value actually is | Why it is not a secret | Trust boundary / reversal |
|---|---|---|---|---|
| 8764_82355 | `evals/retrieval_bakeoff/qwen_llama.py:51` | public Qwen model repo/revision names and tokenizer constants around the `__all__` block | model identifiers and hashes, not credentials | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82525 | `design/04-config-identity.md:102` (value at `:107`) | commented placeholder Discord snowflake in a `persons.yaml` example | placeholder, not a live client id | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82516 | `deploy/signal-cli/Dockerfile:19` | `ARG SIGNAL_CLI_KEY=FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570`, a 40-hex OpenPGP fingerprint | public key fingerprint (not a SHA-256 checksum and not a secret) | none: public data; reversal = a private key or live credential |
| 8764_82513 | `deploy/signal-cli/fetch.sh:9` | the same 40-hex OpenPGP fingerprint in `KEY=` | public key fingerprint | none: public data; reversal = a private key or live credential |
| 8764_82495 | `evals/corpora/injections.yaml:123` | base64-encoded Signal conversation id in the fixture snapshot | eval identifier, not a credential | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82489 | `evals/corpora/trigger.yaml:170` | boolean fixture metadata; adjacent anchor is a base64 conversation id | no secret on the line | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82485 | `evals/corpora/relevance_gate.yaml:153-154` | Discord test channel/snowflake id | public id shape, fixture value | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82479 | `evals/corpora/near_miss.yaml:84` | `entry: tag`; adjacent `snapshot: *room_a` resolves to a base64 conversation id | eval identifier | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82477 | `evals/corpora/no_trigger.yaml:130` | `entry: tag`; adjacent anchor is a base64 conversation id | eval identifier | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82471 | `evals/corpora/participation.yaml:184` | fixture input containing a URL; the case snapshot uses a base64 conversation id | no credential | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82465 | `evals/corpora/injections.yaml:90` | comment line; nearby fixture snapshots hold base64 conversation ids | no credential | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82460 | `evals/corpora/trigger.yaml:90` | case name; its snapshot anchor is a base64 conversation id | eval identifier | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82378 | `evals/retrieval_bakeoff/results/…text_image.json:721` | `tokenizer_sha256` 64-hex | model-file hash | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82372 | `evals/retrieval_bakeoff/results/…names.json:720` | `tokenizer_sha256` 64-hex | model-file hash | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82366 | `evals/retrieval_bakeoff/results/…cantonese.json:785` | `tokenizer_sha256` 64-hex | model-file hash | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82365 | `evals/retrieval_bakeoff/wemm_normaliser_golden.py:31` | adjacent `TOKENIZER_SHA` 64-hex | model-file hash | none: public/fixture data; reversal = a live credential or private identifier |
| 8764_82360 | `evals/retrieval_bakeoff/adapters.py:264` | comment citing the tokenizer SHA-256 | model-file hash | none: public/fixture data; reversal = a live credential or private identifier |

Reversal for any of these: a value that authenticates to a third-party service,
appears in `.env`/CI as a live credential, or is a private identifier rather than
a public/model/fixture value. None of the above does.

### 8764_82704 — Potential file inclusion via reading file (high, sast)

- **Reported:** `deploy/voice-sidecar/src/wav.js:12`
  (`fs.openSync(path, 'w')`).
- **Evidence:** the only importer is `deploy/voice-sidecar/spike.mjs:24`; the
  production entry point `src/service.mjs` never imports `wav.js` and never
  writes captured audio. The path is a spike CLI argument, not a request value.
- **Verdict:** false positive for the deployed service. The spike is a local
  operator tool.
- **Reversal:** if `WavWriter` is imported by `service.mjs` or fed a network
  path, re-triage as a real arbitrary-write finding.

### 8764_82668 — Potential file inclusion via reading file (high, sast)

- **Reported:** `deploy/instagram-bridge/internal/session/ledger.go:136`
  (`os.ReadFile(l.store.Path(ledgerFile))`).
- **Evidence:** `ledgerFile` is the constant `comments.json` (`ledger.go:57`)
  and `Store.Path` is `filepath.Join(s.Dir, name)` (`store.go:55`); the session
  dir is configuration, not a request value. No user input reaches the path.
- **Verdict:** false positive.
- **Reversal:** if the filename becomes a parameter or `Store.Dir` becomes
  request-controlled.

### 8765_44892 — Cognition can search workspace memory outside its authorized unit (high, ai_pentest)

- **Reported:** `nora/app/cognition_wiring.py` lets a cognition session search
  workspace memory outside its unit.
- **Evidence:** `nora/agent/tools.py:13627` calls
  `honcho.search(query=..., unit=_unit())`, not a workspace-wide call without a
  unit. `_unit()` (`tools.py:13334-13366`) returns the supplied unit for a
  unit-keyed adaptor, otherwise resolves the real room's
  `(platform, conversation_id)` through the policy resolver. With `[units]`
  declared, `HonchoMemory._route` returns `None` for a missing unit
  (`client.py:461-475`), and `search` then returns
  `MessageHits(reachable=False)` (`client.py:1356-1358`) — `unavailable`, not a
  widened read. Without `[units]` there is one implicit workspace and no unit
  boundary to cross.
- **Verdict:** false positive. The dangerous shape (a bare workspace search with
  no unit) is not present.
- **Reversal:** a caller that drops `unit=` or a router that maps `None` to a
  default workspace; the existing strict-mode test would need to be removed for
  that to pass.

## Source reasoning — scanner attribution UNVERIFIED

### 8764_96431 — Cookie missing HttpOnly flag (high, surface)

- **Reported:** a cookie lacks `HttpOnly`.
- **Evidence:** the only live cookie missing the flag is `NEXT_LOCALE`, the
  next-intl locale preference. It carries no session, identity or authorization
  value, and the rendered page already exposes the locale. NextAuth's session
  cookies are `HttpOnly; Secure; SameSite=Lax` (observed live).
- **Reasoning:** not a credential in this source. Forcing `HttpOnly` on it would
  change next-intl's client-visible preference without protecting anything. This
  is not counted as a confirmed false positive because the scanner's target is
  UNVERIFIED: the source reasoning holds, the attribution does not.
- **Reversal:** if `NEXT_LOCALE` ever carries a token, user id or other
  server-trusted value, it must become `HttpOnly` or move out of the cookie.

## Intentional risks, not changed

### 8765_44787 — Release builds trust mutable base images and live OS package feeds (high, ai_pentest)

- **Reported:** `scripts/release_bundle.py` builds images whose Dockerfiles use
  mutable `FROM` tags and live `apt`/`npm` feeds.
- **Evidence:** `scripts/release_bundle.py:153-166` builds with the checked-in
  Dockerfiles and records the resulting digest; most Dockerfiles use tags such
  as `python:3.12-slim-trixie`, `node:24-trixie-slim`, `debian:bookworm-slim`,
  `golang:1.27.3-bookworm`, `caddy:2-alpine`. Only
  `deploy/komodo/Periphery.Dockerfile` pins a base digest. `npm ci` and
  `uv`/`go mod` verify lockfiles, but `apt-get` fetches the feed's current
  packages.
- **Why not changed in this branch:** pinning every base image and every OS
  package is a build-reproducibility project, not a one-line fix. A wrong
  digest can break the release build, and pinning `apt` requires a snapshot
  feed and a package-update process. The release already records the built
  image digest in `release.json`, so a published release is pinned after the
  fact even though the build inputs are not.
- **Residual:** build provenance still depends on the registries and OS feeds
  at build time. The real trust boundary is the release job and the registry
  account, not an unauthenticated network caller.
- **Reversal / upgrade path:** pin base images by multi-arch digest and move
  `apt` to a dated snapshot when reproducible builds become a requirement; then
  this becomes a confirmed fix, not an accepted risk. Do not "fix" it by
  switching to unpinned `latest`.

### 8764_79880 — Periphery container runs as root (high, iac)

- **Reported:** `deploy/komodo/Periphery.Dockerfile` has no `USER`.
- **Evidence:** the image is the audited Komodo Periphery base plus stdlib
  Python. Periphery's job is to drive the host Docker socket and read the
  same-path runtime mount; `deploy/komodo/README.md:72-96` documents both. A
  container holding `/var/run/docker.sock` can mount the host filesystem and is
  root-equivalent whatever uid it runs as; switching uid to a `docker`-group
  user would keep the same host escape and add permission failures. The
  Dockerfile now records this rationale beside the `FROM`.
- **Verdict:** intentional risk, not a defect to "fix" by adding `USER`.
- **Residual:** the Docker socket is the privilege. Keep Periphery on the
  private Komodo host, do not expose its API, and treat its compose project as
  root-equivalent. The only real reduction is a socket proxy or rootless
  Docker, which is a separate deployment change.
- **Reversal:** if Periphery is moved off the Docker socket (or onto a
  rootless/socket-proxy setup), drop root and re-triage.

## Verification and residual work

- Python: focused suites green for event ownership (service, store contract,
  cognition evidence), composition scope rooms, event citations, cognition
  read tools, computer restore/lease/race/failure, and the session probe. Ruff
  is clean on every changed Python file.
- Go: `go vet ./...` and `go test ./...` in `deploy/instagram-bridge` exit `0`.
- Node: `node --test deploy/whatsapp-bridge/bridge.test.mjs` is 43 tests,
  36 pass / 7 fail on **both** Node 24.11.1 and Node 26.8.1. The 7 are the
  pre-existing edit-key failures; no decryption helper was touched by this
  branch. They are reported, not hidden.
- Wiki: `npm ci && npm run build` in `docs-site` exits `0`. Under the new Caddy
  policy the built Pagefind search loaded its worker and WASM and returned 5
  results for `security`; under the old policy the same build logged a CSP
  `CompileError` for the Pagefind WASM and never left `Searching…`.
- Full gate: the final `make gate` receipt is posted on PR #188 and bound to
  that head's commit SHA and tree. This file deliberately does not restate a
  local log path or an unbound pass count.
- Deployment still required: rebuilding the portal and site is what puts the
  header/cookie changes on `nora[.]67[.]zone`. That read-back would prove the
  headers live; it would not prove the scanner's target.
- Residual: the restore lease does not cover a process that outlives its
  command (`nohup`, a tmux pane); long-lived room work must stop before a
  `--force` restore. Legacy events with no owner are quarantined, not migrated.
  The scanner's target and commit remain UNVERIFIED.
