---
class: engineering
subject: security scan scope, the code behind each trust boundary and how findings are tagged
owns:
  - security scan scope: "the runtime code a security scan or finding triage judges, and the code behind each trust boundary"
links:
  - {to: docs/design/security/README.md, rel: uses}
---

# Security triage

Scans and finding triage start from the [threat model](../design/security/README.md).

Scope: runtime code only: `nora/`, `app/`, `deploy/`, `docker-compose.yml`,
`Dockerfile`. Not `evals/`, `tests/`,
`voice-lab/`, `docs/`, `docs/design/`. Infrastructure is the in-repo
`docker-compose.yml`, `Dockerfile` and `deploy/`; there is no separate
infrastructure repository.

Related: `SECURITY.md` (reporting; experimental, best-effort, operator owns credentials/network/backups), ADR 0030 (content filters off by default), ADR 0019/0022/0025 (sandbox computer and browser).

## Components

| Component | Code it covers | Trust boundary |
|---|---|---|
| **Portal** | `app/` (Next.js, NextAuth: Discord OAuth + one-time code), `nora/web/` (`/app/v1/*` aiohttp routes on :9100), `nora/app/otp*` | Internet → portal (authenticated, roster allowlist from `identity.toml`); portal → `/app/v1` over the private `nora` network, which trusts the portal to supply `personId` |
| **Messaging ingress** | `nora/platform/`, `nora/ingest/`, `nora/engagement/`, `nora/persons*.py`, `nora/policy.py`, `nora/tag.py`, `deploy/signal-cli`, `deploy/whatsapp-bridge`, `deploy/instagram-bridge`, `deploy/voice-*`, `deploy/stt`, `nora/web/voice.py` | Chat-platform users (roster and non-roster) → Nora; bridges ↔ Nora with `NORA_BRIDGE_TOKEN` (`nora/infra/service_auth.py`) |
| **Agent runtime** | `nora/agent/`, `nora/app/`, `nora/language/`, `nora/doctrine/`, `nora/instructions/`, `nora/skills/`, `nora/dreamer/`, `nora/subagent/`, `nora/subject/` | Untrusted message/web text → LLM → tool calls with side effects; egress/disclosure guards (`nora/agent/egress.py`, `security.py`, `guards.py`) |
| **Computer sandbox** | `deploy/computer/` (computerd, sbx microVM), `nora/infra/computer/`, `nora/infra/social/` (OpenCLI bridge to host Chrome), `nora/agent/web_browse.py` | Nora → computerd at `172.17.0.1:18765` with `NORA_COMPUTER_TOKEN`; sandbox egress `**` with private ranges denied; vault fill broker for credentials (ADR 0022) |
| **Data stores** | `nora/infra/sql/` (+ Alembic), `nora/infra/honcho/`, `nora/infra/blobs/`, `nora/infra/embedder.py`, `nora/documents/`, `nora/infra/external_files/` | Postgres (:5435 loopback), Honcho (no auth, private network only), RustFS, embedder |
| **Presence** | `nora/presence/`, `nora/analytics/`, `nora/infra/life360/`, `nora/infra/presence_refresh.py`, `nora/agent/whereis.py` | Life360 credentials → live GPS fixes → location history; `/refresh` on :9101 (loopback) |
| **Deployment** | `docker-compose.yml`, `Dockerfile`, `deploy/` (komodo, prometheus, alertmanager, alert-bridge, postgres, install.sh), `.github/workflows/`, `scripts/` | Host, Docker networks, CI/release pipeline, secrets in host `.env` |


## Finding tags for scans

- **CVE**: the CVE id, for dependency findings.
- **Platform**: `signal`, `discord`, `whatsapp`, `instagram` or `portal`, when a finding is specific to one surface.
