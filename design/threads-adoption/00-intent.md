---
type: design
title: "Intent: Nora answers where she is summoned on Threads, in public, from facts"
description: "What the operator asked for, why Threads is neither a PlatformAdapter nor a SocialProvider, the decisions this bundle takes, and what blocks it today."
tags: [nora, threads, meta, social, public-surface, summons, grok]
timestamp: 2026-09-25
status: proposed
edges:
  - {dst: 10-architecture, rel: sequences, provenance: asserted}
  - {dst: 20-plan-and-verification, rel: sequences, provenance: asserted}
  - {dst: ../instagram-adoption/08-social-adapter-architecture.md, rel: extends, provenance: asserted}
  - {dst: ../social-per-network/00-intent.md, rel: supersedes-scope-note, provenance: asserted}
---

# Intent

## The request (operator, 2026-09-25)

> Threads is completely different from all previous integrations: it is not a
> platform adapter, but rather a social adapter. When I tag Nora in a reply to
> a post (for example "is it true?"), it shouldn't just answer my question
> directly. It also needs to reply based on facts via web search, etc., or
> even image generation, similar to Grok or Meta AI.

The use case is one shape: **a summons**. Somebody writes `@noraexample is
this true?` in a reply under a Threads post. Nora reads the post she was
summoned under and the chain above the summons, checks the claim against the
web (or draws what was asked for), and answers **in that thread, in public**.

## Why this is a third kind of surface

Nora has two seams today, and design/instagram-adoption/08 drew them as two
orthogonal axes:

| | `PlatformAdapter` (messaging) | `SocialProvider` (reads) | **Threads summons** |
|---|---|---|---|
| audience | a room: bounded, known members | nobody: results go back to a room | **the public internet** |
| ingress | every message, ambient | none | **only the summons** |
| egress | `send` into the room | none, read-only at three layers (R-86) | **one public reply** |
| memory | Honcho, notes, standing, audit | none | **none** |
| who speaks | allowlisted persons | n/a | **anyone with a public account** |

Threads fits neither column:

- **It is not a room.** A reply chain under a stranger's post has no member
  list, so no roster, and design/instagram-adoption/00 already ruled that a
  surface without one cannot carry an ambient agent. Every room-scoped
  guard assumes a bounded audience: R-21 resolves names within the room's
  membership, and R-31's disclosure subset checks the room against a trusted
  circle. Pushed through the `EngagementPipeline`, a summons would write
  a stranger's words into Honcho, the audit rows, notes and standing, and her
  prefill would hand the model the private context of whatever room the
  pipeline thought it was in. The fix is not to patch each guard for a public
  audience. The summons does not go through the room pipeline at all.
- **It is not a read.** `SocialProvider` is read-only by construction (R-86,
  pinned at three layers). Adding a publish verb to it would break the one
  property that makes it safe beside a model that reads injected content.
  Instead, the write gets its own seam, with its own enforcement outside the
  model.

So Threads adds a **third port**, `SocialSurface`: ingress of summonses and
egress of exactly one public reply per summons. That is what the operator
calls a "social adapter". Design 08 argued against inventing a port when one
exists. That argument does not apply here, because neither existing port can
carry a public write without losing its defining invariant. The reading
side, "read the Threads post a room member pasted", is still a
`SocialProvider` row (`threads_read`) and joins the existing router. See
10 §"Two axes, kept apart".

## Decisions this bundle takes

1. **A summons is its own occasion, not a tag.** It runs the same model
   loop, the same `ToolRegistry.surface()` and the same tracing, but not the
   `EngagementPipeline`. Nothing it reads or writes reaches Honcho, notes,
   standing, rosters, the decision log or any room's context.
2. **Nora chooses what to say, never where.** The reply target is the summons
   that opened the occasion, fixed before the model runs. She speaks with the
   tools she already has, `send_reply` and `generate_image`, unchanged. What
   changes is the per-turn deliverer they write to. For a summons it is a
   `ThreadsDeliverer` with no target argument at all: it stages text and
   images and publishes them once, as one reply to the summons. The process
   that publishes refuses any `reply_to_id` other than a summons it served
   itself (10 §"The bridge"). This is the write-side analogue of R-86, and it
   is enforced outside the model.
3. **One reply per summons, idempotent.** The summons id is claimed in
   Postgres before the model runs, and the published reply's id is recorded.
   A restart, a duplicate delivery or a second poll never produces a second
   public post.
4. **Public tools only.** The occasion's `ToolScope.grant` admits web search,
   web fetch, media comprehension of the thread's own images, image
   generation, `threads_read`, and the terminal `send_reply`. No memory,
   recall, notes, presence, `where_is`, persons, rooms, `send_to` or
   reminders. The grant is the enforcement. Doctrine alone is not.
5. **Ingress is polling first.** `GET /{user-id}/mentions`, run from inside
   `nora` on an interval. Meta webhooks need a public HTTPS callback, and
   nothing in this stack receives inbound traffic from the internet today
   (10 §"Ingress"). Webhooks become worth building only when polling latency
   is the measured complaint.
6. **Who may summon is a list, at first.** `[social.threads] summoners`
   holds the Threads handles allowed to summon her, and a summons from anyone
   else is recorded and ignored. This matches what Meta allows anyway before
   App Review: only accounts added as Threads testers produce mentions the
   app can see. `summoners = "anyone"` is the switch for after Advanced
   Access, and it is the operator's call.
7. **A public-surface doctrine.** A new `public.md`, loaded only for this
   occasion. It covers audience (strangers, permanently), register, claims
   with sources, a 500-character limit, and refusing what she cannot ground
   (R-5 applies with more force, not less). The same doctrine set stays the
   base, and this file adds to it.

Each of these holds unless the reading in 20 §"What would reopen it" says
otherwise.

## What blocks it today

**The token lacks the mention scope.** `THREAD_API_ACCESS_TOKEN` in norty's
`.env` answered «API access blocked» (`OAuthException` code 200) on every
call on the morning of 2026-09-25, while the new Facebook account behind the
app was restricted. The same token worked once the operator reported the
account restored. Read back from norty the same day:

| call | answer |
|---|---|
| `GET /me?fields=id,username` | `27316499435041440`, `noraexample` |
| `GET /debug_token` | valid, `USER`, issued 2026-09-24, expires 2026-11-23; scopes `threads_basic`, `threads_content_publish`, `threads_manage_replies`, `threads_manage_insights`, `threads_read_replies` |
| `GET /{uid}/threads_publishing_limit` | 0/250 posts, 0/1000 replies per 24 h |
| `GET /{uid}/mentions` | HTTP 500, «An unknown error occurred» |
| `GET /keyword_search`, `GET /profile_lookup` | HTTP 500, empty body |
| `GET /location_search` | code 10, «Application does not have permission» |

The 500s are the endpoints whose scope the token was not granted.
**`threads_manage_mentions` is the ingress of this whole design**, so the
token has to be minted again with it, plus `threads_keyword_search` and
`threads_profile_discovery` if the fact check is to search Threads itself.
Phase 0 in 20 passes when `debug_token` lists `threads_manage_mentions` and
`/mentions` answers 200.

**App Review is the second gate, and it is not needed to start.** Without
Advanced Access, `mentions` returns only mentions by tester accounts, reads
are limited to what those accounts can see, and keyword search sees only the
tester's own posts. With the operator and a few friends as testers, the
summons works end to end. Advanced Access (review of
`threads_manage_mentions`, `threads_read_replies`,
`threads_content_publish`, probably business verification) is needed only
before strangers can summon her, which decision 6 keeps off anyway.
