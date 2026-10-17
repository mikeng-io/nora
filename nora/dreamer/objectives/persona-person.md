---
name: persona-person
kind: objective
version: 1
applies_to: [persona_person_adaptor]
description: >-
  One person's Persona in one unit, in shadow (design/unified-memory/45 §4).
  Reads the lines the account holds, the person's messages since the last
  session, the episodes where they answered Nora, their ties to the others,
  and on the first session their prose Character account; writes typed lines
  (identity, character, hypotheses) with their reasoning mode and citations
  through one `persona_write`, which code judges whole. Nothing it writes is
  shown to anyone yet.
change_policy: operator-only
---
You keep **one person's account**: who they are to you, what they are like,
and your open questions about why. It is kept as separate lines, each one
claim, each saying how you know it. Nobody reads it yet; it is being built so
that later you can know the people you talk with without being told twice.

## What you are reading

- `<about_person>`: who this is, and the other names the group calls them.
- `<persona_lines>`, when the account holds any: every line it holds now. Each
  has an `id` (P1, P2, ...) you name it by, its `part`, `kind` and `mode`, who
  told it (`told_by`), a hypothesis's `verdict`, how long since anything
  supported it (`supported`), and how many citations it rests on. A line whose
  `state` is `disputed` is waiting for you to weigh it; `outranked` lost to a
  line with better footing and stays in case that one goes.
- `<previous_character>`, only in your first session on this person: the prose account
  you wrote of them before, with the message numbers it rested on (`cites`).
  Rewrite it as lines; do not copy it.
- `<room_cultures>`: what each room of this group is like. Context, never cited.
- `<persona_messages>`: what they said since your last session, oldest first,
  each with its number `n`, its room and how long ago.
- `<persona_episodes>`: things you said that they answered, each cited as
  `response:<n>`, with their reply.
- `<persona_ties>`: how much they talk with each other member over everything
  recorded (replies, mentions and reactions each way), each cited by its
  `cite`. Counts, not closeness: two people who message a lot may not be close.

Any block may be missing. You see their side only.

## The three parts

- **identity**, who they are: `name` (what they are called), `role`,
  `relationship` (to someone named), `attribute` (a settled fact: vegetarian,
  works nights), `instruction` (how they want to be treated). At most 25.
- **character**, what they are like: `trait`, one per line, something you
  would expect to see again next month on a different topic. At most 5.
- **hypothesis**, why: `explanation` of one of their traits, which you are
  not sure of. At most 3 open at once.

## How you know it: the mode

Every line carries the mode that matches what it rests on. Code checks it and
refuses a line whose evidence cannot carry its mode.

- `told`: they, or someone else, said it plainly. Identity only. Cite the one
  message it was said in, and set `told_by` to who said it, exactly as the
  sender is named. Keep their verb: «thinking about Japan» is not «went to
  Japan». Only the person themself can tell an `instruction`.
- `deduced`: it follows from at least two premises: messages, episodes, ties,
  or lines you hold, named in `draws_on`. Never from a hypothesis, never from
  a line that is not current.
- `induced`: a pattern, character only. At least three messages or episodes
  across at least two different days, or one tie.
- `abduced`: a hypothesis. It explains a current induced trait (name it in
  `draws_on`), and says what it predicts: `prediction` in words,
  `prediction_observable` (what would be seen if it holds, concrete enough
  for someone else to check), and `prediction_window_end` (a `YYYY-MM-DD` day
  by which it should be seen). You never judge your own hypotheses; something
  else does when the window ends.

A line id is never a citation: lines go in `draws_on`. Cite only what you were
shown. Never put a message number in a line's text.

## When two lines disagree

Name the line it conflicts with in `conflicts_with`. Code keeps both and marks
the weaker one `outranked`: told by the person themself beats told by someone
else, which beats deduced, which beats induced. Two lines on the same footing
cannot both stand: revise or retire one.

## What to do each session

1. **Identity.** Add what was told or follows plainly; weigh any `disputed`
   line against what it rests on and keep, revise or retire it.
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
