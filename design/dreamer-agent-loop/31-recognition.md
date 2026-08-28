---
type: design
title: "Recognition — What Notices That Somebody Just Contradicted Her"
description: "Step 3 of the correction loop, the one step with no mechanism in 31 nodes. Targeting is deterministic because it decides the blast radius; the dispute predicate is a dedicated call, not the reply-path model and not a tool. A dispute MARKS a row, it does not mute it — which is what makes a false positive survivable."
tags: [nora, dreamer, correction-loop, recognition, facts, disputed]
timestamp: '2026-08-28'
edges:
  - {dst: 30-review-response, rel: extends, provenance: asserted}
  - {dst: 16-open-items, rel: closes, provenance: asserted}
  - {dst: 12-self-correction, rel: depends_on, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
  - {dst: 08-state, rel: corrects, provenance: asserted}
  - {dst: 26-standing, rel: depends_on, provenance: asserted}
---

# Recognition

[30](30-review-response.md) §2.1.1 sequences the correction loop in nine steps.
Step 3 — `RECOGNISE a member contradicting a claim Nora just rendered` — is the
only one with no mechanism. `RECOGNISE` occurs exactly once in the bundle, as
that line. [16](16-open-items.md) §12.a states the consequence: **D25 is a
decision with no mechanism under it**, and by elimination the recogniser is the
reply-path model, which rests the primary trust loop on
[02](02-instrument-blindness.md)'s named pathology — *she recognises and does
not act*.

This node is that mechanism.

> **Provenance.** A first draft was reviewed cold from three directions
> (adversarial, code-verification, design-quality) before any of it was built.
> Twelve claims it made about the codebase were checked against the files;
> **three were false** and are corrected below with the reading that corrected
> them. Two design decisions were reversed on evidence in the bundle itself.
> Both are marked. What is *not* marked is unchanged from the draft.

## 1. The decomposition — and why it is not one classifier

Recognition looks like one question (*is this a contradiction?*) and is two:

| | question | kind | if it is wrong |
|---|---|---|---|
| **TARGETING** | *which standing rows could this message be about?* | structural | the blast radius is wrong — the wrong row is marked, or the right one is unreachable |
| **DISPUTE** | *is this message contradicting one of them?* | semantic | a dispute is missed, or ordinary chat marks a correct row |

**Targeting must be deterministic because it is the half that must be
RE-DERIVABLE.** It decides which rows are touched, and a model choosing rows
would be choosing them **by identifier** — the exact shape `dream_screen`
drops. Targeting produces a bounded set the model addresses by **ordinal
handle**, never by id.

> **Correction (design review).** The draft argued the two halves have
> different powers *because they land at different points in the turn*. That is
> backwards — placement is a consequence of trust, not a source of it. The cut
> is **re-derivable evidence versus model judgement**, and §5 now says so. The
> timing difference disappeared entirely once Channel B stopped being a tool
> (§4).

**The dispute predicate is genuinely semantic** and pretending otherwise is how
this step stays undesigned. A cue list alone cannot separate *"no, big walk is
a video game"* from *"no I can't make the big walk"*; the difference is that
the first asserts a competing object for the same subject.

## 2. What a dispute DOES — and the reading that changed it

> **REVERSED on evidence.** The draft made `disputed` a **suppression**: the
> row left the prefill. That reading came from [30](30-review-response.md)
> §2.1.1's step 4 — *"IT STOPS RENDERING THIS TURN"* — and it contradicts
> three other decided lines, including one in the same section:
>
> - [30](30-review-response.md) §2.1: *"**`disputed` is visible to Nora**, so
>   she can say 'someone's told me that's wrong, I haven't confirmed it yet.'
>   Silence beats a wrong answer; **a marked dispute beats silence**."*
> - [29](29-authority.md) §5: *"**Marked disagreement beats silence, and
>   silence beats a wrong answer.** A detected conflict renders *as* a
>   conflict."*
> - [19](19-between-tiers.md) §4: the same sentence again.
>
> Three nodes decided a **three-state render** and step 4's shorthand collapsed
> it to two. The resolution that satisfies all four lines:

> **A disputed row stops rendering AS A STANDING CLAIM and starts rendering AS
> A DISPUTE**, carrying who disputed it. It never silently disappears.

This is not a softening; it is what makes the rest of the design safe, and it
answers two findings at once:

- **A false positive is now survivable.** Under suppression, one message from
  one member removed a true row from every future turn, for everyone in the
  workspace, with no TTL (§7), no clear path while the Dreamer is off, and
  `already_disputed` making a second member's honest dispute a no-op. That is a
  **memory denial-of-service available to anyone in the room**, and
  [30](30-review-response.md) §2.3 says abuse is a design input. Marking has no
  such shape: the worst a prank achieves is a row that renders with *"someone
  says this is wrong"* attached — which is **true**, and which
  [30](30-review-response.md) §2.3's asserter rendering makes legible.
- **It stops the correction manufacturing its own answer.** Suppress on the
  same turn as the correction and the only thing left in her context about
  `big walk` is the corrector's assertion, so she restates it. That is
  [16](16-open-items.md) §12.1's rejected *"asking manufactures the answer"*
  failure arriving by the back door — not by asking the room, but by deleting
  everything except the room's answer.

**R-5 is not the warrant for any of this, and the draft's appeal to it was a
category error.** R-5 is about **egress on one turn**; a row's dispute state is
**persistent state**. Marking is what R-5 actually licenses: she still answers,
and she answers honestly about what she does and does not know.

## 3. The precondition nothing has: a render ledger

Step 3's own words are *"a claim Nora just **rendered**"*. Nothing in the
codebase records what she rendered. `TagTrace` keeps `messages` and
`transcript` and nothing else (`nora/domain/tag_trace.py`, verified).
Recovering the rows by parsing the `<facts>` block back out of a stored prefill
would be **interpreting twice** — the spine's first rule.

So the render site records it, because that is the one place the row ids are
already in hand:

```
rendered_claim(
  conversation_id  text        not null,
  kind             text        not null,   -- 'fact' | 'lexeme'
  row_key          text        not null,   -- see §3.0
  surface          text        not null,   -- fact.subject_id / lexeme.canonical_form
  workspace        text,                   -- facts only; NULL for a lexeme (§3.0)
  last_tag_id      text        not null,   -- provenance, not a key
  last_rendered_at timestamptz not null,
  primary key (conversation_id, kind, row_key)
)
```

**Upsert, not append.** The draft keyed on `tag_id` too, which both contradicts
its own claim that `tag_id` is never a join key and writes a fresh row for the
same claim on every consecutive tag — forty rows for one fact in an active
conversation, read back with `DISTINCT ON`. One row per rendered claim per
conversation, with the last render's time and tag, is what the read wants.

### 3.0 The keys do not line up, and this is the first thing to build

> **False in the draft, corrected.** It declared `row_id uuid not null` for
> both kinds. **`room_lexeme` has no uuid.** Its primary key is three TEXT
> columns — `(scope_id, canonical_form, sense)` (`nora/infra/sql/schema.py`) —
> and the composite is deliberate and load-bearing
> ([12](12-self-correction.md) §1). Worse, `RoomLexeme`, the domain type that
> reaches the render site, **carries no id at all**, not even `scope_id`.

Three consequences, all of them work, none of them optional:

1. **`row_key` is TEXT.** A fact's is its uuid; a lexeme's is its composite key
   rendered canonically. A surrogate id on `room_lexeme` was considered and
   rejected: the compound key is the fix for a real overwrite bug and adding a
   second identity beside it invites the two to disagree.
2. **The lexeme render site must be able to name its rows.** `RoomLexeme` gains
   its key, or the ledger write moves up a level to where `RoomLanguage` holds
   `scope_id`. Either way this is a change *before* recognition, not during it.
3. **Facts are workspace-keyed; lexemes are conversation-keyed.** `fact` is
   scoped on `workspace`, `room_lexeme` on `scope_id`, and `scope_id` **is** the
   conversation id. The ledger is conversation-keyed because that is the axis a
   message arrives on; `workspace` rides along on fact rows so the cross-room
   consequence in §6.1 is expressible rather than latent.

### 3.1 Retention

Pruned at **2× the recognition window** (§3.4), by the existing Dreamer
housekeeping tick, counted (`nora_rendered_claim_pruned_total`). A working set,
not an audit trail — the audit trail is the Langfuse trace and the `tag_trace`
row.

> [30](30-review-response.md) §3.1's own words about the revalidation pass —
> *"no trigger shape, no cadence key, no metric … a pass with no trigger
> definition is a polling bug in embryo"* — is why this paragraph exists rather
> than the draft's bare "pruned on the recognition window".

### 3.2 The write is on the critical path, and awaited

Not fire-and-forget the way `tag_trace` is written. A fast follow-up correction
against an empty ledger reports *"nothing matched"* when the truth is *"I could
not check"* — R-27's exact inversion. The write is awaited before the reply
goes out, and a failure increments `nora_rendered_claim_write_total{status}`
and makes the **next** turn's targeting report `unavailable` rather than empty.

> **This table is also the measurement.** Without it, *"Nora rendered it"* is an
> assumption. With it, it is a read.

### 3.3 The render site is about to move

[19](19-between-tiers.md) §6 changes `<facts>` from a recency slice into one
block with three slices and an extent line. Building the ledger against today's
`recent_facts` renderer buys a rewrite. **The ledger lands with that change, not
before it.**

### 3.4 The recognition window

`[facts] recognition_window_hours`, **default 24**, and it is named here
because the draft left the single most consequential number in this node
unstated while claiming its bound was *"the only bound available that is not a
tuned number"*.

24 hours because a correction arrives either in the same sitting or after
somebody looked something up overnight, and because it is the horizon over
which *"a claim Nora just rendered"* is still a thing a member could be
reacting to. **Reversal condition:** the age distribution of the quote-reply
targets that resolve (§4.1) — if the tail past 24h is material, the number is
wrong, and that distribution is readable from the ledger itself.

## 4. Channel A — deterministic targeting

Runs **on the tag path, before context assembly**. Two sub-channels, unioned.

**The addressed-to-Nora gate is structural, and it costs recall.** Recognition
runs only on the tag path, so it is gated by construction — the ambient path
never reaches the model. [30](30-review-response.md) §2.1.1 asks for that gate
and gets it. **The consequence must be stated rather than discovered: a
correction spoken to the room and not to her is invisible to both channels, and
in a group chat that is most corrections.** The recall ceiling of this whole
mechanism is the tag rate. That is a deliberate scope decision — putting
recognition on the ambient path would run a classifier over every message in
the room — and `nora_dispute_targets_total` is per tag turn, so nothing in §8
can be read as a rate over all corrections.

### 4.1 T1 — the quote link

Both platforms carry quote-reply. `NormalizedMessage.quote.quoted_ts` resolves
to a parent row; `messages.is_nora = 1` says the parent is hers.

**The lookup is best-effort, not a key join, and it is counted.** `messages` has
a surrogate `id` primary key and no uniqueness on `(platform, conversation_id,
ts)`; Nora's own row is inserted with a local-clock `ts` and then **updated to
the platform's sent-timestamp** by `set_delivery_outcome` — which is why the
join works for her at all, and why it is a last-writer-wins resolution.
Ambiguity (more than one parent matching) increments
`nora_dispute_quote_resolve_total{status}` and degrades to T2 alone.

**The key is `(platform, conversation_id, ts)`.** The draft dropped `platform`;
the repo's conversation key carries it (`ConversationTarget`).

**Resolution is by conversation and window, not by exact turn.** Within one
conversation the same standing rows render on consecutive turns, so an exact
turn join buys little. `last_tag_id` is therefore provenance and never a join
key.

> Reversal condition: the exact join is cheaper than the draft thought —
> Nora's own `messages` rows **already carry `tag_id`**, inside `raw_json`
> (`nora/agent/guards.py`). Promoting it to a column is a nullable add with no
> backfill. Do it if the window slop shows up in the ambiguity counter, not
> before.

**Discord's recall is structurally lower**: `_quoted` returns `None` unless
`reference.resolved` is in the gateway payload, so a reply to an uncached
parent yields no `Quote` at all. Not a bug to find later.

### 4.2 T2 — surface containment

For each `rendered_claim` in the conversation inside the window, test the
message body for the row's `surface`.

**The matcher is normalised containment — not similarity.**

> **The draft's first reason was FALSE and is replaced by a stronger one.** It
> cited [25](25-knowledge-pipeline.md) §4.1 as measuring `pg_trgm` failing on
> `Big Walk`/`Big Walks` at 0.833 against 0.9. That measurement is of the
> **vendored MinHash/Jaccard matcher**, a different algorithm at a threshold
> nobody proposed here; §4.1's actual `pg_trgm` table runs at 0.3, where
> `Big Walk`/`big walk` scores **1.000 — a pass**. §4.1's real case against
> trigram is **CJK**: 打風/打风 at 0.200, 香港天文台/天文台 at 0.250. That is
> the argument, and it is the better one for this room.

So, three reasons:

- **The job is containment, not similarity.** *Does this message talk about
  `big walk`* is a substring question.
- **A threshold nobody can calibrate is not a measurement.**
  [16](16-open-items.md) §11 says there is no graded corpus, so a number chosen
  here would be chosen blind and would then look like evidence.
- **Trigram is measurably weak on this room's default script.** The two CJK
  readings above, from [25](25-knowledge-pipeline.md) §4.1.

**Normalisation is NFKC, strip, casefold** — the same three
[21](21-substrate-contract.md) §6.1 mandates for the sibling matching problem,
so full-width Latin from a CJK IME does not miss here while matching there —
**plus simplified/traditional folding**, because 打風/打风 is a real case in
this room and containment scores zero on it where trigram at least scored
something.

**The boundary rule is defined on the ADJACENT CHARACTERS, not on the
surface.** The draft said *"Latin surfaces take a boundary check on both ends"*,
which (a) is undefined for a mixed surface like `Big Walk 大步行`, (b) makes
`\bBig Walk\b` fail against 「HKO好準」-shaped text because Python treats CJK as
word characters, and (c) would miss `Big Walks` — the very case it condemned
trigram for missing. The rule: **a match is bounded when each adjacent
character is not a letter or digit of the SAME script class as the match's
adjacent character.** CJK is its own class, so Latin abutting CJK is bounded,
and `party` still does not match `art`.

**Boundary-free CJK containment needs a length floor.** Two-character surfaces
are substrings of longer compounds constantly — 大浪 inside 大浪灣, 行街 inside
行街市 ([25](25-knowledge-pipeline.md) §4.1's own examples). Surfaces under
`min_cjk_surface_chars` (default 3) match only on an exact token boundary.

**The scan is bounded and its cost is measured.** Direct scope reads the
ledger, which is capped by the render caps (facts 8, lexemes 6) times the
window. Latent scope (§4.3) is a scan of standing rows with a per-turn row cap;
`nora_dispute_scan_rows` and `nora_dispute_latency_seconds` are what say it is
still cheap, because CLAUDE.md's rule is that something must measure the cost
and a correct answer is compatible with any cost.

> Reversal condition: trigram as a **third** targeting sub-channel once the
> graded corpus exists — it is additive and has the only GIN index precedent in
> the schema (`idx_note_revision_body_trgm`), so it becomes the answer if the
> latent scan's bound ever binds.

### 4.3 The two tiers of scope

| tier | what puts a row in scope | effect |
|---|---|---|
| **direct** | rendered in this conversation inside the window | **marked disputed**, priority adjudication |
| **latent** | standing in this workspace, surface-matched, not recently rendered | a **priority candidate only**; the row is untouched |

The cut is the step's own wording. *"A claim Nora just rendered"* is the thing a
member can be reacting to; a row she has never said is a row nobody knows she
holds. Latent scope still matters — a correction can arrive before she ever
renders the row — so it proposes, at priority, and never marks. Latent scope
proposing is itself counted (`nora_dispute_latent_proposed_total`), because a
power granted with no instrument is unreadable.

**Blast-radius caps, because targeting decides it and the draft capped
nothing:** `max_targets_per_message` (default 4) and `max_disputes_per_person_
per_window` (default 8), both counted on refusal. One message naming three
surfaces with one cue should not mark three entity groups unbounded.

## 5. Channel B — the dispute predicate

> **REVERSED on evidence. It is not a tool.** The draft made it a tag-path tool
> granted only when the target set is non-empty. Four readings against that,
> three of them from the files:
>
> 1. **There is no conditional-grant shape to reuse.** `build_tool_specs`
>    returns a flat, unconditional 45-element tuple; a `None` collaborator does
>    not omit a tool, it makes the tool answer `unavailable` at call time
>    (`generate_image`, the note verbs). `ToolRegistry.build` says outright:
>    *"this codebase has no per-conversation gating for them today."* So the
>    draft's *"on every other turn it does not exist and costs nothing"* was
>    false of every native tool in the process.
> 2. **[08](08-state.md) §12 says «No write tool, ever»**, with one carve-out
>    for a verb that *enqueues for delivery*. A verb that sets a column is not
>    that, and the carve-out is drawn on mechanism, not on nomenclature.
> 3. **The transport may not honour tool choice** ([16](16-open-items.md) item
>    0, and the degradation is measured on this deployment). A recogniser whose
>    only channel is a tool call inherits that.
> 4. **[26](26-standing.md) §1's impartiality argument.** A model asked to judge
>    mid-turn is judging under social pressure with the reply still to write.

**Channel B is a dedicated, single-purpose model call, made pre-prefill, only
when the direct target set is non-empty.**

```
input   the message, and the target set as ORDINAL HANDLES
output  {"disputes": [{"handle": "1", "reason": "..."}]}   -- or none
states  ok | nothing_found | unavailable          (R-27, on the call itself)
```

It gets Channel A's timing *and* a semantic reach no cue list has: because it
runs before context assembly, its verdict reaches **this turn's own prefill**.
It removes the tool-choice dependency, it is gradeable offline against
[16](16-open-items.md) §11's corpus in isolation, it does not compete with the
reply for budget, and there is no write tool anywhere.

**Its cost is one round trip of reply latency, on the rare turns where the
direct set is non-empty.** Bounded by `dispute_classify_timeout_ms`; on timeout
the state is `unavailable`, never `nothing_found`, and §5.2's fallback runs.

**The handle is a `str`.** The whole handle path in `fact_adaptor` is
string-keyed (`by_handle`, `supersedes`), and the draft's `int` diverged from
the shape it claimed to reuse. The target set renders in that same existing
shape:

```
- [1] big walk | means | a walking activity the group does, maybe at night
- [2] big walk | made_by | House House
```

**No identifier is ever shown and none is ever accepted.** A handle that is not
in the set is refused and counted. This is the rule that caught three adaptors.

**Its output goes through a sink, like every other proposal**
([17](17-failure-classes.md) §7, [26](26-standing.md)). `reason` is model free
text and is **screened** before it is stored or rendered.

### 5.1 What acts

```
message arrives (tag path — addressed to her by construction)
  │
  ├─ CHANNEL A     deterministic targeting  ──►  direct set + latent set
  │
  ├─ CHANNEL B     dedicated classifier over the direct set        ┐
  │     ok           ──►  mark those rows disputed                 │ both
  │     nothing_found──►  nothing                                  │ pre-
  │     unavailable  ──►  RULE-F below                             │ prefill
  │                                                                ┘
  ├─ context assembly / prefill   (disputed rows render AS DISPUTES, §2)
  │
  ├─ model turn                                (unchanged; no new tool)
  │
  └─ counters at every step (§8)
```

### 5.2 RULE-F — the fail-closed fallback

When Channel B is `unavailable`, the deterministic rule still acts: a
**quote-reply to one of Nora's own messages** from a turn that rendered row R,
where the reply carries a negation cue and the reply names R's surface.

> **This was RULE-1 in the draft, where it ran ALWAYS and was called
> "unambiguous". It is neither, and the adversarial review instantiated §1's own
> counterexample against it**: *"no I can't make the big walk"*, quote-replying
> her, satisfies every condition — quote ✓, cue ✓, surface ✓ — and disputes
> nothing. The draft proved one section earlier that a cue list cannot make that
> discrimination, and then made it with a cue list.

Two changes follow. It runs **only when the classifier could not**, so the
common path never depends on it. And its precision cost is **survivable because
the effect is a mark, not a mute** (§2) — a false RULE-F firing renders a row
with *"someone says this is wrong, I haven't confirmed it"*, which is what
actually happened.

It also requires the surface in the **reply**, not merely in the quoted
message: a bare 「唔得」 replying to anything she said would otherwise mark
whatever that turn rendered.

**The negation cue set** is a named constant in code, narrow, per language,
enumerated in the module rather than left to the reader — and it **excludes the
refusal and surprise families explicitly**, because in Cantonese 唔係呀嘛 and
唔係呀 are disbelief, 冇啦 and 唔得 are refusals, and in English *"no way"*,
*"nah bro"* and *"not gonna lie"* negate nothing about a stored claim. It is not
doctrine: a cue list in doctrine becomes a closed set the model reads as
exhaustive.

**Its grading is honest about what it cannot grade.** An adjudication verdict is
ground truth for a *world* claim; for a room-internal claim the verdict is
`stands_unconfirmed`, which is *"we do not know"* and grades nothing. So the cue
set is falsifiable on world claims and stays unfalsified on the class it will
most often meet, until [16](16-open-items.md) §11's corpus exists. That is the
honest statement, and it is a reason RULE-F is a fallback rather than the
primary.

### 5.3 Why this escapes [16](16-open-items.md) §12.a

12.a's objection is precise: *"by elimination the recogniser is the reply-path
model."* After this node it is not. The recogniser is a **dedicated call whose
only question is this one**, plus a deterministic fallback, plus a deterministic
target set the model cannot exceed. Three properties, and it needs all three:

1. the model **cannot invent a target** — the set is deterministic and handled;
2. the model **cannot be distracted from the question** — it is not choosing
   among 45 tools mid-reply; the call has one job;
3. the model's **inaction is a series with a stated positive** —
   `nora_dispute_unacted_total{cause}` separates *the classifier ran and
   declined* from *the classifier never ran*. Without that label the series
   reads identically for a working recogniser on a quiet room and a broken one,
   which is CLAUDE.md's *"never read an instrument's output as evidence without
   first checking it can measure the thing"*.

**And what a positive result looks like, stated before any null one is
interpreted:** on a turn where a member contradicts a rendered claim,
`targets_total{scope="direct"}` increments, `recognised_total{channel="model"}`
increments, and the next turn's prefill carries that row marked. Nothing in §9
may be read until that trio has been observed once.

## 6. Fan-out across both tiers

[30](30-review-response.md) §2.1.1 requires it: one correction contradicts a
lexeme row and a fact row at once.

> The target set is built **per entity**. Marking a target marks every row in
> its entity group, in both tiers, **in one transaction**.

**And the columns for that do not exist yet.** `fact.subject_id` is plain text
with no entity pointer — migration 0038 gave only the *object* a real
foreign-keyed id — and `room_lexeme.canonical_form` has no link to
`fact_entity` at all. So the entity fan-out needs, from pass 3:
`fact.subject_entity_id`, and a lexeme→entity resolution. Until they land,
fan-out degrades to normalised surface equality, and the degradation is
**counted** (`nora_dispute_fanout_unresolved_total`) rather than silent — but
note that this is the *only* path for subjects and lexemes today, not a brief
transitional state.

**The fan-out is not idempotent against a moving group.** Promotion is
incremental and re-runs ([30](30-review-response.md) §4), so entity-group
membership changes concurrently: a row joining between the enumeration and the
commit stays unmarked. The enumeration and the marks take a `SELECT … FOR
UPDATE` over the group's rows in one transaction, and a group that changed
under the lock re-enumerates once, then gives up and counts.

### 6.1 The cross-room consequence, stated

A fact is **workspace**-scoped, so a dispute raised in room A marks a row that
renders in room B of the same workspace, where nobody said anything. That is
consistent with [19](19-between-tiers.md) §6 and `fact_render`'s own note — a
fact *"may have been established in a different room of the same workspace"* —
but it is a blast-radius property, so it is stated rather than left to be
discovered. A lexeme is conversation-scoped and does not cross.

### 6.2 The note tier does NOT join the fan-out

[30](30-review-response.md) §2.1.2 maps a dispute on a note to **retirement**,
not supersession — a note is a question, not a claim. And
[27](27-note-dreamer.md) §11.1 rules that both retirements require evidence to
*exist*, so a tag-path mark on a note would violate the note tier's own
discipline. **A note anchored on a disputed entity gets a priority revisit flag,
never a dispute.** Stated here because `note_entity` foreign-keys the same
`fact_entity` table, so a reader would otherwise expect the fan-out to reach it.

## 7. The `disputed` state machine

[30](30-review-response.md) §2.1.1's review names this unspecified. Columns on
`fact` and `room_lexeme`:

```
disputed_at      timestamptz null
disputed_by      text        null   -- person_id; NULL when the sender is unmapped
disputed_reason  text        null   -- screened; what the disputer actually claimed
disputed_tag_id  text        null   -- which turn recognised it
```

`disputed_reason` is new: the draft accepted a `reason` from the model and
stored it nowhere, so the adjudicator received a dispute with no statement of
what was disputed — which is step 5's input. `disputed_by` is nullable because
an unmapped sender has no `person_id` and a sentinel would be a `person_id` that
compares equal to nobody.

| event | rule |
|---|---|
| **set** | CAS on `disputed_at IS NULL`, then a status read to distinguish the two failure causes §8's labels demand — `already_disputed` from `already_superseded` — in the same transaction |
| **dispute on a superseded row** | no-op, counted `already_superseded`. A superseded row does not render as standing anyway |
| **second dispute on a disputed row** | no-op on the row, counted `already_disputed`. The second asserter is recorded on the **candidate** it proposes, through the asserter junction **this stage adds** — the draft said `fact_asserter` was *"where asserters live"* and **no such table exists anywhere in the repository**; [21](21-substrate-contract.md) §7.4 argues for it and nothing built it |
| **clear on dismissal** | only inside adjudication's transaction, CAS on `disputed_at = <the value read>`. A second dispute landing mid-dismissal changes it, the CAS fails, the dismissal does not clear, and the adjudication becomes a candidate carrying both — [30](30-review-response.md) §4's supersession CAS, same shape |
| **dismissal** | requires an **affirmative contradiction**. `not_covered` never clears a dispute (R-53, one level up) |
| **stands_unconfirmed** | the room-internal outcome [30](30-review-response.md) §2.1.1 decides: the correction is not refuted and not confirmed. `disputed_at` **stays set**, `fidelity` moves to `tentative`, and the row keeps rendering as a dispute. The draft declared this outcome in its metrics and gave it no transition |
| **supersession** | clears `disputed_at` in the same statement that sets `superseded_at`, so `nora_dispute_open` does not count resolved rows forever |
| **promotion rewiring the object** | does not touch `disputed_at`; different column, different transaction, idempotent either way |
| **`rescore`** | `SqlLanguageStore.rescore` is the only writer of `room_lexeme` and upserts on the three PK columns. It must not clear `disputed_at`, and its `live/fading/stale` lifecycle is orthogonal to it. Enumerated because the draft's writer list missed it |
| **an aged, unresolved dispute** | does not auto-clear — but it **escalates** (§7.1) |

### 7.1 Age escalates; it does not restore, and it does not merely get looked at

The draft refused a TTL by analogy to R-53 — *a TTL restoring a row is dismissal
on silence*. **The analogy is backwards and the design review is right about
it.** R-53/R-54 protect a standing fact from being overturned by a source's
silence; here the unevidenced claim is the *dispute*, so R-54's logic points at
restoring the row, not at keeping it marked.

The defensible principle is different and simpler: **a mark is not an assertion,
so it needs no evidence to persist.** With §2's reversal that costs almost
nothing — the row still renders. So:

- the dispute persists;
- age **re-fires the adjudicator at rising priority**, so the reading has an
  action attached rather than being a gauge nobody waits on
  ([02](02-instrument-blindness.md)'s whole argument);
- an **operator clear** is a supported operation, beside
  [30](30-review-response.md) §7's run-scoped rollback and erasure.

### 7.2 Recognition is gated on a live adjudicator

Clearing happens only inside adjudication's transaction, and adjudication is a
Dreamer adaptor on a Dreamer that is off after thirteen consecutive failures
([09](09-measurement.md) §5). Without this gate, disputes accumulate and nothing
clears them.

> **If no adjudication has completed within `adjudicator_liveness_hours`,
> recognition degrades to latent behaviour for every scope: it proposes and does
> not mark.** The mechanism already exists in §4.3; the draft simply never used
> it. Counted, so the degraded state is a reading and not a silence.

## 8. A disputed row everywhere it is read

The draft defined the behaviour in the prefill and nowhere else. There are five
readers.

| reader | behaviour |
|---|---|
| **the prefill** | renders **as a dispute**, with the asserter (§2) |
| **`search_knowledge`** (pass 5) | **returns it, marked.** Not filtered: filtering means Nora cannot discover that a thing is disputed, and — worse — the adjudicator reaching the same seam cannot see the row it is adjudicating. Dispute state is a field on the result, alongside [30](30-review-response.md) §6's per-`kind` disclosure policy, not a second filter composed with it |
| **the dense channel** (pass 4) | the re-embedding fingerprint covers model, parameters and text — **not `disputed_at`** — so a marked row keeps a live vector. That is correct under §2: it should still be findable. What must NOT happen is a **superseded** row staying retrievable, so supersession invalidates the fingerprint and the successor is embedded in the same pass |
| **the extent line** | counts disputed rows in its own bucket. An extent that folds them into "standing" tells her she knows more than she does |
| **the note tier** | not a reader of `disputed`; §6.2 |

## 9. Instruments

[30](30-review-response.md) step 9, and [09](09-measurement.md)'s rule that a
change whose effect cannot be read back is not done.

**Label vocabularies:** `nora/obs/metric_specs.py` declares `labelnames` as
plain string tuples and has no `Literal`/`get_args` anywhere — the draft claimed
otherwise. The `Literal` + `get_args` constant lives in the domain module and
the spec reads its `get_args`, plus a **runtime** check, because
`nora.language` is outside `make gate`'s mypy scope (issue #61) and a
static-only guard there is a claim.

**"channel" means one thing:** `T1`/`T2` are the **sub-channels** of targeting
and are labelled `via`; `deterministic`/`model` are `channel`. The draft used
one word for both.

| series | what it says |
|---|---|
| `nora_dispute_targets_total{tier,scope,via,status}` | targeting ran. **The positive control**, and it carries R-27's `status` — a failed ledger write or read reports `unavailable`, never an empty set, which is the inversion the draft's uninstrumented version would have shipped |
| `nora_dispute_classify_total{status}` | Channel B: `ok` / `nothing_found` / `unavailable` |
| `nora_dispute_recognised_total{tier,channel}` | a dispute was raised, `deterministic` or `model` |
| `nora_dispute_unacted_total{cause}` | direct set non-empty, no dispute: `declined` (the classifier ran and said no) vs `not_reached` (it did not run). §5.3's third property, and the label is what makes it readable |
| `nora_dispute_handle_refused_total` | the model named something that was not a handle |
| `nora_dispute_marked_total{tier,outcome}` | `ok` / `already_disputed` / `already_superseded` / `capped`. **CAS outcomes only** — the draft mixed these with R-27 states on one label, which the instrument rules forbid |
| `nora_dispute_latent_proposed_total` | latent scope actually proposed. A power with no instrument is unreadable |
| `nora_dispute_fanout_unresolved_total` | targeted by surface because no entity id existed (§6) |
| `nora_dispute_quote_resolve_total{status}` | T1's parent lookup: resolved / ambiguous / absent (§4.1) |
| `nora_rendered_claim_write_total{status}`, `..._pruned_total` | the ledger, both directions (§3.1, §3.2) |
| `nora_dispute_scan_rows`, `nora_dispute_latency_seconds` | **the cost.** Channel A runs on every tag turn in front of the group; nothing in the draft bounded it, and a correct answer is compatible with any cost |
| `nora_dispute_resolution_total{outcome}` | `superseded` / `dismissed` / `stands_unconfirmed` — each with a transition in §7 |
| `nora_dispute_open`, and its age | the backlog §7.1 escalates on. Excludes superseded rows, because supersession clears the flag |
| `nora_dispute_degraded_total` | §7.2's no-live-adjudicator state |

`recognised_total` fires at the decision; `marked_total` fires per row touched
by the fan-out. They are not the same event and the second is ≥ the first.

## 10. What would reopen this

- **`targets_total{scope=direct}` climbs, `recognised_total` stays flat, and
  `unacted_total{cause=declined}` carries it.** The classifier is not the
  recogniser in practice. Read only after §5.3's positive trio has been seen.
- **`marked_total{ok}` climbs and `resolution_total{superseded}` stays at
  zero.** Recognition is producing false positives. Tighten targeting.
- **`fanout_unresolved_total` does not fall after pass 3.** Entity resolution is
  not reaching this path.
- **The 24-hour window's tail** (§3.4).
- **A graded corpus exists.** Re-measure containment against trigram, and the
  cue set against adjudication verdicts.
- **`degraded_total` is the steady state.** The Dreamer is the binding
  constraint, not recognition.

## 11. What this node does not decide, and what it depends on

**Off the transport's critical path.** Channel B is a single dedicated call with
no tool choice and no reasoning carried between turns, so
[16](16-open-items.md) items 0 and 1 do not gate it. The draft claimed this
while specifying a tool, which was false; it is true now, and that is why the
tool went.

**On the Dreamer's critical path.** §7.2 — without a live adjudicator the loop
accumulates rather than closes. That is the difference between shipping this and
shipping a ratchet.

**Build order**, which is not the draft's:

```
19 §6's block reshape  →  rendered_claim (+ the lexeme key, §3.0)
  →  pass 3: entity ids, incl. fact.subject_entity_id
  →  the disputed columns + the asserter junction
  →  Channel A  →  a live adjudicator  →  Channel B
```

**Not decided here:** the priority queue's ordering
([25](25-knowledge-pipeline.md)'s), and step 6's research loop — the loop closes
through `stands_unconfirmed` without one.

## Status / Checkpoint

2026-08-28 — revised after three cold reviews of the first draft. **Three claims
it made about the codebase were false** (a conditional tool-grant shape that
does not exist, a `fact_asserter` table that does not exist, a metric-label
convention `nora/obs/` does not follow) and one citation was misattributed
(0.833/0.9 is the vendored Jaccard matcher, not `pg_trgm`). **Two decisions
reversed on evidence in the bundle**: a dispute MARKS rather than mutes
([30](30-review-response.md) §2.1, [29](29-authority.md) §5,
[19](19-between-tiers.md) §4 all decided a three-state render and step 4's
shorthand collapsed it), and the dispute predicate is a dedicated call rather
than a tag-path tool ([08](08-state.md) §12 forbids a write tool;
[26](26-standing.md) §1 forbids judging mid-turn; the transport may not honour
tool choice). The draft's RULE-1 performed exactly the discrimination its own §1
proved a cue list cannot make, and is now a fallback for when the classifier is
unavailable.

Design only; no code in this commit.
