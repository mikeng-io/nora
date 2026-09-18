---
name: nora-principle
kind: principle
status: agreed
derived_from: implementation-first
evidence_base:
  - "nora/__main__.py, nora/app/composition.py — the startup contract and the eight StartupError classes"
  - "nora/agent/guards.py, nora/agent/egress.py — the single egress and its five fail-closed branches"
  - "nora/app/participation.py, nora/app/heartbeat.py, nora/app/fire.py — uncalled speech and its at-most-once discipline"
  - "nora/ingest/pipeline.py, nora/ingest/stages/scope.py, nora/policy.py — deny-by-default and the record/answer split"
  - "nora/config.py, config.example.toml — extra=forbid, ~25 subsystems shipping inert, 15 refusing validators"
  - "tests/architecture/ (37 files), tests/test_boundaries.py, tests/test_acceptance_rules.py — the self-imposed laws"
  - "design/09-decision-index.md — 271 rows; 38 DEPRECATED, 17 STALE, 7 WITHDRAWN, 4 RETRACTED, 4 NOT ENFORCED"
  - "git 2026-08-01..2026-09-18, 2,449 commits — four whole-tier deletions, each with its reason recorded"
---

# PRINCIPLE — Nora

> **Nora manufactures trustworthy restraint: an agent that can be left running
> unattended in a private group because what she will NOT do is enforced in
> code rather than requested of a model — and because every claim the project
> makes about itself is built to be mechanically falsified.**

She is not a chatbot that also declines. Declining is the product; the reply is
what is left when nothing refused it.

## What holding this principle commits the project to

1. **Silence is a first-class outcome with teeth.** `SILENT` is a success
   (`nora/app/fire.py:1463`). R-97 refuses a turn that took the floor only to
   report it found nothing (`nora/agent/guards.py:1106`). Participation is
   excluded from all three never-silent recovery paths
   (`nora/engagement/pipeline.py:825, 869, 944`) — an uncalled turn is never
   rescued into speech.

2. **Uncalled speech is at-most-once: lost is preferred to duplicated.** The
   watermark is written before evaluation (`nora/app/participation.py:245`);
   rows are claimed PENDING→FIRING before firing and recovered to FAILED rather
   than re-queued (`nora/app/heartbeat.py:318`), because *nothing links a row to
   a delivered message, so a retry would double-send*. This is the one place the
   system explicitly chooses to lose a message.

3. **Deny by default, and re-check at the moment of use.** Every per-room trust
   switch defaults False and `tools` defaults empty (`nora/policy.py`). The room
   gate runs before the audit write and the sender gate after it — *recorded,
   not answered* (`nora/ingest/stages/scope.py:43`,
   `nora/ingest/pipeline.py:370`). A thirty-day-old proactive row is re-screened
   against the live policy at fire time (`nora/app/heartbeat.py:261`).

4. **Record before deciding, record before speaking.** Ingest writes the audit
   row before the engage gate (`nora/ingest/pipeline.py:324`); the deliverer
   writes PENDING before the wire (`nora/agent/guards.py:527`). The transcript is
   the substrate every other tier reads, so it cannot be downstream of a
   decision.

5. **One egress, fail-closed, in both dialects.** `Deliverer` is the only caller
   of `adapter.send()` (`nora/agent/guards.py:78, 540`). Five refusal branches,
   each returning `False` rather than raising, each counted; leak and identifier
   scans run against the rendered *and* the raw form, because a member's phone
   number in bold matched one and not the other.

6. **Refuse to start rather than run looking healthy.** Eight classes of
   `StartupError`. An allowlist permitting nothing is refused because it is
   *indistinguishable from a working Nora in a quiet group*
   (`nora/policy.py:457`). A gate enabled without the turn it gates is refused
   as *perfectly correct and completely inert* (`nora/config.py:3150`).

7. **An instruction to a model is not a guarantee.** The doctrine carries no
   `R-<n>` references at all; exactly one doctrine claim has code behind it.
   `nora/agent/egress.py:33` states the position: *a doctrine instruction is a
   request to a model, and this codebase has now been bitten four separate times
   by things that were true in intent and false in fact.* Anything that must
   hold is a guard, a validator or an AST test — never prose.

8. **A measurement taken through a proxy is not a measurement.** Retraction is
   normal operation here, and the pattern is consistent: what gets withdrawn is
   almost always a number derived from a stand-in — a grep, a `getattr` default,
   an index row, a doc's own copy of a table — and the conclusion survives on a
   narrower, re-derived basis. Instruments exist to catch the class: the mutation
   sweep proves a fix's test goes red; the decision-index gate carries a positive
   control so a zero-finding run cannot mean *nothing looked*.

## The frontier — where intended reality exceeds built reality

**The enforcement is voluntary.** This project's whole identity is mechanical
enforcement, and nothing runs it. `.github/workflows/` does not exist: 5,570
tests, 37 architecture tests and six import contracts fire only when a human
types `make gate`. The git hooks check commit-message grammar and nothing else.
`A12` records this as the standing decision, and the consequences are already
measurable — `R-85` cites a test node that does not exist, twelve enforced rules
are absent from the index, `AGENTS.md` names a PII gate that is not on disk, two
production modules cite a `test_module_size.py` that was never written, and
`nora.agent` — the package holding the guards and the egress — is not
type-checked. The single largest recurring issue class in the tree is *"a thing
that claims to enforce something enforces nothing."* It recurs because the
outermost enforcer is absent.

**Three moves are underway toward closing it**, and together they are the
forward vector:

- **Adopt UACP as the project's control plane** rather than inventing a
  convention per bundle. The ad-hoc rule is the failure mode one level up:
  conventions improvised inside each design bundle cannot bind the next one.
- **One observability layer, three consumers.** A trace is one inbound message;
  application spans go to Jaeger, LLM spans to Langfuse, errors and performance
  to Sentry. Today a hand-rolled tracer emits attributes and no events beside the
  SDK's own, so half the model calls are invisible to the tools meant to read
  them — and the same gap disables both Langfuse rendering and SDK-native
  evaluation.
- **Evaluation built on `strands-agents-evals`**, not re-derived. Three of the
  four primitives a hand-rolled harness invented already ship in a dependency
  that has been pinned the whole time.

The direction is the same in all three: **stop hand-rolling the thing that
checks the work, and let the check be something the project cannot quietly
opt out of.**
