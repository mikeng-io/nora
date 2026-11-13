# Architecture Decision Records

Nygard-form ADRs: **Context / Decision / Consequences**, one file per decision,
immutable once `accepted` — superseded rather than edited.

## How ADRs relate to the rest

An ADR is the one decision record kept as history: why a decision was made,
what was rejected and what it cost. The design bundle (`design/<topic>/`)
states the design that results, in the present tense; the decision index
lists the decisions that hold now and the test enforcing each. Write an ADR
when a decision has a cost worth recording: an alternative rejected, a trade
accepted, a thing that will look wrong to someone who does not know what it
replaced. A decision without one gets an index row only.

## Convention

- `NNNN-kebab-title.md`, numbered in the order MERGED, whether `proposed` or
  `accepted`. Take the next free number from `origin/master` at merge time
  (`nora-decision`).
- Status: `proposed` → `accepted` → `superseded by NNNN`. Never delete.
- **Consequences must include what got worse.** An ADR with only upside is
  marketing, and the next reader will not trust the rest of it.
- Cross-reference the DEC-n index entry and the enforcing test where one exists.
