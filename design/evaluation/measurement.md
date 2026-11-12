# Measurement and verdicts

## SDK-native harness

`strands-agents-evals` owns `Case`, `Experiment`, reports, content evaluators
and tool-trajectory evaluators. Nora owns the subject, isolation and any
measure the SDK cannot express, principally intended absence. Adding a suite
adds cases, not another runner.

The task returns typed output, environment state and an SDK-mapped `Session`.
The session preserves actual model input, output, tools and invocation
boundaries. If a component does not produce the required invocation shape,
trajectory evaluators cannot grade it; that is an instrument limitation, never
a zero-score behaviour claim.

The task never raises into the harness. Exceptions are caught, scrubbed of
connection secrets and returned as error data because the SDK can otherwise
turn an unrun task into an ordinary failed score.

## ERROR and grading

`test_pass` is boolean and cannot carry the third state. ERROR therefore rides
in `label`, and every aggregate reads it. A failure to compose, capture,
restore, reach a model, settle a turn, match an event or grade a response is
ERROR, not FAIL and never PASS.

Absence grading reads the entry point's own completion signal: gate admission,
delivery or deliberate silence, a completed empty Dreamer outcome, or the
corresponding explicit result. A crash, timeout or unreachable provider that
also emits nothing is ERROR. The absence grader never treats an empty string
as proof of restraint.

A content judge is a different model from the subject and may only remove a
pass. It evaluates the claims and safety property against the exact bounded
source the subject saw, not the whole database or a reply's fluency. Missing
judge evidence, identity, output or reasoning is unavailable, not a verdict.

## Sampling and stability

Repeated draws have unique identities and are grouped back to their base case.
Agreement can support PASS or FAIL; disagreement is UNSTABLE. All-error is
ERROR. A mean over draws cannot replace the case verdict or hide an instrument
failure in the denominator.

Timing, model and served-provider identity, cache usage, finish reasons and
tool calls are recorded per draw. These distinguish a real call from a gateway
cache and expose when the measured answer did not come from the configured
subject. Result caching must not collapse repeated draws or conceal their
variance.

## Evidence and privacy

Capture includes formatted requests, responses, tool calls and refusals,
terminal outcome, effective budgets, source revision, snapshot, instruction
and configuration digests, and judge inputs. Complete trajectories explain why
a statement was saved or missed; tool-call counts alone cannot.

Full content remains in the private artifact with restrictive filesystem
permissions. Public reports carry fixed causes, counts and opaque references.
Production content never enters public receipts, stdout, CI uploads or
non-private exporters. A redacted or missing reasoning channel yields
insufficient evidence, not an imagined explanation.

## Cost and quality are both measured

A verdict records execution and maintenance cost as well as quality: latency,
input/output/cached tokens, external calls, read bounds and judge spend.
A cheaper prompt or faster path is not a win if required context or grounded
quality falls. A passing reply is not evidence that the instrument can measure
the property at all; positive controls and deliberate broken runs establish
that first.
