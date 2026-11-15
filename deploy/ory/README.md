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
these outcomes. Image pinning and wired managed-backend integration remain
service-integration obligations.
