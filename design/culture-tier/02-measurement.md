# Measurement — what "act" is measured against

## 1. Today there is no measurement, and that is the finding

| path | signal | binds to | durable | ground truth |
|---|---|---|---|---|
| participation | model emits `go`/`no_go` | a model's opinion of a taxonomy | **no** — counters only | 40 constructed cases |
| `proactive_decide` | model emits `remind`/`decline` | same | yes, `decision_log` | none |

Both use a model's verdict **as** the measurement. Per CMS that is the proxy
failure: the signal binds to the taxonomy, not to any real property of the
room. A model agreeing with an instruction someone wrote is not evidence about
the room; it is one interpretation confirming itself.

And a `no_go` is unfalsifiable by construction — she stays silent, nothing
happens, and silence is also what success looks like. This is why 0/35 sat
unnoticed for two days: **a refusal is indistinguishable from health in every
log the system keeps.**

The operator's word for this was 虛偽 — there is a thing called measurement,
but nothing it actually decides.

## 2. The room labels it, for free

The sequence that opened this bundle is itself the instrument:

```
15:51:52  Miles:  so what is the plan tonight?
15:52:10  GATE:  no_go
15:52:44  Miles:  @Ora                     ← 34s later, gives up and tags
15:52:49  Ora:   [answers it correctly]
```

A human overrode the gate and the transcript recorded it. No judge model, no
annotation, no self-attestation.

Four measures, none of which requires a model, and none of which defines when
she should intervene:

| measure | rule | grounded in |
|---|---|---|
| **expiry without action** | a note passes `anchor_date` while open and she never raised it | her own store |
| **HOLDING refusal** | gate said `no_go` for room R at *t* while `HOLDING(R, t)` | her own store |
| **override** | a human tags her in R within ~10 min of a `no_go` | human behaviour |
| **ignored** | she speaks unprompted; the next human line does not engage | human behaviour |

> **HOLDING(room, t)** — an open note (`retired_at IS NULL`) established in
> that room with `anchor_date` near *t* or `revisit_at <= t`; **or** the room's
> `standing` recording an unmet commitment.

The first two are derivable from her own state with no human involved. The
last two come from the room. Neither pair is self-attesting, and the two pairs
fail differently, which is the point of having both.

`expiry without action` is already computable and already failing: the
*Bath gathering* note is anchored 20260908, was still open on 20260912 with
`strikes = 0`. Four days past anchor, apparently never woken. No taxonomy is
needed to call that a miss.

## 3. What blocks all of it today

`nora_relevance_gate_total` is labelled by `platform` only — **not
`conversation_id`**. A refusal cannot be joined to a room, so `HOLDING
refusal` and `override` are not computable even though both sides of the join
exist in the database.

DEC-179 kept the gate out of `decision_log` deliberately and put its verdicts
in metrics instead. That decision is not reopened here. The fix is a label on
the metric it already writes.

**This is the first commit, before any doctrine change.** Without it there is
no number, and every argument about the gate stays two people's intuitions —
which is precisely how the 2026-09-09 regression shipped.

## 4. Thresholds are placeholders

Every number in this document is a placeholder. The 10-minute override window,
"near *t*", the TTL in `01-the-tier §5` — none has been measured, because none
*can* be measured until §3 lands. They are written down so they can be
falsified, not because they are known.

## 5. The honest ceilings

**Override only fires when someone cares enough to tag.** Most refusals are
never labelled either way, because the room simply moves on. It is a *lower
bound* on false refusals and can never be the true rate. It is also blind to
the failure that matters most — the room silently giving up.

**HOLDING cannot see category 1.** Lookupable world facts — weather, opening
hours, a route — are not held by her, so `HOLDING` is structurally blind to
refusing them. Only override reaches those.

**Neither survives into Phase 2.** Both assume there is something she could
*supply*. Open-ended participation has no supply: a friend riffing on 「六七
集團有埋秘書」 is not asking for anything. See `04-phase-two-note.md`.

## 6. What the suite has never had

The 40 labelled cases are 1–4 clean lines with the askable line last. Live
windows are up to 40 rows with the askable line buried and chatter after it.
The suite therefore cannot exercise `participation.md:105-107` at all, and that
bullet is the leading hypothesis for the 0/35 floor.

Live-shaped cases are needed: 30-row windows, askable line mid-window, real
chatter following. Until they exist, a green suite says nothing about
production, which the 18/40-versus-0/35 gap already demonstrates.

## 7. Sequencing, and one warning about the labels

1. `conversation_id` on the gate metric. Small, unblocks everything.
2. The four measures above, as derivations. No behaviour change.
3. **Let it run.** Collect a baseline of what the current gate actually does.
4. Room and workspace culture synthesizers, still consumed by nobody.
5. Only then: `participation.md` from taxonomy to contrast, and `silence.md`.

Steps 1–4 change no behaviour and are safe to land. Step 5 is a doctrine change
and this session already established that changing doctrine without a
measurement is gambling.

**The eval labels encode the old specification.** If plan-recap is to be
admitted, those labels change — `real/plans-tonight` and five other
`members_only` cases. That is re-specification, not making a test pass, and it
must be its own visible commit *before* any prompt edit, or the suite silently
becomes a mirror of whatever the new prompt does.

Note what widening costs: `relevance_gate.py:272` records that a gate saying
`go` to everything except the six safety cases already scores 16. Loosening
moves the instrument toward that degenerate baseline, so discriminating power
drops unless new `no_go` cases are added alongside. The six `forge/*` safety
cases are untouched by any of this — none is `members_only`.
