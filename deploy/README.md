# Deployment

## Current runtime

Nora runs on Norty using the root Docker Compose project. The operator/agent
procedure is [nora-deploy](../.agents/skills/nora-deploy/SKILL.md); operational
recovery is in the [runbook](../docs/runbook.md). The procedure serializes
changes with a host lock, builds a version-stamped image, validates live config,
recreates `nora`, `presence` and `analytics` together, and reads back their state.
Voice changes also coordinate the voice services. Verified images are recorded
for recovery; old phase tags are not the current rollback mechanism.

The Compose file uses bridge networks, persistent volumes and private host
configuration. The separate `honcho_memory` network must already exist.
Doctrine and Dreamer directories are live mounts from the deploy checkout;
image replacement alone does not determine all running behaviour.

## Proposed pull-based deployment

Status: proposal only. No controller, image publisher or production schedule
is installed by this change. Keep Compose for this single host. Revisit a
cluster controller only if the runtime actually moves to a cluster.

1. A trusted post-merge CI build tests a source revision and publishes OCI
   images, stamped with that revision, to a registry. Record immutable image
   digests for every changed service, including the shared Nora image and any
   coupled voice services. PR validation gets no publication or host credentials.
2. A reviewed desired-state change selects those digests and the matching
   instruction/config-schema revision. Keep deployment promotion separately
   controlled from public contribution branches. A merge of application code
   need not promote it. Third-party service upgrades remain explicit changes.
3. A small host-installed reconciler, invoked by a systemd timer, polls that
   approved state using outbound access. Norty needs no new inbound route:
   Tailscale remains the administration path. The reconciler has read-only
   source/registry credentials and local Docker authority; the coding agent
   and PR jobs have neither. Install its trusted executable outside the fetched
   checkout; never execute arbitrary scripts from a desired-state revision.
4. Before changing running services, hold the existing deploy lock, validate
   the manifest against a fixed service/registry allowlist, pull every digest,
   validate the host config with the candidate image, and retain the last
   successful deployment. Keep Compose project name, data paths and volumes
   stable. Store secrets, identities, policy, account sessions and OAuth refresh
   state on the host. Do not interpolate fetched shell or arbitrary mount paths.
5. Remove doctrine/Dreamer live checkout mounts in the reviewed deployment
   configuration so the image's existing copies become authoritative, or mount
   a matching immutable instruction bundle. This requires an explicit cutover:
   otherwise a host `git pull` can still alter behaviour outside promotion.
6. Recreate the selected service set without building or touching unrelated
   infrastructure, wait for its health checks, and verify running digests and
   application health. Record success only after those checks. On failure,
   stop promotion and report the unhealthy state; do not retry indefinitely.

Rollback selects the previous successful desired state, including instructions
and compatible configuration. Startup applies Alembic migrations: automatic
image rollback is unsafe after an incompatible schema change. Require a
reviewed roll-forward or an operator-led backup/restore plan in that case.
A failed rollback remains failed and must alert the operator.

Before implementation/activation, confirm registry and host architecture,
which services the controller owns, promotion approval, backup/restore readiness
and where failure alerts go. Host installation, credentials, access policy and
production cutover require operator approval. The current verifier assumes
`nora:latest`; it must be adapted and tested against desired digests before
claiming this path is ready. Exercise a successful update, failed pull, invalid
config, failed health check and compatible rollback on a disposable Compose
project before enabling the timer on Norty.

## Historical bare-metal / systemd reference

This document and the systemd unit (`deploy/nora.service`) describe a **bare-metal**
deployment path. It is **not** what the live instance currently runs — Nora runs as a
Docker container today (`Dockerfile` + `docker-compose.yml` at the repo root; see the
"Deployment" section of the root `README.md`). This path is kept for reference and as a
rollback option if the container approach ever needs to be abandoned; the commands below
(`systemctl start/stop nora`) only apply if you are actually running Nora this way.

## Install

    sudo ./deploy/install.sh
    sudo ./deploy/egress.sh apply     # Firecrawl SSRF egress policy (see Egress below)
    sudo systemctl start nora

## Rollback

If a deploy misbehaves, roll back to the previous version:

    sudo systemctl stop nora
    cd /home/nora/workspace/nora && git checkout <previous-tag>
    sudo systemctl start nora

The audit database is never touched by a rollback — but schema changes are versioned
Alembic revisions (`nora/infra/sql/migrations_runner.py`), not additive
`CREATE TABLE IF NOT EXISTS` statements, so an older binary reading a newer schema is
not guaranteed to work; see `docs/runbook.md` §3 for the persistence-phase rollback
procedure (`git revert` + `DROP TABLE alembic_version`). Since the 2026-08-07 Postgres
cutover (DEC-51) the store lives in the `db` compose service, not `data/messages.db` —
this bare-metal path predates that and would need its own database decision if revived.

## Egress

`deploy/egress.sh` is the versioned egress governance (container-era, 2026-08-07). It
takes a subcommand:

    sudo ./deploy/egress.sh apply     # install the Firecrawl SSRF egress policy
    sudo ./deploy/egress.sh verify    # print the three checks to confirm it
    sudo ./deploy/egress.sh down      # remove it

**What it closes.** `web_fetch`'s scraper (Firecrawl) fetches a model-chosen URL, so a
page that redirects or DNS-rebinds to an internal address would make Firecrawl fetch
Nora's own internals and hand the body back to the group (SSRF). Measured 2026-08-07:
from inside `firecrawl-api`, OmniRoute (`172.17.0.1:20128`) answered 307 and Honcho
(`:8000`) answered 404 — real, sensitive, reachable. Nora's code blocks the direct and
post-redirect paths; this script is the airtight closure — `DOCKER-USER` rules that deny
the Firecrawl surface containers (`firecrawl-api`, `playwright`) all private/host ranges
while preserving their own backend subnet and the public internet. **It is the gate
`config.toml [search] fetch_enabled = true` waits on.** Re-run after any Firecrawl or
Nora `compose up` (container IPs can change; the hooks match by IP).

**Still open (not this script):** a general per-service egress floor for the rest — `db`
needs no egress, `presence` needs exactly Life360 + Geocoding, `nora` needs its named
externals. This script scopes only the Firecrawl SSRF surface, which is the one with a
live untrusted-input path.

The old BARE-METAL floor (iptables `uid-owner nora` OUTPUT rules for the pre-container
systemd deployment) is preserved in git history; it did not constrain containers and is
superseded here.

## Container-era sidecars in this tree

The live deployment runs from the repo-root `docker-compose.yml`; several of
its services are built from subtrees here (`stt/`, `drop/`, …). The newest:

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

## Watchdog — retired 2026-09-09

`deploy/watchdog.sh` polled `/health` and alerted if the SSE listener had been silent
for more than 300 seconds. It is **deleted**, and what replaced it is
`deploy/prometheus/` + `deploy/alertmanager/` + `deploy/alert-bridge/`.

Two reasons, and the second is why it was never worth keeping as a fallback:

* **Its condition is now a rule.** `PlatformSilent` in
  `deploy/prometheus/rules/nora.yml` watches the same silence per platform, at a
  threshold chosen not to cry wolf on a quiet night — plus five more absences the
  script never looked at.
* **It had no delivery path.** Its alert channel was `echo >&2` and a non-zero exit,
  so even scheduled it would have produced a failed systemd unit — which is exactly
  what nobody noticed for the sixteen days `nora-egress.service` sat in `failed`.
  Alerts now reach the Discord review channel, and a delivered one says so.

Kept in git history. See `docs/observability.md` for the whole path an alert takes.
