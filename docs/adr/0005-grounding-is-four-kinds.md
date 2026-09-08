# 0005 — Grounding is four kinds, not one

- **Status:** accepted (the framing). The tools themselves are **proposed** —
  only the prerequisite has been built.
- **Date:** 2026-08-06
- **Index:** DEC-30 (`scan_egress` split into `scan_identifiers` /
  `scan_reply`), R-22 (a tool that could not answer says so in its return
  value), R-18 (egress guards replies), R-21 (scope enforced at assembly),
  `design/proposed/14-grounding.md` (the document this records the reframing
  of). Grounding's own rules are **R-27/R-28**, unassigned.

## Context

Grounding is the first capability that reaches outside the room — everything
else Nora knows comes from the transcript, `identity.toml`, Honcho and the
clock.

**The first draft of `design/14` was wrong in a way worth recording.** It
treated grounding as one capability, `search(query)`, and spent its length
choosing a search provider. Two operator questions broke that framing:

    「K73 幾時到」            — when does the K73 get here
    「由 A 去 B 邊條路最快」  — what is the fastest route from A to B

Neither is answerable by searching or scraping anything. Searching for "K73"
returns pages *about* the route; it does not return the bus.

**Four kinds, verified live 2026-08-06:**

| kind | source | trust | time |
|---|---|---|---|
| live structured | `rt.data.gov.hk`, KMB/MTR, car parks | typed, **trusted** | **now only** |
| routing / geo | Google Routes, Places | typed, **trusted** | **any time** (`arrive_by`) |
| web search | SearXNG → Brave et al. | **untrusted** | whenever indexed |
| page fetch | Firecrawl | **untrusted** | whenever crawled |

    MTR bus K73 ETA   status 0, 27 stops, next bus 112s     free, no key
    KMB ETA           same API family, valid envelope        free, no key
    SearXNG :8080     27 results (engine: brave)             local, PROXYING
    Firecrawl :3002   /v1/scrape -> clean markdown           local
    car park vacancy  560 car parks with live counts         free, no key

**The two transport kinds compose rather than compete.** Google says "take K73
from stop D020" from a *schedule*; `rt.data.gov.hk` says "that bus is 112
seconds away" *live*. Neither alone answers 「幾點出門」. Combining them is
instructions, not capability — which is what a **skill** is, and the first
domain that has ever qualified (`design/12` §1 said none did).

**Corrections to the first draft, recorded because each was believed:**

- **"The query never leaves the host" was FALSE.** SearXNG is a metasearch
  *proxy* with no index of its own — the same measurement line that was cited
  as proof of locality says `engine: brave`. Self-hosting buys that *Nora's
  client identity* does not reach Brave. Real, but weaker, and the outbound
  rules are **not** downgradable on the strength of it.
- **The token argument used the wrong currency.** It reasoned against
  `max_tokens`, the per-call **output** ceiling (2000). Retrieved text lands in
  the **input**, governed by `total_tokens = 64000`. The two are separate
  fields for separate reasons and the argument was numerically meaningless.
- **R-19/R-20 were already taken.** R-20 is the forged-quote rule, registered
  and enforced; R-19 is reserved by `design/11`. Grounding's are R-27/R-28.
- **The scrape verb was never declared.** §3 required Firecrawl while the tool
  list held only `search(query)` — a second verb living in prose with no
  schema, no budget entry, no URL authority. R-7 default-denies any tool absent
  from `NATIVE_TOOLS`, so it would not have run at all.

**The trap that makes route validation a correctness requirement, measured the
same day:**

    KMB  68X  data.etabus.gov.hk           177 ETA rows, real timestamps
    KMB  K73  same endpoint                EMPTY — K73 is MTR-operated
    MTR  K73  rt.data.gov.hk/.../mtr/bus   27 stops, next bus 112s

**Three operators, three endpoints, and a route number does not say which.**
Worse, MTR's API answers `status: 0` with an empty stop list for a route that
does not exist — so **"no such bus" is byte-identical to "no bus right now"**.
This was reproduced by hand, before any tool existed, by trying KMB first for
K73 and reading the empty result as "no buses running". It is the same collapse
of *wrong source* into *nothing found* that hid a dead `peer_card` for the life
of the deployment.

## Decision

**Grounding is four capabilities with different trust, time and cost
properties. It is not one search tool, and the kinds do not substitute for one
another.**

Four verbs, each with its own `NATIVE_TOOLS` entry (budget + `when`, or R-7
default-denies it):

- **`transit(route)`** — live ETA. Free, keyless, bounded typed JSON, no
  injection surface, no egress question. Ships first.
- **`route(origin, destination, mode, arrive_by)`** — Google. Paid, external,
  keyed. `arrive_by` is the point.
- **`search(query)`** — SearXNG. Titles, snippets and URLs only.
- **`fetch(url)`** — Firecrawl, and the URL must come from a **prior `search`
  result in this same turn**, never free-form from the model.

**Three consequences of the framing, each of which is a rule rather than a
preference:**

**1. Trust is per-kind, not uniform.** Search and fetch results are wrapped
`untrusted="true"` with provenance preserved and **no path from retrieved text
to skill activation** — a group message was at least written by someone
`policy.toml` admitted; a page may have been written specifically to be
retrieved by an agent. **Typed API responses are not wrapped**: a government ETA
API returning typed fields is closer to `listGroups` than to a web page, and
wrapping it would miscategorise it. If such a source ever returns a free-text
field, *that field* is wrapped even though its envelope is typed. Stated
non-goal: we do not *detect* injection. The defence is structural.

**2. Time-shiftability is a property of the tool and belongs in
`NATIVE_TOOLS`.** Every tool answers about the present; the operator's actual
scenario (dinner at 20:00, asked at 15:00) is about the future. Google Routes
accepts `arrive_by`; `rt.data.gov.hk` cannot time-travel at all. Nora must know
which sources can be asked about a future moment, or she will quote a
112-second bus wait for a bus leaving at 19:30.

**3. An empty result from one operator is never `nothing_found`.** `transit`
either resolves the operator itself — a lookup Nora owns, per DEC-24, because
the model must not be asked to guess an operator — or queries the operators it
knows and reports which answered. Each tool returns **three states** — `ok` /
`nothing_found` / `unavailable` — with a `{tool, status}` counter (R-22's
shape). "The API is down", "there is no such route" and "there is no bus right
now" must never be one answer.

**Outbound: tool arguments are an egress surface, and the rule is not
uniform.** R-18 guards *replies*, not arguments. Routing makes this concrete
rather than theoretical: **a route query inherently contains two locations**,
and 「由陳生屋企去我公司」 must never become a Google query resolving a named
person to a home address. There is no legitimate version of that request. So:
identifiers (numbers, ACIs, keys) blocked unconditionally on every tool; a
person's name or live position in a routing origin/destination **hard-blocked**;
a person's alias in a *search* query **counted, not blocked** — we do not know
the shape of real queries yet, and a guess encoded as a block fails closed on
legitimate use.

**Built 2026-08-06 (DEC-30) — the prerequisite, and only the prerequisite.**
`scan_egress` is split into `scan_identifiers()` (tool arguments) and
`scan_reply()` (identifiers plus model names), and the old name is **deleted
rather than deprecated** — a third name that does one of the two jobs is how
the wrong one gets called later. Its `_MODELNAME` pattern matches
`gemini|haiku|sonnet|opus|claude|grok`, which is correct for replies (she must
never name her model) and catastrophic for queries: 「搵首 haiku」 would be
refused, and the guard fails closed on the *whole* reply. It also turned out to
be a live bug, not only a prerequisite: `about`/`recall` was filtering Honcho
conclusions through the *reply* predicate, so every durable memory naming an
LLM was silently withheld from a group of software engineers.

**Order, cheapest and safest first:** `scan_egress` split (done) → `transit` →
`route` + the place/person resolver → `search` + `fetch` → the wayfinding
skill. The first draft led with search. That was backwards.

## Consequences

**Better.** The kinds are nameable, so a question can be routed to the source
that can answer it rather than to the one that has a tool. The `status: 0` trap
is written down before the tool exists, which is the only time writing it down
is cheap. And the four corrections above are recorded as *corrections* — the
locality claim in particular is the sort of thing that gets restated
confidently a month later as a reason to relax an outbound rule.

**Worse, and accepted.**

- **Four tools instead of one is four schemas in every request.** Schemas
  already cost 1,222 tokens, about 25% of a call. Four more verbs is a real,
  permanent, per-call cost paid on every tag whether or not anything is looked
  up, and it competes for the same router attention ADR 0004 measured — a
  four-name expansion is four more chances for a name to collide with a
  sibling, and none of them has been measured.
- **`transit` must own an operator lookup**, which is a table of Hong Kong bus
  operators inside Nora that nobody wants to maintain and that will go stale.
  The alternative — ask the model — is forbidden by DEC-24 and would reproduce
  the K73 mistake at machine speed.
- **`fetch` is deliberately crippled.** Constraining it to a URL from a prior
  `search` in the same turn is the whole SSRF mitigation: SearXNG, Honcho,
  signal-cli (`:18080`), OmniRoute (`:20128`) and Firecrawl itself all listen
  on localhost, so a model-chosen URL makes self-hosted Firecrawl a pivot into
  Nora's own internals. It also means she cannot fetch a URL a *human pasted
  into the group*, which is the single most obvious thing a user would expect.
  That is a real capability loss and it is being taken on purpose.
- **`search`'s alias rule fails open.** Counting rather than blocking a
  person's alias in a query is a deliberate choice to leak-and-measure rather
  than block-and-guess, and it will leak something before we know enough to
  block it. The counter is the only thing standing between that and silence.
- **Intake is bounded by constants, not by a component** (see ADR 0003). Four
  new unbounded intakes arrive on a turn budget with no accounting, guarded by
  four independently-chosen numbers.
- **Life360 makes the resolver a scope boundary, not a wrapper.** Live location
  is the most sensitive data in this system — a phone number identifies, a
  position tracks. In-group disclosure is fine (operator decision, 2026-08-06:
  everyone in the group is in the same Life360 circle and can already see each
  other), **but the guarantee must be mechanical**: report a position only into
  a conversation whose members are a SUBSET of the circle. Today they match;
  the day someone joins the Signal group who is not in the circle, this must
  hold by construction. That is R-21's shape one level up, and it is work
  nobody has started.
- **Two measured caveats on car parks shape the tool rather than block it.**
  Pricing was **NOT** in the vacancy response (`data=vacancy` returns counts
  and timestamps only) — the first draft claimed occupancy *and* pricing from
  one call, and pricing appears to need the `data=info` variant, **unverified**.
  And staleness is **per-record**: two car parks in one response carried
  `lastupdate` an hour apart (17:10 and 18:10), so a record's age must reach
  the model. That is a field, not a filter.

## Rejected

**One `search(query)` tool.** The framing the first draft used. It cannot
answer either question that prompted the work, and choosing a provider — which
is what that draft spent its length on — is the least important decision in the
area.

**Leading with search.** It has the largest injection surface, the least
bounded intake, and now the weakest motivating example.

**Detecting prompt injection in retrieved text.** Detection is unreliable and
relying on it is the decorative-gate failure this repo keeps finding. Wrapping
plus no-path-to-activation is structural and testable by reverting it.

**Wrapping typed API responses `untrusted="true"` for uniformity.** It would
miscategorise a government ETA endpoint as adversarial input, and `design/17`'s
argument is that miscategorised things fail silently.

**Relaxing the outbound rules because SearXNG is self-hosted.** The premise was
false; see the corrections above.

**Sampling Life360 positions into a time-series store** so routing can predict
departure. It is continuous surveillance of five friends persisted to disk, it
multiplies the exposure by every second it runs, and it *still* would not know
when Grant finishes work — Life360 gives position, not intent. **The cheap
version is to ask.** She is in the group; 「Grant 你幾點收工？」 is one message
and beats any forecast.
