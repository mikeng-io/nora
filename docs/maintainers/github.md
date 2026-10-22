# GitHub maintenance

Nora uses GitHub Free features for a public repository: standard Ubuntu-hosted
Actions runners, CodeQL, dependency review, Dependabot alerts/security updates,
secret scanning/push protection, private vulnerability reports, branch protection
and tag rules. No paid Advanced Security subscription, larger runner, external
scanner subscription or deployment runner is required. Standard public runner
minutes are free; Actions artifacts/cache and larger runners have separate billing.
These workflows upload no custom artifacts or caches. GitGuardian is an existing
optional integration, not a required paid dependency.

References: [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[repository security](https://docs.github.com/en/code-security/getting-started/quickstart-for-securing-your-repository),
[dependency review](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-review).

## Activation after publication

Publishing is a separate owner action. This command refuses private repositories
and never changes visibility, enables a paid license or changes billing:

```bash
python3 scripts/configure_public_github.py
python3 scripts/configure_public_github.py --apply
```

Run it with an owner/admin `gh` login, after these workflows are merged. It enables
native secret scanning/push protection, Dependabot security updates and private
vulnerability reporting; restricts Actions to GitHub-owned actions plus the two
named external actions; and keeps workflow tokens read-only by default. The merge
rules (required checks, no force-push, immutable release tags) are separate and
work while private: see Merge rules below. The check names are bound to the GitHub
Actions App, rather than arbitrary status producers. The release App has no
branch-rule bypass.

CodeQL and dependency review deliberately skip while private, avoiding paid
private-repository security features. After publication, manually dispatch Security
and open a small PR to confirm those checks report. The existing
fast CI still runs while private. It checks static contracts and selected tests;
it does not run the complete suite. Run the local `make gate` once on the final head before landing code.

Native scanning detects supported credential patterns. The completed private PII
scrub and local identifier gate remain separate; never upload the private denylist
to Actions. Keep outside PRs on hosted runners, with no deployment credentials or
host access. In Actions settings, require approval for **all outside collaborators**
before their workflows run; never give fork PR workflows write tokens or secrets.

## Automatic releases

Install a **free GitHub App**, restricted to this repository. Give it repository
Contents: read/write, Pull requests: read/write and Administration: read-only.
The last permission lets the release job verify branch protection before queuing
a merge; it cannot change that protection. Store its App ID as Actions variable
`RELEASE_APP_ID` and its PEM private key as Actions secret `RELEASE_APP_PRIVATE_KEY`.
Use GitHub Settings or `gh secret set` interactively; never paste the key into an
issue, chat, tracked file or workflow log. No personal token is needed.

Also pin the App bot's numeric GitHub **user** ID as Actions variable
`RELEASE_APP_BOT_USER_ID` for contribution metadata checks. Verify the App ID
and slug in its owner settings, then read `gh api 'users/APP-SLUG[bot]' --jq .id`.
This is a separate identifier from the App ID. The metadata job checks the live
PR author's user ID; it receives no App key or installation token. Private Apps
cannot be discovered through `/apps/{slug}` using that job's repository token.

`scripts/release_plan.py` creates/updates the version PR from the commits reachable from
`master` and not from the last tag (ADR 0027); the release-please action only tags and
publishes it once it is merged. The App queues that PR for a real merge commit; required
checks must pass before GitHub merges it, and queuing is refused unless the `master` ruleset
requires `checks`. The resulting master run publishes the tag and GitHub Release. Installing
this App does not permit deployment. Without the App, the existing manual release-PR workflow
continues and prints a setup notice.

Only `feat`, `fix`, `security`, `perf`, `change`, `doctrine` and `revert` commits
with changes to runtime inputs qualify for creating/updating a release PR.
Documentation, design, tests, CI and housekeeping alone do not. Runtime doctrine
and runtime skills count because they change Nora's behavior. The release scope
check reads actual changed paths since the preceding release, not just the most
recent push; a qualifying pending change can therefore still release alongside a
later documentation commit. Conventional Commit subjects determine semantic
version bumps. Documentation/design directories are also excluded from release
notes, even if a commit was accidentally given a visible type. Use `docs`/`design`
for all such commits, including root Markdown files.

The App is required for unattended PR checks: PRs created with `GITHUB_TOKEN`
can await workflow approval, and its pushes normally do not trigger another
workflow. [GitHub token behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## Merge rules

On a private repository these need GitHub Pro (or Team for an organisation); on a public one
they are free. Run with an owner/admin `gh` login:

```bash
python3 scripts/configure_github_rules.py            # show the policy
python3 scripts/configure_github_rules.py --apply    # write it, then read it all back
```

`master` needs a pull request, merge commits only, resolved conversations, an up-to-date branch
and the `checks` and `pr-metadata` jobs (the second runs the PR content check and fails on a bad
title, body or branch); force-push and deletion are blocked, and `v*` tags
cannot be moved or deleted. There are no bypass actors, so the owner cannot merge a failing PR
either; the way out is editing the ruleset in the repository settings. Only checks that report
on every pull request may be required: CodeQL and dependency review are skipped while private
and the wiki build is path-filtered, so none of them is. Add Aikido's PR check here only after
a PR shows the name it reports under.

## Deployment documentation

`nora-host` is a placeholder SSH alias defined by each operator; all paths and
access details are installation-specific. Public deployment documentation describes
the portable Compose procedure. Keep actual host inventory, account mappings,
network addresses and private installation notes outside the repository. The optional [Komodo release deployment](../../deploy/komodo/README.md) publishes
images and Git desired state without connecting Actions to a deployment host.
It requires `RELEASE_IMAGES_ENABLED=true` plus a separately installed and enabled
Komodo Stack/Procedure. Both activation and host readback are separate from release
publication; merging the implementation does not turn automatic deployment on.

## Contribution content checks

`Contribution content` validates PR/issue metadata with default-branch code. The
required `PR content` check is published on the actual PR head, including after
title/body edits. Branch protection requires it after running the owner setup
command above. Issue completeness and spelling/style suggestions are advisory.
These use standard hosted runners and an offline spelling dictionary, with no
paid service, model calls or production secrets. See CONTRIBUTING.md for the rules.

For read-back or repair, dispatch the workflow with `kind=pr` or `kind=issue` and
the contribution number. This workflow must first exist on master; its initial
implementation PR is tested by the ordinary CI before merge, then dispatched to
verify the live check after merge. Keep manual dispatch on master.

## Publication readiness

The private PII scrub is complete. Keep its private records outside GitHub.
Local commit/push hooks guard new additions; their results are separate from
GitHub's native credential scanning.

Before publication, check open Dependabot alerts against the current lockfiles.
The critical PyJWT PEM-detection advisory (GHSA-ffc3-869f-jxw9) is fixed in
2.14.0 and later. The critical node-tar parse/decompression advisory
(GHSA-23hp-3jrh-7fpw) is fixed in 7.5.19 and later. Nora pins patched releases;
the voice packages override only node-pre-gyp's tar dependency until its upstream
range accepts patched tar. Validate the native installer when changing it.
A passing dependency check does not establish live exploitability or that every
lower-severity advisory has been resolved.

Run the activation command immediately after the owner changes visibility, then
read back the settings and run the Security workflow. Complete the release App
setup above before expecting unattended releases. The activation script refuses
private repositories and does not create an App or its private key. While private
on GitHub Free, branch protection cannot be enforced; publication or an eligible
paid plan is an owner decision.

The issue chooser offers Bug report and Feature request forms, the internal
freeform/design-anchored forms, and a SECURITY.md link for private disclosures.

Dependabot security updates are grouped per ecosystem across the manifest
directories in `.github/dependabot.yml`. Newly configured ecosystems have routine
version-update PRs disabled; security updates remain enabled and are not delayed
by the monthly version-update schedule. The repository's grouped-security setting
also covers manifests outside explicit groups. GitHub cannot combine different
security-update ecosystems into one Dependabot PR; an integration PR can land
several reviewed groups together when needed.
