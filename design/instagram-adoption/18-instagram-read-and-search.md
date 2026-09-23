---
type: design
title: "instagram_read and instagram_search — the per-network social tool convention"
description: "Nora reads Instagram through two tools of its own, over the bridge only: instagram_read takes a person, a pasted link or a typed handle and returns profiles, stories and posts, singly or as a paged list; instagram_search takes free text. Looking at the media stays media_fetch. The naming is the convention every later network follows."
tags: [nora, instagram, social, tools, instagram_read, instagram_search, catalogue, naming]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 09-tool-surface-and-naming.md, rel: realizes, provenance: asserted}
  - {dst: 10-discovery-retrieval-and-paging.md, rel: constrained-by, provenance: asserted}
  - {dst: 15-bridge-is-the-only-entry.md, rel: realizes, provenance: asserted}
  - {dst: 17-implementation-plan.md, rel: amends, provenance: asserted}
  - {dst: ../tool-catalogue/00-overview.md, rel: constrained-by, provenance: asserted}
---

# instagram_read and instagram_search

## Decision (operator, 2026-09-23)

Social reads split **per network**. This realizes the reopening recorded in
`09-tool-surface-and-naming.md`, under the two conditions it kept:

1. **The model names the network, never the account.** The tool takes a
   person, a link, or a handle a human typed. The person index or the bridge
   resolves the account. Nora never infers one.
2. **One read-only allowlist underneath.** However many tools sit on top,
   "what may Nora read" is one table (R-86).

Two things made the split affordable. The tool catalogue
(`design/tool-catalogue/`) cuts a tool's resident cost from about 400 tokens
to one catalogue line. And `15` gives Instagram exactly one backend, so the
tools for one network map onto one backend.

## The naming convention, for every network

Chosen to follow the names Nora's tools already use, not a new scheme:

| Existing pair | Suffix meaning |
|---|---|
| `web_fetch`, `media_fetch` | `_fetch`: retrieve what is at an **address** (a URL) |
| `note_read` | `_read`: read a **subject** Nora knows (an id) |
| `web_search`, `note_search`, `media_search` | `_search`: **free text**, always its own tool, never a mode of fetch or read |

So each network gets:

| Tool | Input | Output |
|---|---|---|
| `<network>_read` | a person, a pasted link, or a typed handle | the subject's profile, stories, posts: one item or a paged list |
| `<network>_search` | free text | matching accounts (or posts, where the network searches posts) |
| a write, if one is ever wanted | its own tool per action, e.g. a story post | never a mode of `_read` |

`<network>_fetch` was the first draft and was rejected. `_fetch` in Nora means
address-keyed retrieval, and 09's central rule is "a subject, not an address";
a `_fetch` name would encode the thinking 09 rejects. It also folded search in
as a `what` value, which Nora has never done: search takes free text, read
takes a subject, and the two inputs are why `web_search` and `web_fetch` are
separate tools.

Instagram gets `instagram_read` and `instagram_search` first. `x_read` /
`x_search`, `threads_read`, `linkedin_read` follow the same shape later.

## `instagram_read`

```
instagram_read(target: str, what: str | None = None, limit: int = 5, cursor: str | None = None)
```

| `target` | `what` | Read |
|---|---|---|
| a person id (from `lookup_person` / `about`) | `profile`, `story`, `posts` | resolved through the person index |
| an Instagram link | omitted, or matching the link | the LINK decides: profile, post, reel or story (`15` §Links) |
| a handle someone typed (no `@`) | `profile`, `story`, `posts` | resolved by the bridge (`GET /accounts?username=`) |

`what` is required unless `target` is a link. A link whose kind contradicts
`what` answers `unavailable` rather than guessing which was meant.

### Listing and reading one item are both `_read`; looking at the media is not

| `what` | Returns |
|---|---|
| `profile` | the account: bio, private or public, follow state both ways, follower / following / post / reel counts |
| `story` | the current story as a list of frames |
| `posts` | a paged list of recent posts: `items` plus `next_cursor` (`10`'s cursor rule: opaque, `null` is the end) |
| a post / reel link | that one item |

Every item, listed or single, carries the **text layer** and **references**:
caption, author, when it was posted, Instagram's own alt text
(`accessibility_caption`, e.g. "a photo of a plate"), kind (image / video /
carousel), and media URLs. It does **not** carry what the media shows.

**Seeing an image or video is `media_fetch(url=…)`**, called on the one item
that matters, exactly as `10-discovery-retrieval-and-paging.md` divides it:
the social tool finds *that* something exists and where; `media_fetch` finds
out *what it is*. Three reasons it stays that way:

- **Cost.** A story of ten frames read with vision is ten vision calls to
  answer one question. The text layer is one cheap read; she looks at the one
  frame the question is about.
- **The text layer often answers alone.** Alt text and caption say "she's at
  the beach" without any vision call.
- **One place understands media.** `media_fetch` already handles Instagram's
  CDN (its domain allowlist and custody) and the two layers the operator set
  for it: prepared answers, and a fresh question that bypasses the cache.
  A second copy inside `instagram_read` would drift from it.

### Verbs join only when they work end to end

"No verb admitted because one backend might grow it" (`09`):

| Read | Lands in | Bridge route |
|---|---|---|
| `profile`, `story` by person | the tool's first PR | `GET /accounts/{igid}`, `GET /accounts/{igid}/story` |
| `profile`, `story` by link or handle | P10 | `GET /accounts?username=` |
| a post / reel by link | P12 | `GET /posts/{shortcode}` |
| `posts` (a person's list) | not before P13 finds a mechanism | none today |

## `instagram_search`

```
instagram_search(query: str, limit: int = 5)
```

Accounts only: Instagram's search finds people, not post content. Backed by
`GET /accounts?query=` (P11). It returns accounts with their handles, so a
follow-up read can name one; it never reads anyone's content itself.

## Results

The same envelope as every social read: R-27's `ok`, `nothing_found`,
`unavailable`, plus `ambiguous` and `refused` where resolution and the Tier 0
check (story only) produce them. The body is the curated DTO (`16`
§Resources), never Instagram's raw response, wrapped as untrusted and clamped.

## Where each concern lives

| Concern | Owner |
|---|---|
| parse the link, pick the read | the link parser, a new module under nora/infra/social (P10) |
| resolve person → igid, igid → person | `SocialSubjectIndex` (`17` P3) |
| authorize (Tier 0, story only) | `SocialRouter`, the single choke point for EVERY social tool, old and new |
| call the bridge | `InstagramSocialClient` (`17` P4) |
| the model-facing contracts | the two tools in `nora/agent/tools.py` |

The tools stay thin: validate arguments, call the router, return its answer.
Neither resolves nor authorizes. A tool with its own authorization copy is
the drift `10` §"Who owns what" forbids.

## Read-only, at three layers, still one table

R-86 pins read-only at three layers: the tools' closed vocabulary, the backend
allowlist, and `read_only=True`. The bridge also serves writes (`/send`,
`/react`, …), so on this path read-only is enforced by the **client**:
`InstagramSocialClient` issues only `GET` to `/accounts…` and `/posts…`, and a
test pins that its request set contains nothing else. `test_r86_…` derives all
three layers from one vocabulary table covering every social tool.

## The tool-catalogue contract (porgy-58, 2026-09-23)

Each item is an existing test that goes red, applied to both tools:

- **Catalogue line = the docstring's first sentence**, ≤40 tokens (cl100k),
  mean ≤25 across tools, no identical lines. It names Instagram and separates
  each tool from its confusable siblings (`social_fetch`, `web_fetch`,
  `media_fetch`, and each other). Drafts:
  - `instagram_read`: *"Read Instagram: a person's profile, story or posts, or
    what a pasted Instagram link points to."*
  - `instagram_search`: *"Search Instagram for accounts by name or handle."*
- **The opening** contains a `WHEN_TRIGGERS` phrase
  (`tests/test_tool_surface_policy.py`), with no permission-suppression wording.
- **A `NativeTool` entry with every field**, shaped like `social_fetch`'s:
  `read_only=True`, `output_contract="three_state"`, `source_tier`,
  `cacheable`, and the side-effect / idempotency / retry triple.
- **Unit-safe for the Dreamer** (`UNIT_SAFE_TOOLS` in `nora/agent/dreamer.py`).
- **Not in `HOT_TOOLS`**: that set is measured, not hand-picked.
- No `ToolScope` change: they read Instagram from any room.

## What happens to `social_fetch`

It loses its Instagram rows **in the same PR** that lands `instagram_read`,
so no Instagram read ever has two tools. Its `platform` enum becomes `{"x"}`
and its docstring X-only, until `x_read` / `x_search` replace it and it is
deleted.

## Order

`instagram_read` **replaces P5** of `17`: it builds on P2–P4 and lands after
the catalogue's #367 and #393 (rebased onto them). `instagram_search` lands
with P11. P10 and P12 each add a read to `instagram_read`.

## How to prove it

Routing cases in the catalogue's tool_selection eval corpus (on #393's
branch), run in both catalogue arms (resident and deferred), n ≥ 3. They are
added after this design merges, so the corpus never names a tool that does
not exist:

| Message | Must call | Must not call |
|---|---|---|
| a pasted `instagram.com/<user>/` link, "who's this?" | `instagram_read` | `social_fetch`, `web_fetch` |
| a pasted `instagram.com/p/<code>/` link | `instagram_read` | `web_fetch` |
| "what's on <friend>'s story" | `instagram_read` | `social_fetch` |
| "what's in that photo on her story" (after a read) | `media_fetch` | a second `instagram_read` |
| "find the account for <name> on IG" | `instagram_search` | `instagram_read` |
| a pasted `x.com/…/status/…` link | `social_fetch` | `instagram_read` |
| a pasted news article link | `web_fetch` | `instagram_read` |

Live read-back: one trace in Jaeger per case, containing Nora's tool span and
the bridge's server and upstream spans. That needs tool spans in Jaeger: the
collector's filter was changed on 2026-09-23 to stop dropping them (it had
dropped every span carrying `gen_ai.operation.name`, which Strands also puts
on tool spans). Not yet confirmed with a live trace; that confirmation is
part of the tool's first PR.
