---
name: nora-design
description: Writes and edits Nora's design bundles (design/<topic>/), the anchor the code is built from. Decides whether a change is architecture (design first) or code (straight to code), keeps a bundle at design altitude, and edits it in place. Load before any design/ edit, any architecture change, or when a bundle and the code disagree.
---

# nora-design

The design bundle is the anchor of the system: its high-level architecture,
precise and concise. Architecture is compiled from it: the bundle changes
first, the code is built to it. Git is the log; the bundle only ever says what
the design IS. It is never a log, a decision table or a task list.

## 1. Is this an architecture change?

Ask: **would a reader of the bundle see a difference?** It is architecture when
the change alters

- a component: added, removed, or given a different responsibility;
- what talks to what, or what crosses a boundary;
- an invariant: who may read what, what must never egress, what fails closed;
- the vocabulary: what a turn, unit, note or occasion *is*.

Then the bundle changes in the same PR, before or with the code.

It is a code change when only *how* a part does its job moves: a hotfix, a
refactor, a threshold, prompt or doctrine wording, an eval case, a performance
fix. The design does not move; do not touch `design/`.

Check: the PR's «Architecture» line names the bundle changed, or says `none`
with the reason.

## 2. Altitude: what a bundle holds

| In the bundle | Elsewhere |
|---|---|
| The parts, what each is responsible for, how they connect | **Spec** (field lists, schemas, exact values, config keys, signatures): the code and its tests |
| The model and its terms: a glossary-level contract | **Plan** (phases, steps, migrations, roadmap, open items, acceptance): GitHub issues and the project |
| Boundaries and invariants | **Log** (dates, reviews, measurements, "supersedes", deprecation): git and the PRs |
| Why it is shaped this way: rationale, alternatives rejected | **Status claims** ("shipped", "not built", "designed vs shipped"): the bundle's one status line and its open issues |

A contract stays at the level of terms and promises. *"A note is a claim about
a subject with the evidence it rests on; it names people by id, never by
display name; only Cognition writes notes"* is design. The note's fields,
lengths and rendering are code.

## 3. Shape

```
design/<topic>/
  README.md        overview: purpose, the parts, one status line, open issues link
  <subject>.md     one file per design subject, named for what it describes
```

- Files are named by subject (`boundaries.md`, `notes.md`), never numbered,
  never dated. No `_index.yaml`, decisions, review, draft or roadmap files.
- Budget: `README.md` at most 150 lines, a subject file at most 300. A change
  that would pass the budget is a cue to consolidate, not to split.
- Status is one line in `README.md`: `designed` (ahead of the code),
  `building`, or `built`. The gap between design and code is the open issues
  whose body links the bundle (`**Design:** design/<topic>/<file>.md`); link
  that search (`is:open "design/<topic>/"`), do not list the items.
- No frontmatter dates, `supersedes:` blocks, deprecation banners or
  "was X, now Y". No decision numbers except a link to an ADR the reader needs.

## 4. Editing

1. **Find the owner.** `git grep -n -i '<subject words>' -- design` and read the
   bundle's `README.md`. Check: one file owns the subject, or none does.
2. **Revise the owner in place**: update or consolidate it so it reads as if it
   had always said the new thing. Delete what the change makes untrue. Add a
   section or file only for a subject nothing covers. Check: `git diff --stat -- design` shows edits and deletions,
   not only additions; a purely additive diff needs a new subject to justify it.
3. **Abandoned design is deleted**, not marked. The commit says what replaced it.
4. **Move non-design content out** as you meet it: plans and open items to
   issues (`nora-decision` §1), detail the code already holds to nothing.
5. **Design behind the code** (the code changed the architecture without the
   bundle): bring the bundle up to the code in the same PR, or file one issue
   naming the gap.

## 5. Reading a bundle

The bundle states the intended design. `built` means the code conforms; for
`designed` or `building`, check the code and the bundle's open issues before
assuming a part exists. Where the bundle and the code disagree, that is a
finding: either the code drifted (fix the code) or the design was never
updated (fix the bundle). Decide which from the PR history, not from which
text is newer.

## 6. Check

```bash
uv run python -m scripts.pytest_gate tests/architecture/test_design_is_design.py \
  tests/architecture/test_doc_references_resolve.py
```

PASS is rc 0. The gate checks shape (names, banners, dates); altitude and
in-place editing are the reviewer's check (`nora-review`).
