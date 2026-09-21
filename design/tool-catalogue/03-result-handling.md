# 03 — Result handling (reporting)

> **PARKED — operator decision, 2026-09-22.** Not in scope for this bundle's
> implementation. Miles: *"let's defer and park the docstring compression
> because it's not really a big deal."*
>
> This file stays in the bundle because the **measurement** is the expensive
> part and it is already done — reproducing it later costs a day, and a future
> reader asking "why is 54% of every tool description about handling its
> result?" deserves the answer rather than the question. Nothing here is built
> without a fresh operator decision.
>
> **What parking it costs — far less than the ~6,970 below.** That figure is
> computed against TODAY's architecture, where all 54 schemas are resident.
> Once the catalogue and `load_tools` land, result-handling prose is only paid
> on tools actually fetched: ~54% × ~444 ≈ **240 tokens per turn**, not 6,970.
> Parking the bundle's only non-selection quality risk to forgo ~240
> tokens/turn is clearly the right trade — more clearly than the number in §3
> suggests.
>
> **Read §3's "≈6,970 fleet projection" as a pre-phase-1 figure.** A future
> reader who reopens this for the 6,970 is chasing a prize that phase 1
> already took.

Realizes `00-overview.md`. Covers category 3 only: *what does `unavailable`
mean, may I read the coordinate aloud, must I carry the age?*

## What moves

8,602 tokens — 54% of all description text — is prose read only after a call
returns. Today it is paid on every call of every turn for all 54 tools,
including the ~52 that did not fire.

Heaviest offenders, by share of their own description:

| tool | description | result-handling | share |
|---|---|---|---|
| `send_reply` | 957 | 957 | 100% |
| `where_is` | 692 | 683 | 99% |
| `online_now` | 699 | 651 | 93% |
| `where_been` | 986 | 830 | 84% |
| `parking_status` | 390 | 307 | 79% |
| `generate_image` | 579 | 440 | 76% |
| `transit` | 524 | 359 | 69% |
| `find_parking` | 466 | 305 | 65% |
| `route` | 626 | 395 | 63% |

## The split rule

**Stays in the schema** (needed before deciding to call, or before calling):

- what the tool answers, in one line
- "call this when…", and the boundary with a confusable sibling
- arm and parameter selection — `what="day"` vs `what="places"`
- constraints on what may be **sent in** (`where_is`: "nothing you send
  leaves — only a member id, never a coordinate")

**Moves to the result** (needed only once something has come back):

- every `status` value and what it means
- field semantics — `age_seconds`, `place_kind`, `steam_wired`,
  `profile_hidden`
- what may and may not enter the reply — the coordinate ban, the corridor
  `sequence` ban
- carry-the-age rules
- follow-up routing that depends on the result (`where_is` → `reverse_geocode`
  for a street address, → `route` for an ETA)
- the incident-derived rules (`where_been`'s "NEVER infer a neighbourhood from
  the coordinates", 2026-09-06, guessed Mong Kok/Sham Shui Po and the group
  caught it)

## Measured on three tools

Hand-split, counted, not estimated:

| tool | before | after | cut |
|---|---|---|---|
| `where_been` | 986 | 286 | −700 (−71%) |
| `where_is` | 692 | 139 | −553 (−80%) |
| `online_now` | 699 | 206 | −493 (−71%) |
| **total** | **2,377** | **631** | **−1,746 (−73%)** |

The schema keeps one line in place of what left:

> The result carries its own reporting rules — read them when it comes back.

**Fleet projection, scaled honestly.** These three are the heaviest cases
(84–99% result-handling against a 54% fleet average). 81% of their
result-handling prose was cut, so applying that rate to the measured fleet
total of 8,602 gives **≈6,970 tokens** — not the 73% headline.

## The risk, stated plainly

LongFuncEval measures accuracy dropping **7–91% as tool response length
grows** (<https://arxiv.org/abs/2505.10570>). This change makes responses
longer by construction.

The −6,970 above is a **prompt-token** measurement. It is one side of a
two-sided ledger, and the other side is unmeasured. This change may relocate
cost rather than remove it.

What makes it plausible anyway, and what does not:

- **Plausible:** the prose arrives on the *one* tool that fired, not on 54.
  Mean cold tools per turn is 1.09. A turn pays one tool's reporting rules
  instead of 54 tools' worth.
- **Plausible:** Anthropic's own tool-authoring guidance puts error and
  response handling in responses rather than descriptions, with a measured
  206 → 72 token example
  (<https://www.anthropic.com/engineering/writing-tools-for-agents>).
- **Not established:** that a reporting rule is obeyed as well when it
  arrives beside the payload as when it was read before the call. This is a
  behavioural claim and needs the eval suite. `05-measurement.md` §2c.

Because the risk lands on **quality** rather than tokens, phase 3 ships after
phases 1–2, and is measured on **total** tokens (prompt + response) and on
reporting-correctness, never on prompt tokens alone.

## Boundary with the doctrine

whitefish-c0 is making the same move from the other direction: the Instagram
story-attribution rule ("say it *was* a story and when it was posted, rather
than restating it as a fact") was written as doctrine and is moving to the
tool result, because it is paid only when a story is actually read and sits
next to the `taken_at`/`expires_at` it is about
(`design/instagram-adoption/06-retention-and-custody.md`).

That is the same lever pointed at the 8,288-token doctrine block. It is
**out of scope here** and recorded as an open question in
`05-measurement.md` §5 — the doctrine is authored, operator-owned, and
governed by `change_policy`, so moving any of it is a separate decision with a
separate owner.
