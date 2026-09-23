---
type: design
title: "instagram-bridge as an Application — layers, domain, REST resources, tracing and logging"
description: "The bridge is a second HTTP application: 7.7k lines in one Go package with no tracing and printf logging. Every bug this bundle has recorded was a missing boundary. This fixes the boundaries — Clean Architecture layers, a small domain with typed ids, one upstream-call helper, REST resources, and OpenTelemetry into the existing collector — and migrates to them incrementally, observability first."
tags: [nora, instagram, bridge, architecture, clean-architecture, ddd, rest, opentelemetry, jaeger, logging, conventions]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 15-bridge-is-the-only-entry.md, rel: realizes, provenance: asserted}
  - {dst: 03-transport-contract.md, rel: extends, provenance: asserted}
  - {dst: 10-discovery-retrieval-and-paging.md, rel: constrained-by, provenance: asserted}
---

# instagram-bridge as an Application

## Why now

`15` makes the bridge the only way Nora reaches Instagram. That turns it from a
small transport shim into Nora's second HTTP application, and it is not built
like one. Measured 2026-09-23: 7,696 lines, 17 files, all in `package main`,
no tracing at all, and logging through `log.Printf` (`logf`). The only other
Go service in `deploy/` is this one (`whatsapp-bridge` is Node), so whatever
this document sets is the Go convention.

Every Instagram bug recorded in this bundle so far was a **missing boundary**,
not bad logic:

| Bug (all 2026-09-22) | Boundary that would have prevented it |
|---|---|
| A username sent into `PolarisProfilePageContentQuery`'s numeric `id` field, failing 4/4 | typed ids: `IGID` and a username are different types, so the call does not compile |
| A GraphQL `field_exception` inside an HTTP 200 read as success, hiding the above for a day | one upstream-call helper that checks transport status **and** the GraphQL `errors` array, and marks its span failed |
| The route returning Instagram's raw key lists | wire structs confined to the gateway; responses built only from domain types |
| Verb-shaped routes (`/send`, `/react`, `/profile-social` proposed) | resource paths, set once, here |
| The bridge call invisible inside Nora's trace | trace context propagated on every call |

## Layers

Clean Architecture's dependency rule, applied to a Go service: dependencies
point inward only.

```
deploy/instagram-bridge/
  cmd/instagram-bridge/main.go   composition root: config, wiring, start. Nothing else.
  internal/
    domain/           ids, entities, domain errors. Imports stdlib only.
    app/              use cases + the ports they need. Imports domain.
    gateway/instameow/  implements app's ports over instameow. Imports app, domain, instameow.
    transport/http/   routes, handlers, request parsing, response DTOs, error→status. Imports app, domain.
    session/          session lifecycle state machine (today's session.go).
    stream/           SSE event hub (today's stream.go).
    observability/    tracer provider, logger, propagation. Imported by main and middleware.
```

| Layer | Owns | May import | Must not import |
|---|---|---|---|
| `domain` | `IGID`, `FBID`, `ThreadRef`, `Username`, `Account`, `Story`, `Post`, `Message`, domain errors | stdlib | anything else |
| `app` | use cases (`ReadAccount`, `ReadStory`, `SearchAccounts`, `ReadPost`, `ListPosts`, `SendMessage`, `React`, …) and port interfaces | `domain` | `net/http`, instameow, transport |
| `gateway/instameow` | Instagram wire structs, raw GraphQL/REST calls, wire → domain mapping | `app`, `domain`, instameow | `transport` |
| `transport/http` | paths, JSON DTOs, status mapping, middleware | `app`, `domain` | instameow, wire structs |

An architecture test (`go list -deps` per package, asserted in a Go test) pins
the "must not import" column, the same way `tests/architecture/` pins Python's.

## Domain: small, and typed where the bugs were

Domain-Driven Design, applied lightly. The bridge holds no state beyond the
session, so there are no repositories, no aggregates with invariants to
guard, and no event sourcing. What DDD contributes here is **a vocabulary with
types**:

- **Two contexts.** *Messaging* (threads, messages, reactions, members) and
  *Social* (accounts, profiles, stories, posts). They share the session and
  the gateway, and nothing else. A handler never mixes them.
- **Ids are value objects, not strings.** Instagram has three id spaces plus
  two thread spaces (`02-identity-and-policy.md`), and mixing them fails
  silently because every one is a decimal string. `IGID`, `FBID`,
  `ThreadRef{Short, Long}` and `Username` are distinct types with parsing
  constructors (`ParseIGID` canonicalizes leading zeros). A function that
  takes an `IGID` cannot be handed a username.
- **Domain errors are a closed set:** `ErrInvalidID`, `ErrNotFound`,
  `ErrNeedsHuman` (checkpointed, logged out), `ErrUnavailable` (transport
  down, expected to heal), `ErrUpstreamRejected` (Instagram answered, and the
  answer was an error), `ErrUnsupported` (no mechanism exists, e.g. posts
  list today). The gateway maps every upstream failure onto one of these;
  transport maps each one onto one status. Nothing else crosses a layer.

## One way to call Instagram

The gateway has **exactly one function per upstream kind** through which
every call passes: `graphql(ctx, doc, vars)` and `rest(ctx, endpoint, …)`.
Each one:

1. starts the upstream span (§Tracing);
2. checks the transport status;
3. **for GraphQL, decodes the `errors` array before `data`, and treats a
   non-empty one as failure**, which is the check whose absence hid a bug
   for a day;
4. maps the failure to a domain error;
5. returns bytes only to the caller that parses them into wire structs.

No handler and no use case calls instameow directly. A new capability is a
new gateway method built on these helpers, never a new raw call site.

## Resources

REST resources are nouns. The bridge talking about **itself** is not a
resource and keeps its operational paths.

| Resource | Method and path | Replaces |
|---|---|---|
| bridge health | `GET /health` | — |
| event stream | `GET /events` (SSE) | — |
| account | `GET /accounts/{igid}` | — |
| account search | `GET /accounts?query=` | OpenCLI `search` |
| story | `GET /accounts/{igid}/story` | — |
| posts | `GET /accounts/{igid}/posts?cursor=` | OpenCLI `user`; returns `ErrUnsupported` until a mechanism exists |
| one post | `GET /posts/{shortcode}` | — |
| members | `GET /threads/{thread_fbid}/members` | — |
| message | `POST /threads/{thread_fbid}/messages` (text or media) | `POST /send`, `POST /send-media` |
| unsend | `DELETE /threads/{thread_fbid}/messages/{message_id}` | `POST /unsend` |
| reaction | `PUT /threads/{thread_fbid}/messages/{message_id}/reaction` | `POST /react` |
| typing | `PUT /threads/{thread_fbid}/typing` | `POST /typing` |

Conventions:

- **Path ids parse into domain value objects at the edge.** A malformed id is
  400 before any use case runs.
- **Success is the resource**: no `ok` field on a 200. **Failure is one
  envelope** on every route: `{"error": {"code": "...", "message": "..."}}`,
  `code` from the closed set above. The status carries success or failure, as
  `03-transport-contract.md`'s table already requires: 409 needs a human, 503
  retryable, 400, 404, 413, 500. Add 502 for `ErrUpstreamRejected`, and 501
  for `ErrUnsupported`, which R-27 surfaces as `unavailable` with its reason.
- **Lists are `{"items": [...], "next_cursor": "..."}`** with
  `10-discovery-retrieval-and-paging.md`'s cursor rule: opaque, `null` is the
  end, absence never means more.
- **JSON is snake_case.** DTOs live in `transport/http`, built from domain
  types, never from wire structs. What Nora reads is decided here, once.
- **No version prefix.** One internal caller, in this repository; a path
  change moves the Python caller in the same PR, and a contract test pins
  every shape the Python side reads.

## Tracing

Mission-critical, and the infrastructure already exists. Measured
2026-09-23 on norty: an OpenTelemetry collector (`otel-collector`, OTLP on
4317/4318) fans out by span: traces containing a model call go to Langfuse
(tail sampling on `gen_ai.operation.name`), and everything else goes to Jaeger
(`jaeger`, UI on 16686) with the LLM payload spans dropped. Its config says
it outright: *"Adding a new app class = config change here, never an app
change."* The bridge needs **no collector change**.

- **Export.** OTLP/HTTP to `http://host.docker.internal:4318/v1/traces`,
  exactly as Nora's own tracer does (its `endpoint` in `config.toml`), with
  `service.name=instagram-bridge`. The bridge container needs
  `extra_hosts: host.docker.internal:host-gateway`, which `nora` has and
  `instagram-bridge` does not (measured).
- **Propagation.** W3C `traceparent`. **Nothing in Nora injects it today**
  (measured: no `propagate.inject` anywhere in `nora/`), so every bridge call
  is currently a separate, unlinked trace. The Python bridge clients inject it
  on every request; the bridge's HTTP middleware extracts it. One trace then
  runs from Nora's turn through the `social_fetch` tool into the bridge and
  out to Instagram. Because that trace contains Nora's model span, the
  collector keeps it whole in Langfuse too.
- **Server spans** are named by **route pattern** (`GET /accounts/{igid}`),
  never the raw path.
- **Upstream spans**, one per Instagram call, from the gateway helpers:
  `instagram.graphql PolarisProfilePageContentQuery`, `instagram.rest
  reels_media`. Attributes: `http.response.status_code`,
  `instagram.graphql.error_code`, `error.type` (the domain error). **Status
  ERROR on any failure, including a 200 carrying GraphQL errors.** That span
  would have shown the `field_exception` bug in red on the first request.
- **Inbound events** (Instagram → `/events`): a span per event, with its
  `traceparent` carried in the SSE payload so Nora's ingest continues the same
  trace. Phase 2; the request/response path comes first.
- **Allowed in spans:** igid, thread id, message id, route, status, error
  codes (operator-only infrastructure, needed to debug). **Never:** message
  text, captions, usernames typed by people, cookies, tokens, and signed CDN
  URLs, which grant access to whoever holds them.

## Logging

- **`log/slog`, JSON to stdout.** One handler, installed in `main`, that adds
  `trace_id` and `span_id` from the context to every record, so a Jaeger span
  and its log lines join on one id.
- **`logf` is removed.** Every log call takes a context.
- **instameow's own zerolog output** (`main.go` builds a console-writer
  zerolog today) is switched to JSON on the same stdout, so there is one
  stream with one format.
- **Levels:** `error` = a request failed for a reason an operator must see;
  `warn` = degraded but served; `info` = lifecycle (connect, checkpoint,
  reconnect); `debug` off in production. Per-request success is **not**
  logged. The span already records it.
- **The same never-list as spans.**

Metrics are out of scope for v1: the collector has no metrics pipeline.
Prometheus on norty is the later home.

## Tests, per layer

| Layer | Test | Seam |
|---|---|---|
| domain | pure unit: id parsing, canonicalization, error identity | none needed |
| app | use cases against a fake port | the port interface. PR #387 had to bolt a seam on (`graphQLIssuer`); here it is the default |
| gateway | parse tests against **literal captured wire bytes**, including every failure shape seen live (the `field_exception` envelope) | the helpers take bytes |
| transport | `httptest` against the app with fake ports; status and envelope per domain error | handlers take use cases |
| contract | every JSON shape the Python side reads, pinned from both sides | shared fixtures |

Each regression test is proven by reverting its fix and watching it fail.

## Migration: incremental, observability first

No rewrite. Each step is one PR, keeps every existing test green, and changes
no behaviour Nora can see, except step 5, which moves Python in the same PR.

1. **Observability on the current code.** OTLP export, `otelhttp`
   middleware, slog with trace ids, upstream spans around today's instameow
   calls, `traceparent` injection in the Python clients. First, so that every
   later step is itself observable, and a refactor that changes behaviour
   shows up in Jaeger rather than in a room.
2. **`domain`**: id value objects and domain errors, adopted by the existing
   handlers.
3. **`gateway/instameow`**: the two upstream helpers; every instameow call
   moves behind them; wire structs move with them.
4. **`app`**: use cases and ports; handlers become thin.
5. **`transport/http` and resource paths**: new paths, error envelope, DTOs;
   the Python adapter moves in the same PR; old verb paths removed there.
6. **New capabilities** (`15`'s migration steps 2–3) are built only after
   step 4, on the gateway and app layers, never on the flat code.
