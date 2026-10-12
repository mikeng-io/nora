---
type: design
title: "What the composer decides — the decomposition and the decision questions"
description: "Step 1 of the operator's sequence (2026-10-12): what a tag turn's input is made of, read from code; what is fixed; and the few decisions a decision model makes, sized to the measured Tev1 latency on norty's iGPU (questions run one after another: ~0.5 s each on 0.8B, ~2.6 s on 4B). Two questions: what the turn is about (one Choice whose probabilities select several bundles) and how much history. Code maps each answer to units."
tags: [nora, context-plan, prompt-composition, decision-model, tev1, systemone]
timestamp: '2026-10-12'
edges:
  - {dst: 14-a-decision-model-composes-the-prompt.md, rel: realizes, provenance: asserted}
  - {dst: evidence/composer-units.yaml, rel: constrained_by, provenance: asserted}
---

# What the composer decides

> **Parked 2026-10-13 (operator), after step 2.** Result
> ([evidence/composer-tev1-step2-2026-10-12/](evidence/composer-tev1-step2-2026-10-12/),
> #833): Tev1 as shipped cannot compose the prompt. Per part it says yes to ~90%
> (AUC 0.557, 73 s a turn on 0.8B); two questions drop ~40% of what turns used
> (59% recall, 4.6 s on 0.8B). Next was training (step 3); its data is thin:
> ~500 real tag turns since 2026-08-28 (~15 a day), lopsided across turn kinds.
> Effort estimated at 4–5 days, 1–2 h of operator review and ~$100–300 of
> compute: evidence labels plus a strong teacher model, ablation replay on ~100
> turns as a check, synthetic turns for rare kinds (never in the held-out set).
> **Resume when** the operator asks; by then more real turns exist (~450 a month).
> Also open regardless of the model: Ollama's `/v1/systemone` puts every
> question's text into each question's prompt (quadratic), Tev1 ships with
> `num_ctx` 2050, and the fixed floor plus base is already ~4.6k for a greeting.

Operator, 2026-10-12, the sequence: (1) understand what is decomposed and which
decisions the model makes; (2) benchmark Tev1 to prove it can; data preparation
and training only after.

## 1. What a tag turn's input is, from code

Read at origin/master `6bb8414d6` (full table with file:line kept with the
benchmark evidence). In the order the model reads it:

| part | what | gated today by | median tokens (node 09) |
|---|---|---|---:|
| system prompt | `<nora_doctrine>`: 8 files for `tag` (identity, belonging, voice, silence, conduct, pictures, core constraints, room constraints) | audience only (`loader.py` `get`) | ~9,130 |
| | `<runtime_context>` (name, trigger, language hint) | always | small |
| | `<available_skills>` (5 skills) | always, even with the computer or video off | 476 |
| tools array | resident schemas (`HOT_TOOLS`, voice music tools), `load_tools` | `defer_tools` (on in production), platform, config wiring, room MCP allowlist | ~2,073 |
| message 1 | `<context_header>` | always | 182 |
| message 2 | the clock line, then `_BLOCKS`: self, lessons, tool_catalogue, room_members, presence, peer_card, peer_history, notes, memos, culture, unit_culture, character, computer, documents, subagents, standing, standing_working_set, group_transcript, spoken_turn | each by config and data presence (none by what the message is about) | ~10,000 together |
| message 4 | `<replying_to>`, `<tag_message>` | always | 145 |

What is already separable: every block is its own object, and the index/body
split already exists for notes, documents and memos (index pushed, body pulled
by a tool). What is not yet separable: doctrine below the file (the loader
selects whole files), the parts of `standing`, `computer` and `subagents`.

**Nothing in today's assembly looks at what the message is about.** Every gate
is config or whether data exists. That is the gap the composer fills.

The clock line opens message 2, so nothing after it is a cached prefix today;
the cached head ends at `<context_header>`.

## 2. The constraint that shapes the decisions

Measured 2026-10-12 on norty (Ollama 0.35 `/v1/systemone`, ROCm iGPU, warm, a
small dummy state):

| model | 1 question | 4 | 16 |
|---|---:|---:|---:|
| Tev1 0.8B | 0.27 s | 2.0 s | 7.8 s |
| Tev1 4B | 1.3 s | 10.5 s | HTTP 500 (`num_ctx` 2050 exceeded) |

Questions in one request are evaluated **one after another**, not in parallel,
on this server. The budget (under 2 s, ideally under 1 s) allows about three
questions on 0.8B and one on 4B. A yes/no per unit (64) is out on this hardware.

So the model makes **few, broad decisions**, and code maps each answer to the
units it implies. A Choice returns a probability for every option (the API
contract), so one Choice can select **several** bundles: every option whose
probability clears a threshold. This is the design's central assumption, and
the benchmark tests it first.

## 3. Fixed (never a decision)

`<context_header>`, the clock line, `<tag_message>` and `<replying_to>`,
`core/constraints.md`, `<runtime_context>`, the `send_reply` schema. Code facts
stay code: `<spoken_turn>` when `msg.voice`; media code present in the window
is a state field.

## 4. The decisions

**State** (named JSON fields): `message` (the tag message), `replying_to` (if
any), `recent` (the last 5 transcript lines), `facts` (spoken, media in the
recent lines, hour, weekday, the room has documents / a computer session /
running jobs / open standing items, the sender is a listed person).

**D1, Choice: what does answering this message involve?** Options (each with a
one-line definition as its criterion):

| option | maps to (beyond the fixed floor and the base bundle) |
|---|---|
| `small_talk` greeting, thanks, chit-chat | nothing more |
| `lookup` a fact to find: weather, place, transit, price, news | room law "say only what you found", conduct "getting to an answer", "where to look first", honesty; `web_search`, `web_fetch`, `now`; the tool catalogue |
| `person` about someone in the room, or the sender themself | room law "other people's words"; conduct "talking about a person"; `peer_card`, `peer_history`, `character`, `room_members`; `search_memory` |
| `group_plans` plans, who owes what, what was decided, reminders | `standing`, `standing_working_set`, `notes`, `memos`; conduct "sorting something out", "writing something down"; room law "writes need somebody here"; `note_read` |
| `media` a picture, clip, sticker or voice note | room law "what you were told about a picture"; `pictures.md`; media tools |
| `work` a document, the computer, a delegated job, an image or video to make | `documents`, `computer`, `subagents`; conduct "a written piece is a document"; room law "what you can do"; `<available_skills>`; the tool catalogue |
| `banter` joking, roasting, judging who is right | conduct "banter", "being asked to judge"; voice 粗口, 笑位; room law "other people's words"; `culture`, `character`, `self` |
| `correction` someone says Nora is wrong, or disputes a fact | room law "when you are told you are wrong", "say only what you found"; conduct "being contradicted"; voice 有人話你錯 |
| `about_nora` who she is, what she can do, where she is | identity (all), belonging (all), `self`, `lessons`, `presence`; room law "what you can do" |
| `music` play, skip, stop, volume | the music tools |

Every option whose probability is at least *t* (tuned in the benchmark; start
0.15) is included; the top option is always included.

**Base bundle (every turn):** voice 香港人點樣打字, 打字唔係寫文, 唔似 Nora 嘅聲音,
一個 message 嘅感覺 (how she sounds; the first labelling pass marked these
needed on 83–99% of turns); conduct "you have judgement", "follow the
conversation"; `lessons` (~200 tokens, learned practice).

**D2, Choice: how much of the conversation does answering need?**
`last_few` (5 lines) / `window` (20, today's default) / `extended` (40, plus the
quoted parent's surroundings).

Two questions: ~0.5 s on Tev1 0.8B; ~2.6 s on 4B, over budget unless the state
is kept small.

## 4a. The other shape: one yes/no per part, and the rules after it

Asking every part at once is allowed by the API (all questions in one request)
and is the more direct judgement. On norty it is slow for a reason found
2026-10-12: Ollama logs "model architecture does not currently support parallel
requests" for `qwen35`, Tev1's base. Questions run one after another at ~0.28 s
each on 0.8B (8 questions 2.3–2.5 s whatever the state size; 8 parallel
requests 4.3 s, slower). So 64 parts is ~18 s on 0.8B and minutes on 4B. This is
the server's limit, not the model's or the API's (TypeSafe's hosted API runs
questions in parallel). The benchmark measures this shape for ACCURACY, and the
latency is solved separately if it wins (a runtime that batches, or a base the
server can run in parallel).

**Rules, in code, after the answers:**

1. **Include:** a part is in when `noul(part) >= threshold(part)`. Thresholds by
   the cost of a miss, starting points to tune: law sections 0.10; memory and
   person blocks 0.20; tools and catalogue 0.20; practice and style 0.35.
2. **Dependencies:** a tool in brings its doctrine section (document tools →
   "a written piece is a document"; `media_fetch` → the picture law; lookup
   tools → "say only what you found"; location tools → the location law);
   `peer_history` brings `peer_card`; `standing_working_set` brings `standing`;
   any deferred tool brings the `tool_catalogue`.
3. **Floor:** always in (§3).
4. **Code facts override:** `msg.voice` → `<spoken_turn>`; a media code in the
   recent lines → the media parts.
5. **Budget (optional):** over a cap, drop included parts lowest probability
   first; never the floor or a law section.
6. **Fail closed:** the model errors or misses its time budget → today's full
   prompt.

The same rules apply to the two-question shape, with a bundle in place of a
part. In both, the model judges and the rules decide; thresholds, dependencies
and the floor are config.

## 5. Rough sizes (estimates)

Floor + base: about 2.5–3k. A greeting (`small_talk`, `last_few`): about 3–3.5k,
under the 5k target. A lookup about a person with a picture: floor + base +
three bundles + 20 lines: about 8–10k.

## 6. What the benchmark (step 2) checks

0. **Both shapes:** per-part (§4a) for accuracy, two-question (§4) for the
   latency budget, on Tev1 0.8B and 4B.
1. **Feasibility:** Tev1 answers D1 and D2 in the `/v1/systemone` contract on
   norty, within budget, on real turns.
2. **Multi-select by probability:** on turns that are two things at once (a
   person and a lookup), does the second option's probability clear *t*.
3. **Agreement with what the turn actually did** (no labeller): tools the turn
   called imply bundles (`web_search` → lookup; `note_read`/`remind` →
   group_plans; `media_fetch` → media; `document_*`/`computer_*` → work); a
   missed implied bundle is a miss.
4. **Cantonese vs English** turns separately (TypeSafe: CJK accuracy is lower).
5. **Sizes:** the composed first call for greetings and for the rest, against
   <5k and ~10k.

Not in step 2: training, a labelled set, wiring into Nora.
