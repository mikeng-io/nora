---
type: proposal
title: "Ambient Participation — A Third Occasion, Not a Fork"
description: "How Nora can choose to speak in a room without being tagged: a third Event occasion on the existing turn machinery, a doctrine audience that makes silence a first-class outcome, and an evaluation suite that gates the flip. No forked codebase, no copied tools, no copied doctrine — every difference is a parameter, an audience, or an added section."
tags: [nora, ambient, occasion, silence, determination, participation, eval]
timestamp: '2026-09-05'
edges:
  - {dst: 26-standing, rel: provides_input, provenance: asserted}
  - {dst: 15-roadmap, rel: extends, provenance: asserted}
  - {dst: 06-trigger, rel: revises, provenance: asserted}
  - {dst: 13-doctrine, rel: extends, provenance: asserted}
---

# Ambient Participation

## 1. The goal, stated as a failure

Nora currently speaks in a room for exactly two reasons: she was tagged, or a
reminder/announcement came due. Both occasions are **must-speak** — the turn
exists because speaking is the point. Tag forces a `send_reply` call on the
first model call (`tool_choice = "required"`); fire recovers final prose into a
delivery because the whole occasion is "say this thing".

**A room she could genuinely serve does not show up in either category.** The
question with no answer, the fact she holds that nobody asked for yet, the
stalled thread, the mistake she could quietly correct — these are ambient
opportunities, and today they are structurally invisible to her. That is the
failure this design closes: **ambient participation is the capability to speak
when nobody called, judged against the room's actual state, with the right to
stay silent.**

"Real ambient" is not a tag-substitute. It is a third occasion with the
opposite default: **speech is the exception, silence is success.**

## 2. What is NOT being forked

The obvious first instinct — "she needs a different agent for ambient" — is
wrong, and this section exists to kill it with the precedent already in the
repo. `nora/app/fire.py` established the pattern: unprompted speech is not a
second pipeline built beside the tag path, it is the **same turn machinery**
parameterized by an `Event` — where she speaks, on whose authority, what words
are put to her, why this turn is happening. Ambient is the third `Event`.

The turn machinery is shared, deliberately and structurally. Context assembly
(the `build_assembler` parity work), `Deliverer` plus all five guards (R-2
latch, R-4 empty, R-6 leak, R-18 egress, A9 audit-first), standing/notes/facts
injection, the Honcho feed, the Langfuse trace, the audit-first write, the
feedback stage — every one of these is reason to NOT fork, because forking
means maintaining two copies of the drift-prone parts we just unified.

What differs is declared, not copied:

| Difference | Mechanism | Forked? |
|---|---|---|
| When she may speak | New `Event` occasion ("ambient") | no — third value of an existing enum |
| Doctrine content | `applies_to` audience (`[ambient]`), not a copy | no — audience filter on existing sections |
| Send semantics | `tool_choice = auto`, no final_text recovery, silence-as-success metric | no — parameter + outcome labelling |
| Tool surface | Same tools, same descriptions, same registry | no — descriptions do not carry "when to use" |
| Evaluation | Ambient eval suite (determination + response quality) | no — new cases on the parity harness |

## 3. The determination gate: relevance AND grounded value

A message is not "worth replying to" in the abstract. The gate is two
independent AND-ed conditions, and the second is the one that makes "real
ambient" hard:

**① Relevance — does this message deserve a reply from anyone?**
Status updates, pure acknowledgements, private two-person exchanges, inside
jokes, commands directed at another service — these are relevance-No, and the
turn should never start.

**② Grounded value — does NORA have something true to add?**
This is the gate that did not exist in the earlier discussion and cannot be
approximated by relevance alone. A relevant question ("does anyone know when
AWS Community Day 2026 is?") with no matching fact in her standing/notes/facts
is a **silence**, not an "I don't know". In tagged mode "I don't know" is an
acceptable obligation — she was called. In ambient mode nobody called her;
speaking just to prove she is listening is noise. **Ambient speech without
grounding is the identity violation this design exists to prevent.**

The evaluation suite makes this distinction the load-bearing test. Every
relevance-Yes scenario has a knowledge-cut twin: same message, same transcript,
only the seed differs (fact present vs absent), and the verdict must flip from
speak to silence. A model that answers S1b from parametric memory has failed —
her memory boundary is her standing/facts, not what the base model happens to
know.

## 4. Doctrine: same files, new audience

The three-layer principle already in the repo — `conduct.md` writes WHEN to
act, `voice.md` writes HOW it sounds — maps cleanly onto occasions:

| File | Audience change |
|---|---|
| `identity.md` | unchanged (`always`) — who she is does not vary by occasion |
| `voice.md` | unchanged (`always`) — how she sounds does not vary by occasion |
| `silence.md` | **must be redefined** — it currently says "you never reply to a message that didn't call you", which is the exact claim ambient overturns. Becomes: tag = must-speak, ambient = may-speak. |
| `conduct.md` | mostly `always` — judgement, checking-first, banter rules do not vary. An ambient-specific section is added declaring: you have the right not to reply; no grounded value = silence; silence is a success, not a failure. |
| **new** `ambient-conduct.md` | `applies_to: [ambient]` — the three ambient-specific rules, so the tag path's silence rules never leak into ambient and vice versa. |

None of these copy a file. The audience filter (`DoctrineStore.get(applies_to=...)`)
already exists; the sections that currently say `applies_to: [tag]` either
become `always` (when the rule holds across occasions) or gain an ambient sibling
(when it does not). The only genuinely new file is the ambient conduct section.

## 5. Tool choice: an argument, not a fork

`nora/agent/tool_choice.py` currently hardcodes `FIRST_CALL = "required"` at
module level. That is the must-speak assumption leaking into the turn loop. The
fix is a parameter threaded through `invoke`:

| Occasion | tool_choice | Rationale |
|---|---|---|
| tag | `required` | she was called; the turn exists to answer |
| fire | `required` | the occasion is a due reminder/announcement — must-speak |
| ambient | `auto` | speaking is optional; the model decides per turn |

No second tool_choice implementation. The `required`/`auto` sequence logic in
`tool_choice.py` is occasion-independent; only the initial value changes.

## 6. Tool descriptions: unmodified, except one accuracy fix

The `send_reply` description says "You MUST call this tool exactly once
whenever you produce a reply" — the MUST is conditional on producing a reply,
which is correct for every occasion. It also says "it always goes to the
conversation you were tagged in", which is already false for fire (a reminder
is not a tag) and would be false for ambient. **That one phrase becomes "the
conversation this turn belongs to"** — a truth across all three occasions,
with no per-occasion copy.

The deeper principle: tool descriptions describe HOW a tool works, not WHEN it
should be used. "When" belongs to doctrine (section 4). A tool description
that says "use me when X" is doctrine smuggled into schema, and ambient would
need a second copy of every such tool just to express the different "when".
The registry filter (if any tool is truly ambient-inappropriate, e.g. a
hypothetical `send_gif`) is a declarative field on `ToolSpec`, not a copy of
the tool.

## 7. Send path: silence as a first-class outcome

The current fire path treats `no_send_call` as a failure
(`nora_silent_failures_total{reason="no_send_call"}`). For ambient, **silence
is the desired outcome** — the metric must be inverted:

- `nora_ambient_silence_total` — increment when the ambient turn ends without
  a send_reply call (the healthy path)
- `nora_ambient_spoke_total` — increment when she did reply
- No `final_text` recovery: fire's recovery exists because a fire occasion
  must deliver its content. Ambient recovers nothing; a model that wrote prose
  without calling `send_reply` produced a draft, not a message.

## 8. R-1b: the two-path rule, consciously evolved

`tests/architecture/test_only_two_paths_can_send.py` pins the current truth:
`deliver.build` has exactly two callers (tag pipeline and fire path). Ambient
adds a third caller — this is the second evolution of the R-1 rule (ambient
"never reaches the agent" → fire → ambient). The test and the decision index
must be updated in the same commit that adds the ambient caller, and the
docstring rewritten to state the new rule: **three senders, all named, all
through the same Deliverer.**

## 9. Evaluation: the door that must open before the flip

Section 3's knowledge-cut pairs are not hypothetical. The evaluation suite is
the deliverable that makes the flip live safe:

- 10 scenarios (the original 5 YES / 5 NO) + 5 knowledge-cut twins (S1b–S5b),
  15 cases total.
- Each case is `transcript + standing + incoming → expected verdict (speak /
  silence)`, seeded through the just-merged parity harness so the eval prompt
  is byte-identical to production.
- Assertion on the verdict is mechanical (send_reply called or not) — the
  suite does not need a judge model for the decision axis.
- A second axis (reply quality when speak) is judged by the existing eval
  judge (`llm.deep`), never the flash model.
- Precision/recall over the 15 cases gate the flip: zero false-speaks on the
  knowledge-cut twins is the hard bar; the original 5 silent cases must stay
  silent.

## 10. Open questions (for Miles)

1. **Determination layer**: lightweight classifier pre-filter before the full
   model, or full model on every message? Lean: full model first (fewer
   components, the model has all context; the classifier's precision problem
   is ill-defined until evals exist), classifier later only if cost forces it.
2. **Ambient trigger cadence**: evaluate every message, or debounced (e.g. at
   most once per N minutes per room)? R-9's debounce protects the tag path;
   ambient has its own noise floor to define.
3. **Shadow mode**: the safe rollout — ambient determinations write to a
   mirror channel (or log) without sending, Miles reviews, then flip. This is
   the "真 shadow mode" discussed; it is a rollout gate, not a separate runtime.
4. **"I can look that up"**: the middle case between answering and silence.
   Lean: v1 never — it creates a commitment and a follow-up obligation; silence
   is safer and cheaper than a promise.

## 11. Migration & governance

This is a governed change: it adds a third send path (protected state), a new
doctrine audience, and a new metric family. Per the operating rules it needs:
the decision-index row alongside the R-1b test change, the design doc reviewed
by Miles, the eval suite green before the flip, and the shadow-mode rollout
approved before live sends are enabled. No ambient sending ships before the
shadow gate closes.
