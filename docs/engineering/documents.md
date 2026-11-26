---
class: engineering
subject: how every document in the repository is classified, placed, linked, named and checked
owns: [document class, source document, derived document, no-overlap rule, document frontmatter, provenance trailers]
links:
  - {to: docs/design/README.md, rel: governs}
  - {to: docs/adr/README.md, rel: governs}
---

# Documents

Every document belongs to exactly one class, and every fact lives in exactly one document. Other
documents link to that document; they never restate it.

## Classes

| class | answers | authority | home |
|---|---|---|---|
| design | how Nora works: parts, connections, vocabulary, invariants, how she looks | **source** of her architecture | `docs/design/<bundle>/` |
| decision | which rules hold now, and what enforces each one | derived from design and the operator's rulings | `docs/design/decisions.md` |
| ADR | why a decision was taken, what was rejected, what it cost | history | `docs/adr/` |
| guide | Nora explained for people | derived from design; defines nothing | `docs/guide/` |
| operations | how Nora is run: hosts, deployment, configuration, observation, backup, restore, incidents | source of operating procedure | `docs/operations/` |
| engineering | how a change to Nora is made and checked: conventions, code standards, orchestration, review, testing, evaluation, releases, lessons, documents | source of engineering process | `docs/engineering/` |
| instruction | what an agent loads | source of agent procedure | `AGENTS.md`, `.agents/` |
| runtime | what Nora herself reads | product code | `nora/doctrine/`, `nora/skills/` |
| log | what happened and when | record | git, PRs, `CHANGELOG.md` (generated), Linear, GitHub Project, GitHub Issues |

The repository root holds only what a tool or convention requires there: `README.md`, `AGENTS.md`,
`CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, the licence, `CODE_OF_CONDUCT.md`,
`THIRD_PARTY_NOTICES.md` and `CHANGELOG.md`. Each one points into `docs/`. A README beside code
describes only its own directory and links design and operations for everything else.

## Rules

- **No overlap.** A subject, term or rule has exactly one owning file, named in that file's `owns`.
  Any other file links to the owner.
- **One direction of derivation:** design → decision → guide. A derived file that states something its
  source lacks is a defect in the derived file. A conflict is resolved in the source first, and the
  derived files change in the same PR.
- **Design is how Nora works, not how she runs.** Hosts, deployment, secrets and backups belong to
  operations, and evaluation belongs to engineering. Each links the design for every "what" and
  "why".
- **No logs in the tree.** A dated file, a review record, an audit or a status report goes to its
  issue or PR.
- **Present tense, no history**, in design, decision, guide, operations, engineering and instruction.
  There are no dates, no "amended", no "was X, now Y", and no superseded or deprecated banners. Git
  and the ADRs hold the history.
- **Policy is runtime-neutral.** Engineering files name no vendor, runtime or model. How a runtime
  performs a step is in `.agents/runtimes/`, and who does which job is in `.agents/lanes.md`.

## Design, decisions and ADRs

| | design | decision | ADR |
|---|---|---|---|
| holds | the architecture, and each rule in its context | each enforceable rule, in one line | the reasoning at the time |
| shape | a sentence in the file that owns the subject | `ID · rule · design link · test or NOT ENFORCED · ADR link` | Context, Decision, Consequences |
| written when | the architecture changes | a rule must hold and be checked | a decision rejected a real alternative or accepted a cost |
| reversed by | revising it in place | revising the row in place, keeping its ID | a new ADR that names the reversed one; the old ADR is never edited |

A ruling from the operator changes the design if it changes the architecture. If it is a rule that
must hold, it adds a decision row and that row's test. If it chose between real alternatives, it
also adds an ADR.

## Frontmatter

Every Markdown file under `docs/` starts with:

```yaml
---
class: design            # one class from the table above
subject: …               # one line, unique in the tree
owns: [ … ]              # the terms and rules this file is the only source for; an entry
                         # may be `term: its one-sentence definition`, which the glossary shows
area: …                  # design only: the code area, nora/<area>/
links:                   # outbound only; inbound links are computed
  - {to: <path>, rel: uses | why | governs | derives}
tracked_by: …            # optional: the Linear project or issue building it; never a status
plain: {term: word}      # guide only: the plain word the guide uses for an owned term
---
```

Dates are not frontmatter; git holds them. `docs/README.md` (the map of `docs/`),
`docs/design/README.md` (the bundle map) and `docs/glossary.md` (every owned term, with its
one-sentence definition, its plain word and its owner) are **generated** from frontmatter and never
edited by hand.

## Design tree

`docs/design/` holds `README.md`, `decisions.md` and bundle directories, and nothing else. Every
bundle is a directory, so its address never moves. Its `README.md` is the overview and holds the
bundle's subjects until it reaches the 300-line budget. A subject that outgrows the README moves to
its own file beside it. Nothing nests deeper than one level. Operations and engineering are
procedure, so they are plain files (`docs/operations/deploy.md`), with no bundle directories.

- A bundle is a code area, named exactly as its package `nora/<area>/`, or a cross-cutting subject
  on the closed list in the bundle map. Adding a cross-cutting subject needs the operator.
- Names are nouns, never numbered, dated or project words (`-adoption`, `-integration`, `-loop`).
- Siblings share a shape: one file per platform under `platform/`, each with the same sections in
  the same order.
- A new subject finds its owner in the map first. Two bundles answering one question are merged.

## Provenance

Every commit carries three trailers, and every PR's «Produced by» section lists every builder and
reviewer with the same three fields:

```
Environment: <the ADE or IDE>
Agent: <the coding agent>
Model: <the exact model id>, with the effort when one is set
```

The trailers are git trailers, the message's last paragraph. No author is exempt: a local hook
cannot authenticate one, and release automation and the dependency bot commit on GitHub, where the
hooks do not run.

## Checks

`scripts/design_shape.py` becomes the document gate. It fails on:
- a missing or invalid frontmatter field;
- a `subject` or `owns` entry claimed twice;
- a link to a path that does not exist;
- a file in the wrong home for its class;
- a root file not listed above;
- a numbered or dated file name;
- a vendor name in an engineering file;
- a generated file that differs from its regeneration.

The commit-message hook and the PR content check refuse a commit or PR without provenance. Existing
debt sits in a baseline that only shrinks.
