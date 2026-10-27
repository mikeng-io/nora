# GitHub maintenance

Nora uses GitHub Free features for a public repository: standard Ubuntu-hosted
Actions runners, dependency review, Dependabot alerts/security updates,
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

Dependency review deliberately skips while private, avoiding paid
private-repository security features. After publication, open a small PR to confirm
it reports. There is no CodeQL job: it was skipped on every PR while private and
Aikido scans the code. The existing
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
on every pull request may be required: dependency review is skipped while private
and the wiki build is path-filtered, so neither is. Add Aikido's PR check here only after
a PR shows the name it reports under.

## Dependabot

Security updates for Python, npm, Go and the base images, and monthly Actions and base-image
version updates, are proposed by `.github/dependabot.yml`. Every PR is assigned to the
maintainer and labelled by that file, because a Dependabot-triggered job cannot label. The
security-only ecosystems commit as `security(deps)`, a user-visible type, so a patched
dependency cuts a release by itself; Actions bumps stay `build(deps)`. Every `FROM` is pinned
to a multi-arch digest and its directory must be listed under the `docker` ecosystem (a test
enforces both). `apt` packages are still fetched live at build time.

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

## Engineering admission pilot

`Engineering dispatch` runs only trusted `master` source, using API JSON. PR
ready/synchronize/reopen events are read-only review intake; completed CI events
are notifications requiring explicit owner admission. Neither launches paid work.
Valid notifications return `nothing_found` with rc 0 without querying task
eligibility; malformed/unsupported events and failed manual measurements remain
`unavailable` with rc 1. A notification is not an admission or review result.
There is no issue-comment command, Project event trigger, PR-code execution,
CLI integration, board writer, auto-enrollment, approval, merge or Done action.
GitHub Issues remain canonical and Project 8 is their scheduling view.

All flags default off when absent. After independent review, the parent owns
activation: `ENGINEERING_REPOSITORY=mikeng-io/nora`, the configured secret
`DEVIN_API_KEY`, variable `DEVIN_ORG_ID`, and `ENGINEERING_CI_ENABLED=true` for
session spending. No Project PAT or copied GitHub credential is needed. Actions
uses its ordinary `GH_TOKEN` for repository facts and native linked-issue reads;
it does not read the private Project. Also configure
`DEVIN_SECURITY_PROFILE_ID` for a dedicated organization-owned profile, created
by the parent after review without changing org defaults. Before claim and
immediately before creation, the controller reads the official v3beta1 profile
endpoint and requires matching profile/org IDs, mandatory enforcement, read-only
Devin MCP and git, removed GitHub CLI token, explicit empty MCP server list, and
only exact hostname destinations `github.com` and `api.github.com`. Null MCP or
network policy means no opinion and is rejected; wildcards/extra hosts are
rejected. Session creation pins that profile and `secret_ids: []`. Missing or
weakened profiles block spending. Keep CI off until profile setup/readback and
parent acceptance; this implementation has not activated a paid pipeline. Leave
the separate native CI Automation disabled so there is only one dispatcher.

The pilot accepts only owner-authored, open, non-draft, same-repository PRs to
master; both author and dispatch actor must currently have write/maintain/admin
permission. GitHub's built-in `GITHUB_TRIGGERING_ACTOR` must also be the owner,
including on reruns; the original actor/event alone is insufficient. Forks, bots
and non-owner collaborators are outside this initial
ceiling. Exactly one native closing-issue link must resolve to an open nonempty
issue. Immediately before dispatch, the orchestrator uses its existing connected
`gh` authentication to verify that same issue's live membership in owner Project
8 and read its current Status, Priority, Execution tier and Review tier. No token
is copied into Actions and no local executor, daemon or signing layer is added.
The owner's explicit dispatch confirms that scheduling snapshot. Actions validates
its canonical shape and binds its issue/head, but **does not revalidate live
Project membership or fields**. CI requires admitted Ready; review candidates
require admitted Ready or Review and a non-none review tier. A Project change
after the orchestrator's readback is outside this controller's measurement.
Labels, titles, PR body assertions and Project membership alone grant nothing.

Dispatch **on master**, with `action=investigate` or `repair`, `number`, the full
current PR `head`, the current failed CI `check_run_id`, and an `admission` JSON
object. The object has these keys (no copied task/design body):

| Key | Owner confirms |
|---|---|
| `head` | Same exact input head as the dispatch |
| `issue_updated_at` | Current canonical issue GraphQL `updatedAt` |
| `project_ref` | Exactly `https://github.com/users/mikeng-io/projects/8` |
| `project_issue_ref` | Exact same native canonical issue URL read by Actions |
| `status`, `priority`, `execution_tier`, `review_tier` | Current Project readback via existing `gh`; values from the root schema, semantic classification resolved |
| `scope_ref` | Current issue section bounds paths, non-goals and investigation or repair authority |
| `acceptance_ref` | Current issue section gives acceptance or revisioned design acceptance pointer |
| `prerequisites_ref` | Current issue section records resolved dependencies, environment and permission prerequisites |
| `ownership_ref` | Current issue section confirms no other active run/PR worker owns this work |
| `capability_ref` | Current issue section records evidence that the normal service route meets the assigned capability |
| `confirmed` | JSON `true`: the owner has read and confirms all those current facts |

The five scope/evidence references must be the canonical issue URL plus a section anchor. The issue
may link immutable design/evidence there; the dispatcher does not guess semantics
or resolve arbitrary external URLs. Existing issue forms lack structured fields,
so this explicit trusted confirmation is required. Actions binds it to issue
revision/head and the owner-confirmed scheduling fields; repeated API reads before
claim and immediately before dispatch cover the current issue, base, paths and
CI evidence only. After its own receipt comment, comparison ignores only issue
`updatedAt` and still binds the body digest and other repository facts. Actual
issue body changes block launch. Project classification/status changes require
fresh orchestrator admission; they are not detected by an Actions Project query.
Investigation is read-only; repair needs its own explicit admitted scope and
produces a local patch/evidence only. The parent owns push and landing. The
already-read issue body, paths and base are ephemeral session input, never a
tracked task copy. Missing logs/check tooling must be reported unavailable.
Operator confirmation covers external workers and Project status; controller
receipts are the machine-measured ownership signal in Actions.

CI must be a completed failure from the GitHub Actions App, the current latest
check of that name on that exact SHA, linked to the repository's `ci.yml` run and
matching check suite. Missing associations, merge-SHA-only evidence, stale/pending
checks, unknown values and API/pagination errors are unavailable, never a clear
result. No automatic repair or background retry follows a notification.

CI uses official v3 session creation, normal **service mode** (underlying model
and vendor are not asserted), `max_acu_limit=1`, `bypass_approval=false`, disposable
sessions and instructions forbidding children/review/auto-fix/merge/deploy. One
Actions concurrency group serializes all controller operations. Before spending,
a pending receipt is written on the canonical issue. All repository issue/PR
comments are read with bounded pagination; hitting the bound blocks. Only the
Actions bot's marked receipts whose producing run is this owner-dispatched master
workflow count. Other trusted default-branch workflows and repository admins
share that trust boundary; preserve receipt history and do not forge/edit/delete
receipts. Two reservations across all actions per rolling 24 hours is the cap;
action+PR+head never relaunches, and any unresolved claim blocks all launches.
The accounting covers this controller, not unrelated/native/manual spending.

A validated session URL plus a subsequent GET confirming its identity and pinned
profile changes a receipt to running; create responses do not populate the profile
binding. A claim alone is never Running. If readback or receipt update fails, the
validated URL is retained in the failure output and, when writable, the pending
receipt. Request/authentication/validation rejections (400/401/403/404/405/415/422)
resolve the claim as rejected; it still counts toward the cap and blocks the same
action/PR/head. A preflight abort before the create call similarly resolves as
`not_started`. Conflict, timeout, throttling, server errors and ambiguous creation
retain the global claim and never retry. For a known running or pending-with-run
receipt, owner-dispatch `action=reconcile`
with `admission={"receipt_id": <comment-id>}`. This reads the current official v3
session and resolves the receipt only for exit/error with no children; it does not
establish acceptance or update Project status. An ambiguous claim without a run
identity cannot be released by this route: investigate the issue/run evidence
and escalate to the parent. There is no automated recovery/relaunch engine.

For **review selection**, set `ENGINEERING_REVIEW_SELECTOR_ENABLED=true`, then
owner-dispatch `action=review` with number, head and the same current admission
object (no CI check ID). The exact qualified signal is JSON `state: "ok"` plus
`candidate: {pr, head, issue, acceptance_ref, review_tier}` in the workflow log.
It means admission qualified a request, not that a review ran. Automatic intake
without this confirmation is only a notification. The cheap exclusion skips only
Markdown at the root or under docs/design (excluding policy/security/boundary
paths), and exact client config paths `.mcp.json`, `.cursor/mcp.json`,
`.vscode/mcp.json`, `.codex/config.toml`, `.claude/.mcp.json`, `.claude/mcp.json`.
Both sides of renames count. Runtime Markdown, mixed changes, general Claude
settings, tests, CI, gate and unknown paths remain subject to canonical tiers.
This exclusion does not assign tiers or override an unresolved semantic decision.

The operator configures Review Console themselves. For each qualified candidate:

1. Recheck its exact head, current issue acceptance, non-none Project review tier
   and absence of another active reviewer before selecting that PR in Console.
2. Verify **automatic-review** enrollment scope and the budget mechanism applicable
   to that route; avoid blanket repository enrollment/every-push review. Console
   authentication is an external prerequisite, never bypassed by this controller.
3. Read back the actual reviewed commit SHA and findings. The API/Console may fetch
   a newer head after selection: a raced result does not review the admitted head.
4. Treat Devin Review as supplemental; vendor independence remains unproven unless
   separately evidenced. Never autoapprove, auto-fix, merge or mark Done from it.

**Manual Review API creation is absent.** Its documented per-PR Console spend cap
applies only to automatic reviews and does not cap a manual API trigger. No flag
can enable that POST here. A verified applicable account/org budget mechanism
and separately reviewed implementation would be needed to add it. `REVIEW.md`
supplies context, not enrollment control, and cannot cancel a paid review.
[Official security profile](https://docs.devin.ai/api-reference/v3/security-profiles/get-organizations-security-profile),
[profile enforcement](https://docs.devin.ai/product-guides/security-profiles),
[official session API](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions),
[manual Review API](https://docs.devin.ai/api-reference/v3/pr-reviews/post-organizations-pr-reviews),
[Review budget scope](https://docs.devin.ai/work-with-devin/devin-review).

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
