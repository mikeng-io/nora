# Nora — Architecture

This is the current implementation map. Decisions and their trade-offs belong
in [ADRs](docs/adr/README.md) and the [design index](design/09-decision-index.md).
Design proposals and historical measurements do not imply a deployed feature.
Enabled capabilities and room grants are deployment configuration.

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
reminders. The Dreamer supplies background research, including culture work;
it is not the retired set of note-extraction/curation/proactive adaptors.
Voice and asynchronous compute are additional paths into this runtime, not
independent copies of Nora's policy or memory.

## Code map

Import contracts in `pyproject.toml` and architectural tests enforce boundaries.
The domain is framework-free; application composition supplies infrastructure
and configuration to the runtime.

| Package or module under `nora/` | Responsibility |
|---|---|
| `app/` | Composition, dispatch, background lifecycle and cognition wiring |
| `domain/` | Shared models, ports and domain rules |
| `platform/` | Messaging adapters and platform-specific transport |
| `ingest/` | Normalize the processing path, record events, media and stimuli |
| `engagement/` | Admission, context, invocation, delivery and feedback |
| `agent/` | Strands integration, tools and context assembly |
| `infra/` | SQL stores and migrations, memory clients and external services |
| `presence/`, `analytics/` | Presence collection and derived location history |
| `language/` | Shared context renderers and background adaptors |
| `doctrine/` | Character content, audience selection and validated loading |
| `instructions/` | Shared instruction parsing, hashing and reload |
| `dreamer/`, `subagent/` | Model-facing instruction corpora |
| `skills/` | On-demand runtime skills and their loader |
| `documents/` | Structured document validation |
| `web/` | Portal API and web-facing application services |
| `obs/` | Health, metrics, logging and tracing |
| `subject/`, `testing/` | Evaluation subject and shared test doubles |
| `config.py` | Validated deployment configuration |
| `config_diagnostics.py` | Safe schema coordinates and value-free validation explanations shared by private TOML loaders. |
| `persons.py`, `persons_file.py` | Identity resolution and identity-file loading |
| `policy.py` | Room membership and grants |
| `tag.py` | Trigger matching and debounce helpers |

The portal frontend is in `app/`; the public site is in `site/`. Service
implementations and container build contexts live under `deploy/`.

## State and trust boundaries

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

The existing agent procedure builds on the deployment host, validates configuration,
recreates services and verifies image/process state. Rollback must account for
schema and configuration compatibility, not just retag an image.
[Deployment](deploy/README.md) records the proposed migration to host-side
pull reconciliation; it is not active. No Kubernetes runtime is required by
the current stack.

## Verification

`make gate` runs lint, scoped type checks, import contracts and affected tests.
The suite includes real database/container tests; model behaviour is evaluated
separately. A green gate proves neither production health nor the enabled
configuration of a running installation. The package-coverage check in
`tests/test_architecture_doc.py` keeps this map connected to the tree.
