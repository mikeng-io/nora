# Ory managed-mode contract artifacts (Task 1)

Declarative sources the service integration (Task 2) consumes, pinned at
self-hosted Ory **v26.2.0** (OSS):

- `kratos/person.schema.json` — Nora's identity schema: descriptive traits
  only, no `ory.sh/kratos` extensions, no credentials and no account
  ownership in mutable traits.
- `kratos/kratos.dev.yaml` — development config: sqlite DSN, private
  listeners, `identity.schemas` entry for the person schema.
- `keto/namespaces.ts` — the OPL permission model declared by
  `design/managed-identity-access/authorization.md`.
- `keto/keto.dev.yaml` — development config: sqlite DSN, private read/write
  listeners, `namespaces.location` pointing at the OPL source and strict mode enabled.
  Positive and negative per-permit tuples use `grant_<permit>` and
  `deny_<permit>` respectively; `restricted` is a blanket denial.

The permission check uses a disposable local Keto with the committed namespace
source, strict mode and read-depth setting. Publish its read/write ports on
loopback, keep its store empty and temporary, then run:

```bash
uv run python -m scripts.check_ory_policy --read-url http://127.0.0.1:4466 --write-url http://127.0.0.1:4467
```

It creates generated relationships, checks all scope/permit pairs, conflicting
direct grants, permit-specific and blanket denials, and parent inheritance,
then removes those relationships. OPL source-content tests alone do not prove
these outcomes.

## Disposable local/CI stack

`compose.yaml` runs both services pinned by digest:

- `oryd/keto:v26.2.0@sha256:bfdb8b9e283aedbd9e1d005c44eb1bbefd2096b2cb5b5aa0b7360b6e39c178d2`
- `oryd/kratos:v26.2.0@sha256:2a13bb8d362c7a7ae33bd7c0f5168aee46921f15c916a06346db91c06dc76643`

```bash
docker compose -f deploy/ory/compose.yaml up -d --wait   # migrate, then serve
docker compose -f deploy/ory/compose.yaml down -v        # wipe all state
```

Published ports are loopback-only and match `[identity.managed]`'s defaults:
Keto read `127.0.0.1:4466`, write `127.0.0.1:4467`; Kratos public
`127.0.0.1:4433`, admin `127.0.0.1:4434`. Two container-side adjustments ride
on environment variables so the committed `*.dev.yaml` files stay correct for
a host-side process: `SERVE_*_HOST=0.0.0.0` (the configs' `127.0.0.1` bind is
the container's own loopback) and sqlite DSN overrides under `/home/ory/data`
(the images do not create the configs' `/var/lib` paths). One-shot data services
assign fresh volume ownership to each image's own `ory` user before migrations;
the migrations and servers retain their non-root image user. Migrations run once
per `up`; `serve` waits on their completion. This stack is development/CI
scaffolding — it carries no credentials and no production data.

CI selects the `ory` job for managed source, OPL/schema or dependency changes.
It starts this disposable stack, runs `scripts.check_ory_policy` plus
`scripts.check_ory_clients` against the real listeners, and removes the volumes.
The client check covers credentialless identity creation, exact correlation,
active/inactive read-back, session revocation, scoped grant/deny and cleanup;
it does not claim provider login or a real holder session journey.
