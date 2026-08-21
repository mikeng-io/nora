---
type: analysis
title: "Live State — What the Dreamer Has Actually Produced"
description: "Read back from the running container and database: total lifetime output, the watermark ratchet, a tick-by-tick reconstruction of the failure window, and why each tier table is empty."
tags: [nora, dreamer, evidence, forensics, live-state]
timestamp: '2026-08-22'
edges: []
---

# Live State — What the Dreamer Has Actually Produced

Everything here was read back from the running system on 2026-08-21/22. Nothing
is recalled or inferred unless marked.

## 1. Total lifetime output

963 non-Nora messages have been processed. The complete output of every Dreamer
tier, ever:

```
room_lexeme            2      <- both wrong, see §2
room_exchange          0
room_portrait          0
fact                   0
fact_candidate         0
fact_entity            0
fact_premise           0
note                   0
language_observation   2
exchange_observation   0
```

The fact tier has written nothing. Ever.

## 2. The two rows

| canonical_form | sense | occ | speakers | state |
|---|---|---|---|---|
| `Big Walk` | "a specific long walking activity/event this group plays, where they hunt for gourds" | 1 | 1 | live |
| `big walk` | 「群內約定俗成嘅散步/行路活動，可能係夜晚出去行街」 | 1 | 1 | live |

Big Walk is a **co-op puzzle video game**. Neither gloss says so. The second is
quoted verbatim in the keystone spec §2 as the motivating failure the whole
Dreamer-as-an-agent design was written to fix; it is still in production,
unchanged, because the language tier was never migrated.

Four things compound in these two rows:

1. **They are two rows, not one corrected row.** The primary key includes
   `sense`, so a better gloss is a *new row*, never a correction. This is
   deliberate — see [09-self-correction.md](09-self-correction.md).
2. **`canonical_form` is case-sensitive and model-authored.** `Big Walk` and
   `big walk` are different keys.
3. **The two observations swapped `surface_form` and `canonical_form`** in
   opposite directions. Nothing downstream normalises.
4. **Both carry `instruction_sha256 = NULL` and `model = NULL`.** They predate
   the provenance migration, and because the stamp is inherited from the newest
   observation for that key, they will carry NULL indefinitely.

**What Nora actually sees.** Both are `live`, both carry a quotable example,
and two is under the render cap — so **both render, wrong one first** (it
scores marginally higher), with no marker of contradiction, no confidence and
no score shown. The render rule that is supposed to protect against this —
*"the verbatim quote is the only thing that makes a wrong gloss visible"* — is
satisfied in letter and defeated in fact: 「big walk」 appears in both quoted
messages, so the quote falsifies neither gloss. The quote under the English
gloss is a Cantonese message.

## 3. The watermark ratchet

Two consolidator names exist in `consolidation_watermark`: `language` (the
shared cursor, hardcoded) and `fact-candidates`. `fact-adjudication` is
watermark-free and correctly has no row.

| name | scope | last_message_id | updated_at |
|---|---|---|---|
| `fact-candidates` | the busy room | **1180** | 2026-08-21 **11:53:33** |
| `language` | same room | **1304** | 2026-08-21 16:55:06 |

The `fact-candidates` row still carries its **seeding** timestamp. The advance
path writes `updated_at` on both of its arms, so this proves the adaptor never
once reached the advance, on any scope, in five hours and roughly sixty ticks.

The lag was 125 rows and growing while `language` on the same scope kept pace.
Failure holds the watermark — correct, fail-closed, and exactly what makes it a
ratchet: the batch that failed is re-read next tick, larger.

The other three scopes are zero behind; they were seeded at their
conversation's head and have been silent since.

## 4. The failure window, reconstructed

Thirteen runs, `14:10:09Z` → `16:59:59Z`, all `fact-candidates`, all one scope,
all the same traceback: `MaxTokensReachedException` raised inside the Strands
event loop, each preceded by `handling max_tokens stop reason - replacing all
tool uses with error messages`.

Failures land 14–58 s after their tick — nowhere near the 60 s timeout. **The
model blew the per-call ceiling on the first call.** `turns = 2` was never
reached.

The window is fully accounted for, tick by tick. The only live fire condition
was `lag > 0 and quiet >= 1800` (lag never exceeded `safety_lag`; age never
reached 24 h). Compressed:

| period | what happened |
|---|---|
| 13:34–14:04 | quiet clock under threshold; adaptor does not fire (7 ticks) |
| 14:09–14:20 | fires; **failures 1–3**; backoff climbs 60 → 120 → 240 |
| 14:24 | a message arrives → quiet resets → adaptor does not fire → **backoff CLEARED** |
| 14:54–15:20 | fires; **failures 4–8**; backoff reaches 960 s, skipping four ticks |
| 15:39 | a message arrives → **backoff CLEARED** |
| 16:09–16:20 | fires; **failures 9–11** |
| 16:24 | a message arrives → **backoff CLEARED** |
| 16:54–16:59 | fires; **failures 12–13** |
| 17:02:56 | restart with `[dreamer] enabled = false` → `0 adaptor(s)` |

The backoff engaged four times and was **destroyed three times by a single
incoming chat message**. Mechanism and consequences in
[10-orchestration.md](10-orchestration.md).

A Langfuse trace of one run carries exactly three observations — an AGENT, one
event-loop-cycle span, and **one GENERATION** at input 3,201 / output 4,096
tokens. One generation. The runaway is inside a single completion, not a loop
failing to terminate.

## 5. Why each table is empty

**`fact_candidate` / `fact`** — the extractor has never completed a run. §4.

**`exchange_observation` / `room_exchange`** — the observer **is** wired, **is**
running, and parses fine. It returns `{"patterns": []}`. No flag, no wiring
gap, no code defect. The fire condition hands it roughly thirty-minute
conversational slices while its objective asks for *"a RECURRING EXCHANGE
PATTERN … a kind of interaction they trade back and forth"* and explicitly
licenses the empty answer. Two of the four scopes are single-speaker and can
never qualify — the observer requires two distinct participants.

**`room_portrait`** — its trigger requires a **new** append in the **same**
cycle. The only two productive cycles ever (2026-08-17 and 2026-08-19 ~03:00)
**predate the synthesizer's own deployment** (2026-08-19 20:34). It has had
zero eligible cycles since it existed. A dormant scope's empty batch can never
satisfy it either, so the portrait fires strictly *less* often than the
language tier writes rows — the opposite of the keystone's framing of it as a
low-bar, watermark-free participant.

**`note`** — the note tier is live and inert by design; no tools are wired.

## 6. Two rows in 963 messages is also a finding

The extraction objective is heavily conservative — it tells the model it is
*"expected most of the time to find NOTHING"* — and the productive window was
one backfill batch when the tier launched. Incremental batches have yielded
roughly one row every two days. Whether that is correct calibration or a
starved tier cannot currently be distinguished, for the reason in
[02-instrument-blindness.md](02-instrument-blindness.md).

## 7. Stale claims in the corpus, corrected here

The keystone's §1 "measured against HEAD" table has drifted:

| keystone claim | actual |
|---|---|
| `LexemeObserver`: 3 model call sites, **2 inline prompt constants** | 2 call sites; prompts loaded per-run from the objective corpus |
| `ExchangePatternObserver`: 2 sites, **1 inline constant** | 1 call site; prompt from the corpus |
| several `file:line` references | drifted by hundreds of lines |
| portrait "participates with no watermark/lag/quiet" | true, but omits that it has a *stricter* trigger of its own — the reason it is empty |

The prompt-substrate lift already happened. What remains true, and is the real
defect, is that the two observers **bypass the runtime entirely** and therefore
never receive the doctrine at all — see [11-doctrine.md](11-doctrine.md).
