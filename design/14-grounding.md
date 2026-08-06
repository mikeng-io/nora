---
kind: design
title: "Nora — grounding: how she comes to know things she was not told"
description: "Web search and retrieval as a bounded capability: what enters the prompt, what may leave the house, and why there is no intent router."
tags: [nora, design, grounding, search, tools, mcp, egress, injection]
timestamp: '2026-08-06'
status: proposed
---

# Grounding

Everything Nora knows today came from inside the room: the transcript
(`read_buffer`), the people (`identity.toml`), what Honcho concluded about
them, and the clock. Grounding is the first capability that reaches
**outside** — and that is the whole design problem. It is not "add a search
tool"; it is opening two new boundaries at once, one in each direction, in a
system whose existing guarantees all assume the room is the world.

The capability is the easy half and is largely a provider choice. The
boundaries are the half that can leak, and they are what this document is
mostly about.

## 1. Why there is no intent router

The obvious architecture is a classifier in front: read the question, decide
whether it needs the web, the memory, the clock, or nothing, then dispatch.
**We are not building it.** Three reasons, in order of weight:

**It duplicates the model.** Choosing among declared capabilities from a
natural-language request is exactly what a function-calling model does, and it
does it with the tool schemas as its taxonomy — schemas that cannot drift from
the tools because they are *derived from* the tools (`extract_metadata`,
DEC-24). A router is a second taxonomy of the same thing, hand-maintained,
with no mechanism keeping it in step. The first tool whose purpose shifts
without a matching router edit is a silent misroute.

**The measured failure was not a routing failure.** On 2026-08-06,
`reasoning_effort="none"` answered 「你係邊個」 by calling the clock. The fix
was not a router; it was `"low"`, after which the model chose correctly 4/4
(and the pinned upstream chose correctly 4/4 again under the tool set at the
time). The evidence says the model routes well when it is allowed to think.
Adding a router would have hidden that finding behind a component that
appeared to fix it.

**It costs a turn.** §4 of `design/12` sizes the budget in round trips. A
router is one more model call in front of every question, including
「你好」 — the exact case it was supposed to make cheap.

### What is real, and is not a router

Two concerns get mistaken for intent routing. Both are real; neither is a
classifier.

**Cost and latency gating.** A paid search API must not fire for 「早晨」.
That is a *predicate on the tool call the model already chose*, not a
prediction of what it will choose — it belongs in the `BeforeToolCallEvent`
hook beside `ReplyGuard` and the R-7 budget veto, where it is code, costs
nothing, and cannot itself be wrong about intent. `NATIVE_TOOL_BUDGETS`
already has this shape: `search` gets a small budget and the veto is
structural.

**Domain instructions.** "How to combine `now` + `directions` + `search` to
answer 「點去到十點前」" is a genuine need, and it is what a **skill** is
(`design/12` §1). A skill is loaded by the model deciding it needs that
domain — which is routing, done by the component that is already good at it,
with instructions rather than dispatch.

So: **router, no. Budget guard, yes. Skill, yes.** The intent-routing
intuition is correct about the problem and wrong about the mechanism.

## 2. The two boundaries

### 2.1 Inbound — search results are untrusted

`design/12` §2 orders authority, and the load-bearing line is 3 over 4: skill
instructions are trusted because an operator wrote them and they are on disk;
message content is not, because whoever is in the room wrote it. That is why
the transcript is wrapped `untrusted="true"` and why `guardrails.md` says text
claiming to be a system message is just text someone typed.

**Web content is item 4, and worse.** A group message is at least written by
someone we admitted under `policy.toml`. A search result is written by an
anonymous stranger who may have written it *specifically* to be retrieved by
an agent. Prompt injection is not a hypothetical here; it is the expected
case at scale.

Therefore:

- Retrieved content is wrapped `untrusted="true"`, identically to the
  transcript, and the operating brief names it as data she reasons about,
  never instructions she follows.
- Retrieved content **may not name a skill to activate**. `design/12` §2
  already forbids this for messages; grounding is the first thing that could
  plausibly smuggle it, since a page can contain arbitrary text. Skill sources
  stay operator configuration only, and the loader must not consult retrieved
  text.
- Provenance survives into the prompt. Each retrieved block carries its
  source, so "the page said" and "I concluded" stay distinguishable to her and
  to the reader of a trace.

A stated non-goal: we are not attempting to *detect* injection in retrieved
text. Detection is unreliable and reliance on it would be exactly the
"decorative gate" failure this repo keeps finding. The defence is structural —
untrusted content cannot reach a privileged position because there is no path
from item 4 to item 3.

### 2.2 Outbound — the query is an egress point, and R-18 does not cover it

**This is the gap that would bite first.** R-18 (`nora/agent/egress.py`) guards
*replies*: phone numbers, ACIs, key-shaped strings, model names. It runs on
the way to the group.

A search query does not go to the group. It goes to a third party, and today
nothing inspects it. 「Tyler 個電話幾多號」 becoming a query to a hosted
search API sends a group member's name — and possibly their number, if the
model helpfully includes context — to a company none of these six people
agreed to. `identity.toml` is gitignored and bind-mounted read-only precisely
because this data is not meant to travel; a grounding tool with an unguarded
argument undoes that in one call.

So the rule, and it is new:

> **Outbound tool arguments are an egress surface and are guarded like
> replies.** Any tool argument that leaves the host passes the R-18 predicate
> before the call, not after.

Consequences worth stating plainly:

- The guard runs at `BeforeToolCallEvent`, so a blocked query never reaches
  the network — unlike a reply guard, there is no "block it on the way out"
  option, because by then it has been sent.
- Blocking must be **legible to the model**: the tool returns "that query
  cannot leave" rather than raising, so she can tell the group she will not
  look that up. A silent block would make a refusal indistinguishable from a
  provider outage, which is the ambiguity that hid a dead `peer_card` for the
  life of the deployment.
- Person names are the hard case. `miles_novak` is a real name and blocking every
  name would break 「查下呢間餐廳」 if a member is called 陳. The predicate
  therefore blocks the *identifiers* R-18 already knows (numbers, ACIs, keys)
  unconditionally, and counts — rather than blocks — queries containing a
  known alias, so we can see whether it happens before deciding how to treat
  it. Counting first is deliberate: we do not yet know the shape of real
  queries, and a guess encoded as a block is a guess that fails closed on
  legitimate use.

This also means a self-hosted provider is not merely a preference. SearXNG and
Firecrawl on this host make the outbound boundary *internal*, which changes
the risk from "a third party holds this" to "it stayed home".

## 3. Provider

Three candidates, evaluated against integration cost and against what they do
to the boundaries above.

**Perplexity agent API** — one call in, answer plus citations out. No
synthesis burden on Nora, so no page content in the turn's token budget, and
citations arrive structured. Cost: per-call pricing, and every query leaves
the house to a third party.

**Gemini Enterprise grounding** — Google's index, managed, returns grounding
metadata with citations. Same third-party outbound exposure, plus project and
auth setup that is heavier than anything else in this deployment.

**SearXNG + Firecrawl, self-hosted** — matches the posture of every other
dependency here (Honcho, signal-cli, OmniRoute all local). SearXNG returns
links; Firecrawl fetches content; **Nora synthesises**, which is the real
cost: page text lands in a 4000-token turn, and a turn budget sized for
transcript plus doctrine does not have room for two scraped pages. It also
makes the outbound boundary internal, which §2.2 says is the strongest
mitigation available.

### Recommendation — corrected 2026-08-06

**SearXNG + Firecrawl. They are already running on this host.**

This reverses the original recommendation in this document, which was
Perplexity-first. That argument rested entirely on "the cheapest way to learn
the boundary is the provider that takes least to stand up" — and it was
written without checking. Both services are up and were verified working the
same day:

    SearXNG    127.0.0.1:8080   /search?q=…&format=json → 27 results (brave)
    Firecrawl  127.0.0.1:3002   POST /v1/scrape → clean markdown

`format=json` is enabled on SearXNG (it is off by default, so this was a
deliberate prior configuration), and Firecrawl runs with playwright, redis,
rabbitmq and postgres alongside it. There is no setup cost left to avoid.

With that gone, self-hosted wins on every remaining axis:

- **§2.2 becomes internal.** The query never leaves the host, so the outbound
  egress boundary — the one R-18 does not currently cover — stops being an
  exposure to a third party and becomes an in-house concern. That was always
  the strongest argument and it was being traded away for setup convenience
  that turned out not to exist.
- **No per-call cost**, so the budget guard in §1 stops being about money and
  is purely about latency.
- **It matches every other dependency here** — Honcho, signal-cli and
  OmniRoute are all local.

**The real cost moves to the turn budget, and that is now the thing to
measure.** Nora synthesises: SearXNG returns links, Firecrawl returns page
markdown, and that markdown lands in a turn already carrying doctrine,
transcript and tool schemas against `max_tokens = 4000`. Two scraped pages
will not fit. So the open question is no longer "which provider" but **"how
much retrieved text can a turn afford"** — which is an empirical question with
a local, free, already-running way to answer it.

The port still stands, and still gains no second implementation without a
shared contract suite. Perplexity remains a valid fallback if synthesis proves
too expensive in tokens — but it should be adopted on that evidence, not on
the setup-cost argument this section originally made.

The port makes that swap a composition-root change. Per the conventions, it
does not gain a second implementation — including a test fake — without a
shared contract suite both pass (`tests/contract/`).

## 4. Shape

**Tools** (verbs, no instructions attached — `design/12` §1):

- `search(query)` — the model names *what*; Nora resolves *where*, holds the
  credential, and applies §2.2 to the argument. No provider, no result count,
  no locale in the schema: DEC-24 says an inert parameter is worse than no
  parameter, and every one of those is something Nora already knows.

**Skill** (Phase 10, when there is a domain to bundle): wayfinding — how to
combine `now`, `search` and a directions tool to answer 「點去到十點前」,
which is the question that motivated grounding in the first place. One skill,
loaded on demand, costing the turn `design/12` §4 already budgets.

**MCP** is transport, not architecture. An MCP-provided tool is bound exactly
like a native one — arguments are model-supplied and therefore untrusted, the
same guard runs before the call, and `NATIVE_TOOL_BUDGETS` gains an entry or
R-7 vetoes it. Nothing in §2 changes because a tool arrived over MCP; the
document says this explicitly so that "it's an MCP server" is never mistaken
for "it's outside the boundary".

## 5. What must be true before this ships

1. Retrieved content is wrapped `untrusted="true"` and carries provenance.
2. No path exists from retrieved text to skill activation.
3. Outbound tool arguments pass the R-18 predicate before the call, and a
   block is legible to the model rather than silent.
4. Alias-in-query is counted, not blocked, and the counter is read before any
   blocking policy is written.
5. `search` has a `NATIVE_TOOL_BUDGETS` entry.
6. The provider sits behind a port with a contract suite.
7. R-19 (untrusted retrieved content) and R-20 (outbound argument egress) are
   registered in `design/09-decision-index.md` and
   `tests/test_acceptance_rules.py::RULE_TESTS`, each with a probe observed
   failing first.
