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
  listeners, `namespaces.location` pointing at the OPL source.

Status: source-verified against the pinned API/config/OPL references, not yet
compiled or served by a running Keto/Kratos — image digests, live syntax
checks and effective-permission cases are Task 2 obligations.
