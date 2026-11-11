# Nora — Architecture

The map of the system: how it runs and where its code lives. Decisions and their
costs are in [ADRs](docs/adr/README.md) and the [decision index](design/09-decision-index.md);
enabled capabilities and room grants are deployment configuration.

## Continuity beyond a session

Nora is a social agent whose continuity belongs to persistent state, scoped to
people, rooms and social units. Messaging and voice are interfaces into that
state. Each speaking turn constructs a fresh Strands agent from assembled
context, with no SDK session manager; background cognition has its own bounded
sessions. Sessionless operation means continuity independent of a chat session,
not an absence of sessions inside the implementation.

The connected paths run at different timescales:

- Ingestion records observations and raises durable stimuli.
- Cognition reads a grounded agenda, maintains ongoing matters and working
  state, and can schedule its own next look or a proactive occasion.
- Participation uses conversation windows and, when configured, a relevance
  gate to admit an unrequested turn. A proactive occasion re-reads the situation
  at action time and may remain silent.
- The computer supplies a persistent, room-scoped workspace through computerd;
  files and process state can outlive a speaking turn. Browser tasks have a
  separate bounded agent runner.
- Response episodes connect her sends with subsequent responses. Lessons and
  her own character account derive contextual learning from those outcomes;
  this changes stored context, not model weights or human-authored doctrine.

The prefill brings the relevant state back into subsequent turns. Shared
continuity does not grant shared access: the existing admission, read and
disclosure boundaries still apply. These are implemented paths, not claims
about which features a deployment has enabled. The memory migration's remaining
Honcho dependency is described below.

## Runtime

Platform adapters normalize Signal, Discord, WhatsApp and Instagram events.
The composition root in `nora/app/composition.py` wires dependencies;
`nora/app/dispatch.py` connects ingestion, engagement and background work.

```text
Platform events → ingestion → PostgreSQL record / media custody / memory feed
                       ↓
                 admission + room policy
                       ↓
             context assembly → Strands agent → delivery → recorded outcome

Recorded stimuli → cognition and scheduled background work
                          ↓
                 scoped state and governed actions
```

Ingestion records permitted events and resolves identities before engagement.
Tags enter the engagement pipeline; participation and scheduled occasions have
separate admission rules. Ambient processing can also transcribe or interpret
media when configured, so it is not universally a model-free path.

Context assembly combines doctrine, transcript and scoped state. Tools provide
additional reads and actions; delivery is explicit, not whatever prose the
model happens to produce. Tool results distinguish success, absence and
unavailability. Policy controls disclosure and whether Nora may speak in a
room, including when queued work is delivered.

Cognition processes stimuli and maintains working state, notes, events and
reminders; the Dreamer supplies background research, including culture work.
Both share one agent factory, tool set and instruction infrastructure
([execution and time](design/cognition-loop/18-execution-and-time.md)). The
reminder heartbeat drives deferred conscious turns; addressed turns continue
through engagement.

Voice and asynchronous compute are additional paths into this runtime, not
independent copies of Nora's policy or memory.

## Code map

Code is grouped by **area**, one package per subject designed under `design/`
([ADR 0037](docs/adr/0037-code-is-grouped-by-area-and-depends-inward.md)), in
three layers importing inward ([rules](nora/AGENTS.md), enforced in `pyproject.toml`).

| layer | holds | may import |
|---|---|---|
| `domain/` | models, rules, the ports the area needs | stdlib, `nora.domain` |
| `app/` | the area's use cases | its domain |
| `adapters/` | stores, clients, tools and context blocks | its app and domain, frameworks, `nora.infra` |

`wire.py` builds an area's adapters and the composition root assembles areas;
an area reaches another only through its root. New code goes to its area.

| area | subject |
|---|---|
| `conversation` | rooms, the Record, messages, threads, edits, reactions, polls, replies |
| `memory` | the Notebook, notes, memos, Standing, rollups, profiles, recall |
| `people` | identity, policy, units, who is online |
| `location` | presence, places, movement, routes, transit, parking, weather |
| `media` | images and video: custody, comprehension, generation |
| `social` | web search and fetch, the social networks |
| `computer` | the computer unit, its sessions, the browser, artifacts |
| `documents` | structured documents the portal renders |
| `agenda` | gatherings, reminders, wakes |
| `voice` | voice channels, speech, music |
| `subagents` | delegated agent work |
| `self` | culture, lessons, her account of herself |

The shared runtime: `platform/`, `ingest/`, `engagement/`,
`agent/` (Strands, tool registry, context assembly), `app/` (dispatch,
lifecycle, composition root), `language/`, `doctrine/`, `instructions/`,
`skills/`, `dreamer/`, `subagent/`, `web/` (portal API), `obs/`, `infra/`,
`presence/`, `analytics/`, `subject/`, `testing/`, `config.py`, `secret.py`,
`config_reload.py`, `memory_routing.py`, `tag.py`, and `domain/`, the shared
kernel. `documents/` and `persons.py`, `persons_file.py`, `policy.py` (`people`)
are area code still to move.

Outside `nora/`: the portal in `app/`, the site in `site/`, services in `deploy/`.

## State and trust boundaries

The optional `[content_checks]` filters default off. Their retained phone/UUID/
coordinate, place-query/person-name and location audience checks can be restored
with `enabled = true` and a restart. Credential-pattern blocking and doctrine
LeakGuard always run; authentication, room/unit scope, location opt-in/collection
consent, freshness and unavailable/error handling remain independent. See
[the decision](docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md).

PostgreSQL is the record store, with Alembic migrations applied at startup.
Honcho supplies durable interpreted memory; it is not the authoritative record.
Media custody uses the configured blob store, with RustFS supplied in Compose.
The SQL memory index and embedding server support graph/vector retrieval.
Honcho is still wired while the unified-memory migration proceeds; its planned
retirement is not a completed cutover. Records and derived context
are scoped by people, rooms and social units rather than a global shared chat.

Three untracked files have distinct lifetimes: `config.toml` configures the
runtime, `identity.toml` maps accounts to people, and `policy.toml` grants access.
Secrets stay in host-owned environment/state. OAuth refresh state and platform
registrations are persistent data and must survive image replacement.

A deployment that bind-mounts `nora/doctrine` and `nora/dreamer` can hot-reload
that content on mtime. Other application code and runtime skills come from the
image. Operators using this option must track the mounted content alongside
the image revision when reconstructing running behaviour.

## Deployment and operations

[docker-compose.yml](docker-compose.yml) is the executable topology:

- `nora`, `presence` and `analytics` share the `nora:latest` image today.
- Messaging bridges, portal/site, speech/voice services, PostgreSQL, RustFS,
  embedding and monitoring services have their own lifecycle and state.
- Nora uses the `nora` bridge plus the external internal `honcho_memory`
  network and the internal embedding network. It reaches co-located host
  services through `host.docker.internal` where configured. Host-network
  **builds** do not mean host-network **containers**.
- Nora publishes health and metrics on host loopback port 9100. Health combines
  dependency and platform status; inspect degraded-platform detail as well as
  the HTTP status. Prometheus/Alertmanager and tracing serve different needs;
  [observability](docs/observability.md) and the [runbook](docs/runbook.md)
  describe diagnosis.

Application releases deploy through the installed Komodo Stack: a published
release commits its image bundle to the `deployments` branch and Komodo's poller
applies it, then writes a receipt. The checkout procedure (build on the host,
validate configuration, recreate services, verify image and process state)
remains for infrastructure services and is legacy for the application. Rollback
must account for schema and configuration compatibility, not just retag an
image. [Deployment](deploy/README.md) describes both paths. No Kubernetes runtime
is required by the current stack.

## Verification

`make gate` runs lint, scoped type checks, import contracts and affected tests.
The suite includes real database/container tests; model behaviour is evaluated
separately. A green gate proves neither production health nor the enabled
configuration of a running installation. The package-coverage check in
`tests/test_architecture_doc.py` keeps this map connected to the tree.
