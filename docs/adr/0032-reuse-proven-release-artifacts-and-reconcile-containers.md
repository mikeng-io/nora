# 0032 · Reuse proven release artifacts and reconcile containers

- **Status:** accepted (operator, 2026-10-28)
- **Date:** 2026-10-28
- **Extends:** ADR 0031's complete release-to-Komodo pipeline
- **Index:** DEC-322

## Context

The operator requested selective builds and recreation in
[issue #324](https://github.com/mikeng-io/nora/issues/324). The publisher added the
selected global source/version labels to every image, and the installed wrapper
forced recreation of all owned services. Unchanged transports therefore restarted
on every release. Docker caching alone cannot preserve an image whose labels change.

[Docker build contexts](https://docs.docker.com/build/concepts/context/) define the
files available to a recipe. [Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)
already recreates services when their image or effective configuration changes.
Bind-file contents, however, do not change that configuration hash: loaded-once
host files still need an explicit recreation request. Disposable publisher and
twelve-service Compose controls demonstrated both behaviors before production use.

## Decision

Keep a complete twelve-service manifest with ten artifact records. Separate the
selected release identity from each artifact's original source/version/channel.
Reuse the exact prior immutable artifact only when conservative Git context,
reviewed Docker recipe, retained publisher/workflow inputs, platform and verified
Signal download fingerprints match. Check registry availability. Build from a Git
tree export, supplemented only by the generated Nora stamp or verified Signal
downloads. Legacy manifests bootstrap with full builds; unavailable or malformed
proof never authorizes a skip. Nora rebuilds for its global version/source stamp;
Nora, presence and analytics share that artifact.

Reconcile the complete owned cohort through native Compose without forcing it.
A one-shot host-local request lists config/identity/policy file names and forces
only their owned mount readers. Consume it after full image/health/stamp readback.
Retain the shared lock, candidate validation, pull-before-stop, failed-attempt
marker, private diagnostics and native recovery. Install the reviewed wrapper
outside Git under the same lock before claiming live selective recreation.

## Consequences

Unchanged artifacts retain their digest and truthful OCI provenance; unchanged
service definitions preserve their running containers. Automatic release cadence,
major approval, config custody and infrastructure ownership remain unchanged.

**Worse:**

- Conservative context hashing counts ignored files. The site's root context can
  rebuild on unrelated changes; Nora's stamp requires rebuilding every release.
- Recipe equality does not refresh mutable apt catalogs. Dependency/base/recipe
  changes explicitly request refresh; this is reuse proof, not reproducible builds.
- New Dockerfile recipes build until reviewed for reuse. A missing registry object
  refuses publication instead of guessing that an old digest remains available.
- Retained containers retain historical restart counts; verification binds the
  pre-deploy count to each container ID and rejects any increase. New containers
  require zero restarts. Host-file edits need the explicit scoped request.
- The first bundle lacks prior fingerprints and therefore rebuilds all artifacts.

## Rejected

Blind path filters, rewriting reused artifact labels as the new source, removing
force recreation without preserving config reload, a custom deployment scheduler,
and cache-only changes. A narrower site build context can follow measured waste.

This holds unless a missed effective input, incorrect provenance or measured
dependency-refresh requirement invalidates the reuse contract. Such evidence
disables reuse until repaired rather than weakening publication verification.

## Enforcement

`tests/unit/test_release_deployment.py` covers effective-input invalidation,
original provenance, legacy bootstrap, Nora stamp rebuilds, native reconciliation
and scoped reload consumption. Runtime acceptance separately compares selected
digests, container identities, the installed receipt and Nora's live stamp.
