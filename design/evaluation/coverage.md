# Scenario coverage

Coverage follows failure modes and user-visible risk, not which assertions are
cheap to write. Deterministic checks cover what code can decide; live model
runs cover interpretation that requires the real subject and its trajectory.

## Classes

- **Room and target** — delivery lands in the intended room and addresses the
  intended person, including cross-platform and deferred occasions.
- **Tool routing** — the selected tool, arguments and order answer the actual
  request without substituting an easier read or write.
- **Grounding** — claims are supported by the cited evidence, preserve
  attribution and uncertainty, and do not overrule a newer source with an older
  summary.
- **Context** — required blocks render non-empty with the right subject, time,
  identity and state; the subject is not reasoning from a reduced prompt.
- **Time** — dates, ages, deadlines and recurrence resolve against the case's
  frozen clock and the source's assertion time.
- **Idempotence** — a repeated drive does not duplicate a note, reminder,
  delivery or other persistent act, including ambiguous outcomes.
- **Speech and silence** — the turn speaks, acts or stays silent as intended,
  with an explicit positive signal that it completed.
- **Failure visibility** — broken tools and providers produce bounded, legible
  failures rather than silent wrong answers or endless retries.
- **Injection resistance** — untrusted text cannot become instructions,
  override context boundaries or forge a commitment.
- **Disclosure and custody** — content that should not leave does not escape
  through a reply, export, tool argument, trace, cache or derived memory.
  This is the outward counterpart of injection resistance.

A class earns cases from observed failures or from a failure that would be
silent and consequential. Case counts alone do not prove coverage: duplicated
near-misses and reply-expecting smoke checks cannot crowd out the classes that
real incidents keep reaching.

## Choice of instrument

Room targets, context, time and idempotence are primarily deterministic and
can run without model spend. Tool routing uses the actual trajectory. Grounding
and injection need independent semantic judgement. Speech and silence use the
entry point's typed outcome. Failure and disclosure require negative controls
as well as successful cases.

Anchored scenarios preserve the source conditions that make the expectation
meaningful. Counterfactuals are for variables no real moment can supply, not
for manufacturing easier passes. Parked and unanchored cases remain visible in
coverage reports so the suite cannot appear broader than what actually ran.

## Before and after

An eval confirms a built change against a baseline on the same anchors and
measurement path. It does not discover the cause of a failure or compare
incomparable historical numbers. Source revision, model, doctrine,
configuration, sample and evidence digests travel with the result.

A useful negative result identifies the smallest responsible contract to
reopen. A green run with missing anchors, unmapped trajectories, unavailable
judges or changed source semantics is not a valid before/after comparison.
