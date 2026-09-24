---
type: design
title: "Changes: the four tools, the bridge rows, and the order social_fetch leaves in"
description: "Each tool's signature and rules, what the router and the host bridge learn, the two defects the LinkedIn rows would otherwise inherit from the bridge's X-shaped code, and the two PRs that land it."
tags: [nora, social, x, linkedin, social_fetch, bridge, router, opencli]
timestamp: 2026-09-24
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
---

# Changes

Everything here follows `../instagram-adoption/18-instagram-read-and-search.md`
unless it says otherwise: the ADR 0015 Level 3 description rules, the L1
opening paragraph gate, a `NativeTool` entry with every field, results in
R-27's states with a `note` on every non-`ok`, content wrapped as untrusted
and clamped, and no identifier on the wire (R-18).

## `x_read`

```
x_read(what: Literal["profile", "posts"] | None = None,
       handle: str | None = None, link: str | None = None,
       limit: int = 5)
```

- **Exactly one of `handle`, `link`.** None, both, or a blank one is
  `unavailable` and nothing is sent, as in `instagram_read`.
- **A link decides the read.** `x.com` or `twitter.com` (with or without
  `www.` or `mobile.`, with or without a scheme):
  - `/<user>/status/<id>` reads that post and its thread (router row
    `x/post`, target the numeric id). Any `what` beside it is
    `unavailable`, «the link already says what to read».
  - `/<user>` reads the profile, or the recent posts when `what="posts"`.
  - Any other path (`/i/...`, `/search`, `/home`, `/explore`, `/hashtag/...`,
    `/settings`, `/messages`, `/notifications`) is `unavailable`: it names no
    account or post.
  - A link to another network is `unavailable` with a note naming the tool
    that reads it (`instagram_read`, `linkedin_read`), never a web read.
- **A handle needs `what`.** One leading `@` is stripped; the rest must match
  the existing username charset (`[A-Za-z0-9_]{1,15}` is X's own rule; the
  shared `[A-Za-z0-9_.]{1,30}` stays the host-side check).
- `limit` applies to `posts` only (1 to 20, default 5), as `social_fetch`'s
  did. With a profile or a post it is ignored, not an error: a model that
  passes the default everywhere should not be refused for it.
- The link parser is a new pure `x_links` module in `nora/domain/`, beside
  `instagram_links.py`. `_social_tweet_id` moves into it; the host bridge
  keeps its own copy (it trusts nothing about its caller).

## `x_search`

```
x_search(query: str, limit: int = 5)
```

Free text, at most 200 characters, `limit` 1 to 20. Router row `x/search`,
`named`, sent untouched.

## What both X tools keep from `social_fetch`, unchanged

The egress screen (`scan_identifiers` on the percent-decoded input) and the
raw-coordinate screen run before anything leaves; both are `nothing_found`
and never echo the value. `read_only=True`, `source_tier="web"`,
`cacheable=True`, `output_contract="three_state"`, `level=3`. Budget: `x_read`
3 (a post, then the author's profile or posts), `x_search` 2. Both stay in
`UNIT_SAFE_TOOLS` (`nora/agent/dreamer.py`), replacing `social_fetch`'s entry;
no adaptor declares either today, and this does not change that.

## `linkedin_read`

```
linkedin_read(what: Literal["profile", "experience", "posts", "company"] | None = None,
              handle: str | None = None, link: str | None = None,
              limit: int = 5)
```

- **Exactly one of `handle`, `link`**, as above.
- **A link decides what it can.** `linkedin.com` (any `www.` or country
  subdomain):
  - `/in/<id>/` names a person. `what` picks `profile` (default when `what`
    is left out), `experience` or `posts`. `company` with a person's link is
    `unavailable`.
  - `/company/<slug>/` reads the company page. Any `what` other than
    `company` or none is `unavailable`.
  - `/posts/...`, `/feed/update/...`, `/pulse/...` are `unavailable` with a
    note that one post cannot be read from its link (00 §"What LinkedIn can
    do today"), so she says so instead of reading the author's whole
    activity.
  - Anything else, including `/jobs/`, `/school/`, `/in/` with a
    percent-encoded non-Latin id, is `unavailable`.
- **A handle is a person's public id** (the `<id>` in `/in/<id>/`), and needs
  `what` of `profile`, `experience` or `posts`. A company is read from its
  link only: a company slug and a person id are the same shape of string.
- `limit` applies to `posts` only (1 to 20, default 5).
- The parser is a new pure `linkedin_links` module in `nora/domain/`, beside the other two.
- **The asked-turn rule** (00 §"LinkedIn reads are visible to the person
  read"): in a fire turn or a Dreamer pass, every call is `refused` before
  the link is parsed or anything is sent. The rule lives in the tool, which
  holds the turn's provenance, exactly as `instagram_read`'s story rule
  does. `linkedin_read` is NOT in `UNIT_SAFE_TOOLS`.
- Budget 2 (a profile, then its experience or posts).

## `linkedin_search`

```
linkedin_search(query: str, limit: int = 5)
```

- Maps to OpenCLI `people-search`, never to `linkedin search` (jobs).
- `limit` 1 to 10 (OpenCLI's range), default 5.
- Every result carries its profile link, and the description says the list
  can hold several people with the same name and headline and that she must
  not pick one: she shows them, or asks which. Measured: two of three results
  for a public figure's name were impostors.
- **Budget 1 per turn**, and the asked-turn rule above. It spends LinkedIn's
  monthly people-search limit on a dedicated account; one search a turn is
  the ordinary shape, and a second is a refinement she can ask the room for.
- **The monthly limit is not counted by Nora.** What OpenCLI returns once
  LinkedIn's limit is reached is UNMEASURED. Until it is read back, any
  non-zero exit is `unavailable` like every other failure. When it is
  measured, it gets its own stable note («LinkedIn's monthly search limit is
  used up; a profile link still works») so she does not retry. A local
  counter is added only if usage shows the limit being reached; the metric
  below is how that would be seen.
- Not in `UNIT_SAFE_TOOLS`.

## Both LinkedIn tools keep the X tools' screens

The egress screen (`scan_identifiers` on the percent-decoded handle, link or
query) and the raw-coordinate screen run in both LinkedIn tools before
anything leaves, exactly as in the X tools and `instagram_search`, with the
same `nothing_found` and no echo. It matters most in `linkedin_search`, whose
free text goes to a PEOPLE search: a phone number pasted into it would be
looked up against real people.

## What `SocialRouter` learns

`SUBJECT_INPUTS` gains five rows, all `named`:
`linkedin/{profile, experience, posts, company, search}`. The four X rows are
unchanged. `EXCEPTION_ROUTES` and `TIER_ZERO_READS` are unchanged: both
networks resolve to `default` (OpenCLI) and neither has a reach check.

The target handed to the router is never a URL: a LinkedIn person id, a
company slug, or the query, as X gets a username or a post id.

`social_read_vocabulary()` (`nora/agent/tools.py`) drops `social_fetch` and
gains `x_read`, `x_search`, `linkedin_read`, `linkedin_search`; the R-86 test
derives the three layers from it as now, and the per-tool sets stay disjoint.

## What the host bridge learns

`nora/infra/social/bridge_server.py`:

| `(platform, what)` | OpenCLI `(site, command)` | argument |
|---|---|---|
| `linkedin/profile` | `linkedin profile-read` | `--profile-url https://www.linkedin.com/in/<id>/` |
| `linkedin/experience` | `linkedin profile-experience` | the same |
| `linkedin/posts` | `linkedin posts` | the same, plus `--limit` |
| `linkedin/company` | `linkedin company` | `<slug>` positional |
| `linkedin/search` | `linkedin people-search` | `<keywords>` positional, plus `--limit` (1 to 10) |

Two defects the LinkedIn rows would inherit if added to today's code as is:

1. **`_validate_target` falls through to «a query».** Its last branch accepts
   any 1 to 200 character string for any command it does not name, so
   `linkedin posts` or `company` would be validated as free text. Validation
   becomes a TOTAL table keyed on `(site, command)`, with an unknown pair
   refused before spawning, and a test that every allowlist value has a
   validator. The LinkedIn validators: person id `[A-Za-z0-9-]{3,100}`,
   company slug `[A-Za-z0-9-]{1,100}`, keywords 1 to 200 characters with no
   control characters.
2. **Three LinkedIn commands default to the logged-in account.**
   `profile-read`, `profile-experience` and `posts` read `/in/me/` when
   `--profile-url` is absent. An argv built without the flag would answer
   with Nora's own account and look like a successful read. The argv builder
   for these commands requires the validated id, and a test asserts no
   LinkedIn profile argv is built without `--profile-url`.

Also in the bridge: the argv shape moves into a frozen table beside the
allowlist (positional versus `--profile-url`), still filled only from the
allowlist's values; `_LIMIT_COMMANDS` gains `linkedin posts` and
`linkedin people-search`; `_source_url` names the profile or company URL
(none for a search); and `OPENCLI_WRITE_VERBS` gains `connect`, `safe-send`
and `salesnav-message` (`login` is already on it).

## What the result renderer learns

`bridge_client._render_item` reads X's keys and `bio`. LinkedIn's fields
(`headline`, `about`, `featured`, `body`, `posted_at`, `reactions`,
`comments`, `title`, `company`, `date_range`, `industry`, `size`,
`headquarters`, `profile_url`) would be dropped or rendered as a bare name.
It gains the LinkedIn keys, and:

- `raw_text` is never rendered: it repeats every field unbounded and is the
  one field most likely to carry text from outside the item.
- `profile_url` renders like `url`, because `linkedin_search`'s whole answer
  is which profile link to read next.
- LinkedIn returns `media_urls` as a string, not a list; the renderer
  accepts both, so an image renders as `[image https://media.licdn.com/...]`
  for `media_fetch(url=...)`. Whether `media.licdn.com` serves those URLs
  without LinkedIn's cookies is UNMEASURED; `media_fetch` answering
  `nothing_found` on them is the failure it would show.
- `impressions` is dropped when zero: OpenCLI reports 0 for posts that are not
  the viewer's own.

## Config

- `[social] enabled` stays the flag for the OpenCLI backend, and so for X.
- `[social.linkedin] enabled` (default `false`) is new. It follows the
  pattern every backend flag here follows (DEC-151, `[social.instagram]`):
  when it is false the tools stay **registered and inert**, and answer one
  stable `unavailable` («LinkedIn isn't set up») without sending anything.
  The router has one `default` backend for every non-Instagram row, so the
  gate is not in the router: the composition root hands the two LinkedIn
  tools the router only when the flag is on, and `None` otherwise, the way
  `social_fetch` is handed `None` when `[social]` is off. `[social.linkedin]`
  without `[social]` is a config error at boot, since there would be no
  OpenCLI backend behind it.
- Not registering them when off was the first draft (audit, 2026-09-24) and
  is not done. Nothing in Nora admits a tool by config: `ToolScope.admits`
  (`nora/agent/surface.py`) filters by platform, Dreamer and grant only,
  `build_tool_specs` builds every tool, and the declared table
  (`platform=None`) admits everything. Adding config-keyed admission is a
  separate decision, and it saves nothing in production, where LinkedIn is
  meant to be on. The cost when off is the two schemas, measured in PR 2.
- `config.example.toml` documents it and says private profile viewing is a
  precondition.

## The order

**PR 1: X, and `social_fetch` is deleted.** After #444 (ADR 0015 rewrite of
every tool) merges, because both change the same descriptions in
`nora/agent/tools.py`.

- `x_read`, `x_search`, a new `x_links` module in `nora/domain/`.
- `social_fetch` is removed in the same PR. All four of its rows have a
  replacement in it, so no read goes dark and none has two tools (18's
  per-verb cutover, applied to the last network).
- Everything that names it: `nora/agent/dreamer.py` (`UNIT_SAFE_TOOLS`),
  `nora/agent/tools.py` (the `NativeTool` entry, the vocabulary table, the
  Instagram redirect constant, `media_fetch`'s docstring), `nora/config.py`,
  `config.example.toml`, the registry, egress, guards and composition
  comments, `nora/infra/social/*` docstrings, `deploy/social-bridge/README.md`,
  `docs/runbook.md`, `ARCHITECTURE.md`, `README.md`, and the specimen line in
  `nora/dreamer/objectives/standing-debrief.md` (a prompt that names a tool
  she no longer has is a prompt that tells her to call it). Comments outside
  `nora/infra/social/` that name it too: `nora/domain/ports.py`,
  `nora/platform/instagram/parser.py`, `nora/infra/vision/preprocess.py`.
  PR 1 ends with `grep -rn social_fetch nora/` returning only
  decision-history comments.
- Tests: `tests/test_social_fetch_tool.py` becomes per-tool tests under
  `tests/unit/` (the tests root takes no new files), and the eval corpus rows
  `route_social_fetch_x_link` and `route_social_fetch_x_search` become
  `route_x_read_link` and `route_x_search`. Also, each of which goes red
  otherwise (audit, 2026-09-24):
  - `tests/architecture/test_tool_names.py`: `social_fetch` is in the
    digest-pinned ADR 0015 snapshot, so it goes into `_RENAMED_SINCE`; the
    snapshot itself is never edited.
  - `tests/unit/test_social_tool_notes.py` calls `social_fetch` directly
    (its `_TOOLS` tuple and six calls); the X calls move to the new tools.
  - `tests/test_tools.py`: two exact-set assertions list `social_fetch`.
  - `tests/architecture/test_port_fakes_live_in_nora_testing.py` and
    `tests/architecture/test_tests_root_takes_no_new_files.py` name the file
    `test_social_fetch_tool.py`; its entries leave with it.
  - `scripts/mutations/instagram-p5-read-tool.json` and
    `instagram-p11-search-tool.json` target the R-86 test by its old name and
    three `test_social_fetch_tool.py` tests that are deleted. A mutant whose
    target is gone reports as «survived» and `make gate` never runs them, so
    each entry is either re-pointed at the test that now pins the same
    property or removed with a line saying the property left with the tool.
- A model that still names `social_fetch` after this (an old note, a stale
  habit) gets Strands' unknown-tool error, not the three-state redirect
  `social_fetch` gave for Instagram (18 §"What happens to `social_fetch`").
  Accepted: keeping a stub tool to redirect costs its schema on every turn
  forever. The routing rows in 20 are what show whether she still reaches
  for it.
- The metric label changes from `social_fetch` to the two new names. The old
  series stops, which is expected and says nothing about use: Nora restarts
  several times a day and a rare tool's series reads empty between uses.
- Decision index: a new DEC row for this bundle; DEC-151 is marked
  superseded (never rewritten); R-86's text names the four tools.
- Resident tokens re-measured with 05 §1's method against the master it
  lands on, and written into the PR and 20.

**PR 2: LinkedIn.** After PR 1, and deployed only after the operator reads
back private profile viewing on the account.

- The five bridge rows and the two bridge fixes, the renderer keys,
  `linkedin_read`, `linkedin_search`, a new `linkedin_links` module in `nora/domain/`,
  `[social.linkedin] enabled`.
- The host bridge runs the checkout's code, so it needs a restart on norty
  after the pull, as well as the image rebuild for Nora itself.

**Later, only if asked:** a `person` input on either `_read` tool, with an
account kind in `identity.toml` (00 §"Not in this bundle").
