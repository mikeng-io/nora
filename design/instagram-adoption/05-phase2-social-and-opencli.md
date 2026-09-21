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
| `GetProfile(igid)` | PASS | `instagram/profile` |
| `FetchReel([igid])` | PASS — `status=ok`, 2 reels | **nothing — stories are not in the vocabulary** |
| `SearchUsers(query)` | PASS | `instagram/search` (users only, same as today) |
| `FetchMedia(mediaID, shortcode)` | not exercised | `instagram/post` — **currently `unavailable`** |

Set against DEC-151's three recorded limitations:

1. **`instagram/post` answers `unavailable`** because OpenCLI 1.8.7 has no
   caption-by-URL command. `FetchMedia` returns `responses.Items`, a full post
   object carrying caption, like count and usertags. This limitation is
   closable.
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

## Recommendation

**Keep the tool. Add a backend. Do not add a second tool.**

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
        ├── instagram/profile    → instameow  GetProfile
        ├── instagram/posts      → instameow  FetchMedia
        ├── instagram/post       → instameow  FetchMedia   (closes limitation 1)
        ├── instagram/story      → instameow  FetchReel    (NEW vocabulary row)
        └── instagram/search     → opencli or instameow (equivalent)
```

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
reel id=221731317 @nka_bpumvg type=user_reel media_count=2
  seen=1789935673  expiring_at=2026-09-22T04:21:13+08:00
  can_reply=true can_reshare=true
  media_ids=[3575332932625258692 3993554204431792593]
  item 1  video  taken_at=2026-09-20T20:20:32  url=https://instagram.f…fbcdn.net/…
  item 2  video  taken_at=2026-09-21T04:21:13  url=https://instagram.f…fbcdn.net/…
```

Per item: media type, a directly playable CDN url, and `taken_at`. Per reel:
`expiring_at`, which is the natural retention bound — a story is gone in 24
hours and anything Nora keeps outlives its source.

### Reading a story does NOT register as watching it

This was the open ethical question and it is decidable, because `ReelInfo`
carries the viewer's own `seen` watermark.

Measured: two `FetchReel` calls twenty seconds apart both returned
`seen=1789935673` (2026-09-20 20:21:13), while item 2 — posted 2026-09-21
04:21:13 — stayed **outside** that watermark across both reads. Marking a
story seen is a separate mutation that this call does not perform.

**So the framing inverts.** The worry was that Nora reading the circle's
stories would be visible to the circle. It is not: she can read without
appearing in anyone's viewer list. That is technically less intrusive and
ethically more so, because the author gets no signal at all — the ordinary
social contract of "I can see who watched" silently does not apply to her.

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
