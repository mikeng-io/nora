# Reviewing Nora

Review answers: **does this candidate satisfy the agreed change, and what
real regression could it introduce?** It returns a scoped judgment and
evidence — not another design, a task list or merge permission.

[ORCHESTRATION.md](ORCHESTRATION.md) owns tiers, independence, dispositions
and convergence, [AGENTS.md](AGENTS.md) engineering constraints and
[nora-review](.agents/skills/nora-review/SKILL.md) the procedure. All
reviewers share this contract — shaping behavior, never enforcement or paid-review admission.

## Scope and inputs

The launch message's short brief — not a new document — is the input;
an automatic reviewer without one derives and states it, and missing intent remains a limitation.

    Question   what this review must decide; change / repair re-check / acceptance audit
    Basis      original outcome and completion conditions, approved amendments; source revision and base/head
    Surface    changed behavior and affected callers; prior findings for a re-check
    Lenses     relevant risks and the concrete question for each; tier is depth
    Evidence   relevant receipts and allowed checks; what would disprove the claim
    Exclude    unrelated areas, settled decisions and action/spending limits

The same outcome, conditions and boundaries carry through repairs — in
the brief, not an archive link or the latest findings; findings do not
replace acceptance. An authorized amendment names what changed and what it
reopens — else the anchor holds. A re-check names its subset; conditions
outside it are not newly reviewed or implicitly passed.

Lenses follow what changed and can break, never a fixed panel —
application, security/privacy, data/concurrency, measurement/operations —
and ORCHESTRATION.md's risk floors.

## Findings and return

Admit a defect only with basis, reachability and consequence — the
violated requirement, contract or trust boundary, a real entry or
attacker path, concrete harm — and name the agreed condition left unmet
or boundary regressed; a new capability is no repair requirement. NONE
is valid. Severity — P1 material correctness, safety or availability
failure, P2 bounded impact — is the reviewer's; disposition, verdicts,
classes and convergence are the policy's.

Lead with APPROVE or REQUEST CHANGES for the reviewed scope, then base/head,
type/tier and reviewer vendor/independence where required. Return only
coverage (each condition met / unmet / unavailable with an evidence pointer;
group only matching results), findings (severity and disposition, file:line,
trigger → harm, failed C, evidence, smallest remedy, introduced /
pre-existing / repair-induced) and limits (uninspected areas and required
evidence gaps). REQUEST CHANGES names a supported blocker or a required
evidence gap; APPROVE is not task completion, green CI, merge or deployment.
