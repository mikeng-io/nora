---
kind: spec
title: "Nora Drop — local-first artifact delivery and link shortener"
description: "A composed local service (drop pages + link shortener, one app container) on Nora's own docker-compose, reusing her existing Postgres, backed by a dedicated Valkey and RustFS, retiring the n.example.net Cloudflare Worker. Compute (how artifacts get produced) is a deliberately separate, not-yet-specified follow-on."
tags: [nora, spec, drop, shortener, compute, local-first, infra]
timestamp: '2026-08-12'
status: proposed
derives_from:
  - ARCHITECTURE.md
  - docs/superpowers/specs/2026-08-08-mcp-tool-provider-design.md
  - design/proposed/15-gif-replies.md
---

# Nora Drop

## 0. What this document is

Nora today has exactly one way to reach a person: `send_reply(text)`
(`nora/agent/tools.py`). Every capability ends as words in a chat. This spec
adds a second kind of output — something that exists *outside* the chat that a
person can open, revisit, or hand to someone else — without adding a second
kind of *infrastructure philosophy* to a codebase that has consistently kept
Cloudflare (and every other backend) behind a port, never as the abstraction
itself (`nora/domain/ports.py`; DEC-2's MCP tool-provider layer).

This document specs the **delivery half only**: where an artifact lives, how
it gets a URL, how that URL reaches a person. It deliberately does **not**
spec how artifacts get produced — that's Nora Compute, named in §7 as a
follow-on with its own open questions, not filled in here with invented
answers.

## 1. Why not `cloudflare/computer` (or Cloudflare as the storage/hosting layer generally)

Investigated directly (`~/workspace/computer-spike`, a clone of
`cloudflare/computer`) before this design. Its core machinery — a Durable
Object holding authoritative SQLite state, a FUSE-mounted container synced to
it over a capnweb RPC protocol — exists to solve a problem specific to
Cloudflare's edge model: Workers have no local disk, and Containers are
ephemeral and rescheduled across machines, so filesystem state has to live
somewhere durable and get synced in and out of whichever container instance
is currently attached.

That problem does not exist on one box Nora already runs on. If the artifact
producer and the artifact store share a machine, the store can just *be* the
host filesystem or a locally-hosted object store — nothing to keep in sync
because there is only one copy. Concretely, adopting `cloudflare/computer`
would also have meant: Docker for its container backend anyway, real Workers
AI billing (with the tutorial's own model, `@cf/zai-org/glm-5.2`, gated to the
paid plan regardless of usage), Cloudflare Containers requiring the $5/mo
Workers Paid plan (not available on Free at all), and adopting a package whose
own README says "PREVIEW ONLY... NOT suitable for production use." None of
that buys anything a bind-mounted Docker container and a local object store
don't already give for free.

Cloudflare's role in this design is reduced to exactly what it's good at and
nothing more: DNS, TLS via the existing Cloudflare Tunnel, and (unrelated to
this spec) the AI Gateway/OmniRoute path Nora already uses for models.

## 2. Scope

**In scope:**
- One new composed service, `drop`, added to `~/workspace/nora/docker-compose.yml`
  on the existing `nora` network — not a new repo, not folded into the
  separate `ridgeline-drop` project (which stays scoped to Miles's own
  markdown draft/brief/report store; mixing the two was explicitly ruled out).
- Host-based routing for the **public, read-only** surface, mirroring the
  pattern `ridgeline-drop` already uses for `drop.example.net` / `drop.mikeng.io`:
  - `drop.nora.example.net` — renders/serves artifacts (`GET`)
  - `nora.example.net` — redirect (`GET /{slug}`), replacing `n.example.net`
- An **internal-only** write API — mint (`POST /api/links`) and artifact
  registration — reachable solely on the `nora` Docker network, never
  published to a host port or given a Tunnel ingress rule. See §5a.
- A stub artifact-ingest contract (a `Protocol` in `nora/domain/ports.py`,
  no second implementation yet) — the seam a future Compute tool calls
  through. See §7a.
- Storage: artifact metadata in Nora's own existing Postgres (`db` service);
  slug → URL in a new dedicated Valkey; artifact bytes in a new RustFS.
- Retiring the `deploy/shortener/` Cloudflare Worker + KV once `nora.example.net`
  is live and verified, and repointing `config.toml`'s `[shortener].base_url`.
- Public delivery through the existing Cloudflare Tunnel — no Tailscale, no
  Caddy in this service. **Prerequisite outside this repo:** the Tunnel's
  ingress rules are Terraform-owned in `~/workspace/cloudflared`
  (`terraform/tunnel.tf`), not here — adding `drop.nora.example.net` and
  `nora.example.net` as public hostnames is a change in that repo, not this one,
  and needs to land before `drop` can be reached publicly.

**Out of scope (deliberately, not deferred-and-forgotten):**
- How an artifact gets produced (the compute/sandbox layer, the
  `make_artifact`-shaped Nora tool, its budget/terminal classification). See
  §7.
- Interactive drop pages (action callbacks, RSVP-style state), mini-app
  generation, live/presence pages, job status pages, governed compute
  profiles. All were part of the wider brainstorm this spec grew out of;
  none are needed for a first artifact-in, link-out slice, and CLAUDE.md's
  own discipline is against building for a shape that doesn't exist yet.
- Attachment/inline delivery. A drop URL is text and goes out through
  `send_reply` exactly like any other reply — no new egress path, no change
  to R-18's scanning. This resolves the link-vs-attachment question raised
  early in the brainstorm in favor of link, for the reason stated in §7.
- A second `ComputeBackend`/storage adapter of any kind (Cloudflare or
  otherwise). The port this design's storage choices sit behind should stay
  swappable in principle, but only one concrete backend ships.

## 3. Architecture

`drop` runs two listeners, not one — the split that makes §5a's network
containment possible:

```
Nora (existing tool-call loop, native tool — see §6a)
      │  produces a file, calls the ingest port with it
      ▼
drop:<internal-port>  — reachable ONLY as `drop:<port>` on the `nora`
                         Docker network. No host port, no Tunnel rule.
      │  POST /api/artifacts  (register bytes + metadata; ingest contract, §7a)
      │  POST /api/links      (mint a slug; same contract n.example.net has today)
      ├─ artifact metadata  → Nora's existing `db` (Postgres, DEC-51's store)
      ├─ artifact bytes     → rustfs (new, S3-compatible)
      └─ slug → URL map     → valkey (new, dedicated)

drop:<public-port>    — the ONLY port with a Tunnel ingress rule
      ├─ Host: drop.nora.example.net  GET /d/<slug>  → renders an artifact
      └─ Host: nora.example.net       GET /<slug>    → 301 redirect
      ▼
Cloudflare Tunnel (existing, `~/workspace/cloudflared`) — public, no Caddy,
no Tailscale
      ▼
person opens the link
```

Both listeners are one process/container (`drop`) reading the same three
stores — the split is which socket a route is bound on, not a second
service.

## 4. Why each storage choice, and what was ruled out

This repo's own convention (`nora/domain/ports.py`, DEC-2) is: define the
interface Nora depends on, keep the concrete backend swappable, never let the
backend become the abstraction. The choices below are the first concrete
backend behind that interface, not a claim that they're permanent.

**Artifact metadata — Nora's own `db` (Postgres), not Honcho's.**
Nora already runs her own `postgres:18-alpine` (`db` service, database
`nora`, `docker-compose.yml`). Honcho runs a separate `pgvector/pg15`
instance for its own purposes. Reusing Nora's own store keeps this entirely
in Nora's scope with zero new coupling; reusing Honcho's would have made a
Honcho migration or reset able to take Nora's artifact data down with it.

**Slug → URL — a new, dedicated Valkey, not shared with Honcho's Redis.**
Honcho already runs `redis:8.2` for its own cache (`CACHE_URL:
redis://redis:6379/0`). Explicitly ruled out from reuse — the instruction
was "do not mix it," and Redis containers are cheap enough that a dedicated
one fully avoids the question rather than relying on a DB-index or
key-prefix convention staying correct forever.

Valkey over Redis: Redis relicensed away from its permissive BSD terms in
March 2024 (dual RSALv2/SSPL); Valkey is the Linux Foundation–governed
BSD-3 fork, protocol-identical (RESP2/RESP3, same commands — no
application-code cost), and current benchmarks show it slightly ahead on
throughput and memory. Nora's use here (`SET`/`GET` on a slug) needs none of
Redis Stack's modules (RedisSearch/RedisJSON), so there is no offsetting
reason to prefer Redis.

**Artifact bytes — RustFS, not MinIO, not SeaweedFS, not Garage, not plain fs
— and not `FilesystemBlobStore` either, on purpose.** `nora/infra/blobs/filesystem.py`
already made "object store vs. plain filesystem behind a port" once, for
media/attachment custody, and chose plain filesystem — with an explicit
"do not re-litigate without evidence" instruction in its docstring. This is
not that decision reopened; it's a different question. `FilesystemBlobStore`
holds attachments a known, roster-gated human sent — trusted input, whose
risk profile a `for` loop over a directory genuinely matches. `drop` holds
whatever a Compute job *produces* — self-generated, freeform output that
§7's own guard discussion already names as content no Nora guard has read,
one level past the GIF-attachment risk `design/proposed/15` describes. That
difference in trust, not artifact volume, is why the two land on opposite
answers: a plain directory is the right container for something a person
already vetted by sending it; an API-bounded object store is the more
defensible container for something Nora's own arbitrary-code sandbox wrote,
even before any content-scanning or immutability features actually get used.
"Few artifacts a day" was the wrong axis to justify this on and is dropped
below in favor of the trust argument.

Plain filesystem storage was ruled out for this specific case — not as a
blanket rejection of `FilesystemBlobStore`'s own pattern, which stays correct
for what it protects. **Note on presigned URLs specifically:** §3's public listener has
`drop` read from `rustfs` and serve the bytes itself rather than 302 a
browser straight to a signed RustFS URL, so that particular S3-API benefit
is not actually exercised by this design as drawn — flagged in review, kept
here rather than quietly dropped. Object metadata and a standard client are
still real reasons over plain fs; presigned URLs are a capability available
if a future revision wants `drop` to stop proxying bytes, not something
this version relies on. Among self-hosted S3-compatible options investigated
2026-08-12:

- **MinIO** — ruled out outright. Its OSS repository moved to "no longer
  maintained" (Feb 2026) and was formally archived (Apr 25 2026); no further
  security patches; MinIO's own stance now redirects users to their paid
  AIStor product.
- **SeaweedFS** — the mature option (production-proven since ~2016, Apache-2.0,
  used by projects like Kubeflow), but its master/volume/filer architecture
  is built to scale across many nodes — machinery aimed at a distribution
  problem this single-box, few-artifacts-a-day deployment doesn't have. Same
  shape of objection as §1's argument against `cloudflare/computer`.
- **Garage** — AGPL, and purpose-built for geo-distributed/unreliable
  infrastructure, neither of which applies here.
- **RustFS** — chosen. Apache-2.0, single-binary, S3-compatible (full
  multipart/versioning/lifecycle per its own docs), and the lightest
  operational footprint of the four. Caveat, weighed deliberately: RustFS is
  pre-stable (`v1.0.0-alpha` as of early 2026, distributed mode not
  released). Accepted because drop artifacts are not Nora's system of
  record — they're regenerable outputs of a compute job, with their own
  expiry — so the failure mode of an alpha storage engine here is "regenerate
  the file," not data loss of anything irreplaceable.

## 5. Retiring `n.example.net`

`n.example.net` is currently live: a Cloudflare Worker + KV, Terraform-owned at
`deploy/shortener/`, `enabled = true` in `config.toml` today, with an
identical API contract to what `drop`'s shortener routes will expose
(`POST /api/links`, bearer-authed via `MINT_TOKEN`/`NORA_SHORTENER_TOKEN`;
`GET /{slug}` → 301; base58-ish 7-char slugs). Because the contract matches,
cutover is: stand up `nora.example.net` on the new `drop` service, verify mint +
redirect behave identically, point `config.toml`'s `[shortener].base_url` at
`https://nora.example.net`, then decommission the Worker, its KV namespace, and
the `deploy/shortener/` Terraform state. `ShortenerConfig`
(`nora/config.py`) should not need to change shape — only where it points.

## 5a. Network exposure and auth

The write API (mint, artifact registration) is never reachable from the
public internet at all — that is the primary control, not a bearer token
guarding a public endpoint. This repo already has the pattern to copy:
`signal-cli` "publishes NOTHING" in `docker-compose.yml` — no host port
mapping, reachable only as `signal-cli:18080` on the private `nora` network.
Its own compose comment: "a netns reachable only on that network, a
STRICTER trust set than the old host loopback (any host process could hit
that). The no-auth / host-header concern only bites behind a host port-map
or a browser (DNS-rebinding); neither can reach an internal-only network
with no published port." `drop`'s internal listener follows the same rule:
no `ports:` entry, no Tunnel ingress rule, reachable only by container DNS
name from Nora's own container.

This is a network-level control, not a protocol-level one: the plain
ingress-rule mechanism `~/workspace/cloudflared/terraform/tunnel.tf` actually
uses matches only on hostname and path, never HTTP method (verified against
Cloudflare's own Tunnel config reference) — there is no ingress-rule-level
way to make a Tunnel "let GET through, block POST" on a single port. (Method
matching does exist in Cloudflare, but only in the separate Zero Trust
Gateway/Access HTTP-policy layer, which nothing in `~/workspace/cloudflared`
currently configures — not a mechanism this deployment can lean on today.)
Splitting
the write routes onto a second listener that simply has no path to the
internet is the robust version; a Tunnel ingress rule that merely omits POST
paths from its config would rely on that omission never being a copy-paste
mistake away from exposure.

A bearer token (same shape as `NORA_SHORTENER_TOKEN` today) still sits on the
internal listener as defense-in-depth — worth keeping so a compromise of the
`nora` network itself (a different, larger problem) doesn't trivially grant
write access — but it is not the only thing standing between the internet and
a mint/registration call, the way it currently is for `n.example.net`'s Worker.

## 6. What ships in `docker-compose.yml`

Three new services on the existing `nora` network, reusing `db`:

| service  | image/build          | role                                    |
|----------|-----------------------|------------------------------------------|
| `drop`   | new, this repo        | drop pages + shortener, host-routed      |
| `valkey` | `valkey/valkey`        | slug → URL                               |
| `rustfs` | `rustfs/rustfs`        | artifact bytes, S3 API                   |

No Caddy (nothing here is Tailscale-private — both hosts are meant to be
public from the start, unlike `drop.example.net`). No new Cloudflare Tunnel —
new ingress rules on the existing one (in `~/workspace/cloudflared`, not
here — §2).

**`drop`'s public listener needs its own host port publish, unlike its
internal one.** `cloudflared` runs `network_mode: host` in that separate
compose project and reaches every backend it fronts via `localhost:<port>`
(confirmed against every existing ingress rule in `tunnel.tf`) — it is never
on the `nora` Docker network and cannot resolve `drop` by container name.
So the public listener needs a `127.0.0.1:<port>:<port>` entry, the same
pattern `nora` and `presence` already use for their own `/health` endpoints
(`docker-compose.yml:109`, `:206`). The internal listener (§5a) stays
unpublished — this applies to the public one only.

## 6a. How Nora calls it: a plain native tool, not MCP

This repo already drew this line — DEC-2's rule is hot path (Honcho, SQLite
audit, **platform send**) is native; cold path (Maps, web search — generic,
external, read-heavy lookups) is MCP, mounted via `[tools.mcp.<name>]` with
zero custom Python (`docs/superpowers/specs/2026-08-08-mcp-tool-provider-design.md`).

`drop` doesn't fit the cold-path shape that spec was built for. It isn't a
third-party read-only server like Pulse or Google Maps — it's a service Nora
herself owns, in her own repo, on her own network, called at a turn-critical
moment (produce an artifact → need a URL → reply *this turn*). That's the
same category as "platform send," not "mount a generic external capability."
The MCP tool-provider spec doesn't state a read-only scope in so many words,
but every signal in it points that way — its one example (Pulse) is
described as "a read-only MCP server," and its own registry design treats
every MCP tool as non-terminal (§4: "All MCP tools are non-terminal... only
`send_reply` sends it"). `drop`'s calls (mint, register) are writes, and
`drop` itself is meant to end a turn with something sent, not just queried —
neither fits that shape. And there's already a precedent
in this exact repo pointing the same way: the shortener's mint call was
always meant to be "the Nora-side minting integration" reading a bearer
token from `.env` directly (`deploy/shortener/README.md`) — never framed as
an MCP mount.

So: a plain native tool in `nora/agent/tools.py` (or a small dedicated
module), holding an HTTP client against `drop`'s **internal** listener
(§5a), closed over its own bearer token the way every other native tool
closes over its dependencies (this module's own docstring, on why
`conversation`/`question` are closures and never model-supplied parameters).

## 7. Explicitly deferred: Nora Compute

How an artifact gets *produced* — the thing that calls into `drop` once it
has a file to hand over — is a separate, not-yet-specified design. What's
agreed at the concept level, for whoever picks this up next:

- A `ComputeBackend` port Nora depends on (same shape as every other port in
  `nora/domain/ports.py`), with a `LocalDockerBackend` as the only adapter
  that ships: one Docker container per job, a bind-mounted workspace
  directory, run, exit, files land on host disk. No FUSE, no sync protocol —
  the host filesystem already is the single source of truth once producer
  and store share a machine (§1's argument, applied to compute instead of
  storage).
- A new native Nora tool, same shape as `remind`/`media_fetch` in
  `nora/agent/tools.py` — not an MCP mount (DEC-2's MCP path is for external
  read-only servers; this is a capability Nora owns directly).

### 7a. The ingest contract — the one piece of this that IS in scope now

Subagent review of an earlier draft of this spec caught a real gap: nothing
specified how bytes and metadata actually cross from wherever a job produces
them into `drop`'s stores. That seam belongs to Drop, not Compute — Compute's
job is only to produce a file; it shouldn't need to know RustFS or Postgres
exist. So this spec adds one small thing to its own scope: a `Protocol` in
`nora/domain/ports.py` describing that seam (a `register(path_or_stream,
title, kind, expires_at) -> artifact_id` shape, roughly — exact signature is
implementation work, not this document's job). **Named precisely, since this
differs from the established pattern:** every existing `Protocol` in
`nora/domain/ports.py` is declared alongside at least one concrete
implementation already in the repo and a real caller — declared before a
*second* adapter exists, not before a first. This port would ship with
*zero* implementations anywhere until Compute is built — a step further out
than anything currently in that file. Reasonable given the seam genuinely
belongs to Drop and Compute doesn't exist yet to write the other half, but
whoever implements this should pair it with a test fake (the way every other
port in `ports.py` has a fake under `nora.testing`) rather than shipping a
Protocol nothing satisfies, even in tests — otherwise the sketched shape is
unverified. `drop`'s internal `POST /api/artifacts` (§3, §5a) is the concrete
implementation of that port. This gives whoever builds Nora Compute next a
contract to call rather than a blank page, without this document inventing
Compute's own decisions to fill it.

**Not yet decided, and not invented here to fill the gap:** the tool's exact
signature, its budget and whether it's terminal (`NATIVE_TOOL_BUDGETS`,
`ToolRegistry.terminal_tools()`), and — the one substantive open risk —
whatever a compute job produces is content no Nora guard has read. A drop
URL is text and passes R-18's scanning exactly like any reply; the *bytes
behind the link* are exactly the unresolved problem `design/proposed/15`
already named for GIFs ("the first thing Nora would send that she cannot
read and no guard can inspect"), one level worse here because the content is
freeform job output rather than a curated third-party API response. This
spec's link-only decision (§2) keeps that risk from growing further — it
does not close it.

## 8. Open questions for review

- Naming: `drop`/`valkey`/`rustfs` as service names — any preference against
  the plain image-name convention `docker-compose.yml` mostly already uses
  (`db`, not `postgres`)?
- Artifact expiry policy (how long a drop URL stays live) isn't pinned down
  yet — inherited from the wider brainstorm's artifact-metadata sketch
  (`expires_at` field) but no concrete default was agreed.
- Whether `drop`'s app framework should match `ridgeline-drop`'s
  (Starlette + uvicorn) for consistency, or Nora's own stack — no strong
  reason surfaced either way in the conversation this spec was drawn from.
- Exact internal-listener port number and whether it's a second port on the
  same `drop` container or genuinely a second process — either satisfies
  §5a's "no path to the internet" requirement; picking one is implementation
  detail, not a design decision, unless there's a reason to prefer one.
- §7a's ingest `Protocol` signature is sketched, not settled — worth a look
  before implementation starts, since Compute will eventually be written
  against whatever shape it lands on.
