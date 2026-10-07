---
type: design
title: "Intent: why the doctrine shrinks to law, and the order that makes it safe"
description: "The cognition loop makes doctrine her law and puts her growth below it. A doctrine that carries personality and practice blocks that growth, forces one personality on every unit, and turns tension into noise. So the doctrine shrinks to law, but only after the tiers that receive what it sheds exist: Character, the self block, Lessons."
tags: [nora, self, doctrine, character, lessons, cognition]
timestamp: '2026-10-07'
edges:
  - {dst: 10-character, rel: sequences, provenance: asserted}
  - {dst: ../cognition-loop/55-self.md, rel: realizes, provenance: asserted}
---

# 00 · Intent

## 1. The problem

The doctrine is law (`design/cognition-loop/55-self.md` §3, operator decision
D6). Nora never changes it. Her growth lives only in her own blocks, which
render after it and below it: Character · Nora (`self`), Lessons (`lessons`)
and Standing. Every lesson and every Character · Nora revision passes a
doctrine check. A lesson that would loosen or contradict the doctrine is
refused and written as a tension row for the operator
(`design/cognition-loop/50-character-and-standing.md` §6).

Today the doctrine carries more than law. `nora/doctrine/rooms/voice.md`,
`nora/doctrine/rooms/belonging.md` and much of
`nora/doctrine/rooms/conduct.md` describe who she is and how she practises:
banter, swearing, stickers, "usually one reply", "short is the default", how
to weave memory in. While that text is law:

1. **Growth cannot move it.** A unit whose people keep showing they want
   something else can never teach her that. Her growth produces only tension
   rows.
2. **Every unit gets the same her.** Character and Lessons are per unit
   (`55-self.md` §5). The doctrine is global.
3. **Tension becomes noise.** The operator called tension the experiment's
   most interesting possible result. It only means something when it is
   pressure against a real law, not taste against taste.
4. **The doctrine check gets harder.** It is a model reading a lesson against
   the doctrine. Law mixed with style asks it to judge whether "be drier here"
   contradicts a line about humour (AGENTS.md: a model's input has to explain
   itself).

## 2. The goal

The doctrine holds only what must be true in every unit, that no lesson may
loosen and no Character revision may contradict. Practice moves to Lessons.
Character moves to Character · Nora. Surfaces with no unit (the public
surface, summons) keep an authored minimum, because no `self` or `lessons`
block can reach them.

## 3. The order (operator, 2026-10-07)

| step | what | node | why here |
|---|---|---|---|
| 1 | Character for every entity: room, unit, person, Nora | [10](10-character.md) | the receiver for character text; most of the machinery exists |
| 2 | The self block | [20](20-self-block.md) | renders Character · Nora below the doctrine; retires `self_card` |
| 3 | Classify the doctrine, no edits | [30](30-doctrine-sort.md) | the doctrine check must read law only, or Lessons produces noise |
| 4 | Lessons | [40](40-lessons.md) | the receiver for practice text |
| 5 | Cut the doctrine to law | [30](30-doctrine-sort.md) §4 | only once 1, 2 and 4 carry what it sheds |

Step 3 is an insertion into the operator's order, not a change to it: the
operator's "doctrine at the end" is step 5. Without step 3, the doctrine check
judges every lesson against today's personality-heavy doctrine, and "this unit
prefers two options, not five" is refused as a contradiction of `conduct.md`.

## 4. What exists on 2026-10-07

Read from master at 67520d7.

| part | built | what exists |
|---|---|---|
| Character · room (culture) | partly | `nora/language/culture.py`: a Dreamer adaptor with nine read tools, once a day per room with new messages (watermark; a quiet room never fires, DEC-183) |
| Character · person | simplified | DEC-278/280: the same room pass pre-fetches one `memory.chat` answer per person who posted and writes their account in the same store; rendered as `<character>` (replaced by C2b: one session per (unit, person), [10](10-character.md) §2) |
| Character · unit | no | |
| Character · Nora | no | one line of "Nora's own standing" inside each room account |
| `self` block | no | `self_card` is the nearest thing: Honcho's record of what she said and did before, episodic continuity rather than self-description (`nora/language/self_card_render.py`) |
| Lessons, doctrine check, tension | no | |
| Standing · room | yes | live since DEC-239 and the step 4 cut-over (DEC-277) |
| Standing · unit, person | no | |
| `response` episodes | yes | Rollup step 2a (`nora/infra/sql/rollups.py`): each of her sends with the replies, reactions and edits inside the window, or `silent` |
| `character_read` | no | in the Cognition catalogue, marked "not built yet" (`nora/app/cognition_wiring.py`) |

Character (step 1) is finished before Lessons starts (operator, 2026-10-07):
Lessons reuses its wake, its write-verb checks and its pairing of sends with
responses. Only recording the brief's `ask` intent (Lessons L0) runs beside
step 1, because `silent` needs weeks of data.

## 5. Parked

- Recommendations as a runtime skill, and whether personalisation goes back to
  a skill. ADR 0016 §8 moved personalisation into `conduct.md` on reach; that
  stands until measured otherwise (`docs/adr/0016-skill-policy.md`).
- Standing for units and persons. Step 8 of the cognition loop builds it; this
  bundle does not need it.
- The portal correction page, `character_dispute` and the rebuild-drift
  metric. No harm is shown yet (AGENTS.md, "A guard needs a consequence").

## 6. After the cognition loop: Honcho, and one search over every tier

Operator, 2026-10-07, tracked in #736. Character, culture and Lessons rebuild
most of what Honcho derives, cited and per unit. Once they are live, Honcho
is merged or retired. What it does that nothing here replaces: search over
history, a question-answering path (`ask_memory`), and stated person facts
(`remember`). The first two become one central retrieval over every tier,
WeMM-Embedding-2B the model candidate (`design/conversational-recall/`):
each hit carries its tier (evidence may be cited; state and derived rows are
linked, never cited; tension rows are operator-only), the caller's
permissions are applied inside the query before ranking (unit partition,
room lineage and the DisclosureGuard, caller kind, egress screens), and the
index is a rebuildable projection. This bundle's only obligation now:
memory stays a tool the Character session may call, never a dependency
([10](10-character.md) §3.3).
