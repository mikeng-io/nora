---
type: design
title: "Intent: one pair of tools per network, and social_fetch goes"
description: "Why social_fetch is retired now, what replaces it for X and LinkedIn, what the LinkedIn probe measured, and the privacy rule LinkedIn needs that X does not."
tags: [nora, social, x, linkedin, social_fetch, tools, naming]
timestamp: 2026-09-24
edges:
  - {dst: 10-changes, rel: sequences, provenance: asserted}
  - {dst: 20-verification, rel: sequences, provenance: asserted}
---

# Intent

## The decision (operator, 2026-09-24)

`social_fetch` is retired. Its reads split per network, under the naming
`../instagram-adoption/18-instagram-read-and-search.md` fixed for every
network:

| Tool | Takes | Reads |
|---|---|---|
| `x_read` | a pasted X link, or a typed handle | one post and its thread, a profile, an account's recent posts |
| `x_search` | free text | X posts matching it |
| `linkedin_read` | a pasted LinkedIn link, or a typed handle | a profile, a person's experience, a person's recent posts, a company page |
| `linkedin_search` | free text | LinkedIn people matching it |

`_read` takes a subject, `_search` takes free text, and `_fetch` stays
reserved for retrieval by address (`web_fetch`, `media_fetch`). The first
draft of the request said `xfetch` / `linkedinfetch`; 18 rejected `_fetch`
for exactly this, and the operator corrected it to `_search`.

**Threads is out of scope.** OpenCLI 1.8.7, the backend both networks use,
has no Threads site (`opencli list -f json`, read on norty 2026-09-24).
`threads_read` joins when a backend serves it, per 09's rule that a verb is
admitted when something can serve it.

## Why now

`social_fetch` has served X alone since P11 (18 §"What happens to
`social_fetch`"). 18 already names its end: «its `platform` enum becomes
`{"x"}`, until `x_read` / `x_search` replace it and it is deleted». What is
left is a one-network tool with a `platform` parameter that accepts one
value and a redirect branch for a network it no longer serves. Every turn
pays for that shape, because every tool is resident (`[llm] defer_tools`
stays `false`, `../tool-catalogue/05-measurement.md` §3a).

The two conditions 09 kept when it reopened the per-network split still
hold, and this bundle keeps them:

1. **The model names the network, never an account it inferred.** Both
   `_read` tools take a link a person pasted or a handle a person typed.
   Neither takes a person's name in this bundle (§"Not in this bundle").
2. **One read-only allowlist underneath.** X and LinkedIn both go through
   the host-side OpenCLI bridge, whose frozen `READ_ONLY_ALLOWLIST` stays
   the one table of what Nora may ask OpenCLI for (R-86).

## One backend per network, unchanged

| Network | Backend | Door |
|---|---|---|
| X | OpenCLI (`twitter` site) | host bridge `:19826/read` |
| LinkedIn | OpenCLI (`linkedin` site) | the same bridge |
| Instagram | instagram-bridge | 15, unchanged |

`SocialRouter` stays the single choke point. X and LinkedIn rows resolve to
its `default` backend (OpenCLI), exactly as `social_fetch`'s X rows do now.
No new process, no new port, no new credential in Nora's container: the
LinkedIn session lives in the host's Chrome profile, like X's.

## What LinkedIn can do today (probe, 2026-09-24)

The operator logged the social Chrome into a **dedicated LinkedIn account**,
not a personal one (`opencli linkedin whoami` → `logged_in: true`, the
account's display name is "Nora Nor"). Before the login every LinkedIn
command answered `AUTH_REQUIRED`, exit 77. One call each, so this shows the
commands work and says nothing about how reliable they are:

| OpenCLI command | Target | rc | Time | What came back |
|---|---|---|---|---|
| `profile-read --profile-url` | a public figure's `/in/` page | 0 | 12.6 s | name, headline, about, featured. Location, experience, education EMPTY |
| `profile-experience --profile-url` | the same page | 0 | 10 s | one row per role: title, company, date range, location |
| `posts --profile-url --limit 3` | the same page | 0 | 13.2 s | body, age (`11h`), reactions, comments, media URLs on `media.licdn.com`. `url` is empty on a repost |
| `company <slug>` | a large company | 0 | 8.0 s | industry, size, HQ, founded, website, followers, about |
| `people-search <keywords> --limit 3` | a public figure's name | 0 | 6.2 s | name, headline, location, profile URL. **Two of three were impostors** with the real person's headline |

Three findings shape the design:

- **`linkedin search` is a JOBS search.** People search is
  `people-search`, and OpenCLI's own description says each call counts
  against LinkedIn's monthly Commercial Use Limit on people search. So
  `linkedin_search` maps to `people-search`, and the limit is a budget
  (10 §`linkedin_search`).
- **There is no read of one post by its URL.** `posts` lists a profile's
  activity; nothing takes a `/feed/update/` or `/posts/` link. A pasted
  LinkedIn post link is therefore not readable, the same gap Instagram's
  post lists have (18, P13). `linkedin_read` refuses it as `unavailable`
  with a note saying why, rather than reading the author's activity and
  hoping the post is in it.
- **A profile's roles are a second command.** `profile-read` returned the
  experience section empty; `profile-experience` returned it. They are two
  `what` values (10 §`linkedin_read`), not one read that makes two
  browser visits.

## LinkedIn reads are visible to the person read

This is the one place LinkedIn differs from X, and it decides a rule.

Viewing a LinkedIn profile is a product feature on LinkedIn's side: the
person viewed can see "who viewed your profile", with the viewer's name,
unless the viewer's account has profile viewing set to private. Every
`linkedin_read` opens a profile or company page as the dedicated account.
An X profile read has no such property.

So, before any LinkedIn tool is enabled:

1. **The account's profile viewing is set to private** (LinkedIn
   Settings → Visibility → Profile viewing options → Private mode). Only the
   operator can set it, and it is a deploy precondition in 10, read back by
   the operator rather than assumed.
2. **Both LinkedIn tools run only in a turn a person asked for.** In a fire
   turn (reminder, proactive, participation) or a Dreamer pass they answer
   `refused` before anything is sent, the same mechanism and the same
   provenance (`fire_turn`, `dream_adaptor`) `instagram_read` uses for a
   story (18 §"A story is opened only when a person asks"). Private mode
   hides the name; it does not make an unprompted visit to someone's
   profile something Nora should do. `linkedin_search` is covered too,
   because it spends a monthly budget and because a search that nobody
   asked for is Nora looking people up on her own, which 18 left as an open
   operator decision for `instagram_search`.

X keeps `social_fetch`'s current standing: both X tools are unit-safe and
usable in any turn, because they read public posts and nothing about the
read reaches the account read. That is a carry-over, not a new decision.

## Not in this bundle

- **Reading by a person's name** (`x_read(person=...)`,
  `linkedin_read(person=...)`). It needs an X or LinkedIn account kind in
  `identity.toml` and the person index to resolve it, as Instagram's
  `igid` does. Nobody has asked for it and nothing in `identity.toml`
  records either account today. It is added as a new input kind on the
  existing tools if the operator asks, not as a new tool.
- **Reading one LinkedIn post by its link.** No backend serves it (above).
- **Any write** on either network. OpenCLI's write verbs stay unreachable
  through the bridge; LinkedIn's (`connect`, `safe-send`,
  `salesnav-message`, `login`) join the tripwire list (10).
- **Threads**, until a backend serves it.
