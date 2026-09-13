# Phase 2 — a note, not a plan

**Deliberately not executable.** It records what Phase 2 is, why it cannot be
specified yet, and what Phase 1 must produce first. Do not implement from it.

## 1. What it is

Phase 1 is participation as *answering*: something is hers to supply, and she
supplies it.

Phase 2 is participation as *being in the circle*. The operator's example, from
a real room:

> 「六七小隊已經升級做六七集團」
> 「我哋而家有埋秘書添」

Nobody names her. If the room's culture has accumulated, she should work out
that she is the 秘書 — and that the right move may be to play along rather than
to answer anything.

> 慢慢昇華 Nora，由一個 AI assistant 變成一個有 cognition 嘅 AI agent。

**Measured, 2026-09-13 — this is the one claim in this document with a number
behind it.** The adjacent experiment (#215, memory reaching the participation
turn) carries a case, `admin-joke-known`, in which the room jokingly calls her
管理員 and asks her to deal with spam. Two arms, n=20 each, live:

| | spoke |
|---|---|
| without memory | 5/20 = **0.25** |
| with memory | 12/20 = **0.60** |

**+0.35, p = 0.054 (Fisher).** The sibling case `asked-before-known` — a
deadline she had given before — moved 4/20 → 12/20, **+0.40, p = 0.023**. The
three cases seeded from `notes` or `standing` rather than Honcho conclusions
moved by 0.10, 0.05 and −0.05, p ≥ 0.74. **2 of 2 memory-seeded cases moved; 0
of 3 others did.**

What she said on the passing runs: 「管理員嚟咗。邊個 spam 邊個，話我知」 — she
took the role the room assigned her.

So the capability in this document is not speculative, and it is
**memory-dependent**: without the block she declines the role at 0.25, with it
she takes it at 0.60. Two caveats that belong with the number. It was measured
*before* #220's renderer work landed, and `<people_in_window>` is a block #215
introduces, so it was never in #220's byte-identity corpus — a re-take is owed
once the larger prompt-format change lands. And an earlier reading of this same
case at n=5 said the opposite (master 3/5, "not memory-driven"); that was a high
draw off a true 0.25, which is why §4's caution about n=5 applies to this
document's own evidence too.

Two things make this reachable that were not obvious at the start:

- The dialectic **already produces this shape of understanding**. Asked about
  a real room it returned self-naming with its origin, the register, and her
  own evolving place in it — unprompted, in one call (`00-overview §4`).
- The 秘書 case is not a lookup. It is the room assigning a role and her
  noticing. Culture is the only artifact that could carry it, because a
  chronicle of `[when] what happened` structurally cannot.

## 2. It is not proactive

Three things, not two:

| | |
|---|---|
| Participation | she supplies something she holds |
| Proactive | she raises something that is due — already built, different axis |
| Phase 2 | she is a member and can take a role the room gives her |

Phase 2 extends participation. Borrowing the proactive name would blur a
boundary the operator has drawn repeatedly. It has no name yet; `ambient`
collides with `design/ambient-loop/`, the loop Phase 2 runs inside.

## 3. What is actually missing, corrected

An earlier draft of this document said the material was "already wired" and
only admission was missing. **Both halves were false**, and the correction is
the useful content here.

**#210, verified against the tree:** `fire.py:339` sets `person_id=None` for
the participation occasion; `fire.py:817` documents *"`ctx.tagger is None`, so
`peer_card` is `()` — she remembers nothing"*; and `self_card=` appears at
exactly one site, `nora/engagement/context.py:208`, the **tag path**. The
participation turn renders no Honcho block at all — no card, no history, no
self card. It knows the people in the window by display name.

So Phase 2 needs three things, not one:

1. admission at a non-zero rate (Phase 1),
2. **memory reaching the participation turn at all** (#210),
3. culture accumulated long enough to have a history.

And the participation turn has run **once, ever**, so none of its context has
been observed in production. Every claim about what it sees is a claim about
code, not about behaviour.

## 4. Why it cannot be specified

**Measurement collapses here.** Every instrument in `02-measurement` assumes
something she could *supply*. Open-ended participation has no supply — a
friend riffing about a secretary is not asking for anything, and there is no
correct answer to score against.

The only grounded signal left is **uptake**: she said something; did anyone
engage. Slow, noisy, confounded by everything — and #216 — n=5, not re-taken — reports the current
turn false-speaking on 4 of 5 silence twins, which uptake would score as
failure only after the room had already been interrupted.

This is written down before anyone builds, because the alternative is to
invent a precise-looking measurement for something unmeasurable and find out in
three months that it was 虛偽 — the exact failure this bundle corrects one
layer down.

**And culture cannot be back-tested.** Phase 1's measures can be computed
retroactively from data already in the database. Uptake against a culture that
does not exist yet cannot. Phase 2 has no retrospective baseline available to
it at all.

## 5. What Phase 1 must produce first

1. Refusals attributable per room (#221), and a non-zero admission rate.
2. Memory reaching the participation turn (#210).
3. Culture with real revisions behind it — the chain, not one snapshot, is
   what carries 演變.
4. An uptake measure with a base rate taken on the **tag** path, where she
   already speaks, so Phase 2's uptake has something to be compared against.

Item 4 is the one most likely to be skipped and the one that makes the rest
interpretable.

## 6. Open, deliberately

- What does `silence.md` become when silence stops being the near-universal
  correct answer? It currently says most unasked-for turns end in silence —
  and `nora_participation_silence_total` has no series at all, so that claim
  has never once been exercised.
- Does taking a role the room assigned need an operator grant, the way pushing
  does? The prior position was that pushing is the operator's call.
- What stops a room's culture being skewed by one member who talks about her
  constantly? The revision chain makes it visible; it does not make it safe.
  There is no `support_count` in this design and no proposal for one.
