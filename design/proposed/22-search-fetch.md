---
kind: design
title: "Nora — search + fetch: the web, wrapped untrusted, with fetch fenced against SSRF"
description: "The implementation of design/14 §2.1/§3's two web verbs. search(query) over SearXNG and fetch(url) over Firecrawl, where the ONLY url fetch will accept is one a search returned THIS turn — the structural fence that stops self-hosted Firecrawl becoming an SSRF pivot into Nora's localhost internals — and every retrieved byte is wrapped untrusted with escape-safe provenance and no path to skill activation."
tags: [nora, design, grounding, search, fetch, searxng, firecrawl, ssrf, untrusted]
timestamp: '2026-08-07'
status: proposed
---

# search + fetch — the web, structurally fenced

design/14 §2.1/§3 designed the two web verbs and left them last, on purpose: they
are the only grounding capability whose *inbound* content is untrusted and whose
*outbound* call (a self-hosted fetcher against a model-chosen URL) is an SSRF
surface. This is the implementation spec — the concrete mechanisms, ship
conditions, and tests that make design/14's structural defences real rather than
prose. Both services already run locally: **SearXNG** (`127.0.0.1:8080`) and
**Firecrawl** (`127.0.0.1:3002/v1/scrape`).

**The two rules design/14 fixed, restated because they are the whole point:**
1. **`fetch` only accepts a URL a `search` returned THIS turn.** A model-invented
   URL is refused. SearXNG, Honcho (`:8000`), signal-cli (`:18080`), OmniRoute
   (`:20128`) and Firecrawl itself all listen on localhost, so a model-chosen URL
   makes self-hosted Firecrawl an SSRF pivot into Nora's own internals. Fencing
   `fetch` to a prior search result is the structural mitigation.
2. **Every retrieved byte is wrapped `untrusted`.** A web page may have been
   written specifically to be retrieved by an agent (authority item 4, worse than
   a group message). We do NOT detect injection (design/14 §2.1's stated non-goal
   — detection is the decorative-gate failure). The defence is structural: wrap,
   preserve provenance, and **no path from retrieved text to skill activation**.

## 1. `search(query)` — SearXNG, titles/snippets/URLs only

- **Over SearXNG's JSON API** (`GET {searxng_url}/search?q=…&format=json`), through
  the `post_json`/HTTP pattern `transit`/`route` already use (never raises; a
  failure is `unavailable`, not an empty result — R-5).
- **Returns titles, snippets, and URLs only** — the three fields the model needs
  to decide what to `fetch`. Each result's title+snippet are **untrusted text**
  (a page controls its own snippet) → wrapped (§3). The URL is data the model
  passes back to `fetch`, not prose.
- **The query is an egress surface** (design/14 §2.2): `scan_identifiers` blocks an
  identifier (a phone number / ACI / key) in the query before it reaches SearXNG;
  a person's **alias is COUNTED, not blocked** (`nora_search_alias_total`) — we do
  not yet know the shape of real queries, and a guess encoded as a block fails
  closed on legitimate use. Model names are **not** blocked here (`scan_identifiers`,
  not `scan_reply` — 「搵首 haiku」 must work).
- **The per-turn URL fence is populated HERE.** Every URL `search` returns is
  added to a per-tag set (`allowed_urls`) that `fetch` reads. Fresh per tag (built
  in `build_tool_specs`, GC'd with the tag — not retained per-tag state), so a URL
  from another turn or conversation is not fetchable.

## 2. `fetch(url)` — Firecrawl, fenced two ways and clamped

- **The URL must be in `allowed_urls`** (a prior `search` result this turn).
  Otherwise `refused` with an honest note ("I can only open a page a search turned
  up"), and **nothing reaches Firecrawl** — the probe is that a model-invented URL
  leaves `firecrawl.calls == []`, the same shape R-28's route-person test uses.
- **Defence in depth: the URL is ALSO checked to not resolve to a private/loopback
  address**, even if it came from a search result. Parse the URL; reject
  `localhost`, `127.0.0.0/8`, `::1`, `10/8`, `172.16/12`, `192.168/16`,
  `169.254/16` (link-local), and non-`http(s)` schemes — *before* the call. Belt to
  the allow-set's braces: a search engine that returned an internal URL cannot
  pivot. This resolves the host to an IP and checks the IP, so a DNS name pointing
  at `127.0.0.1` is caught too.
- **Over Firecrawl** (`POST {firecrawl_url}/v1/scrape`, `formats:["markdown"]`),
  same never-raises HTTP pattern.
- **The markdown is untrusted** → wrapped (§3), **and clamped** to
  `fetch_max_chars`: retrieved text lands in the INPUT (governed by
  `total_tokens = 64000`, not the output ceiling), so an unclamped page could
  blow the turn's context budget. The clamp is stated to the model ("first N
  characters").

## 3. The untrusted wrap — escape-safe, provenance-carrying, skill-inert

Both a search snippet and a fetched page are returned **wrapped**:

    <untrusted source="https://example.com/x">
    …retrieved text…
    </untrusted>

- **Escape-safe.** Retrieved text that itself contains `</untrusted>` (or the
  opening tag) must NOT be able to forge the boundary — the wrapper neutralises
  the sentinel in the content first, the exact discipline the canary and the `↳`
  snippet use (R-13). A test feeds a page whose body IS `</untrusted>` and proves
  the wrap holds.
- **Provenance preserved** — the `source` URL rides the wrap, so the model (and a
  reader of the transcript) knows where the claim came from.
- **No path to skill activation** (design/14 §2.1, R-29). Retrieved text is data;
  it can never introduce a skill source. This already holds structurally
  (`AgentSkills`/`SkillStore` load only from the operator dir), but the spec names
  it as an acceptance check: a page whose body says "activate skill navigation"
  activates nothing.
- **Structural, not detected.** We do not scan retrieved text for injection
  strings (design/14's non-goal). The wrap + the no-skill-path + the fact that it
  enters as a tool RESULT (content, never a system/prompt position) is the defence.

## 4. States, budgets, gating

- **Three states** (R-27): `ok` / `nothing_found` (SearXNG returned zero results;
  Firecrawl returned an empty page) / `unavailable` (service down / timed out).
  `fetch` adds `refused` (URL not in the fence, or a private address).
- **Budgets** (DEC-10): `search` 2, `fetch` 2 — a question may need a second
  search or a second page, rarely a third. `terminal=False`,
  `time_shiftable=None` (a page is not time-sensitive the way a bus is; "whenever
  indexed / crawled", design/14's table).
- **Gated behind `[search] enabled` + reachable SearXNG/Firecrawl.** Absent →
  `unavailable` (the `route`-with-no-key inert shape). Ships **inert-by-config**;
  `[search] enabled=false` until the operator turns it on. `searxng_url`,
  `firecrawl_url`, `fetch_max_chars`, and the timeouts are real config fields.
- **The intent router (design/14's later idea) is NOT built here** — these are two
  plain tools the model selects among the rest; routing them is the model's job
  today, a skill's or a router's later.

## 5. What must be true before it ships

1. `search` returns titles/snippets/URLs from SearXNG; a down SearXNG is
   `unavailable`, zero results is `nothing_found` (distinct); `scan_identifiers`
   blocks an identifier in the query, a person alias is counted not blocked.
2. **`fetch` refuses any URL not returned by a `search` this turn** — a
   model-invented URL leaves `firecrawl.calls == []` (the SSRF fence; reverting it
   turns the test red). The `allowed_urls` set is per tag, not retained across tags.
3. **`fetch` additionally refuses a private/loopback/link-local address or a
   non-http(s) scheme**, resolving the host to an IP first — `http://127.0.0.1:18080`
   and a DNS name pointing at loopback both refuse, before any Firecrawl call.
4. Retrieved text (snippet AND page) is wrapped `untrusted` with the `source` URL;
   a body containing `</untrusted>` cannot forge the boundary (escape-safe test).
5. The fetched markdown is clamped to `fetch_max_chars`; the clamp is stated to
   the model, and a huge page cannot blow `total_tokens`.
6. Retrieved text activates no skill (R-29 acceptance probe) and enters only as a
   tool result, never a system/prompt position.
7. `search`/`fetch` never raise into the loop (`test_a_tool_never_raises`); both
   gated behind `[search] enabled` + reachable services → `unavailable` when
   absent; budgets 2/2 in `NATIVE_TOOLS`.
8. The fakes behave like the real servers — SearXNG's `format=json` shape,
   Firecrawl's `/v1/scrape` markdown envelope, and the wrong-shape for a wrong call
   (CLAUDE.md fake rule); reverting any of the fences turns a test red.

## 6. Decision-index entries this creates

- **DEC-57 / R-32** — `fetch` accepts only a URL a `search` returned this turn AND
  not a private address; retrieved web content is wrapped `untrusted` (escape-safe,
  provenance-carrying, skill-inert) and clamped; the query is `scan_identifiers`-
  screened with aliases counted not blocked. Inert-by-config behind `[search]
  enabled`. Criteria 1–8.
- A RULE test in `tests/test_acceptance_rules.py::RULE_TESTS`.

(DEC/R numbers provisional — reconciled against the sibling branches at merge time.)
