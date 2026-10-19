# Nora

Nora is an experimental AI companion for a circle of friends. She follows
conversations across Signal, Discord, WhatsApp and Instagram, remembers shared
context, and replies through the Strands Agents SDK. Participation, reminders,
voice and background cognition are configured separately and governed by room
policy.

## Start here

- [Architecture](ARCHITECTURE.md): runtime boundaries, data and deployment.
- [Contributing](CONTRIBUTING.md): development checks and pull requests.
- [Decisions](docs/adr/README.md) and [design index](design/09-decision-index.md):
  rationale, trade-offs and design work, including proposals not yet implemented.
- [Operator runbook](docs/runbook.md): diagnosis, configuration and recovery.
- [Agent instructions](AGENTS.md): procedures for coding agents.

## Development

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). From a checkout:

```bash
uv sync --frozen --extra evals
make lint typecheck imports
uv run pytest -q tests/test_docs.py tests/test_architecture_doc.py
```

`make gate` runs static checks and the tests affected by the branch against
`origin/master`; unknown changes run the full suite. Database and object-store
tests need Docker. Live model evaluations are separate and need operator-owned
services and credentials; ordinary contributors do not need production access.
See [Contributing](CONTRIBUTING.md) for the full workflow and local hooks.

## Configuration

The tracked examples describe three separate inputs:

| Example | Purpose |
|---|---|
| [config.example.toml](config.example.toml) | Services, models and optional capabilities |
| [identity.example.toml](identity.example.toml) | Platform accounts mapped to people |
| [policy.example.toml](policy.example.toml) | Allowed rooms, social units and grants |

Real `config.toml`, `identity.toml`, `policy.toml` and `.env` stay outside version
control. Secrets such as `OMNIROUTE_API_KEY` are environment values, never
values to paste into documentation. Example files are starting points, not a
ready-to-run production deployment: platform accounts, memory and model
services must be provisioned and policy must explicitly allow the intended rooms.

Nora's character is in `nora/doctrine/`, including `rooms/voice.md`. With the
current deployment's read-only bind mounts and `doctrine.reload = "mtime"`,
a valid edit is picked up on the next read with **no restart**. Broken reloads
retain the last good version. Runtime skills are baked into the image and need
a rebuild. Changes to character and constraints follow the ownership rules in
[AGENTS.md](AGENTS.md).

## Deployment

The current deployment is Docker Compose on Norty. The application uses bridge
networks; `build.network: host` is a build-time DNS workaround, not its runtime
network mode. Host administration is through Tailscale.

[Deployment](deploy/README.md) describes the current build/recreate procedure
and a proposed pull-based GitOps replacement. Production deployment is still
operator/agent-driven; merging a PR does not install a deployment controller.
The [runbook](docs/runbook.md) carries health checks and rollback constraints,
including database migrations. Do not use old phase tags as rollback targets.

Releases are managed by [release-please](.github/workflows/release-please.yml).
A release and a deployment are separate: the image records the source revision
and, when available, its generated version manifest.
