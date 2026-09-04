---
type: proposal
title: "Ambient Participation — The Fifth Event, Occasion-Gated, Not a Fork"
description: "How Nora can choose to speak in a room without being tagged: a fifth Event occasion built THROUGH FirePath.speak (the repo's settled precedent for unprompted speech), a doctrine audience that makes silence a first-class outcome, occasion-gated delivery recovery, and an evaluation suite that gates the flip. No forked codebase, no copied tools, no copied doctrine. Rev 2 absorbs a full adversarial Kimi code audit (session_abdb8262) — every finding verified against file:line before folding in."
tags: [nora, ambient, participation, occasion, silence, determination, eval, firepath]
timestamp: '2026-09-05'
edges:
  - {dst: 26-standing, rel: provides_input, provenance: asserted}
  - {dst: 15-roadmap, rel: extends, provenance: asserted}
  - {dst: 06-trigger, rel: revises, provenance: asserted}
  - {dst: 13-doctrine, rel: extends, provenance: asserted}
  - {dst: 08-state, rel: depends_on, provenance: asserted}
version: 2
---

# Ambient Participation

## 1. The goal, stated as a failure

Nora currently speaks in a room for exactly two reasons: she was tagged, or an
`Event` came due. Both are **must-speak** — the turn exists because speaking is
the point. Tag forces a tool call on the first model call; fire recovers final
prose into a delivery because the occasion is "say this thing".

**A room she could genuinely serve does not show up in either category.** The
question with no answer, the fact she holds that nobody asked for yet, the
stalled thread, the mistake she could quietly correct — these are ambient
opportunities, and today they are structurally invisible to her.

The failure this design closes: **ambient participation is the capability to
speak when nobody called, judged against the room's actual state, with the
right to stay silent.** Speech is the exception; **silence is success.**

## 2. The shape: a FIFTH Event through FirePath — R-1b stays intact

Rev 1 proposed a third `deliver.build` caller and a "conscious evolution of
R-1b". **Rev 2 rejects both.** The repo's settled and enforced precedent is
explicit: new unprompted speech goes **through** `FirePath.speak` as a new
occasion, never as a new send mechanism.

- `fire.py:257` — relay is "the FOURTH `Event` (DEC-105), following DEC-99's
  ruling exactly: a new way for Nora to speak is an occasion on this path,
  never a fourth send mechanism."
- `ARCHITECTURE.md:174` — "R-1b forbids a third caller of `deliver.build`,
  which is exactly why the Event seam exists."
- `fire.py:17-20` — "a second unprompted path cannot be built beside this one.
  It has to be built THROUGH it."

**Consequence: R-1b (`test_only_the_tag_path_and_the_fire_path_can_send`) does
NOT change.** The three-link chain stays: `Deliverer` is the only caller of
`adapter.send`; `deliver.build` is the only construction site; its callers stay
`{tag pipeline, fire path}`. Ambient is an occasion `FirePath.speak` hosts, the
same way reminders, version announcements, compute jobs and relays are.

**What actually changes is R-1's second clause** — "ambient traffic never
reaches the agent" — which has survived every occasion so far untouched
(`design/09-decision-index.md:68`, enforced by
`test_ambient_never_constructs_agent`, `tests/integration/test_ambient.py:13-19`).
Ambient participation is the **first-ever change to clause 2**: untagged
messages may now reach the agent, under occasion-gated conditions. That test,
its decision-index row, and its name must be revised in the same commit.
(R-1's row survives; the clause statement is revised, exactly as DEC-69 revised
clause 1.)

**Name collision — resolved by renaming.** "ambient" already means "recorded,
never answered" in this repo: `GLOSSARY.md:110`, `nora_messages_total`'s help
text "Ambient messages ingested", `test_ambient.py:59`,
`test_ambient_never_constructs_agent`. Reusing the word for its opposite would
silently invert three glossary/metrics/test meanings. **This design uses
"participation" for the new occasion** (`applies_to=[participation]`,
`nora_participation_silence_total`, `test_participation_never...`). The
renamed R-1 clause-2 test becomes `test_participation_only_reaches_agent_when...`.
(Rename is an open question for Miles — see §10.4.)

## 3. The determination gate: relevance AND grounded value

A message is not "worth replying to" in the abstract. The gate is two
independent AND-ed conditions, and the second is the one that makes real
ambient hard:

**① Relevance — does this message deserve a reply from anyone?**
Status updates, pure acknowledgements, private two-person exchanges, inside
jokes, commands directed at another service — relevance-No, the turn never
starts.

**② Grounded value — does NORA have something true to add?**
A relevant question ("does anyone know when AWS Community Day 2026 is?") with
no matching standing/notes/fact is a **silence**, not an "I don't know". In
tagged mode "I don't know" is an acceptable obligation — she was called. In
participation mode nobody called her; speaking just to prove she is listening
is noise. **Participation speech without grounding is the identity violation
this design exists to prevent.**

The eval suite makes this distinction load-bearing: every relevance-Yes
scenario has a knowledge-cut twin (same message, same transcript, only the
seed differs: fact present vs absent) — and the verdict must flip to silence.
A model that answers from parametric memory has failed; her memory boundary is
her standing/facts, not what the base model happens to know.

## 4. Doctrine: same files, new audience — plus the pins Rev 1 missed

The three-layer principle — `conduct.md` writes WHEN to act, `voice.md` writes
HOW it sounds — maps cleanly onto occasions. **Rev 1's table was factually
wrong in two ways** (verified): no file uses the `always` audience today
(they are `[tag]` or `[tag, heartbeat]`), and the loader's `get()` "always"
branch is dead code (`loader.py:569`). And a new section must satisfy
`check_authority_graph` reciprocity (`loader.py:372`) — a multi-file governed
edit, not "one new file".

| File | Audience change |
|---|---|
| `identity.md` | `[tag, heartbeat]` → add `participation` — who she is does not vary by occasion |
| `voice.md` | `[tag, heartbeat]` → add `participation` — how she sounds does not vary by occasion |
| `silence.md` | **redefined** — currently `[tag]`, "You never reply to a message that didn't call you" — the exact claim participation overturns. Becomes: tag = must-speak, participation = may-speak, and the file gains the participation audience. |
| `conduct.md` | judgement/checking-banter rules are occasion-neutral → add `participation`. An ambient-specific section declares: you have the right not to reply; no grounded value = silence; silence is a success, not a failure. |
| `constraints.md` | **was missed by Rev 1.** Pins "reply only where you were called" (R-14, `tests/test_doctrine_content.py:252`). Participation is the first occasion that deliberately replies where she was NOT called — this clause must be revised, not inherited. |
| **new** `participation-conduct.md` | `applies_to: [participation]` — the participation-specific rules, so the tag path's silence rules never leak into participation and vice versa. |

Existing test pins that accompany this: `tests/test_doctrine_content.py:23-38`
(pins the file set and every file's `applies_to`), `tests/test_doctrine_audiences.py`
(exists because a thin audience thins **LeakGuard's verbatim corpus (R-6)** on
the very path that speaks unprompted). A thin participation audience would
deliver unprompted speech with a smaller R-18/R-6 surface than the tag path —
the audience must be as complete as `[tag]`'s.

## 5. Tool choice: the seam already exists — at ModelSource, not invoke

Rev 1 claimed `tool_choice` must be threaded through `invoke`. **Wrong —
verified.** `nora/agent/factory.py:78` — `ModelSource` already takes
`tool_choice: str = FIRST_CALL` as a **parameter**, with a shipped per-occasion
precedent: the Dreamer uses `AFTER_SPEAKING` (`factory.py:174`) — it is "a
PARAMETER since the Dreamer" (`factory.py:106`). Threading through `invoke`
would be 4+ signature changes for nothing; the composition root already
chooses per occasion today.

What the parameterization actually requires:

| Occasion | tool_choice at composition | Rationale |
|---|---|---|
| tag | `required` (`FIRST_CALL`) | she was called; the turn exists to answer |
| fire | `required` | the occasion is a due reminder/announcement — must-speak |
| dreamer | `AFTER_SPEAKING` | its loop has no terminal verb (`factory.py:174`) |
| participation | `auto` | speaking is optional; the model decides per turn |

**Two real landmines, both verified:**
1. `tool_choice.require()` releases a force pin back to the hardcoded
   `FIRST_CALL` (`tool_choice.py:99-106`) — an `auto` participation turn would
   be flipped back to `required` mid-turn after any forced tool. The release
   must go to the model's **initial** value, not the module constant.
2. The intent router (`nora/agent/intent_router.py`) and any other component
   that re-pins tool_choice mid-turn must be occasion-aware; an unguarded
   router turning a participation `auto` into `required` destroys the
   right-to-silence the design exists to create.

## 6. Tool descriptions: Rev 1's "one accuracy fix" was false — it is at least four

The audit read the full `send_reply` docstring (`tools.py:7142-7191`). The
claim that the MUST is "conditional on producing a reply" scopes only the
"exactly once" sentence. **At least four phrases presuppose a tag or forbid
the silence mechanism:**

1. `tools.py:7146-7147` — "the conversation you were tagged in": the one fix
   Rev 1 admitted. Becomes "the conversation this turn belongs to" — true for
   all five occasions.
2. **`tools.py:7143-7145` — the load-bearing one** — "do not return the reply
   as the assistant's final message instead of calling it (that path is an
   emergency fallback, **never a proactive choice**)". Under `auto` with
   no recovery, prose-without-send IS the participation silence mechanism —
   and this sentence brands it "never a proactive choice", unconditionally. It
   actively converts near-miss silences into sends, against the design's own
   hard bar. Must be occasion-qualified or reworded so choosing not to call is
   legitimate.
3. `tools.py:7178-7179` — MAY-burst case "a reply to **whoever tagged you**
   AND a word to somebody else": no participation reading; there is no tagger.
4. `tools.py:7183-7185` — "When the person is here and you do not hold the
   fact they want, ask them: «@Jordan 有人想知你幾高»": presupposes a
   questioner; instructs unprompted third-party pings — precisely what the
   grounded-value gate (§3②) exists to prevent.

The descriptions' deeper problem is structural: they are saturated with
when-to-use content, contradicting the "HOW not WHEN" principle. The honest
fix is a description that says what the tool DOES (deliver a message), leaving
when-to-use to doctrine — a rewrite of one docstring, still single-source, no
per-occasion copy.

## 7. Send path: silence is first-class, and "no recovery" is four occasion-gated branches

Rev 1 said "no final_text recovery" as if it were a parameter. **It is not —
verified.** Delivery-producing recovery branches exist in at least four
places, and the participation-decline shape (end_turn, nothing delivered,
non-empty final_text) hits every one of them:

1. Tag path DEC-93 recovery — `pipeline.py:964-983` delivers final_text with
   `source="recovered"`.
2. Fire path DEC-93 recovery — `fire.py:1015-1031`, same shape.
3. Salvaged reply fallback — `pipeline.py:870-894`.
4. Canned exhaustion fallback — `_EXHAUSTED_FALLBACK`, `pipeline.py:913-929`.

An ungated participation turn that chooses silence would be delivered by 2
(if occasioned on FirePath) or 1/3/4 — the room hears a draft she decided not
to send. **Each branch must be occasion-gated: participation never recovers.**

**Metrics — Rev 1 under-specified and would poison existing measurement:**
- `invoke.py:163-166,186-200` hardcodes `audience="tag"` on every invoke
  series; `check_outcome` likewise (`deliver.py:231-237`, whose docstring says
  "if a second caller ever appears, this literal is wrong"). Every
  participation turn with audience="tag" would corrupt the tag path's standing
  measurement.
- `nora_engagement_outcomes_total` (`pipeline.py:1241-1242`) has **no audience
  label at all** — participation silences would flood `outcome="silent"` and
  destroy the tag path's silent-failure accounting. Needs an audience
  dimension before participation ships.
- The proposed `nora_participation_silence_total` must be gated on
  `ok=True and stop_reason not in BUDGET_STOP_REASONS` — otherwise it
  re-creates the R-23 bug (chosen silence and `invocation_timeout`/`llm_error`/
  `limit_*` silence land in the same "healthy" counter).
- R-5's acceptance matrix defines no-send as failure today
  (`tests/integration/test_silent_failures.py:21`: `("no_send_call", False,
  "no_send_call")`). Participation needs its own row where `no_send_call` is
  healthy, and A8's SLO (`metric_specs.py:85-90`, `docs/runbook.md:11-24`) is
  `audience="tag"`-filtered — the runbook calls that filter "load-bearing, not
  tidiness". An ambient silence must never land in `nora_silent_failures_total
  {audience="tag"}`.

**The indicate stage was missed entirely and is room-visible.**
`indicate.py:13-14` resolves 👀→⚠️ on the triggering message when undelivered.
Fire escapes via a synthetic blank-sender message; participation triggers on a
**real** message, so every silent determination would advertise itself to the
room (👀 then ⚠️). **Participation must NOT indicate — no 👀, no ✅/⚠️, no
reaction — (Miles decision, 2026-09-05 review).** An ambient reply does not need
an emoji indicator; the room sees a message or nothing. Unlike fire — which
escapes `indicate.begin` by construction because its synthetic event has a
blank `sender_id`/`timestamp` (`fire.py` docstring) — participation triggers on
a REAL message with both present, so the 👀 would fire and every silent
determination would advertise itself. Skipping the indicate stage entirely on
the participation path is a positive design choice, not a workaround: no
processing feedback loop is exactly what makes a silent turn look like nothing
happened.

What survives scrutiny (verified accurate): feedback stage skips on no-send
(no reply to feed), A9 audit-first writes no outbound row on no-send,
ConversationSlots release is `finally`-guarded (`pipeline.py:1155-1168`),
Honcho human feed is ingest-time not turn-time.

## 8. What the shared machinery requires — the honest touchpoint list

Rev 1's "not a fork" table understated the work. The shared machinery is
reused (that is the point) but the following MUST be occasion-aware:

| Touchpoint | Change |
|---|---|
| `FirePath.speak` | hosts a fifth `Event` (`reminder`, `version`, `compute`, `relay`, `participation`). `EventFactory` currently builds from `ReminderStore.due()` rows (`nora/domain/events.py:32-63`) — participation's trigger is an incoming message; it needs its own event construction path and a dispatcher branch. |
| `EngageDecision.engage` | currently a **bool** (`nora/domain/ports.py:478-487`, `dispatch.py:59-66`) — "maybe speak" is inexpressible. Needs a third value or a separate participation verdict. New trigger source + decision vocabulary + dispatcher branch — this is a genuinely new path, not a "new value of an existing enum" (Rev 1's table lied there). |
| ConversationSlots / R-30 | heartbeat uses `try_acquire`, never queues (`design/09-decision-index.md:101` calls slot serialisation "correctness"). A participation turn racing a tag turn is exactly the re-answer/contradict failure R-30 exists to prevent. Participation's slot policy must be specified. |
| Debounce / anti-noise | R-9's debounce protects the tag path; participation needs its own cadence state and the same banned per-tag retained state treatment (`nora/tag.py:73-86`, policed by `test_no_retained_per_tag_state`). |
| Policy | `[engagement].policy` is a closed `Literal["trigger_text"]` (`nora/config.py:350`); "allowed to answer when tagged" vs "allowed to be spoken to unprompted" is a trust distinction `policy.example.toml:77-79` already draws for Discord. A participation policy surface is required. (Genuine good news the audit found: untagged messages already reach ingest, and `VoiceNotePolicy` is precedent for engagement without a tag.) |
| Trace volume | Rev 1's "full model on every message" would put a retained generation trace on the never-answered flood the plumbing sampler exists to drop (`nora/ingest/pipeline.py:235-244`). Participation needs a sampling decision before the full-model default is accepted. |
| Egress | verified unaffected (R-18 applies through `Deliverer`); presence/analytics/Docker healthcheck also unaffected. |

## 9. Evaluation: not writable on the harness today — the honest gaps

The 15-case suite (5 relevance-Yes + 5 knowledge-cut twins + 5 pure-No) is the
right shape and gates the flip. The audit verified it is **not writable on the
harness as it stands** — four concrete gaps, each with its path:

1. **No participation path to drive.** The harness drives the tag path only
   (`harness.py:395-397`, via `Dispatcher.on_message` → engagement on
   `decision.engage` == True). Participation must exist in production first —
   the harness inherits drive-ability from the real dispatcher. This is why
   participation ships in two phases: (a) build the occasion, (b) eval and
   tune. The eval cannot precede the occasion.
2. **No per-case seed in the case schema.** `Case.from_dict` reads no per-case
   seed (`harness.py:131-140`) though `run_case` accepts a hand-built
   `ProductionSeed` (`harness.py:259,319-362`) — the case format needs a seed
   field. (Footgun: facts/notes keyed outside workspace "nora" silently vanish,
   `eval_support.py:66-71`.)
3. **Verdict assertion — "called" vs "sent".** Live-mode `tool_calls` is
   reconstructed from Prometheus counters with documented blindness to
   ReplyGuard-vetoed calls (`harness.py:222-233`); "send_reply called" and
   "message sent" are different verdicts (called-but-vetoed → `tool_calls=
   ["send_reply"]`, `sent=[]`). A participation evaluator must pick one signal
   and say which (lean: `sent`, the room-visible truth, matching the
   indicate-suppression design).
4. **Parity premise must hold.** The parity test compares kwarg NAMES only
   (value-blind, documented `tests/architecture/test_assembler_parity.py:25-32`),
   and fire already proves a path can bypass `build_assembler` — it has its own
   `build_context`, "This is the trap" (`fire.py:41-42`), and the parity scan
   covers only composition.py + offline.py. **"Byte-identical to production"
   is false from day one for the exact occasion the suite exists to test
   unless participation routes through `build_assembler` and the parity scan
   gains the participation module.**
5. **Judge.** There is no `llm.deep` tier — the judge is
   `ollamacloud/glm-5.3` (`config.example.toml:1408`); `JUDGE_MODEL_TIER =
   "deep"` is a leftover label (`judge.py:42`). And `_build_judge_prompt` is
   tag-shaped ("what Nora was asked", `judge.py:259`) with an
   `addressed_the_tag` rubric that would score correct participation speech
   ~0. An participation judge rubric is a separate deliverable.

## 10. Open questions (for Miles)

1. **Determination layer**: lightweight pre-filter vs full model on every
   message. Lean (unchanged): full model first — fewer components, the model
   has all context, and a classifier's precision problem is ill-defined until
   evals exist. BUT §8's trace-volume concern means the full-model default
   needs a sampling decision, and the per-room cadence (§10.3 below) bounds
   the cost either way.
2. **Participation slot policy**: how does a participation turn take the
   ConversationSlot versus a racing tag? (try_acquire-and-skip, queue-behind-
   tag, or participation-only slot?) R-30 exists to prevent the re-answer
   failure; the least surprising choice is try_acquire-and-skip so a tag never
   waits behind an ambient thought.
3. **Cadence / anti-noise floor**: at most once per N minutes per room? The
   tag path's R-9 debounce is separate; participation needs its own floor
   before any live traffic.
4. **Name**: adopt "participation" (resolves the "ambient" collision — §2) or
   pick another? This renames the R-1 clause-2 test, the metrics, the doctrine
   audience, and the glossary entry as one sweep.
5. **"I can look that up"**: the middle case between answering and silence.
   Lean (unchanged): v1 never — it creates a commitment and follow-up
   obligation; silence is safer and cheaper than a promise.
6. **Shadow mode** (rollout gate, not a separate runtime): participation
   determinations write to a mirror channel/log without sending; Miles reviews;
   then flip. The mirror surface must be decided before the shadow phase
   starts.

## 11. Migration & governance

This is a governed change: it revises R-1 clause 2 (protected state), adds a
fifth Event occasion, a new doctrine audience, a new metric family, and a new
policy surface. The same-commit sweep on the rule change is NOT optional and
is larger than Rev 1 claimed:

- `test_ambient_never_constructs_agent` renames/rewrites (R-1 clause 2's test)
  + its `design/09-decision-index.md` row
- `RULE_TESTS` binding (`tests/test_acceptance_rules.py:51-61`) — any test
  rename must update the dict or `test_every_enforced_rule_has_a_named_test`
  goes red; the decision index declares itself sourced FROM `RULE_TESTS`
- `tests/architecture/test_only_two_paths_can_send.py` — **unchanged** (the
  deliver.build callers stay two)
- Doctrine pins: `tests/test_doctrine_content.py`, `tests/test_doctrine_audiences.py`
- `GLOSSARY.md`, `README.md`, `ARCHITECTURE.md` stale pins (Rev 1 named none
  of these; the audit found them at `README.md:77-80`, `GLOSSARY.md:110`,
  `ARCHITECTURE.md:35,174`, `design/25-version-and-announcement.md:117-121`)

Rollout gates, in order: (1) occasion built + occasion-gated recovery +
audience-labelled metrics + indicate suppression; (2) eval suite green on 15
cases with zero false-speaks on knowledge-cut twins; (3) shadow mode review;
(4) Miles authorizes live participation sends. No participation sending ships
before the shadow gate closes.
