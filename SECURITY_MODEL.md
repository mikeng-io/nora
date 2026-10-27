# Nora security model

The threat model for Nora: what is exposed, to whom, what matters, and what the
operator has ruled out of scope. Attackers, critical assets, scope and accepted
risk are operator rulings (2026-10-27); the component map and controls are read
from the code. Security scans and finding triage start from this file. Vulnerability
reporting is in [`SECURITY.md`](SECURITY.md).

Scope: runtime code only: `nora/`, `app/`, `deploy/`, `docker-compose.yml`,
`Dockerfile`. Not `evals/`, `tests/`, `docs-site/`, `site/`, `showreel/`,
`voice-lab/`, `docs/`, `design/`. Infrastructure is the in-repo
`docker-compose.yml`, `Dockerfile` and `deploy/`; there is no separate
infrastructure repository.

Related: `SECURITY.md` (reporting; experimental, best-effort, operator owns credentials/network/backups), `docs/security/2026-10-22-aikido-high-triage.md` (prior Aikido triage), ADR 0030 (content filters off by default), ADR 0019/0022/0025 (sandbox computer and browser).

## System overview

Nora is a self-hosted, single-operator social AI agent. Platform adapters receive messages from Signal, Discord, WhatsApp and Instagram (and Discord voice), record them in PostgreSQL, and run admitted turns through a Strands LLM agent with tools (web search/fetch, sandbox computer and browser, MCP servers, location, places, image/video generation, reminders). A Next.js portal lets roster members read their own memory, presence, documents and artifacts. Everything runs on one host under Docker Compose; only the portal (and the optional static site) is reachable from the internet through an operator-run Cloudflare Tunnel.

## Components

| Component | Code it covers | Trust boundary |
|---|---|---|
| **Portal** | `app/` (Next.js, NextAuth: Discord OAuth + one-time code), `nora/web/` (`/app/v1/*` aiohttp routes on :9100), `nora/app/otp*` | Internet → portal (authenticated, roster allowlist from `identity.toml`); portal → `/app/v1` over the private `nora` network, which trusts the portal to supply `personId` |
| **Messaging ingress** | `nora/platform/`, `nora/ingest/`, `nora/engagement/`, `nora/persons*.py`, `nora/policy.py`, `nora/tag.py`, `deploy/signal-cli`, `deploy/whatsapp-bridge`, `deploy/instagram-bridge`, `deploy/voice-*`, `deploy/stt`, `nora/web/voice.py` | Chat-platform users (roster and non-roster) → Nora; bridges ↔ Nora with `NORA_BRIDGE_TOKEN` (`nora/infra/service_auth.py`) |
| **Agent runtime** | `nora/agent/`, `nora/app/`, `nora/language/`, `nora/doctrine/`, `nora/instructions/`, `nora/skills/`, `nora/dreamer/`, `nora/subagent/`, `nora/subject/` | Untrusted message/web text → LLM → tool calls with side effects; egress/disclosure guards (`nora/agent/egress.py`, `security.py`, `guards.py`) |
| **Computer sandbox** | `deploy/computer/` (computerd, sbx microVM), `nora/infra/computer/`, `nora/infra/social/` (OpenCLI bridge to host Chrome), `nora/agent/browse.py` | Nora → computerd at `172.17.0.1:18765` with `NORA_COMPUTER_TOKEN`; sandbox egress `**` with private ranges denied; vault fill broker for credentials (ADR 0022) |
| **Data stores** | `nora/infra/sql/` (+ Alembic), `nora/infra/honcho/`, `nora/infra/blobs/`, `nora/infra/embedder.py`, `nora/documents/`, `nora/infra/external_files/` | Postgres (:5435 loopback), Honcho (no auth, private network only), RustFS, embedder |
| **Presence** | `nora/presence/`, `nora/analytics/`, `nora/infra/life360/`, `nora/infra/presence_refresh.py`, `nora/agent/whereis.py` | Life360 credentials → live GPS fixes → location history; `/refresh` on :9101 (loopback) |
| **Deployment** | `docker-compose.yml`, `Dockerfile`, `deploy/` (komodo, prometheus, alertmanager, alert-bridge, postgres, install.sh), `.github/workflows/`, `scripts/` | Host, Docker networks, CI/release pipeline, secrets in host `.env` |
| **Public site** | `site/` (Astro + Caddy), `docs-site/`, `openwiki/` (outside scan scope) | Internet, static content only |

## Entry points and who can reach them

- **Internet (anyone):** portal pages and `/api/auth/*`, `/api/v1/*` proxy routes in `app/src/app/api/` (session-gated by `app/src/server/session.ts` → `checkSession()`), login (Discord OAuth, OTP request/verify through the portal server actions), static site. `trustHost: true` in `app/src/auth.ts`.
- **Chat platforms (any member of a room Nora is in, any DM sender):** message text, media, edits, reactions, voice utterances. Admission (`nora/engagement/stages/admission.py`) and `policy.toml` grants decide whether a turn runs; ingestion records more than it answers.
- **Web content Nora reads:** search results, fetched pages, browsed pages, Google Drive files, MCP server responses: indirect prompt-injection input into tool-using turns.
- **Private Docker network `nora` (co-networked containers):** `/app/v1/*` reads on `nora:9100` have **no caller authentication** (they trust the portal proxy, `nora/web/otp.py` docstring); OTP routes and voice routes require bearer tokens; bridges require `NORA_BRIDGE_TOKEN`; signal-cli JSON-RPC has no auth (relies on unpublished network).
- **Host loopback:** :9100 health/metrics/app API, :3400 portal, :5435 Postgres, :9101/:9103 presence/analytics, :8092 embedder, Prometheus/Alertmanager.
- **Operator:** `config.toml`, `identity.toml`, `policy.toml`, doctrine bind mounts, `.env`.

## Assets

**Operator-named critical assets:** the `identity.toml` roster and portal access; chat records and memory (Postgres, Honcho); platform sessions and keys (Signal, WhatsApp, Instagram, Discord). The others below still matter but were not named critical.

1. Location: live Life360 fixes and derived location history (`presence`, `analytics`).
2. Chat records, memory and derived profiles of people (Postgres record store, Honcho, documents, artifacts).
3. Platform sessions and keys: Signal device registration (`state/signal-cli`), WhatsApp/Instagram sessions, Discord bot token: lets an attacker read and send as Nora.
4. Secrets: model provider/OmniRoute keys, `AUTH_SECRET`, `NORA_OTP_API_TOKEN`, `NORA_BRIDGE_TOKEN`, `NORA_COMPUTER_TOKEN`, Life360, Google, Tavily, Pulse tokens; vault-held browser credentials.
5. Portal access and the `identity.toml` roster (who is allowed, which accounts map to which person).
6. Host integrity and the sandbox VM (`/workspace`).

## Attacker profiles

Operator: **all five matter**; none is ruled out (including A5, the co-networked container).

- **A1 Unauthenticated internet user** against the portal: auth bypass, OTP brute force/enumeration, SSRF/path traversal through the `/api/v1` proxy, header/host trust.
- **A2 Non-roster person in a room or DM:** prompt injection to make Nora disclose another person's data or location, run tools, or bypass admission/grants.
- **A3 Signed-in roster member:** horizontal access to another person's or room's/unit's data through portal routes (`personId` handling) or through Nora (cross-unit reads).
- **A4 Malicious web/MCP content:** indirect prompt injection; SSRF or private-range reach from the sandbox or from server-side fetchers.
- **A5 Compromised co-networked container/bridge:** reaching unauthenticated `/app/v1` reads, signal-cli, Honcho.

## Existing controls

- Portal: NextAuth JWT sessions, roster re-check on every request (`checkSession()`, `personById` with `Object.hasOwn`), dev login only in `next dev` with `NORA_DEV_LOGIN=1`, CSP and headers (`app/next.config.ts`, `site/Caddyfile`).
- OTP: bearer token with constant-time compare, uniform 202/401 responses, purpose-bound codes (`nora/web/otp.py`, `nora/app/otp*`).
- Network: only loopback publishes; bridges, signal-cli, Honcho, stt and voice services publish nothing; Honcho isolated on its own network.
- Service auth: `NORA_BRIDGE_TOKEN` on bridges and voice routes; `NORA_COMPUTER_TOKEN` on computerd.
- Agent: deny-by-default `policy.toml` (refuses to start empty), admission rules, unit read scope (DEC-231), location consent/opt-in, three-state tool results, always-on credential-pattern blocking and doctrine LeakGuard.
- Sandbox: sbx microVM, egress deny for 10/8, 172.16/12, 192.168/16, 127/8, 100.64/10; credentials only through a vault fill broker.
- Supply chain: signal-cli release signature verified at build; lockfile pins test (`app/src/lockfile-pins.test.ts`).

## Out of scope / accepted risk (operator-confirmed)

- **Content filters off by default (ADR 0030):** phone/UUID/coordinate content vetoes, third-party place/person-name screening and location audience subset checks are disabled by an operator ruling. Findings that only amount to "this filter is off" are accepted risk; credential blocking, LeakGuard, admission, unit scope and location consent are not.
- Host, operator and Docker-daemon compromise; the operator's own config files.
- Dev-only paths (`evals/`, `voice-lab/`, `showreel/`, `NORA_DEV_LOGIN`), unless reachable in the production image.
- LLM provider / OmniRoute misbehaviour (the model provider itself returning malicious or wrong output); prompt injection from room members or web content is still in scope (A2/A4).
- Experimental project, best-effort fixes, default branch only (`SECURITY.md`).

## Open questions

- Is the static site live, and is the Cloudflare Tunnel the only internet path?
- Location data was not named a critical asset: confirm it ranks below the three named ones.

## Finding tags for scans

- **CVE**: the CVE id, for dependency findings.
- **Platform**: `signal`, `discord`, `whatsapp`, `instagram` or `portal`, when a finding is specific to one surface.
