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

And one thing neither the decision nor OpenCLI has: **stories**, which is
precisely what the operator asked for in Phase 2 ("喺 group 入面唔同人嘅
story"). `FetchReel` is the only measured path to them.

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

**Operator input required before Phase 2 starts:** which Instagram account
does the social Chrome profile hold — `@not_____nora`, or a different one?
The answer changes the plan:

- **Same account** → adopting instameow for Instagram is strictly *safer*,
  because it removes a session rather than adding one: retire Instagram from
  the Chrome profile and let the bridge own it.
- **Different account** → two identities are reading, and which one a story
  is fetched *as* becomes a visible fact (stories record viewers). That is a
  policy question about the people in the room, not a routing question.

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
