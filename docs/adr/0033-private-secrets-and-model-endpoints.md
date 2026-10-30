# 0033 Private secrets and named model endpoints

- **Status**: accepted; operator-authorized implementation, 2026-10-30
- **Date**: 2026-10-30
- **Amends**: DEC-8 credential-source contract; retains settings/identity/policy separation
- **Index**: DEC-325

## Context

Issue [402](https://github.com/mikeng-io/nora/issues/402) identifies consumers
outside config references and native images that cannot parse TOML. Shared settings
mounts include speech services; placing credentials there broadens readership.
The operator authorized one integrated migration and selected singular `secret.toml`.

[Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
operates before service creation. A file mount alone cannot satisfy required
interpolation or a third-party image's native environment interface.

## Decision

Private `secret.toml` has a single `credentials` table keyed by explicit environment
identifiers. Existing `*_env` settings name those entries and their environment
overrides. Present environment wins, including empty; absent falls back to the file.
No naming prefix is required. No process-global environment mutation supplies secrets.
The installed deployment wrapper resolves host sources and projects native Compose
interfaces and configured aliases to their readers. It never mounts the full source
into sidecars. The source and generated delivery files remain private and untracked.

Named providers select explicit API formats; only OpenAI Chat Completions is active.
Per-purpose routes may select a provider/model pair; a different provider requires
an explicit model. Existing direct media transports retain their session headers.
OAuth bootstrap storage changes; rotating state, locks, keeper and fingerprints do not.

Settings and secrets are boot-time. Identity/policy retain validate-then-swap reload.
Controlled scoped recreation refreshes process environments. Repository comments
are short local descriptions; architecture, rationale and decisions belong in
maintained docs, with pointers where useful. Existing comments are not mechanically
removed across the repository.

## Consequences

The existing environment-only deployment remains valid. Host migration retains
legacy `.env` during transition, with the same bootstrap bytes and all OAuth state.

**Worse**:

- Private files are plaintext; mode 0600 does not isolate programs sharing one UID.
- Generated Compose inputs contain credentials and require private backup/cleanup.
- Installed wrapper/helper and computer lifecycle files need explicit host updates;
  merging the fetched clone does not install them.
- Legacy `.env` entries override file entries until deliberately retired under lock.

Reopen if demonstrated multi-host authorization/rotation needs justify a vault or
an endpoint needs an independently proven native adapter.

## Rejected

A generic vault/provider framework, global environment mutation, full secret mounts
in third-party images, hot reload of boot settings, and rewriting rotating tokens
into TOML add scope or weaken existing boundaries.
