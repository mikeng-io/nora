---
type: evidence
title: "What Was Measured, And What Four Rooms Showed"
description: "The numbers behind the claim that the acting half does not work, the four operator scenarios that fall outside the design, and an honest statement of what the instrument can and cannot currently decide."
tags: [nora, measurement, evals, scenarios]
timestamp: '2026-09-11'
version: 1
---

# What Was Measured, And What Four Rooms Showed

## 1. The suite

`proactive_decide`, 11 cases. Seven expect silence, four expect her to set a
reminder. Run 2026-09-10/11 against the deployed model alias `nora-dreamer`.

| arm | pass | false-positive | recall | samples |
|---|---|---|---|---|
| objective v3 (shipped) | 8/11 | 0.06 | 0.25 | 55 |
| v4 (tool ration stated) | 8/11 | 0.03 | 0.40 | 55 |
| v5 (one read; anchor ≠ deadline) | 9/11 | 0.00 | 0.50 | 55 |
| v5, **re-run, identical prompt** | 7/11 | 0.00 | 0.20 | 55 |
| v5, n=15 | 7/11 | 0.02 | **0.34** | 165 |

The seven quiet cases have been 5/5 or 15/15 in every run. The four acting
cases fail on the majority at n=15: d01 2/15, d02 6/15, d03 6/15, d11 6/15.

## 2. What the two v5 rows mean

Same commit, same model, **same instruction hash `41fb24d2aa1b5708`**: 0.50 and
0.20. The n=15 run puts the true value near 0.34 and both n=5 readings inside
the noise.

Consequence, stated plainly because it was got wrong twice on the night: **the
v3 → v4 → v5 comparison was never decidable.** Two of those steps were reported
as improvements. They were not measured; they were sampled. Filed as issue
#158.

What *is* trustworthy is the asymmetry, because it holds across every run at
every n: she is right to be quiet and unable to act.

## 3. Why the failures are one failure

The four cases fail differently and for the same underlying reason — a
judgement about *time* or *progress* put to a model whose input carries
neither.

- **d01** — dinner tomorrow, nothing booked. Expected: nudge tonight, while
  there is still time to choose. She schedules it for tomorrow afternoon, or
  declines. The prompt tells her the dinner is tomorrow. Nothing tells her how
  long a choice takes to make.
- **d03** — Ben promised the group an answer *by today*; the party is on the
  25th. The index line reads `about Fri 25 Sep, in 14 days`, so triage files it
  as not-today. The deadline exists only as prose beside a loud number, because
  `Note` has one date and it is the wrong one. See `03 §3`.
- **d11** — a note written in the room's own nicknames, both of which are in
  the `<people>` block right above it. She looks them up anyway, 3–5 times
  against a budget of 2. Five interventions have lowered the count and none has
  landed it (issue #160).
- **d02** — the only one that mostly worked, and it collapsed 5/5 → 1/5 the
  moment a hard memory ceiling was added. Told a cap she cannot meet, she
  declines to act at all rather than acting with fewer reads. Reverted.

d02's collapse is the bundle in miniature: **under pressure she resolves toward
silence**, because that is what she is told to do.

## 4. Four rooms

Brought by the operator, from his own groups. Each is stated with what it kills.

### 4.1 The wedding-gift-day dinner

A friend's 過大禮 falls on 27 Sep; the ceremony is over by early afternoon. The
group discussed the day, and someone asked Nora whether they would need to book
somewhere. **She answered — it gets busy, expect a queue — and the thread
stopped there.** Nobody has decided what happens that afternoon or that
evening.

Mid-Autumn is on the 25th, two days before, which makes booking materially
harder. Nobody in the group has connected those two facts; she could.

If she raises it on the 25th or 26th it is already too late. The right time to
start is a fortnight out — not because anything is due then, but because
progress takes several rounds and each one needs the previous to have landed.

**Kills:** that she notices a thread stalling; that she follows up on her own
unacted-on answer; that "when" can be derived from a deadline.

### 4.2 The game the room re-argues from zero

They are playing something now. In a week or two people start asking what is
next; nobody answers. Nearer the end somebody proposes one, and the answers
are *I don't play those*, *that one isn't fun*, *not my kind of thing*. It
stalls again. When the current game finishes they take whatever is nearest.

Every rejection was a constraint stated out loud. Nobody keeps them, so the
next round proposes the same kind of thing and collects the same refusals. She
hears all of it — she is the one being asked *is this game any good*.

Note also that the urgency here is **a state, not a date**: "nearly out of
game" is the current one being ~90% done. That is observable, and it forecasts
a gap two or three weeks ahead.

**Kills:** that a stall is silence (this one dies from engagement); that
rejections accumulate; that urgency needs a calendar; that being late means
nothing happens (here it means a worse choice).

### 4.3 A recommendation with no event behind it

"You may still be on the last one, and I don't know if you have finished — this
came out a few days ago, it might suit you."

Nothing was asked. Nothing is due. Nothing is unsettled. It is worth saying
only because she holds two things the room does not: months of who-likes-what,
and the outside world.

And it is worth saying **only if she knows what they are playing and roughly
how far along**. Without that state, the same sentence is an advertisement.
`web_search` and `web_fetch` are already in her decide-turn toolset; the
objective forbids using them for this:

> Other research — the weather for the day in question, the calendar of the
> place — do when it changes **WHEN** you would speak, not to fill the morning.

**Kills:** that a turn needs a trigger from the room; that outward research is
only for timing.

### 4.4 A friend's sprained ankle

He hurt his ankle at work; there were photos and the group talked about it.
Days later he posts a photo at acupuncture. A word — *is it still bad?* — is
worth saying.

Nothing is undecided. Nobody needs to do anything. It is simply **true for a
while, and then it is not**, and asking after it has healed is worse than not
asking.

And the exit matters more than the content: that sentence dropped into an
ordinary conversation is care; the same sentence arriving at 18:00 as a
scheduled interruption is unsettling.

**Kills:** that a note is an unsettled question; that memory (which never
expires) is the right home for a fact about a person; that one exit fits every
noticing.

## 5. What the instrument cannot currently decide

Recorded here so no later reader mistakes a sampled number for a measured one.

1. **Recall at n=5 is noise** (§2). Any claim of the form "this prompt change
   improved recall" from a single n=5 run is unsupported, including two made on
   2026-09-10.
2. **There is no case for any of §4.** No dateless-but-live note. No state. No
   participation exit. No stall. The suite cannot fail on the thing this bundle
   is about, which means it also cannot pass on it.
3. **The quiet side is trustworthy** and must stay measured: whatever else
   changes, false-positive must not rise. It is the only number that has held
   still all night, and it is the one protecting the room from her.

`06 §4` therefore treats the eval corpus as a precondition of the work rather
than as its follow-up.
