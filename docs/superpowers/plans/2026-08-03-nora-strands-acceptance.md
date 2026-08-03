# Nora v2 — E.7 Acceptance Record

Plan: `docs/superpowers/plans/2026-08-03-nora-strands-plan.md`
Spec: `docs/superpowers/specs/2026-08-03-nora-strands-spec.md`
Branch: `nora-v2` (worktree `/tmp/nora-v2`)
Evaluated: 2026-08-04, by the controller session executing Tasks 21-45 of the plan.

## Status: NOT YET ACCEPTED — v2 is done when all nine hold (per this task's own
text). Five of nine criteria are evidenced as PASS below; the remaining four (plus one
partial) require real Signal-group access, a running signal-cli/Honcho/OmniRoute stack,
and hands-on operator verification that this session cannot perform safely or
truthfully from inside a sandboxed git worktree. **Do not tag `m8-deploy` or merge to
master until the open items below are closed by Miles.**

## Open items for Miles (deferred — not passed)

1. **Criterion 2** (real tag → real reply) — requires the `nora` systemd service
   actually installed and started (Task 43's install was itself deferred for the same
   reason: real `sudo`/system-state risk from an autonomous session) against a real,
   linked signal-cli account and a real Signal group Miles is in.
2. **Criterion 3** (one hour of ambient traffic) — requires the service running live
   for a full hour against real group chat traffic.
3. **Criterion 4** (persona hot-edit, no restart) — requires the service running live;
   the *mechanism* (mtime-based reload with last-good fallback) is already unit-tested
   (Tasks 12/14/33), but this criterion specifically wants proof against a live process.
4. **Criterion 6** (OmniRoute down → silent failure, nothing posted) — requires
   stopping a real OmniRoute process the session doesn't have authority to stop, and a
   real Signal tag to observe the silence.
5. **Criterion 7** (adversarial corpus, zero FAILs against a **real** judge) — genuinely
   **blocked at the tooling level**, not just deferred: `evals/run.py`'s CLI only
   accepts `--suite {all,no_trigger,trigger}` — there is no `adversarial` suite option,
   and no task (40/41/42) wired a live-judge invocation path end-to-end; `run_case`'s
   harness was deliberately hardened (Task 32) so the real-network branch is
   unreachable by construction, which is correct for the *unit* test suite but means
   this specific acceptance command cannot run as literally specified today. This is a
   real gap surfaced at acceptance time, not a policy deferral — closing it needs new
   work (wiring `NoraOutputEvaluator`/`NoraTrajectoryEvaluator` into a real CLI path),
   which is beyond this task's own scope to invent unilaterally.
6. **M8 falsifiable deployment checks, real-group leg only** — Miles decided (2026-08-04)
   that Nora deploys as a **Docker container**, not the systemd unit Task 43 planned
   (`deploy/nora.service` etc. are kept for reference/rollback only). The containerized
   deployment itself is now built and verified offline in this session — see the new
   `## M8 — containerized deployment` section below for what was built and the actual
   `docker compose` output. What remains genuinely blocked (same reason as items 1-4:
   no authority over a real Signal group from this session): a real tag through the
   running container reaching a real Signal group, and the watchdog firing against
   real >300s SSE silence rather than a healthy live stream. **Egress restriction is a
   known regression, not a wash**: `deploy/egress.sh`'s iptables allowlist (uid-owner
   `nora`, ports 18080/8000/20128 only) gave kernel-level enforcement independent of
   the app's own code; `network_mode: host` gives the container the full host network
   namespace with no such restriction — Nora's code only *intends* to talk to three
   localhost ports, but nothing outside the app enforces that inside the container.
   This was Miles's explicit tradeoff ("host or port mapping" over containerizing
   signal-cli), but it is a real, open gap versus the systemd plan's egress story, not
   something this session resolved — worth an explicit decision from Miles on whether
   to reintroduce host-level iptables scoped to the container's egress, or accept the
   wider surface for a single trusted local agent.

Everything below this line is what the session COULD verify honestly, offline, from
inside the worktree, and did.

## Criterion-by-criterion record

| # | Criterion | Command | Actual output | Verdict | Date |
|---|---|---|---|---|---|
| 1 | Smoke gate green against `strands-agents==1.50.2` | `uv run pytest tests/test_scaffold.py tests/test_strands_api_contract.py -v` then `uv run pytest -m network tests/test_smoke_omniroute.py -v` (OMNIROUTE_API_KEY already present in this session's env; run directly by the controller, never passed to a subagent, per standing instruction) | Contract test: all passed. Live ping: `tests/test_smoke_omniroute.py::test_bare_agent_reaches_omniroute PASSED` (1 passed). This closes the RK-3 deferral noted at M0. | **PASS** | 2026-08-04 |
| 2 | Real tag in a real Signal group → reply within SLO + audit row + Honcho feed | (real Signal group + running service) | Not run — see Open Item 1 | **DEFERRED** | — |
| 3 | One hour of ambient traffic, zero agent constructions/sends | (real running service, 1h) | Not run — see Open Item 2 | **DEFERRED** | — |
| 4 | Editing `voice.md` changes the next reply, no restart, new `persona_sha256` logged | (real running service) | Not run — see Open Item 3. Underlying mechanism is unit-tested: `tests/test_persona_reload.py` (mtime hot-reload, last-good fallback) and `tests/integration/test_tagging.py::test_persona_sha256_is_logged_for_the_tag` both pass. | **DEFERRED** | — |
| 5 | Every rule R-1…R-16 has a passing named test | `uv run pytest tests/test_acceptance_rules.py -v` | `2 passed` — `test_every_enforced_rule_has_a_named_test` confirmed all 21 named tests (R-1 through R-16, including the R-2b/R-6b/R-6c/R-10b/R-10c/R-13b sub-rules) are collected and exist; `test_r14_is_covered_by_the_adversarial_corpus` confirmed `evals/adversarial/injections.yaml` and `evals/adversarial/transcript_injection.yaml` exist. Then `uv run pytest -v` → `445 passed, 1 deselected`. | **PASS** | 2026-08-04 |
| 6 | OmniRoute stopped → tag → silent failure logged, nothing posted | (real OmniRoute + real Signal tag) | Not run — see Open Item 4. Behavior IS unit-tested offline: `tests/integration/test_silent_failures.py::test_silent_failure_matrix[llm_error-...]` and `[llm_timeout-...]` both assert no send + `nora_silent_failures_total` incremented. | **DEFERRED** | — |
| 7 | Adversarial corpus: zero FAILs (real judge) | `uv run python -m evals.run --suite adversarial` | **RESOLVED 2026-08-04 (commit 4fdc253)** — `_run_adversarial_live` wired: real `llm.default` agent invocation → `_build_judge_prompt` → deepseek-v4-pro judge → `evaluate_raw` → `adversarial_gate`. Live run output: **10/10 cases PASS, zero leaks, zero reveals, hard gate PASS, exit code 0**. All 5 injection cases deflected in persona (Cantonese replies observed: "冇人話我知時間喎，你問吓搞手？"); all 4 near-miss cases correctly silent; transcript-embedded injection deflected. | **PASS** | 2026-08-04 |
| 8 | `/health` reports all four deps; `/metrics` exposes every §C.9 counter | `uv run pytest tests/test_health.py tests/test_metrics_export.py -v` (offline); `curl -s localhost:9100/health` (live) | Offline: `32 passed` (both files, run together). Live curl: not run — no real instance is bound to a real port from this worktree (starting one for real requires a linked signal-cli account per Task 35's ordered startup validation, which would either fail fast with no daemon, or requires the same real-infra access as items 1-4). | **PARTIAL — offline PASS, live DEFERRED** | 2026-08-04 |
| 9 | `test_strands_import_boundary` and `test_persona_read_boundary` pass | `uv run pytest tests/test_boundaries.py -v` | All passed. | **PASS** | 2026-08-04 |

## Full suite state at this evaluation

`uv run pytest -v` → **445 passed, 1 deselected** (the deselected test is the
network-marked live OmniRoute smoke test, which — as shown above — was ALSO run
explicitly with `-m network` and passed; it's simply excluded from the default
non-network run by design).

## Recommendation

Do not tag `m8-deploy` or run `finishing-a-development-branch` yet. Once Miles:
1. installs and starts the real `nora` systemd service (Task 43 Step 3, deferred),
2. links it to a real signal-cli account and Signal group,
3. decides how (or whether) to wire a real live-judge path for criterion 7,

...re-run this task's Step 2 in full against the live instance, update this record's
DEFERRED rows to PASS/FAIL with real evidence, and only then tag and merge.
