---
type: design
title: "Implementation Plan — the bridge as the only way in, PR by PR"
description: "The concrete order for 15 and 16: observability first, then Nora's read path onto the bridge for people she knows, then the bridge's layers, then links, search and posts on the new layers, then writes onto resource paths. Every PR names its files, its tests, the revert that proves them, and the live read-back that closes it."
tags: [nora, instagram, bridge, plan, sequencing]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 15-bridge-is-the-only-entry.md, rel: realizes, provenance: asserted}
  - {dst: 16-bridge-architecture-and-observability.md, rel: realizes, provenance: asserted}
---

# Implementation Plan

## Done means, for every PR

1. `make gate` green, read from a captured exit code, never through `tail`.
2. A release fragment.
3. Each new behaviour has a named test, **proven by reverting the fix and
   watching that test fail**.
4. A Sonnet subagent review before merge.
5. Deployed by rebuilding the image(s) it touches (`instagram-bridge`,
   `nora`, or both), after checking `git status` on norty's checkout.
6. **A live read-back**: the trace in Jaeger (and Langfuse, where a model call
   is in it), not a test passing and not a 200.

## Order

```
P1 observability ──┬── P2 → P3 → P4 → P5      Nora's read path, people she knows   (Python)
                   └── P6 → P7 → P8           bridge layers                         (Go)
                               P9 probe ─ P10 links ─ P11 search ─ P12 one post     (both; needs P5 + P8)
                                                     P13 posts/reels lists          (investigation)
                                                     P14 writes onto resources      (both; needs P8)
```

**P1 first, alone**: every later PR is verified by its trace, so tracing has to
exist before the first behaviour change it will verify. After P1, the Python
track (P2–P5) and the bridge track (P6–P8) touch disjoint files and can run in
parallel. New bridge routes (P10–P12) are built only on the layers from P8
(`16` §Migration).

## P1: Tracing and logging, bridge and Nora (`16` step 1)

- **Bridge.** Tracer provider (`service.name=instagram-bridge`) exporting
  OTLP/HTTP to the collector; the endpoint comes from
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`. `otelhttp` middleware with span names
  set to the route pattern. One upstream span around every instameow call, set
  to ERROR on transport failure **and** on a 200 carrying GraphQL `errors`.
  `log/slog` JSON with `trace_id`/`span_id`; `logf` removed; instameow's
  zerolog switched to JSON.
- **Compose.** `extra_hosts: host.docker.internal:host-gateway` on
  `instagram-bridge`, plus the endpoint env.
- **Nora.** One helper injects W3C `traceparent` into outbound headers, used
  by every bridge client that makes a request inside a turn:
  `nora/platform/instagram/` (send, react, roster) and
  `nora/infra/social/instagram_client.py`. **Not the listener**: it opens
  `/events` from a background task with no current span, and the bridge does
  not trace `/events`, so a header there would carry nothing. Inbound traces
  arrive in phase 2, carried inside each event's payload (`16` §Tracing).
  Not the Instagram CDN fetch (`media.py`) either: that goes to a third
  party.
- **Tests.** Go, using the SDK's in-memory exporter: a server span named
  `GET /accounts/{igid}`; an upstream span with status ERROR for the captured
  `field_exception` envelope; **no attribute carrying message text** (the
  negative). Python: an aiohttp test server records a `traceparent` on every
  bridge call.
- **Read-back.** Tag Nora in the Instagram room: **one** trace in Jaeger that
  contains Nora's spans, the bridge's server span and the Instagram upstream
  span; the bridge's log lines carry that trace id.

## P2: The person index knows which Instagram id is which

`Persons.index` is `(platform, value) → person_id`. It drops the id **kind**,
so `platform_ids("instagram", p)` returns the fbid and the igid mixed, both
decimal strings: the `02` trap again.

- **`nora/persons.py`**: `instagram_igids(person_id) -> frozenset[str]`,
  built in `load_persons` from `kind == "igid"`, keyed per person the way
  `_instagram_handles` is keyed per fbid.
- **Tests**: fbid+igid returns only the igid; two accounts return two; an
  unknown person returns an empty set.

## P3: `SocialSubjectIndex`, implemented

**`nora/infra/social/subjects.py`**: `PersonsSubjectIndex(persons, policy)`.

- `igid_for(person_id)` → the igid when there is **exactly one**; `None` for
  zero or several (`ambiguous`, as the port documents: never pick one of a
  person's accounts).
- `is_tier_zero(person_id)` → the person is **listed** in `persons` of at
  least one configured room. **A `persons = "*"` room does not count**: an
  open room admits anyone and asserts nobody's membership. Fail closed.
- `person_for_igid(igid)` → the person that igid belongs to, or `None`: the
  reverse lookup the link path needs (`15` §Links), added to the
  `SocialSubjectIndex` port. Backed by `Persons.resolve("instagram", igid)`.
- **Tests**: including the `*` negative, the two-account `ambiguous`, and an
  igid mapping to no person.

## P4: Nora's Instagram client, on the bridge's real contract

**`nora/infra/social/instagram_client.py`**, rewritten. Today it POSTs
`/social/story` and expects a `status` envelope the bridge never had.

- `story` → `GET /accounts/{igid}/story`; `profile` → `GET /accounts/{igid}`.
- Status mapping, per route (they differ today):
  - `profile`: 200 → `ok`; 404 (`errNoProfile`, the igid names no account)
    → `nothing_found`; 409 and 503 → `unavailable`; anything else →
    `unavailable`.
  - `story`: 200 with `found: true` → `ok`; 200 with `found: false` →
    `nothing_found`. The story route has **no** "no such account" path; an
    unknown igid is `found: false` as well. Acceptable, because a story igid
    comes only from the person index or from a lookup that just found the
    account. 409 and 503 → `unavailable`; anything else → `unavailable`.
- Profile rendered from the curated fields (`is_private`, both follow
  directions, counts, bio) into the same untrusted wrap as story. The
  existing hardening stays: the bridge's text never reaches `note`, and output
  is clamped.
- **Contract fixtures.** Bodies captured from the live bridge on norty,
  committed once, read by **both** the Go handler tests and the Python client
  tests. A shape change on either side then fails on both.

## P5: `instagram_read` (replaced 2026-09-23)

**Superseded by `18-instagram-read-and-search.md`.** P5 originally rewired
`social_fetch`'s Instagram rows and docstring, and the operator then decided to
split social reads per network. Changing `social_fetch` and then replacing it
would have rewritten Nora's tool surface twice, so P5 is now: land
`instagram_read` on the bridge path, and remove `social_fetch`'s Instagram
rows in the same PR. It builds on P2–P4 and lands after the tool catalogue's
#367 and #393. The routing-by-network, allowlist and `test_r86_…` changes this
section used to list move into `18` §"Read-only, at three layers" unchanged
in substance.

## P6–P8: The bridge's layers (`16` steps 2–4)

- **P6 `internal/domain`**: `IGID`, `FBID`, `ThreadRef`, `Username` with
  parsing constructors; the closed error set. The existing handlers adopt
  them. Test: a username does not parse as an `IGID`.
- **P7 `internal/gateway/instameow`**: the `graphql` and `rest` helpers; every
  instameow call moves behind them, and the wire structs move with them. Tests:
  literal wire bytes for every failure shape seen live.
- **P8 `internal/app`** plus the import test: use cases with ports; handlers
  go thin. `go list -deps` asserts `16`'s "must not import" column.

Behaviour-preserving throughout: the P4 contract fixtures and the P1 spans
must not change.

## P9: Probe, username → igid (gate for P10)

`SearchUsers` is the DM contact picker's search
(`search_messenger_contact_v2`). It returns `pk` and `username`, but whether
it finds **any** public account by exact username is unmeasured. Run it
against the 11 mapped friends, public accounts Nora has never messaged, a
private account she does not follow, and a username that does not exist.
Record the exact-match rate. **If it misses accounts outside her contacts,
P10 needs a different mechanism, found first**, and the plan stops here for a
decision.

## P10: Links, and the username lookup

- **Bridge**: `GET /accounts?username=` returns 0 or 1 accounts, in the same
  shape as `/accounts/{igid}`.
- **Nora**: `nora/infra/social/instagram_links.py` parses a URL into
  `(kind, key)`: profile, post, reel, story. The router resolves per
  `15` §Links: a story link resolves username → igid → person and must pass
  Tier 0, or it is `refused`.
- `profile` accepts a person id, a username, or a profile link.
- **Negative test**: a story link to someone in none of her rooms is
  `refused`, and **no story request reaches the bridge** (asserted on the
  bridge's recorded calls).
- **Multi-account test**: a friend with two Instagram accounts; a story link
  to one of them reads *that* account's story, while a `person_id` request for
  the same friend stays `ambiguous`.

## P11: Search onto the bridge

`GET /accounts?query=`. The `instagram/search` row leaves OpenCLI. After this
OpenCLI serves X only, and `test_r86` asserts **no `instagram` key** remains
in `READ_ONLY_ALLOWLIST`.

## P12: One post from a link

Probe first: `FetchMedia` on a real feed post, not the story id that returned
400; derive the media id from the shortcode. Then `GET /posts/{shortcode}`,
wired to post and reel links.

## P13: Posts and reels lists (investigation, not a build)

The routes return `501` (`ErrUnsupported`), which Nora says as `unavailable`
with its reason. Finding a working mechanism is its own track. No PR is
planned until one is measured.

## P14: Writes onto resource paths (`16` step 5)

`POST /threads/{id}/messages`, `DELETE …/messages/{id}`,
`PUT …/messages/{id}/reaction`, `PUT /threads/{id}/typing`; the error envelope
on every route. That includes `GET /accounts/{igid}` and
`GET /accounts/{igid}/story`: their paths do not change, but their bodies
(`profileBody`, `storyBody`) lose the `ok` field. The Python adapter
(`nora/platform/instagram/`) and `nora/infra/social/instagram_client.py` move
in the **same PR**, and the old verb-shaped paths are deleted in it.
