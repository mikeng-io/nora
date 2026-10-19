# The Persona adaptor: schema, reasoning modes, writes and curation

Status: **built in shadow**, amended 2026-10-19 by [75](75-typed-identity.md).
The live render switch remains subject to historical coverage and reply acceptance. **Amended 2026-10-15 (operator)** on an audit:
line-to-line citations become links (§3), hypotheses are graded by something
other than their writer (§3), the build's prerequisites are listed (§4),
shadow changes no prompt (§6, §8), and the coverage gate fails closed (§6).

**§1 to §4's storage and write built 2026-10-17 (00 «Sequence» step 5,
DEC-317)**: `persona`, `persona_line` (migration `0117_persona`), the judge
`nora.domain.persona.plan_write` and the one write path
`nora.infra.sql.persona.SqlPersonaStore.persona_write`. What the build
settled that this node left open is marked «Built» where it applies. The
adaptor (§4's session) is built since 2026-10-18 (in shadow): mentions,
disputes and the verdict judge included. The unit, room (Culture) and Nora
sessions are built the same day (in shadow, DEC-319): the person session's checked
writer over each collective's own evidence
(`nora/language/persona_collective.py`), the verdict judge grading a
collective's hypotheses on its own rooms. Rendering and portal/index wiring are built;
behavioral coverage and cutover acceptance remain unrun.

## 0. This reverses "prose, no schema", deliberately

Character accounts today are prose with no section schema:

- **DEC-184:** a three-section JSONB body was drafted and rejected
  (migration `0055_culture`). It "could not have held 「秘書」", a role a room
  invented that no column anticipated, and a required "what shifted" section
  made the first revision unwritable.
- **DEC-290** settled the same for Character: "prose, no schema"
  (self-growth/10 §7).
- **DEC-302:** an account is a synthesis, usually three traits in 200–600
  characters, re-judged each time and never a growing record.

**Operator, 2026-10-15:** the Persona must be well formatted, not free
style. This node reverses DEC-184 and DEC-290 for Persona, and keeps what each
of the three learned:

| learned | kept as |
|---|---|
| a fixed set of fields cannot hold what a group invents (DEC-184) | **the structure is the container, the content is prose.** Each line is one free-text claim. Its part, kind, mode, citations and state are typed and checked; what it says is not enumerated. A room's invented role is one `role` line in its own words. |
| no section may be required (DEC-184) | no part and no kind is required; an account may hold one line |
| provenance sits around the prose, not inside it (0055, the `note_revision` precedent) | citations, mode and state are columns of the line, never markup in its text |
| a synthesis, not a log (DEC-302) | hard caps per part (§2), and every session re-judges every held line |

## 1. Storage

Keys are today's culture keys: `{unit}#character:{person}`, `{unit}#unit`,
`{unit}#nora`, and a room's `primary_id` (GLOSSARY).

**`persona`**: one row per account.

| column | type | holds |
|---|---|---|
| `key` | text, primary key | the entity's key |
| `entity_kind` | text | `human`, `nora`, `agent`, `room`, `unit` |
| `revision` | int | bumped by every committed `persona_write` |
| `updated_at` | bigint | |

**`persona_line`**: append-only; a change inserts a row that supersedes
the old one.

| column | type | rule (CHECK or validator) |
|---|---|---|
| `id` | uuid | |
| `persona_key` | text → `persona.key` | |
| `part` | text | `identity`, `character`, `hypothesis` |
| `kind` | text | identity: `name`, `role`, `relationship`, `attribute`, `instruction`, `purpose`, `norm`; character: `trait`; hypothesis: `explanation`. CHECK pairs kind with part. |
| `text` | text | one claim, prose, at most 300 characters; no message id, no coordinate (R-18/R-28 screen) |
| `mode` | text | `told`, `deduced`, `induced`, `abduced`; allowed per part in §3. Authored lines are rendered, never stored |
| `citations` | text[] | non-empty; each a Record row id, a Rollup key or an episode `response:<id>`. **Never another line's id** (DEC-249: an entry → entry reference is a link, not a citation) |
| `draws_on` | uuid[] | the lines a `deduced` line rests on, as typed links ([25](25-memory-graph.md) §2); empty otherwise |
| `told_by` | text | the speaker's person id when `mode = told`, else null |
| `prediction` | text | required when `mode = abduced`, else null: the prediction in words |
| `prediction_observable` | text | required when `mode = abduced`: what would be seen, named so a judge can check it (a count over a Rollup key where it is countable, else a described event) |
| `prediction_window_end` | bigint | required when `mode = abduced`: when the window closes |
| `state` | text | `current`, `disputed` (awaiting weighing, one meaning only), `outranked` (lost on precedence), `hidden` (the subject disputed it or said "forget that"), `superseded`, `retired`, `refuted` |
| `verdict` | text | for a hypothesis: `open`, then `confirmed`, `refuted`, `expired` (window closed, observable never occurred) or `unknown` (the observable could not be read); set by the judge (§3), never by this adaptor |
| `verdict_by`, `verdict_at` | text, bigint | the judge that set it (code or a named judge model) and when |
| `supersedes` | uuid | the line it replaces, or null |
| `support_count` | int | how many distinct citations have supported it over its life |
| `last_supported_at` | bigint | the newest evidence that supported it |
| `unit`, `exclude_persons`, `as_of` | | today's derived-row columns (cognition-loop/10 §3); scope is the unit ([35](35-persona.md) §3; the `lineage` column stays computed but `[lineage]` stays off) |
| `written_by_session` | text | the adaptor run that wrote it |

The lines are also index nodes, and their citations, `about`, `supersedes`
and `draws_on` become edges inside the write's own `begin()` block
([25](25-memory-graph.md) §7).

**Built (2026-10-17), and what it settled:**

- **The account is the chain heads.** Append-only means an old row is never
  re-stated as `superseded`: a row is superseded when another row names it
  in `supersedes` (unique, so a line is superseded once and two racing
  writers cannot both land). `superseded` stays in the vocabulary and is
  never written.
- **`draws_on` also carries the traits a hypothesis explains.** §3 requires
  an abduced line to explain a current induced trait, and a line id is never
  a citation, so the link is `draws_on`; it is empty for told and induced
  lines (a CHECK).
- **`written_at`** (epoch ms) is the row's own time; `recorded_at` is system
  time, as on every append-only table. `lineage` is not a column: lineage is
  off and the unit is the boundary (35 §3).
- **The shadow is the tables themselves.** Keys are today's culture keys in
  a new table that nothing renders, so a separate shadow key adds nothing;
  the switch (step 8) is what makes these rows render.
- **Not projected into the index yet.** A node is a `memory_search` hit, and
  shadow changes no prompt (§6); lines join the index at the switch.

## 2. The parts, their kinds and their caps

| part | kinds | cap (current lines) | decays |
|---|---|---|---|
| identity | `name`, `role`, `relationship`, `attribute`, `instruction` (actors); `name`, `purpose`, `norm` (collectives) | unbounded storage, bounded rendering | no; replaced only by a later told line or a dispute the adaptor accepts |
| character | `trait` | 5 | yes: unsupported for 60 days → `retired` at the next session unless re-evidenced |
| hypothesis | `explanation` | 3 whose window is still open | refuted → retired; confirmed stays a hypothesis |

**Identity's former 25-line storage cap is removed by 75. The character/hypothesis caps
(5 / 3) and the 60-day decay are starting values** (amended
2026-10-15), to be sized on data: the line counts and re-support intervals the
shadow sessions actually produce (00 «Sequence» step 6), not these guesses.

A hypothesis holds its slot until its window closes, whatever its verdict, so
confirming one early cannot free a slot for another.

`Accounts` (platform identities) are rendered from `identity.toml` and are
never a line a model writes (30 §3). **A collective's names** are `name`
lines; Culture's `Names and where they came from` section (cognition-loop/12
§4) is retired into them. A collective's members stay the roster's
(`room_members`). Over a cap, the write must retire a line in the same call,
and the refusal says which cap.

## 3. The reasoning modes, enforced

**This is the one amended "allowed in" table for Persona** (2026-10-15); it
amends cognition-loop/10 §4 for this tier, and [35](35-persona.md) §1 defers
to it.

| mode | allowed in | must cite | refused when |
|---|---|---|---|
| `told` | identity | the message it was said in | the citation is not a message the session was shown; the message was said in a room the subject is not listed in ([35](35-persona.md) §3); a `instruction` whose `told_by` is not the subject |
| `deduced` | identity, character | at least 2 premises: cited Record rows, Rollup keys or episodes, and premise lines linked by `draws_on` | fewer than 2 premises; a line id in `citations`; any premise line is `abduced` or not `current` |
| `induced` | character | at least 3 instances on at least 2 calendar days, or one Rollup aggregate key | too few instances; all on one day |
| `abduced` | hypothesis | at least one current `induced` trait it explains, and a `prediction` | no prediction; it explains no induced trait; the prediction names no observable or no window |

**Authored lines** (names and accounts from `identity.toml`) carry no mode:
`authored` is not one of cognition-loop/10 §4's six modes. They are rendered
from the operator's files and never written as lines.

A `told` line keeps the verb it was told with: "thinking about Japan" is not
"went to Japan" (30 §4). Precedence when two current lines conflict: authored >
told by the subject > told by someone else > deduced > induced. Both stay, the
lower marked `outranked` (not `disputed`, which means only "awaiting
weighing"), until a later statement settles them.

**A hypothesis is a question, not a fact.** It renders under "open
questions", and Nora may ask it. Assent ("yeah") never changes its verdict
(cognition-loop/10 §4).

**It is not graded by its writer** (amended 2026-10-15; the first version had
each session grade its own predictions). The verdict is set when
`prediction_window_end` passes, by a judge other than the Persona adaptor:
code, where `prediction_observable` is countable (a Rollup key, a count of
response episodes); otherwise a separate judge model given the observable,
the window and the Record rows inside it, and nothing the adaptor wrote. The
outcomes stay distinct: `confirmed`, `refuted`, `expired` (the observable
never occurred in the window) and `unknown` (it could not be read, R-27), and
`unknown` is never counted as either side. The confirmation rate's
denominator is **every hypothesis ever written**, retired or not, so a writer
cannot improve its rate by retiring the ones going badly.

## 4. The adaptor

The `character-person` adaptor (DEC-290), renamed and widened. The unit,
room (Culture) and Nora accounts follow the same shape with their own inputs.

**Build prerequisites** (2026-10-15; none exists today):

1. **`Citable` widens** from an id to `id → (ts, person_id, room)`, so the
   validator can check a citation's day (the induced rule), its speaker (the
   told and instruction rules) and its room (35 §3).
2. **Rollup keys and response episodes become citable**: today the validator
   accepts neither.
3. **The time zone for "2 calendar days"** is Hong Kong time (HKT, UTC+8),
   stated in the validator, not taken from the host.
4. **The stimulus wake is new.** The dream loop wakes on evidence counts and
   staleness only; a wake on a named stimulus is built here.

**Wakes:** today's rule (`person_wake_batch` = 100 messages, or 7 days stale),
**plus** at once on a stimulus that names this entity: a `remember`, a
dispute, or a told line about them by someone else.

**Reads:**

| input | today | after |
|---|---|---|
| its own current lines, with handles (`P1`, `P2`, ...) | the previous account, as prose | every current line with part, kind, mode, citations, verdict |
| the person's new messages | yes | yes |
| **messages that mention the person** | no | yes, through the index's `mentions` edges, only from rooms the person is listed in ([35](35-persona.md) §3) |
| **their response episodes** (how they answered Nora) | no | yes ([50](50-learning-from-acts.md)), under the same rule |
| **their Rollup aggregates** (rhythm, ties) | no | yes, as evidence for induction |
| open disputes and `remember` items about them | no | yes |
| **their social items** (posts, reels and story frames: what each is, when posted, its caption and media readings, and a post's or reel's counts, [cognition-loop/22](../cognition-loop/22-social-stream.md) §5) | person Character only, at most 6 a wake (cognition-loop/22 §6) | yes, through the readable social store (opt-out and gone or hidden enforced), cited `social:<network>:<item id>`; an item has no room, so it counts as evidence in a unit that lists its author; never its place (R-31: the account renders in rooms whose `location` flags differ). The social input is added once step 6's adaptor lands |
| the unit's room cultures | yes | yes |

The mentions input is the one that matters most for Honcho parity (§6): today
the adaptor is **handed** only the person's own messages. It can reach what
others say about them only if it thinks to search for it, through Honcho's
`search_memory` and `about` (`CHARACTER_PERSON_TOOLS`,
`nora/language/character.py:119-121`), and that is where "Rin is Sam's
sister" lives. After, those messages are an input, not a search it may skip.

**Tools:** `ask_memory` over the index (replacing Honcho's `ask_memory` and
`search_memory`), `about` reading Persona, the Record reads, `lookup_person`,
`group_members`. No act verb.

**One session, three ordered phases** in the objective:

1. **Identity:** fold in told and deduced lines from new statements; weigh
   disputes ([35](35-persona.md) §2).
2. **Character:** re-judge every trait: keep, merge, retire, or add an
   induced one.
3. **Hypotheses:** read the verdicts the judge has set since the last
   session; retire the refuted; propose at most one new explanation, with its
   observable and window.

One session rather than three because each phase reads the one before it,
and three sessions cost three passes over the same input. The reading that
would split it: a measured share of lines whose mode is wrong for their
evidence above 10% on the eval (§7).

**One atomic write**, which settles the session:

```
persona_write(entity, ops)
  op: {"op": "add",     "part", "kind", "text", "mode", "citations", "draws_on"?, "told_by"?,
                        "prediction"?, "prediction_observable"?, "prediction_window_end"?}
      {"op": "keep",    "line": "P3", "citations"?}        # re-support, refreshes last_supported_at
      {"op": "revise",  "line": "P3", "text"?, "citations", "draws_on"?, "mode"?}
      {"op": "retire",  "line": "P3", "why"}
persona_no_change(entity, why)
```

There is no `verdict` op (amended 2026-10-15): the adaptor that wrote a
hypothesis cannot grade it (§3).

**Built (2026-10-17):** `add` and `revise` take `conflicts_with: "P2"`, the
one way a writer says two lines conflict; code ranks the two by §3's
precedence, marks the lower `outranked`, keeps both, and refuses a conflict
between lines of the same rank (revise or retire instead) or with a
hypothesis. Revising an `outranked` line without naming a conflict leaves it
`outranked`; once the line that beat it is gone, the way back is to retire it
and add the claim afresh, which lands `current`. A `keep` without citations writes nothing and is not
re-evidence, so it does not stop a trait decaying. Handles `P1`, `P2`, ...
number the held lines (`current`, `disputed`, `outranked`) part by part,
oldest first (`nora.domain.persona.handles_for`). The write takes the
revision the session read its handles at, and a write against a newer one is
a `conflict` that applies nothing. Prerequisites 1 to 3 are built:
`PersonaCitable` maps each cited row and episode to `(ts, person, room)`,
cites Rollup aggregates as `rollup:<kind>:<subject>` and episodes as
`response:<id>`, and counts days in HKT. A member's social item is
citable as `social:<network>:<item id>` (cognition-loop/22 §6), mapped to
`(taken_at, author, no room)` and resolved only against the items the session
was handed: evidence on its author's own account alone, and only while the
author is listed in a room of the unit, as a `deduced` premise or an
`induced` instance on its `taken_at` day, never what a `told` line cites; its
id in a line's text is refused like a message number. Prerequisite 4, the stimulus wake,
is the adaptor's (step 6).

Every op is validated against §2 and §3 before any is applied. One refused
op refuses the call, with each refusal's reason and handle, and nothing is
repaired for the model (today's `check_revision` pattern). A session that
settles nothing holds its batch, and the third time for the same batch it is
given up (DEC-280).

**Built (2026-10-17, step 6, DEC-318), and what it settled:** `PersonaPersonAdaptor`
(`nora/language/persona.py`, objective `persona-person`) is a new adaptor
beside `character-person`, not a rename: Character keeps rendering until the
switch, so both run in shadow. It is off unless `[persona] enabled`. Its
wake is Character's rule over the rooms that list the person, plus the
stimulus: a turn's `told` line (`written_by_session = 'tag'`) written after
the account's session last settled (`persona.settled_at`, migration
`0120_persona_settled_at`) counts as a full batch. Every settle, a
`persona_no_change` included, sets `settled_at`, so one told line wakes one
session. It is handed its held lines by handle, the person's messages with
their age, the episodes they answered (`response:<id>`), their Rollup ties
summed over the current build (`rollup:tie:<unit>|<a>,<b>`, never
co-location, R-31) and the rooms' cultures. The verbs judge a write against
the lines the session was shown and STAGE it; `commit` writes it, judged
again in the store's transaction against the revision the session read, so
a write landing meanwhile is a `conflict` that writes nothing, and the next
wake reads the new account. Built since: the `mentions` input (the index's
`mentions` edges over `messages.mention_person_ids`), disputes (§2's
`persona_dispute` turn tool and the `<persona_disputes>` block) and the
verdict judge (the `persona-verdict` adaptor, §3). Not built yet: `about`
over Persona, and the eval (§7). Each input is one item type and one block,
so another source (a person's social items) adds without reshaping the
adaptor.

## 5. Rendering

One block per entity in the turn, replacing `peer_card`, `peer_history` and
`<person_account>`:

```
<persona of="Kit">
identity:   prefers "Kit" (told by Kit) · vegetarian since August (told by Kit)
character:  organises plans; drops them when work runs late
open questions: picks short co-op games, maybe because weekday evenings are short?
</persona>
```

No ids, no dates beyond what a line says, `disputed` lines marked as such,
`hidden` lines never, and the per-account and per-block character caps of
today's `<character>` block. A line resting on a message since deleted renders
as it did, and its evidence view says "a message since deleted"
([35](35-persona.md) §5).

**Which rooms see it** (amended 2026-10-15; this said lineage decides, and
lineage is off): the unit is the boundary. A person's block renders in the
unit's rooms that list them, and in no other unit
([35](35-persona.md) §3); a collective's block renders in its own room, or in
every room of the unit for the unit's account.

## 6. What Honcho knew is re-derived, and that is measured

1. **Shadow first, and shadow changes no prompt** (amended 2026-10-15). The
   adaptor writes Persona under a **shadow key**; the live Character head
   keeps rendering, `peer_card` and `peer_history` keep rendering, and
   Honcho's feed keeps running, until the switch (00 «Sequence» step 8).
2. **Fix the denominator before measuring.** The card lines and a random 50
   of each person's `explicit` conclusions are listed first, and the
   `not-worth-keeping` set is hand-labelled before any Persona is judged. A
   line the judge later calls `not-worth-keeping` does not leave the
   denominator unless it was in that pre-labelled set.
3. **Calibrate the judge.** It is a model that did not write the Persona.
   Before it judges, it labels 20 hand-labelled lines; it is used only if it
   agrees on at least 18.
4. **Coverage, on norty, counts only.** Each listed line is judged into one
   of: `covered` (a current shadow Persona line says it), `derivable-missed`
   (the source message is in the Record and no line says it),
   `not-worth-keeping` (pre-labelled only), or **`error`** (the judge call
   failed, timed out or returned no label). An `error` is never `covered`.
5. **Pass:** at least 80% of the cards' identity, relationship and
   instruction lines are `covered`, **and** at least 60% of the sampled
   `explicit` conclusions that state a stable fact about the person are
   `covered`, **and** `error` is under 5% of the judged lines (above that the
   run is an ERROR, not a FAIL or a PASS, and is rerun). Every
   `derivable-missed` line is traced to a missing input or an objective gap
   and fixed before switching.
6. **Then the replies.** The one replay threshold, used here and in
   [10](10-honcho-audit.md) §4: at least 80 recorded tag turns with
   `peer_card` and `peer_history` against the same turns with `<persona>`,
   judged blind in pairs. The switch passes only if Persona is preferred in
   at least 55% of the pairs.

89% of Honcho's conclusions are `explicit`, what people said, and those
messages are in Nora's own Record. The coverage measure is how that claim is
checked rather than assumed.

## 7. Evaluation

A `persona` corpus, synthetic people only:

- each mode's validator cases (refusals are unit tests, not eval cases);
- a told line against a contradicting induced trait;
- a dispute by the subject, by a close friend and by a bystander, one of them
  wrong;
- a hypothesis whose prediction is later confirmed, and one later refuted;
- an invented role, the case DEC-184 was rejected over;
- mode correctness: the share of written lines whose mode matches what
  their citations can carry, judged.

## 8. Migration

The one-off backfill ([35](35-persona.md) §4) maps each live Character head to
`character` lines: the adaptor's first **shadow** session rewrites the prose
head into at most 5 `trait` lines under the shadow key, citing what the prose
head cited. The prose head keeps rendering until the switch (00 «Sequence»
step 8), not until the first session. The Culture heads move the same way,
with `purpose` and `norm` starting empty and `name` lines taken from Culture's
`Names and where they came from`.


## Typed Identity amendment, 2026-10-19

[75](75-typed-identity.md) and [76](76-typed-identity-build.md) supersede the
free-text Identity values and newest-page first-backfill behavior above.
Nullable `fact_key`, `fact_value`, `valid_from`, `valid_until` extend
`persona_line`; `as_of` is evidence time and `written_at` is transaction time.
Legacy prose remains untyped until original evidence supports a typed revision.
One shared validator/formatter serves session writes, graph `remember`, prompt,
portal and index. Historical claims cannot replace a newer source assertion.
There is one Persona writer per subject on the render surface: legacy Culture
and Character model adaptors are removed from the schedule at render-on;
render-off restores their configured lifecycle. Persona room accounts supply
person context and unit revision wakes on the new surface.

Historical recovery uses two existing consolidation-watermark rows per person
account: `persona-person:history-boundary` freezes the Record highwater and
`persona-person:history` records successfully handed progress. One oldest-first
SQL own/mention union is limited after union, with ingestion's resolved mention
list available even when graph indexing is off. Recent runtime watermarks are
independent. No refusal, conflict or unsettled historical session consumes a
page, including past the normal recent-session abandonment bound. A crash
between Persona commit and cursor settlement repeats evidence, never skips it.

Operator receipts: `nora memory persona-history` reports account boundary,
progress, eligible-source counts, missing fields, untyped legacy heads and
disputes without message text. `--key <scoped-account> --restart --apply`
clears only that pass's two cursors; the next wake freezes a fresh boundary
and re-evidences history without deleting claims. Without `--apply`, restart
is a dry run. Completion proves source exhaustion, never factual parity.
