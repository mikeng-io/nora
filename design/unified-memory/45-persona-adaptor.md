# The Persona adaptor: schema, reasoning modes, writes and curation

Status: **proposed**, 2026-10-15, on the operator's rulings in
[35](35-persona.md). Nothing is built. The decision row is written when the
schema lands (nora-decision).

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
| `mode` | text | `authored`, `told`, `deduced`, `induced`, `abduced`; allowed per part in §3 |
| `citations` | text[] | non-empty; each a Record row id, a Rollup key, an episode `response:<id>`, or another line's id |
| `told_by` | text | the speaker's person id when `mode = told`, else null |
| `prediction` | text | required when `mode = abduced`, else null |
| `state` | text | `current`, `disputed`, `superseded`, `retired`, `refuted` |
| `verdict` | text | for a hypothesis: `open`, `confirmed`, `refuted` |
| `supersedes` | uuid | the line it replaces, or null |
| `support_count` | int | how many distinct citations have supported it over its life |
| `last_supported_at` | bigint | the newest evidence that supported it |
| `lineage`, `exclude_persons`, `as_of` | | today's derived-row columns (cognition-loop/10 §3) |
| `written_by_session` | text | the adaptor run that wrote it |

The lines are also index nodes, and their citations, `about` and `supersedes`
become edges in the same transaction ([25](25-memory-graph.md) §7).

## 2. The parts, their kinds and their caps

| part | kinds | cap (current lines) | decays |
|---|---|---|---|
| identity | `name`, `role`, `relationship`, `attribute`, `instruction` (actors); `purpose`, `norm` (collectives) | 25 | no; replaced only by a later told line or a dispute the adaptor accepts |
| character | `trait` | 5 | yes: unsupported for 60 days → `retired` at the next session unless re-evidenced |
| hypothesis | `explanation` | 3 with verdict `open` | refuted → retired; confirmed stays a hypothesis |

`Accounts` (platform identities) are rendered from `identity.toml` and are
never a line a model writes (30 §3). A collective's members stay the roster's
(`room_members`). Over a cap, the write must retire a line in the same call,
and the refusal says which cap.

## 3. The reasoning modes, enforced

| mode | allowed in | must cite | refused when |
|---|---|---|---|
| `authored` | identity | the authored source | never written by the model; rendered from operator files |
| `told` | identity | the message it was said in | the citation is not a message the session was shown; a `instruction` whose `told_by` is not the subject |
| `deduced` | identity, character | at least 2 premises, lines or rows | fewer than 2 premises; any premise is `abduced`; any premise is not `current` |
| `induced` | character | at least 3 instances on at least 2 calendar days, or one Rollup aggregate key | too few instances; all on one day |
| `abduced` | hypothesis | at least one current `induced` trait it explains, and a `prediction` | no prediction; it explains no induced trait; the prediction names no observable or no window |

A `told` line keeps the verb it was told with: "thinking about Japan" is not
"went to Japan" (30 §4). Precedence when two current lines conflict: authored >
told by the subject > told by someone else > deduced > induced. Both stay, the
lower marked `disputed`, until a later statement settles them.

**A hypothesis is a question, not a fact.** It renders under "open
questions", and Nora may ask it. Assent ("yeah") never changes its verdict
(cognition-loop/10 §4). Each later session tests its prediction against the
new evidence it was handed and sets `confirmed` or `refuted`, citing the rows.
The share confirmed is the grounded measure of her abduction.

## 4. The adaptor

The `character-person` adaptor (DEC-290), renamed and widened. The unit,
room (Culture) and Nora accounts follow the same shape with their own inputs.

**Wakes:** today's rule (`person_wake_batch` = 100 messages, or 7 days stale),
**plus** at once on a stimulus that names this entity: a `remember`, a
dispute, or a told line about them by someone else.

**Reads:**

| input | today | after |
|---|---|---|
| its own current lines, with handles (`P1`, `P2`, ...) | the previous account, as prose | every current line with part, kind, mode, citations, verdict |
| the person's new messages | yes | yes |
| **messages that mention the person** | no | yes, through the index's `mentions` edges |
| **their response episodes** (how they answered Nora) | no | yes ([50](50-learning-from-acts.md)) |
| **their Rollup aggregates** (rhythm, ties) | no | yes, as evidence for induction |
| open disputes and `remember` items about them | no | yes |
| the unit's room cultures | yes | yes |

The mentions input is the one that matters most for Honcho parity (§6): today
the adaptor never sees what others say about a person, which is where
"Rin is Sam's sister" lives.

**Tools:** `ask_memory` over the index (replacing Honcho's), the Record
reads, `lookup_person`, `group_members`. No act verb.

**One session, three ordered phases** in the objective:

1. **Identity:** fold in told and deduced lines from new statements; weigh
   disputes ([35](35-persona.md) §2).
2. **Character:** re-judge every trait: keep, merge, retire, or add an
   induced one.
3. **Hypotheses:** test each open prediction; retire the refuted; propose at
   most one new explanation.

One session rather than three because each phase reads the one before it,
and three sessions cost three passes over the same input. The reading that
would split it: a measured share of lines whose mode is wrong for their
evidence above 10% on the eval (§7).

**One atomic write**, which settles the session:

```
persona_write(entity, ops)
  op: {"op": "add",     "part", "kind", "text", "mode", "citations", "told_by"?, "prediction"?}
      {"op": "keep",    "line": "P3", "citations"?}        # re-support, refreshes last_supported_at
      {"op": "revise",  "line": "P3", "text"?, "citations", "mode"?}
      {"op": "retire",  "line": "P3", "why"}
      {"op": "verdict", "line": "P7", "verdict": "confirmed"|"refuted", "citations"}
persona_no_change(entity, why)
```

Every op is validated against §2 and §3 before any is applied. One refused
op refuses the call, with each refusal's reason and handle, and nothing is
repaired for the model (today's `check_revision` pattern). A session that
settles nothing holds its batch, and the third time for the same batch it is
given up (DEC-280).

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

No ids, no dates beyond what a line says, `disputed` lines marked as such, and
the per-account and per-block character caps of today's `<character>` block.
Lineage decides which lines render in which room ([35](35-persona.md) §3).

## 6. What Honcho knew is re-derived, and that is measured

1. **Shadow first.** The adaptor writes Persona while Honcho keeps running
   read-only and every prompt is unchanged.
2. **Coverage, on norty, counts only.** For each person with a peer card,
   every card line, and a random 50 of their `explicit` conclusions, are
   judged by a model that did not write the Persona into one of:
   `covered` (a current Persona line says it), `derivable-missed` (the source
   message is in the Record and no line says it), `not-worth-keeping`
   (transient or trivial).
3. **Pass:** at least 80% of the cards' identity, relationship and
   instruction lines are `covered`. Every `derivable-missed` line is traced
   to a missing input or an objective gap and fixed before switching.
4. **Then the replies.** Node 10's replay: at least 80 recorded tag turns
   with `peer_card` and `peer_history` against the same turns with
   `<persona>`, judged blind in pairs. Persona must not lose.

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
`character` lines: the adaptor's first session after the switch rewrites the
prose head into at most 5 `trait` lines, citing what the prose head cited.
Until then the prose head renders as it does today. The Culture heads move
the same way, with `purpose` and `norm` starting empty.
