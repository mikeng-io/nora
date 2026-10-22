# 0028 · Pull requests are labelled and assigned by a workflow, and a visible commit has a body

- **Status:** proposed (2026-10-22), on operator direction: PRs had no labels and no
  assignees, and "take a look at how other open source projects" define PR templates and
  commit requirements
- **Date:** 2026-10-22
- **Extends:** ADR 0027 (the merge rules are rulesets), whose required `pr-metadata` job now
  also judges commits
- **Index:** none: a process convention, enforced by the tests named in Decision

## Context

Read from the repository and from the projects it was compared with, 2026-10-22:

- **Nothing set a label or an assignee.** Titles, body sections and branch names were checked
  (ADR 0027); the PRs still carried no label and no assignee, so nothing could be filtered
  by kind or area and nothing said whose PR it was.
- **Established projects automate this and never rely on the author.** Kubernetes requires a
  `/kind` label and a `release-note` block and sets labels and reviewers through bot
  commands; Rust's triagebot assigns reviewers from the changed paths; Home Assistant's
  template asks for the change type and a checklist; Angular requires a body of at least 20
  characters (docs excepted) on a `type(scope): summary` commit; Kubernetes' template also
  asks whether AI was used.
- **This repository's commits already meet the Angular body rule.** Of 489 non-merge commits
  of the visible types since 2026-10-01, 456 (93%) had a body of 20 or more characters, and
  none ended its subject with a period. A rule here costs little.
- **A Dependabot-triggered job has a read-only token**, so a workflow cannot label or assign
  its PRs; only `.github/dependabot.yml` can.
- **`--no-verify` skips every local hook.** The commit rules were enforced only there.

## Decision

1. **`scripts/triage_pr.py`, run by `.github/workflows/pr-triage.yml`, labels and assigns.** On
   `pull_request_target`, from default-branch code and API data only, it sets `type: <t>`
   from the Conventional Commit title, `area: <a>` from the changed paths, and `breaking`
   from `!`, removes managed labels that no longer apply, leaves every other label alone,
   creates missing labels, and assigns the author when the author is a person. Bot PRs are
   skipped. `.github/dependabot.yml` gives Dependabot's PRs the maintainer as assignee and
   `dependencies`, `type: build` and `area: deps`.
2. **A `feat`, `fix`, `security`, `perf`, `change` or `doctrine` commit needs a body of at
   least 20 characters**, trailers and git comments excluded. Merge, revert and hidden types
   do not. `scripts/check_commit_subject.py` judges it in the commit-msg and pre-push hooks.
3. **The `pr-metadata` job judges every commit of a PR** by the same function, so a
   `--no-verify` commit is still refused. GitHub's own "Update branch" merge subject is the
   one default merge subject allowed, because no local hook can see it.
4. **The PR template gains a required `Authored by` section**: human or agent, the tool and
   model for an agent, and who reviewed it.
5. **The CodeQL job is removed.** It was skipped on every PR while private and showed an
   unexpanded `${{ matrix.language }}` name; Aikido scans the code.

## Consequences

- Every PR is filterable by kind and area, and has an owner, without anyone remembering.
- A `fix` commit with no explanation cannot be merged, including one made with `--no-verify`.
- The record shows which PRs were made by an agent and which model reviewed them.

**Worse:**

- **The label set is one more list to keep right.** A new commit type needs a colour in
  `scripts/triage_pr.py`; a test fails until it has one. Area rules are path prefixes and
  will drift as directories move.
- **A PR whose old commits lack bodies must be rewritten** to merge. That is the intended
  pressure, and also a rebase of reviewed history.
- **Dependabot's labels are fixed text.** If a label is renamed, Dependabot drops it silently;
  a test ties `dependabot.yml` to the label list.
- **The triage job needs write permission on `pull_request_target`.** It never checks out
  or runs PR code, and PR data only selects from a fixed list, but it is a privileged job.
- **`Authored by` is the author's word.** Nothing verifies it, as with the gate line.

## Rejected

- **Requiring a label or an assignee from the author.** Peers that tried it moved to bots.
- **Squash-merge so the PR title is the commit.** The repository merges with authored
  `merge:` commits and keeps branch history (`CONTRIBUTING.md`).
- **A third-party labeler or semantic-PR action.** The repository already validates titles,
  and an Actions allow-list keeps external actions to the two named ones.
- **Auto-merging Dependabot PRs.** The fast CI is not the full suite (`github.md`).
