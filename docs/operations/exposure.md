---
class: operations
subject: how Nora is exposed, the entry points and who can reach each
owns:
  - entry points: "every way something outside Nora can reach her, and who can use it"
links:
  - {to: docs/design/security/README.md, rel: uses}
---

# Exposure

The ports, networks and entry points behind the trust boundaries in the
[threat model](../design/security/README.md).

## Entry points and who can reach them

- **Internet (anyone):** portal pages and `/api/auth/*`, `/api/v1/*` proxy routes in `app/src/app/api/` (session-gated by `app/src/server/session.ts` → `checkSession()`), login (Discord OAuth, OTP request/verify through the portal server actions), static site. `trustHost: true` in `app/src/auth.ts`.
- **Chat platforms (any member of a room Nora is in, any DM sender):** message text, media, edits, reactions, voice utterances. Admission (`nora/engagement/stages/admission.py`) and `policy.toml` grants decide whether a turn runs; ingestion records more than it answers.
- **Web content Nora reads:** search results, fetched pages, browsed pages, Google Drive files, MCP server responses: indirect prompt-injection input into tool-using turns.
- **Private Docker network `nora` (co-networked containers):** `/app/v1/*` reads on `nora:9100` have **no caller authentication** (they trust the portal proxy, `nora/web/otp.py` docstring); OTP routes and voice routes require bearer tokens; bridges require `NORA_BRIDGE_TOKEN`; signal-cli JSON-RPC has no auth (relies on unpublished network).
- **Host loopback:** :9100 health/metrics/app API, :3400 portal, :5435 Postgres, :9101/:9103 presence/analytics, :8092 embedder, Prometheus/Alertmanager.
- **Operator:** `config.toml`, `identity.toml`, `policy.toml`, doctrine bind mounts, `.env`.


## System overview

Nora is a self-hosted, single-operator social AI agent. Platform adapters receive messages from Signal, Discord, WhatsApp and Instagram (and Discord voice), record them in PostgreSQL, and run admitted turns through a Strands LLM agent with tools (web search/fetch, sandbox computer and browser, MCP servers, location, places, image/video generation, reminders). A Next.js portal lets roster members read their own memory, presence, documents and artifacts. Everything runs on one host under Docker Compose; only the portal (and the optional static site) is reachable from the internet through an operator-run Cloudflare Tunnel.
