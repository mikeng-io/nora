---
type: design
title: "Separation of Concerns — Who Resolves, Who Authorizes, Who Reads, And How Little Comes Back"
description: "social_fetch is thin by construction: it returns references and summaries, never content. Identity comes from the person index and never from Honcho. Discovery and retrieval are separate calls, and Nora keeps her own read cursor because Instagram's seen watermark tracks the human, not her."
tags: [nora, social, separation-of-concerns, pagination, identity, honcho, cursor]
timestamp: '2026-09-22'
edges:
  - {dst: 09-tool-surface-and-naming.md, rel: parent, provenance: asserted}
  - {dst: 07-social-boundary-and-scope.md, rel: constrained-by, provenance: asserted}
  - {dst: 06-retention-and-custody.md, rel: constrained-by, provenance: asserted}
---

# Separation of Concerns

## Is `social_fetch` doing too much?

**No — provided it returns references and summaries and never content.** The
moment it returns story media inline it has absorbed `media_fetch`'s job, and
the operator's instinct is correct.

The composition already exists and was designed for this. `media_fetch`'s own
docstring names the source:

> `url` — an http(s) link to a picture or video found anywhere else: a
> `web_fetch`/`social_fetch` result, an image a tweet shows, a link in a
> message.

So the division is already written down: **`social_fetch` finds out *that*
something exists and where; `media_fetch` finds out *what it is*.** A story
read returns items with media URLs; looking at one is a second, deliberate
call that the model makes only for the item it cares about.

This is the same two-layer shape `media_fetch` itself uses — a marker in the
transcript says a photo exists, and the call says what is in it.

## Who owns what

The responsibilities the operator is worried about are real; almost none of
them belong to the tool.

| Concern | Owner | Explicitly NOT |
|---|---|---|
| who this person is → network + id | the **person index** answers it; the **router** asks it | Honcho, the model, the tool, a backend |
| may Nora read this subject | the **router**, before dispatch (`07`) | the tool's vocabulary alone; a backend |
| which backend serves it | **`SocialRouter`** | the model |
| performing the read | the backend, behind its own allowlist | the tool |
| how much comes back | the **provider contract** (`limit` + cursor) | the backend's natural page size |
| untrusting + clamping the text | the provider (R-13, already required) | the tool |
| what the content *means* | the model, via `media_fetch` | `social_fetch` |
| what survives afterwards | retention (`06`) — for social reads, nothing | custody |

What is left for the tool is thin and is what DEC-151 already built: validate a
closed `(platform, what)` pair, screen `target` for identifiers before egress,
delegate, return a three-state result.

**Subject resolution and the tier check belong to the ROUTER**, decided
2026-09-22 after the first implementation put them in the Instagram backend
and `11`'s Flow 3 drew them in the tool. Three reasons the router wins:

- It is the **single choke point** every social read passes through — below
  the tool, so the model cannot bypass it; above the backends, so no backend
  needs its own copy of an authorization rule.
- A **second backend would otherwise duplicate the tier check**, and a
  duplicated authorization rule is one that will diverge.
- `07` makes the tier ladder govern *all* social reads, not Instagram's. A
  check living in `InstagramSocialClient` cannot be that.

The tool still screens `target` for identifiers before egress — that is an
egress concern and stays where DEC-151 put it.

## Identity comes from the person index, never from Honcho

The operator asked directly how Nora learns someone's Instagram handle. **Not
from Honcho, and this matters more than it looks.**

Honcho holds what Nora *remembers and infers* about people — representations,
conclusions, things that are true-ish and improve over time. That is the right
home for "Miles posts a lot of food photos". It is the wrong home for "Miles is
igid 246967358", for three reasons:

1. **It is an authorization key.** Tier 0/1 permission is decided on it
   (`07`). A binding that can drift is a permission that can drift.
2. **A wrong binding reads the wrong person.** Not a degraded answer — a
   different human's stories, silently.
3. **It must be auditable and revocable.** Someone must be able to read a file
   and say who Nora can look up, and delete a line to stop it. Inferred memory
   supports neither.

So identity is **deterministic config or derivation**, in two tiers:

- **Room members — derived, free.** `GetThread` → `Users[].ID` is the igid.
  Measured on a three-person room: every message's `sender_fbid` matched the
  roster's `interop_messaging_user_fbid`, so the join is exact. No handle
  table is needed for anyone in a room Nora is in.
- **Everyone else — declared.** A line in the person index, written by a
  human.

Honcho may hold *observations about* a person whose identity the index
established. It never establishes one.

## Concretely: how Nora learns that this account is Miles

There is no new mechanism. It is the workflow `identity.toml` already uses for
Signal and WhatsApp, and the **social** identity arrives free alongside the
messaging one.

1. **Nora is in the Instagram room. Miles sends a message.** The adapter
   records the sender carrying *both* ids — `sender.id` (the interop messaging
   fbid) and `sender.igid` — and does not yet know it is Miles.
2. **`nora persons pending` prints the unmapped sender**, exactly as it does
   for an unrecognised Signal phone or a WhatsApp `@lid`. The username is
   printed as a *hint* so a human can tell who it is.
3. **A human writes one block:**

   ```toml
   [[persons.miles.instagram]]
   fbid = "186217604552756"   # messaging identity — who spoke
   igid = "246967358"         # social identity — whose profile
   ```

4. **Restart** (reload is startup-only in v2).
5. From then on Nora knows the sender is Miles **and** which profile is his.

The two kinds mirror WhatsApp's `phone` + `lid` precedent: list whichever you
have, match on any. `username` is deliberately **not** a kind — the identity
file already refuses renameable handles on every platform, and an Instagram
handle is renameable.

**The social capability is unlocked by the same one-time act that unlocks
messaging attribution.** Nobody maintains a separate handle table: the roster
already carried the igid (`GetThread` → `Users[].ID`, measured exact on a
three-person room), so writing the block once binds both axes.

Two cases worth naming:

- **A room member Nora has not yet been told about** is `pending`: she records
  what they said (the allowlist records every sender) but cannot answer about
  them and cannot read their profile, because tier 0 authorization needs a
  person, not a sender.
- **Someone not in any room** (tier 1) gets a block with `igid` only and no
  `fbid` — a social-only identity, which is a shape the file does not have
  today and which Tier 1 will need.

## Getting the next page, and getting the content

Two different questions with two different answers.

### The next page: the same tool, with a cursor — NOT in v1

**Scope note, and this document contradicted `11` until 2026-09-22.** `cursor`
is the shape paging *will* take; it is **out of social v1**, which serves only
`story`, and stories do not paginate. Nothing below is implemented. It is
recorded now so that when `posts` moves to this port the shape is already
decided rather than invented under pressure.

The eventual shape: `social_fetch` gains `cursor` beside `limit`, and a paged
result carries the cursor for the next call:

```
social_fetch(platform="instagram", what="posts", target=<miles>, limit=5)
  -> {status:"ok", items:[…5…], next_cursor:"QVFE…"}

social_fetch(…, limit=5, cursor="QVFE…")
  -> {status:"ok", items:[…5…], next_cursor:null}
```

A `next_cursor` of `null` means the end; **absence of a cursor is never
inferred to mean more**. The cursor is opaque to the model and to Nora — it is
the backend's own, passed back verbatim — so nothing above the provider knows
whether it is an OpenCLI offset or an Instagram pagination token.

Stories are the exception: a reel arrives **whole** from the protocol (two
items in the measured case), so `limit` clamps Nora-side and there is no
`next_cursor`. Asking for more of a story is asking for something that does
not exist.

### The content: `media_fetch`, not `social_fetch`

A `social_fetch` result carries media **URLs**. Looking at one is
`media_fetch(url=…)`, which is the composition `media_fetch`'s own docstring
already describes. `social_fetch` never returns bytes and never describes an
image — if it did, it would be two tools in one, which is the operator's
original objection.

**Two problems with that path, both real, neither yet solved:**

**1. It would persist a story, violating the retention rule.** `media_fetch`
is declared `side_effect_state: none` with an explicit narrow exemption: *"its
content-addressed cache write is the narrow exemption"*. That write goes to
the durable, sha256-keyed, cross-room, cross-restart `media_objects` cache
(R-90). So `media_fetch(url=<story frame>)` **durably stores a story frame**,
which is exactly what `06-retention-and-custody.md` forbids for social reads —
and it does so through a tool that is correct for every existing caller.

This needs a decision before Phase 2 ships. Three shapes, none free:

- an **ephemeral mode** on `media_fetch` that comprehends without caching
  (smallest change; adds a second code path to a tool whose cache is load-
  bearing elsewhere);
- **social URLs never go through `media_fetch`**, and a separate ephemeral
  comprehension path exists (clean boundary; duplicates the hard part);
- the cache entry is **written and swept**, marked ephemeral with the sweep
  keyed on it (keeps one path; a sweep that fails silently re-creates the
  violation, and `18-media-custody.md` §8 records that no blob sweep exists
  today).

Left open deliberately rather than decided here: it is a change to a shared
tool, so it belongs to whoever owns `media_fetch`, not to this bundle.

**2. Instagram CDN URLs need no authentication — measured.** The concern that
`media_fetch` would fail because it carries no session is **unfounded**, and
worth recording because the way it fails when you get it wrong is misleading.

| fetch | result |
|---|---|
| story video URL, **no cookies, no UA, no referer** | `200`, `video/mp4`, 1,402,828 bytes |
| group-message image URL, same | `200`, `image/png` |

The URLs are signed (`oh=`, `oe=`, `_nc_sid`), and the signature is the
authorization — a session is not required and adds nothing.

**The trap:** a URL taken out of the raw JSON without decoding it carries
`\u0026` where `&` belongs, and Instagram answers `403 Bad URL hash`. That is
a *signature* error, not an auth error, but it reads exactly like one and will
send whoever hits it looking for a cookie problem that does not exist. It cost
this investigation two wrong conclusions before the URL was compared byte for
byte. Any client that JSON-**decodes** rather than regex-scrapes never sees
it — which the bridge will do — so this is a note for whoever debugs a 403,
not a design constraint.

**3. Instagram CDN URLs are signed and expire.** A story URL carries `oe=` and
`oh=` parameters; it is valid for a window, not indefinitely. So a URL is only
useful promptly, and a URL stored anywhere is a dead link later. This cuts in
the design's favour — it reinforces that social reads are ephemeral by nature
— but it means a two-step "list now, look later" flow must be *soon* after,
and that a cursor must never be assumed to keep its URLs alive.

## Worked example: "Nora, read my latest story"

The concrete path, with the measured values from this study.

| # | Step | What happens |
|---|---|---|
| 1 | **ingest** | the message arrives on whatever platform; `sender_fbid 186217604552756` resolves to person `miles` through the person index |
| 2 | **engage** | Nora is addressed, the engagement pipeline decides to answer, an agent turn starts |
| 3 | **resolve subject** | "my" = the speaker = person `miles` → `persons.miles.instagram.igid = 246967358`. The model never supplies a handle. **No entry → `ambiguous`, never a guess from the name** |
| 4 | **authorize** | `miles` is a known person in a room Nora is in → tier 0. Story reads are permitted (operator, 2026-09-21). Tier 3 would refuse here |
| 5 | **route** | `(instagram, story)` is the one exception row → instameow, through the instagram-bridge, which re-checks its own allowlist before reading |
| 6 | **discover** | `GetProfile(246967358).latest_reel_media` — one cheap call, no media. Nothing there → `nothing_found` ("no story up") and the turn stops here |
| 7 | **retrieve** | `FetchReel(["246967358"])`, newest first, clamped to `limit=1` for "latest" → one item |
| 8 | **return references** | `{status:"ok", items:[{id, kind:"video", taken_at, url}], expires_at}` — no bytes, no description |
| 9 | **comprehend** | the model calls `media_fetch(url=…)` for that one item. Measured: fetches `200` unauthenticated. **This is the step that collides with retention — see below** |
| 10 | **answer** | Nora says what the story shows, in the room |
| 11 | **bookkeep** | Nora's own cursor for `(miles, story)` advances to that item's `taken_at`. Nothing else survives: not the bytes, not the URL (it expires anyway) |

Three things to notice about the shape:

- **"Latest" is cheap.** Step 6 answers "is there anything" without touching
  media, and a `limit=1` at step 7 means one item even if the reel holds
  fifteen.
- **The model spends deliberately.** Steps 7 and 9 are separate calls; a story
  can be *listed* without anything looking at it, which is what makes "does
  Miles have a story up" a different cost from "what is in it".
- **Step 9 is the only durable risk in the whole path**, and it is durable by
  accident rather than by design.

## The steps, end to end

For "what is on this person's story", the honest sequence:

```
1. subject        a person named in the conversation
2. resolve        person index (or room roster) -> instagram igid
                  no index entry -> `ambiguous`, never a guess by name
3. authorize      which tier is this subject? (07)  tier 3 -> refused
4. route          (instagram, story) -> the one exception row -> instameow
5. discover       cheap: does a story exist, how many items, how fresh
                  GetProfile.latest_reel_media — no media fetched
6. retrieve       FetchReel -> items: id, kind, taken_at, media URL
                  clamped to `limit`; URLs are references, not bytes
7. comprehend     media_fetch(url=...) per item the model actually wants
8. forget         nothing persists but the cursor (06)
```

Steps 5 and 6 are separate because they cost differently. Step 5 answers "is
there anything" for one cheap call and no media. Only a conversation that
actually needs the content pays for step 6, and only step 7 spends a model
call on pixels.

## "Unread" must be Nora's own cursor, not Instagram's

`FetchReel` returns a `seen` watermark, and it is tempting to use it. **It is
the wrong signal**, and this is measured rather than assumed:

- Read against the operator's story, `seen = 1789935673` — a timestamp set
  when *the human* viewed a story in the app, 41 seconds after it was posted.
- Two `FetchReel` calls twenty seconds apart did **not** move it, with a newer
  item outside the watermark both times.

So `seen` tracks the *account holder's app usage*, which Nora neither controls
nor participates in. Using it would make "unread" mean "the human has not
opened Instagram lately".

Nora needs **her own per-subject cursor**: the `taken_at` (or media id) of the
newest item she has already read. That cursor:

- is **bookkeeping, not content**, so the retention rule permits it. `06` says
  social *reads* do not persist; a cursor records that a read happened, not
  what was in it. Stating this explicitly because it will otherwise be
  litigated later as a violation.
- is small, per (subject, verb), and safe to lose — losing it re-reads
  something once.
- is the only durable trace a social read leaves.

## Pagination: nothing returns everything

The provider contract already carries `limit` (`{platform, what, target,
limit}`). What it needs is a discipline about shape:

| verb | returns | never returns |
|---|---|---|
| `profile` | a **summary**: name, counts, whether a story is up and how fresh | posts, story items, any media |
| `story` | items `{id, kind, taken_at, url}`, newest first, capped at `limit` | media bytes; comprehension |
| `posts` | items `{id, taken_at, caption_excerpt, url}` capped at `limit`, plus a cursor | full post bodies; comments |
| `search` | ranked `{username, name, url}` capped at `limit` | profiles of the results |

Two rules behind the table:

1. **A read returns references and a count, never a corpus.** The model can
   always ask for more; it cannot un-see a dump, and every token of a tool
   result is re-sent on every subsequent model call.
2. **Expansion is always a second call.** A profile does not expand into
   posts; a post does not expand into its media. This is the same rule as "no
   implicit tier escalation" (`07`) applied to depth rather than reach, and
   for the same reason — the model should spend deliberately, and each step
   should be visible in the trace.

Note that stories are not paginated by the protocol: a reel arrives whole, two
items in the measured case. The cap is therefore **Nora-side clamping**, not a
protocol feature, and is worth implementing anyway — a fifteen-item story
would otherwise arrive entire.

## What this forbids

- **No content in a `social_fetch` result.** URLs and metadata only;
  `media_fetch` does the looking.
- **No identity from Honcho.** The index resolves; memory observes.
- **No use of Instagram's `seen` as Nora's read state.**
- **No unbounded read.** Every verb caps at `limit`, including ones the
  protocol does not paginate.
- **No implicit expansion.** Profile → posts → media are three calls, always.
