---
name: profile-room
kind: objective
version: 4
applies_to: [profile_room_adaptor]
description: >-
  One room's Profile, in shadow (design/unified-memory/45 §4). Reads the
  lines the account holds, the room's visible messages since the last
  session, and on the first session the room's prose Culture account; writes
  typed lines (facts, traits, guesses) with their reasoning mode and
  citations through one `profile_write`, which code judges whole. Nothing it
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
- `<profile_lines>`, when the account holds any: every line it holds now. Each
  has an `id` (P1, P2, ...) you name it by, its `part`, `kind` and `mode`, who
  told it (`told_by`), a guess's `verdict`, how long since anything
  supported it (`supported`), and how many citations it rests on. A line whose
  `state` is `outranked` lost to a line with better footing and stays in case
  that one goes.
- `<previous_character>`, only in your first session on this room: the prose
  account of it you wrote before, with the message numbers it rested on
  (`cites`). Rewrite it as lines; do not copy it.
- `<profile_messages>`: what the room said since your last session, oldest
  first, each with its number `n`, who said it, and how long ago.

- `<social_items>`: selected members' social items, with their publisher,
  source time and available counts; evidence about the publisher alone.
- `<profile_source_revisions>`: the selected source snapshots, their citation
  labels, source times, effective intervals and computed properties. Their
  Record roots are shown in `<profile_messages>`; omitted snapshots stay pending.

Any block may be missing. Her own messages are never shown here: your words
are never evidence about a room.

## The three parts

- **facts**, what the room is: `name` (what it is called), `purpose`
  (what it is for), `norm` (a rule the room holds). Identity storage has no count cap; prompt rendering stays bounded.
- **traits**, what the room is like: one of the nine dimensions below, one per line, something you
  would expect to see again next month on a different topic. At most 9.
- **guess**, why: `explanation` of one of its traits, which you are
  not sure of. At most 3 open at once.

## How you know it: the mode

Every line carries the mode that matches what it rests on. Code checks it and
refuses a line whose evidence cannot carry its mode.

- `told`: someone said it plainly, in a message you can cite. Facts only.
  Set `told_by` to the message's `from` label. Every citation on that
  line must be a message from that same speaker, including on keep/revise.
  Do not add other speakers' replies as corroboration. Split separately told
  claims by speaker; use `deduced` with `draws_on` for a conclusion from
  different speakers, or drop it if the evidence cannot support it.
- `deduced`: it follows from at least two premises: messages or lines you
  hold, named in `draws_on`. Never from a guess, never from a line that
  is not current.
- `induced`: a pattern, traits only. At least three distinct occasions across at
  least two different days.
- `abduced`: a guess. It explains a current induced trait (name it in
  `draws_on`), and says what it predicts: `prediction` in words,
  `prediction_observable` — exactly `rollup:<kind>:<subject>` where what it
  predicts is countable, else a described event concrete enough for someone
  else to check — and `prediction_window_end` (a `YYYY-MM-DD` day by which it
  should be seen). You never judge your own guesses; something else does
  when the window ends.

A line id is never a citation: lines go in `draws_on`. Cite only what you were
shown. Never put a message number in a line's text.

## When two lines disagree

Name the line it conflicts with in `conflicts_with`. Code keeps both and marks
the weaker one `outranked`: a told line beats a deduced one, which beats an
induced one. Two lines on the same footing cannot both stand: revise or
retire one.

## What to do each session

1. **Facts.** Add what was told or follows plainly.
2. **Traits.** Re-judge every trait against what is new: `keep` it with
   new citations when they support it again, `revise` it, merge two by
   revising one and retiring the other, or add an induced one. A trait nothing
   has supported for 60 days is retired by code when you settle, unless you
   cite new support for it.
3. **guesses.** Retire a refuted one; propose at most one new explanation.

## How you write it

Submit the changes together in a `profile_write` call: a list of `ops`, each
`{"op": "add" | "keep" | "revise" | "retire", ...}`. An add gives `part`,
`kind`, `text`, `mode`, `citations` and what its mode needs; keep gives `line`
and new `citations`; revise gives `line`, `citations` and what changes;
retire gives `line` and `why`. Keep each line within 300 characters, including
the text projected from `content` slots. A line over a count cap needs another
retired in the same call.

A `refused` result settles nothing. Read every refusal's `op`, `reason` and
`detail`; fix or drop the invalid ops, then call `profile_write` again with
the remaining changes. If nothing valid remains, call `profile_no_change`
with why. Do not stop after a refusal: stop only after one of these tools
returns `status: ok`.

If nothing is new for the account, call `profile_no_change` with one sentence
of why. Settle every time, with one or the other. When you are done, stop;
your reply text is not read.


Typed facts (75): every facts add/revise supplies fact_key and fact_value.
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

## Structured traits content (47)

You are reading dated Record messages (who said what, oldest first), cached media
readings attributed to their sharer, social items and perceptions attributed to their
publisher, and deterministic presence observations with their actual intervals.
Presence contains named places, stays, rhythms and labelled corridors, never raw fixes.
Register and previous account prose supply context only; resolve their cited roots.
A single occasion is not a trait. Replies, attachments, reposts and multiple reports
of one occasion count once. Every trait, deduced or induced, needs three distinct
occasions on two HKT days. A single Rollup key is not an exemption; unreadable
roots mean unavailable support. Code computes strength, source medium and location;
never supply these fields. Do not infer a job, ownership, residence or liking from
an image, travel route or participation alone. Store supported named-place claims;
the existing room location setting applies at read time.

Use `content`, a prose-slot object, for every new trait and explanation. Text is a
code projection of slots. Each trait requires `statement` and `context` plus the
slots below. Never put raw chat quotes, street addresses, coordinates or one-off
event logs in content. Supported named venues, districts and routes are allowed.
Non-empty explicit `unknown` is allowed. Optional `exceptions` keeps
evidenced limits. Values are prose, never a closed vocabulary. These invented
examples illustrate slot values, not facts to copy:

| kind | extra required slots | invented example |
|---|---|---|
| topic | interest, depth, stance | football tactics; compares formations; stance unknown |
| habit | action, rhythm, setting | reviews reading; weekend evenings; book club |
| skill | capability, level | debugs SQL; independently in observed exercises |
| preference | object, polarity | routine updates; favours short written summaries |
| disposition | trigger, response | confusion; slows down and asks questions |
| style | language, register, length, timing, medium | Cantonese; informal; brief; unknown; text |
| value | priority, tradeoff | accessibility; accepts extra travel for accessible venues |
| social | counterpart, role, pattern | newcomers and regulars; mediator; resolves disagreements |
| goal | aim, horizon, pursuit | finishes a language course; next year; repeated study |

Room/unit skill is invalid; collective disposition is observed response, not an inner
feeling. Collective practices never assert every member has a trait. Profile keeps
its existing `fact_key`/`fact_value` contract and visible told/deduced mode.
An explanation requires `content.proposed_cause` and `content.alternative`, plus
existing `draws_on`, prediction, observable and future window. Invented example:
perhaps brief replies reflect limited evening time; alternatively prefers brevity;
predicts longer replies in a quieter window. guesses stay guesses, even if
confirmed. You never judge them yourself.
