---
kind: design
title: "Nora — search + fetch: the web, wrapped untrusted, with the SSRF control at Firecrawl's egress"
description: "The implementation of design/14 §2.1/§3's two web verbs, revised after a design review found the fence in the wrong place. search(query) over SearXNG and fetch(url) over Firecrawl. The SSRF control lives at Firecrawl's container egress (the only place the post-redirect, post-rebind connection is governed) — Nora's search-result URL allow-set and is_global precheck are defence-in-depth. Retrieved bytes are wrapped untrusted (escape-safe, reusing the transit _wrap precedent), search is result- and byte-capped, and the whole thing is inert-by-config."
tags: [nora, design, grounding, search, fetch, searxng, firecrawl, ssrf, untrusted]
timestamp: '2026-08-07'
status: proposed
---

> **SUPERSEDED — half of this shipped.** `web_search` is live (Tavily); the
> `web_fetch` half was RETIRED (DEC-73) after it exhausted the token budget —
> fetched pages are input-resident on every subsequent call of the turn, so a
> research turn dropped empty replies. Read for the SSRF reasoning, not as a
> plan.

# search + fetch — the web, fenced at the right layer

design/14 §2.1/§3 designed the two web verbs and left them last: they are the only
grounding capability whose *inbound* content is untrusted and whose *outbound* call
(a self-hosted fetcher against a model-chosen URL) is an SSRF surface. Both services
run locally: **SearXNG** (`127.0.0.1:8080`, `format=json`) and **Firecrawl**
(`127.0.0.1:3002`).

**This revision folds a design review (2026-08-07) whose verdict was NEEDS-FIXES
bordering REDESIGN — the SSRF layer was wrong.** The v1 spec put the SSRF control
in Nora's pre-call IP check. That is near-useless: **Firecrawl follows redirects
and re-resolves DNS itself**, so a URL that passes Nora's check (public IP, a real
search result) but `302`s to `http://127.0.0.1:18080/api/v1/rpc`, or DNS-rebinds
(public to Nora's resolver, `127.0.0.1` to Firecrawl's), is fetched by Firecrawl
against Nora's own internals. Nora never sees the redirect target or Firecrawl's
resolution. The control must live where the connection is actually made.

## 0. The SSRF control is Firecrawl's egress — everything else is defence in depth

**Primary control (operator ship condition): Firecrawl is network-isolated so it
cannot reach Nora's internals at the packet layer.** Firecrawl runs on a dedicated
docker network with an egress policy that DROPs to loopback, RFC1918 (`10/8`,
`172.16/12`, `192.168/16`), CGNAT (`100.64/10`), link-local (`169.254/16`,
`fe80::/10`), ULA (`fc00::/7`) and `0.0.0.0/8`, allowing only public routable
destinations — applied to the *actual* post-redirect, post-rebind `connect()`,
regardless of how Firecrawl arrived at the IP. Today Firecrawl is co-located with
Honcho (`:8000`), signal-cli (`:18080`), OmniRoute (`:20128`) and SearXNG (`:8080`),
all reachable from it; that co-location is the vulnerability, and the network policy
is what closes it. **This is the one control that actually holds**, and it is a
deployment change (like the Postgres cutover), so #24's build is gated on it.

**Defence in depth, in Nora (real, but NOT sufficient alone — the spec says so):**
- **`fetch` accepts only a URL a `search` returned this turn** (`allowed_urls`,
  per tag). Reduces the surface from "any model-invented URL" to "any URL an
  attacker can surface in search then redirect/rebind" — worth having, not a
  guarantee.
- **An `is_global` allow-check, not a hand-rolled denylist.** Resolve the host via
  `getaddrinfo` (normalises octal/decimal/hex/IPv4-mapped encodings and does DNS),
  and accept only if **every** returned address is
  `ipaddress.ip_address(a).is_global` (rejecting `is_private | is_loopback |
  is_link_local | is_reserved | is_unspecified`). Three stdlib calls that cannot
  miss a range — unlike the v1 enumerated list, which missed `0.0.0.0/8`, CGNAT,
  IPv6 ULA/link-local, IPv4-mapped, and numeric encodings. Still does not stop
  Firecrawl's own re-resolution/redirect (finding 1) — hence "in depth".

## 1. `search(query)` — SearXNG, capped, untrusted

- **`GET {searxng_url}/search?q=…&format=json`**, the `post_json`/HTTP pattern
  `transit`/`route` use (never raises → `unavailable`, not empty; R-5).
- **Titles, snippets, URLs only.** Title+snippet are **untrusted** (a page controls
  its snippet) → wrapped (§3).
- **HARD CAPS (design/14 §4, missing from v1 — a token-DoS and untrusted-text
  amplifier without them):** `search_max_results` (constant, ~6) and a per-snippet
  byte clamp. SearXNG `format=json` returns the whole engine result set (design/14
  measured 27); retrieved text lands in the INPUT and is re-sent on every
  subsequent model call (`tokens × turns-remaining` against `total_tokens=64000`).
  Truncation is counted, the `read_buffer`/`READ_BUFFER_MAX` discipline.
- **The query is an egress surface** (design/14 §2.2): `scan_identifiers` blocks an
  identifier before it reaches SearXNG; a person **alias is COUNTED, not blocked**
  (`nora_search_alias_total`, over `_known_names(persons)`); model names are **not**
  blocked (`scan_identifiers`, not `scan_reply` — 「搵首 haiku」 must work).
- **`allowed_urls` is populated here** — every returned URL, per tag, for `fetch`.

## 2. `fetch(url)` — Firecrawl, defence-in-depth then clamped

- **In `allowed_urls`** (a search result this turn) else `refused`, nothing reaches
  Firecrawl (`firecrawl.calls == []` probe).
- **`is_global` check** (§0) before the call.
- **Firecrawl API version PINNED** to the deployed instance's — **verify `/v1/scrape`
  vs `/v2/scrape` against the running `firecrawl-api` container before build** (the
  review notes v2 is current upstream; design/14 measured `/v1/scrape` on this
  instance). Record the chosen version; the fake mirrors that instance's envelope
  (`{success, data:{markdown, metadata:{url, sourceURL}}}`).
- **The markdown is untrusted** → wrapped (§3), **and clamped** to `fetch_max_chars`
  (input token budget).

## 3. The untrusted wrap — reuse the proven precedent

Both a search snippet and a fetched page are returned **wrapped**, reusing the
**existing, escape-safe `nora.infra.transit.arrivals._wrap`** (R-13): it
`escape()`s the content so a body containing `</untrusted>` becomes
`&lt;/untrusted&gt;` and cannot forge the boundary. A test feeds a page whose body
IS `</untrusted>` and proves it holds.

    <untrusted source="https://example.com/x">…escaped retrieved text…</untrusted>

- **Provenance** — the `source` URL rides the wrap. (Note: `escape()` entity-encodes
  all angle brackets, so fetched HTML/code arrives entity-encoded — safety-sound,
  minor fidelity loss, stated.)
- **No path from retrieved text to skill activation** (R-29) — verified structural:
  `SkillStore.resolve` is a pure name lookup against the operator dir, `_NAME_RE`
  rejects paths/URLs/inline blobs, retrieved text can at most name a shipped skill.
- **Enters as a tool RESULT**, never a system/prompt position (`ports.adapt` →
  `ToolResult` block) — content, not directive.
- **Structural, not detected** — we do not scan retrieved text for injection
  (design/14 non-goal).
- **`fetch` deliberately does NOT `scan_identifiers` the page** (unlike `about`),
  and this is a STATED decision, not a silent gap (finding 5): a public web page is
  not Nora's people-data, and stripping a whole page is impractical; the identifier
  boundary for anything the model then tries to *relay* is `scan_reply` at
  `Deliverer` (fails the reply closed — acceptable R-5 silence). Named so it does
  not read as the gap `_asked_about` closed.

## 4. States, budgets, gating

- **Three states** (R-27): `ok` / `nothing_found` (zero results / empty page) /
  `unavailable`; `fetch` adds `refused` (not in the fence, or non-global).
- **Budgets** `search` 2, `fetch` 2; `terminal=False`, `time_shiftable=None`.
- **Gated behind `[search] enabled` + reachable services** → `unavailable` absent
  (the `route`-with-no-key shape). Inert-by-config; `[search] enabled=false` until
  the operator turns it on **and the Firecrawl egress isolation (§0) is in place**.
  `searxng_url`, `firecrawl_url`, `firecrawl_api_version`, `search_max_results`,
  `snippet_max_bytes`, `fetch_max_chars`, timeouts are real config fields.
- **SearXNG `format=json` → 403 if disabled** (many instances disable it): a 403
  maps to `unavailable` FOREVER — the dead-`peer_card`/61-day-signal-cli silent
  shape (CLAUDE.md). So a ship-time check must confirm a live search returns **`ok`**
  (not merely "service reachable"), and the fake returns the 403-on-disabled.
- **The intent router is NOT built here** — two plain tools among the rest.

## 5. What must be true before it ships

1. **Firecrawl runs network-isolated** so its `connect()` to loopback/RFC1918/
   CGNAT/link-local/ULA/`0.0.0.0` is dropped at the packet layer, public egress
   allowed — the primary SSRF control (§0, a deployment change). **(The blocker.)**
2. **The SSRF property is gated on a REAL-Firecrawl integration probe, not a fake**
   (finding 7): a public URL that `302`s to `127.0.0.1:18080` is fetched and the
   internal service is verified *never reached* at the network layer. No fake can
   prove this (the bypass runs inside Firecrawl) — a green unit suite means only
   "Nora refuses naive URLs", which must not be read as "SSRF closed".
3. `fetch` refuses a URL not in `allowed_urls` (`firecrawl.calls == []`), and a
   non-`is_global` host resolved via `getaddrinfo` (numeric encodings + DNS names
   pointing at loopback both refuse) — defence in depth, before any call.
4. `search` is capped: `search_max_results` and a per-snippet byte clamp, truncation
   counted; a broad query cannot flood the input token budget.
5. Retrieved text (snippet AND page) is wrapped via `arrivals._wrap`; a body of
   `</untrusted>` cannot forge the boundary; the fetched markdown is clamped to
   `fetch_max_chars`.
6. Retrieved text activates no skill (R-29 probe) and enters only as a tool result.
   `fetch`'s no-identifier-scan is stated with `scan_reply`/`Deliverer` as the
   boundary.
7. The Firecrawl API version is pinned to the deployed instance (verified against
   the running container); SearXNG `format=json` disabled → the fake returns 403 and
   a ship check confirms a live search returns `ok`.
8. `search`/`fetch` never raise (`test_a_tool_never_raises`); gated behind `[search]
   enabled` + reachable → `unavailable`; budgets 2/2; the fakes return the real
   SearXNG/Firecrawl envelopes and the wrong-shape for a wrong call.

## 6. Decision-index entries this creates

- **DEC-57 / R-32** — the SSRF control for `fetch` is Firecrawl's network-isolated
  egress (packet-layer, post-redirect/rebind); Nora's search-result URL fence and
  `is_global` precheck are defence-in-depth, not the guarantee. Retrieved web content
  is wrapped `untrusted` (escape-safe via `arrivals._wrap`, provenance, skill-inert),
  search is result/byte-capped, fetch is char-clamped, the query is
  `scan_identifiers`-screened with aliases counted. Inert-by-config behind `[search]
  enabled` + the egress isolation. Criteria 1–8.
- A RULE test in `tests/test_acceptance_rules.py::RULE_TESTS`, plus the §5.2
  real-Firecrawl SSRF integration probe (not a unit fake).

(DEC/R numbers provisional — reconciled at merge time.)
