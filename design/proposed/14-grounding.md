---
kind: design
title: "Nora — grounding: four kinds, not one, and the seams they need"
description: "Search, fetch, live structured data and routing are different capabilities with different trust, cost and time properties. What they share is a place resolver and a clock."
tags: [nora, design, grounding, search, transit, routing, egress, injection]
timestamp: '2026-08-06'
status: proposed
supersedes: "this document's own first draft, which treated grounding as one search(query) tool"
---

# Grounding

> Content-policy update, 2026-10-25: phone/UUID/coordinate, place/person-name
> and location audience filters described here remain implemented and tested in
> enabled mode, but runtime defaults them off through `[content_checks]`.
> Credentials, doctrine, scope, consent and availability are independent.
> See the [current decision](../../docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md).


Everything Nora knows comes from inside the room: the transcript,
`identity.toml`, Honcho, and the clock. Grounding is the first capability that
reaches outside.

**This replaces its own first draft**, which was wrong in a way worth
recording: it treated grounding as one capability called `search(query)` and
spent its length choosing a search provider. Two operator questions broke that
framing — 「K73 幾時到」 and 「由 A 去 B 邊條路最快」 — and neither is
answerable by searching or scraping anything.

## 1. Four kinds, and they do not substitute

| kind | source | answers | trust | time |
|---|---|---|---|---|
| **live structured** | `rt.data.gov.hk`, KMB/MTR, car parks | facts true for seconds | typed, trusted | **now only** |
| **routing / geo** | Google Routes, Places | how to get there, how long | typed, trusted | **any time** |
| **web search** | SearXNG → Brave et al. | *pages about* a thing | **untrusted** | whenever indexed |
| **page fetch** | Firecrawl | contents of a known URL | **untrusted** | whenever crawled |

Verified live, 2026-08-06:

    MTR bus K73 ETA   status 0, 27 stops, next bus 112s     free, no key
    KMB ETA           same API family, valid envelope        free, no key
    SearXNG :8080     27 results (engine: brave)             local, PROXYING
    Firecrawl :3002   /v1/scrape -> clean markdown           local

Two consequences the first draft missed:

**The transport kinds compose rather than compete.** Google says "take K73 from
stop D020" from a *schedule*; `rt.data.gov.hk` says "that bus is 112 seconds
away" *live*. Neither alone answers 「幾點出門」. Combining them is
instructions, not capability — which is what a **skill** is, and the first
domain that has ever qualified (`design/12` §1 said none did).

**Trust is not uniform.** §2.1 holds for search and fetch. It does **not** hold
for a government ETA API returning typed fields — that is closer to
`listGroups` than to a web page, and wrapping it `untrusted="true"` would
miscategorise it. `design/17`'s argument is that miscategorised things fail
silently.

## 2. The two boundaries

### 2.1 Inbound — search and fetch are untrusted; typed APIs are not

Retrieved **web** content is authority item 4 and worse: a group message was at
least written by someone `policy.toml` admitted; a page may have been written
specifically to be retrieved by an agent. So: wrapped `untrusted="true"`,
provenance preserved, and **no path from retrieved text to skill activation**.

Stated non-goal: we do not *detect* injection. Detection is unreliable and
relying on it is the decorative-gate failure this repo keeps finding. The
defence is structural.

**Typed API responses are not wrapped** — no free-text field for an adversary
to write in. If a source ever returns one, that field is wrapped even though
its envelope is typed.

### 2.2 Outbound — tool arguments are an egress surface

R-18 guards *replies*, not arguments. Routing makes this concrete rather than
theoretical: **a route query inherently contains two locations.**
「由陳生屋企去我公司」 must never become a Google query resolving a named
person to a home address. There is no legitimate version of that request.

Superseding the first draft's uniform rule:

- **Identifiers** (numbers, ACIs, keys) — blocked unconditionally, every tool.
- **A person's name or live position in a routing origin/destination** —
  **hard block**. Unlike a search query there is no benign reading.
- **A person's alias in a search query** — **counted, not blocked.** We do not
  know the shape of real queries yet, and a guess encoded as a block fails
  closed on legitimate use.

**`scan_egress` must be split first.** Its `_MODELNAME` pattern matches
`gemini|haiku|sonnet|opus|claude|grok` — correct for replies (she must never
name her model), catastrophic for queries: 「搵首 haiku」 would be refused, and
`scan_egress` fails closed on the *whole reply*. Split into
`scan_identifiers()` (tool arguments) and `scan_reply()` (identifiers + model
names) so the two call sites cannot be confused.

**Built 2026-08-06 (DEC-30).** `scan_egress` is gone rather than deprecated —
a third name that does one of the two jobs is how the wrong one gets called
later. It also turned out to be a live bug, not only a prerequisite: `recall`
was filtering Honcho conclusions through the reply predicate, so every durable
memory naming an LLM was silently withheld from a group of software engineers.

### 2.3 Corrections to the first draft, recorded

- **"The query never leaves the host" was false.** SearXNG is a metasearch
  *proxy* with no index — the same measurement that claimed locality says
  `engine: brave`. Self-hosting buys that *Nora's client identity* does not
  reach Brave. Real, but weaker, and §2.2 is **not** downgradable.
- **The token argument used the wrong currency** — it reasoned against
  `max_tokens` (per-call *output* ceiling, now 2000). Retrieved text lands in
  the **input**, governed by `total_tokens = 64000`. See §4.
- **R-19/R-20 were already taken.** R-20 is the forged-quote rule, registered
  and enforced; R-19 is reserved by `design/11`. Grounding's are **R-27/R-28**.
- **The scrape verb was never declared** — §3 required Firecrawl while the
  tool list held only `search(query)`: a second verb living in prose with no
  schema, no budget entry, no URL authority.

## 3. Shape

**`transit(route)`** — live ETA. Free, keyless, bounded typed JSON, no
injection surface, no egress question. Ships first.

**`route(origin, destination, mode, arrive_by)`** — Google. Paid, external,
keyed. `arrive_by` is the point (§5.1).

**`search(query)`** — SearXNG. Titles, snippets and URLs only.

**`fetch(url)`** — Firecrawl, and the URL comes from a **prior `search` result
in this same turn**, never free-form from the model. That is the whole
mitigation: SearXNG, Honcho, signal-cli (`:18080`), OmniRoute (`:20128`) and
Firecrawl itself all listen on localhost, so a model-chosen URL makes
self-hosted Firecrawl an SSRF pivot into Nora's own internals. Constraining it
to a search result is what stops `http://127.0.0.1:18080/api/v1/rpc` being
fetchable.

Each gets a `NATIVE_TOOLS` entry (budget + `when`) or R-7 default-denies it,
and each returns **three states** — `ok` / `nothing_found` / `unavailable` —
with a `{tool, status}` counter. "The API is down" and "there is no such bus"
must never be one answer; that ambiguity hid a dead `peer_card` for the life of
the deployment.

## 4. Intake is bounded by constants, not a component

Retrieved text lands in the input and is **re-sent on every subsequent model
call**, so its true cost is `tokens × calls remaining`. Measured:
`read_buffer(200)` = 6,592; base prompt 4,952; `total_tokens` 64,000.

`design/18` proposes `ContextBudget` for this, and the review of that document
argued it is a component where a constant would do — and that
`nora_llm_stop_reason_total{reason="limit_total_tokens"}` is **already live**
and should be read first. So grounding does not wait on it. It ships with hard
ceilings in code: a result count and snippet-byte cap on `search`, a byte
ceiling on `fetch` with truncation **stated in the return value**, and asserted
bounds on the typed tools. If the counter shows real pressure once grounding is
live, that is the evidence `design/18` needs. Not before.

## 5. What the operator's scenario demands

> 「@nora 幫每個人設計最好路線」 — dinner at Yoho Mall at 20:00, asked at 15:00.
> Miles, Tyler and Aaron travel together from home; Grant and Blake drive
> separately from wherever work ends; the drivers want a **car park** near the
> destination with pricing, live availability and a fallback.

The four tools do not answer this, and the gap is not more sources.

**5.1 The question is about the future.** Every tool answers about the present.
A live ETA is worthless for a bus three hours out. Google Routes accepts
`arrive_by` and will answer for 19:30; `rt.data.gov.hk` cannot time-travel at
all. **Nora must know which sources can be asked about a future moment**, or
she will quote a 112-second bus wait for a bus leaving at 19:30. That is a
property of each tool and belongs in `NATIVE_TOOLS` beside `when` and `budget`.

**5.2 Nothing resolves a place or a person to a location.** 「我屋企」,
「Yoho Mall」, 「Tyler 而家喺邊」 must become coordinates before any route call.

**Life360 is the right source for the PERSON half** (operator, 2026-08-06) —
it already holds everyone's position, so nothing needs to be inferred or asked.
That makes the resolver easy and the boundary hard, and the boundary is the
part to design:

- Live location is **the most sensitive data in this system**, more than phone
  numbers. A number identifies; a position tracks. `identity.toml` is
  gitignored and mounted read-only for less than this.
- **In-group disclosure is fine — operator decision, 2026-08-06.** An earlier
  draft said a position must never be reported to the group. That imported a
  boundary that does not exist here: everyone in the group is in the same
  Life360 circle and can already see each other. 「Tyler 而家喺 Yoho」 discloses
  nothing the app does not.

  **But the guarantee must be mechanical, not remembered.** Report a position
  only into a conversation whose members are a SUBSET of the Life360 circle.
  Today they match; the day someone joins the Signal group who is not in the
  circle, this must hold by construction. That is R-21's shape one level up,
  and R-21 is enforced at assembly for exactly this reason.

- **Outbound is a different question and the block stands.** "Nora may tell
  your friends where you are" is not "Nora may send your live coordinates to
  Google." She can compute a route FROM a position without that position
  appearing in an argument that leaves the host (§2.2).
- **Read at decision points, never persisted.** A tool called during a turn,
  not a background collector. That keeps it inside R-5's model, where failure
  is visible in a turn rather than silently stale on disk.

The resolver is thus **not** a thin wrapper over Places + Life360 — it is a
scope boundary, and belongs beside `Room` (`design/12` §3: scope is enforced at
assembly).

**5.3 A plan is not a lookup.** Per-person routing needs to know when each
person leaves — a fact only that person has. Life360 gives position, not
intent: knowing Grant is at his office does not say whether he leaves at 18:00
or 19:30. **The cheap version is to ask.** She is in the group;
「Grant 你幾點收工？」 is one message and beats any forecast.

This is also the argument against sampling positions every few seconds into a
time-series store: it is continuous surveillance of five friends persisted to
disk, it multiplies §5.2's exposure by every second it runs, and it still would
not know when Grant finishes work.

**5.4 Car parks are ordinary — verified 2026-08-06.**
`api.data.gov.hk/v1/carpark-info-vacancy` returns **560 car parks** with live
vacancy counts. A fifth live-structured tool, not a research problem.

Two measured caveats that shape the tool rather than block it:

- **Pricing was NOT in the vacancy response** (`data=vacancy` returns counts and
  timestamps only). design/14's first draft claimed occupancy *and* pricing
  from one call; pricing appears to need the `data=info` variant and is
  **unverified**. Do not promise pricing in the tool's `when` string until it
  has been seen.
- **Staleness is per-record.** Two car parks in the same response carried
  `lastupdate` an hour apart (17:10 and 18:10). A record's age must reach the
  model — reporting an hour-old vacancy as current is R-5's shape in miniature,
  and the fix is a field, not a filter.

### 5.5 `transit` needs the operator, not just the route number

Measured the same day:

    KMB  68X  data.etabus.gov.hk         177 ETA rows, real timestamps
    KMB  K73  same endpoint              EMPTY — K73 is MTR-operated
    MTR  K73  rt.data.gov.hk/.../mtr/bus 27 stops, next bus 112s

**Three operators, three endpoints, and a route number does not say which.** I
tried KMB first for K73 and got an empty result that looked exactly like "no
buses running" — the same collapse of *wrong source* into *nothing found* that
§3's three-state rule exists to prevent, reproduced by hand before the tool
existed.

So `transit` either resolves the operator itself (a lookup Nora owns, per
DEC-24 — the model must not be asked to guess an operator) or queries the
operators it knows and reports which answered. Either way, **an empty result
from one operator is not `nothing_found`** until the others have been asked.

## 6. Order, cheapest and safest first

1. **`scan_egress` split** (§2.2) — prerequisite; nothing outbound ships first.
2. **`transit`** — free, keyless, verified, bounded, no injection surface.
3. **`route`** + the resolver (§5.2) — the resolver is the hard part, which is
   why routing is not second.
4. **`search`** + **`fetch`** — last: unbounded intake, injection surface, and
   the weakest motivating example now that 2 and 3 exist.
5. **The wayfinding skill** — once there is something to bundle.

The first draft led with search. That was backwards.

## 7. What must be true before any of it ships

1. `scan_identifiers()` and `scan_reply()` separate; arguments use the former,
   `Deliverer` the latter; probed by 「搵首 haiku」 passing as a query and being
   refused in a reply.
2. Three states per tool with a `{tool, status}` counter; probed that
   `unavailable` != `nothing_found`.
3. Every tool has a `NATIVE_TOOLS` entry including §5.1 time-shiftability; one
   without fails a test.
4. `fetch` accepts only a URL from a `search` in the same turn; probed with
   `http://127.0.0.1:18080/api/v1/rpc`, which must be refused.
5. Search/fetch wrapped `untrusted="true"` with provenance; typed responses not,
   and a free-text field inside a typed response is.
6. A person's name or live position hard-blocked in a routing argument; an
   alias in a search query counted. Both probed.
7. Live location is never persisted and never reported to the group; probed.
8. Intake ceilings are constants; truncation stated in the return value.
9. R-27/R-28 registered in `design/09-decision-index.md` and `RULE_TESTS`,
   probed failing first.
10. `identity.md`/`conduct.md` say nothing about web access — under `design/17`
    the capability block is generated, so these tools update what she believes
    she can do with no prose edit. Verify rather than assume.
