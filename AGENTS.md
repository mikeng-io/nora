# AGENTS.md

**The entry point for any agent working in this repo** — Claude Code, Codex,
Cursor, or anything else that reads an [`AGENTS.md`](https://agents.md). It is
vendor-neutral on purpose: `CLAUDE.md` is a pointer at this file, not a second
copy, because two copies drift and the one that drifts is always the one you
did not read.

This file is the terse, scannable rule set. The reasoning, the war-stories, and
the specific values behind these rules live in **`docs/CONVENTIONS.md`** — read
it when a rule needs its "why"; every terse rule below is only safe because that
detail is preserved. **`CONTRIBUTING.md`** carries the branch, commit, PR and
issue conventions. **`.agents/skills/`** carries the procedures: one skill per
recurring task (landing a change, deploying, running the eval, proving a fix,
recording a decision, writing a tool). **When a task matches a skill in the
table under [Skills](#skills-the-procedures), load it before the first edit**,
not after the first mistake: the rules below are what must hold, the skill is
the order of the commands that make them hold.

## The spine: comprehend → measure → serialize

Every path in this repo already runs this loop; it is named here so new work — a
tool, a stage, an **eval** — keeps to it rather than reinventing a weaker shape.
It is a **constraint, not a procedure**: not *how* to build a thing, but what
every step must preserve. (This is UACP's CMS applied to Nora; the three verbs
are the same everywhere the codebase reasons, decides, or commits state.)

- **Comprehend** — turn raw input into a *computable model*, and interpret it
  **once**. A Signal message becomes a `NormalizedMessage` → `TagContext` (tagger
  resolved, transcript, peer card, the prefill blocks); everything downstream
  computes on that fixed model and never re-parses the raw text. Interpreting
  twice is how two copies drift.
- **Measure** — reduce the model to a **decidable signal**: grounded, and
  **fail-closed**. This repo's signature is the R-27 three-state tool result —
  `ok` / `nothing_found` / `unavailable` — where an ERROR is never a PASS: a
  wiring gap is `unavailable` («I couldn't check»), never `nothing_found`
  («there's nothing»), and a stale/absent fix is `unavailable`, never a guessed
  point. The guards are the *negative* measurement — R-5 (silence beats a wrong
  answer), R-18/R-28 (no identifier or coordinate egresses), the disclosure
  subset (R-31 fails closed on any unknown in the room). **Bind to the real
  property** — a `where_is` fix is a live GPS read, a `weather` reading is HKO's
  own — never a proxy, and never assert what you cannot ground.

  **When the measurer is a MODEL, its input has to explain itself.** A block
  handed to a model is not evidence because it is present; it is evidence when
  the model can tell what it is, how old it is, and which way it reads. Every
  question you ask the model, ask of the block first: if the judgement turns on
  *when*, the block carries a time; on *who*, it carries an index; on *whether
  this is still open*, it carries the thing that would close it. A judgement
  whose input cannot ground it does not fail loudly — the model answers anyway,
  and it answers wrong at a rate nothing reports.

  **So when a model judges badly, read its input before you rewrite its
  instruction.** Measured three times over on 2026-09-09/10 and it went the
  same way each time: the Relevance Gate was asked whether a room had moved on,
  was still waiting, or had been answered — three questions about time — from a
  transcript with no timestamps and no statement of which end was newest. Three
  rewrites of its instruction changed nothing or made it worse, one of them
  breaching a safety bar. Rendering an age per row cut its error rate by two
  thirds over 40 labelled cases. The same omission was then found in
  `<standing>`, which asserted it was current while carrying no time, and in the
  note curator (deleted 2026-10-03, DEC-277), which was asked whether an anchor
  had passed and was given no clock at all. The instruction is the first thing blamed and usually the wrong
  one.
- **Serialize** — fix the result into **canonical, typed state with provenance**.
  Her reply is the one authored surface (strict-silence: only a `send_reply` tool
  call delivers; the never-silent fallback is delivered but explicitly kept out
  of her authored `sent_texts`). The audit rows and the Honcho feed carry her
  words under the Nora aiPeer with their `doctrine_sha256`/`tag_id`; the Langfuse
  trace is the provenance every claim about a turn re-derives from.

**The three rules that make it hold** (UACP's, and they are load-bearing here):

1. **The discipline IS the engineering.** A `measure` that isn't fail-closed, or
   a `serialize` without provenance, is *decoration* — the narrative reads
   systematic while the result rots back into re-judgment.
2. **No self-attestation.** "Done" is never decided by the doer — it is
   **measured against evidence** (a green gate, a moved metric, a read-back
   trace) judged by something else. This is exactly the "Never claim a merge,
   deploy or fix without reading back the state" rule below.
3. **Re-derivable, so no actor is trusted.** Every step binds to reality and
   carries provenance, so a turn — or an eval verdict — can be reconstructed and
   checked *without trusting the producer*.

**Every decision and every judgement passes the five Cs** — a design, a
review finding, a fix, a report, a "done". Name the C that fails; a verdict
that names none is not a verdict.

1. **Coherence** — it holds together as one model: no part contradicts
   another, one name per thing, one owner per job.
2. **Consistency** — it agrees with what already exists: the same component,
   pattern, store and word the repo already uses (context assembly for every
   block, the existing verbs and stores). A parallel custom part fails here.
3. **Conformance** — it follows what was ruled: this file, the decision
   index, the design, the operator's rulings.
4. **Correctness** — it is true and it works, measured against evidence (a
   named test, a read-back), never asserted by whoever built it.
5. **Completeness** — it covers the whole scope: every caller, every doc,
   every deleted name, nothing left "still in shadow" or "not yet built".
   Coverage, not correctness; each C is checked on its own.

**Apply it at every grain** — the whole turn AND each sub-step, down to one tool
call. `serialize(N)` is the input you `comprehend(N+1)`. The **eval layer is this
same loop one level up**: comprehend a real user-journey into a typed case,
measure the LIVE model's trajectory fail-closed (empty reply / wrong path /
budget blown = FAIL; timeout = ERROR ≠ PASS), serialize a verdict bound to its
Langfuse trace. Same three verbs, same mindset — that is how a new capability's
evaluation reuses this framework instead of growing a bespoke one.

## Build the least that works

Nora is a private, experimental project among friends, not a production
service. It must stay maintainable (clear code, a green gate, true docs, one
deploy path); it does not need production ceremony. Distilled from
[ponytail](https://github.com/DietrichGebert/ponytail) (MIT), and active on
every task: writing, fixing, reviewing, designing.

**Read first, then climb the ladder and stop at the first rung that holds.**
Trace the real flow end to end before choosing; the ladder shortens the
solution, never the reading.

1. **Does it need to exist at all?** A speculative need is skipped, in one line.
2. **Already in this codebase?** Reuse the helper, store, verb or pattern a few
   files over. Re-implementing it is the most common slop.
3. **Stdlib, a Postgres constraint, or an installed dependency does it?** Use
   it. Never add a dependency for what a few lines can do.
4. **One line?** One line.
5. **Only then** the minimum code that works.

**A guard needs a consequence.** Before writing a validator, cascade, cap,
alert, ledger, switch or review round, name the harm it prevents and show it
can happen: a live count, an incident, or a real trust boundary (an identifier
or coordinate egressing, a room reading another room, a credential). Then check
that the store that matters honours it. If the harm has never happened and
nothing breaks without the guard, leave it out and note the ceiling in one
line. 2026-10-03: two days built delete-withdrawal cascades, lineage, a shadow
compare and a live ledger around a loop that had never written a row. Live
there had been 2 deletes ever, and Honcho keeps the deleted text anyway: it
has no per-message delete.

- **A boundary is drawn by scope, never by a filter inside it.** The unit is
  the read boundary (DEC-231): if two rooms must not share what is said, they
  belong in different units. `[lineage]` is the example kept to learn from:
  built, marked do-not-enable, and leaky anyway because Memory has no lineage.
- **Ship the feature, then harden what breaks.** Undo is a config flip and a
  restart, or a database backup, not revert, pause or ledger machinery.
- **One integrated change from one spec**, measured once by snapshot replay.
  No piecewise PRs with nothing measured between them, and no shadow week at
  this volume.
- **No unrequested abstraction**: no port with one implementation and no
  reason, no config for a value that never changes, no scaffolding "for later".
- **Deletion over addition; fewest files; shortest working diff**, but only in
  the right place. A bug fix goes where every caller routes through, not in
  the one path the report names.
- **A deliberate corner gets a `# ponytail:` comment** naming its ceiling and
  the upgrade path (`# ponytail: one global lock; per-unit locks if sessions
  contend`).
- **One check, not a suite.** Non-trivial logic leaves the smallest test that
  fails if it breaks. A mutation table, a second review round or a strongest-
  tier reviewer is spent only where a real failure was found.
- **Complex ask: ship the small version and question the rest in the same
  report.** "Did X; Y covers it. Need full X? Say so." Never stall on a choice
  you can default.

**Never simplified away:** the egress and disclosure rules (R-18, R-28, R-31),
fail-closed measurement (R-27), error handling that would lose input, and
anything the operator asked for explicitly.

## Skills: the procedures

Each skill is a `SKILL.md` under `.agents/skills/` (the cross-tool location);
`.claude/skills/<name>` is a symlink to it, so Claude Code reads the same
files and there is one copy. A skill is the ORDER of the commands and the check after
each one; the reasons stay in the documents it links. Load the skill when the
task starts, not when it goes wrong: the audit behind them (ADR 0017) found the
rules were written down and not read at the moment they applied.

| when the task is | load |
|---|---|
| starting a change, running the gate, committing, opening or merging a PR | `nora-land` |
| putting merged code or config live on `nora-host`, or checking it is live | `nora-deploy` |
| cutting a release: the release-please PR, the `vX.Y.Z` tag, the GitHub Release | `nora-release` |
| reading a trace, a metric, a row or a log as evidence | `nora-readback` |
| confirming a built prompt, doctrine, tool-text or routing fix with the eval suite (find the cause offline first) | `nora-eval` |
| proving a fix is guarded (revert, watch a named test go red, restore) | `nora-prove-fix` |
| a new DEC or R row, an ADR, an issue, or citing a decision | `nora-decision` |
| adding or changing one of Nora's tools (ADR 0015) | `nora-tool` |
| adding or changing one of Nora's runtime skills in `nora/skills/` (ADR 0016) | `nora-runtime-skill` |

`nora/skills/` holds NORA's skills, which she loads at runtime; `.agents/skills/`
holds the skills of the agents that build her. `tests/architecture/test_agent_skills.py`
keeps this table and that directory in step.

## Orchestrating: who builds, who checks, and how much effort

**The session the operator talks to is the orchestrator, not the builder**
(operator ruling, 2026-10-21). It comprehends the ask, writes the brief, picks
the agent and the effort, launches, reads what comes back, decides, and lands
and reports through the skills above. It does not write the implementation
itself. The work goes to three lanes:

| the task | lane | how it is launched |
|---|---|---|
| build, fix, debug, a design bundle, merge conflicts: anything that edits code, tests, doctrine or design | Codex, `gpt-6.1-sol` | T3 `delegate_task`: `providerInstanceId: codex`, `model: gpt-6.1-sol`, `options.reasoningEffort` = the effort table's level (it takes all five), `isolation: worktree` |
| independent review, audit, verification of a built change or a PR | Kimi CLI (K3, 256K context) | `kimi -p "$(cat <brief>)" > <log> 2>&1`, run in a checkout of the head under review |
| mechanical: renumber, rename, doc sync, config edit to a given value, read-back, lookup, log or diff fetch | Claude `haiku` | the native sub-agent tool with `model: haiku`, or `delegate_task` with `claudeAgent` / `claude-haiku-5-5` |
| a light check: read a gate rc, open a cited line, compare two shas, count a log | the orchestrator itself | no launch; a launch costs more than the check |

- **The builder never reviews its own work**, and neither does the
  orchestrator that wrote its brief. Codex builds and Kimi reviews by default,
  so a review crosses vendors. A follow-up on a different lane or effort is a
  new launch with the old report pasted in; resuming keeps the model.
- **A brief carries everything.** A delegated agent starts with no history: the
  brief names the spec or the findings, the worktree and branch, the skills to
  load (`nora-land` for a build), the tests that must pass, and what to report back.
  A review brief adds the PR's claims and the diff command (`gh pr diff <N>`).
- **A `delegate_task` worktree starts from the orchestrator's local commit**,
  not `origin/master`, and this checkout's `master` lags. A build brief's first
  step is `git fetch origin && git switch -c mikeng-io/<slug> origin/master` in
  that worktree, in place of `nora-land` step 1's `git worktree add`.
- **Keep Codex on the build; it drifts into checking.** `gpt-6.1-sol` is
  over-proactive: left alone it re-reviews its own diff, runs the suite again
  and again, invents edge cases and widens the change to guard them (operator,
  2026-10-21). Its brief bounds that: build to the spec, run the named tests
  and `nora-land`'s loop once, and stop. It does not run the full gate (the orchestrator runs it once, on
  the final head), launch
  its own reviewers, add tests or handling the spec did not ask for, or touch
  files outside the brief; an edge case it notices goes in its report as a
  line, not into the diff. It still checks its own work, but that is the
  minority of the run. The orchestrator guards it, reading the diff for
  scope creep and anything reckless, and the review lane does the checking.
- **Kimi is told it is read-only, and that is measured, not trusted**: `-p`
  cannot be combined with `--plan`, so `git status --short` in its checkout
  after the run must print nothing. Its thinking stays on; it takes no effort
  flag, so the brief's scope is the lever. Its findings are verified like any
  other review's (`nora-land` step 7).
- **When a lane is down, use the next and say so in the report**: implementation
  falls back to Claude `opus` through the native tool; review falls back to a
  fresh `gpt-6.1-sol` launch that did not build the change (or `opencode/kimi-k3`
  through `delegate_task`); mechanical work falls back to `gpt-6.1-sol` at `low`.
  A lane is down when its launch fails or its quota is spent, not when the
  orchestrator would rather do the work.
- **Say the lane AND the effort in every launch** (where the lane takes one);
  never let either default.

**Effort is adaptive: it follows the task's ambiguity and what a miss costs,
not the lane** (operator, 2026-10-07). The lane picks the model; effort picks
how long it thinks. Start at the row's level and move it on evidence:

| the sub-agent's task | effort |
|---|---|
| lookup, search, read-back, rename or renumber, doc sync | low |
| build to a finished spec, off by default or additive; fix findings that name a file and line; review of an off-by-default or additive change; merge conflicts, config edits | medium |
| build that needs judgement inside a spec (a new key, a migration, a write verb); debugging after a first fix missed; review of a change live on merge | high |
| build with no spec (design judgement); a design bundle or its audit; review of a change touching disclosure (R-18, R-28, R-31), a lock or concurrency path, a hot-table migration, or one that starts spending model calls | xhigh |
| a round after two BLOCKERs or a data-loss finding in the same area; a security or disclosure audit before publication | max |

- **Escalate effort before model.** Each BLOCKER raises the next round in the
  same area one level, and a second BLOCKER or any data-loss finding goes to
  `max` directly. A third BLOCKER in the same area at `max` is the orchestrator's
  cue to re-read the spec and the input before launching again: the brief, not
  the model, is the usual cause. Each new task starts at its row's level,
  whatever an earlier one needed.
- **An explicit operator override for a session wins** («only use low»).
- A resumed agent keeps its effort as it keeps its model: a different level
  is a new launch with the old report pasted in.

## Reporting to the operator

**Two registers, and using the wrong one is the common failure.**

**THINKING** — architecture, brainstorming, design trade-offs, *why is this
wrong*. **Full depth. Take the room you need.** Compressing an architectural
argument damages it, and terse reporting must never bleed into design
discussion.

**Proceed unless blocked.** Before stopping to ask, answer two questions: is
there a blocker, and is there a fact only the operator holds? If both are no,
carry the work end to end (merge and deploy included, through the skills
above). A decision you can make from the evidence is not input; asking for it
is ping-pong: the operator restated this at the start of work more than a
dozen times between 2026-09-08 and 09-22.

**REPORTING** — work finished, a check ran, a peer replied. Terse:

```
<the result, first line, no preamble>

STATUS      what is true now
DECIDED     what changed — one line each
NEEDS YOU   only what genuinely requires the operator, with the real trade-off
```

- **Headers are subjects, not verdicts** — *"notes never called"*, not *"three
  things I got wrong"*.
- **Lead with the conclusion.** If the most important fact is in paragraph five,
  the report failed.
- **No internal identifiers in prose unless asked** — no section numbers, metric
  names, instance counts. The operator should not have to look something up to
  parse a sentence.
- **Reasoning goes in the artifact, not the message.** Design nodes carry
  provenance because they must be re-derivable; a status report must not.
  Applying the spine's provenance rule to *conversation* is a category error —
  it is the single most common way these reports go wrong.
- **Errors get one clause, then move on.** Never a paragraph on how you found it,
  never a tally of past mistakes.

## Decisions change on EVIDENCE, not on argument

When a peer, a reviewer or the operator pushes against something already decided:

1. **Is there new evidence** — a measurement, a read-back, an executed probe, a
   file that says otherwise?
2. **Yes** → change it, state the evidence in one line, do not narrate the
   reversal.
3. **No** → **hold the decision.** Say what it rests on and what reading would
   overturn it. A better-argued restatement of a known position is not new
   information.

**Deference-to-recency is the failure**: treating the most recent confident input
as authoritative. It reads as responsiveness and is the absence of a position.
2026-08-27: one session flipped three times on the same build-vs-vendor question
with **no new measurement between the flips** — which means it never had a
decision, only a preference.

**Write the reversal condition when you decide.** *"This holds unless X"* — the
bundle already does this for design nodes (*"the reading that would reopen it"*);
it applies to any position you state. It makes the next challenge cheap to answer
and makes a flip visible as a flip.

**A decision the operator has locked is closed.** Surface new reasoning; leave
the decision.

## Reading order

1. `README.md` — what Nora is, how to run her.
2. `docs/CONVENTIONS.md` — the detail behind this file: gate reasoning, the "Never"
   war-stories, the five operational constraints in full, the conventions.
3. `ARCHITECTURE.md` — the map: topology, package table, ports, a walk of one
   ambient message and one tag.
4. `design/09-decision-index.md` — one line per decision, pointing at the
   document that made it and the test that enforces it.
5. `design/*.md` — the topology documents the decision index points into.
6. `CONTRIBUTING.md` — branch, commit, PR and issue conventions, and the
   issue → ADR → decision-row flow.
7. `docs/adr/` — Nygard-form ADRs: why a decision was made and what it cost.
8. `DESIGN.md` — the visual system for every Nora surface (portal, social,
   imagery, 3D): palette, type, shape, motion. Read it before touching `app/`
   styling or anything she looks like.
9. `docs/observability.md`, `docs/eval-sop.md`, `docs/runbook.md` — how to
   read an instrument back, how a behavioural change is measured before it
   ships, and what to do when production misbehaves. Read the one the task
   touches; the matching skill (below) is the procedure, these are the reasons.

**A `docs/superpowers/…` path is a historical citation, not a location.** That
tree — 34 dated specs and 35 plans — was **removed 2026-09-08** along with
`.superpowers/`; the process it named is gone and is not coming back. The paths
still appear in design nodes and docstrings as provenance for
where a decision was first written, and they resolve only in `git log`. Treated
the same way as a `DEPRECATED` decision row: marked, never rewritten to point
somewhere it does not point.

**Deprecated 2026-09-07 — the knowledge tier (the "fact tier") and the language
tier.** Both are gone: the code is deleted, the decision-index rows are marked
(never renumbered), and every design node and spec that describes them carries a
`DEPRECATED` banner. **Their database tables are NOT dropped** — the code goes,
the schema stays, and dropping them is a separate explicit decision. The evidence
that decided it is `design/dreamer-agent-loop/20-knowledge-tier.md` §0; the issue
is #115. Nothing under `design/dreamer-agent-loop/` numbered 20, 21, 23, 24, 25,
28, 31 or 32 describes code that exists.

**Deprecated 2026-09-08 — the note tier's entity-resolution chain and the
curator's merge half.** With nothing minting entities, `note_entity` could never
be populated and merge was inert by construction; the registry store, the
resolution pass and the merge verb are deleted (their tables stay). The
curator only retires. R-75 in the decision index says which clauses survive. A
model-judged merge returns in a separate `note-merge` adaptor (DEC-202, design
41 §7, designed 2026-09-24, built 2026-09-25 behind a `merge_mode` key that has since been
removed, first run in `shadow`, which wrote no note); the curator
stayed retire-only.
**2026-10-03 (DEC-277): `note_candidates`, the curator and `note-merge` are
deleted; the Cognition session writes, retires (`note_retire`) and merges
(`note_merge`) notes, and `[notes] enabled` is the only notes key left. The
paragraph above is the history.**
The create gate in `note_candidates` matched on anchor date alone from that
day until **2026-09-13**, when two firings in production merged two unrelated
occasions twice (#228); it now resolves the note title the MODEL names, and
the date is only when the thing happens.

## The three config files

Three files, three lifetimes, deliberately not merged (DEC-8's argument):

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | **who people are** | a human joins |
| `policy.toml` | **who may talk to her, and where** | trust changes |

**All three are gitignored now** — `config.toml` joined the other two on
2026-09-07 (operator decision). It was tracked on the argument that it holds no
secret values, and what made that untenable was collision rather than secrecy:
the live file is a bind-mount source the operator edits in place, so every
merge touching it fought an uncommitted working change and each session had to
stash-merge-restore-diff by hand to keep from losing an edit. `config.example.
toml` is now the only committed record of the config's shape, and
`tests/test_env_template.py`'s three drift checks compare the two — but
`config.toml` is gitignored in every dev worktree and in CI, so they SKIP
there by design (`tests/test_env_template.py::_load_live_or_skip`); nothing
in the established workflow (worktrees only, never the deploy checkout —
"Reaching the deployment" below) runs them for real. What
DOES run automatically, once per eval, is a different and narrower property:
`evals/subject_config.materialise()` rebuilds the eval subject's config from
the live `config.toml` and REFUSES to start the run if the two still disagree
outside `ALLOWED_TO_DIFFER` (issue #321). It does not subsume the drift
checks — it self-heals around a template that is stale or incomplete rather
than requiring the checked-in text to be right, so it protects the *computed
eval config*, not `config.example.toml` itself as the thing a human reads to
reason about the deployment. That is still only `test_env_template.py`'s job,
and it stays SKIP-based (a fixture would compare the template to itself,
green forever, measuring nothing — the exact anti-pattern the file's
docstring warns against).

**No config change reaches a process unvalidated** (deploy convention §4.5,
implemented 2026-09-08): `nora config set/get/unset/validate/path --config
<path>` validates against the schema, prints what changed, and never touches
`.env` — secrets stay there and are referenced by name. On the deploy host the
CLI cannot write the live file from inside the container (`EBUSY` on the file
bind-mount, 2026-09-09), so the live procedure is `nora-deploy` step 5: edit a
COPY, `nora config validate` the copy through the running container, `cp` it
into place (in place, so the mount keeps its inode), recreate, and restore the
backup on any failure. An unvalidated hand edit of the live file is never the
procedure: `extra = "forbid"` catches a broken file only by crash-looping.

`config.toml` is ALSO bind-mounted read-only into the two voice sidecars
(`stt`, `voice-wake`): they read `[speech.stt]` / `[speech.wake]` with stdlib
tomllib at boot (2026-09-17 — before that, the same knobs were env vars in
compose and disagreed with this file). The last two are bind-mounted read-only and **loaded once at
startup** — editing either without a recreate changes nothing and warns you about
nothing (`nora_unmapped_senders_total` is the tell). `identity.toml` takes one
`[[persons.<id>.<platform>]]` block **per account**, all resolving to one
`person_id` (their Honcho peer, A2/DEC-14). `policy.toml` is deny-by-default and
Nora **refuses to start** if it is missing or allows nothing (R-5). Memory's
hierarchy is split across them on the same lines (DEC-214): `policy.toml`
`[units]` says which workspace each unit is in, `identity.toml` `[workspaces]`
binds each workspace to its Honcho workspace, and `memory.workspace_id` is only
the binding used when there is no `[units]`.

## Reaching the deployment

Deployment examples use the SSH alias `nora-host`; development happens locally.
Configure that alias for your own Linux deployment host before using the commands.
The reference checkout path is `~/workspace/nora`; substitute your installation path
throughout the runbook and agent skills if it differs. The host is
a **deploy target, not a dev checkout**: nothing is developed there. Sessions
do run commands on it (deploys, eval runs, read-backs), and its checkout has
collected commits that were never pushed, so read `git status -sb` there before
any pull and push what you find to a branch rather than resetting it. Deploys
from different sessions must not overlap (`nora-deploy` takes a lock). Every
read-back the "Never claim without reading back" rule demands is one `ssh`
away; `nora-deploy` and `nora-readback` carry the commands.

Configure `~/.ssh/config` with your own address, login and dedicated key:

```sshconfig
Host nora-host
    HostName <deployment-hostname-or-address>
    User <deploy-user>
    IdentityFile ~/.ssh/nora-host
    IdentitiesOnly yes
```

- **Key auth only.** `~/.ssh/nora-host` is a dedicated ed25519 key, installed once
  with `ssh-copy-id`; `Host nora-host` in `~/.ssh/config` binds user, host and key.
  **Never `sshpass`, never a password in `.env`, in chat, or in a file a session
  reads** — a password typed into a session is transcribed into memory (this
  happened once), and `.env` is the compose environment on both machines.
- **`config.toml` on the host is the only copy that matters.** It is untracked
  since 2026-09-07; the worktree's copy, if any, is stale. Read the live file
  before asserting what a flag is set to.
- **The db container is reached through compose** (`docker compose exec -T db`),
  never by grepping `docker ps` for a name — unrelated databases may live on the
  same host and match the same patterns.
- **Container logs ARE on the docker log driver, since 2026-09-09** — `docker
  compose logs --no-log-prefix nora` gives JSON lines for `jq` (without the
  flag every line starts `nora  | ` and `jq` rejects it). It was a file
  (`data/nora.log`) for five weeks because this module landed one day before
  the Dockerfile, and «`docker compose logs nora` is empty» was documented as
  a rule in three places instead of fixed. Anything still telling you to grep
  `data/nora.log` is stale. The metrics endpoint and the tables remain the
  stronger evidence for whether a background pass RAN.

## Gate commands

**While iterating**, use the fast loop — it is what the edit-run-edit cycle is for:

```bash
make test-affected     # targets from the LAST COMMIT (base HEAD~1), not uncommitted edits; -n 8
make test-fast         # the whole suite, -n 8
```

**Before claiming a PR is done**, run `make gate` once, on the final head: ruff,
mypy and lint-imports in full, then the tests the branch's diff against
`origin/master` can reach (the whole suite when it cannot map the diff, or when
the selector itself fails: only an explicit `NONE` runs nothing). While
iterating, run the tests you touched or `make test-affected`; never a gate per
fix round. `make full-gate` runs all four in full, serially:

```bash
make full-gate         # all four, in order — or run them individually:

uv run pytest -q                                         # tests green
uv run ruff check nora/ evals/ tests/                    # lint clean
make typecheck                                           # types clean (see the scope note)
uv run lint-imports                                      # import boundaries clean
```

**What "types clean" actually covers.** The `typecheck` target in the
`Makefile` is the list: 13 packages, and the strict override in
`pyproject.toml` (`[[tool.mypy.overrides]]`) names the same 13 (#121,
re-measured 2026-09-17). Everything else under `nora/` (`agent`, `compute`,
`platform`, `testing`, `obs` among them) is not type-checked, so a green gate
means "those 13 are", not "this repo is". Measure a package's OWN errors by
scoping the override to it, never by putting strict flags on the command line:
those leak into every imported module and once inflated a count 15x
(`pyproject.toml` carries the method).

A fresh worktree needs `uv sync --extra evals` once, or pytest dies at
COLLECTION on the eval modules — `strands-agents-evals` is a declared optional
extra and each worktree has its own venv.

The gate is the FINAL check, not the iteration check. It takes ~8 minutes; running
it after every edit instead of `make test-affected` costs an hour a day and buys
nothing — this file previously named only the serial form, and that is exactly the
mistake it produced (2026-08-21: eight full gate runs in one session).

## Releases

Release notes are the Conventional Commit subjects (DEC-314). release-please
(`.github/workflows/release-please.yml`) keeps one open release PR on GitHub,
built from the `feat`/`fix`/`security`/`perf`/`change`/`doctrine` commits merged
since the last `vX.Y.Z` tag. A repository-scoped GitHub App queues that PR for
automatic merge after required checks; merging it makes the tag and GitHub Release.
Without the App (or while private), the manual merge path remains. Documentation
and design alone never qualify: `scripts/release_scope.py` requires a visible
commit changing runtime inputs. Setup: `docs/github-maintenance.md`. Nothing else releases: no release fragments, no hand tags, and no
announcement to a room. Write each subject as a line a reader of the changelog
can use. The `nora-release` skill has the procedure. A deploy is not a release:
`scripts/build-image.sh` stamps whatever it builds with `git describe`.

The schema gate is `tests/infra/test_schema_parity.py`, **not** `alembic check`
(which is blind to the CHECK constraint and the `ts DESC` index ordering); both
run in `pytest`. There is no module-size gate (deleted 2026-08-06). See
`docs/CONVENTIONS.md` for the DEC-11 story, the `lint-imports` cache note, and why
the size gate went.

## Design backlog

Not-yet-built design ideas discussed in a session get filed as a **GitHub issue**
on `mikeng-io/nora` (not left only in chat/memory) and tracked there — comments,
scoping, status all live on the issue, not scattered across sessions. Only a
decision that actually lands gets a `design/09-decision-index.md` entry; an open
issue is a candidate, not a decision. Example: #4 (per-conversation rolling
summary — update-in-place, separate from the Dreamer and Honcho).

## Adding or changing a tool

Read **`docs/adr/0015-tool-surface-policy-v2.md` before writing one line** of a
tool's name, docstring or result (DEC-200). A tool's text is the model's
routing surface on every call, and 0007's rules, which had no test, were not
met even on the day they were written. The **`nora-tool`** skill is the
checklist with each rule's check; 0015 §6 says which rule is enforced how.
The name is `<domain>_<verb>` from the verb list in
`tests/architecture/test_tool_names.py`, and a tool is never renamed without a
`tool_selection` measurement (0004). Put the before/after routing numbers in
the PR.

## Adding or changing a skill

Two different things share the word. **Nora's runtime skills** (`nora/skills/`)
follow **`docs/adr/0016-skill-policy.md`** (proposed): placement by reach, then
cost, read from Langfuse and put in the PR; the **`nora-runtime-skill`** skill
is the checklist, and `tests/test_skill_content.py` enforces the shape.
**Agent skills** (`.agents/skills/`, the procedures above) follow ADR 0017 and
`tests/architecture/test_agent_skills.py`; a new one adds its row to the
[Skills](#skills-the-procedures) table and its `.claude/skills/<name>` link in
the same commit.

## Never (one line each — the war-story behind each is in `docs/CONVENTIONS.md`)

- **Never `pip install`** — dependencies via `uv add` (operational constraint 5).
- **Never write a test whose fake answers every request the same way** — make the
  fake behave like the real server, including its wrong-call shapes. If reverting
  the code under test doesn't turn the test red, the test is decorative.
- **Never point signal-cli at a different account, re-register it, or unlink its
  device** without an explicit decision — that store holds the device
  registration. Routine reads/RPC are ordinary; destructive account ops are not.
- **Never reference the archived legacy path** (`~/.hermes/profiles/nora/`).
- **Never assert a per-tag object was cleaned up by checking an attribute was
  deleted** — count live `gc`-tracked objects instead.
- **Never rewrite a model's instruction before reading its input** — if the
  judgement turns on *when*, check the block carries a time; on *who*, an
  index; on *still open*, the thing that would close it. Three instruction
  rewrites in one night changed nothing or made it worse; rendering an age per
  transcript row cut the same gate's error rate by two thirds.
- **Never build a model prompt outside context assembly** — a `_BLOCKS` table walked by
  `assemble`; no debt ledger, no exception for an occasion (DEC-273, ADR 0021).
  `tests/architecture/test_every_model_call_is_a_declared_occasion.py` fails on one;
  a new occasion follows `design/context-assembly/04-every-occasion.md` «How a new occasion joins».
- **Never paste production rows into a fixture** — not a slice, not "just six
  people", not with a docstring saying it is real. Fixtures are GENERATED, or
  placeholders from `identity.example.toml`; if a synthetic corpus cannot
  reproduce the property under test, that is a finding about the property.
  Deleting a file does not delete its object: a fixture of real GPS fixes stayed
  reachable from `master` for three weeks after its removal
  (`docs/CONVENTIONS.md` has the story). **The `pre-commit`, `commit-msg` and
  `pre-push` hooks enforce it** (#283, `.githooks/README.md`): a known real
  value, a phone/id/email shape or a coordinate table that a change ADDS is
  refused, fail-closed, against a private denylist outside the repo. It needs
  `make hooks` and the denylist on your machine; it stops a pasted corpus, not
  a disguised one, so the rule stays yours too. It is deterministic: no model
  judges a finding. A denylist hit has no way around it; a shape finding
  clears only by a pushed declaration, the private pseudonym registry, or a
  human's `NORA_PII_ACK=<id>` after LOOKING at the value. Never acknowledge a
  value yourself: replace it with a generated one, or ask your human.
- **Never read an instrument's output as evidence without first checking it can
  measure the thing** — state what a positive result would look like before
  concluding from a null one.
- **Never claim a merge, deploy or fix without reading back the state you claim**
  — `git log -1`, `docker exec … import config`, the metric endpoint. Every time.
  Pointed at a test suite instead of a deployment, this is the mutation rule: a
  fix is done when reverting the exact production line turns a NAMED test red,
  not when the gate is green. `CONTRIBUTING.md` «Proving a fix» carries it, with
  the two harness rules that otherwise turn "caught" into a lie.
- **Never read a gate's success through a pipe.** `make gate | tail` returns
  `tail`'s exit code, and a backgrounded `cmd; echo done` reports `echo`'s:
  write the log, capture `RC=$?`, then read the log. Four incidents before it
  was written down; one merged a red `master`.
- **Never cite a file, test or function you have not opened** in this session.
  A peer saying it will write one is not an opening, and finding a definition
  is not finding a caller: grep for the call before repeating that it works.
- **Never edit, commit, or merge on `master` in this checkout** — every change,
  including a one-line doc fix, starts in a worktree on its own branch and
  reaches `master` by merge, so the merge is the moment somebody decides rather
  than a side effect of editing. **The bind-mount source is `nora-host`'s own
  checkout (`~/workspace/nora`), not this one** — read back 2026-09-08: the
  compose bind sources are relative to the compose file on the host, so a merge
  here reaches the container only when `nora-host` pulls. An older topology, in
  which the dev checkout WAS the bind-mount source, is what the previous
  wording described; it is superseded, and it mattered in both directions — it
  made one session sequence a deploy around a window that did not exist, and
  read while standing on `nora-host` it points at the wrong machine. What is
  unchanged: on `nora-host`'s checkout an edit to `nora/doctrine` IS a deploy (it
  hot-reloads on mtime), and `config.toml` there is live once its services are recreated.
  **`nora/skills` is NOT** — it is `COPY`d into the image (`Dockerfile`) and the
  compose file binds only `nora/doctrine` and `nora/dreamer`, so a skill edit
  needs a rebuild. Editing one on the host the way you would edit doctrine
  deploys nothing and reports nothing.
  **Landing a merge does not move you back
  in** — the next change starts in a new worktree too. 2026-08-21: a session
  merged, kept editing in place, and another session was seconds from building
  an image out of a mid-refactor tree with 16 tests red in it.
- **Never run two agents against this checkout at once** — sequence them, or give
  each a worktree.
- **Never trust `alembic revision --autogenerate` for `idx_messages_conv_ts`** —
  it is excluded on purpose; a revision touching it is hand-written.
- **Never assume your migration's parent is unclaimed** — two branches cut from
  the same head each write `down_revision = <that head>`, git merges both files
  without a conflict, every test that does not actually migrate stays green, and
  the container fails at boot because `upgrade "head"` refuses two heads.
  **`uv run alembic heads` must print exactly ONE line after every merge.**
- **Never take a DEC or R number from a chat agreement** — re-read
  `design/09-decision-index.md` at MERGE time, not at claim time. 2026-08-21:
  two sessions coordinated numbers carefully and a third took DEC-131 anyway;
  it conflicted only because the rows landed on the same line. Check with
  `grep -oE '^\| DEC-[0-9]+ \|' design/09-decision-index.md | grep -oE '[0-9]+' |
  sort -n | uniq -d` (and the same with `R-`) — note `uniq -d` exits 0 with no
  output, so test the string, not the exit code. **The trailing `\|` is
  load-bearing**: sub-lettered rules (`R-6b`, `R-10c`) otherwise read as their
  parent number and the check reports 8 duplicates that are not. 2026-08-23: a
  REAL duplicate — two rows numbered R-29 — sat on master for 15 days, reported
  and backlogged by the session that found it, because the check that should
  have caught it cried wolf seven times. A noisy gate is a disabled gate.

## Instruments and spans (one line each — war-stories in `docs/CONVENTIONS.md`)

**`docs/observability.md` is the map**: what the three signals are for, how a
span physically reaches a collector (there are TWO filters, and only the first
is visible from this repo), how to instrument a model call, and how to read any
of it back. Read it before adding an instrument. The catalogue of instances is
`design/dreamer-agent-loop/02-instrument-blindness.md`; these are the rules it
earned. They are the `measure` verb pointed at our own instruments.

- **One root span per entry point, and stages are its CHILDREN.** Sibling spans
  with no root are not one trace with gaps — they are N traces.
- **Langfuse gets the traces with a GENERATION in them; everything else is
  Prometheus'.** A root declares `nora.trace_kind`; plumbing is dropped at the
  sampler and its outcome ships as a counter label instead. The rule is about
  ROOTS — a stage span with no model call is exactly what makes an early exit
  legible under a root that has one.
- **Spawned work does not inherit the caller's span.** `create_task` copies the
  context of the task that CALLS it, so work handed to a worker starts its own
  trace unless the intended context is captured and attached explicitly.
- **One spelling for "this span is a model call" — and it is Strands'
  own, not this repo's, since 2026-09-19.** An Agent-backed call
  (`nora.agent.structured.ask_text`/`ask_structured`) needs no
  instrumentation code at all; a raw `chat.completions.create` call
  instruments through `nora.agent.telemetry.start_raw_model_span`/
  `end_raw_model_span`. A call site never writes a `gen_ai.*` or
  `langfuse.observation.*` literal by hand, and never wraps an
  `ask_text`/`ask_structured` call in a span of its own — the Agent already
  opens one, and a second span around it is two overlapping traces for one
  call (the debrief/standing fold's own incident: `4448c737` moved them onto
  the SDK, nothing removed the hand-rolled wrapper still around the call, and
  every fold produced two nested traces for months).
- **Detaching a span and dropping it are two decisions.** Spawned work should
  detach so it does not hold a trace open; that is not a reason to declare it
  plumbing. Conflating them hid every media read from the collector for two
  weeks while its counter recorded them.
- **A log line is not a metric.** If the answer is "how many, how often, with
  what outcome" it is a counter; a line that restates one every interval is a
  third copy. 78% of the deployment's log was two such sources.
- **Severity is a NUMBER as well as a word** — `level` sorts alphabetically, so
  "at least a warning" is not expressible over it. `severity_number` carries
  OTel's SeverityNumber bands; an unmapped level gets no field, never a guess.
- **A span with only a name is worse than no span** — carry what makes the step
  re-derivable. Empty traces read as progress; an absence at least asks.
- **Instruments measure the RESULT; something must measure the COST.** An index
  is used, a call is bounded, a read is scoped: check the PLAN or the bound.
  Never the answer — a correct answer is compatible with any cost.
- **R-27's three states on every instrument: `ok` / `nothing_found` /
  `unavailable`.** A wiring gap is `unavailable` — never `nothing_found`, never
  `ok`, and never two of them on one event.
- **A declared metric with no call site is a lie** — it renders as a series that
  is genuinely always zero. Ship the name with its increment, or not yet.
- **And an increment with no declaration is worse** — `Metrics.inc` raises
  `KeyError`, which the caller's own `except Exception` then reports as the
  failure of whatever it wrapped. `nora_peer_card_empty_total` spent a
  deployment turning every genuinely empty card into a fake transport error.
  `tests/architecture/test_every_recorded_metric_is_declared.py` is the gate;
  a call site whose name it cannot read statically must be PINNED there, never
  skipped.
- **Label vocabularies are DERIVED from the code, never hand-copied.** One named
  constant the code increments against and the help text reads from: `get_args`
  off a `Literal`, plus a RUNTIME check wherever `make gate`'s mypy does not
  reach (issue #61) — a static-only guard in an unchecked package is a claim.
- **Pair every zero-assertion with a positive control.** `== 0` is also true of
  a stage that crashed; assert something only a run that RAN produces.

## The five operational constraints (one line each — full text in `docs/CONVENTIONS.md`)

1. Container builds require `docker build --network=host` (BuildKit's bridge can't
   resolve `deb.debian.org` on this host).
2. Honcho health uses **`GET /health`**, not `GET /v3/workspaces` (POST-only → 405).
3. **signal-cli is Nora's transport, in her compose project.**
   **`--receive-mode=on-start` is load-bearing — never drop it** (without it the
   websocket to Signal never opens and every message is silently dropped). Needs
   **Java 25**.
4. The legacy Hermes gateway is retired. If migrating an old installation,
   archive its profile outside the checkout; do not reuse the old profile path.
5. Dependencies via **`uv add`**, never raw pip.

## Conventions (short — full text in `docs/CONVENTIONS.md`)

- **One stage, one module, one public entry point** — about RESPONSIBILITIES, not
  lines (a 400-line module doing one thing is fine; count what it is responsible
  for).
- Test-seam policy: production classes take collaborators through `__init__` only
  — no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons.
- A port never gains a second implementation — including a test fake — without a
  shared contract suite both pass (`tests/contract/`).
- **A contract suite scopes its database to the MODULE, not the test.** The
  shared `pg_url` fixture is function-scoped: `CREATE DATABASE` + the full
  migration chain + `DROP`, every test. At ~30 database-backed tests that
  degrades the container enough to fail an unrelated wall-clock test in another
  directory — the cost lands nowhere near its cause. Build once per file and
  `TRUNCATE` between tests; the isolation these suites need is "no rows from
  the last test", not "no schema from the last test".
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test in `tests/test_acceptance_rules.py::RULE_TESTS`.
- **A test directory names what makes the test TRUE, not what it is about**
  (`tests/README.md`): `unit/` no I/O, `contract/` one suite N implementations,
  `integration/` the wired pipeline, `architecture/` properties of the source.
  The `tests/` root is legacy and takes no NEW files.
- **A test gets its config from the template** — the `example_config_path` /
  `example_config` fixtures — never a path to the repo's `config.toml`. Only a
  test whose SUBJECT is the deployment reads the real file, pinned in
  `tests/architecture/test_config_comes_from_the_template.py`.
- **A fake standing in for a port lives in `nora/testing/`** and is
  parametrized into that port's contract suite. Inline classes are local
  scaffolding only; `tests/architecture/test_port_fakes_live_in_nora_testing.py`
  is the gate and its `_ALLOWED` list only shrinks. Issue #113 for all three.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Do not enumerate, preload, or search wikis at task start. Use retrieval when the user asks for it, when unfamiliar architecture or dependency behavior materially affects the task, or when source inspection leaves an important uncertainty. Stop once the question is grounded.
- When those conditions apply and OpenWiki retrieval tools are available, use `openwiki_search` for just-in-time context and `openwiki_read` for the relevant complete sections. If search returns `workspace_required`, ask which listed workspace to use and retry with its ID.
- Use `openwiki_list_workspaces` or `openwiki_list_wikis` when workspace membership itself needs to be discovered.
- If the retrieval tools are unavailable, read `openwiki/quickstart.md` and follow its links to the relevant pages.
- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
