# Architecture Decision Records

Nygard-form ADRs: **Context / Decision / Consequences**, one file per decision,
immutable once `accepted` — superseded rather than edited.

## Why these exist alongside `design/09-decision-index.md`

The decision index is an **index**: one line per decision, naming the document
that made it and the test that enforces it. It is very good at *"which test
guards this?"* and structurally unable to answer *"why, and what did we give
up?"* — there is no room on one line.

That gap bit on 2026-08-06. Several architectural decisions had their reasoning
only in commit messages or in a chat transcript: what Honcho is actually for,
whether a Honcho workspace binds per group, why `ContextBudget` was deferred. A commit
message is not a record — nobody greps commit messages before making the
opposite decision six weeks later.

**Both stay.** The index remains the fast path and the test map; an ADR is
written when a decision has a *cost* worth recording — an alternative
rejected, a trade accepted, a thing that will look wrong to someone who does
not know what it replaced. Trivial decisions get an index line and nothing more.

## Convention

- `NNNN-kebab-title.md`, numbered in the order MERGED, whether `proposed` or
  `accepted` (0014 and 0016 merged as `proposed`). Take the next free number
  from `origin/master` at merge time: two ADRs numbered 0013 landed the same
  day (#330, #331) because both branches took it from their own base.
- Status: `proposed` → `accepted` → `superseded by NNNN`. Never delete.
- **Consequences must include what got worse.** An ADR with only upside is
  marketing, and the next reader will not trust the rest of it.
- Cross-reference the DEC-n index entry and the enforcing test where one exists.
