# Open decisions and unimplemented follow-ups

Two different categories, kept apart on purpose: §1 is a decision nobody has
made yet. §2–5 are decisions **already made** (by Miles, in a prior session)
that have not been wired into code. Do not treat §2–5 as open questions — they
are a punch list.

## 1. OPEN — the redaction lever (`design/13-trace-content-and-redaction.md`)

**Status: open-decision since 2026-08-06, still unresolved.** Full
investigation, the live-probe evidence, and the four-option cost table live in
that file — this is the summary, not a replacement for it.

The group transcript reaches the Langfuse exporter unredacted today via the
Strands SDK's own `Tracer` (the tag-turn path, §3 of `00-overview.md`), which
is a config surface (`OTEL_SEMCONV_STABILITY_OPT_IN=
gen_ai_unredacted_attributes=<list>`) with real limits: it is an allowlist over
three keys (`gen_ai.input.messages`, `.output.messages`,
`.system_instructions`), the input key covers the group transcript, tool
arguments, and tool *results* as one policy — you cannot keep tool-call
visibility and drop the transcript — and it **cannot reach** the
`system_prompt` attribute the Agent span carries as a plain `**kwargs` extra
outside the redaction path entirely (`design/13` §4).

Four options, costs in the source file:

| option | what it costs |
|---|---|
| A — redact everything | traces become structure-only: span tree, tool names, tokens, latency, no strings, `system_prompt` still leaks |
| B — outputs only | tool *results* disappear too (policed under the same input key); `system_prompt` still leaks |
| C — collector-level masking (new OTel Collector stage) | a new always-on service + its own silent-misconfiguration failure mode; the only option that also reaches `system_prompt` |
| D — Langfuse SDK `mask=` | not reachable without a rewrite — Nora exports through raw OTel, not the Langfuse client |

Recommendation in `design/13`: **B if a decision is needed today, C if traces
are meant to stay useful for evaluation.** Doing nothing is explicitly
defensible *given* the current deployment shape — a self-hosted collector on
`127.0.0.1` holding messages already sitting in `data/messages.db` on the same
disk — and stops being defensible the instant `endpoint` points anywhere
off-host, a condition nothing in the code currently detects.

Miles's stated position (this session, prior to UACP removal): Langfuse and
Jaeger are both localhost-only, no data leaves the machine, so this has not
been treated as blocking. This bundle does not close the decision — it holds
it here so the next session opens `design/13` instead of re-deriving the
tradeoff table from scratch.

## 2. DONE (2026-09-19) — `include_local_variables=False` on Sentry

**Decision** (prior session): keep Sentry, but its default frame-locals
capture is wrong for this codebase — a stack trace's local variables are
exactly where unredacted room content ends up if an exception fires with a
transcript still in scope, and that is a second, uncontrolled egress path next
to the one `trace_safe` exists to guard.

**Wired** in `nora/obs/errors.py`'s `sentry_sdk.init(...)` call. The fix also
corrected a mistaken assumption already live in the code and its test: the
existing comment and `tests/unit/test_errors_reporting.py` both asserted that
`send_default_pii=False` already kept a frame's local *values* off the wire.
Checked against the installed SDK (`sentry-sdk==2.44.0`, `consts.py`):
`include_local_variables` defaults to `True` and is independent of
`send_default_pii`, which only gates broader request/user PII — so the
threat the comment named (an ACI or phone number in a local variable) was
never actually mitigated until this change. Test updated to assert both flags
separately; `tests/unit/test_errors_reporting.py` passes.

## 3. DEFERRED, RECORDED — Sentry has no release attached

`scripts/build-image.sh` builds unstamped on purpose (`--build-arg
NORA_VERSION=`, "deployments are not releases"), so an issue cannot answer "is
this new since the last deploy." `scripts/release.py` already stamps a value;
wiring it through `setup_errors(release=...)` (`nora/__main__.py:64-76`) is the
named follow-up, not yet done.

## 4. CLEANUP, NOT YET DONE — debug spans in the production Langfuse project

`probe2`, `in-container-probe`, `nora-plain-span`, `fanout-test-span-2` and
others were still present in the production project as of 2026-09-09
(`docs/observability.md`, Known Gaps). No functional cost; a filed cleanup.

## 5. PORT-SHAPE DECISION, DEFERRED — vision reads cost zero tokens

`nora.infra` may not import `nora.obs`, so the vision client that answers a
media-comprehension call cannot annotate its own span with usage; the
alternative is widening `MediaDescription.provenance` (which already travels
the result back out) to carry token counts so the caller can annotate. Weighed
against "usage belongs on the span, never in a return type" and left
unresolved; recorded next to the test exemption it justifies in
`tests/architecture/test_raw_model_calls_carry_their_own_machinery.py`.
