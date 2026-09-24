# The adaptor

## 1. It is a Dreamer adaptor, and almost nothing else

The operator's description, which is the design:

> Culture tier 都係要存在，但我會形容佢只不過係 dreamer 嘅 adapter，亦即係
> memory tier 嘅一個 wrapper。

Everything it needs exists. `DreamAdaptor`'s own docstring is *"One Dreamer
objective: what to research, over what, **with which tools**, and how to turn
the answer into rows"*, and the runtime owns the loop, the tool ceiling, the
budget, its own doctrine, and R-18/R-28 screening.

| piece | where it already is |
|---|---|
| the room's own messages, hard-scoped | **the batch the Dreamer already hands it (§1.1)** |
| the room's `standing` — what Nora's position in it actually is | the `standing` tier (design 26), **read by nobody here today (§1.2)** |
| what the room has open — plans, commitments, follow-ups | the note tier's index, **read by nobody here today (§1.2)** |
| `search_memory`, `ask_memory`, `where_been`, `web_search`, `web_fetch` | tool registry — 35 read verbs, and this adaptor declares **none** of them (`culture.py:166`); ungranted, not unreachable (`03-decisions` correction 14) |
| agent loop, tool ceiling, budget | Dreamer runtime |
| watermark / volume / timeout trigger | `AdaptorSchedule` |
| `instruction_sha256`, `model`, `established_by` | **the runtime stamps these onto every `Proposal`** |
| R-18/R-28 screening of every string | runtime, before `accept` |

That last row deleted a whole column-set from the first draft: provenance is
not this tier's problem, it is the runtime's, and `accept` returns payloads
precisely so an adaptor never has to know its own `instruction_sha256`.

## 1.1 It reads its own batch — no new tool

An earlier draft of this file specified two new Honcho verbs
(`ask_room_memory` / `ask_workspace_memory`). They were built, and closed
unmerged (#237), because the path they served does not scope: see
`00-overview §4`.

What the adaptor reads instead is the batch the Dreamer already hands it:

```
record.since(conversation_id=key, after_id=watermark)
```

`WHERE conversation_id = :key` in our own Postgres. **Hard-scoped by
construction**, which is the property Honcho could not give at any layer. For a
unit-keyed variant, the same read across that unit's rooms — the
boundary being DEC-105's own rather than an assumption about someone else's API.

No tool registry change, no roster question, no 25s dialectic call, and no
`session_id` anywhere.

## 1.2 `standing` and `notes` are inputs, and this bundle never said so

Added 2026-09-17. It is the plainest instance of the general finding in
`design/dreamer-agent-loop/03-no-agent-loop §7`, and it was sitting inside this
file's own §2 the whole time.

§2 asks the model for **"my own standing here"**. `standing` is a tier
(`design/dreamer-agent-loop/26-standing.md`) that holds exactly that — Nora's
position in one room, per room, written by the debrief. The relevance gate
already treats it as authoritative: `relevance_gate.py:266-272` makes it *"the
ONLY place that can establish what she owes this room"*. The culture adaptor
reads it not at all and infers it from raw transcript instead.

The same holds one seam over. An account of how a room works that cannot see
what the room currently has open — the plan with an undecided corner, the thing
somebody owes — is inferring from the transcript a thing another store already
states. Note the vocabulary trap, because this bundle has fallen into it
before: the note tier's index is reached through `NoteStore.standing()`, and
that is **the index of open notes**, a different thing from the per-room
`standing` tier above. Two inputs, two stores, similar spelling.

**There is a precedent in this exact seam and it is one file over.**
`NoteCandidateAdaptor.inputs` renders three things, not one: a `<room>` block,
the batch, and `await self._notes.standing(unit=unit)`
(`nora/language/note_candidates.py:989`). Its reason for the third is written
beside it — *"THE INDEX IS THE BALLOT"*: without it the model cannot name what
already exists and writes a duplicate instead. Culture's version of that
sentence is that without `standing` it cannot say where she stands and will
write a guess instead, and a guess in this tier is prose that reads exactly
like an observation.

**Why this is not covered by §1.1's "no new tool".** It is not: §1.1 is about
the two HONCHO verbs #237 built, and nothing here proposes a new verb. Two
routes exist and they are not the same shape, so name which one before
building:

- **As `inputs`** — code reads both stores and renders them into the prompt,
  the way `note_candidates` does. Cheapest, no grant, no loop, and it is what
  the design should assume unless something argues otherwise. The cost is that
  what is read is fixed in advance by code.
- **As tools** — the adaptor declares read verbs and the loop asks. This is
  what correction 14 unblocks, it is what makes a cross-store derivation
  possible rather than a fixed join (`03-no-agent-loop §7.2`), and it is the
  more expensive of the two.

**Sequence, not preference:** inputs first, because it changes no grant and is
measurable against the same block; tools only when there is a question the
prompt cannot be pre-rendered to answer. `02-measurement §5` owns the ordering
and neither route is a step that ships without its arm.

One key detail, because getting it wrong is the silent failure DEC-181 names:
`standing` is keyed `(platform, conversation_id)` and culture is keyed
`primary_id`. They are equal for every live conversation today and that is an
accident of data (#229). The read must resolve through the policy, not assume
the two are one, or it matches zero rows and renders as «this room has no
standing» — a real state that nothing reports as a miss.

## 2. What the model is asked

One question, per room, on the Dreamer clock, over the batch: *describe this room's culture as
a short account — how they talk, how decisions get made, what they call
themselves, my own standing here, and **why** it is like that; not dated
observations.*

**Over the batch, the account written last time, the room's `standing`, and
what the room has open.** The last two were added 2026-09-17 (§1.2) and they
are not decoration: the clause *"my own standing here"* asks for something a
store already holds, and a question whose input cannot ground it does not fail
loudly — the model answers anyway, in prose, at a rate nothing reports.

The "not dated observations" clause is load-bearing. Without it a model handed
a batch of timestamped rows drifts back toward `representation`'s shape, which
is the chronicle this tier exists to not be (`00-overview §3`). The batch is
literally dated rows, so the pull toward restating them is the default, not an
edge case.

`web_search` earns its place only because the pass is daily: when the room
leans on a reference nobody explains — a game, a meme, a place — the loop can
find out what it is, so the account can say why a name landed rather than
merely recording that it did.

## 3. The store

Two tables, deliberately thin, because the interesting content is prose and
the provenance arrives from the runtime.

```
culture             key, head_revision_id, updated_at
culture_revision    id, culture_key, body TEXT,
                    parent_revision_id,
                    <runtime provenance: instruction_sha256, model,
                     established_by, written_at>
```

**`body` is prose. There is no section schema.** The first draft required
three JSONB sections under a CHECK; that was the same enumeration error as
`participation.md`'s five shapes, one layer down, and it could not have held
「秘書」 — a role the room invented that no column could have anticipated.
`note_revision` is the precedent: free text up to 32000 chars, CMS-clean
because the provenance sits *around* the prose rather than decomposing it.

**No `retired_at`, no `closing_condition`, no TTL.** See `00-overview §5`. A
quiet room's culture is not stale; it is accurate. `parent_revision_id` reads
"the same thing, earlier", never "superseded".

## 4. The trigger

**`uses_watermark = True`, `min_interval_seconds = 86400`.**

```
fires = lag > 0 and age >= min_interval
```

New human messages in this room, and a day since the last fold. A silent room
does not fire, **and that is correct rather than a defect**: nothing here
retires, so a quiet room's culture is accurate, not stale, and there is nothing
to refresh it against. Re-running the fold with no new material yields either
the same account (a wasted model call) or a different one — and the second is
pure model variance polluting the one signal the revision chain carries.

An earlier draft had `uses_watermark = False`, borrowed from `note_curator.py:526`.
That was wrong. The curator needs it because **an anchor date passing IS the
event** — retirement is time-driven whether or not anyone speaks. Culture has no
equivalent clock event. The precedent does not transfer.

**`lag` excludes her own messages** (`language.py:130`, `is_nora == 0`), so she
cannot trigger her own refresh. The room triggers it; her acts still reach the
CONTENT through `decision_log`. Trigger and content are separate, and only the
trigger needs the guard.

### The watermark is a gate here, not a cursor — and conflating them costs weeks

`batch_limit` bounds how far one run advances the watermark, and a new adaptor
starts at 0. `runtime.py:508-513` names the trap: an adaptor with a new name
*"re-reads the room from the beginning, `batch_limit` rows at a time, paying a
model call per batch… slow, expensive, and looks exactly like a bug."*

At `min_interval = 86400` that is one batch per DAY. The busiest live room holds
1,362 messages; at `batch_limit = 80` culture would spend **17 days** walking a
backlog it never reads, burning a model call a day to catch up to a present the
room has long since moved past.

So:

- **`batch_limit` must be sized for a real read**, because the batch IS the
  corpus now. An earlier draft pinned it at 1 on the assumption that culture
  asks Honcho and never reads its batch; that assumption is gone
  (`00-overview §4`), and 1 would starve the fold.
- **Seed the watermark at the record HEAD on first run, not 0** — otherwise the
  first fold walks the whole history one batch per day (`runtime.py:508-513`;
  the busiest live room holds 1,383 messages). Culture wants what a room has
  been saying lately, not its origin story replayed at one batch a day.

So the watermark is BOTH gate and cursor here, and the two must be sized
together: it gates on `lag > 0` and it delimits what the fold reads.

## 5. The block

The block vocabulary and its abstraction belong to #219/#220, not here. This
bundle declares the fields a culture block fills and pins no rendering.

Per that work (confirmed with the session doing it):

A culture block fills `ContextBlock`: `name`, `placement`, `order`, `note`,
`header`, `body`, `key`, `untrusted`.

- **`placement = "turn"`, `key = "room"`.** Not the cached head, and the
  reason is the correction that matters. Daily change is *not* the problem; a
  once-a-day prefix miss is nothing. The problem is that the static head is
  **shared across rooms**, so a room-keyed block in it fragments the ~13–17k
  prefix per room, permanently, against a 95–99% hit rate. `_notes_block` is
  already held out of the head for exactly this reason at workspace
  granularity, and the rule `BlockKey` now encodes is that **anything keyed
  narrower than `global` belongs in `turn`, whatever its cadence.**

  So **culture's daily cadence is a production win — one fold a day instead of
  a model call per turn — not a cache win.** That is the honest argument for the
  design, and it is the one this bundle originally got backwards.

- **`note`** and **`header`** are this tier's to write, because nothing else
  can: what the block *is*, in the second person, and its line in the context
  header's catalogue. A block shipped without them is the `<loop_decisions>`
  defect — served every turn with no entry telling her what she was reading.

- **The block asserts its own age.** `<standing>` carries `written=` so the
  model knows how stale it may be; culture must carry the same. This tier
  needs it more than standing does, not less: nothing here retires, so a head
  revision may legitimately be weeks old and still be correct — and a block
  claiming to say "how this room works" with no indication of when it was
  derived is R-27's trap written in prose.

**Never `injection` or `injector`.** The word already carries three meanings
here — prompt injection (R-14), dependency injection, and `TranscriptInjection`
(an assembler, not a block). The vocabulary is `block` / `block source`.

## 6. Who reads it, and the one thing #220 does not settle

> **SUPERSEDED IN PART 2026-09-25 — DEC-207.** The next line named ONE
> consumer, and #312 held every other one back behind its own question. The
> operator widened the tier to every runtime: the tag turn, every fire
> occasion, the proactive pass, both note passes and both standing folds read
> the room's account. The GATE paragraphs below are unchanged and still hold —
> the gate does not read it. `03-decisions.md` «DEC-207» records how #312's
> three questions were answered.

The participation turn is a straightforward consumer.

The **gate** is not, and this must not be glossed. Adopting the shared row
renderer does not open the gate's block set: `render_context`'s *"every block
it learns to add would silently widen this one"* and
`test_the_prompt_holds_exactly_two_untrusted_blocks` are about which blocks
exist, and the refactor changes neither. Putting culture in front of the gate
is a decision owned by the gate, and #220 neither helps nor blocks it.

It is also the decision with the most evidence against it: #144/#147 measured
the gate getting **worse** when given more context, and #216 — an n=5 reading,
never re-taken, closed 2026-09-16 by PR #262 — reported the turn behind the gate
false-speaking on 4 of 5 silence twins. That one shape is now refused by R-97;
the evidence it stands for is that this path has a history of getting worse with
more context. Adding a block to the gate is therefore sequenced last and
measured, not assumed — see
`02-measurement.md §5`.

That the gate is a new-tier's normal cost, rather than a constitutional bar,
is settled: every tier that ever landed added a block. The first draft of this
bundle mistook a pin for a prohibition.

## 7. Scope and keys

`key_kind = "scope"`, one adaptor — and that is still what ships
(`nora/language/culture.py:145`).

> **THE REASON THIS SECTION GAVE IS DEAD TEXT, swept 2026-09-17.** It read:
> *"the workspace half is deleted, because workspace-wide recall is what Honcho
> does by default and re-deriving it is the duplication the operator flagged."*
> That sentence was authored in `db71b8a6` (2026-09-13 02:13, the Honcho-cache
> draft) and survived `f856cccf` 19 hours later, which reversed the premise
> under it, and `6691daa1`, the sweep that was supposed to catch exactly this
> (`03-decisions` correction 13). **DEC-182 restores the workspace half** — it
> says so in its own body, *"fold one workspace's rooms together and the
> boundary is DEC-105's own"* — and `00-overview §4.2` adds that at today's
> single-workspace binding a workspace account is available with no routing
> change at all. So the old reason argues against a decision this bundle has
> already taken.
>
> **What survives is the fact, not the argument.** One adaptor ships, and it is
> scope-keyed. The workspace variant is not deleted-because-duplicative; it is
> **not built yet**, and the thing that would decide it is `02-measurement`,
> not this paragraph. A reader who acts on the old sentence will delete a half
> the decisions restored.

**Honcho workspace: `nora`, for every conversation.** `_workspace_for` returns
the router's answer *"or the single default when no router is wired (today,
every conversation)"*, and no router is wired. Nora's own policy units
(`ridgeline` / `friends` / `wedding`, used by `note` and `decision_log`) are a
different axis and do not select a Honcho corpus. A Honcho workspace named
`ridgeline` exists and nothing routes to it.

**The store keys on `primary_id`**, and that is a consequence rather than a
choice: a Dreamer adaptor is handed `scopes()`, so `primary_id` is its key.
`standing` and `decision_log` key `(platform, conversation_id)` because the
ingest and tag paths are handed the message that arrived. `GLOSSARY.md` carries
the rule (PR #230) and DEC-181 is withdrawn accordingly.

A store keyed differently from its loop matches zero rows on the watermark
query, so the adaptor is scheduled, raises nothing, logs nothing and never
fires. That is why it is worth stating rather than leaving to whoever writes the
migration.
