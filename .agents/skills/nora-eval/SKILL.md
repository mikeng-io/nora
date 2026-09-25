---
name: nora-eval
description: Runs Nora's eval suite (python -m evals on the deploy host) as a before/after that CONFIRMS a built change to a prompt, doctrine, objective, tool text, gate, routing or model setting. Load when a task says run the eval, baseline, corpus, sample or measure the arms. It never diagnoses a failing case; find the cause offline first. Not for proving a code fix is guarded by a test (nora-prove-fix).
---

# nora-eval

A change to what a model reads, or to which tool it reaches for, is decided by a
before/after on labelled cases, never by one example. The reasoning is `AGENTS.md`
"The spine" and `docs/CONVENTIONS.md` "The instruction is the first thing blamed
and usually the wrong one"; do not retell it, apply it.

**Eval required:** `nora/doctrine/` or an objective, a tool's name, description or
schema, a gate or classifier instruction, a rendered prompt block, the model,
effort or routing config. **Gate is enough:** deterministic code that a named test
can turn red (use nora-prove-fix). Many changes need both.

**When a model judges badly, read its input before rewriting its instruction.** If
the judgement turns on when, who, or whether something is still open, check the
input carries the time, the index, or the fact that would close it.

## An eval confirms a fix; it never finds one

An eval says THAT a case fails, never WHY. It costs hours (a judged draw is
~211s) and millions of tokens, and a failure is fixed by reading, not by
drawing again. On 2026-09-25 one session queued a five-question batch plus an
8-corpus re-baseline; it ran for hours (one agent ~5h), and every answer was a
failure (0/5, 2/10, 0/15, 0/15) that reading the input would have explained
with no model call. The operator stopped it: nothing was fixed, only measured.

No run answers WHY a case fails. Before any run, one of these holds:
- **(a) A fix for a failing case.** The cause is named offline first:
  `--print-input CASE` (no model call), the render functions called on the
  case's state, the case's own anchor and expectation, and the objective the
  model runs. Name it: a wrong expectation, a fact missing from the input, a
  doctrine line steering against it, or unlabelled material (the model cannot
  tell what it is reading). The fix is built; the run is `--case` those cases
  plus one control, and the corpus they sit in when the fix can move others.
- **(b) A change with no failing case** (a model, effort or routing swap, a
  tool rename, new doctrine or behaviour, a no-regression check). It is built,
  step 5 shows the arms differ, and the run covers the corpora it can move at
  step 6's n.

Deterministic failure (every draw FAILs, on every arm) is an input, framing or
labelling defect: never answer it with more samples. The before arm of a
confirming run is its baseline; a standalone re-baseline or higher-n re-run
"to see where we are" is not work unless the operator asks. Before launching,
compute cases x n x arms x ~211s (judged) and state it; over 60 minutes, cut
the selection or ask.

## Where it runs

On the deploy host (`norty`), in an eval worktree, never in the deploy checkout
`~/workspace/nora` (a run writes `config.evaluation.toml` into the checkout root).
Composition refuses to start anywhere else with the deployment's features on
(`design/evaluation/11-the-sandbox-rebuild.md`, "Where the suite runs"). The
subject's model comes from the deployment's `config.toml`; there is no `--model`
flag. The dev-machine tunnel recipe with `--model` belonged to the deleted
`evals.run` harness.

`nora_eval`/`nora_eval_blank` are SHARED with every other session on the host:
a run refuses only if the source is AHEAD of this checkout's own head
(refresh it with `make eval-source` or switch branch — a source BEHIND head
runs, and a read that actually hits a column it lacks refuses just as
clearly), and a divergent template migrates a private per-run copy rather
than the shared one, swept at startup if a killed run (or SIGTERM) left one
behind — fixed 2026-09-25 after an ahead branch upgraded `nora_eval_blank`
and broke every other session's seeding
(`design/evaluation/11-the-sandbox-rebuild.md` §1.10).

## Procedure

1. **Read the suite's open defects.** An eval that another session or the operator
   has declared broken or not yet run stays stopped until its fix lands; do not run
   around it and never build a second harness beside `evals/` (the most repeated
   operator correction, 09-16 to 09-24).
   ```bash
   gh issue list --state open --search "evals in:title" --json number,title
   gh issue view 462 --json state      # draws share one Honcho workspace
   ```
   Check: you can say, per open issue, whether it biases the corpus you will run.

2. **Nothing in flight.** One run per host: a second competes for the gateway and,
   while #462 is open, reads the first run's memory writes.
   ```bash
   ssh norty 'pgrep -af "[p]ython.*-m evals"; pgrep -af "[r]efresh_eval_clone"'
   ```
   Check: no output. The `[p]` keeps pgrep from matching its own ssh command line.

3. **Put each arm's checkout at its exact ref.** The eval worktree is a separate
   checkout; an unpulled run measures old code and reads exactly like success
   (2026-09-22). One worktree per arm keeps the arms from sharing a tree.
   ```bash
   ssh norty bash -s > arm.log 2>&1 <<'EOF'
   cd ~/workspace/nora-wt-eval-rebuild || exit 1   # or a fresh worktree per arm
   git fetch -q origin && git checkout -q --detach <arm-sha> || exit 1
   ~/.local/bin/uv sync --extra evals > /dev/null 2>&1; echo "SYNC_RC=$?"
   git log -1 --oneline
   git grep -n '<a phrase or symbol your change adds>'
   EOF
   ```
   Check: HEAD is the arm's sha AND the grep finds (after arm) or misses (before
   arm) the change.

4. **Refresh the source only when you must.** `make eval-source` rebuilds
   `nora_eval` from production and drops every sandbox and the blank template, so
   it kills any run in flight (`scripts/refresh_eval_clone.sh`). Both arms read the
   same source: never refresh between them.
   Check: its last line prints `messages=… newest_ts=… alembic=…`; record it.

5. **Prove the arms differ before paying for a run.** A harness that cannot see
   the variable reports PASS, not ERROR (2026-09-17).
   - `standing` corpus: `--print-input CASE` on each arm, then `diff` the two
     outputs. It calls no model.
   - Other corpora: `git diff --stat <before> <after>` names only the files under
     test, and the changed text is in a file the corpus's entry point composes
     (`--list` prints each corpus's entry point).
   - Tool returns: every tool result on the path must have production's
     success/failure shape. A case input naming a handle the sandbox cannot serve
     changed the tool result and with it the decision: eval 8/10, live 0/2 (#454).
     If the shapes cannot match, the eval cannot gate the change; say so.
   Check: a concrete difference you can point at in each arm's input.

6. **Choose the corpus and n by the question.**
   ```bash
   ~/.local/bin/uv run python -m evals --list
   ```
   Select by name, `--class S7`, or `--all` (hours: a judged draw is ~211s).
   `--case NAME` (one corpus only) re-draws just the cases that moved. `--sample N`
   draws each case N times; the default 1 is a smoke run. Judged corpora take
   `--judge` (default judge id `nora-judge`, `--judge-samples` default 3), and
   `standing` refuses to start without it. `--reasons` prints every draw's grader
   reason, PASS included; on `tool_selection` (routing, graded on the tools that
   executed) that is the tool list per draw. The `eval-*` targets in `Makefile`
   wrap common selections, all but `eval-note-candidates` at one draw. If you name another judge id, probe it
   with a real call: the gateway's `/v1/models` listing is not the catalogue
   (2026-09-10).
   Check: n is stated before the run, and chosen for the claim (a rate over four
   positive cases needed n>=15, #158).

7. **Run each arm, one after the other, capturing the code.** From the worktree
   root (the deployment's doctrine path is relative, so the checkout's doctrine is
   what the subject reads):
   ```bash
   ssh norty bash -s <<'EOF'
   cd ~/workspace/nora-wt-eval-rebuild || exit 1
   set -a; . ~/workspace/nora/.env; set +a
   ~/.local/bin/uv run python -m evals relevance_gate --sample 5 \
     > /tmp/eval-<arm>.log 2>&1; echo "RC=$?" > /tmp/eval-<arm>.rc
   EOF
   ssh norty 'cat /tmp/eval-<arm>.rc; grep -E "spend:|PASS|FAIL|ERROR|UNSTABLE" /tmp/eval-<arm>.log'
   ```
   Check: read the rc file, then the log. `RC=0` means no FAIL, ERROR or UNSTABLE;
   `RC=1` means at least one, or a crash (the log says which); `RC=2` means
   refused before spending (read why).
   Output is block-buffered under a redirect: wait for exit, do not tail.

8. **Read the log honestly.**
   - The first line names the config the subject was rebuilt from; the census line
     per corpus counts parked cases (never run) and unanchored ones (they ERROR).
   - Per case: PASS or FAIL only when every draw agreed; UNSTABLE prints the split
     `[p pass / f fail / e error]`. ERROR is never PASS. A case read stable at n=3
     later failed 1 of 4 draws (2026-09-22): report n with every number.
   - `spend:` per corpus: calls, input (cached, fresh), output. `TRUNCATED` means a
     call hit its ceiling and that draw measured a cut-off output.
   - While #462 is open, draws on a memory-reading entry point (tag,
     participation, proactive_act, dreamer with memory tools) are correlated:
     call the result a direction, not a rate, and treat a "you already asked"
     FAIL as #462 before calling it a model defect.
   - Judged corpora: the judge's source is bound to the window her turn
     rendered since #459 (fixed 2026-09-25): a judged number from an older
     build is not a baseline; compare only against a before arm run now.
   - A case that speaks (tag, participation, proactive_act) anchored in a Discord
     room ERRORs; Discord's adapter is off in the sandbox (2026-09-21).
   - A FAIL is the end of the run, not a reason for more samples: go back to
     "An eval confirms a fix" and read the case's input, anchor and expectation.
     A wrong expectation fails every draw and measures the anchor, not the model.
   Check: every number you will quote has its n, draw split and arm beside it.

9. **Write it into the PR body** (a `What` block, per `CONTRIBUTING.md`):
   ```
   Eval: `python -m evals <corpus> [--case …] --sample N [--judge]`, on norty,
   source refreshed <newest_ts>, one run on the host at a time.
   | case | n | before <sha> P/F/E | after <sha> P/F/E |
   Spend: before <calls> calls, in=<…> (cached <…>), out=<…>; after <…>.
   Arms shown to differ by: <print-input diff | the grep in step 3>.
   Biases: #462 open (draws correlated), <others from step 1>.
   ```
   Check: a reader can re-run the exact command on the same shas.

## Adding a case

Extend the corpus that covers the behaviour under `evals/corpora/`; anchor rules
and parking are `design/evaluation/11-the-sandbox-rebuild.md` §1.3 and §7.1, and
`tests/architecture/test_every_case_is_anchored_or_parked.py` rejects an
unanchored case before a run.

## Tool notes

- **Claude Code:** launch the long run as the Bash command itself with
  `run_in_background`, never `nohup … &` inside it: the completion notice then
  reports the wrapper, and a live run looks dead (2026-09-17). The notice's exit
  code is the last command's, so read the rc file, not the notice.
