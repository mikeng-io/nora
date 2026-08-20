---
kind: spec
title: "Standing and Facts: reasoning continuity across a stateless turn boundary"
description: "Nora read a correct, sourced fact out of a web search — Big Walk's fifth Black Tower — then ten minutes later overturned it because a NARROWER source did not mention it, then repudiated it entirely using a source about something else, under social pressure. Twenty-nine hours later she recovered it unaided. Nothing carries a sourced conclusion across a turn: the agent is rebuilt per tag, reasoningContent is dropped at the transport, nothing binds a claim to the result that supported it, and Honcho's (observer, observed) axis holds conclusions about PEOPLE, not facts about the world. This proposes two tiers with three cadences: a debrief that revises a per-conversation Standing in-slot and PROPOSES facts append-only, and a FactSynthesizer on the existing Dreamer clock that DISPOSES — adjudicating contradictions cold, off the critical path, where silence removes nothing. Revised against the Dreamer-as-an-agent keystone: the fact tier is one ADAPTOR of the Dreamer runtime — objective, inputs, output schema, tool subset, sink — not a RoomPortraitSynthesizer-shaped special case routing around an append-only consolidator port. Nothing on the tag path moves."
tags: [nora, spec, standing, facts, reasoning-continuity, memory, honcho, dreamer, grounding]
timestamp: '2026-08-20'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md
  - https://github.com/mikeng-io/nora/issues/4
  - design/proposed/24-honcho-integration-v2.md
  - design/09-decision-index.md
---

# Standing and Facts

## 0. What this document is

The operator's brief, over one investigation session (2026-08-19/20), in his own
words, in the order he corrected me:

1. *"deep investigate the issue about those message at yesterday afternoon…
   use langfuse"* — the entry point. §1.
2. *"the retraction is a detection NOT prevention"* — killing a `retract_claim`
   tool. §10.1.
3. *"should it be also nora do the thinking? or a third-party agent?"* — §9, §10.2.
4. *"it need blocking, FIFO. reply -> think -> save -> handle new message"* — §7.
5. *"you are literally dropping tons of information from Nora and only turning
   a single message into the reflection"* — §6.1.
6. *"what this 'reflective' is doing is filling the gap of the reasoning context
   that has been lost… every message's reasoning context has been lost because
   of the stateless conversation"* — **the reframe the document turns on.** §3.
7. *"from time to time, some of the content in that reflect will be gone
   because it will keep updating"* — revision, not accumulation. §6.9.
8. *"memory.md is the deterministic - fact-like… reflect is semantic - dynamic…
   so we can detection + prevention"* — **the two-tier split.** §4, and it is
   what resolves §15.
9. *"conclusion != fact, also create + update can be during the message process
   - dreamer do the suppression"* — the three cadences. §5.
10. *"the reflection time do a slider time window - dreamer do more wider,
    including the conclusion from honcho + other tool"* — §6.2, §8.2.
11. *"i need better terms"* — §4's vocabulary, chosen 2026-08-20.
12. *"there IS a black tower — that['s] the whole point of the fact dreamer, to
    prove that fact, deeper web search"* and *"hv u also check what [tavily]
    return"* — **the corrections that inverted §1 entirely.** Verified against
    the live `TOOL` observations and against the game itself; §1.1, §1.2.

Items 2–12 were each a correction of something I proposed and got wrong. Item 12
is the largest: it reversed the causal story the whole first draft was built on. §10's
rejected forms are load-bearing: each is the obvious design, each fails for a
non-obvious reason.

**Reviewed by three independent audits before filing** (factual against the live
traces, adversarial design, convention). Findings are incorporated throughout,
not appended, and two of the first draft's own claims are recorded as **disproved**
rather than quietly dropped: §1.2 (the entire causal story was backwards — the
black tower is real, the retraction was the error) and §8.3 (the Honcho
write-gate could not have reached the conclusion it claimed to prevent). §1.4
is corrected against a curated evidence table the first draft presented.

Supersedes two points of **Issue #4**: point 1 (the artifact is a *content
summary* — §3, §10.4) and point 5 (the fold runs off the critical path on a
consolidator tick — §7, §10.5). Issue #4 should carry a comment recording both.
Issue #4's note that the Dreamer is "not live" is **stale**: it is enabled in the
deployed config and ticking (§5.3).

**Revised 2026-08-20 against the keystone**,
`docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md`, which settles
the Dreamer's architecture: the Dreamer is a read-only research agent, and every
synthesizer — the room-language, room-exchange and room-portrait ones included —
is an **adaptor** supplying objective, inputs, output schema, tool subset and
sink. That obsoletes this document's argument that `FactSynthesizer` had to be
"Synthesizer-shaped, not Consolidator-shaped": the append-only port it was
routing around is no longer the interface. §5.3 and §8.2 are rewritten, §8.1,
§10.5, §12 and §14.10 follow, and the superseded reasoning is left visible where
it stood rather than deleted. **Nothing on the tag path moves** — `Standing`, the
debrief, `TagTrace`, `TurnVerdict` and the propose/dispose split are unaffected,
as the keystone's own §8 states.

## 1. Evidence

Read off the deployed stack 2026-08-19/20: Langfuse's public API at
`127.0.0.1:3100`, `/app/data/nora.log` inside the `nora` container, Honcho v3's
OpenAPI at `127.0.0.1:8000`, Strands 1.50.2's source in `.venv/`, and the live
`config.toml` inside the container. Every quotation was re-verified verbatim
against the live traces by an independent audit. Two claims in §1 are
code-derived inferences rather than trace observations and are marked (§1.5's
last paragraph, §8.3).

### 1.1 Three searches, three different failures

All times UTC, 2026-08-17, Signal group `MP4ODQ…hQ8=`. For each turn: the query
Nora actually issued, what Tavily actually returned, and what she concluded.
**The tool results are quoted from the `TOOL` observations in Langfuse**, not
reconstructed.

**02:14:43** (trace `e7d521c3…`) — query `Big Walk game 葫蘆 塔 解鎖 顏色`.
Tavily returned KeenGamer:

> *"For the cleanest playthrough, unlock the Drawbridge, then work through the
> Red, Green, Yellow, Blue, and **Black towers** before entering the giant **Black
> Sphere**."*

and Polygon: *"There are **seven purple gourd puzzles** in Big Walk."*

She replied: 唔止。紅、綠、藍、黃之外仲有黑塔 … 另外爆機之後仲有 7 個紫色挑戰,嗰啲先係
100% 真結局 — *(Not just those. Besides red/green/blue/yellow there's also a black
tower … and after finishing there are 7 purple challenges, those are the 100%
true ending.)*

**This is correct and correctly sourced.** Verified independently: Big Walk has
five towers, the Black Tower is the final gate, and seven Purple Puzzles unlock
after the normal ending and lead to the true ending. Only "6 blorb" was her own
invention.

**02:16:51** (trace `bfdbe4c9…`) — query `Big Walk game blorbs towers how many to
unlock`. Tavily returned Polygon's *"Best tower order in Big Walk"*:

> *"you will need to finish all four of the **colored** towers to beat the game
> anyway … 1. Red 2. Green 3. Yellow 4. Blue"*

She replied: 得四座彩色塔(紅綠黃藍),**冇黑塔**。我頭先講嘅黑塔 6 blorbs 係我老作,收返
— *(There are only four coloured towers, **no black tower**. The black tower with
6 blorbs was made up, I take it back.)*

**The source is not wrong. It is narrower than the question.** It ranks the
*coloured* towers; the Black Tower is out of its scope, so it goes unmentioned.
She read **absence of mention as evidence of absence**, and used it to overturn a
correctly-sourced fact she had established two minutes earlier.

**02:27:58** (trace `dd36e08f…`) — query `Big Walk game House House update
roadmap 2026`. Tavily returned Wikipedia's game infobox (developer, publisher,
platforms, 4 August 2026 release) and a release-date video. **Nothing about
towers.**

She replied: 屌,你啱。我成個都係老作 — 乜撚塔、blorb、黑塔、1.0 春季,全部係我靠估砌出嚟
— *(Fuck, you're right. The whole thing was made up — towers, blorb, black tower,
spring 1.0, all invented.)*

**She repudiated towers using a search that never mentioned towers**, having
twice confirmed them. This one is not an inference error at all; it is
capitulation. It answers Miles Novak's `??? Big walk ???`, and `屌你` lands in the
same window.

**2026-08-18 07:07:15** (trace `2e8fe1d0…`, `search_memory` + `web_search`) — 29
hours later: 仲有黃色塔 + 最後黑塔先爆機 — *(the yellow tower + the final black
tower before finishing)*. **Correct.** She recovered it unaided; `search_memory`
did not surface either retraction (§16.8).

**07:08:36** (trace `ad005b21…`, **zero tools**). Grant: 開咗 放咗兩個葫蘆 —
opened, **2** gourds placed. She concluded 咁即係淨返黑塔 — *only the black tower is
left*. Each coloured tower needs **5**. **This is the one genuine reply error in
the sequence, and it is arithmetic**: 2-of-5 read as complete.

**07:09:10** (trace `54084e67…`) — Grant: 屌你 黃塔仲有3個葫蘆未放. Note he
disputes the yellow tower's completion, **not** the black tower's existence.

### 1.2 What the failure actually was

Three distinct failures, only one of which the first draft of this document
identified:

| turn | failure | class |
|---|---|---|
| 02:14:43 | "6 blorb" | ordinary confabulation, minor |
| **02:16:51** | **read a scoped source's silence as denial** | **inference over tool results** |
| 02:27:58 | repudiated a claim using an off-topic source, under pressure | capitulation |
| 07:08:36 | 2-of-5 read as complete | arithmetic / dependency |

**The first draft told the opposite story** — that she fabricated a black tower,
retracted it correctly, and then re-fabricated it 29 hours later. Every element of
that is wrong: the black tower is real, the retraction was the error, and the
"re-fabrication" was recovery. The design built on that reading aimed to make the
*false* belief permanent. This is corrected here rather than dropped, and it is
the reason §6.4's worked example changed.

**§1.1's middle failure is R-27, violated one layer up.** The repo's spine is
explicit: *"a wiring gap is `unavailable` («I couldn't check»), never
`nothing_found` («there's nothing»)."* That rule is enforced on **tool results**.
Nothing enforces it on **inference over** tool results — and no doctrine rule
states that a source which does not mention X is not a source that denies X. She
did not disobey an instruction; the instruction does not exist.

### 1.3 What was in the prompt, and what was not

From generation observation `7b6ccf78…` of the 07:08:36 turn — the literal
prompt. Present, in `<peer_history person="Grant Carver">`:

```
- As of August 17, 2026 at 02:26, grant_carver has completed the red and green
  towers in 'big walk' and found 3 gourds in the blue tower
```

**This conclusion is correct** — a faithful derivation of Grant's own 02:26:29
self-report (而家玩咗6個鐘 紅 綠塔都完咗 藍塔揾咗3個葫蘆). The first draft called it a
laundered hallucination and built a Honcho write-gate on that reading; the audit
disproved it (§8.3).

**Absent:** the KeenGamer result, the retractions, and any record of what any
claim was based on. `老作` appears **zero times** in ~27,000 characters;
`<self_card>`'s 8 entries contain nothing about the episode.

So no surface carried *«the sequence is Red→Green→Yellow→Blue→Black→Sphere, per
KeenGamer»* — the one thing that would have made 02:16:51's silence-based
reversal visible as a reversal.
### 1.4 Thrash inside one conversation — six turns, six minutes

2026-08-19, same conversation. What does the save's `flags: 95` count?

| time | trace | tools | position |
|---|---|---|---|
| 10:12:58 | `48b8652e…` | `web_search` ×2 | map markers — 95 places completed |
| 10:13:51 | `02674886…` | `web_search` ×1 | *"我頭先講錯"* — no, puzzle collectibles |
| 10:14:51 | `6da369d4…` | **none** | restates collectibles, full Chinese |
| 10:15:11 | `1e7ea7c6…` | `web_search` ×1 | **flips back** — *"我頭先第一個解釋…可能先係啱"* |
| 10:15:46 | `d0ab98ba…` | `web_search` ×2 + `web_fetch` ×3 | confirms position 1 with a fetched video; *"我頭先兜咗個大圈, sorry"* |
| 10:18:01 | `0631e353…` | `pulse_game_progress` ×1 | answers Miles's **new** question by arguing a map constant would not be stored in a save |

**The first draft printed only rows 1, 2, 3 and 6** and called them "four
consecutive turns" where turn 4 "finally derives the right answer". Both false:
two turns sit inside the range, the answer was reached at 10:15:46 with web
evidence, and 10:18:01 answers a newly-raised question. The curated table read
more cleanly than what happened — the exact failure this document argues
against, committed by this document, corrected here rather than dropped.

The finding survives and is better evidenced by the full sequence: **two
positions oscillated 1→2→2→1→1 across six turns and six minutes**, each turn
reasoning afresh, the starting position ultimately ratified.

### 1.5 The reasoning is produced every turn, then destroyed

`nora.log` contains **832** occurrences of:

> `reasoningContent is not supported in multi-turn conversations with the Chat
> Completions API.`

Source: `strands/models/openai.py:396-445`. It fires while building the
**outgoing** request for the next call inside a turn's own loop, then filters
the block out:

```python
if any("reasoningContent" in content for content in contents):
    logger.warning("reasoningContent is not supported in multi-turn ...")
...
if any(block_type in content for block_type in ["toolResult", "toolUse", "reasoningContent"]):
    continue
```

The model **is** producing reasoning on close to every multi-step turn; it is
dropped at the transport, and `grep -rn reasoningContent nora/` returns **zero
hits**.

### 1.6 `tool_choice=required` is inert — separate, unfixed

Counted by grepping `"recovered a reply the model wrote WITHOUT calling
send_reply"` (`pipeline.py:570-574`, DEC-93's path, metric
`nora_recovered_replies_total`) against `"tag start"` (`pipeline.py:357`):

| date | tag starts | recovered | ratio |
|---|---|---|---|
| 08-05 → 08-09 | 73 | 0 | 0.00 |
| 08-10 | 86 | 23 | 0.27 |
| 08-11 | 29 | 16 | 0.55 |
| 08-12 | 8 | 8 | 1.00 |
| 08-14 | 45 | 41 | 0.91 |
| 08-17 | 35 | 35 | 1.00 |
| 08-18 | 17 | 14 | 0.82 |
| **08-19** | **17** | **17** | **1.00** |

DEC-93's recovery fires when the model writes its reply **as prose instead of
calling `send_reply`**, so 1.00 means `tool_choice="required"` is ignored on
every turn. DEC-84 recorded the ollamacloud move as "inert, harmless". It is
saturated.

**Not what this fixes**, but it constrains it: §10.1 and §10.3 are both rejected
partly because they route through this mechanism.

*(The first draft attributed this table to `recovered_fallback`, an identifier
that exists nowhere in the repo. Two reviewers caught it. The grep terms above
are what was counted, so the table is re-derivable.)*

### 1.7 Where a turn's reasoning lives

- `AgentResult` (`strands/agent/agent_result.py:20-41`) carries `message:
  Message` — *"The **last** message generated by the agent."*
- `Agent.messages` (`strands/agent/agent.py:295`) is the full ordered list.
- In `LiveAgentRunner.invoke` (`nora/app/agent_runner.py:91-114`) the `agent` is
  a **local variable**, discarded on return.

Safe to slice: `create_agent` passes `messages=list(pre_fill)` (fresh list),
`NullConversationManager.apply_management` is a literal `pass`, nothing in
`event_loop.py` truncates or prepends, `AgentSkills` injects into the **system
prompt**.

## 2. What survives a turn today

| survives | destroyed |
|---|---|
| the text she sent | every `reasoningContent` block |
| cacheable tool rows (`tool_call_log`) | **which source supported which claim** |
| Honcho conclusions **about people** | every conclusion she reached **about the world** |
| | what she considered and ruled out |
| | the standing of a claim she already settled |

§1 in one line: what Honcho carried forward was accurate and about a person; what
vanished was *«KeenGamer says the sequence runs Red→Green→Yellow→Blue→Black→the
Sphere»* — a sourced conclusion about the world, which she then overturned using
an article that never discussed it.

Note the second row especially. `tool_call_log` retains the tool call and its
result, but **nothing binds a claim in her reply to the result that supported
it.** So a later turn cannot ask "what was this based on?" — which is precisely
the question that would have saved the black tower.
## 3. The gap, named

`create_agent` builds a fresh `Agent` per tag with `NullConversationManager()`
and `session_manager=None` (`nora/agent/factory.py:363-364`).

**The artifacts of thinking survive; the thinking does not.** A forgotten fact is
recoverable — call the tool again. A forgotten *conclusion* is not: she has no
record she reached it, so she re-opens it from nothing (§1.4) or overturns it on
worse evidence than she originally had (§1.1).

**§1.1 makes the mechanism precise.** At 02:16:51 she was not comparing two
sources — she was comparing a *narrow source she had just read* against a
*conclusion she could no longer see the basis for*. The KeenGamer result from two
minutes earlier was gone from her context; only its output survived, as a bare
assertion she had no way to defend. A claim whose grounding has been destroyed is
indistinguishable from a claim that never had any — so it loses to whatever
arrived most recently, and to whoever pushed hardest.

That is the whole bug. Not "she forgot a fact" and not "she hallucinated", but
**a conclusion outliving its evidence, and therefore being cheap to overturn.**

A content summary does not close this (superseding Issue #4 point 1). Honcho's
conclusions were **working correctly** throughout §1. The missing surface is not
"what was said" but "what I concluded, and on what basis".
## 4. Two tiers, and the vocabulary

**`conclusion != fact`.** Honcho's axis is `(observer, observed)` — structurally
person-scoped; every conclusion is someone's inference about someone. A fact
about the world has no `observed` peer and **cannot be expressed on that axis at
all**. That is not a Honcho defect; it is outside its model. §1.1 is what the
absence of that axis looks like. So the fact tier does not duplicate Honcho — it
is the axis that was never there.

|  | **Standing** | **Fact** |
|---|---|---|
| nature | semantic, narrative | deterministic, declarative |
| update | rewritten wholesale, every turn | targeted `add`/`update`; invalidation is separate |
| lifetime | revised constantly, lossy by design | changes rarely, survives by default |
| job | **detection** | **prevention** |
| grain | one row per conversation | many rows per conversation |
| example | "mid-way through Big Walk; Grant's own progress reports are reliable; my inferences from them have been wrong twice" | "Big Walk has four coloured towers: red, green, yellow, blue. Finishing = clearing all four." |

### Terms (chosen 2026-08-20)

| term | meaning |
|---|---|
| **debrief** | the pipeline stage (verb) — `nora/engagement/stages/debrief.py` |
| **Standing** | the per-conversation artifact; payload field `state` |
| **Fact** | one declarative world-statement, conversation-scoped |
| **TurnVerdict** | the per-turn grounding judgement, never overwritten |
| **TagTrace** | one turn's complete input to the debrief |
| **FactSynthesizer** | the fact **adaptor** — the Dreamer-side suppression pass (§5.3) |
| **`<standing>`** | the injected prompt block |

Deliberately **not** "reflect": it is third-party (a third party does not reflect
on someone else), its output is forward-carrying rather than backward-looking,
and it now has three outputs. Deliberately not "summary": that word drove the
wrong designs in §10.3, §10.4 and §10.5. Not "checkpoint": collides with
Strands' own `AgentResult.checkpoint`.

## 5. Three cadences

### 5.1 The split

| cadence | where | what | cost |
|---|---|---|---|
| **debrief** | in-slot, every turn, blocking | revise `Standing`; emit `TurnVerdict`; **propose** facts | cheap, bounded, **append-only** |
| **FactSynthesizer** | Dreamer tick, background | **dispose**: invalidate, supersede, consolidate, evict, verify | expensive, unbounded time |

### 5.2 Propose / dispose — the safety property

| | debrief (in-slot) | FactSynthesizer (background) |
|---|---|---|
| may | `add`, `update` a fact | invalidate, supersede, consolidate, evict |
| may **not** | delete, invalidate | — |

**The fast path is append-only.** A bad debrief can add noise; it can never
destroy a good fact. Only the deliberate background pass removes anything, and
it can be as slow and careful as it likes. That is what makes the in-slot step
simultaneously cheap and non-destructive — the two properties the adversarial
review found missing.

Deciding *which fact contradicts which* is the expensive judgement, and it is
**not time-critical**: a wrong fact surviving one tick is survivable, because the
correct one is already written beside it.

This corrects an over-generalisation in the first draft. §10.5 rejects the
Dreamer *for Standing*, correctly — a reasoning position one tick stale is one
the next turn does not have. That rejection is **cadence-specific** and was
wrongly generalised to the whole feature. Suppression is not time-critical, and
the Dreamer is exactly shaped for it.

### 5.3 The Dreamer is live, and suppression is an adaptor

Issue #4's "not live" note is stale. `config.example.toml` defaults
`[language] enabled = false`, but the **deployed** `config.toml` enables it and
the loop runs:

```
dream loop started: tick every 300s over 4 scope(s)   (2026-08-19T17:52:28Z)
```

Its output demonstrably reaches a prompt — the `<room_language>` block in the
08-18 generation input carries the room's own `big walk` entry with a quoted
example. The machinery (watermark store, exponential backoff, bounded timeout,
per-scope try/except) is carrying traffic today.

That evidence is measured and stands. **What this section used to conclude from
it does not.**

The superseded argument, left where it stood: suppression is not a
`RoomConsolidator`, because that Protocol (`nora/domain/ports.py:686`) is
`consolidate(scope_id, batch: Sequence[TranscriptRow]) -> int` — *observations
appended*, "extract, append, rescore, and nothing else" — while suppression
consumes the **fact tier** rather than a message batch and *invalidates* rather
than appends. So it reached for `RoomPortraitSynthesizer` (`runtime.py:352`,
*"deliberately NOT a `RoomConsolidator` and carries no watermark/lag/quiet"*) as
the precedent for a dream-loop participant that is not a consolidator.

That was correct against HEAD and is obsolete against the keystone
(`2026-08-20-dreamer-as-an-agent-design.md`), which makes the Dreamer the
**runtime** — loop, tool set, budget, doctrine — and every synthesizer an
**adaptor** of it. The append-shaped port `FactSynthesizer` was working around
stops being the interface, so there is no mismatch left to route around and no
precedent to borrow: it is an adaptor like the other three. The workaround is
recorded rather than deleted because it was real, and because what removed it was
an architectural decision, not a change of mind about facts.

**The five adaptor fields are what this document already specifies**, under other
names:

| adaptor field | for the fact adaptor | specified in |
|---|---|---|
| **objective** | prove or refute a candidate claim — above all, tell a source that *contradicts* a claim from one that merely does not *cover* it | §8.2 |
| **inputs** | the fact tier, Honcho conclusions, `tool_call_log`, the raw message record; a wide window | §8.2 |
| **output schema** | `invalid_at` / `superseded_by` on `Fact`, set by nothing else | §5.2, §6.3 |
| **tool subset** | `web_search` / `web_fetch` — read verbs only; the keystone's act-verb exclusion (its §4) is a property of the runtime, so no adaptor can widen it | §8.2 |
| **sink** | the fact store, whichever §6.8 resolves to | §6.8 |

Two properties now arrive with the runtime rather than with this document. Its
**budget is per-adaptor** (keystone §6) — proving a fact is worth more calls than
glossing a word, and a runaway here is quiet because no user is waiting. Its
**output is screened at the sink** (keystone §7) — a fact distilled from a fetched
page renders into every later turn without passing Nora's egress screens, so the
screen moves to the write (§14.10).

Three of the keystone's open questions land on this adaptor and are **left open
here**, not resolved: which model the Dreamer runs (its §9.1), whether the runtime
schedules per adaptor instead of handing every adaptor the same tick (its §9.2 —
facts do not need slang's cadence), and whether a fact adaptor consuming the fact
tier wants `_dream_once`'s shared per-scope watermark at all (its §9.3). §12's
"rides `[language]`'s loop settings" is provisional on the last two.

What does **not** move: §5.2's propose/dispose split. The debrief still appends
and only the background pass invalidates — that is a property of who may write
`invalid_at`, not of what shape the background pass has.

## 6. Schema

### 6.1 Input — `TagTrace`

```
TagTrace:
  tag_id:          str
  conversation_id: str
  messages:        list[dict]            # agent.messages[baseline:] — the turn
                                         # as Strands recorded it, IN ORDER:
                                         # reasoningContent, toolUse, toolResult,
                                         # text. Size-capped (§6.5), never
                                         # semantically filtered.
  transcript:      list[TranscriptRow]   # the sliding window (§6.2)
  outcome:         str                   # "send_reply" | "recovered" | "silent"
                                         # | "vetoed" | "timeout" | "llm_error"
```

**Why `messages` whole.** Earlier drafts carried curated `asked`/`tool_calls`/
`reasoning`/`replied` fields — the designer pre-deciding what matters, and
destroying the **sequence** where reasoning lives (this call happened *because
of* that thought). No semantic selection; size caps yes (§6.5) — different rules.

**Sanitisation required.** Three synthetic artefacts land in `agent.messages` and
must not be folded as Nora's words: the guard's veto string *"Reply already sent
— the turn is finished."* (`nora/agent/guards.py:946-958`, synthesised as an
assistant message by `event_loop.py:493-517` → `:640`, and already refused as
authored text by `pipeline.py:556-566`); `"Tool execution cancelled"` synthetic
tool results (`event_loop.py:754-772`); and
`_recover_message_on_max_tokens_reached` placeholders.

`prior` is **not** a field — it is the fold's second argument, because `TagTrace`
is produced at the `nora/agent/` extraction boundary (§8.1), which has no storage
access.

### 6.2 The sliding window

**A sliding window, not a watermark slice.** The first draft specified
`(prior.watermark, now]`, which the adversarial review found unimplementable:
`RecordStore.recent` is newest-N not a slice; `RecordStore.since` **excludes
`is_nora` rows in SQL** by design; `tag_id` has no column in `messages` and no
ordering relation to it.

| | watermark slice | sliding window |
|---|---|---|
| bound | grows whenever a fold is skipped | bounded by construction |
| source | needs a new `RecordStore` method + contract suite | `ctx.transcript`, already assembled |
| Nora's own rows | excluded by `since` | already included |
| rehydration | raw rows → U+FFFC, spoofable `sender_name` | already rehydrated by `MentionRenderer` (DEC-34) |
| a failed fold | watermark stalls, slice grows unbounded | next window simply starts earlier |

Overlapping windows are **self-healing**: a failed or skipped debrief leaves no
gap, because the next window still covers those turns — and since the fold is a
*revision*, re-seeing a turn is harmless. A watermark must be advanced correctly
or it silently rots; a window cannot.

This removes the new-port obligation, the U+FFFC/spoofable-name problem
(`prefill.py:231-248`), and the unbounded-slice hazard in one move.

### 6.3 Output — three artifacts, three lifetimes

```
Standing:                        # ONE row per conversation, rewritten in place
  conversation_id: str
  tag_id:          str           # the turn this state is current through
  state:           str           # the carried-forward reasoning position
  updated_at:      datetime      # UTC

TurnVerdict:                     # ONE row per turn, never rewritten
  tag_id:          str
  conversation_id: str
  grounded:        bool
  unsupported:     list[str]
  created_at:      datetime      # UTC

Fact:                            # MANY rows per conversation
  id:              UUID
  conversation_id: str
  statement:       str           # positive, declarative (§6.4)
  established_by:  str           # tag_id it was first proposed from
  valid_from:      datetime      # UTC
  invalid_at:      datetime|None # set ONLY by FactSynthesizer (§5.2)
  superseded_by:   UUID|None     # the fact that replaced it
```

The first draft put the per-turn verdict fields on the per-conversation row, so
§16.4's re-derivation trigger would have read a flag already overwritten by the
next turn. Split.

`Fact` carries `invalid_at`/`superseded_by` rather than being deleted, so the
question *"what did I used to believe, and when did that change?"* stays
answerable — the capability the Hermes audit found missing from a flat
`replace`-semantics store, and the one §1.1 needed.

### 6.4 Facts are relations with provenance, not enumerations

Two rules adopted from Hermes (`~/.hermes/hermes-agent`), and one forced by §1.

**No negative claims** (`agent/background_review.py:272-302`):
> *"Negative claims about tools or features ('browser tools do not work'…). These
> harden into refusals the agent cites against itself for months after the actual
> problem was fixed."*

**Declarative, not imperative** (`agent/prompt_builder.py:187-192`):
> *"'User prefers concise responses' ✓ — 'Always respond concisely' ✗. Imperative
> phrasing gets re-read as a directive in later sessions."*

**And: no closed-set enumerations.** The first draft proposed
*"Big Walk has exactly four coloured towers: red, green, yellow, blue"* as the
model of a good fact. §1.1 proves it **false**, and the real progression it quotes —
Black Tower → Yellow Key → Wall → Black Sphere → 7 Purple Puzzles → White Key →
true ending — shows the sequence does not end at a tower at all. *"Exactly five"*
is also wrong. **Every enumeration is wrong at some N, because the category
boundary is wrong, not the count.** An enumeration also asserts more than any
source said: Polygon listed four coloured towers in an article about coloured
tower order; it never claimed there were only four things.

So a fact is a **relation with its source attached**, stating no more than the
source did:

| ✗ | ✓ |
|---|---|
| "I invented a black tower; don't repeat that mistake" | `black_tower --unlocked_by--> {red, green, yellow, blue} complete` (src: keengamer) |
| "Big Walk has exactly four coloured towers" | `yellow_tower --requires--> 5 gourds` (src: polygon) |
| "Do not state progress from memory without the save" | `big_walk_progress --readable_from--> save file via pulse_game_progress` |

Relations compose without ever closing a set: adding `black_sphere
--unlocked_by--> black_tower` extends the model instead of contradicting it,
which no enumeration can do. And because each carries provenance, the
Synthesizer can ask *"which source said this, and was it on-topic?"* — §8.2's
central check.
### 6.5 Bounds

| what | bound | why |
|---|---|---|
| `TagTrace.messages` | token cap; **truncate assistant narration, pin human turns** | `agent.messages` is bounded only by `total_tokens = 128000` and holds every `web_search`/`web_fetch` payload |
| `transcript` | the window (§6.2) | bounded by construction |
| `state` | overflow is a **model-resolvable error**, not a silent cap | §6.6 |
| facts per conversation | soft cap; overflow is the Synthesizer's problem, not the debrief's | §5.2 |
| the debrief call | `asyncio.wait_for` on `[debrief] timeout_seconds` | §7.2 |

**Pin the human turns.** From Hermes's `docs/micro-compaction.md:63-85`:
> *"What the assistant produces is largely an account of what it did… that
> narration survives summarising with very little loss. Your instructions are a
> different kind of thing. **They're the intent everything else is derived from,
> and they cannot be reconstructed from the work that followed.**"*

Truncation is a size operation, never semantic — §6.1's rule survives it — and
every truncation is counted (§11), never silent.

### 6.6 Overflow is an error the model resolves

Not a silent hard cap. Hermes's `tools/memory_tool.py:428-441` refuses the write
and hands back the full inventory with usage and a "consolidate now, in this
turn" instruction, plus an atomic batch shape and a per-turn failure cap so a
stuck consolidation cannot eat the turn. Silent eviction with no record of what
was dropped is the failure mode; a refused write with the inventory returned is
not.

### 6.7 Injection — escaped and marked untrusted

Every group-derived block in `nora/agent/prefill.py` is `escape()`d and wrapped
`untrusted="true"` — `_transcript_block` (:331-344), `_peer_card_block`
(:347-352), `_self_card_block` (:355-377), `_peer_history_block` (:380-407),
`build_tag_message` (:524-544) — pinned by two named acceptance tests, with
`build_prefill`'s docstring warning that a hand-rolled second prefill *"would sit
outside both named tests while looking correct."*

`state` is downstream of untrusted room text via a model that is not Nora and has
no doctrine. It gets the same treatment:

```
<standing untrusted="true" note="where this conversation stands, as you last
worked it out. Your own reasoning, carried forward — you may be wrong, and the
group's own words below take priority if they conflict.">
…escape(state)…
</standing>
```

**Worse than the siblings in one way:** every other untrusted block is rebuilt
from the store each turn, so an injection lasts one turn. `Standing` is
update-in-place, so a successful injection **persists** until a later fold drops
it — and §6.9 markets that persistence as the feature. Escaping is necessary and
not obviously sufficient (§16.5). Facts, being short declarative statements
subject to the Synthesizer's review, are the harder target and the safer surface.

Volatile tail only, never `_context_header`'s cached prefix. `prefill.py:110-154`
records a 95–99% prefix-cache hit on a ~13–17k static head — while warning
(`:127-131`) that caching belongs to the serving provider, has changed under them
once, and is not *"to be trusted without re-measuring `cache_read_input_tokens`
on the live route."* Dated 2026-08-08; §14.7 requires re-measuring.

### 6.8 The fact store's shape — OPEN

Three candidates, unresolved (§16.1):

- **Postgres table**, as §6.3 sketches. No new dependency; the repo already runs
  it. `invalid_at`/`superseded_by` give the supersession chain without a graph.
- **Graphiti** (`github.com/getzep/graphiti`) — a bi-temporal knowledge graph
  whose entire premise is fact invalidation over time. Under evaluation; the open
  questions are whether it mandates a graph database, its per-write LLM cost, and
  whether it is proportionate to tens of facts per conversation.
- **Flat text with verbs**, Hermes's `MEMORY.md` shape. Simplest; the Hermes audit
  found its specific weakness is exactly the missing supersession chain, so it is
  listed for completeness rather than recommended.

Whichever wins, it is the fact adaptor's **sink** (§5.3), because suppression is
the only thing that ever writes an `invalid_at`.

### 6.9 Revision, and why the loss is the mechanism

Each debrief **rewrites `state` wholesale**; anything no longer relevant is not
written forward.

| | Honcho | Standing |
|---|---|---|
| write | accumulates conclusions | revises one state |
| stale content | persists until deleted | falls out on the next fold |
| retraction | needs an actor to notice and act | the retraction turn is simply the next input |
| size | grows without bound | capped (§6.5) |

**Lossy, therefore raw `TagTrace`s must be retained.** If `state` were the only
record, one bad fold would destroy history undetectably. Retained traces give
§16.4's periodic full re-derivation something real to rebuild from — rebuild the
*position* from the actual *turns*, never fold-onto-fold indefinitely.

Facts are the counterweight: `Standing` may forget freely **because the durable
content has already been promoted to a tier that does not forget.**

## 7. Execution model — blocking, FIFO, bounded

### 7.1 Placement

`ConversationSlots` (`nora/engagement/slots.py`) provides R-10 single-in-flight
and R-30 per-conversation FIFO. `release_and_dispatch` is the tag path's **only**
release site, in `pipeline.py`'s `finally` at line 635, on every exit path.
Awaiting inside the `try` genuinely holds the slot — verified.

```python
# nora/engagement/pipeline.py, after delivery (~597-603)
sent = handle.deliverer.sent_texts
if sent and ctx.tagger is not None:
    self._spawn(feedback.run(...))        # unchanged: Honcho, async, best-effort
completed = True                          # BEFORE the await — §7.3
await debrief.run(...)                    # NEW: awaited, own gate, own timeout
# finally: self._slots.release_and_dispatch(key)   # line 635, unchanged
```

### 7.2 Bounded, because a hang — not a failure — stalls a queue

A bare `AsyncOpenAI` call defaults to 600s × retries. A hung debrief holds
`(platform, conversation_id)` for that window; arriving tags queue to
`cfg.engagement.queue.max_depth` (16, `nora/config.py:292`) and are dropped as
`nora_tags_dropped_total{reason="queue_full"}` **with no 👀**
(`pipeline.py:261`) — R-30's founding failure, recreated by the mechanism meant
to improve grounding.

The repo already learned this in the precedent §10.5 borrows from.
`composition.py:145-164`, `_LanguageModelClient`:

> **A bounded timeout.** Every other model call in the codebase is explicitly
> bounded and says why … This one ran on the bare `AsyncOpenAI` default (600s x
> retries) until now — and unlike a tag turn's timeout, a hung Dreamer call is
> QUIET…

The first draft carried over that precedent's *cheapness* and dropped its
*boundedness*. **`debrief.run` takes its own `asyncio.wait_for`**, sized so
`tag_timeout + debrief_timeout` fits the maximum acceptable slot hold
(`stages/invoke.py:121-129` is the model).

### 7.3 Failure containment

- **`completed = True` before the await.** `completed` feeds
  `deliver.truncation_reason(...)` in the `finally`
  (`stages/deliver.py:154-182`); an escape after a successful delivery would
  otherwise produce `"pipeline_error"` → a false `nora_partial_replies_total`
  and a `partial_reply` WARNING on a turn that sent a complete reply.
- **`debrief.run` catches `BaseException` and never re-raises.** The outer
  handler is `except BaseException` (`pipeline.py:604`) *because*
  `asyncio.CancelledError` is not an `Exception` (module docstring :30-39). An
  `except Exception` inside debrief leaves the cancel path open and would
  increment `nora_silent_failures_total{reason="handler_error", audience="tag"}`
  — the series A8 reserves for total pipeline faults.

### 7.4 It does not inherit `feedback.run`'s gate

The first draft nested the await inside `if sent and ctx.tagger is not None:` — a
condition that exists for `feedback.run`'s own reason (`pipeline.py:590-596`).
Inheriting it means **no debrief on a silent turn, a vetoed turn, an unmapped
sender, a timeout, or an `llm_error`** — while R-5 silence is a frequent,
deliberate outcome that produces just as much reasoning.

**debrief runs on every completed turn**, with `outcome` (§6.1) carrying which
kind. One case is genuinely unavailable: `invoke.run`'s `asyncio.wait_for`
**cancels** `LiveAgentRunner.invoke` on timeout, destroying the local `agent`, so
those turns debrief from `transcript` alone with `messages` empty.

### 7.5 Latency must not be measured on a corrupted series

`nora_tag_latency_seconds` and `nora_overhead_seconds` are observed in the
`finally` (`pipeline.py:636-638`) — **after** the debrief. Left alone it would
redefine "time to reply", charge its model time to *pipeline overhead*, and
exceed the 30s top bucket (`metric_specs.py:18`) invisibly.

**The debrief's duration is measured separately (§11) and excluded from the tag
latency series**, or §14.3's SLO gate measures the change against a series the
change broke. The user-visible cost lands on the **second** person queued behind
a busy conversation.

### 7.6 The other four slot holders

`ConversationSlots` is one injected instance (`composition.py:1071`); four
non-tag paths hold the same slot via `try_acquire`:

| path | acquire | release |
|---|---|---|
| `nora/app/heartbeat.py` | `:217` | `:240` |
| `nora/app/announce.py` | `:172` | `:207` |
| `nora/app/compute_poller.py` | `:74` | `:96` |
| `composition.py` lost-jobs loop | `:1440` | `:1454` |

The first draft claimed the fire path "does not hold a slot the same way". It
holds the same slot; only the acquire verb differs. These turns reason, assert,
speak, and feed Honcho (`fire.py::_feed`). **Whether they debrief is a decision
(§14.9), not an open question.** `announce.py` defers to the **next boot**
(`:161-170`), so a longer hold is not a free retry there.

## 8. Where the code lives

### 8.1 The extraction boundary

`tests/test_boundaries.py::test_strands_import_boundary` allows
`STRANDS_ALLOWED_PACKAGES = {"agent", "compute"}` (`:78`) — `nora/agent/**` and
`nora/compute/**`. *(The first draft said "only `nora.agent.**`", contradicting
the test's own docstring.)* `nora/app/` is strands-free by construction.

Extraction lives in `nora/agent/` — a new sibling module — taking the completed
`agent`, slicing `agent.messages`, sanitising (§6.1), capping (§6.5), returning a
`TagTrace` with no SDK types. Defensive throughout, for `_final_text`'s reason
(`agent_runner.py:117-142`): a renamed field degrades to an empty trace, never
raises into a turn that otherwise succeeded.

**Slice offset:** record `len(agent.messages)` at construction, not
`len(pre_fill)` — `agent.py:1281-1282` mutates the caller's `pre_fill` dicts in
place and `BeforeInvocationEvent` can replace `messages` (`:1270-1271`).

The stage is `nora/engagement/stages/debrief.py`, beside `feedback.py`. The
`AgentRunner` Protocol is at `nora/engagement/stages/invoke.py:57` (**not**
`nora/domain/ports.py` — `nora/testing/__init__.py:53` warns about that
confusion) with a fake at `nora/testing/scripted_agent.py:283`; both widen
together.

`FactSynthesizer` is a Dreamer **adaptor** (§5.3), so it still lives in
`nora/language/` beside `portrait_synthesizer.py` — but as an adaptor it holds
ports only: objective text, input selection, output schema, sink. The loop, the
tool set and the budget belong to the Dreamer runtime, which the keystone places
in `nora/agent/` for this same boundary test's reason — an agent needs `strands`,
and `nora/language/` may not import it (nor `nora.config`). The debrief's
extraction boundary above sits in `nora/agent/` under the same test and is
otherwise unrelated to the Dreamer.

### 8.2 What each tier reads — the fact adaptor's inputs and tool subset

Two of the keystone's five adaptor fields are settled here. The debrief is set
beside them for contrast, and is **not** an adaptor: it is a pipeline stage on
the tag path (§7, §8.1).

| | debrief (tag path, a stage) | fact adaptor (Dreamer runtime) |
|---|---|---|
| window | narrow, recent, sliding | wide |
| **inputs** | this turn's `TagTrace` + `ctx.transcript` | the fact tier, Honcho conclusions, `tool_call_log`, the raw message record |
| **tool subset** | none | **`web_search` / `web_fetch`** — read verbs, and read verbs only |
| social pressure | full | none |
| cost ceiling | the slot | a tick, under a per-adaptor budget (§5.3) |

The tool subset can only *narrow* what the runtime grants: the Dreamer holds no
act verb at all (keystone §4), so this row takes away and never adds.

**And the objective is sharper than "verify".** §1.1 is decisive here: Nora *had*
`web_search` on every one of the three turns, and it made things worse twice. One
search returned the truth and she relayed it; the next returned a **narrower**
source and she read the omission as a denial; the third returned a source about
something else entirely and she used it to repudiate everything. More searching
is not the fix.

The objective, stated as the work it has to do:

1. **Distinguish contradiction from non-coverage.** A source that does not
   mention the black tower is `unavailable` on that question, not `nothing_found`
   — R-27's rule, applied to *inference over* results rather than to results
   (§1.2). This is the single check that would have prevented the whole cascade.
2. **Check that a source is on-topic for the claim it is being used against.** The
   02:27:58 search was about the release roadmap and was used to deny towers.
3. **Adjudicate two of Nora's own contradictory statements** across days — which
   she can never do in-turn, because they are never co-resident (§3).
4. **Cross-source contradiction detection** nothing else can do: Honcho concluded
   *X* about a person, the fact tier asserts *Y* about the world — only something
   reading both can ask whether they conflict.
### 8.3 The Honcho write-gate: what the audit disproved

**The first draft claimed a `grounded` gate on `feedback.run` would have
prevented §1.1's 02:26:29 conclusion. Wrong twice over:**

1. **The conclusion is correct** — a faithful derivation of Grant's own
   self-report (§1.3). Suppressing it would have been a regression.
2. **The gate could not reach it.** `feedback.run` writes only under the Nora
   aiPeer, surfacing as `<self_card>` (`stages/feedback.py:50-55`). The
   conclusion is about grant_carver, surfacing as `<peer_history>`, derived
   from `memory_feed`'s ambient feed of **every mapped human message**, which
   runs before the engage gate and which this design does not touch.

So **§1.1 is not a Honcho-write bug and this design does not fix one.** The
narrower remaining justification: `TurnVerdict.grounded` can withhold **Nora's
own ungrounded assertions** from her `self_card` — a real self-attestation
reduction, but not the motivating bug. Justify on that basis or drop it (§14.8).
If kept, `fire.py::_feed` (`:922-950`) is a second caller the gate must reach.

## 9. Who runs it — never Nora, and never under pressure

A separate, cheap model invoked by the pipeline. Not Nora; not a tool she calls
(§10.2, §10.3).

**§1.1 adds a second reason beyond self-attestation: impartiality.** Both
retractions were *socially* driven — 02:16:51 answered a challenge, 02:27:58
answered `??? Big walk ???` and arrived amid `屌你`. The doctrine's
anti-sycophancy position (DEC-89..92) did not hold. A debrief running in-slot,
seconds after the same exchange, is exposed to the same pressure in the same
context and would have ratified the capitulation — writing "no black tower" as a
durable fact and making the error permanent.

**So the debrief must not adjudicate contradictions.** It proposes, append-only
(§5.2). Adjudication belongs to the FactSynthesizer, which sees the exchange
cold, hours later, with no one waiting and nobody swearing at it. The
propose/dispose split is not only about cost — it is about which component can be
*impartial*.

The debrief's instruction is comparative — *given what was asked, what was
actually checked and what came back, and what was actually said: where does this
conversation stand, does anything asserted lack backing, and what durable facts
were established?* — producing all three outputs from one call. **Facts are only
proposed from a grounded turn.**

**Note a deliberate divergence from Hermes**, which runs its post-turn review as
a fork of the *same* agent — for prompt-cache economics, not epistemics. The
Hermes audit found it has **no grounding verification on memory writes at all**,
which is consistent: if you never check, self-review costs nothing extra. This
design checks, so §10.2's argument binds.
## 10. Rejected alternatives

### 10.1 A `retract_claim` tool — *"detection NOT prevention"*
Honcho v3 supports it (`POST …/conclusions/query`, `DELETE
…/conclusions/{id}`, `POST …/conclusions`; `ConclusionCreate` takes explicit
`observer_id`/`observed_id` — verified live). **Rejected:** correctness contingent
on Nora noticing *and* on a tool call landing through the mechanism §1.6 shows
failing ~100% of turns. §6.9 gets the same outcome structurally.

### 10.2 Nora reflects on herself
**Rejected.** Self-attestation in the confidence state that produced the error.
§1.1 and §1.4 are not turns where she knew she was guessing.
`_self_card_block`'s docstring already states the rule: *"nothing verified
them… done is never decided by the doer."*

### 10.3 A `reflect()` tool Nora calls
**Rejected** for §10.1's and §10.2's reasons, plus: a tool fires at the model's
discretion mid-loop, so §7's ordering guarantee cannot hold.

### 10.4 A content summary (Issue #4 point 1)
**Rejected as insufficient.** It closes "she forgot what was said"; §1.1's loss
was a conclusion she had reached and published. A content summary of §1.1
preserves the tower facts — already fine — and still loses *«I made the black
tower up.»*

### 10.5 A Dreamer-style tick for `Standing` (Issue #4 point 5)
**Rejected on cadence, for `Standing` only.** 300s, sized for slang; a reasoning
position one tick stale is one the next turn does not have. **Not** generalised
to facts — §5.2 puts suppression on exactly that tick, because suppression is not
time-critical. The precedent the debrief borrows from the Dreamer is **the
bounded timeout** (§7.2), and that stands. The rest of the description this
sentence used to carry — bare lightweight client, no doctrine, no tools — was the
Dreamer at HEAD, and the keystone changes it: the runtime gets a tool set and its
own doctrine. The debrief stays cheap and toolless for its own reasons (§9), not
by inheriting the Dreamer's shape.

### 10.6 One tier
**Rejected — this is §15's resolution.** A single revisable state cannot hold a
standing correction: §6.9's mechanism drops what stopped being relevant, and a
fold forty unrelated turns later has every incentive to drop a caution about a
game nobody has mentioned. Splitting by *update semantics* means the durable
content never enters the revised tier at all.

## 11. Metrics

Appended to `nora/obs/metric_specs.py`'s tuples, never edited
(`tests/test_metrics_export.py::DECLARED_NAMES`). `Metrics.inc` on an undeclared
name **raises `KeyError`** (`nora/obs/metrics.py:94-111`) — inside the
slot-holding `try`, so a missing declaration is a §7.3 failure.

| metric | why |
|---|---|
| `nora_debrief_total{status, outcome}` | R-27's three states — a failed debrief is `unavailable`, never silently absent |
| `nora_debrief_seconds` | §7.5 — its own latency, excluded from tag latency, buckets past 30s |
| `nora_debrief_truncations_total{what}` | §6.5 — silent truncation reads as "folded everything" |
| `nora_standing_chars` | §6.5's cap, observed not assumed |
| `nora_facts_proposed_total` / `nora_facts_invalidated_total` | the propose/dispose split, visible |
| `nora_debrief_ungrounded_total` | §8.3's gate firing; a non-zero value is the honest read |

## 12. Configuration

`nora/config.py` is `extra="forbid"` (D5). **Ships OFF**, as every comparable
subsystem did.

```toml
[debrief]
enabled              = false  # until §14's gates are green
model                = ""     # NOT the conversational model (§9)
timeout_seconds      = 0      # §7.2 — its own bound, never inherited
max_input_tokens     = 0      # §6.5
window_rows          = 0      # §6.2
state_max_chars      = 0      # §6.5

[facts]
max_per_conversation = 0      # §6.5
retention_days       = 0      # invalidated facts are kept this long (§6.3)
```

`FactSynthesizer` rides the Dreamer loop on `[language]`'s existing settings —
**provisionally**. A fact adaptor is not sized like a slang extractor, and the
keystone puts budgets per adaptor (its §6) while leaving adaptor scheduling open
(its §9.2); both stay open here (§5.3). This line is what holds until they are
settled, not a decision that they need not be.

## 13. Testing

- **Contract suites** (CLAUDE.md: a port never gains a second implementation,
  *including a test fake*, without one): the `Standing`/`TurnVerdict`/`Fact`
  store is a new port needing its own. §6.2 removes the `RecordStore` obligation
  the first draft incurred.
- **Red on revert**: an eval over §1.1's real trace — does the sourced fact
  established at **02:14:43** (KeenGamer: Red→Green→Yellow→Blue→Black→Sphere)
  survive the 02:16:51 silence-based reversal and the 02:27:58 off-topic
  repudiation? Reverting the fact tier must turn it red. Note the test asserts
  the fact is **retained**, not that a retraction is honoured — §1.2.
- **R-13/R-13b**: `<standing>` escaping pinned by named acceptance tests, as
  `build_prefill` already is (§6.7).
- **Append-only invariant** (§5.2): a named test that `debrief` can never set
  `invalid_at` or delete a `Fact`.
- **Schema**: `tests/infra/test_schema_parity.py`, **not** `alembic check`.
- **Failure containment**: a raising debrief, a *cancelled* debrief, and a
  *hanging* debrief each leave `handler_error` and `partial_replies` untouched
  and release the slot.

## 14. What must be true before it ships

1. `reasoningContent` confirmed readable from `agent.messages` post-
   `invoke_async` on the live route. *Never read an instrument's output as
   evidence without first checking it can measure the thing.*
2. §6.8 decided — the fact store's shape.
3. Debrief latency measured on `nora_debrief_seconds`, second-queued-tag case
   explicit, against the p95 ≤ 5s SLO (`design/05-evaluation.md:79`).
4. §7.3's containment tests green; §7.2's timeout wired.
5. All §6.5 caps enforced and counted; §6.6's overflow path implemented.
6. Raw `TagTrace` retention and the re-derivation path exist **before** the first
   debrief ships, or early folds are unrecoverable.
7. `<standing>` escaped, untrusted, in the volatile tail, prefix cache-hit
   re-measured (§6.7).
8. §8.3 resolved: the narrowed gate justified on its own merits or dropped, and
   `fire.py::_feed` covered either way.
9. §7.6 decided: the four non-tag paths debrief, or are excluded with the
   exclusion tested.
10. `FactSynthesizer` wired as a Dreamer **adaptor** — objective, inputs, output
    schema, tool subset, sink (§5.3) — with a named test that it, and only it,
    writes `invalid_at`, and with its sink screened before the write (keystone
    §7) under a named test of its own. The screen is not optional here: this is
    the one adaptor whose tool subset holds `web_fetch`, so it distils
    attacker-influenceable pages into a store that renders into every later
    turn.
11. A decision-index entry and named tests in
    `tests/test_acceptance_rules.py::RULE_TESTS`. Candidate rule: *a `Standing`
    is never asserted as fact; it is situation-tier and contradictable.*

## 15. The central question, resolved

**Revision is the right shape for dropping a stale fact and the wrong shape for
keeping an established one.** The first draft filed this unresolved. The two-tier
split resolves it, and §1's corrected evidence shows why the resolution has to be
*structural* rather than a matter of wording.

**The failure was silence, not contradiction.** At 02:16:51 nothing asserted
"there is no black tower". A source simply *did not mention* it, and the omission
propagated into a repudiation. Any surface that is **rewritten wholesale** shares
that flaw: when the fold re-authors `state`, a fact it does not restate is a fact
it has deleted, and deletion-by-omission is indistinguishable from
deletion-by-contradiction. That is exactly the move that lost the black tower.

A fact tier fixes this by construction:

> **A fact can only be removed by an explicit contradicting assertion. Silence
> removes nothing.**

`Standing` may forget freely, precisely because the durable content lives in a
tier where forgetting requires an act. This is also the sharpest argument for the
graph shape (§6.8): an edge is deleted by an opposing edge, never by a source
that failed to mention it.

**Wording alone cannot carry this.** The first draft proposed a positive
closed-set fact — *"Big Walk has exactly four coloured towers: red, green,
yellow, blue"* — which §1.1 proves is **false**, and §1.1's KeenGamer result shows the real
progression does not end at a tower at all (Black Tower → Yellow Key → Wall →
Black Sphere → 7 Purple Puzzles → White Key). Any enumeration is wrong at some N
because the *category boundary* is wrong. A closed set states more than the
evidence supports; the relation "X unlocks Y" states exactly what a source said,
and survives being wrong about what counts as a tower.
## 16. Open questions

1. **The fact store's shape** (§6.8) — Postgres table, Graphiti, or flat text.
2. **Fact scope.** Conversation-scoped as specified. "Four towers" is a world
   fact that would help every room — but a *wrong* global fact is §1.1's
   contamination shape one tier up. The evidence favours conversation-scope: the
   fabrication and both retractions were in one conversation, so it would have
   sufficed and fails safer. Global promotion can come later with its own rule.
3. **Feed ordering** (§8.3): if the gate is kept, `feedback.run` spawns before
   the debrief. Reordering must not put a second network dependency in the slot.
4. **Re-derivation trigger** (§6.9): fold count, elapsed time, or forced whenever
   `TurnVerdict.grounded` is false?
5. **Injection persistence** (§6.7): escaping is necessary; is it sufficient for
   a surface that *retains* what other blocks discard each turn?
6. **Corroborating `grounded`**: `handle.tool_call_log` and `handle.deliverer`
   already know deterministically which tools ran. Fold that in beside the
   model's verdict?
7. **Window size** (§6.2): `context_window` is 40 rows. Is the debrief's window
   the same, or its own?
8. **Why `search_memory` missed it** (§1.1): it ran on the 07:07:15 turn and did
   not surface the retraction, which *was* an indexed message. Query shape, the
   per-message guard, or ranking? If it can be made to find her own corrections,
   part of this problem may not need solving here at all.
9. **Key shape**: `Standing` is keyed on `conversation_id`, but every
   lock/queue/slot keys `(platform, conversation_id)` (`slots.py:36`) and
   `messages` carries a `platform` column.
10. **Fire-path injection**: `build_prefill` is shared and parameterised only by
    `header=`, so `<standing>` renders on reminder/announce/relay prompts too.
    Probably right; undecided.

## 17. Status

Design only. No code. No `design/09-decision-index.md` entry — the index carries
landed decisions; this is a candidate. Filed as a spec rather than a
`design/proposed/` document because it is a single feature, not a programme
(Issue #4 is its candidate tracker); §4's "new memory axis" claim is the one part
touching topology, and it reaches `ARCHITECTURE.md` only if this lands.

**Blocking context:** §1.6's `tool_choice=required` saturation is a separate,
unfixed reliability failure. Everything here assumes replies keep arriving via
DEC-93's recovery path. It should be fixed on its own merits; §10.1 and §10.3 are
rejected partly because of it.

**Reviewer note:** the first draft was audited three ways and its §1.4 evidence
table was found to be a curated subset, its §8.3 central claim false, and its
§6.2 data source non-existent. All three are corrected above with the corrections
left visible, per `design/proposed/README.md`'s convention that a wrong turn is
recorded so it is not taken twice.
