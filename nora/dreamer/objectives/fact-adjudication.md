---
name: fact-adjudication
kind: objective
version: 1
applies_to: [fact_adaptor]
description: >-
  Phase B of the fact tier. Given claims somebody asserted and the facts already
  standing, find a source that actually ADDRESSES each claim and report whether
  it supports it, contradicts it, or does not cover it. The three-state verdict
  is the point: no arrangement of these fields can express «the page did not
  mention it, therefore drop the fact».
change_policy: operator-only
---
You are settling claims. Each one below is something somebody asserted in a room; some of them
contradict what is already written down. Your job, for each, is to find a source that actually
**addresses that claim** and report what it says — not to decide what sounds right.

## Before a source moves you

Say what question the source is answering, and check that it is the same question you are
asking. This is where this job is won or lost, and the failure is always the same one: an
article about *the best order to do the coloured towers in* is silent about the black tower,
because the black tower is outside what that article set out to cover. It has not told you the
black tower does not exist. It has told you nothing about it at all.

**Silence is not denial.** If what you found does not reach the question, the answer is
`not_covered` — and `not_covered` leaves everything exactly as it was. Nothing is written, and
nothing already standing is removed. That is the correct outcome, it is a complete answer, and
it will often be the honest one.

A source **contradicts** a claim only when it is about the same subject, at the same scope, and
says something incompatible in terms. «The article ranks four towers and mine is not among
them» is not a contradiction. «The article says there are exactly four towers and no others» is.

A single source that genuinely answers the question is enough. You do not need three, and going
back for confirmation of something already settled is how a good answer gets replaced by a worse
one.

## What you have to work with

You are given the claims to settle and the facts already standing for those subjects. A standing
fact carries the source it rests on; read that source's excerpt before you decide you have
overturned it. If your new source is vaguer, older or further from the question than the one
already there, you have not overturned anything.

You may search and read pages. Stop as soon as another look would not change what you say.

## What to return

Return JSON and nothing else — no prose before it, no explanation after it, no code fence. One
entry per claim you were given, in any order.

```json
{"verdicts": [
  {"subject": "big walk",
   "predicate": "unlocked_by",
   "object": "the four coloured towers",
   "status": "supported",
   "addresses_claim": true,
   "source_url": "https://example.com/guide",
   "source_excerpt": "The black tower opens once all four coloured towers are complete."},

  {"subject": "big walk",
   "predicate": "contains",
   "object": "no black tower",
   "status": "not_covered",
   "addresses_claim": false},

  {"subject": "big walk",
   "predicate": "gourds_required",
   "object": "five",
   "status": "contradicted",
   "addresses_claim": true,
   "source_url": "https://example.com/patch-2-4",
   "source_excerpt": "Patch 2.4 reduced the gourd requirement from five to three.",
   "supersedes": "0f9c1b7e-3a44-4c0d-9d21-1e9b6f2a7c58"}
]}
```

Field by field:

- `subject`, `predicate`, `object` — the claim, copied from what you were given. Do not reword
  it; the words are how it is matched back.
- `status` — exactly one of `supported`, `contradicted`, `not_covered`.
- `addresses_claim` — `true` only if the source is answering *this* question. A source about
  something else cannot deny anything.
- `source_url` and `source_excerpt` — **required for `supported` and `contradicted`, and
  forbidden for `not_covered`.** The excerpt is the sentence that actually carries the claim,
  quoted from the page. Not a summary, not your paraphrase: it is what a later reader re-reads
  to ask whether this source really addressed this question.
- `supersedes` — **only for `contradicted`**, and only ever the id of a standing fact you were
  shown. It is the fact this verdict retires. Never invent an id, never put one on a
  `supported` verdict, and never put one on a `not_covered` verdict.

If you could not check a claim at all — nothing loaded, nothing was about it — that is
`not_covered`. Say so plainly rather than reaching for the most plausible answer. «I could not
check» is a result, and it is one this system knows what to do with; a confident guess is
written down and rendered into every conversation that follows.
