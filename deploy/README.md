# Deployment

## Current runtime

The reference installation uses the root Docker Compose project on a Linux host.
It assumes model/memory services, persistent storage and separately provisioned
platform accounts. Start from `config.example.toml`, `identity.example.toml` and
`policy.example.toml`; configure only the capabilities you intend to operate.
The examples require operator configuration before startup. Store real config,
credentials and platform sessions outside version control and back up persistent
data before upgrading.

The operator/agent
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

## Release deployment with Komodo

The off-by-default [Komodo release deployment](komodo/README.md) publishes
version-stamped images from stable releases, selects exact image digests on a
separate Git branch, and uses a native Stack with a locked validation/readback
wrapper. It replaces the earlier unimplemented custom-reconciler proposal.
Neither the publisher nor the host schedule is enabled by merging its code.
The current operator/agent deployment remains in use until the documented
installation, disposable rehearsal and explicit production cutover complete.

## Historical bare-metal / systemd reference

This document and the systemd unit (`deploy/nora.service`) describe a **bare-metal**
deployment path. The reference Compose installation described above runs as a
Docker container (`Dockerfile` + `docker-compose.yml` at the repo root; see the
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
    cd <deployment-checkout> && git checkout <previous-tag>
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
its services are built from subtrees here (`stt/`, `voice-sidecar/`, …). The newest:

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
