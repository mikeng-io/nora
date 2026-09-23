---
type: design
title: "instagram_fetch — the first per-network social tool"
description: "Nora reads Instagram through one tool of its own, over the bridge only. It takes a person, a pasted link or a typed handle; the router resolves and authorizes; one read-only allowlist sits underneath. It replaces social_fetch's Instagram rows and P5 of the plan, and lands on the tool catalogue."
tags: [nora, instagram, social, tools, instagram_fetch, catalogue]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 09-tool-surface-and-naming.md, rel: realizes, provenance: asserted}
  - {dst: 15-bridge-is-the-only-entry.md, rel: realizes, provenance: asserted}
  - {dst: 17-implementation-plan.md, rel: amends, provenance: asserted}
  - {dst: ../tool-catalogue/00-overview.md, rel: constrained-by, provenance: asserted}
---

# instagram_fetch

## Decision (operator, 2026-09-23)

Social reads split **per network**. `instagram_fetch` comes first; `x_fetch`,
`threads_fetch`, `linkedin_fetch` follow the same shape later. This realizes
the reopening recorded in `09-tool-surface-and-naming.md`, under the two
conditions it kept:

1. **The model names the network, never the account.** The tool takes a
   person, a link, or a handle a human typed. The person index or the bridge
   resolves the account. Nora never infers one.
2. **One read-only allowlist underneath.** However many tools sit on top,
   "what may Nora read" is one table (R-86).

Two things made the split affordable. The tool catalogue
(`design/tool-catalogue/`) cuts a tool's resident cost from about 400 tokens
to one catalogue line. And `15` gives Instagram exactly one backend, so one
tool per network maps onto one backend per tool.

## The tool

```
instagram_fetch(target: str, what: str | None = None, limit: int = 5, cursor: str | None = None)
```

| `target` | `what` | Read |
|---|---|---|
| a person id (from `lookup_person` / `about`) | `profile`, `story` | resolved through the person index |
| an Instagram link | omitted, or matching the link | the LINK decides: profile, post, reel or story link (`15` §Links) |
| a handle someone typed (no `@`) | `profile` | resolved by the bridge (`GET /accounts?username=`) |
| free text | `search` | `GET /accounts?query=`, accounts only |

`what` is required unless `target` is a link. A link whose kind contradicts
`what` answers `unavailable` rather than guessing which was meant.

**Verbs at landing are only the ones that work end to end.** A verb joins the
tool's vocabulary when its bridge route and resolution exist, never before
("no verb admitted because one backend might grow it", `09`):

| Verb | Lands in | Bridge route |
|---|---|---|
| `profile` by person | this tool's first PR | `GET /accounts/{igid}` |
| `story` by person | first PR | `GET /accounts/{igid}/story` |
| `profile` / `story` by link or handle | P10 | `GET /accounts?username=` |
| `search` | P11 | `GET /accounts?query=` |
| `post` / `reel` by link | P12 | `GET /posts/{shortcode}` |
| `posts` (a person's list) | not before P13 finds a mechanism | none |

## Result

The same envelope as every social read: R-27's `ok`, `nothing_found`,
`unavailable`, plus `ambiguous` and `refused` where resolution and the Tier 0
check (story only) produce them. The body is the curated DTO (`16` §Resources),
never Instagram's raw response, wrapped as untrusted and clamped. It carries
media URLs as references; looking at one is `media_fetch(url=…)`, per `10`.

## Where each concern lives

| Concern | Owner |
|---|---|
| parse the link, pick the verb | the link parser, a new module under nora/infra/social (P10) |
| resolve person → igid, igid → person | `SocialSubjectIndex` (`17` P3) |
| authorize (Tier 0 for story only) | `SocialRouter`, the single choke point for EVERY social tool, `social_fetch` and `instagram_fetch` alike |
| call the bridge | `InstagramSocialClient` (`17` P4) |
| the model-facing contract | the tool in `nora/agent/tools.py` |

The tool itself stays thin: validate the arguments, call the router, return
its answer. It does not resolve or authorize. A second tool with its own
authorization copy is the drift `10` §"Who owns what" forbids.

## Read-only, at three layers, still one table

R-86 pins read-only at three layers: the tool's closed vocabulary, the
backend allowlist, and `read_only=True`. With the bridge as the backend, the
second layer changes shape. The bridge also serves writes (`/send`, `/react`,
…), so read-only is enforced by the **client**: `InstagramSocialClient` can
issue only `GET` to `/accounts…` and `/posts…`. A test pins that its method
set contains no other request. `test_r86_…` then derives all three layers from
one vocabulary table covering every social tool, not one per tool.

## The tool-catalogue contract (porgy-58, 2026-09-23)

Each item is an existing test that goes red, not a convention:

- **Catalogue line = the docstring's first sentence** (up to the first
  `". "`), ≤40 tokens (cl100k), mean ≤25 across tools, no identical lines. It
  must name Instagram and separate this tool from its confusable siblings
  `social_fetch`, `web_fetch` and `media_fetch`. Draft: *"Read Instagram: a
  person's profile or current story, or what a pasted Instagram link points
  to."*
- **The opening** (first ~8 lines) contains a `WHEN_TRIGGERS` phrase
  (`tests/test_tool_surface_policy.py`), with no permission-suppression
  wording.
- **A `NativeTool` entry with every field**, shaped like `social_fetch`'s:
  `read_only=True`, `output_contract="three_state"`, `source_tier`,
  `cacheable`, and the side-effect / idempotency / retry triple.
- **Unit-safe for the Dreamer** (`UNIT_SAFE_TOOLS` in `nora/agent/dreamer.py`),
  as `social_fetch` is.
- **Not in `HOT_TOOLS`**: that set is measured, not hand-picked.
- No `ToolScope` change: it reads Instagram from any room, like `social_fetch`.

## What happens to `social_fetch`

It loses its Instagram rows **in the same PR** that lands `instagram_fetch`,
so no Instagram read ever has two tools. Its `platform` enum becomes `{"x"}`
and its docstring X-only, until `x_fetch` replaces it and it is deleted.

## Order

`instagram_fetch` **replaces P5** of `17`. It builds on P2–P4 and lands after
the catalogue's #367 and #393 (rebased onto them). P10–P12 then each add a
verb to it, not to `social_fetch`.

## How to prove it

Routing cases in the catalogue's tool_selection eval corpus (on #393's branch), run in
both catalogue arms (resident and deferred), n ≥ 3. They are added after this
design merges, so the corpus never names a tool that does not exist:

| Message | Must call | Must not call |
|---|---|---|
| a pasted `instagram.com/<user>/` link, "who's this?" | `instagram_fetch` | `social_fetch`, `web_fetch` |
| a pasted `instagram.com/p/<code>/` link | `instagram_fetch` | `web_fetch` |
| "what's on <friend>'s story" | `instagram_fetch` | `social_fetch` |
| a pasted `x.com/…/status/…` link | `social_fetch` | `instagram_fetch` |
| a pasted news article link | `web_fetch` | `instagram_fetch` |

Live read-back: one trace in Jaeger per case, containing Nora's tool span and
the bridge's server and upstream spans. That needs tool spans in Jaeger: the
collector's filter was changed on 2026-09-23 to stop dropping them (it had
dropped every span carrying `gen_ai.operation.name`, which Strands also puts
on tool spans). Not yet confirmed with a live trace; that confirmation
is part of this design's first PR.
