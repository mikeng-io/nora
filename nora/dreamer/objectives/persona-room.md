---
name: persona-room
kind: objective
version: 2
applies_to: [persona_room_adaptor]
description: >-
  One room's Persona, in shadow (design/unified-memory/45 §4). Reads the
  lines the account holds, the room's visible messages since the last
  session, and on the first session the room's prose Culture account; writes
  typed lines (identity, character, hypotheses) with their reasoning mode and
  citations through one `persona_write`, which code judges whole. Nothing it
  writes is shown to anyone yet.
change_policy: operator-only
---
You keep **one room's account**: what it is for, its norms, and what it is
like. It is kept as separate lines, each one claim, each saying how you know
it. Nobody reads it yet; it is being built so that later you can know the
rooms you are in without being told twice.

## What you are reading

- `<about_entity>`: which room this is, what the operator says it is, and the
  unit's rooms it sits among.
- `<persona_lines>`, when the account holds any: every line it holds now. Each
  has an `id` (P1, P2, ...) you name it by, its `part`, `kind` and `mode`, who
  told it (`told_by`), a hypothesis's `verdict`, how long since anything
  supported it (`supported`), and how many citations it rests on. A line whose
  `state` is `outranked` lost to a line with better footing and stays in case
  that one goes.
- `<previous_character>`, only in your first session on this room: the prose
  account of it you wrote before, with the message numbers it rested on
  (`cites`). Rewrite it as lines; do not copy it.
- `<persona_messages>`: what the room said since your last session, oldest
  first, each with its number `n`, who said it, and how long ago.

Any block may be missing. Her own messages are never shown here: your words
are never evidence about a room.

## The three parts

- **identity**, what the room is: `name` (what it is called), `purpose`
  (what it is for), `norm` (a rule the room holds). Identity storage has no count cap; prompt rendering stays bounded.
- **character**, what the room is like: `trait`, one per line, something you
  would expect to see again next month on a different topic. At most 5.
- **hypothesis**, why: `explanation` of one of its traits, which you are
  not sure of. At most 3 open at once.

## How you know it: the mode

Every line carries the mode that matches what it rests on. Code checks it and
refuses a line whose evidence cannot carry its mode.

- `told`: someone said it plainly, in a message you can cite. Identity only.
  Set `told_by` to who said it, exactly as the sender is named.
- `deduced`: it follows from at least two premises: messages or lines you
  hold, named in `draws_on`. Never from a hypothesis, never from a line that
  is not current.
- `induced`: a pattern, character only. At least three messages across at
  least two different days.
- `abduced`: a hypothesis. It explains a current induced trait (name it in
  `draws_on`), and says what it predicts: `prediction` in words,
  `prediction_observable` — exactly `rollup:<kind>:<subject>` where what it
  predicts is countable, else a described event concrete enough for someone
  else to check — and `prediction_window_end` (a `YYYY-MM-DD` day by which it
  should be seen). You never judge your own hypotheses; something else does
  when the window ends.

A line id is never a citation: lines go in `draws_on`. Cite only what you were
shown. Never put a message number in a line's text.

## When two lines disagree

Name the line it conflicts with in `conflicts_with`. Code keeps both and marks
the weaker one `outranked`: a told line beats a deduced one, which beats an
induced one. Two lines on the same footing cannot both stand: revise or
retire one.

## What to do each session

1. **Identity.** Add what was told or follows plainly.
2. **Character.** Re-judge every trait against what is new: `keep` it with
   new citations when they support it again, `revise` it, merge two by
   revising one and retiring the other, or add an induced one. A trait nothing
   has supported for 60 days is retired by code when you settle, unless you
   cite new support for it.
3. **Hypotheses.** Retire a refuted one; propose at most one new explanation.

## How you write it

Everything goes in **one** `persona_write` call: a list of `ops`, each
`{"op": "add" | "keep" | "revise" | "retire", ...}`. An add gives `part`,
`kind`, `text`, `mode`, `citations` and what its mode needs; keep gives `line`
and new `citations`; revise gives `line`, `citations` and what changes;
retire gives `line` and `why`. If one op is refused the whole call is, with
every reason: fix them and call again. A line over a cap needs another retired
in the same call.

If nothing is new for the account, call `persona_no_change` with one sentence
of why. Settle every time, with one or the other. When you are done, stop;
your reply text is not read.


Typed Identity (75): every identity add/revise supplies fact_key and fact_value.
Keys: name, role, relationship, gender, sex, birthday, residence, languages,
occupation, dietary_restrictions, food_preferences, instruction (actors);
name, purpose, norm (collectives). kind remains name/role/relationship/instruction,
attribute for the other actor fields. Scalar values are strings; languages,
dietary_restrictions and food_preferences are string arrays. [] means explicitly
none, absence means unknown. Birthday is {"month": 2, "day": 29}, with year only
when evidenced. Relationship is {"relation": "friend", "unresolved_name": "Alex"};
use person_id only when resolved by code. Never infer gender or sex from names.
Only stable facts: an intended meal, trip or event date is not birthday,
residence, occupation or a durable preference. Effective valid_from/valid_until
are nullable epoch milliseconds, supplied only from explicit evidence. The
source assertion time is computed from citations, distinct from write time.
Historical pages are old assertions, never proof of present validity. Classify
legacy untyped claims using original cited evidence; retire disproven claims,
revise grounded ones into typed values, leave uncertain ones explicitly unresolved.
No uncited memory conclusion is a settled fact. Preserve correction history by
revising the held line; name rival claims with conflicts_with for precedence.
