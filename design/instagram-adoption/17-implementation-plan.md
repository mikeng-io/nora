---
type: design
title: "Implementation Plan — the bridge as the only way in, PR by PR"
description: "The concrete order for 15 and 16: observability first, then Nora's read path onto the bridge for people she knows, then the bridge's layers, then links, search and posts on the new layers, then writes onto resource paths. Every PR names its files, its tests, the revert that proves them, and the live read-back that closes it."
tags: [nora, instagram, bridge, plan, sequencing]
timestamp: '2026-09-24'
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

**Status, 2026-09-24: P1 to P12 and P14 are merged and live on norty.**
PRs: P1 #396, P2 #400, P3 #402, P4 #407, P5 #421, P6 #404, P7 #411, P8 #413,
P9 #419 (measured), P10 #442 + #446, P11 #415 + #443, P12 #439 + #446, P14
#447. Shared posts #435. P13 remains an investigation with no mechanism: the
library has no list call, so a person's posts or reels list stays
`unsupported`. Rollback images `nora:pre-p14` and `instagram-bridge:pre-p14`
were kept on norty at the P14 deploy.

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
`instagram_read` on the bridge path for `person` input (profile, story),
teach the router input-kind resolution for it (`18` §"What the router has to
learn"), and move `social_fetch`'s dead or unbacked Instagram rows (profile,
story, posts) in the same PR. `instagram/search` stays on `social_fetch` until
P11. It builds on P2–P4 and lands after the tool catalogue's #367 and #393.
The allowlist and `test_r86_…` changes this section used to list move into
`18` §"Read-only, at three layers" unchanged in substance. P10 then adds the
`link` and `handle` inputs, and P11 lands `instagram_search`.

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

**Measured 2026-09-24: it does.** Run through the bridge's own
`GET /accounts?query=` (#415, deployed), one query per username, with a case-
insensitive exact match on `username`:

| Set | n | exact match | at rank 0 | igid matches the person index |
|---|---|---|---|---|
| mapped friends | 11 | 11 | 11 | 11 |
| large public accounts, never messaged | 6 | 6 | 6 | — |
| private accounts she does not follow (found by profile read) | 4 | 4 | **3** | — |
| usernames that cannot exist | 3 | 0 (0 items returned) | — | — |

So the contact picker's search reaches accounts outside her contacts, private
ones included, and returns nothing for a name that does not exist. **One exact
match was not the first result**, so P10 filters on an exact, case-insensitive
`username` and never takes `items[0]`. Zero exact matches means
`nothing_found`, and two or more cannot happen for a unique username but would
be `ambiguous`. The query text was absent from every span of the run (checked
against Jaeger). n is small and every case agreed; P10 proceeds on this
mechanism.

## P10: Links, and the username lookup

- **Bridge**: `GET /accounts?username=` returns 0 or 1 accounts, in the same
  shape as `/accounts/{igid}`.
- **Nora**: `nora/infra/social/instagram_links.py` parses a URL into
  `(kind, key)`: profile, post, reel, story. The router resolves per
  `15` §Links: a story link resolves username → igid → person and must pass
  Tier 0, or it is `refused`.
- `profile` accepts a person (by name), a handle, or a profile link (`18` §`instagram_read`).
- **Negative test**: a story link to someone in none of her rooms is
  `refused`, and **no story request reaches the bridge** (asserted on the
  bridge's recorded calls).
- **Multi-account test**: a friend with two Instagram accounts; a story link
  to one of them reads *that* account's story, while a request naming the same
  friend by `person` stays `ambiguous`.

**The bridge half is built (2026-09-24)**: `GET /accounts?username=`
(`accounts_search.go`), over the SAME search port `?query=` uses
(`app.LookupAccountByUsername`), filtering to an exact, case-insensitive
username match and never `items[0]` — P9's own measurement, applied. It
answers in the LIST shape `16` §Resources and `?query=` already use
(`{"items":[...],"next_cursor":null}`, 0 or 1 items), not the single-resource
shape this section's first bullet named before P9 measured the mechanism.
Two exact matches is 502 `upstream_rejected` (usernames are unique, so more
than one means the upstream data disagreed with itself). The Nora half
(`instagram_links.py`, the router, and the negative and multi-account tests
above) is not started.

**2026-09-24: the Nora half is built** (PR `mikeng-io/instagram-read-links`,
with P12; not merged, not deployed). The link parser is
`nora/domain/instagram_links.py`, not `nora/infra/social` (18 §"Where each
concern lives" says why), and #435's parser reads it unchanged.
`instagram_read` takes exactly one of `person`, `handle`, `link`. A handle
or a profile/story link's username resolves through the bridge's exact
lookup (`InstagramAccountLookup`, implemented by `InstagramSocialClient`,
which never takes `items[0]`); a story then passes
`person_for_igid → is_tier_zero` on the igid the input named, or it is
`refused` and no story request is sent. Both named tests exist:
`test_a_story_of_someone_in_none_of_her_rooms_is_refused_and_never_requested`
and `test_a_story_link_to_one_of_two_accounts_reads_that_account`
(`tests/unit/test_instagram_read_links.py`, on a recording fake bridge).
18 has the as-built detail. Still open for "done": the deploy, the routing
eval run, and the live Jaeger read-back.

**#446 review, 2026-09-24:** a story by handle or link must also name
someone on the ASKING room's roster, or it is `refused` with the one story
refusal sentence (operator's conservative default; widening to «any group
room» is an OPEN decision, 15 §Links and 18 say why: it would make a story
link a cross-room membership oracle). A profile link with `what="story"`
reads that account's story, a story-ring link `/stories/<user>/` reads the
current story, and a highlight link is refused. The egress screen now runs
on a link's username as well as a typed handle.

## P11: Search onto the bridge

`GET /accounts?query=`. The `instagram/search` row leaves OpenCLI. After this
OpenCLI serves X only, and `test_r86` asserts **no `instagram` key** remains
in `READ_ONLY_ALLOWLIST`.

**The bridge half landed early**, to serve P9: `GET /accounts?query=` exists
today (gateway, domain, app and transport, on the new response conventions),
since P9's probe needed to run through the real route rather than a second
client on the live session. The Nora half — moving `instagram/search` off
OpenCLI and the allowlist assertion above — is still pending.

**2026-09-24: the Nora half is built** (PR `mikeng-io/instagram-search-p11`,
not merged, not deployed). `instagram_search(query)` returns up to 5
accounts, `{username, is_verified}` each in the bridge's order plus one
wrapped text block with the display names, no igid or fbid; zero items is
`nothing_found`, 409/502/503 are `unavailable`; a blank query or one over 64
bytes is refused before any call. `(instagram, search)` joined the router's
`EXCEPTION_ROUTES` with a `named` input, and its OpenCLI row left
`READ_ONLY_ALLOWLIST` in the same change: OpenCLI serves X only, and
`test_r86` asserts no `instagram` key remains. `social_fetch` is X only and
answers any Instagram request `unavailable`, naming `instagram_read` and
`instagram_search`. The bridge side gained three shared fixtures (the 409,
502 and 503 envelopes for `GET /accounts?query=`), generated by
`contract_fixtures_test.go`. Design 18 §`instagram_search` has the as-built
detail. Still open for this PR's "done": the deploy (an image rebuild of
`nora`), the routing eval run, and the live Jaeger read-back.

**Deploy step for P11 (2026-09-24): restart the host social bridge.** After
the `nora` image is rebuilt and recreated, run
`systemctl --user restart nora-social-bridge` on norty. The OpenCLI bridge
is not in the image: it is a systemd user service running the host
checkout's code, so it keeps the old `READ_ONLY_ALLOWLIST` (with its
`instagram/search` row) until it is restarted. Until then nothing in Nora
sends it an Instagram request, but the host would still spawn one for
anything that asked.

## P12: One post from a link

Probe first: `FetchMedia` on a real feed post, not the story id that returned
400; derive the media id from the shortcode. Then `GET /posts/{shortcode}`,
wired to post and reel links.

**The bridge half is built (2026-09-24) and awaits the live probe**:
`GET /posts/{shortcode}` over `FetchMedia` (REST `media/{id}/info/`), the id
decoded from the shortcode in `domain`, on synthetic unmeasured fixtures; the
Nora half (wiring post and reel links) is not started.

**2026-09-24: the Nora half is built**, in the same PR as P10. A `/p/`,
`/reel/` or `/reels/` link becomes `GET /posts/{shortcode}` through the new
`(instagram, post)` row, with the shortcode as a `named` target (never
resolved). The result is the whole post: every item in order with kind, url
and a video's duration, the author, the caption wrapped and clamped, no ids,
and a note to `media_fetch` each item Nora needs to see. It is read only in
a turn a person asked for; a fire or Dreamer turn is `refused` before any
call, as a story is. The bridge's 404 is `nothing_found`, 501 is
`unavailable` («can't read that kind of post link yet»). The bridge half's
live probe on a real post is still outstanding, so the Nora half is tested
on the generated fixtures only.

**Measured at deploy, 2026-09-24: the CDN item urls did not carry the
author's igid.** On one carousel (2 images) and one reel (1 video), the
author's igid (resolved through `GET /accounts?username=`) appeared nowhere in
the 3 urls, raw or with `efg` / `ig_cache_key` base64-decoded. n is 2 posts;
it is a finding, not a guarantee, and the client still checks the host only.
The bridge half's live probe also passed on the same two: both shortcodes
decoded to ids Instagram accepted, the carousel returned both items, the reel
its video with a duration.

## P13: Posts and reels lists (investigation, not a build)

No route exists (corrected 2026-10-16: an earlier draft said the routes return
`501`); Nora's side answers `unavailable` without a call
(`nora/infra/social/instagram_client.py`). The one untried mechanism is in
design/cognition-loop/22-social-stream.md §7. Finding a working mechanism is its own track. No PR is
planned until one is measured.

## P14: Writes onto resource paths (`16` step 5)

`POST /threads/{id}/messages`, `DELETE …/messages/{id}`,
`PUT …/messages/{id}/reaction`, `PUT /threads/{id}/typing`; the error envelope
on every route. That includes `GET /accounts/{igid}` and
`GET /accounts/{igid}/story`: their paths do not change, but their bodies
(`profileBody`, `storyBody`) lose the `ok` field. The Python adapter
(`nora/platform/instagram/`) and `nora/infra/social/instagram_client.py` move
in the **same PR**, and the old verb-shaped paths are deleted in it.

**2026-09-24: built** (branch `mikeng-io/instagram-bridge-rest-writes`, not
merged, not deployed). The four write routes and the envelope are on every
route, the legacy status table is deleted, every path id parses before the
session gate, and a failed send's error text is fixed before it reaches a span
(`sendTextError`), classified first so too long stays 413. What Nora reads
from a write response, listed from the callers before the move: from a send's
200 only `timestamp_ms` (`send.py` `_sent_timestamp`); from a reaction, a
roster failure and every send failure only the status; from the members 200
only `members[].fbid`; from the profile 404 the envelope's `code`
(`not_found`, formerly `ok: false`). The sent message's `id` is not read by
Nora; the bridge keeps it and its tests pin it. Nora has no unsend or typing
caller. Shared fixtures `members_*`, `messages_*`, `reaction_*`, `unsend_*`
and `typing_*` carry the request each write was generated from, and the
adapter's tests assert it sends exactly that body. 16 §Migration step 5 lists
what did not fit 16 as written. **Deploy: both images together, bridge first,
then nora immediately**; between the two every Instagram send and reaction
fails on a 404. The PR body carries the runbook and the no-send read-back.
