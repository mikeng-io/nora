---
type: operations
title: Configure and operate Nora
description: The separate lifetimes of runtime configuration, identity, access policy, persistent state and deployment evidence.
tags: [configuration, policy, deployment, operations]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:28:42.613Z
sources:
  - id: openwiki-source-362e06c30ccfdafd87339cb0
    resource: repo://ARCHITECTURE.md
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-c3657a3ebdffb63a928f5aec
    resource: repo://nora/policy.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "codex", at: "2026-10-20T00:28:42.613Z" }
---

A working installation needs explicit model services, platform accounts, storage and room grants. Repository examples describe input shapes; they are not account configuration or evidence of live enablement.

## Three files, three lifetimes

| Input | Owns | Changes when |
| --- | --- | --- |
| `config.toml` | Services, models, timeouts and optional capabilities | Deployment changes |
| `identity.toml` | Platform accounts resolved to people | Someone joins or changes accounts |
| `policy.toml` | Allowed rooms, units and grants | Trust or participation changes |

Real files and `.env` stay untracked. Start from their example files and keep credentials in operator-owned environment state. Validate a candidate with the configuration CLI before installing it. Never copy live values into wiki pages or fixtures.

Policy is deny-by-default. Missing policy, no allowed conversations, duplicate room identities or ambiguous ownership are startup errors. Room labels provide model-readable references without exposing transport identifiers. Units are memory boundaries, not merely display groups.

## Enable the whole path

A capability can depend on a service, a feature switch and room permission at once. Indexed retrieval and embedding have separate switches; Persona writers and rendering have separate switches. Computer use needs its daemon and room grants. Global enablement does not authorize every room.

Read source and example configuration together. Historical plans cannot establish current wiring. See [memory](../architecture/memory.md) for migration distinctions and [computer work](../workflows/computer.md) for the sandbox boundary.

## Preserve state across image changes

Compose keeps runtime data, platform registrations and rotating OAuth state outside the image. Their persistence must survive container replacement. Back up stores before changes whose rollback depends on them.

Application code and runtime skills come from the image. Doctrine and Dreamer instructions currently use read-only directory mounts; valid hot-reloads can change instructions without rebuilding the image. An image revision alone therefore does not identify every instruction used in a turn.

Rollback must account for schema and configuration compatibility. Follow the deployment procedure and read back the exact image, configuration and processes after recreation. A merge or release does not itself deploy Nora.

## Measure the right thing

Health, metrics, traces and records answer different questions. Health shows dependency/platform status; counters can establish a background path ran; traces expose model input and trajectory; records establish what was stored or delivered. Logs alone are weaker completion evidence.

A green deterministic gate does not prove model judgement, live enablement or external trace persistence. Keep those outcomes separate when reporting a deployment or fix.

Continue with [contributing and verification](contributing.md) or [the runtime flow](../architecture/ambient-cognition.md).
