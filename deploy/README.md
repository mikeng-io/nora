# Deployment

## Current runtime

Production application deploys use the installed Komodo Stack `nora-releases`
on a Linux host; infrastructure retains the legacy root Docker Compose project.
It assumes model/memory services, persistent storage and separately provisioned
platform accounts. Start from `config.example.toml`, `identity.example.toml` and
`policy.example.toml`; configure only the capabilities you intend to operate.
The examples require operator configuration before startup. Store real config,
credentials and platform sessions outside version control and back up persistent
data before upgrading.

The operator/agent
procedure is [nora-deploy](../.agents/skills/nora-deploy/SKILL.md); operational
recovery is in the [runbook](../docs/runbook.md). The installed wrapper serializes
changes with the shared host lock, validates config and reads back every owned service and the success receipt.

The Compose file uses bridge networks, persistent volumes and private host
configuration. The separate `honcho_memory` network must already exist.

## Release deployment with Komodo

The installed [Komodo release deployment](komodo/README.md) is the normal path:
merge a fix PR, manually merge release-please's release PR while the repository
is private, then let `release-images.yml` publish all images and commit
`compose.json` and `release.json` to `deployments`. The enabled
`nora-release-poll` Procedure checks every ten minutes and deploys `nora-releases`
through the installed wrapper, which writes `release-deploy.json`.
Rollback selects a previous bundle by reverting the selecting commit on
`deployments`; it does not revert schema migrations or host config.

Doctrine and Dreamer are baked into these images; host pulls do not change them.
`~/workspace/nora` is still the live runtime root for private config, `.env`,
`data/`, `state/`, infrastructure Compose services and the computer unit's
`ExecStart`. **Do not delete or reset it.** Checkout rebuild/pull deployment is
legacy; it must not run concurrently with or replace the installed release path.

## Legacy checkout bootstrap: first installation

This is a configurable stack, not a preconfigured hosted service. Prerequisites:
Docker Engine with Docker Compose on Linux; GnuPG (`gpg`) for verifying the
signal-cli download; a messaging account or bot for at
least one supported platform; an OpenAI-compatible model endpoint; and an
external Honcho memory backend. Compose supplies PostgreSQL and RustFS storage.
Optional speech, computer, social and portal features need their own services
and credentials.

1. Clone the repository and copy the inputs:

   ```bash
   cp config.example.toml config.toml
   cp identity.example.toml identity.toml
   cp policy.example.toml policy.toml
   cp .env.example .env
   ```

2. Replace the synthetic examples with your own accounts and policy grants.
   Disable unused platforms and optional integrations. Configure service URLs
   reachable **from the Nora container**: for example, `http://signal-cli:18080`
   for Signal, `/app/data` for `[store] data_dir`, and your memory service's DNS
   alias on the shared network. Container loopback does not reach the host;
   use `host.docker.internal` for a host model service where configured.
   Provision Honcho separately, then create `honcho_memory` with
   `docker network create --internal honcho_memory` if absent and attach the
   memory API to it. This repository does not bootstrap that backend.

3. Fill the required Compose environment variables. `POSTGRES_PASSWORD`,
   `RUSTFS_ROOT_USER`, `RUSTFS_ROOT_PASSWORD`, `OMNIROUTE_API_KEY` and
   `SIGNAL_ACCOUNT` are required by the checked-in Compose file. Portal variables
   `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `AUTH_SECRET` and `AUTH_URL` are
   also interpolated by Compose even when selecting other services. Use your own
   values; `.env.example` lists these separately from optional integrations.
   The model key's variable name does not require an OmniRoute deployment: set
   `[llm] base_url` and `model` for your OpenAI-compatible endpoint.

4. Provision the selected platform before starting Nora. For Signal, link or
   register the account with signal-cli and preserve its complete
   `state/signal-cli/` directory; see the [Signal notes](../docs/runbook.md#9-where-does-everything-live-read-this-first-months-later).
   For Discord, create a bot application, enable Message Content intent, invite
   it to the intended server and set `DISCORD_BOT_TOKEN`. Map the platform's
   account IDs in `identity.toml` and allow only the intended rooms in
   `policy.toml`. Do not reuse the example identities.

5. Validate configuration and prepare the images. With Python 3.12 and uv:

   ```bash
   uv sync --frozen --extra evals
   uv run nora config validate --config config.toml
   ./deploy/signal-cli/fetch.sh
   ./scripts/build-image.sh
   docker compose build signal-cli
   docker compose up -d db rustfs signal-cli nora
   ```

   This starts a Signal-oriented subset. Add your configured platform bridge or
   optional services explicitly. Keep model/memory services available before
   starting Nora. The image entry point runs database migrations at startup.

6. Read back health and logs, then tag Nora from an allowed room:

   ```bash
   docker compose ps
   curl -fsS http://127.0.0.1:9100/health
   docker compose logs --tail 100 nora
   ```

   Inspect degraded platform details even with HTTP 200. Confirm the message is
   received and a reply delivered; the [runbook](../docs/runbook.md) explains
   policy denials and silent failures. Do not publish raw logs or production rows.

## Upgrades, recovery and fetching

Back up configuration, PostgreSQL and persistent storage before an upgrade.
Follow the installed deployment mode in the [operator procedure](../.agents/skills/nora-deploy/SKILL.md)
and the [runbook's rollback constraints](../docs/runbook.md#3-rollback).
An older image must remain compatible with both schema and config; never clear
Alembic's version table to force a rollback.

`web_search` and `web_fetch` use the configured remote Tavily or Exa provider.
Enable `[search]` and supply that provider's API key. The former local Firecrawl
egress script is retired. Any separately operated local scraper needs its own
network isolation: a model-selected URL must not reach private services through
redirects or DNS rebinding.

## Container-era sidecars in this tree

The repo-root `docker-compose.yml` still runs database, RustFS, Prometheus,
Alertmanager and embedder infrastructure from the legacy runtime root. Komodo
owns application services through the generated `deployments` Compose manifest;
their release images are built from subtrees here (`stt/`, `voice-sidecar/`, …).
The newest:

* **`voice-sidecar/`** — the Node audio-controller (design/voice-channel/07):
  the SOLE holder of the Discord voice connection. Joins a channel,
  DAVE-decrypts per-user audio and forwards PCM over a local TCP seam
  toward the Python wake gate, AND drives outbound music/speech playback
  itself over an HTTP control plane (`nora/infra/voice/http_controller.py`)
  — no longer receive-only, the stage-2 name from before nora stopped
  holding its own voice connection. See
  `design/voice-channel/07-audio-owner-controller.md` and
  `deploy/voice-sidecar/README.md`.
* **`voice-wake/`** — Python wake gate (stage 3), the seam's only consumer:
  openWakeWord 「喂 Nora」 detection per user, utterance collection,
  transcription via the local `stt` service, and the transcript POSTed to
  Nora's `/app/v1/voice/utterance` as text. Audio is transient end to end.
  See `deploy/voice-wake/README.md`.

## Monitoring

See [observability](../docs/observability.md) for Prometheus, Alertmanager and
delivery diagnostics. Health and dependency metrics answer different questions;
monitor both.
