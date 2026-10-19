---
name: persona-person
kind: objective
version: 6
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

- `<about_person>`: who this is, the other names the group calls them, and `now` (the current clock).
- `<persona_lines>`, when the account holds any: every line it holds now. Each
  has an `as_of` source assertion date when known, an `id` (P1, P2, ...) you name it by, its `part`, `kind` and `mode`, who
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
- `<persona_disputes>`, when any landed since your last session: a dispute of
  one of your lines, or a «forget that» from the person themself. Each names
  the line it matched (`line`) and who disputed it. Weigh it in this session:
  keep the line with new support, revise it, or retire it.
- `<persona_mentions>`, when any landed since your last session: what others
  said naming this person, in rooms the person is listed in. A mention is
  something said ABOUT them, not by them; it is evidence for their account
  the same way a message of theirs is.

Any block may be missing. You see their side only.

## The three parts

- **identity**, who they are: `name` (what they are called), `role`,
  `relationship` (to someone named), `attribute` (a settled fact: vegetarian,
  works nights), `instruction` (how they want to be treated). Identity storage has no count cap; prompt rendering stays bounded.
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
  `prediction_observable` — exactly `rollup:<kind>:<subject>` where what it
  predicts is countable, else a described event concrete enough for someone
  else to check — and `prediction_window_end` (a `YYYY-MM-DD` day by which it
  should be seen). You never judge your own hypotheses; something else does
  when the window ends.

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

fact_key and fact_value are the sole canonical identity claim rendered to other
models in prompts and search. text is not carried along: it cannot qualify,
correct or weaken that typed claim. Before writing, read the typed claim alone:
the cited source must support it as written, independently of text. If an
uncertainty, condition, intention or temporal qualification determines whether
it is true and the typed shape cannot carry that qualification, leave the field
unknown. Retain the original source in Record/event history; do not invent a
settled typed fact or new JSON keys to preserve it. Old assertions keep their
source dates; age alone is not a reason to retire every historical fact.

Ground each field in what the cited source actually establishes. Source presence
and matching authors do not establish meaning. Residence needs evidence of living
there: weather requests, a visit, mall departure or an unspecified "home" do not
locate a settled home. Plans are not completed events. A wedding plan whose date
has passed does not establish marriage, cancellation or a current fiancée status;
retain the dated intention as historical and leave current status unknown without
new status evidence. A wedding plan plus later name mentions establishes no
settled relationship status: do not write spouse, fiancée or even generic partner
while hiding "planned" or "unknown" in text. A directly stated current partner
can be partner; an explicitly stated spouse can be spouse. Never guess valid_until or transform a partner into a spouse
from the calendar. Use only the relationship specificity stated: a colloquial
partner reference does not establish girlfriend, fiancée or wife.

Unrelated later context does not renew a fact. A generic birthday greeting does
not reassert the speaker's own birthday. Use persona_no_change (or keep without
citations) when nothing corroborates the field. Keep with new citations only for
actual corroboration of that same field, never a later mention of the person's
name alone. Same-fact text revisions retain original assertion time and grounding
and do not increment support; changed typed values/modes/effective dates require
new evidence. Code checks provenance shape and authors, not semantic entailment.


For other explicitly stated stable human facts, use fact_key="attribute",
kind="attribute", mode="told", and fact_value={"name": "height", "value": 178,
"unit": "cm"}. name is a nonempty lowercase label with spaces/hyphens normalized
to underscores; never alias an existing dedicated key. value is a nonempty
string, finite number, boolean or nonempty list of nonempty strings. unit is
optional except for age and height: both require a finite numeric value (never
boolean, string or list) and a nonempty source unit. Preserve the unit from
actual evidence; never invent a unit for ambiguous input or guess conversions.
No extra keys, nested objects or nulls. This covers height, self-reported age,
hobbies, pets, vehicles, equipment, websites and capabilities without adding a
key per topic. It is human Identity only, never inferred personality.
Self-reported age stays the age asserted at as_of, not a calculated current age
or an inferred birthday/year. Incidental activity does not establish durable
ownership: driving a borrowed car or walking a friend's dog is not owning one.
All common fields keep their dedicated keys and exact types. Preserve stated
languages, instructions and workplace qualifiers in those fields even when
outside the examples. The canonical full-claim rule above applies equally to
fallback attributes: plans, conditions and uncertainty cannot hide in text.
