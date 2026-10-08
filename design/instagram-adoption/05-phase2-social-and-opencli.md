---
type: design
title: "Phase 2/3 — instameow's Social Reads Beside the Existing OpenCLI Bridge"
description: "Instagram social fetching is NOT greenfield: DEC-151's social_fetch already covers it through OpenCLI. instameow closes two of its recorded limitations and adds stories, but must inherit its read-only boundary rather than route around it."
tags: [nora, instagram, social-fetch, opencli, instameow, phase2, phase3]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: ../09-decision-index.md, rel: constrained-by, provenance: asserted}
  - {dst: 04-capability-evidence.md, rel: evidence, provenance: measured}
---

# Phase 2/3 — instameow's Social Reads Beside OpenCLI

## What already exists

Instagram social fetching is not a new capability for Nora. **DEC-151** ships
`social_fetch`: one model-facing tool with a CLOSED `(platform, what)`
vocabulary — `x`/`instagram` × `post`/`profile`/`posts`/`search` — POSTing to
a host-side bridge (`python -m nora.infra.social`, `deploy/social-bridge/`)
that runs `opencli <site> <command> <target> -f json` as an argv vector.

The runtime behind it is substantial and already operating: Xtigervnc on
`:2`, headed Chrome holding authenticated Instagram and X sessions, OpenCLI
1.8.7 on `localhost:19825`, all as systemd user services on the host, with
Nora running fine when every part of it is down (`social_fetch` answers
`unavailable`).

**R-86 is the part that matters most.** Read-only is enforced at three layers
— the tool's closed vocabulary, the bridge's host-side `READ_ONLY_ALLOWLIST`,
and the `NativeTool read_only=True` declaration — and the three are pinned
against each other. The reason is explicit in the decision: the tool runs
beside a model that can be prompt-injected *by the very content it reads*, so
the allowlist lives host-side and refuses without spawning.

Any Instagram social work in this bundle inherits that boundary. It is not a
detail of the old implementation; it is the security architecture.

## What instameow adds

The DM session measured in `04-capability-evidence.md` already exposes, on
the **same authenticated connection**, with no browser and no second
credential:

| instameow call | Measured | Maps to DEC-151 |
|---|---|---|
| `GetProfile(igid)` | PASS, but **thin** — see below | only PART of `instagram/profile` |
| `FetchReel([igid])` | PASS — full story reel | **nothing — stories are not in the vocabulary** |
| `SearchUsers(query)` | PASS | `instagram/search` (users only, same as today) |
| `FetchMedia(mediaID, shortcode)` | **FAIL on the id tried** — HTTP 400 | `instagram/post` — **still unproven** |

### GetProfile is a MESSAGING profile, not a social one

Measured field set, 14 keys:

```
fbid_v2, friendship_status, full_name, id, interop_messaging_user_fbid,
is_cannes, is_predicted_cannes, is_verified, latest_besties_reel_media,
latest_reel_media, pk, profile_pic_url, reel_media_seen_timestamp, username
```

No `follower_count`, no `following_count`, no `media_count`, no `biography`.
It answers "who is this person in messaging terms, and do they have a story
up" — not "what does their profile look like". OpenCLI's browser-scraped
profile returns the social fields; this does not replace it.

> **CORRECTION 2026-09-22: `interop_messaging_user_fbid` is NOT on this
> response.** The 14-key list above says it is. Read live through the bridge's
> `/profile` route against three real accounts, `fbid` came back EMPTY on all
> three. Those keys were measured on a DIFFERENT call — plausibly a thread
> read, whose `Users[]` does carry it — and written up as this one's.
>
> The paragraph's real claim survives. What `GetProfile` actually returns,
> measured: `username`, `full_name`, `id`, `is_verified`,
> `latest_reel_media`, and a `friendship_status` of exactly three booleans —
> `following`, `blocking`, `is_restricted`. **No `is_private`.**
>
> The generalisable error is the one worth keeping: a field set read off one
> call and attributed to another looks exactly like a measurement.

What it does add that OpenCLI has no notion of: `friendship_status` (does this
account follow them, are they restricted or blocking) and `latest_reel_media`
/ `reel_media_seen_timestamp`, which are the story hooks.

Set against DEC-151's three recorded limitations:

1. **`instagram/post` answers `unavailable`** because OpenCLI 1.8.7 has no
   caption-by-URL command. An earlier revision of this document claimed
   `FetchMedia` closes that limitation, reasoning from its return type
   (`responses.Items`, which carries caption, like count and usertags).
   **That was an inference and it did not survive contact**: the only media id
   available to try — a story frame — returned
   `HTTP 400` from `/api/v1/media/<id>/info/`. Whether it works for an
   ordinary feed post is **untested**, because no post id could be obtained
   (OpenCLI's `instagram user` is currently failing, see below). The claim is
   withdrawn until a real post id is run through it.
2. **Instagram `search` searches USERS only.** `SearchUsers` is also
   users-only. **No gain** — this limitation stands either way.
3. **Threads waits on a native OpenCLI adapter.** `instameow` is Instagram
   only. **No gain.**

And one thing neither the decision nor OpenCLI has: **stories**. Verified
against the live tool rather than assumed — OpenCLI 1.8.7's `instagram`
command list marks `story` as **[write]** ("Post a single Instagram story
image or video"). There is no story *read* anywhere in its surface.
`FetchReel` is the only measured path to reading someone's story, and reading
stories is precisely what the operator asked for in Phase 2 ("喺 group 入面唔
同人嘅 story").

### OpenCLI reads that DEC-151 does not currently use

The tool is wider than the vocabulary exposed to the model. `opencli
instagram` also marks `explore`, `followers`, `following`, `saved` and
`download` as **[read]**. None is in `social_fetch`'s closed vocabulary, and
each would be one allowlist row plus one vocabulary entry if wanted. They are
noted here because `followers`/`following` are the graph reads Phase 3 would
most plausibly want, and they already exist.

## The distinction that actually matters: URL-keyed vs person-keyed

`social_fetch` today is **URL-keyed**. Its `target` is an address someone
supplies — a link pasted into the room, a handle typed by the model — and the
tool fetches whatever is at it. Nora is a retriever pointed at a location.

What Phase 2 is actually for is **person-keyed**: Nora already knows that the
person in this room *is* this Instagram account, and can ask "what is their
latest story, what does their profile say" with no URL anywhere in the path.
The subject is someone she knows, not an address she was handed.

That is not a backend choice. It is a change to what `target` MEANS, and it
belongs in the identity layer rather than in either fetcher:

```
person in a room  ──(identity.toml / person index)──>  igid
                                                        │
                        ┌───────────────────────────────┤
                        ▼                               ▼
              FetchReel(igid)                    GetProfile(igid)
              their stories                      their profile
```

The join is already measured and free for anyone in a room Nora is in:
`GetThread` returns `Users[].ID`, which is the igid. Read against the
operator's own account this returned their two live stories and their profile
with no link supplied by anybody — the graph model working end to end.

**Person-keyed reads are therefore the design, and URL-keyed reads become the
fallback** for content nobody in the room owns: a link someone pastes, which
is what `FetchMedia`'s `(mediaID, shortcode)` signature is shaped for.

## Which backend can serve a PERSON-keyed read

This is where the "drop OpenCLI entirely" question resolves, and it resolves
on a structural fact rather than a preference. instameow's complete social
read surface is:

```
FetchReel(reelIDs, mediaID)          stories        PERSON-keyed
GetProfile(igid)                     profile        PERSON-keyed (thin)
SearchUsers(query)                   user search    query-keyed
GetUserForNewDM(fbid)                user info      PERSON-keyed
FetchMedia(mediaID, mediaShortcode)  one post       URL/ID-keyed
```

**There is no "list this user's posts" call.** Not a thin one, not a broken
one — it does not exist. So:

| Person-keyed read | instameow | OpenCLI |
|---|---|---|
| their **stories** | **yes** — the only path anywhere | no story read at all |
| their **profile, incl. social fields** | **yes, all of it — see correction below** | yes, but 429 all day |
| their **posts, as a count** | **yes** (`media_count`, `total_clips_count`) | `instagram user <username>` |
| their **posts, as a list** | **no such call** | `instagram user <username>` |
| their friendship status to us | yes, both directions (`following`, `followed_by`) | no |
| X, anything | no — Instagram only | yes |

> **CORRECTION 2026-09-22 (later the same day this table was written).** The
> "profile" row above read `yes, but no follower/following/media counts or
> biography` — true of `GetProfile`'s typed response, false of what the
> underlying document actually carries. A second call to the same document
> (`PolarisProfilePageContentQuery`), issued raw and keyed on the numeric
> igid rather than the username the first working version wrongly used,
> returns all of it: `is_private`, `follower_count`, `following_count`,
> `mutual_followers_count`, `media_count`, `total_clips_count`, `biography`,
> `is_business`, `is_professional_account`, `followed_by`. Full account,
> `deploy/instagram-bridge/internal/gateway/instameow/social_profile.go` and `07`'s matching correction.
>
> **Posts-as-a-LIST is unaffected** — `media_count` is a count, not a feed,
> and instameow's client surface still has no call that pages a user's
> posts. OpenCLI keeps that job, along with X.

So OpenCLI's remaining job for Instagram is posts-as-a-list only, plus X
entirely (instameow is Instagram-only, and X is half of `social_fetch`'s
vocabulary). It cannot be dropped, but the "profile fields" reason for
keeping it — the reason this section gave twice — no longer holds.

The defensible split is therefore not "one tool wins" but a per-row backend
choice, which is what the routing table below encodes. The prize the operator
was after — fewer external dependencies, no VNC desktop, no headed Chrome — is
only reachable by giving up X *and* posts *and* the profile fields, which is a
much larger sacrifice than it first looks.

## Recommendation

> **SUPERSEDED FOR INSTAGRAM 2026-09-23 by `15-bridge-is-the-only-entry.md`.**
> The per-row split below (OpenCLI for posts lists, instameow for the rest)
> is replaced by one rule: every Instagram verb goes through instagram-bridge.
> A verb with no bridge mechanism answers `unavailable`; it does not fall back
> to OpenCLI.

**Keep the tool. Add a backend for what instameow is actually better at. Do
not add a second tool.**

The split is narrower than this document first claimed, and measurement moved
it twice. instameow is **not** a general replacement for the browser path:

`social_fetch` stays the one model-facing surface with its closed vocabulary.
The host-side bridge gains an Instagram backend that calls instameow instead
of spawning OpenCLI, selected per `(platform, what)` row:

```
social_fetch(platform, what, target)        ← unchanged model surface
        │
        ▼
social bridge  READ_ONLY_ALLOWLIST          ← unchanged security boundary
        │
        ├── x/*                  → opencli (unchanged)
        ├── instagram/story      → instameow  FetchReel   (NEW row; opencli cannot)
        ├── instagram/profile    → instameow, one call (REVERSED 2026-09-22 —
        │                          see the correction above; was opencli+instameow)
        ├── instagram/posts      → instameow for the COUNT, opencli for the LIST
        │                          (instameow still has no listing call)
        ├── instagram/post       → still unavailable, both paths
        └── instagram/search     → either; equivalent, users only
```

### Live state of the browser path, 2026-09-21

Measured minutes after OpenCLI came back up, and it is **partially broken**:

> **RE-MEASURED 2026-09-22, and the failure is now a different and more
> diagnostic one.** `profile` no longer fails with "Navigation rejected"; it
> returns **HTTP 429 on `web_profile_info`**, sustained across five hours,
> and so does `following`, which calls the same endpoint. `whoami` and
> `search` answer fine from the SAME CLI and the SAME session, and the
> operator reports the account working normally in a browser at the same
> time.
>
> So this is **not the account being throttled and not a transient**: it is
> Instagram refusing OpenCLI's REQUEST SHAPE. `@jackwener/opencli` is at
> 1.8.7, which is also the latest on the registry (published 2026-08-23), so
> waiting for upstream is not a plan. `user` fails differently again —
> served an HTML login/challenge page rather than a 429.

| command | result |
|---|---|
| `instagram whoami` | works |
| `instagram search` | works, returns ranked users |
| `instagram profile` | **fails** — `Pre-navigation to https://www.instagram.com failed: Navigation rejected` |
| `instagram user` (posts) | **fails** — returns HTML, `SyntaxError: Unexpected token '<'` |

The social bridge service itself is healthy (`systemctl --user is-active
nora-social-bridge` → active, `/health` → 200), so this is the browser
runtime, not the seam. It is the ordinary failure mode of a headed-Chrome
scraper and it is why story reads moving to the API path is worth doing on
reliability grounds alone — `FetchReel` and `GetProfile` answered correctly in
the same minutes that `profile` could not navigate.

Why this shape rather than a new tool:

- **The allowlist is the boundary and it is keyed on `(site, command)`.** A
  second tool would need its own equivalent, and R-86's three-layer pin
  exists because one layer was judged insufficient. Two independent surfaces
  is the failure mode that decision was written against.
- **A new vocabulary row plus one allowlist row is exactly the extension
  shape DEC-151 names**: "This holds unless OpenCLI grows a native Threads
  adapter or an Instagram post-read command, **each of which is one allowlist
  row plus one vocabulary entry**." Swapping the implementation behind a row
  is smaller than that, not larger.
- **It keeps the heavy runtime optional.** X still needs Chrome and OpenCLI;
  Instagram stops needing them. If the desktop is down, Instagram reads keep
  working and X answers `unavailable` — strictly better than today, where
  both die together.

## The risk this creates

**Two Instagram sessions for one account.** The Chrome profile on norty
"holds the Instagram and X sessions", and the DM bridge holds its own. If
both are the same Instagram account, adopting instameow means two concurrent
authenticated sessions from one host, which is a plausible way to provoke a
checkpoint — the thing `01-bridge-container.md` says must not happen.

**Answered by measurement, 2026-09-21.** `opencli instagram whoami` on norty
returns:

```json
{"logged_in": true, "site": "instagram",
 "user_id": "79597894900", "username": "not_____nora", "full_name": "Nora"}
```

`79597894900` is the same `ds_user_id` the DM probe read from its own cookie
session. **It is one account, held twice.**

That resolves the question in the better direction: adopting instameow for
Instagram **removes** a session rather than adding one. Retire Instagram from
the Chrome profile, let the bridge own the account, and the browser profile
keeps only X — which also means an OpenCLI outage stops taking Instagram down
with it, the exact failure observed on 2026-09-21 while this bundle was being
written.

It also means the two sessions are running concurrently **right now**: the
longevity soak and the browser profile, same account, same host. No auth error
has appeared in the soak so far. That is weak evidence that Instagram
tolerates multiple device sessions — which it obviously does in general — and
is **not** evidence that a bridge session is safe long-term. The soak is the
instrument for that, not this observation.

## What a story read returns, and what it costs (measured 2026-09-21)

Read against the operator's own story, with their approval, before anyone
else's — the circle-first sequencing they asked for.

`FetchReel([igid])` returns a `user_reel` with everything needed:

```
reel id=246967358 @nka_bpumvg type=user_reel media_count=2
  seen=1789935673  expiring_at=2026-09-22T04:21:13+08:00
  can_reply=true can_reshare=true
  media_ids=[3811649187021092147 3236689281856363849]
  item 1  video  taken_at=2026-09-20T20:20:32  url=https://instagram.f…fbcdn.net/…
  item 2  video  taken_at=2026-09-21T04:21:13  url=https://instagram.f…fbcdn.net/…
```

Per item: media type, a directly playable CDN url, and `taken_at`. Per reel:
`expiring_at`, which is the natural retention bound — a story is gone in 24
hours and anything Nora keeps outlives its source.

### Reading a story and the viewer list — WITHDRAWN, the instrument was wrong

**This section claimed, from 2026-09-21 to 2026-09-22, that reading a story is
invisible to its author. That claim is withdrawn.** It is recorded rather than
deleted because the operator approved story reads partly on the strength of
it.

The reasoning was: `ReelInfo` carries a `seen` watermark, it did not advance
across two `FetchReel` calls, therefore the read left no trace. The flaw is
named in this bundle's own node 10: **`seen` tracks the account holder's app
usage** — what the logged-in human viewed in Instagram — which Nora neither
controls nor participates in. If that reading is right, `seen` *could not*
move for a Nora-side read whatever Instagram did, so an unchanged value
measures nothing at all.

The property that matters is **what the author sees in their own viewer
list**, and it was never read. It is cheap to settle: read a story from Nora's
account, then look at the viewer list on the author's phone. Until then this
is UNMEASURED, and the ethical question it was thought to close is open.

What the original measurement *does* support, stated narrowly:

Measured: two `FetchReel` calls twenty seconds apart both returned
`seen=1789935673` (2026-09-20 20:21:13), while item 2 — posted 2026-09-21
04:21:13 — stayed **outside** that watermark across both reads. Marking a
story seen is a separate mutation that this call does not perform.

**So the framing inverts.** The worry was that Nora reading the circle's
stories would be visible to the circle. It is not: she can read without
appearing in anyone's viewer list. That is technically less intrusive and
ethically more so, because the author gets no signal at all — the ordinary
social contract of "I can see who watched" silently does not apply to her.

[2026-09-24: this paragraph is part of the withdrawn claim above and states
invisibility as settled. It is not: whether the author sees the view is
unmeasured (design 04, 05 §Reading a story). Kept as written, as the record
of what the story-read approval was partly based on.]

This is now a decision with the facts in hand rather than a risk to weigh:
the operator has approved story reads for the circle, starting with their own
and widening in a second wave. Recorded here so the reason the question was
closed is legible later.

## Policy has no shape for this yet

`policy.toml` is room-shaped: `voice`, `relevance_gate`, `music` are
properties of a room. "May Nora read this person's stories" is a property of
a **person**, and there is no precedent for a person-scoped capability.

Stories are also not like a post: Instagram tells the author who viewed their
story. A story read is *observable by the person being read*, which no
existing Nora read is. Phase 2 therefore needs a decision on whether
`social_fetch` may fetch stories at all, and if so whose — before
implementation, not during it.

## Phase 3

The wider feed — posts beyond the group's members — stays with OpenCLI. It is
already built, it already covers X, and its browser runtime reaches content
an authenticated DM session has no reason to touch. `instameow` should not
grow into a general scraper; keeping it scoped to the group and its members
is what keeps the DM session's risk profile defensible.
