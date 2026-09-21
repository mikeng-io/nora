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
| who this person is → network + id | **person index** (`identity.toml`, or derived from room membership) | Honcho, the model, the tool |
| may Nora read this subject | **tier check** (`07`) | the tool's vocabulary alone |
| which backend serves it | **`SocialRouter`** | the model |
| performing the read | the backend, behind its own allowlist | the tool |
| how much comes back | the **provider contract** (`limit` + cursor) | the backend's natural page size |
| untrusting + clamping the text | the provider (R-13, already required) | the tool |
| what the content *means* | the model, via `media_fetch` | `social_fetch` |
| what survives afterwards | retention (`06`) — for social reads, nothing | custody |

What is left for the tool is thin and is what DEC-151 already built: validate a
closed `(platform, what)` pair, screen `target` for identifiers before egress,
delegate, return a three-state result.

## Identity comes from the person index, never from Honcho

The operator asked directly how Nora learns someone's Instagram handle. **Not
from Honcho, and this matters more than it looks.**

Honcho holds what Nora *remembers and infers* about people — representations,
conclusions, things that are true-ish and improve over time. That is the right
home for "Miles posts a lot of food photos". It is the wrong home for "Miles is
igid 221731317", for three reasons:

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
