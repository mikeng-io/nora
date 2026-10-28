# Daily release evaluation and execution

The operator authorizes this automation to evaluate the criteria below and, when
all pass, execute the formal release pipeline through `SKILL.md`. Ordinary master
pushes never release. This is conditional daily automation, not advisory-only.
Standing execution authority covers patch and minor releases only. A major bump
requires the owner's explicit approval for the assessed candidate and version;
without it, report needs owner approval and perform no release mutations.

## Evidence and hard gates

1. Read current `AGENTS.md`, this file and `SKILL.md`. Pin the latest published
   stable tag/commit/time, current master SHA, all still-unreleased eligible
   runtime changes, existing release PRs and any in-flight publication/build.
   Session memory is context, not the baseline. No eligible change means wait.
2. Honor operator holds. Bootstrap requires #250 fixed with merged evidence and
   #317 merged with its final gate/review complete. A closed issue or an RC suffix
   alone is not proof. Do not merge either implementation PR from this automation.
3. Read successful CI on the exact source candidate and relevant merged PR
   checks/review/acceptance evidence. No unresolved release blocker, known failing
   gate, missing required evidence or unavailable check may be treated as green.
   Use recorded evidence; do not launch live model evals or production probes.
4. Identify actual compatibility impact from diffs and contract evidence, not
   only commit markers. Patch = compatible fixes; minor = compatible capability;
   major = an evidenced incompatible supported interface. Respect the planner's
   declared minimum. If major is required, stop for owner approval; never select
   patch/minor to avoid that boundary. A disagreement between the diff and markers is blocked for
   correction, not permission to hide the break. Required migrations, rollback
   limits and live config prerequisites must be documented and ready before
   publication; unfinished manual work is blocked.
5. Re-read the candidate and existing release state immediately before acting.
   A changed candidate requires reassessment. Never create a competing release
   PR, duplicate version or second active pipeline. Resume matching existing
   release work only after checking its candidate, checks and prior progress.

## Is a release worthwhile now?

After all hard gates pass, release when at least ONE condition is evidenced:

- A fix addresses an active security exposure, service failure, data-loss risk
  or regression in a supported, currently used path.
- A complete user-visible capability or coherent improvement is ready, with its
  relevant acceptance evidence. Internal renaming alone is not such a capability.
- Eligible routine fixes remain unreleased and the previous stable release was
  published at least seven days ago (use its actual timestamp).

Otherwise wait and name the condition that is not met. Counts of commits or PRs
alone never force a release. These are the initial policy defaults; changing them
is an operator decision, not a decision the scheduled agent rewrites for itself.

## Execute the admitted release

- Record the candidate, gate readings, worth-releasing condition, chosen SemVer
  and migration facts in the session, then follow `SKILL.md`.
- Dispatch prepare on master with the chosen bump and full assessed `candidate`.
  The workflow rejects a different source head. Read back its run and the one
  generated release PR. Its diff must contain only the five release metadata
  files; read the changelog for range coverage and breaking/migration details.
- Require the release PR's actual required checks/review state to pass on its
  exact head. Confirm its base still matches the assessed source. Merge only
  that release metadata PR, with `--match-head-commit`, a real merge commit and
  the authored subject. Never bypass checks or merge application/implementation PRs.
- Read back the merge and dispatch publish. Release-please alone creates the
  formal tag/Release. Publication starts the existing image bundle and Komodo
  deployment pipeline; the operator's daily authorization includes this normal
  path once the criteria pass. Do not SSH to production, edit live config, invent
  tags, rebuild host images or introduce another deployment route.
- Verify the Release/tag/source and follow the existing pipeline's run/receipt
  evidence. Started, queued, built, published and deployed are separate states.
  If image/deploy evidence is pending or inaccessible, report that state honestly;
  never declare deployed from a tag or green image build alone.

Return wait, blocked/unavailable, pipeline in progress, or verified outcome,
with evidence links. Keep partial progress in the existing PR/workflow/session
records so the next run can resume without a second release. Budget exhaustion
or an API/check failure stops further mutations, preserves progress and is never
success. No blind retry, new child session, code repair, unrelated GitHub writes,
branch-rule change, credential inspection or external messages. Native Preflight
is optional; the no-change check must also run when Run now bypasses it.

RC/nightly image publication remains unsupported. If stabilization is needed,
report blocked/needs RC and its missing prerequisites; never label an unready
candidate stable merely to fit the current publisher.
