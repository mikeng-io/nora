---
type: design
title: "Link owners: one registration per network, consulted by every fetcher"
description: "Why web_fetch and media_fetch redirect an Instagram link and nothing else, what live traffic shows, and a registry of link owners that every generic fetcher consults through one declared wrapper instead of a hand-wired copy per network."
tags: [nora, social, x, linkedin, instagram, web_fetch, media_fetch, routing, registry]
timestamp: 2026-09-25
status: proposed
edges:
  - {dst: 00-intent, rel: extends, provenance: asserted}
  - {dst: 10-changes, rel: depends_on, provenance: asserted}
  - {dst: ../instagram-adoption/13-shared-posts-and-reels.md, rel: generalizes, provenance: asserted}
---

# Link owners

## The question (operator, 2026-09-25)

When somebody shares an Instagram post, reel or story link, `web_fetch` and
`media_fetch` refuse it and point Nora at `instagram_read`. X and LinkedIn
have their own tools since DEC-201, but neither fetcher knows it. The
operator's words: «how exactly will this web fetch or web extract do the
interception? Does it have a universal, general, or unified interceptor or
redirector? I don't want something like C&TD again; I want this to be
reusable.» C&TD is copy-and-tweak duplication, one hand-wired copy per
network. The requirement is that **adding a network is one registration,
not an edit to every fetcher**.

This node is a design only. Nothing in it is built.

## What exists today

Every line number below was read on `origin/master` at `83a8ffe7`.

### Where a URL is classified by network

| Site | What it asks | How |
|---|---|---|
| `_instagram_page_kind`, `nora/agent/tools.py:3147` | is this pasted URL an Instagram page, a mobile Instagram page, or neither | `parse_link` after `_with_scheme` (`tools.py:3001`), then its own `m.instagram.com` check, the only one in the tree (`tools.py:3159`) |
| `media_fetch`, `tools.py:6101` | the same, before any download | calls `_instagram_page_kind` |
| `web_fetch`, `tools.py:7375` | the same, before its scheme check | calls `_instagram_page_kind` |
| `x_read`, `tools.py:7499` | is a link it refused an Instagram link | `_is_x_host` and `_parse_instagram_link`, hand-written |
| `linkedin_read`, `tools.py:7707` | is a link it refused an X or an Instagram link | `_is_linkedin_host`, `_parse_x_link`, `_parse_instagram_link`, hand-written |
| `instagram_read`, `tools.py:7991` | is this its own link | `_parse_instagram_link`; a refusal names no other network |
| `_shared_page_link`, `tools.py:3133` | which post or reel a stored share line links, for a stale cover | a regex over the rendered line (`tools.py:3128`), then `parse_link` |
| `_shared_target`, `nora/platform/instagram/parser.py:172` | what label and what link a SHARED item gets in the transcript | `link_shape(story_ring=False)` (`parser.py:204`), `parse_link`, and a story's user segment bound to the share's author |
| the host bridge, `nora/infra/social/bridge_server.py:136` | a tweet id out of a status URL, host-side | its own regex, which disagrees with `x_links` (no `mobile.` host, a 20-character user, no `/i/status/`) |
| `_target_for`, `nora/infra/music/ytdlp_source.py:283` | is a URL YouTube | an exact host set (`ytdlp_source.py:53`) |

Nothing on the ambient ingest path classifies a pasted URL. The only ingest
classification is the Instagram adapter rendering a share it was given as a
structured object (`_shared_target`); Signal, WhatsApp and Discord pass a
pasted link through as text.

### Tools that fetch an arbitrary URL

| Tool or path | Who connects | Consults a network? |
|---|---|---|
| `web_fetch(url)`, `tools.py:7356` | Tavily's servers | Instagram only |
| `media_fetch(url=...)`, `tools.py:6029` | Nora, through `_download_media` (`tools.py:1646`) | Instagram only |
| `play_music(query)`, `tools.py:8537` | yt-dlp, Nora's network | YouTube hosts only, as its own allowlist |
| the outbound link-preview card, `nora/infra/preview.py:258` | Nora, SSRF-guarded | no, and not a model tool: it previews Nora's own reply |
| `instagram_read`'s item looks, `tools.py:7905` | `media_fetch`'s closure, called directly | not the model's call: it fetches CDN files `instagram_read` returned |

`web_search` (`tools.py:7261`) takes a query, not a URL, and tells the model
to read a post with `x_read` in its docstring (`tools.py:7270`).

### The three parsers disagree about shape, and that is fine

| | `instagram_links` | `x_links` | `linkedin_links` |
|---|---|---|---|
| hosts | exact: `instagram.com`, `www.` (`instagram_links.py:56`) | `x.com`, `twitter.com`, each with `www.` or `mobile.` (`x_links.py:34`) | `linkedin.com`, `www.`, or a two-letter country subdomain (`linkedin_links.py:52`) |
| mobile host | refused; `m.instagram.com` is caught in `tools.py:3159` instead | accepted | `m.` is one letter, so refused |
| trailing segments | exact counts: `/p/<code>/c/...` is no link | anything after the post id is ignored (`/photo/1`) | anything after the id is ignored (`/recent-activity/`) |
| trailing slash | one stripped (`instagram_links.py:141`) | all stripped (`x_links.py:77`) | all stripped |
| kinds | `profile`, `post`, `reel`, `story` | `post`, `profile` | `person`, `company`, `post` |
| a kind the owner cannot read | none | none | `post`, recognised only to be refused (`linkedin_read`, `tools.py:7725`) |
| host-only predicate | none | `is_x_host` | `is_linkedin_host` |
| no scheme | refused; `_with_scheme` in the TOOL adds one | same | same |
| shorteners | none | `t.co` not recognised | `lnkd.in` not recognised |
| return | `InstagramLink(kind, key, story_pk)` or `None` | `XLink(kind, key)` or `None` | `LinkedInLink(kind, key)` or `None` |

These differences are the networks' and belong in their parsers. A
registry must not flatten them. What the three share is only the
**signature**: a string in, a typed link or `None` out, and that is enough
to register them side by side.

### What each owning tool accepts

| Tool | Link parameter | `url` alias |
|---|---|---|
| `instagram_read` | `link` | yes, since design 13 |
| `x_read` | `link` | yes, since 2026-09-25 (decision 2, below) |
| `linkedin_read` | `link` | yes, since 2026-09-25 (decision 2, below) |

Strands validates through a model built from the signature and drops any
key it does not declare, silently (design 13, «`instagram_read` accepts
`url` as `link`»). Until 2026-09-25 a redirect that said «call x_read with
url» produced a call with no input; since R-99 (#511) such a call is refused
as `bad_arguments`, and since decision 2 was taken (below) all three owners
declare `url`, through the one shared `_resolve_url_alias` in `tools.py`.

### Three hand-written cross-network matrices already exist

The C&TD the operator names is not only in the fetchers. Each owning tool
carries its own «that link belongs elsewhere» branch, and the three
disagree:

| A link to... | `instagram_read` says | `x_read` says | `linkedin_read` says |
|---|---|---|---|
| Instagram | (reads it) | «use instagram_read» | «use instagram_read» |
| X | generic refusal | (reads it) | «use x_read» |
| LinkedIn | generic refusal | generic refusal | (reads it) |

And the status differs by caller for the same meaning: the fetchers answer
`nothing_found` (`tools.py:7378`, `6104`), the owners `unavailable`
(`tools.py:7501`, `7710`).

### What is keyed on `_instagram_page_kind` and its outputs

A replacement breaks all of these unless it moves them:

- **Mutation specs embed the literal source**, so a rewrite turns them into
  silent no-ops that report «survived»: five entries in
  `scripts/mutations/instagram-shared-no-cover-handle.json` (the
  `if page == "page":` and `if page == "mobile":` guards, the
  `_instagram_page_kind(url) if url and not code` line, and
  `candidate = _with_scheme(url)`) and two in
  `scripts/mutations/instagram-shared-post-short-caption.json` (the
  `page = _instagram_page_kind(url)` line in `web_fetch`).
- **Tests**: `tests/test_web_fetch_tool.py` (the page matrix at line 158, the
  guard with Instagram off and in a fire turn, the mobile note, and the
  `nothing_found/instagram_page` rows of its every-status-has-a-note table at
  line 254), `tests/test_media_fetch.py` (from line 274, including the CDN
  negative control), `tests/unit/test_instagram_read_links.py`.
- **Notes pinned by name**: `tests/unit/test_social_tool_notes.py:123-128`
  imports `_INSTAGRAM_PAGE_LINK`, `_INSTAGRAM_MOBILE_PAGE_LINK`,
  `_WEB_FETCH_INSTAGRAM_PAGE` and `_WEB_FETCH_INSTAGRAM_MOBILE_PAGE` from
  `nora.agent.tools` for the no-dash rule.
- **The status-note gate** scans only the modules listed in
  `tests/architecture/test_every_status_carries_a_note.py` (`RESULT_MODULES`);
  a redirect built in a new module is invisible to it until listed.
- **Docs**: design 13 names the helper as the one both fetchers use.

Two notes for the one Instagram page already differ on purpose:
`media_fetch`'s adds «then look at the media it returns» (`tools.py:3093`),
because the model came to `media_fetch` to see something. A single redirect
shape must keep room for that tail, or the unification removes it.

## What live traffic shows

Read-only, on norty: Langfuse `TOOL` observations and the `messages` table,
counts only.

**Langfuse, 2026-09-11 08:43 to 2026-09-24 19:17 UTC (305 engagement traces):**

| Tool | Calls | X | LinkedIn host | Instagram page | Other or no url |
|---|---|---|---|---|---|
| `web_fetch` | 25 | 0 | 1 (`lnkd.in`) | 1 | 23 |
| `media_fetch` | 24 | 0 | 0 | 0 | 6 CDN urls, 18 by code |

- The one `lnkd.in` link reached `web_fetch`, came back `ok` with a page
  that is not on LinkedIn at all, and she replied from it. **A shortener is
  not its network's page**: had `lnkd.in` been registered to
  `linkedin_read`, that turn would have broken.
- The one Instagram page link was redirected, and she followed it: 1 of 1,
  through `load_tools`, then `instagram_read`, then `media_fetch` on the
  returned CDN urls.
- All six `media_fetch` urls were Instagram CDN files that `instagram_read`
  returned. A registry that claimed a CDN host would break every item look.
- The owning tools over the same window: `instagram_read` 6, `x_read` 1,
  `linkedin_read` 0. `instagram_read` was called with `url` twice, both
  accepted by the alias. `media_fetch` was called twice with `file=`, a key
  it does not declare, and both arrived empty: the silent drop is live.

**`messages`, 2026-09-11 03:42 to 2026-09-24 19:17 UTC (2,021 rows):** a
person posted 1 X status link, 1 `linkedin.com` link, 3 `lnkd.in` links, 19
Instagram page links (18 of them shares the Instagram adapter rendered) and
21 YouTube links. **Since the first stored row (2026-08-05):** 15 X status
links, 1 LinkedIn person or company link, 0 LinkedIn post links, 23
Instagram post or reel links, and 0 Instagram post links of the
user-prefixed form (`/<user>/p/<code>/`), which the Instagram parser would
refuse.

**Langfuse, the whole retention (`web_fetch` 2026-08-07 13:10 to 2026-09-24
16:51 UTC):**

| Tool | Calls | X | `lnkd.in` | `linkedin.com`, `t.co` | Instagram page | Other |
|---|---|---|---|---|---|---|
| `web_fetch` | 164 | 7 | 1 | 0 | 1 | 155 |
| `media_fetch` (08-07 to 09-24) | 77 | 0 | 0 | 0 | 0 | 71 by code, 6 Instagram CDN |
| `social_fetch`, retired (08-30 to 09-22) | 14 | 5 | | | | 9 by handle |

- **All 7 X links in `web_fetch` came on one day (2026-08-30), across 4
  turns, and none was usable**: 4 came back as a login wall, 1 as an
  error, 2 as thin pages. None pointed anywhere. In 2 of the 4 turns she
  searched and then found `social_fetch` on her own; in the other 2 she
  never reached the owner. That was before `x_read` existed, so the owner
  then was `social_fetch`; the shape of the failure is the one the
  operator describes.
- Nothing LinkedIn-hosted reached a fetcher in the retention, apart from
  the one `lnkd.in` link above.

**Reading.** The premise holds for X: a pasted X link sent to `web_fetch`
came back unusable 7 times out of 7, and half the turns never found the
tool that could read it. It is not observed for LinkedIn (1 link in seven
weeks, never fetched). The rate is low (one burst in seven weeks), so the
case for the registry is mostly structural: one registration instead of N
copies, and three owners that disagree today. The X row earns its place on
the 7 of 7; the LinkedIn row rides on the same mechanism at no extra cost,
and both get an eval case before they ship (§"Migration", PR 2).

## The published convention

| Pattern | What it is | Fit |
|---|---|---|
| yt-dlp extractors | each extractor declares `_VALID_URL` and `suitable()`; an ordered list, first match wins, `GenericIE` last | **the closest fit**: our parsers already are `suitable()`, and the generic fetcher is the fallback |
| oEmbed `providers.json` | a provider registers URL `schemes` (wildcards) and an endpoint | the right idea (register a provider once, consumers look it up), but wildcards would replace parsers that already validate charsets, which is a loss |
| Django URLconf, werkzeug `Map`/`Rule` | ordered or specificity-sorted rules with converters | host dispatch is trivial next to what these solve; the idea to keep is «first match wins, order is explicit» |
| LangChain loaders, LlamaHub readers | the developer picks a loader class; no runtime URL dispatch | no precedent |
| Anthropic «Writing tools for agents», OpenAI function-calling guide | namespace overlapping tools; return an error the model can act on | this is the redirect note's job, and it is guidance, not a registry |
| MCP tool-name collisions | overlapping tools shadow each other; the fix is naming | same lesson: one owner per capability |
| Strands `BeforeToolCallEvent` | a hook that sees `tool_use` and can set `cancel_tool` or swap `selected_tool` | a real interception point; weighed below, and rejected for this job |

So the pattern is known and cheap: **an ordered registry of recognisers, each
bound to one owner, with the generic fetcher as the fallback**. What no
published pattern covers is where the lookup runs in an agent that has
its own result contract, which is the question the next section answers.

## Design

### Placement

- **The parsers stay in `nora.domain`**, one module per network, unchanged.
  They are shared by the Instagram adapter (`nora.platform`) and the tools
  (`nora.agent`), and `nora.domain` is framework-free by contract
  (`pyproject.toml:173`).
- **The registry is new, in `nora.agent`**, as `nora.agent.link_owners`. It
  names TOOLS and their parameters, which is agent vocabulary; the domain
  must not know that a tool called `x_read` exists.
- **The layers contract keeps the transcript out of it for free.**
  `"nora.agent | nora.platform"` (`pyproject.toml:252`) is an independent
  pair in import-linter, so the Instagram adapter cannot import the
  registry. The share render and the fetch routing share the PARSER, which
  is the real single source, and cannot share the owner table. That is
  the intended split (§"What the registry does not do").

### Types

**Shipped shape (PR 1), superseding the sketch this section originally
carried.** The note is composed from TWO independent slot sets rather than a
`hint`/`status=`/`then=` triple passed into `redirect` at the call site —
the owner's half (what the link is, who reads it, and, only the network
knows this, why an unreadable one can't be) and the fetcher's half (what
kind of tool is asking):

```python
# nora.agent.link_owners

@dataclass(frozen=True)
class LinkClaim:
    network: str               # "instagram" | "x" | "linkedin"
    kind: str                  # the parser's kind, or "mobile_page"
    readable: bool             # can the owner read this kind?
    # The network's OWN explanation, required when readable=False
    # (LinkClaim.__post_init__ enforces this — ADR 0015 §3: no non-ok status
    # ships with an empty note). A str.format template with exactly two
    # fetcher-filled slots, {not_clause} and {verb}; a template naming any
    # other slot fails at LinkClaim construction, not at redirect()'s own
    # call.
    unreadable_template: str = ""

@dataclass(frozen=True)
class LinkOwner:
    network: str
    tool: str                  # "x_read"
    param: str                 # "link": the parameter the note names
    label: str                 # "an X link": how the note names it
    claim: Callable[[str], LinkClaim | None]   # the network's recogniser

LINK_OWNERS: tuple[LinkOwner, ...] = (
    LinkOwner("instagram", "instagram_read", "link", "an Instagram page", _instagram_claim),
    LinkOwner("x", "x_read", "link", "an X link", _x_claim),
    LinkOwner("linkedin", "linkedin_read", "link", "a LinkedIn link", _linkedin_claim),
)

@dataclass(frozen=True)
class LinkInput:
    # Fetcher-generic only: no network name, no network text. NativeTool.
    # link_inputs is a tuple of these.
    param: str
    unless: str = ""       # another parameter of the same tool that means
                            # "don't check this one" (media_fetch's "code")
    not_clause: str = ""   # e.g. ", not a picture or video file"
    then: str = ""         # e.g. ", then look at the media it returns"
    verb: str = ""         # e.g. "read" / "opened"

def owner_of(text: str) -> tuple[LinkOwner, LinkClaim] | None: ...
def redirect(owner: LinkOwner, claim: LinkClaim, link_input: LinkInput) -> dict: ...
```

- **A recogniser claims a PAGE, never a host.** Each wraps its network's
  `parse_link` after `_with_scheme`. Instagram's also claims the
  `m.instagram.com` form as `readable=False`, with its own
  `unreadable_template` asking for the www.instagram.com link — what
  `_instagram_page_kind` does today. X's needs no such shape: `x_links.
  X_HOSTS` already accepts every mobile/alternate spelling
  (`mobile.x.com`, `mobile.twitter.com`, bare `x.com`/`twitter.com`) as
  fully readable, so `_x_claim` is a plain wrap with every claim
  `readable=True`.
  **Shipped, PR 2.** LinkedIn's claims `person` and `company` as
  `readable=True`, and — the same shape as Instagram's mobile host —
  `m.linkedin.com` as `readable=False`: a real `linkedin.com` subdomain
  the parser's own host charset refuses on purpose (`_host_ok`'s country
  code is exactly two letters, and `m` is one), judged on the www spelling
  of the same path, with its own `unreadable_template` asking for the
  `www.linkedin.com` link. A LinkedIn `post` is **not claimed at all** (on
  `linkedin.com`, `www.linkedin.com` or `m.linkedin.com` alike), because
  `linkedin_read` cannot read one and a public LinkedIn post page is
  something `web_fetch` may be able to read. Refusing it in `web_fetch`
  would route her to a tool that says «I can't».
- **Shorteners and CDN hosts are never claimed** (`t.co`, `lnkd.in`,
  `*.cdninstagram.com`, `*.fbcdn.net`, `pbs.twimg.com`, `*.twimg.com`,
  `media.licdn.com`). The live `lnkd.in` turn and the six CDN reads are the
  evidence. The matrix test carries each as a negative row.
- **Every other LinkedIn page shape stays unclaimed, PR 2's own scope
  reading.** `/posts/`, `/feed/`, `/pulse/` are the parser's own `post`
  kind (never claimed, above); `/school/`, `/showcase/` and `/jobs/` are
  real LinkedIn page types the parser does not recognise at all (`parse_link`
  returns `None`: no `in`/`company`/post-first-segment matches), so they
  reach `web_fetch` unclaimed, same as any page no owner can read — adding
  them is a parser change, not a registry one, the day `linkedin_read`
  grows a page-type it can read for them. Only `m.linkedin.com` gets the
  special unreadable treatment; a non-canonical subdomain the parser
  doesn't otherwise special-case (a historical mobile alias such as
  `touch.www.linkedin.com`) is simply unclaimed and reaches `web_fetch` as
  an ordinary page, exactly like `x.com.evil.example` — claiming every
  lookalike or legacy subdomain by name is the «claim a whole host»
  anti-pattern this node's non-goals reject.
- **Order is explicit and the claims are disjoint.** The hosts do not
  overlap, and a test asserts that no URL in the matrix is claimed by two
  owners, so «first match wins» never has to decide anything.
- **ONE result shape**: `{"status": "nothing_found", "note": <note>}`, built
  only by `redirect`, status fixed (open decision 1, accepted). The note is
  composed, never a fetcher-held literal: the owner's sentence from `label`,
  `tool` and `param` («That link is an X link. Open it with x_read using
  this link.») plus the fetcher's `not_clause`/`then` when the claim is
  readable, or the claim's own `unreadable_template` (filled with the
  fetcher's `not_clause`/`verb`) when it is not. Adding a network is one
  `LinkOwner` row (plus, if it has an unreadable shape, one
  `unreadable_template`); adding or changing a fetcher's own wording is one
  `LinkInput` — neither ever touches the other. The link is never echoed, as
  today (`tests/test_web_fetch_tool.py:183`).

### Where the lookup runs

Three places were weighed.

**A. A helper called at the top of each fetcher.** What exists today for
Instagram, with the helper made generic. Keeps the R-27 result, the counter
and the tests. Still one call site per fetcher, and a new fetcher that
forgets the call is silently unrouted. This is the C&TD the operator named,
one level smaller.

**B. A Strands `BeforeToolCallEvent` hook.** One place, and it sees the raw
`tool_use` input. Rejected, for reasons in this repo rather than in general:

- `cancel_tool` produces an **error** `ToolResult`, not a tool result
  (`nora/agent/guards.py:1363-1367`, pinned against the SDK). The R-27
  status, the output-contract check in `adapt()`
  (`nora/agent/ports.py:248`) and the `_record` counter
  (`tools.py:1774`) all run inside the tool, so a hook's redirect would
  be the only non-ok answer in the catalogue with none of the three.
- Hooks are registered **per Agent**, in two constructors
  (`nora/agent/factory.py:447` and `531`). A fetcher built into an agent
  whose hook list lacks the router is silently unrouted, and «which agent»
  is a composition fact, not a property of the tool.
- Swapping `selected_tool` to the owner rewrites a call after
  `ReplyGuard._before_tool` reserved budget under the fetcher's name
  (`guards.py:1318`). A release under the owner's name is the name-keyed
  leak `guards.py` documents from 2026-08-21.
- Every routing test today calls the tool as built (`spec.call`, the
  `_tools` harness in `tests/test_web_fetch_tool.py:79`). A hook is
  invisible to all of them, so they would pass with the router missing.

**C. A declared wrapper, applied once where tools are built. Chosen.**
`NativeTool` (`tools.py:201`) gains one field, `link_inputs`: the
parameters of this tool that carry a URL another tool may own, each a
`LinkInput` of fetcher-generic slots only (no redirect status here — it is
fixed, see above):

```python
"web_fetch": NativeTool(2, ..., link_inputs=(LinkInput("url", verb="opened"),)),
"media_fetch": NativeTool(4, ..., link_inputs=(
    LinkInput(
        "url", unless="code", not_clause=", not a picture or video file",
        then=", then look at the media it returns", verb="read",
    ),
)),
```

`build_tool_specs` applies ONE post-pass to the tuple it returns
(`tools.py:10717` onward): every spec whose declaration has `link_inputs`
gets its `call` wrapped. The wrapper reads the named arguments, asks
`owner_of`, and on a claim returns `redirect(...)` without calling the
tool, counted through the tool's own recorder (`_record`, or
`_record_media` for `media_fetch`) with `reason="link_owner"`, the owner's
network, and `claim.kind` (so a page and a mobile-page redirect can still
be told apart in the log). `functools.wraps` keeps the signature Strands
derives the schema from, the same way `_validated` does (`ports.py:277`). This is the
precedent this repo already has for «one seam every native tool passes
through, driven by a declaration»: `output_contract` on the same table,
applied in `adapt()`.

Why C over A and B: the redirect is still a tool result under R-27, still
counted, still visible to every existing test through `spec.call`, and
independent of which Agent is built. Adding a fetcher is one field on its
row; adding a network is one `LinkOwner` row. Neither edits a fetcher
body.

Two consequences, stated:

- **The redirect now runs before everything in the fetcher.** In
  `media_fetch` it runs before the question screen (`tools.py:6079`)
  instead of after. Nothing leaves in either order, so the only visible
  change is that an owned link with an identifier in the `question` gets
  the route rather than the identifier refusal.
- **Internal calls are not routed**, on purpose. `instagram_read`'s item
  looks call the `media_fetch` closure directly (`tools.py:7909`), not the
  spec, and they fetch CDN files.

### The owners' «not mine» branch

`x_read`, `linkedin_read` and `instagram_read` replace their hand-written
cross-network branches with `owner_of(text)`: a claim by another owner
returns `redirect(...)`; anything else keeps its own refusal. The three
disagreeing matrices become one, and `instagram_read` starts naming the
right tool for an X or LinkedIn link, and `x_read` for a LinkedIn one.

### What the registry does not do

- **The transcript share render stays where it is.** `_shared_target`
  answers a different question («what label does this shared item get,
  and may its link be shown») from a different input (a structured share
  from the bridge, not pasted text), with three rules the fetch routing
  must not inherit: the two-segment story ring is not a story there
  (`story_ring=False`), the label survives a rejected link, and a story's
  user segment must equal the share's author. Unifying them would hide
  those differences behind one call. The render is gated by its own tests
  (`tests/unit/test_instagram_parser.py`), and the layers contract keeps
  the platform from importing the owner table at all.
- **`_shared_page_link`** reads a line Nora rendered, not a URL a person
  pasted. It stays on the Instagram parser.
- **The host bridge's own tweet regex stays separate.** The bridge
  re-validates host-side because it trusts nothing about its caller; the
  tools send it an id, never a URL. Its disagreement with `x_links` is
  recorded here, not fixed.

### Tests and enforcement

| Property | Test |
|---|---|
| Each URL in one table lands on the right owner or on no owner: every Instagram kind, the mobile form, X post, profile and the `mobile.`/`twitter.com` forms, LinkedIn person, company and country subdomain; negatives for a LinkedIn post, `t.co`, `lnkd.in`, both Instagram CDN suffixes, `pbs.twimg.com`, a lookalike host (`x.com.evil.example`), YouTube, and a plain page | new, the redirect matrix, parametrized over the table in `test_link_owners.py` |
| The same table through each fetcher as built (`spec.call`): claimed means the redirect and no provider or download call; unclaimed reaches the fake | new, parametrized over every tool with `link_inputs`, so a new fetcher is covered by declaring itself |
| Every `LinkOwner` names a tool that is built and a parameter in that tool's signature | new: build the specs, `inspect.signature(spec.call)`, assert `owner.param` is a parameter. Renaming `x_read`'s `link` turns it red |
| No URL is claimed by two owners | new, over the matrix |
| Every tool whose signature has a parameter named `url` or `link` is either an owner or declares it in `link_inputs` | new architecture test over the built specs |
| No function in `nora/agent/tools.py` calls a network parser or host predicate outside the registry, except an owner's own read of its own network and the two Instagram share helpers | new architecture test, AST over the call sites, with an allowlist that only shrinks (the shape of `tests/architecture/test_tool_builders_forward_everything.py`) |
| No redirect note carries a dash; every redirect has a note | `tests/unit/test_social_tool_notes.py` re-pointed at `redirect`'s outputs; the registry module added to `RESULT_MODULES` |
| The wrapper is load-bearing | a mutation spec for the registry: drop the post-pass, drop one `LINK_OWNERS` row, claim a whole host instead of a page; each names the test it reddens |

### Migration

Each PR names the test that goes red when it is reverted.

1. **The registry, with Instagram only, behaviour identical.**
   `nora.agent.link_owners` with the Instagram row; `NativeTool.link_inputs`;
   the post-pass in `build_tool_specs`; `web_fetch` and `media_fetch`
   declare their inputs and lose their inline guards; `_instagram_page_kind`
   and the four note constants move into the registry. Both notes keep
   their current text and status (`nothing_found`). The seven mutation
   entries that embed the old lines are re-pointed at the post-pass and the
   Instagram recogniser in the same commit, and `make mutate` is run on
   both files. **Falsification**: removing the post-pass line turns
   `tests/test_web_fetch_tool.py -k instagram` and
   `tests/test_media_fetch.py -k "instagram_page_link or mobile_page_link"`
   red; they are the tests that pin today's behaviour, unchanged. The new
   signature test and the parameter-declaration architecture test land here
   too.
2. **X and LinkedIn register.** Two rows. This is the behaviour change: a
   pasted X post or profile link, or a LinkedIn person or company link,
   is redirected by both fetchers. One eval case per network under
   `nora-eval` (a pasted link and «what does this say»; expected first
   owner call `x_read` / `linkedin_read` with `link`), because a result
   note is routing text. **Falsification**: deleting either row reddens its
   matrix rows in the fetcher parametrization.
3. **The owners use the registry.** `x_read`, `linkedin_read` and
   `instagram_read` replace their cross-network branches. **Falsification**:
   a three-by-three test (each owner handed each other network's link)
   reddens when any owner falls back to its own hand branch; the AST
   allowlist shrinks by the `x_read` and `linkedin_read` entries.

Each lands with a DEC row taken at merge time (`nora-decision`), and PR 1
cites this node.

## Open decisions for the operator

1. **One redirect status.** Today the fetchers say `nothing_found` and the
   owners say `unavailable` for the same meaning. **Recommendation:
   `nothing_found` everywhere**, because it is the only one with live
   evidence (1 of 1 followed in production; design 13's replay) and it is
   what the fetchers already return. It changes the owners' cross-network
   answer only, which had no call in the window. Reverse if an eval shows
   the model telling the person «there's nothing there» after a redirect.
2. **Does `x_read` and `linkedin_read` get `instagram_read`'s `url`
   alias?** Design 13 found every deferred-catalogue call to
   `instagram_read` used `url`. **Recommendation: no, not yet.** The
   redirect note names `link`, the one live `x_read` call used `link`, and
   the alias costs about 22 tokens per tool on every turn. Add it the day a
   live `x_read` or `linkedin_read` call is seen arriving empty with `url`.
3. **Redirect, or rewrite the call to the owner?** A rewrite saves a model
   round trip (in the one live redirect, `load_tools` plus the owner call).
   **Recommendation: redirect.** A rewrite has the model call `web_fetch`
   and receive `x_read`'s answer, runs under the wrong budget name, and
   skips the owner's own «only when asked» check unless it is duplicated
   into the router. At eight fetches of an owned link in seven weeks
   (7 X, 1 Instagram, 0 LinkedIn), the round trip costs nothing
   measurable.

**2026-09-25, operator: all three accepted as recommended.** `nothing_found`
is the one redirect status; `x_read`/`linkedin_read` get no `url` alias yet,
and decision 2 does not schedule one — it stays "not yet" until a live
`x_read`/`linkedin_read` call is seen arriving empty with `url`; a claimed
link redirects, it is never rewritten to the owner's call. PR 1 (this
commit) builds only the Instagram row under decision 1 — the owners'
cross-network branches (decision 1's other half) are PR 3's work, not built
here.

**2026-09-25, decision 2 taken, by the operator, on eval evidence.** The
accepted text above waits for a LIVE call arriving empty with `url`. That
never happened, and since R-99 (#511) it cannot: an undeclared `url` is
refused as `bad_arguments` and she retries with `link`. What was seen
instead: on the deployed build (#512, 91abdf9b), `python -m evals
tool_selection` over the four X/LinkedIn routing cases at n=5 drew 20
turns, and 7 of them called the owner with `url=` (5 `x_read`, 2
`linkedin_read`), each costing a model round trip before the retry. The
operator, shown those numbers, ruled the question no longer open and the
alias added. The cost decision 2 weighed stands and is accepted: about 22
tokens per tool on every turn the schema is loaded. `x_read` and
`linkedin_read` now declare `url` through the one `_resolve_url_alias` all
three read tools share (`instagram_read`'s own inline copy is gone): `url`
alone reads as `link`, two spellings of one link agree by the network's
parser, and two different links read nothing.

## Non-goals

- **Resolving shorteners.** Following `t.co` or `lnkd.in` is a request to
  a third party before any tool runs; the one live `lnkd.in` link was not
  LinkedIn. A shortener goes to `web_fetch` as it does today.
- **Claiming a whole host.** An X path the parser refuses (`/explore`,
  `/search`) still goes to `web_fetch`, and on the 2026-08-30 evidence it
  will likely come back as a login wall. That is accepted: no owner can
  read those pages either, so a redirect would send her to a tool that
  refuses. Which paths the 7 measured X links had was not read.
- **Annotating links in the transcript with their owner** so the first call
  is right. That is a change to the model's input and needs its own eval;
  at eight fetches of an owned link in seven weeks it would add tokens to
  every turn to save about one call a week.
- **`play_music`.** It takes a URL in `query` and keeps its own YouTube
  allowlist; YouTube has no single owner (play it, or read about it), so
  it is not registered.
- **Threads, TikTok, Facebook.** No owning tool exists. Each is one row
  the day one does.
- **The user-prefixed Instagram post path** (`/<user>/p/<code>/`). The
  parser refuses it; 0 of 23 stored Instagram post links used it. Reopen
  the first time one is stored.

## Reopens if

- A LinkedIn person or company link reaches `web_fetch` and comes back
  READABLE: then LinkedIn's row is refusing something the fetcher could
  have answered, and it should narrow.
- A redirect is followed by a reply that says the link is empty or broken
  (open decision 1).
- A new fetcher lands without `link_inputs` and the parameter-name test
  did not catch it: the test's name rule is then too narrow.
