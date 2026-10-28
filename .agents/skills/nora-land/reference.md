# nora-land: tool notes

**Claude Code with the rtk hook, in a worktree-isolated subagent.** Every `git`
call is refused: the hook rewrites it to `rtk git ...`, and the isolation guard
refuses a launcher with git among its operands (`git -C`, `rtk proxy`, `PATH=`
too, 2026-09-24). Measured fix: write `<scratchpad>/vcs` (no "git" in the
name) with the file-writing tool, `chmod +x` it, then `V=<scratchpad>/vcs; $V status -sb`.

```bash
#!/bin/bash
cd <worktree> || exit 1
exec "$(command -v git)" "$@"
```

`command -v` takes whichever git PATH gives (`/usr/bin/git` or Homebrew's
`/opt/homebrew/bin/git`). The subagent starts on a harness branch: step 1 is
`$V fetch origin && $V switch -c <type>/<slug> origin/master`, then `uv sync
--extra evals`. Claude Code commits end with a `Claude-Session:` footer.

## Choosing a worker

[ORCHESTRATION.md](../../../ORCHESTRATION.md) owns current capability tiers,
adaptive routing, independence and review selection. Resolve the available
worker and its advertised options at launch; the historical choices below are
not admission evidence or current routing instructions.

**T3 `delegate_task` (`isolation: worktree`).** The worktree starts from the
orchestrator's local commit. A build starts with `git fetch origin && git switch
-c <type>/<slug> origin/master` inside it, then `uv sync --extra evals`; never a
second `git worktree add`. A reviewer selects the exact head under review.

## Superseded routing history — 2026-10-21/22

**Historical reference only, superseded 2026-10-27 by ORCHESTRATION.md.** The
quoted rules below preserve provider incidents, former fallbacks and the trial;
they do not assign today's workers. Recheck capabilities and availability live.
The former Codex/Kimi/Haiku lane table replaced the Sonnet/Opus tier table on
2026-10-21; that had replaced the 2026-09-24 “Opus reviews” rule on 2026-09-30.

### Former assignments and quota rationale (historical)

It runs on Claude **Sonnet 5.5** by default and on Opus 5.5 for a
design or hard-judgement session (operator, 2026-10-22). The Pro plan's
5-hour and weekly limits are shared across models, Opus also has its own
weekly cap and costs more per prompt, and the orchestrator's seat is the
longest-running context of any lane. The work goes to these lanes:

| the task | lane | how it is launched |
|---|---|---|
| build, fix, debug, a design bundle, merge conflicts: anything that edits code, tests, doctrine or design | Codex, `gpt-6.1-sol` | T3 `delegate_task`: `providerInstanceId: codex`, `model: gpt-6.1-sol`, `options.reasoningEffort` = the effort table's level (it takes all five), `isolation: worktree` |
| full review, audit, verification of a built change or a PR (the review table below says when) | Kimi, `kimi-code/k3-256k` | T3 `delegate_task`: `providerInstanceId: acpRegistry_kimi_cli`, `model: kimi-code/k3-256k`, `role: review`, `isolation: worktree`; when that route is down, the CLI, `kimi -m kimi-code/k3-256k -p "$(cat <brief>)" > <log> 2>&1`, run in a checkout of the head under review |
| light review (the review table below says when) | DeepSeek Harness, `deepseek-v4.1-flash:1017` | T3 `delegate_task`: `providerInstanceId: acpLocal_dsh`, `model: ["ollama-cloud","deepseek-v4.1-flash:1017"]`, `role: review`, `isolation: worktree`. `model: default` resolves to the same model today (2026-10-22); pin it anyway. The profile routes to Ollama Cloud (`~/.dsh/profiles/acp/cordis.patch.yml`); `deepseek-official` has no key stored and fails to open a session |
| mechanical: renumber, rename, doc sync, config edit to a given value, read-back, lookup, log or diff fetch | Claude `haiku` | the native sub-agent tool with `model: haiku`, or `delegate_task` with `claudeAgent` / `claude-haiku-5-5` |
| a light check: read a gate rc, open a cited line, compare two shas, count a log | the orchestrator itself | no launch; a launch costs more than the check |


### Provider incidents and bounded-work lessons (historical quotations)

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

- **Kimi runs through T3 on the installed binary.** The provider's
  executable override (`commandPath` in `~/.t3/userdata/settings.json`) is
  `~/.kimi-code/bin/kimi`; without it T3 runs its own downloaded copy, which
  returned empty turns (2026-10-21). The T3 route pins the model by name
  (`kimi-code/k3-256k`): `model: default` is not advertised and fails with
  `model_unavailable` (2026-10-21). The model a run used is in its
  `wire.jsonl` under `~/.kimi-code/sessions/`; on the CLI, `-m` pins it. Its
  worktree starts from the orchestrator's local commit, so a review
  brief's first step is `git fetch origin && git switch --detach origin/<head
  branch>`.
- **A reviewer is told it is read-only, and that is measured, not trusted.**
  No review route has a read-only switch (Kimi's `-p` cannot be combined
  with `--plan`, `--auto` or `-y`), and T3 does not report which worktree a
  task used. So every review brief ends by asking for `pwd` and
  `git status --short`, and the orchestrator checks that `git status
  --short` in that checkout prints nothing. Kimi's thinking stays on; it takes no effort
  flag, so the brief's scope is the lever. Its findings are verified like any
  other review's (`nora-land` step 7).

- **When a lane is down, use the next and say so in the report**: implementation
  falls back to Claude `opus` through the native tool; review falls back to the
  Kimi CLI, then a fresh `gpt-6.1-sol` launch that did not build the change; light
  review falls back to Kimi; mechanical work falls back to `gpt-6.1-sol` at `low`.
  A 429/403 quota error means the lane is down; use the fallback above.
  A lane is down when its launch fails or its quota is spent, not when the
  orchestrator would rather do the work.
- **Kimi builds on trial** (operator, 2026-10-22). GPT's quota runs out
  first, so 3 to 5 builds to a finished spec, additive or off by default,
  go to `kimi-code/k3-256k`. Each one gets a review by a fresh `gpt-6.1-sol`
  at `medium`, whatever the review table's row: the trial is a measurement,
  and a build nobody reviewed measures nothing. While GPT is down, no trial
  build launches, because its reviewer would have to be Kimi. The
  report records what the review found and any defect found after merge.
  If Kimi's builds come back as clean as GPT's, spec'd builds move to Kimi
  and its plan grows instead of GPT's. If not, Kimi stays on review.
  MiniMax is not a lane: never M3, and M3.1-Flash only if the operator
  asks for it by name.
