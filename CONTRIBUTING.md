# Contributing to Nora

Nora is an experimental project maintained by [mikeng-io](https://github.com/mikeng-io).
Bug reports, focused proposals and pull requests are welcome. For a substantial
change, discuss scope in an issue first. Maintenance and review are best effort;
interfaces and requirements can change.

Follow the [Code of Conduct](CODE_OF_CONDUCT.md). Keep credentials, personal
messages, account identifiers, location histories and unredacted screenshots out
of contributions. Use synthetic reproductions. Report vulnerabilities through
[Security](SECURITY.md), rather than public issues.

## Setup

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). Fork the repository if you
do not have write access, then clone your fork and work in a separate worktree:

```bash
git clone https://github.com/<your-account>/nora.git
cd nora
git remote add upstream https://github.com/mikeng-io/nora.git
git fetch upstream
git worktree add -b docs/example-change ../nora-example upstream/master
cd ../nora-example
uv sync --frozen --extra evals
make hooks
```

Database and object-store tests need Docker. Live model evaluations require
operator-owned services and credentials; ordinary contributions use synthetic
data and do not need production access. See the [README](README.md#development)
for individual development checks.

## Privacy hook

`make hooks` installs the local hooks for this clone, including linked worktrees.
The PII pre-commit hook scans staged additions; commit messages and unpushed Git
objects are also checked. A finding or an inability to scan blocks the operation.
Keep the hooks enabled.

The private denylist is never distributed with the repository. If your machine
has no access to it, set a non-empty reason in the shell where you commit and push:

```bash
export NORA_PII_DENYLIST_ABSENT='Outside contributor without access to the private denylist'
```

This permits scanning without the denylist and prints the reason on every run.
Only shape rules apply: they detect patterns such as phones, emails and account
IDs, but cannot identify known private names. An unreadable denylist remains an
error. The opt-out does not suppress findings or authorize private data. See the
[hook guide](.githooks/README.md) for exact rules and declarations.

## Branch naming

We use trunk-based development (GitHub Flow) plus GitOps: short-lived branches
reach `master` through pull requests; Git records deployment desired state.
There is no `develop` branch. Use **`<type>/<kebab-slug>`** for maintainers, agents
and fork contributors alike, with a type from the commit table below, for example
`docs/public-readiness` or `fix/message-delivery`. Describe the change in the slug.
Every change starts in its own worktree and branch.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): description` (scope is optional). This type table is closed;
adding a type requires updating both this table and the `TYPES` map in
`scripts/check_commit_subject.py`, which enforces it.

| type | for |
|---|---|
| `feat` | new capability |
| `fix` | defect repair |
| `docs` | documentation |
| `test` | tests |
| `refactor` | restructuring with no behaviour change |
| `chore` | tooling, hygiene, dependencies |
| `change` | a deliberate behaviour change that is neither feature nor fix |
| `merge` | the merge commit for a branch |
| `design` | design-bundle / architecture-document work |
| `config` | configuration surface — schema, example files, loader |
| `build` | build/deploy tooling — Dockerfile, compose, Makefile |
| `style` | formatting or lint with no behaviour change |
| `ci` | CI / GitHub plumbing |
| `ops` | operational toggles on the live stack (policy, config reload) |
| `doctrine` | Nora's doctrine files — voice / conduct / silence / constraints |
| `perf` | a change that only affects performance (standard Conventional Commits) |
| `security` | a security-relevant fix |


Write a specific description and a body explaining what changed and why, with
measurements where relevant. Hooks reject missing or undeclared types, `wip:`
and default merge subjects. User-visible subjects become release notes.

## Pull requests

Target `master`. Use the [PR template](.github/pull_request_template.md), with a
Conventional Commit title and these headings in order:

- **What**: concrete changes and supporting artifacts, one block per piece.
- **Also**: findings left outside the change; omit when empty.
- **Gate on the merged tree**: the actual outcome and exit code.
- **Closes**: `Closes #N` only when the PR fully resolves it; otherwise reference
  `issue #N` in the prose. Omit when there is no issue.

Run **`make gate` once on the final head**, including the latest target branch.
It runs full static checks and tests affected by the diff against `origin/master`;
unknown changes or selector failures run the whole suite. For a fork, fetch
upstream master into `origin/master` before running it (keep `origin` as your fork):
`git fetch upstream master:refs/remotes/origin/master`.
During iteration, run the tests you touched. `make full-gate` is the unconditional
full run. Report failures explicitly; a pre-existing failure is not a pass.
The contribution-content workflow checks populated headings and their order;
it does not verify the truth of a claimed result.

## Issues

Use **Bug report** for a reproducible problem: include expected and actual
behaviour, revision, environment and a minimal synthetic reproduction.
Use **Feature request** for an improvement: explain the problem and proposed
solution. Both are available in the [issue chooser](https://github.com/mikeng-io/nora/issues/new/choose).
Blank issues are disabled. The forms marked **Maintainers** support internal
planning and require no action from ordinary contributors. For a private channel,
follow [Security](SECURITY.md#reporting-a-vulnerability).

Maintainer procedures, including merge ownership, mutation proof, numbering and
issue lifecycle, live in [maintainer conventions](docs/maintainers/conventions.md).
Coding agents also follow [AGENTS.md](AGENTS.md).
