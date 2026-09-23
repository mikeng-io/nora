---
type: design
title: "instagram_read and instagram_search — the per-network social tool convention"
description: "Nora reads Instagram through two tools of its own, over the bridge only: instagram_read takes a person (by name), a pasted link or a typed handle, as three separate parameters, and returns profiles and stories now and posts once a mechanism exists; instagram_search takes free text. Looking at the media stays media_fetch. The naming is the convention every later network follows."
tags: [nora, instagram, social, tools, instagram_read, instagram_search, catalogue, naming]
timestamp: '2026-09-24'
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

> **2026-09-24: the first reason no longer holds.** The catalogue's deferral
> switch (`[llm] defer_tools`) measured worse per turn and stays `false`
> (`../tool-catalogue/05-measurement.md` §3a), so every tool, these included,
> ships RESIDENT with its full schema. `instagram_read` costs its schema on
> every turn: 382 tokens (cl100k, `json.dumps` of its `tool_spec`, the 05 §1
> method). The same PR cut `social_fetch`'s schema from 847 to 577 by removing
> its Instagram rows, so the resident total moved +112 (27,044 → 27,156). The
> split is still the decision; what it costs is now measured rather than
> assumed away. 05 §3a names this tool set's growth as a re-measure trigger.

## The naming convention, for every network

Chosen to follow the names Nora's tools already use, not a new scheme:

| Existing pair | Suffix meaning |
|---|---|
| `web_fetch`, `media_fetch` | `_fetch`: retrieve what is at an **address** (a URL) |
| `note_read` | `_read`: read a **known thing by what it is called** (a note by its title, a person by their name), not by where it lives |
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
as a `what` value. `social_fetch` does that today (`search` is one of its
`what` arms), and it is the shape being replaced; everywhere else Nora keeps
search as its own tool (`web_search` / `web_fetch`, `note_search` /
`note_read`), because search takes free text and read takes a subject.

Instagram gets `instagram_read` and `instagram_search` first. `x_read` /
`x_search`, `threads_read`, `linkedin_read` follow the same shape later.

## `instagram_read`

```
instagram_read(what: str | None = None, person: str | None = None,
               link: str | None = None, handle: str | None = None,
               limit: int = 5, cursor: str | None = None)
```

> **As built in P5 (2026-09-24): `instagram_read(what, person)`, both
> required.** `link` and `handle` are not in the schema until P10, and `limit`
> and `cursor` are not in it until `posts` (P13), per §"Verbs join only when
> they work end to end" below: a parameter in the schema is a word
> advertising an input, and a resident schema is paid for on every turn. A
> story is returned whole (the client clamps frames and text on its side).
> The signature above is the P13 end state.

**Exactly one of `person`, `link`, `handle`.** Three parameters, not one
overloaded `target`: a person's name and an Instagram handle are the same
shape of string ("miles" could be either), and nothing could tell them apart
reliably. `media_fetch` already solves the same problem the same way (`code`
for something in the conversation, `url` for an address).

| Input | Means | Resolved by |
|---|---|---|
| `person` | a person, named the way the message named them (a nickname is fine) | **the tool** resolves the name with the same room-scoped lookup `about` uses (`room.scope().find`), because only the tool holds the turn's `room`; the router then takes the resolved person through the person index to their Instagram account. No match → `nothing_found`; several people or several accounts → `ambiguous`; a person with NO igid is `ambiguous` too (the port's `None` is «none or several», and `nothing_found` would claim a read ran). An unreadable room roster is `unavailable`. **No tool hands Nora a person id**, so the tool never asks for one |
| `link` | a pasted Instagram URL | the link parser; the LINK decides the read (profile, post, reel or story, `15` §Links) |
| `handle` | an Instagram username someone typed (no `@`) | the bridge (`GET /accounts?username=`) |

`what` is required unless the input is a `link`. A link whose kind
contradicts `what` answers `unavailable` rather than guessing which was meant.
`cursor` is only meaningful with `what="posts"`; passed with anything else it
answers `unavailable` (a profile or a story has no next page, `10`).

### Listing and reading one item are both `_read`; looking at the media is not

| `what` | Returns |
|---|---|
| `profile` | the account: bio, private or public, follow state both ways, follower / following / post / reel counts |
| `story` | the current story as a list of frames (whole; no paging) |
| `posts` (when it exists, P13) | a paged list of recent posts: `items` plus `next_cursor` (`10`'s cursor rule: opaque, `null` is the end) |
| (a post or reel `link`: the link decides, no `what`) | that one item |

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
| `profile`, `story` by `person` | the tool's first PR | `GET /accounts/{igid}`, `GET /accounts/{igid}/story` |
| `profile`, `story` by `link` or `handle` | P10 | `GET /accounts?username=` |
| a post / reel by link | P12 | `GET /posts/{shortcode}` |
| `posts` (a person's list) | not before P13 finds a mechanism | none today |

A verb, an input kind, and the words advertising them all land together. The
first PR's tool accepts only `person` and only `profile` / `story`; `link` and
`handle` arrive with P10; `posts` is absent from the vocabulary, the
docstring and the catalogue line until it works. A catalogue line that names
a read she cannot do is the line she will call from (see the catalogue
contract below).

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
| resolve a NAME → person | the tool, with the turn's `room` (the lookup `about` uses) |
| resolve person → igid, igid → person | `SocialSubjectIndex` (`17` P3), called by the router |
| authorize (Tier 0, story only) | `SocialRouter`, the single choke point for EVERY social tool, old and new |
| call the bridge | `InstagramSocialClient` (`17` P4) |
| the model-facing contracts | the two tools in `nora/agent/tools.py` |

The tools stay thin: validate arguments, resolve a spoken name to a person
(the one resolution that needs the conversation, and the tool is the only
layer that has it, exactly as `about` does today), call the router, return
its answer. Neither resolves an account nor authorizes: both stay in the
router, which is composed once and holds no per-conversation state. A tool with its own authorization copy is
the drift `10` §"Who owns what" forbids.

### What the router has to learn

Today `SocialRouter` is keyed on `(platform, what)` and resolves only
`instagram/story` through the person index; every other row's target passes
through untouched (`TIER_NAMED`). That cannot serve this contract, so the
router changes, and the change is part of the tool's first PR (for `person`)
and P10 (for `link` / `handle`), not a detail left to either:

- **Resolution is keyed on `(platform, what, input kind)`.**
  - `person` → the tool has already resolved the name to a person id →
    `igid_for`. Any read.
  - `handle` → the bridge's username lookup → igid.
  - `link` → the parser → (read, username or shortcode) → as for `handle`, or
    straight to the post route.
- **Authorization stays keyed on the read, not the input.** Only `story` has
  a reach check. For `person` it is `is_tier_zero(person)`. For `link` and
  `handle` it is `igid → person_for_igid → is_tier_zero`, **on the igid the
  input named, never re-derived through `igid_for`** (`15` §Links). An igid
  that maps to nobody is not in any room: `refused`, and no story request is
  sent. `profile` and `post` need resolution only.
- **The port gains `cursor` and the input kind.** `SocialProvider.read` and
  `SocialRouter.read` take `(platform, what, target, limit)` today; both grow
  a cursor and a typed target, and the contract suite
  (`tests/contract/test_social_provider_contract.py`) grows with them.

  > **As built in P5 (2026-09-24).** `SocialRouter.read` grew `target_kind`
  > (`named` | `person`, default `named`, the port's own meaning), with
  > resolution keyed on `(platform, what, target_kind)` (`SUBJECT_INPUTS`)
  > and reach on the read alone (`TIER_ZERO_READS`, story only). The
  > BACKENDS' port did not grow: the kind is consumed by resolution, and a
  > backend only ever sees a resolved target (an igid, or a typed string), so
  > the argument would be one every backend ignores. `cursor` joins with the
  > read that pages (P13). The Instagram rows admit `person` only; a `named`
  > target there is `unavailable` until P10's username lookup exists.

## Read-only, at three layers, still one table

R-86 pins read-only at three layers: the tools' closed vocabulary, the backend
allowlist, and `read_only=True`. The bridge also serves writes (`/send`,
`/react`, …), so on this path read-only is enforced by the **client**:
`InstagramSocialClient` issues only `GET` to `/accounts…` and `/posts…`, and a
test pins that its request set contains nothing else. `test_r86_…` derives all
three layers from one vocabulary table covering every social tool.

## The tool-catalogue contract (porgy-58, 2026-09-23; amended 2026-09-24)

> **Amended 2026-09-24 (catalogue owner; landed with P5).** Two things this
> section assumed changed after it was written. (1) The catalogue line is no
> longer the docstring's first SENTENCE; it is the first PARAGRAPH, and that
> paragraph is ADR 0007's L1 — what the tool does AND when to call it —
> gated by `tests/test_tool_surface_policy.py::
> test_the_opening_paragraph_is_an_l1`: a `WHEN_TRIGGERS` phrase, and ≤40
> cl100k tokens rendered as `- name — <paragraph>`; the block ceiling is
> 2,000 and the mean 33 (`tests/unit/test_tool_catalogue.py`). (2) Deferral
> is OFF (`../tool-catalogue/05-measurement.md` §3a), so the tools ship
> resident with their full schemas and the "she calls from the catalogue
> line" argument below applies only if the switch is ever turned on. The
> line must still name the input and separate the siblings, because it is
> also the opening of the resident description. The bullets below are
> updated in place; the drafts are replaced by what shipped.

Each item is an existing test that goes red, applied to both tools:

- **Catalogue line = the docstring's first paragraph**, what + when, ≤40
  tokens (cl100k) as a catalogue line, mean ≤33 across tools, no identical
  lines. Each PR's line is re-measured by the gate itself.
- **The line names the input the tool takes** (a person, a link, a handle;
  free text), not only the network, names Instagram, and separates each tool
  from its confusable siblings (`social_fetch`, `web_fetch`, `media_fetch`,
  and each other). It is what she would call from if deferral were on
  (measured, porgy-58), and it opens the resident description either way.
  - `instagram_read`, as shipped in P5 (34 tokens as the L1 gate counts it): *"Read someone's
    Instagram profile or current story, by their name. Call this when
    somebody asks about a person's Instagram or what is on their story."*
    From P10 it names handles and pasted links; `posts` joins the line only
    with P13.
  - `instagram_search` (P11), draft: *"Search Instagram for accounts by name
    or handle."* plus its when-clause.
- **The opening** contains a `WHEN_TRIGGERS` phrase
  (`tests/test_tool_surface_policy.py`), with no permission-suppression wording.
- **A `NativeTool` entry with every field**, shaped like `social_fetch`'s:
  `read_only=True`, `output_contract="three_state"`, `source_tier`,
  `cacheable`, and the side-effect / idempotency / retry triple.
- **Unit-safe for the Dreamer** (`UNIT_SAFE_TOOLS` in `nora/agent/dreamer.py`).
  > **Not as built (P5, 2026-09-24): `instagram_read` is classified
  > ROOM-scoped**, beside `about` and `online_now`
  > (`tests/unit/test_dreamer_runtime.py`). This bullet was written before
  > name resolution moved into the tool; the tool now resolves the name
  > against the room's live roster, which a unit-keyed Dreamer tick does not
  > have, and `UNIT_SAFE_TOOLS`' own rule refuses room-scoped reads. In a
  > unit-keyed tick it could only ever answer `unavailable`. Moving it is a
  > one-line change and a separate decision.
- **Not in `HOT_TOOLS`**: that set is measured, not hand-picked.
- No `ToolScope` change: they read Instagram from any room.

## What happens to `social_fetch`

It loses its Instagram rows **verb by verb, each in the PR that lands its
replacement** (`15`'s per-verb cutover), so no Instagram read ever has two
tools and none goes dark in between:

| `social_fetch` row | Today (measured) | Leaves in |
|---|---|---|
| `instagram/profile` | OpenCLI answers **429**, all day, 2026-09-22 | `instagram_read`'s first PR: nothing working is lost |
| `instagram/story` | never had a backend (the Python side is unwired) | first PR |
| `instagram/posts` | OpenCLI's `user` returns Instagram's **HTML shell**, even from its own browser | first PR: nothing working is lost; the capability waits for P13 |
| `instagram/search` | **works** through OpenCLI | **P11**, when `instagram_search` lands, not before |

Until P11 `social_fetch` keeps exactly one Instagram verb, search. Then its
`platform` enum becomes `{"x"}`, until `x_read` / `x_search` replace it and it
is deleted.

The story row's docstring has a live defect that goes with it: it tells Nora
the target is a person id "the same one `lookup_person` and `about` take".
Neither tool takes or returns an id. `instagram_read`'s `person` parameter
replaces it.

**Its first sentence changes in that same PR too.** It currently reads "Read a
post, profile, or search results from X or Instagram", and it is `social_fetch`'s
catalogue line. Left as is, it overlaps both new lines: exactly the
confusable pair the catalogue gates exist to prevent (porgy-58, 2026-09-23).
It must name X only.

> **As built in P5 (2026-09-24):** *"Read X (Twitter) posts, profiles and
> searches, or search Instagram accounts. Call this when somebody links or
> asks about something on X, or wants an Instagram account found."* (39
> tokens as the L1 gate counts it). It names X for every read, and Instagram for the one verb it still
> serves: the overlap with `instagram_read` is gone (no profile, no story),
> and there is no `instagram_search` line to overlap until P11, when the
> Instagram clause leaves with the row. Naming X only would have left
> Instagram account search, which works today, out of the line that
> advertises it — the per-verb cutover's "none goes dark in between", one
> level up. A `story` or Instagram `profile` asked of `social_fetch` answers
> `unavailable` with a note naming `instagram_read`.

## Order

`instagram_read` **replaces P5** of `17`: it builds on P2–P4 and lands after
the catalogue's #367 and #393 (rebased onto them). `instagram_search` lands
with P11. P10 and P12 each add a read to `instagram_read`.

## How to prove it

Routing cases in the catalogue's tool_selection eval corpus (on #393's
branch), run in both catalogue arms (resident and deferred), n ≥ 3. They are
added after this design merges, so the corpus never names a tool that does
not exist:

> **2026-09-24:** with deferral off (05 §3a) the resident arm is the one that
> ships; the deferred arm is worth running only as part of re-measuring the
> switch. P5 added four rows (`route_instagram_read_story`,
> `route_instagram_read_posted`, `route_social_fetch_instagram_search`,
> `route_social_fetch_x_link` in `evals/corpora/tool_selection.yaml`); until
> P11 an Instagram account search still routes to `social_fetch`. The link,
> post and `instagram_search` rows land with P10–P12.

| Message | Must call | Must not call |
|---|---|---|
| a pasted `instagram.com/<user>/` link, "who's this?" (from P10) | `instagram_read` | `social_fetch`, `web_fetch` |
| a pasted `instagram.com/p/<code>/` link (from P12) | `instagram_read` | `web_fetch` |
| "what's on <friend>'s story" | `instagram_read` | `social_fetch` |
| "what's in that photo on her story" (after a read) | `media_fetch` | a second `instagram_read` |
| "find the account for <name> on IG" (from P11) | `instagram_search` | `instagram_read` |
| a pasted `x.com/…/status/…` link | `social_fetch` | `instagram_read` |
| a pasted news article link | `web_fetch` | `instagram_read` |

Live read-back: one trace in Jaeger per case, containing Nora's tool span and
the bridge's server and upstream spans. That needs tool spans in Jaeger: the
collector's filter was changed on 2026-09-23 to stop dropping them (it had
dropped every span carrying `gen_ai.operation.name`, which Strands also puts
on tool spans). Confirmed live on 2026-09-23: Jaeger now holds Nora's
`invoke_agent`, `execute_event_loop_cycle` and `execute_tool` spans, and no
`chat` spans, which is the intended split.
