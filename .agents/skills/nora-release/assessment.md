# Daily release assessment

Read-only instructions for a native Devin Automation. It recommends a release;
it does not prepare or execute one. `SKILL.md` owns the manual release procedure.

1. Read the repository's current `AGENTS.md`, this file and `SKILL.md`. Resolve
   the latest published stable release and pin its source commit and current
   `master` head. Use the full unreleased range, including changes held on prior
   days. Also read existing release PRs, RCs and their candidate commits.
2. Use trusted git history and read-only GitHub metadata. Do not run repository
   code, tests, builds or production probes. If no eligible user-visible runtime
   changes exist, report **wait — no unreleased change** and stop. Reuse the
   scope described by `scripts/release_scope.py`; do not execute the planner,
   which writes release metadata.
3. Read the actual changes and commit bodies. Assess user-visible value, urgent
   fixes, completeness of a useful change set, compatibility and migration
   requirements. Commit counts and days elapsed do not force publication.
   Report the Conventional Commit minimum and any discrepancy with actual
   compatibility; never conceal a breaking change or rewrite commit history.
4. Read existing checks on the exact candidate and relevant review/acceptance
   evidence. Label anything missing, stale or inaccessible as unverified. An RC
   suffix or earlier green head does not prove this candidate is ready. A newer
   master head invalidates an older head-specific recommendation.
5. Return one recommendation: **wait**, **prepare RC**, **propose stable**, or
   **unavailable**. For RC/stable, name a proposed SemVer and explain why now;
   for wait, explain what would make the next assessment different. Unavailable
   is an inability to assess, not a conclusion that no release is needed.

The report includes the stable baseline, candidate SHA/range, covered changes,
suggested version, migration needs, exact-head check links and unresolved gaps.
For an existing candidate, update the recommendation rather than suggesting a
duplicate PR. Read current facts each run; session memory is only context.

Keep the report in the Devin session. Do not write to GitHub, modify files,
create/merge a PR, change branch rules, dispatch workflows, tag, publish, deploy,
repair code, send messages, create child sessions or extend this automation.
Do not access live config, credentials, production rows, logs or hosts. Stop at
the configured session budget and report incomplete evidence without retrying
through another agent. Missing tools or permissions are unavailable.

The live schedule, enabled state, profile and budget are configured and read back
in Devin, not duplicated here. Native Preflight is optional; the no-change check
above must run even if a manual Run now bypasses it.
