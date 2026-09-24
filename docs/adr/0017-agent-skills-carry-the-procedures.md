# 0017 · Agent skills carry the procedures; AGENTS.md carries the rules

- **Status:** proposed (the operator asked for the audit and the skills on
  2026-09-24; accepting this ADR is the operator's call)
- **Date:** 2026-09-24
- **Index:** none. Like [0010](0010-agents-md-is-the-entry-point.md), this is a
  governance decision about how agents work on the repo, not about Nora's
  runtime behaviour, so it earns no DEC row.
- **Extends:** 0010 (one entry point). **Not to be confused with**
  [0016](0016-skill-policy.md), which governs Nora's OWN runtime skills in
  `nora/skills/`. This ADR governs the skills of the agents that build her.

## Context

On 2026-09-24 the operator asked for the conventions to be audited against
practice, and for dedicated skills for implementing Nora. Eight read-only
audits measured: all 478 PR numbers, the history of every governance file,
116 lessons in the operator's agent memory, four sessions running that day
(about 220 operator turns, 2,642 shell commands), and 591 past sessions
(1,059 operator messages; 55,767 tool calls across 999 transcripts).

What they found, with the numbers that decided this:

- **The rules were written and not reached when they applied.** The
  operator's most direct statement of it (2026-09-23): conventions and ADRs
  are often not followed when writing tools. AGENTS.md linked neither ADR
  0015 nor the eval SOP nor the runbook at the moment those tasks start.
- **Procedures lived in the always-read file, and rotted there.** AGENTS.md
  (593 lines) described strict mypy over 5 packages a week after it became
  13, cited a privacy gate deleted eight days earlier, and carried deploy,
  tool-writing, fragment and numbering procedures that each also lived in a
  second document. `docs/eval-sop.md` taught about 100 lines of commands for
  a harness its own header says no longer exists.
- **A rule with no mechanism at the moment of action drifts.** Of the 136
  PRs merged from #321 to #478 (2026-09-17 to 09-24), 98 landed with GitHub's
  default merge message (the audit read each PR's `mergeCommit`) against CONTRIBUTING.md's
  rule, because `gh pr merge --merge` writes the default unless `--subject`
  is given. The retired `Summary` and `Test plan` headings still appeared in
  11 PRs each. The commit-subject rule, which a hook enforces, held on 598 of
  599 commits.
- **Operating lessons lived only in one machine's memory.** Of 84 lessons
  about the repo or its infrastructure, 16 were fully in the repo's
  documents and 51 were absent: gate exit codes read through a pipe, the
  deploy host collecting unpushed commits, concurrent deploys leaving orphan
  containers, eval runs that another session owned. The eval-ownership
  correction was made 8 times across 6 sessions in 8 days.
- **Agents re-derived the same procedures.** A worktree-isolated agent spent
  15 commands rediscovering that git is refused under one tool's hook; "the
  next free DEC number" was looked up in more than five different ad hoc
  ways (326 calls); the release fragment directory was guessed wrong
  repeatedly. Only 18 skill invocations appear in the whole corpus, and none
  was specific to this repo.

## Decision

1. **Procedures are agent skills** in `.agents/skills/<name>/SKILL.md`, in the
   Agent Skills format (agentskills.io): `name` equal to the directory, a
   `description` that says what the skill does and when to load it. Eight
   ship with this ADR: `nora-land`, `nora-deploy`, `nora-readback`,
   `nora-eval`, `nora-prove-fix`, `nora-decision`, `nora-tool`,
   `nora-runtime-skill`.
2. **One copy.** `.agents/skills` is the location other tools read; each
   `.claude/skills/<name>` is a symlink to `../../.agents/skills/<name>`, so
   Claude Code reads the same files. Per skill rather than one directory link:
   the deploy host's checkout already held an untracked personal skill under
   `.claude/skills/`, and a tracked link in that directory's place would make
   every later `git pull` there refuse. A new skill adds its link in the same
   commit.
3. **A skill is the order of the commands and the check after each.** It
   links the document that holds the reason and never restates it (0010's
   argument: two copies drift). A pitfall is one line with its evidence.
4. **AGENTS.md keeps the rules and names the skills.** Its skills table maps
   a task to the skill to load before the first edit. Procedure text that a
   skill now carries leaves AGENTS.md, which keeps the rule and a pointer.
5. **The table and the directory are held in step by a test,**
   `tests/architecture/test_agent_skills.py`: shape, one copy, listed both
   ways. The doc-reference gate already scans every tracked `.md`, so a path
   a skill names must resolve.
6. **Lessons that apply to any agent move from memory into the repo.** The
   cross-cutting ones become AGENTS.md rules (proceed unless blocked; never
   read a gate through a pipe; never cite a file you have not opened); the
   procedural ones go into the skill that runs that procedure.

## Rejected

- **Keep the procedures in AGENTS.md.** That is where they rotted: always
  read, so every stale line is read too, and each also lived in a second
  document that drifted from it.
- **One `.claude/skills` symlink to the whole directory.** Simpler, and it
  breaks `git pull` on any checkout that already holds a personal skill there
  (the deploy host did).
- **Claude-only skills in `.claude/skills/`.** The operator works with more
  than one agent tool; `.agents/skills` is the location the others read.
- **Put the missing lessons into more AGENTS.md rules instead.** The corrections
  that recurred most were already written somewhere; the gap was the moment
  of reading, not the absence of text.

## Consequences

**Better.** The procedure is in front of the agent when the task starts,
from any tool that reads `.agents/skills`. A stale command now has one home
to fix. The merge-message and PR-heading rules get a mechanism at the moment
of action (the exact `gh pr merge --subject` command and the template
headings in `nora-land`). The operator's memory stops being the only place
half the working conventions exist.

**Worse.** Eight more files to keep current, and a skill that goes stale is
worse than none because it is trusted. The test holds the shape and the
listing, not the truth of the commands; only the doc-reference gate checks
paths, and nothing checks that a command still works. A skill that is not
loaded does not apply, and loading depends on the tool matching the
description to the task, which this repo cannot measure for tools it does
not run. Some knowledge is now split between a rule in AGENTS.md and a step
in a skill, and a reader of one may miss the other.

**The reading that would reopen it.** If sessions still re-derive the
procedures a skill covers (the same shell-command families and retries the
audit counted), or skills are loaded rarely enough that the rules they carry
are missed, the procedures return to AGENTS.md and the skills are deleted.
