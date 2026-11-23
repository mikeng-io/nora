# Source adapters

A **source adapter** is the plugin that brings one outside source into Nora:
it reads or receives the source, records each change of state as an item
through intake, reports the source's health, and serves on-demand reads of one
reference. A new network is a new adapter, never a new table family, loop or write
path.

## Ports by source shape

An adapter implements the port of the shape its source produces; a network
that produces both implements both.

| port | source produces | items | examples |
|---|---|---|---|
| **item source** | discrete things that are published and may change: posts, stories, reels, threads | one per thing, and one per change of its state | Instagram, X |
| **activity source** | a state that holds until it changes: online, idle, playing, listening | one per change of state | Steam, Discord status |

Every port declares:

- its **network** and **capabilities**; a capability the network lacks is
  absent, never an error;
- its **cost** per read, so the collector's budget counts upstream calls;
- its **health**, answered from the adapter's own state without an upstream
  call;
- an **on-demand read** of one reference, used by watches and live reads.

A read never raises: it answers a value or a failure typed by what the caller
does next.

## Recording changes, deriving sessions

- An adapter records a **change of state**, never a repeated reading of the
  same state. A poll that sees what the last poll saw records nothing but the
  source's health.
- A **session** — online from one time to another, playing one game, an
  active story — is an episode computed from those items
  ([aggregates](../cognition/observation.md#aggregates)), never written by the
  adapter. Rebuilding sessions from the items at a watermark gives the same
  sessions.
- A session opening or closing is a presence change, and that is the
  stimulus; the change items themselves wake nothing
  ([stimuli](../cognition/observation.md#stimuli)).
- A gap while the source was unhealthy closes no session and opens none: the
  session's coverage records it, and a condition over it evaluates unknown.

## Push and poll

| source | how it arrives | where the adapter runs |
|---|---|---|
| a platform that pushes (Discord status) | the bridge receives the gateway event | in the bridge, which emits the change to intake |
| a source that must be polled (Steam, Instagram, X) | the collector polls at its cadence and budget | in the collector process |

- The **collector** is one process, separate from the engagement process,
  that runs every polling adapter on its own cadence, budget and back-off. A
  polling adapter is a module in it, never a new container.
- A bridge that hosts a push adapter stays transport: it emits changes and
  never folds them into sessions.

## Registration

- An adapter package registers through a Python entry point in the
  `nora.sources` group, naming its network, its ports and its configuration
  section. Nora discovers installed adapters at start; configuration enables
  each per agent.
- An adapter receives its credentials by name from the secret contract and
  its accounts from the managed identity: which account belongs to which
  identity is managed identity's, never the adapter's.
- An adapter never writes a table, never names a unit and never reads policy.
  Placement is intake's, by the observation rules.
- A port's contract suite (`tests/contract/`) is the adapter's admission: an
  adapter that does not pass it for every declared capability is not loaded.

## Reads for the loops

- **What happened** — what an identity played this week, when they are usually
  on Discord — is a graph read over recorded sessions and items: scoped by the unit, cheap,
  and historical.
- **What holds now** — is an identity online this minute — is one live activity read
  through `sources`, across every activity adapter, keyed by the unit for the
  reflection loop and by the room for the engagement loop. It is one read
  surface, never one tool per network.
- Both loops may call both reads within their surface
  ([surface](../cognition/surface.md#tools)); a live read is an outside read
  under its spend controls, and its answer is recorded as an item like any
  other observation.

## Why this shape

- **Ports by shape, not by network**: the social poller already never names
  a network; generalising the shape lets X, Instagram and the next network
  share one poller, one table family and one contract suite.
- **Changes, not readings**: thousands of identical readings carry no more
  than one transition, and sessions are rebuildable only if the items they
  fold are the transitions themselves.
- **Sessions computed, not reported**: an adapter that wrote sessions would
  make the episode a claim of one process that no rebuild could check.
- **Entry points**: the standard library's plugin discovery lets a third
  party add a network without editing Nora, and the contract suite keeps the
  plugin honest.
