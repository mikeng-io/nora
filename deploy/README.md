# Deployment

## Ownership and entry points

The root [`docker-compose.yml`](../docker-compose.yml) is the canonical source
model. [`release_bundle.py`](../scripts/release_bundle.py) reads it directly,
removes builds, selects immutable application images and rewrites host binds for
the runtime root. The generated `compose.json` still describes infrastructure;
the installed wrapper selects only its application cohort with `--no-deps`.
A service appearing in that manifest does not make Komodo its lifecycle owner.

| Scope | Source and owner | Entry point |
|---|---|---|
| Application | Generated `deployments` branch `compose.json` + `release.json`; installed Komodo Stack `nora-releases` | Ordinary release/image/poller flow; explicit maintenance uses the [Komodo procedure](komodo/README.md) |
| Infrastructure: `redis`, `db`, `rustfs`, `embedder` | Root Compose file in the persistent runtime checkout | Explicit service maintenance under the shared deploy lock; outside application reconciliation |
| Application telemetry wiring | [`signoz/docker-compose.signoz.yaml`](signoz/docker-compose.signoz.yaml), an overlay on the selected application manifest | Installed Stack configuration, after the backend exists; never a standalone stack |
| SigNoz backend | [`signoz/deployment/compose.yaml`](signoz/deployment/compose.yaml), separate project `signoz` | [Observability procedure](../docs/observability.md); retain installed host overrides |
| Computer VM image and lifecycle | [`computer/`](computer/README.md), outside root Compose | `nora-computer` and its documented systemd lifecycle |
| Memory graph experiment | [`memory-graph-spike/compose.yaml`](memory-graph-spike/compose.yaml), isolated experiment project | Its [experiment procedure](memory-graph-spike/README.md); never production memory storage |
| OTel queue fixture | [`otel-queue-fixture/docker-compose.yml`](otel-queue-fixture/docker-compose.yml) | Disposable test fixture; not a production deployment entry point |

The application cohort is `nora`, `presence`, `analytics`, `app`, optional
`site`, `stt`, `voice-sidecar`, `voice-wake`, all four platform bridges and
`bridge-projection`. The projection publisher is a running admission service,
not a bootstrap job: bridges use its shared projection volume while Nora is
restarting. Signal's `signal-cli` executable runs **inside `signal-bridge`**;
there is no current standalone `signal-cli` service.

Application and infrastructure deliberately share Compose project `nora` and
its existing network/volume identities. Do not rename the project, move the
runtime root or split the model as housekeeping: those changes need a migration
of the publisher, installed wrapper, binds and persistent resource identities.
Use explicit services for infrastructure work; project-wide `up`, `down`,
`stop`, `--remove-orphans` and volume pruning cross lifecycle boundaries.
The former `make up`, `make stop`, `make logs` and `make ps` shortcuts are removed.
For installed container reads, use `docker logs <container>` and `docker ps -a`
without resolving private Compose inputs. Host exec uses the
[credential helper](../docs/configuration.md#host-migration-and-installed-files).

The operator/agent procedure is [nora-deploy](../.agents/skills/nora-deploy/SKILL.md);
recovery is in the [runbook](../docs/runbook.md). Before a host change, identify
the installed deployment mode, preserve outstanding attempts and private state,
and use the shared lock. Unreadable deployment receipts are unavailable evidence,
not permission to fall back to checkout deployment.

## Retirement boundaries

An exited container is not proof that its data or role is obsolete. Inventory
its owner, replacement, mounts, volumes and remaining consumers before removal.
Retire selected containers separately from state deletion; never use a broad
project teardown or orphan cleanup to remove old services.

- A legacy standalone `signal-cli` must never run beside `signal-bridge` against
  the same registration store. Preserve `state/signal-cli/`, linked-device data
  and bridge journals even if the old container is removed.
- Legacy Prometheus, Alertmanager and alert-bridge are outside the current root
  model. Their replacement and retained telemetry follow the
  [SigNoz cutover](../docs/observability.md), not application release cleanup.
- A separately installed OTel collector may still serve other producers. Trace
  its receivers, exporters and clients before stopping it; the SigNoz overlay
  alone does not establish that it is unused.
- Graph-spike databases and volumes are experiment state. Confirm the experiment
  owner's remaining purpose and retention before stopping or deleting them.
- Completed SigNoz bootstrap/migration jobs and probes belong to that project's
  procedure; an exited job is not a failed permanent service.

These boundaries authorize no host stop, deletion, restart or deployment.

## Release deployment with Komodo

The installed [Komodo release deployment](komodo/README.md) is the normal path:
ordinary master pushes prepare patch/minor metadata, auto-merge it after required
checks on its exact head and let release-please publish. Breaking ranges stop for
an owner-only exact-candidate major dispatch (`nora-release`). The resulting Release triggers
`release-images.yml` to build or prove reuse of every artifact and commit
`compose.json` and `release.json` to `deployments`. The enabled
`nora-release-poll` Procedure checks every ten minutes and deploys `nora-releases`
through the installed wrapper, which writes `release-deploy.json`. The complete
bundle retains reused artifacts' original source/version provenance. Native Compose
preserves unchanged containers; loaded-once host edits require the explicit scoped
file reload request in the [Komodo procedure](komodo/README.md#loaded-once-host-file-reload).
Rollback selects a previous bundle by reverting the selecting commit on
`deployments`; it does not revert schema migrations or host config.

Doctrine and Dreamer are baked into these images; host pulls do not change them.
`~/workspace/nora` is still the live runtime root for private config, `.env`,
`data/`, `state/`, infrastructure Compose services and the computer unit's
`ExecStart`. **Do not delete or reset it.** Checkout rebuild/pull deployment is
legacy; it must not run concurrently with or replace the installed release path.

## Container runtime maintenance

Node build/runtime stages use `node:24-trixie-slim`; Python services use
`python:3.12-slim-trixie`. Instagram keeps `golang:1.27-bookworm` with a Debian
bookworm runtime. These bases are digest-pinned: update the digest to adopt a
patch; `--pull` alone does not advance it. uv is copied from the official 0.13.4
image with patched embedded roots, and Periphery explicitly upgrades system CAs
for its HTTPS Git fetches. Rebuild,
test and redeploy to apply updates; running containers do not update themselves.
The computer is managed separately via `nora-computer build` and the
[computer lifecycle](computer/README.md): its Ubuntu digest, distro Python and
Chrome pins stay independent, with official Node 24 copied from the Node stage.

Bare Compose commands require a runtime `.env` sufficient for interpolation;
they do not read `secret.toml`. Host exec uses the
[installed helper](../docs/configuration.md#host-migration-and-installed-files).

## First installation and checkout use

A complete supported clean-host walkthrough remains tracked in
[issue #368](https://github.com/mikeng-io/nora/issues/368). The installed Komodo
procedure assumes an existing runtime; it is not a first-install recipe.
The old Signal-only bootstrap commands named the retired standalone service
and are removed rather than presented as a working installation path.

Prepare private `config.toml`, `identity.toml`, `policy.toml` and credential
sources from their examples. Provision an OpenAI-compatible model endpoint,
external Honcho and the selected platform accounts; the `honcho_memory` network
must exist. Configure bridge endpoints for bridge mode, not the old
`signal-cli:18080` direct endpoint. Preserve platform session directories and
use generated credentials, never example identities or production data in Git.
Disabling an integration in config does not remove its Compose interpolation
requirements or dependencies. A selected-platform bootstrap must account for
those requirements before it can be called supported.

The root file remains available for deliberate source builds and isolated
checkout deployments. `make build` builds the local Nora image; it does not
publish or deploy a release. Bare Compose requires an environment sufficient
for interpolation and does not read `secret.toml`. Follow the selected deployment
mode's procedure rather than running the root project without service selection.

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

The repo-root `docker-compose.yml` defines Redis, PostgreSQL, RustFS and embedder
infrastructure maintained from the persistent runtime root. Komodo owns
application services through the generated `deployments` Compose manifest;
their release images are built from subtrees here (`stt/`, `voice-sidecar/`, …).
The newest:

* **`voice-sidecar/`** — the Node audio-controller (design/voice-channel/audio.md):
  the SOLE holder of the Discord voice connection. Joins a channel,
  DAVE-decrypts per-user audio and forwards PCM over a local TCP seam
  toward the Python wake gate, AND drives outbound music/speech playback
  itself over an HTTP control plane (`nora/infra/voice/http_controller.py`)
  — no longer receive-only, the stage-2 name from before nora stopped
  holding its own voice connection. See
  `design/voice-channel/audio.md` and
  `deploy/voice-sidecar/README.md`.
* **`voice-wake/`** — Python wake gate (stage 3), the seam's only consumer:
  openWakeWord 「喂 Nora」 detection per user, utterance collection,
  transcription via the local `stt` service, and the transcript POSTed to
  Nora's `/app/v1/voice/utterance` as text. Audio is transient end to end.
  See `deploy/voice-wake/README.md`.

## Monitoring

See [observability](../docs/observability.md) for SigNoz metrics, traces, logs
and delivery diagnostics. Health and dependency metrics answer different questions;
monitor both.
