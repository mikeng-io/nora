---
kind: spec
title: "Fact store research: what was measured, what was rejected, and what is still open"
description: "A handoff, not a decision. The Dreamer-as-agent workstream researched the fact tier's storage and then handed the question to a separate workstream that owns it. This carries over what was actually measured — Graphiti read at 0.29.3 against the motivating failure, the bi-temporal shape worth keeping, Apache AGE as the option a first pass missed, and the storage-versus-retrieval distinction an earlier draft conflated — plus the four interface constraints the runtime places on whatever store wins. It deliberately makes no choice."
tags: [nora, handoff, facts, storage, graph, graphiti, apache-age, retrieval]
timestamp: '2026-08-20'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md
  - docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
---

# Fact store research — handoff

## 0. What this document is and is not

**It is a handoff.** The Dreamer-as-agent workstream researched the fact tier's
storage while settling the runtime, then the operator split the work: this
session keeps the substrate (runtime, adaptor port, tool set, instruction
substrate, budget, screening), and a separate session owns the fact synthesizer
and the graph-database question.

**It is not a decision.** An earlier draft of the keystone decided the store
outright and was wrong to; that reasoning is preserved below because the
measurements are real and re-deriving them costs a day, not because the
conclusion should be inherited. **The workstream that owns this question decides
it.**

Everything here was measured or read at source. Where something is unverified it
says so.

## 1. The four constraints the runtime places on the store

These are not preferences — they follow from decisions already made in
`2026-08-20-dreamer-as-an-agent-design.md`, and any candidate store has to
satisfy them.

1. **The sink is a port.** The runtime writes through it and does not know what
   is behind it — same relationship the Dreamer already has to `SearchProvider`.
   This is what lets the store decision land later without touching the runtime.
2. **The runtime writes, not the model** (keystone §3.2). There is no write tool
   and there must not be one: a write verb is an act verb pointed at the database
   instead of the group, and it would leave no sink to screen at. The store is
   reached by code holding a schema-validated value.
3. **Screening happens at the sink** (keystone §7). Adaptor output renders into
   Nora's prompt, and the R-18/R-28 screens sit on *her egress*, so an adaptor's
   write is a second path in. With `web_fetch` in the tool set the Dreamer will
   be reading attacker-influenceable pages, so this is load-bearing.
4. **`invalidate, never delete`.** The fact adaptor supersedes rather than
   removes. The store must retain a superseded row *with the time it stopped
   being true*, or the question "what did I used to believe, and when did that
   change?" becomes unanswerable — which is the question the motivating failure
   turns on.

## 2. The motivating failure, in one paragraph

Full account: `2026-08-20-reflection-reasoning-continuity-design.md` §1.

Nora read a **correct, sourced** fact out of a web search — Big Walk's fifth
Black Tower, from a KeenGamer walkthrough that directly answered the question.
Ten minutes later she overturned it because a **narrower** source (Polygon's
*"Best tower order"*, which ranks only the *coloured* towers) did not mention it,
and she read that silence as denial. Half an hour after that she repudiated
everything using a Wikipedia infobox about the game's release, which addressed
nothing. Twenty-nine hours later she recovered the correct answer unaided.

**Any candidate store has to be evaluated against this**, because it is the case
the fact tier exists for. The specific test: does the KeenGamer fact survive the
silence-based reversal?

## 3. Graphiti — read at 0.29.3, and why a first pass rejected it

Source read directly, not from docs. Apache 2.0, 30k stars, actively developed —
this is not a quality judgement about the project.

### 3.1 What it would give

Real and non-trivial: supersession with retained history; **bi-temporal** proper
(`valid_at`/`invalid_at` for event time, `created_at`/`expired_at` for
transaction time — those are the actual field names on `EntityEdge`);
point-in-time query; per-fact provenance via `episodes: list[str]` back to the
raw text; hybrid retrieval with reranking; **relational traversal**, which is the
one thing a table genuinely cannot do.

### 3.2 Why it probably fails *this* case

Three independent reasons, any one sufficient:

- **Retraction has no triple form.** `prompts/extract_edges.py` instructs the
  model to extract facts that *"involve two DISTINCT ENTITIES from the ENTITIES
  list"*. *"There is no black tower"* is a negative existential over one entity.
  Most likely outcome: **no edge is emitted at all**, so nothing exists to
  contradict the fabrication and the black-tower edge survives untouched.
- **Contradiction detection is one LLM judge, measured poorly on our shape.** A
  user-run graded eval ([issue #1666]) scored the stock schema **1/3 on a clear
  two-fact contradiction** and **0/3** on the cross-list invalidation-candidate
  case, which is exactly ours. `contradicted_facts` is the only thing driving
  `invalid_at`, so a missed contradiction is silent.
- **A ~30-line rule can discard the verdict anyway.**
  `resolve_edge_contradictions` invalidates only when *both* edges have non-`None`
  `valid_at` *and* `old.valid_at < new.valid_at` strictly. Equal timestamps → no
  invalidation. Missing timestamp → no invalidation. **No warning, no counter** —
  an ERROR reading as a PASS, which is the R-27 anti-pattern this repo names
  explicitly.

### 3.3 Two operational facts

- **No Postgres driver.** Neo4j, FalkorDB, or Amazon Neptune. There is an
  unmerged community RFC ([#1781], filed 2026-08-18, zero comments) — not a plan.
  `falkordblite` exists as an embedded option but requires Python 3.12+ and the
  README's own usage example for it is commented out.
- **Ingestion cost and latency.** Roughly `3 + N_entities + 2·N_edges` LLM calls
  per episode, plus 2 hybrid searches per extracted edge. `add_episode`'s own
  docstring says to run it in a background queue. `add_triplet` — the
  skip-extraction path — **still runs the `resolve_edge` judge**, so declared
  edges do not avoid §3.2's second problem.
- **Invalidated edges are NOT filtered from search by default**, and the docs do
  not document how. Arguably the behaviour you want for a retraction, but it is
  an unfiltered default rather than a designed guarantee.

### 3.4 The argument that the architecture strengthens the rejection

Worth stating because it is the part most likely to be wrong if the architecture
shifts again: Graphiti's two real value-adds are **extraction** and **automatic
contradiction detection**. The Dreamer-as-agent design replaces both — edges are
*declared* by the adaptor, and contradictions are adjudicated by a *research loop
with `fetch`* rather than a one-shot judge. So the case for adopting it rests on
traversal and bi-temporality alone.

**If that architecture changes, re-open this.**

## 4. Storage is not retrieval — the distinction a first pass missed

An earlier draft asserted *"relations do not need a graph database; an edge table
is a table."* That is true about **storage** and says nothing about
**retrieval** — traversal, multi-hop, *what connects to X* — which is precisely
where a graph engine differs. It also framed the choice as a binary that skipped
a real option:

| option | traversal | new database? | notes |
|---|---|---|---|
| edge table + `WITH RECURSIVE` | bounded depth, hand-written SQL | no | standard SQL, poor ergonomics |
| **Apache AGE** | openCypher **as a Postgres extension** | no | *the option the first pass missed* — maturity and packaging **unverified** |
| Graphiti | full, + extraction + judge | **yes** | §3 |
| no engine — load the live set | the model reads it | no | see §5 |

**Apache AGE is named here as a candidate to evaluate, not as a recommendation.**
Nobody in this workstream has verified its maturity, packaging, or how it
interacts with the existing migration/schema-parity gates.

## 5. The scale argument, which may make all of this premature

At the fact tier's expected size — tens of facts per conversation — **you do not
retrieve, you load**. The topic's live set goes into the prompt and the model
traverses it by reading. Hybrid search, RRF, MMR, node-distance reranking and
BFS are all inert at n≈30, and communities are meaningless.

So there is a real option of **deferring the engine entirely**: put retrieval
behind a port whose first implementation is a `WHERE` clause returning the live
set, and swap it when the tier's size makes multi-hop worth paying for. The agent
does not change when that happens — same `SearchProvider` pattern.

**The counter-argument, which deserves a fair hearing:** the store's *shape* is
much harder to change later than its query layer, and choosing a relational shape
now may foreclose traversal in practice even if it does not in principle. That is
the workstream's call.

## 6. The shape worth keeping regardless of engine

If the decision lands on a relational store, these are the pieces of Graphiti's
model that transfer and are cheap:

```
valid_from, valid_to        -- event time: when the fact was/stopped being true
created_at, superseded_at   -- transaction time: when we learned it
superseded_by  -> id        -- the merge record Graphiti COMPUTES AND DISCARDS
source                      -- the URL/warrant the fact was established from
instruction_sha256          -- which Dreamer instruction produced it
```

Two notes. `superseded_by` is called out because Graphiti's own weakness there is
documented ([#1771]): it computes the merge record and throws it away, so a false
merge is *"neither auditable nor reversible from the graph"*. And
`instruction_sha256` comes from the substrate spec
(`2026-08-20-dreamer-prompt-substrate-design.md` §4.3) — **that column is not
blocked by this decision and can land on the four live Dreamer tables today.**

## 7. What is genuinely open for the owning workstream

1. **Graph or relational**, and if graph, which engine.
2. **Apache AGE's maturity** — unverified by anyone here.
3. **Is traversal needed at all**, or does §5's load-the-set defer it indefinitely?
4. **Does the store shape foreclose traversal later** if relational is chosen?
5. **Fact scope** — the Standing/Facts spec argues facts are *global* because
   stability implies it (its §15), but its §16.2 leaves the contamination risk of
   a wrong global fact open. That interacts with the store choice.
6. **Retrieval as a Dreamer tool.** If the Dreamer is to *read* facts while
   researching, that is a read verb in its tool subset and needs a shape.

## 8. Status

Handoff only. No decision, no code, no `design/09-decision-index.md` entry.

**Owned by:** the fact-synthesizer / graph workstream.
**Constrains:** nothing except via §1's four interface requirements.
**Constrained by:** `2026-08-20-dreamer-as-an-agent-design.md` §3.1, §3.2, §7.

[issue #1666]: https://github.com/getzep/graphiti/issues/1666
[#1771]: https://github.com/getzep/graphiti/issues/1771
[#1781]: https://github.com/getzep/graphiti/issues/1781
