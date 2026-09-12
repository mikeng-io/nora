# The Culture Tier — overview

## 1. What was measured, 2026-09-12

Every number here was read off the live deploy on norty during the session
that opened this bundle. None of it is inferred.

### The gate has never once said `go`

`nora_relevance_gate_total`, Prometheus, ~30h retention:

| platform | `go` | `no_go` |
|---|---|---|
| signal | — | 30 |
| discord | — | 5 |

The `verdict="go"` series **does not exist**. That counter has never been
incremented. `nora_relevance_gate_errors_total{kind="call_failed"}` is 2, so
the model is genuinely being called; 2 of the 35 refusals are fail-closed
errors, correctly separated in metrics.

`nora_participation_silence_total` and `nora_participation_spoke_total` have
**no series at all**. The participation turn has never run. Not once. She is
not choosing silence — she has never been admitted.

### The plumbing is correct, so this is not a config fault

```toml
[participation] enabled = true, shadow = false     # live, not mirroring
[standing]      on — 6 rows, one updated the same day
[relevance_gate] reasoning_effort = "none", max_tokens = 512
```

### She was holding something the whole time

`note`, four rows open:

| note | room | anchor | strikes |
|---|---|---|---|
| **AI Hackathon dinner plan** | signal `Lf2L9zqeNUah` | **20260912** | 2 |
| Bath gathering tonight | discord `19362423…` | 20260910 | 1 |
| Bath gathering | discord `19362423…` | 20260908 | 0 |
| Blake 拉人跑馬拉松，邊個落水未定 | signal `MP4ODQOAWL` | 20260911 | 0 |

Row 3 is anchored four days before the reading and is still open with zero
strikes. Row 1 is anchored to the day of the reading, titled *dinner plan*,
and was refused in that room all day.

### `decision_log` — where the two deciders can see each other

| writer | verdict | count |
|---|---|---|
| `proactive_decide` | `decline` | 7 |
| `tag` | `replied` | 3 |
| `participation` | — | **0** |

Proactive **can** see notes and declined 7/7 anyway. It woke on the hackathon
note twice (`strikes = 2`) and declined both times. That is a second, separate
fault and this bundle does not fix it; it is named here because it is the
control case proving that giving the gate more input is necessary and not
sufficient.

### The layer that would have grounded all of this was deleted five days earlier

`room_portrait`: **one row, for one of six rooms, synthesized 2026-08-29**.

> `Its people tease each other. This is a synthesis of the two blocks above —
> not a claim about any one person.`

`runtime.py:814-816`:

> It was ADDITIVE while the room-language consolidators and **the portrait
> synthesizer** ran beside it … **both went with the language tier**, and
> neither is in the signature.

Commit `7020219c`, 2026-09-07. The timeline is the finding:

- **09-07** portrait + language tier deleted
- **09-09** proactive adaptor lands (#131 / #134 / #137)
- **09-11 →** participation runs live, 0/35

The ambient machinery was built two days after the ground it needed was
removed.

## 2. Why 0/35 is not a tuning failure

`participation.md` v5 is a taxonomy: five `go` shapes and a `no_go` list. It
is read by a model configured `reasoning_effort = "none"`, `max_tokens = 512`,
against up to 40 rows of real chat.

A reasoning-less model handed a taxonomy pattern-matches. A pattern-matcher
handed an ambiguous case takes the safe label. **0/35 is the stable output of
a classifier with no stake, not a mistuned threshold.**

Two structural consequences, both measurable in the tree:

**The gate is asked a question its input cannot ground.** `participation.md`
lines 25-27 tell it: *"You see only the transcript and her standing note —
not her memory, not her notes, not her tools' answers."* Then the five shapes
ask it whether she can supply something. It must guess by sentence shape. This
is the 2026-09-09 failure recorded in `CMS.md` — three questions about *time*,
an input carrying no timestamps, a model answering anyway, wrong, at a rate
nothing reported. Here the questions are about *what she holds*.

**The suite cannot see it.** The 40 labelled cases score 18 `go` / 22 `no_go`.
Live scores 0/35. The suite feeds 1–4 clean lines with the askable line last;
production feeds 40 rows where there is nearly always chatter after any
question — which trips `participation.md:105-107` ("an unanswered question
with unrelated talk after it is a question the room dropped"). That bullet
fires on almost every real window and on almost no eval case.

## 3. The layer that is missing

| | timescale | cost | exists |
|---|---|---|---|
| `standing` | rewritten every turn, keeps nothing by design | free | ✅ but see §5 |
| **culture** | slow — what is normally true here | O(1) read | ❌ |
| Honcho representation | per person, accurate | O(members) | ✅, unaffordable per window |

The operator's framing, which is the spine of this bundle:

> 其實你係冇可能 define Nora 幾時去介入。

So stop defining it. A window judged against nothing is a window judged in a
vacuum — "nobody picked this up" is meaningless until you know whether this
room normally picks things up. **The baseline is what turns a window into a
deviation**, and a deviation is something a reasoning-less model can read
because the reasoning already happened, offline, in arithmetic.

That is the reconciliation between the operator's `reasoning_effort = "none"`
and the judgement he wants: the reasoning moves out of the model and into SQL.

The token constraint is solved by scope, not by cleverness. Her own side is
O(1); the room's is O(1); the workspace's is O(1). Only *every member's*
representation is O(members), and that is the one thing this tier never holds.

```
workspace culture   what she is carrying, across rooms      O(1)
room culture        how this room works, where she stands   O(1)
self card           who she is                              O(1)   ← Honcho, exists
────────────────────────────────────────────────────────────────
other people's representations                              tag path only
```

## 4. Why the old tier is not simply restored

The deleted `portrait_synthesizer.py` made **zero model calls**. It was a
template: take `(term, count)` and `(act, count)`, strip person identity,
emit one sentence. It had no inputs of its own. Restoring it alone reproduces
exactly the sentence already in the database.

The chain behind it was smaller than "language tier" sounds — `observer.py`
opens with *"The one LLM call in the Dreamer"*, batched per scope per cycle,
everything downstream arithmetic. It was not expensive. It was swept up in the
09-07 scope cut.

What is kept is the discipline, not the code:

1. **One model call; arithmetic decides adoption.** An LLM glosses, it does
   not set policy.
2. **Person identity stripped by TYPE, not by care.** `_LexemeFact` carried no
   `person_id` field — *"not merely unread, it is not a field that exists on
   the type."* DEC-111 / DEC-120.
3. **Provenance arrays.**
4. **Zero model call at the synthesis step.**

What is dropped: the term index. Per the operator — *我唔需要細到每一個 term
都有 mapping* — and with the lexicon goes `language_scoring`, adoption
thresholds, occurrence counts, decay and rescore, all of which existed only to
maintain it.

`room_portrait` is **not revived**. Its one `narrative` column is the wrong
shape. Per the 09-07 rule, its table is not dropped either; that stays a
separate explicit decision.

## 5. `standing` is the counter-example, and it is not fixed here

```
standing: platform, conversation_id, body TEXT, fold_count, updated_at, …
CHECK (length(btrim(body)) > 0 AND length(body) <= 4000)
```

One text column and a length check. That is the entire validation on Nora's
position in a room, and it is the gate's only non-transcript input.

| | Comprehend | Measure | Serialize |
|---|---|---|---|
| `standing` | ✅ `standing-debrief.md` is good | ❌ nothing can judge a position right or wrong | ⚠️ prose, zero provenance, overwritten in place |

Nothing in it can be traced to the message that supports it, so it can never
be falsified — *a serialize with no provenance is decoration*.

Its trigger is a second defect: standing is written on **tag-turn completion**,
but consumed by the **participation** path. A room nobody tags never updates.

```
signal   MP4ODQOAWL…   same day    fold 7
discord  138583306…    09-11       fold 8
signal   LhofNOBM…     09-11       fold 3
discord  168854749…    09-06       fold 1   ← 6 days
whatsapp 176569429…    09-05       fold 2   ← 7 days
signal   xICeKSui…     09-04       fold 4   ← 8 days
```

Three of six rooms are stale by more than five days. **Standing decays exactly
where ambient participation needs it.**

Culture does not replace standing — different timescales, one is *where I
stand now*, the other *how this place works*. But standing needs the same
treatment, and it is filed separately rather than folded in here.

## 6. Two phases, and the boundary is a measurement boundary

| | | measurable? |
|---|---|---|
| **Phase 1** | Participation — there is something she can supply, and she supplies it | yes: HOLDING, override, expiry |
| **Phase 2** | She is a member of the circle and can take the role the room gives her | **uptake only** |

The phase order is not preference. Phase 1's instruments are the only thing
that will ever tell you whether Phase 2 works. Building Phase 2 first repeats
what already happened: two days of zero, unnoticed.

"Proactive" is a third thing on a different axis — actively pushing the room
forward, already built. Phase 2 extends participation, not proactive, and
should not borrow its name.

See `04-phase-two-note.md`, which is deliberately not an executable plan.
