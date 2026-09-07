---
kind: spec
title: "The Dreamer is an agent, and the synthesizers are adaptors"
description: "Today the Dreamer is a clock that calls three hand-written extractors, each with its own inline prompt and one shot at a bare text-completion. Proving a fact is not a one-shot task — it is search, read, notice the source does not cover the question, search differently, cross-check. That is a tool loop with judgement in it, which is what Nora already is. This makes the Dreamer a real read-only research agent, and turns each synthesizer into an ADAPTOR supplying five things: objective, inputs, output schema, tool subset, sink. The act verbs are excluded as a hard line — a background agent that can speak to the group unprompted is a categorically different thing — and so is any write verb: the runtime writes the schema-validated return, so the model proposes and code disposes. Facts land in Postgres beside the room adaptors, not Graphiti, whose two value-adds (extraction, automatic contradiction detection) are exactly what this architecture replaces."
tags: [nora, spec, dreamer, agent, adaptor, architecture, tools]
timestamp: '2026-08-20'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-20-dreamer-prompt-substrate-design.md
  - docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
  - docs/superpowers/specs/2026-08-15-room-language-dreamer-design.md
  - design/09-decision-index.md
---

> **PARTLY DEPRECATED 2026-09-07 — every reference below to the knowledge tier (the "fact tier") or to the language tier describes code that has since been deleted.** Those parts are retained as a record of the reasoning, not as a description of the system; the rest of this document still stands.
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.


# The Dreamer is an agent

## 0. What this document is

The operator's framing, over three messages, which is the whole design:

> *"The 'dreamer' is the abstracted module, and the room consolidator, the room
> portrait synthesizer, and the fact synthesizer are all instances of the
> dreamer."*

> *"Dreamer should technically have the same toolset as Nora, because it
> requires something like a deep research agent."*

> *"it probably only needs to look up the previous two calls, the image, the
> previous transcript, and the user profile to cross-validate and create a
> synthesis … This means we're going to make Dreamer a real, abstracted agent.
> Those fact synthesizers and room language consolidator components are just
> different principles or objectives of what Dreamer is trying to achieve."*

and, on the shape: *"like a adptor"* — which is already this codebase's word for
the pattern, from the same operator, recorded in `SearchProvider`'s docstring
(`nora/domain/ports.py:1143`): *"a PORT (operator: «same as web_search —
adaptor») so another backend plugs in with no tool change."*

**This is the keystone of three documents.** It settles the architecture; the
prompt-substrate spec and the Standing/Facts spec are revised against it. §8
records exactly what it changes in each.

## 1. What the Dreamer is today

Measured against HEAD.

`dream_loop` (`nora/app/runtime.py:303`) is a clock: it ticks every 300s across
4 scopes, holds one watermark per scope, and hands the same batch to a list of
`RoomConsolidator`s plus an optional `RoomPortraitSynthesizer`. It is live and
carrying traffic.

Each participant is a hand-written extractor:

| participant | model calls | prompt | shape |
|---|---|---|---|
| `LexemeObserver` | 3 sites | 2 inline constants | one-shot completion |
| `ExchangePatternObserver` | 2 sites | 1 inline constant | one-shot completion |
| `RoomPortraitSynthesizer` | 0 | — | deterministic composition |
| `LanguageConsolidator` / `ExchangeConsolidator` | 0 | — | sequencing shims |

The model client is `_LanguageModelClient` (`nora/app/composition.py:122`) — a
bare `async def complete(self, *, prompt: str) -> str`. **No tools. One shot. No
loop.**

`LexemeObserver` does hold a `SearchProvider` (both `search` and `fetch` verbs),
injected at `composition.py:876`, with `verify_known_entities = true` live. But
it uses it for one boolean — *is this an established entity or group slang?* —
and **no Dreamer instance calls `fetch` at all.**

## 2. Why a one-shot is the wrong shape

The motivating case
(`2026-08-20-reflection-reasoning-continuity-design.md` §1) is decisive, because
the failure happened *with* search available:

- one search returned KeenGamer, which **directly answered** the question, and
  the answer was correct;
- the next returned Polygon's *"Best tower order"*, which ranks the **coloured**
  towers and is silent on the Black Tower because it is out of that article's
  scope. Read as a denial, it overturned the correct answer;
- a third returned a Wikipedia infobox about the game's release, which addressed
  nothing, and was used to repudiate everything.

Telling those three apart is not a lookup. It requires reading the page, noticing
what question it actually answers, and searching differently when it does not
cover the claim. **A single `search()` call fails exactly the way she did.**

The same is true of the cheaper adaptors. The Dreamer glossed `big walk` as
「群內約定俗成嘅散步/行路活動，可能係夜晚出去行街」 — a nighttime stroll — for what is a
co-op puzzle video game. One shot at a completion, with no way to look it up, is
how that happens.

**Proving a fact and understanding a term are the same kind of work: research.**

## 3. Runtime and adaptors

**The Dreamer is the runtime.** It owns the loop, the tool set, the budget, and
its own doctrine. It knows nothing about lexemes, exchanges, or facts.

**Each synthesizer is an adaptor** supplying exactly five things:

| | |
|---|---|
| **objective** | what to achieve, in words — the research goal |
| **inputs** | which records to look at, and their bound |
| **output schema** | the shape the answer must take, validated |
| **tool subset** | which of the Dreamer's tools this objective may use |
| **sink** | where the result is written |

| adaptor | objective | sink |
|---|---|---|
| room language | what do this room's words mean | `room_lexeme` |
| room exchange | what interaction patterns exist here | `room_exchange` |
| room portrait | synthesise the two | `room_portrait` |
| **facts** | **prove or refute a candidate claim** | fact table |

This is `RoomConsolidator` generalised. Today that port is
`consolidate(scope_id, batch: Sequence[TranscriptRow]) -> int` — append-shaped,
"extract, append, rescore, and nothing else". That shape is why `FactSynthesizer`
did not fit it (a fact adaptor consumes the fact tier, not a message batch, and
invalidates rather than appends) and why `RoomPortraitSynthesizer` had to be a
separate parameter. As an adaptor port, all four fit one interface.

**`RoomPortraitSynthesizer` stays deterministic.** Its module docstring is an
argument, not an accident: *"No model call anywhere … the inputs here are already
model-vetted, already-structurally-safe output — a THIRD model call synthesizing
prose from them would reintroduce exactly the [risk]."* It is an adaptor whose
tool subset is empty and whose objective is executed in code. Making everything
an agent is not the goal; making everything *the same interface* is.

### 3.1 The sink is a port; what backs it is decided elsewhere

**This document does not choose the fact tier's store.** That question — graph
versus relational, Graphiti, Apache AGE, retrieval and traversal — is owned by a
separate workstream, and the research behind it is handed over in
`2026-08-20-fact-store-research-handoff.md`.

What this document *does* fix is the interface, because the runtime depends on
it and it constrains whatever store wins:

- **The sink is a port**, one of the adaptor's five fields. The runtime writes
  through it and does not know what is behind it — the same relationship the
  Dreamer already has to `SearchProvider`, and the reason a store decision can
  land later without touching the runtime.
- **The runtime writes, not the model** (§3.2). Whatever the store is, it is
  reached by code holding a schema-validated value, never by a tool the model
  calls.
- **Screening happens at the sink** (§7), so the port's write path is where the
  identifier and coordinate rules apply — not inside the store.
- **`invalidate, never delete`.** The fact adaptor supersedes rather than
  removes, so the store must be able to retain a superseded row with the time it
  stopped being true. This is a requirement on the store, not a choice of one.

An earlier draft of this section decided the store outright (Postgres, not
Graphiti) and, worse, conflated **storage** with **retrieval** — "an edge table
is a table" answers the storage half and says nothing about traversal, which is
where a graph engine actually differs. It also presented a false binary that
skipped Apache AGE (openCypher as a Postgres extension, no second database).
That reasoning is preserved in the handoff document rather than discarded, and
the decision belongs to the workstream that owns it.

The room adaptors' sinks are already Postgres (`room_lexeme`, `room_exchange`,
`room_portrait`) and this document does not move them.

### 3.2 How an adaptor writes — the return value is the write

**There is no write tool, and there must not be one.** A `write_fact` tool is an
act verb pointed at the database instead of at the group, and §4 makes "no act
verbs" a property of the runtime rather than a setting. It would also defeat §7:
if the model writes directly there is no sink left to screen at.

The path is:

```
adaptor declares an output schema
  → agent researches with read-only tools
  → returns a structured result
  → runtime VALIDATES it against the schema
  → runtime SCREENS it (§7)
  → runtime WRITES it to the sink
```

**The model proposes; the runtime disposes.** This is the same asymmetry the
Standing/Facts spec already applies one level up (the debrief may append, only
the background pass may invalidate), and it is how the existing consolidators
already work — `LexemeObserver` extracts, the consolidator persists. The model
returns data; code writes it. Nothing about that changes when the model gains a
tool loop.

It also narrows the injection surface materially. With a write tool, a crafted
page reached through `web_fetch` could *cause a write*. With a schema-validated
return, an injection can only influence the content of a structured value that
then has to pass the sink screen — a real risk, but a much smaller one.

**Invalidation is the same answer.** The fact adaptor never deletes. It returns
*"supersede fact X, because Y, per source Z"* and the runtime performs it, which
keeps the append-only/dispose asymmetry intact at both levels.

## 4. The tool set — read verbs in, act verbs out

**The hard line: the Dreamer may look, and may not act.** A background process
that can speak to the group unprompted is a categorically different and more
dangerous thing than one that can only read. This is not a default to be
configured — it is a property of the runtime.

**In** — the operator's list, plus what the existing adaptors already need:

- `recall_lookups` — *the previous calls*
- `media_fetch` — *the image*
- `load_transcript` — *the previous transcript*
- `about` / `ask_memory` / `lookup_person` — *the user profile*
- `search_memory`, `group_members`
- `web_search`, `web_fetch`
- ~~`pulse_*`~~ — **excluded, see §9.6.** MCP-sourced, so nothing carries its
  classification. Named here because it is what the Dreamer *wants*; the
  mechanism cannot grant it yet.
- `now`, `calculate`

**Out** — every act verb, **and any write verb.** A tool that persists an
adaptor's result would be an act verb pointed at the database instead of at the
group; §3.2 is why the runtime writes the validated return instead.

**CORRECTION (2026-08-20).** An earlier draft of this section hand-listed six act
verbs: `send_reply`, `send_to`, `send_gif`, `remind`, `cancel_reminder`,
`make_artifact`. **That list was four short.** It omitted `create_event`,
`update_event`, `rsvp_event` and `cancel_event`, which had been added to the
registry (DEC-117) after the mental model behind the list was formed.

A plan written against that list verbatim **would have handed a background agent
four write verbs.** The implementation plan caught it while sequencing; nothing
was built on it.

Derived from `NATIVE_TOOLS` rather than recalled, there are 34 native tools, of
which **ten** act:

```
remind  cancel_reminder
create_event  update_event  rsvp_event  cancel_event
make_artifact
send_reply  send_to  send_gif
```

`suggest_venue` recommends without creating and `search_gifs` searches while
`send_gif` is the terminal verb — both read.

**This is the argument for the field, made by its own absence.** The list above
is illustrative and dated; it is not the specification. The specification is that
the Dreamer's set is **derived from a field on the tool**, so a tool added
tomorrow is classified by whoever adds it and cannot silently join the Dreamer's
set. A hand-list is exactly the pre-DEC-29 stale-catalogue failure this repo
already fixed once, and the draft above is a fresh instance of it.

**The field goes on `NativeTool` (`nora/agent/tools.py`), not `ToolSpec`.** An
earlier draft named `ToolSpec` (`nora/agent/ports.py`) — wrong: `ToolSpec`
carries `budget`, `terminal`, `time_shiftable`, `disclose_failure` and
`timeout_seconds`, but **not** `cacheable`, and all four derived subsets
(`terminal_tools()`, `cacheable_tools()`, `live_only_tools()`,
`web_tier_tools()`) read `NativeTool`. That is where the pattern lives and where
the new field belongs.

**`pulse_*` is not in `NATIVE_TOOLS` at all** — it arrives over MCP, which has no
row to carry the flag, so it cannot be classified. **Resolved 2026-08-20: MCP
tools are excluded from the Dreamer until they have a classification surface**
(§9.6). Fail-closed, at the cost of the game-state family the motivating case
most wants.

`ToolSpec` (`nora/agent/ports.py:74-83`) already carries `budget`, `terminal`,
`time_shiftable`, `cacheable`, `disclose_failure`, `timeout_seconds`, and the
registry already derives frozenset subsets from those fields —
`terminal_tools()`, `cacheable_tools()`, `live_only_tools()`, `web_tier_tools()`.
**So the read/act split is one more field and one more derived subset, not a new
mechanism.**

`terminal` is close but not sufficient: it marks turn-*ending* tools
(`send_reply`, `send_gif`), and `remind` / `send_to` / `make_artifact` act
without being terminal. The new field must mean "this tool only reads", and the
Dreamer's set is derived from it — never hand-listed, or it goes stale the way
the pre-DEC-29 tool catalogue did.

## 5. Where the code lives

`tests/test_boundaries.py::test_strands_import_boundary` allows `strands` in
`nora.agent` and `nora.compute` only. `nora.language` is also barred from
`nora.config` by an import contract. An agent needs `strands`, so:

- **the Dreamer runtime lives in `nora/agent/`**, beside `factory.py`, which is
  the package already allowed to construct agents;
- **the adaptors stay in `nora/language/`**, holding ports only — objective text,
  input selection, output schema, sink. No `strands`, no `nora.config`.
- **composition wires them**, exactly as it already wires `_LanguageModelClient`
  and `SearchProvider` into `LexemeObserver`.

This keeps the existing boundary rather than widening it, and it is the same
split the Dreamer already uses for its model client — the pattern is proven in
this codebase, not imported from elsewhere.

**CORRECTION (2026-08-20): `create_agent` cannot build the Dreamer, and this
section read as though it could.** The implementation plan found it while
sequencing, and it is the largest hidden cost in the design.

`nora/agent/factory.py`'s `create_agent` requires a `ReplyGuard` — a deliverer, a
set of terminal verbs, and the `tool_choice` relaxation — none of which a
read-only research loop has. Worse, `create_model_provider` hardcodes
`tool_choice="required"`, which that module documents as *"load-bearing, not a
tuning knob"*. Under `required` the model must call something on every pass, so
**a loop with no terminal verb can never stop by itself**: the guard's
`BeforeModelCallEvent` hook is the only thing that ends the loop, and it ends it
by observing that a terminal verb was called (DEC-21, DEC-32).

The Dreamer has no terminal verb by construction — §4 removes every act verb, and
the terminal ones are act verbs. So it needs its own stopping condition and its
own guard, not a reused `ReplyGuard`. That is two implementation tasks this
document implied were free.

## 6. Budget

`dream_loop` ticks 300s across 4 scopes. An agent loop per adaptor per scope is
unbounded unless capped, and the failure would be quiet — the Dreamer has no
user waiting on it, which is exactly why a runaway there is harder to notice than
a runaway on the tag path.

R-8's shape applies: turns, total tokens, output tokens, plus a wall-clock
timeout — and the timeout is not optional. `composition.py:145-164` already
records why, from the last time this was gotten wrong:

> **A bounded timeout.** Every other model call in the codebase is explicitly
> bounded and says why … This one ran on the bare `AsyncOpenAI` default (600s x
> retries) until now — and unlike a tag turn's timeout, a hung Dreamer call is
> QUIET…

Per-adaptor budgets, not one global one: proving a fact is worth more calls than
glossing a word, and a single ceiling would either starve the first or overpay
for the second.

## 7. Output screening

**Dreamer output reaches the model.** `room_lexeme` renders into
`<room_language>`, `room_exchange` into `<room_exchange>`, `room_portrait` into
`<room_portrait>`, and facts will render into their own block.

Nora's identifier and coordinate screens (R-18, R-28) sit on *her egress*. An
adaptor writing to its sink is a second path into the prompt that does not pass
them. With `web_fetch` in the tool set this matters more, not less: the Dreamer
would be reading attacker-influenceable pages and writing distillations of them
into a durable store that renders into every later turn.

**So screening moves to the sink**: an adaptor's output is screened before it is
written, with the same rules, and a named test per adaptor. This is the
`room_language.sense` concern (2026-08-20 audit, F14) generalised — and it is
the reason it must be settled here rather than per-adaptor later.

## 8. What this changes in the other two documents

**`2026-08-20-dreamer-prompt-substrate-design.md` — under-scoped, revise.**
It is written for a one-shot world: "three prompts, two files", versioning three
inline constants. Under this architecture what needs versioning is (a) the
Dreamer's **own doctrine** as a research agent and (b) each adaptor's
**objective** — which is much closer to `nora/doctrine`'s shape than to three
string constants. Its §4.3 (a `prompt_sha256` provenance column on every row an
adaptor writes) survives unchanged and remains the piece to keep even if the
rest slips.

**`2026-08-20-reflection-reasoning-continuity-design.md` — revise §5.3 and §8.2.**
`FactSynthesizer` is described there as "Synthesizer-shaped, not
Consolidator-shaped", chosen because `RoomConsolidator`'s append contract did not
fit. Under the adaptor port that reasoning is obsolete: it is an adaptor like the
others, and the shape mismatch it was working around no longer exists. Its §8.2
(what each tier reads) becomes the fact adaptor's *inputs* and *tool subset*
fields. Nothing in `Standing`, the debrief, or the propose/dispose split changes
— those are on the tag path and untouched by this.

## 9. Open questions

1. ~~**Which model does the Dreamer run?**~~ **DECIDED 2026-08-20 (operator):
   `deepseek-v4-flash` — the same model Nora runs.** `LanguageConfig.model`
   already defaults to `ollamacloud/deepseek-v4-flash`, so this needs no config
   change; the decision is to keep it rather than reach for something heavier for
   the research loop.

   **This makes §2's central claim falsifiable, which is the reason it is worth
   recording as a decision rather than a non-change.** §2 argues the motivating
   failure was *situational, not a capability limit*: the same model read a
   scoped source's silence as denial and capitulated under `屌你` on the tag
   path, and the Dreamer is meant to do better **because it is differently
   situated** — no social pressure, no turn budget, a loop, and `fetch` — not
   because it is smarter.

   Running the *same model* is the honest test of that claim. If a Flash research
   loop still cannot distinguish a source that contradicts from one that merely
   does not cover, then "differently situated, not smarter" was wrong and the
   design needs revisiting — not the model swapped quietly to hide it.

   **Task 2.9 in the plan therefore changes shape rather than disappearing**: it
   was "measure and choose"; it is now "verify Flash holds under a research
   loop", against the corpus in §2. A negative result reopens this.
2. ~~**Adaptor scheduling.**~~ **CLOSED 2026-08-20 — a requirement, not a
   question.** The fact-store workstream keys the fact adaptor on a *workspace*
   (`Conversation.workspace`, DEC-105) while every existing adaptor is keyed on a
   `scope_id`. That is not a wider or narrower batch — it is a **different key**,
   and `_dream_once` cannot host it unchanged. **Adaptors declare their own key,
   and the runtime schedules per adaptor.**
3. ~~**Does the fact adaptor use the shared watermark?**~~ **ANSWERED: no.**
   Follows from (2). Precedent: `RoomPortraitSynthesizer` already participates
   with no watermark/lag/quiet (`runtime.py:352`); the fact adaptor is the second
   such participant and the first with a non-`scope_id` key.

   **Hazard the plan surfaced, recorded here because it bites at cutover:**
   `_dream_once` hardcodes a single watermark named `"language"` and `exchange`
   rides it. An adaptor renamed during migration gets `watermark_for → 0` and
   **re-reads every room from the beginning.**
4. ~~**Failure isolation.**~~ **DECIDED 2026-08-20 (operator): isolate PER
   ADAPTOR.** `dream_loop`'s try/except is already per-scope so one scope cannot
   starve another; adaptor isolation is a second axis and is added.

   A throwing adaptor must not cost the tick for the others. The asymmetry that
   settles it: the adaptors have different *value densities* — a fact adaptor
   researching the open web has far more ways to fail than a lexeme extractor
   reading its own message batch, and letting the riskiest one silence the
   cheapest is backwards. `RoomPortraitSynthesizer`'s branch already behaves this
   way, so this generalises existing behaviour rather than inventing it.

   **Implementation consequence the plan flagged:** `_run_one`'s backoff key has
   to move with the isolation boundary. A per-adaptor try/except with a
   per-*scope* backoff key would let one failing adaptor back off every adaptor
   in that scope — the bug this decision exists to prevent, reintroduced one
   layer down.
5. **Does the Dreamer get its own doctrine files, or a single objective file per
   adaptor?** Settled in the substrate spec once revised.
6. ~~**How is an MCP-sourced tool classified read-or-act?**~~ **DECIDED
   2026-08-20 (operator): MCP tools are EXCLUDED from the Dreamer until they have
   a classification surface.** `pulse_*` arrives over MCP, which has no row to
   carry the field the read/act split derives from, so it cannot be classified —
   and an unclassifiable tool must not be granted by default.

   **This is fail-closed, and it costs something real.** The Dreamer loses
   `pulse_*` — the game-state family, which is directly relevant to the
   motivating case: the save file is what actually settles a Big Walk progress
   claim. A fact adaptor without it can research the open web but cannot read the
   group's own game state.

   That cost is accepted rather than hidden. The alternative — defaulting
   unclassified MCP tools to "read" — would mean an MCP server added later could
   silently hand a background agent a write verb, which is precisely the failure
   §4's derived-not-hand-listed rule exists to prevent, and which §4's own
   four-verb omission already demonstrated is not hypothetical.

   **Reopen** by giving MCP tools a classification surface; the tool set in §4
   stops naming `pulse_*` until then.
7. **The fact tier's store and its retrieval** — NOT open here. Owned by the
   fact-store workstream; see `2026-08-20-fact-store-research-handoff.md`. The
   only constraint this document places on it is §3.1's interface.

## 10. Status

Design only. No code. No `design/09-decision-index.md` entry — a candidate.

**Blocks:** the prompt-substrate work, and `FactSynthesizer`.
**Blocked by:** nothing.

**Not in scope:** anything on the tag path. `Standing`, the debrief, the
propose/dispose split, and the doctrine changes shipped by the 2026-08-20 audit
are unaffected — this document is about the background loop only.
