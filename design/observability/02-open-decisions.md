# Open decisions and unimplemented follow-ups

Three categories, kept apart on purpose: §1–2 are decisions nobody has made
yet. §3 is done. §4–6 are decisions **already made** (by Miles, in a prior
session) that have not been wired into code — a punch list. §7 is itself
undecided, weighed but not resolved. Don't collapse these categories; an
earlier draft of this file said "§2–5 are already-made decisions," which was
wrong at both ends (§2 wasn't written yet and old-§5 was never decided) —
restated correctly here.

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
| C — collector-level masking (new OTel Collector stage) | a new always-on service + its own silent-misconfiguration failure mode; the only *reachable* option that also covers `system_prompt` (D buys roughly the same but needs a rewrite, see below) |
| D — Langfuse SDK `mask=` | not reachable without a rewrite — Nora exports through raw OTel, not the Langfuse client |

`design/13` §6's recommendation is **B if a decision is needed today, C if
traces are meant to stay useful for evaluation** — and it does not present
that as a settled two-way tie. Its own next sentence: *"the honest answer is
that the native lever is too blunt to be the end state… if traces are
load-bearing for evaluation, C is the option that actually does what is
wanted."* An earlier draft of this file dropped that qualifier and read as
more balanced than the source. Restated with it here: **the source leans C**,
and treats B as the cheap interim, not the destination.

Doing nothing is explicitly defensible *given* the current deployment shape —
a self-hosted collector on `127.0.0.1` holding messages already sitting in
`data/messages.db` on the same disk — and stops being defensible the instant
`endpoint` points anywhere off-host. §5 below turns that precondition into a
punch-list item rather than leaving it as an unmonitored fact.

**Provenance note on "not blocking."** Session context prior to this bundle
records that Langfuse and Jaeger are both localhost-only and no data leaves
the machine, and that this has kept the decision from being treated as
blocking. That context predates the UACP-run revert this bundle's `_index.yaml`
records, and is carried forward from the session record rather than
re-confirmed as a standing instruction — if this decision is finalized on that
basis, reconfirm the "localhost only, nothing off-host" premise with the
operator first rather than treating this paragraph as authorization.

## 2. OPEN — redaction is enforced by convention, not by a test

Raised in `00-overview.md` §4 and `01-implementation.md` §2's closing
paragraph; recorded here as the decision it actually is.

Today, the obligation to call `trace_safe` before passing `prompt=`/
`completion=` to `generation()` sits entirely on the caller — `nora.obs`
cannot import the screen (the layered-contract reason in `00-overview.md` §4),
so nothing checks that a new call site remembers to screen before it reaches
`generation()`. The two ingest call sites currently pass this test only
because neither sends real content in the first place (`01-implementation.md`
§3) — that is a fact about what they happen to send today, not a structural
guarantee.

An injected port — `nora.obs.tracing` declaring a `Protocol` for a screener
and taking it as a parameter, the same pattern already used for
`StandingReader` and `_PlaceSource` elsewhere in this codebase — would close
this gap without violating the layered-import contract (no concrete import of
`nora.agent` is needed for a structural type) and would make the obligation
testable: a fake screener could prove a call site can't bypass it.

**Not decided.** The cost is real: every `generation()` call site would need
to pass a screener parameter, for the benefit of the one call site
(`debrief.py`) that currently uses it. Whether that plumbing is worth it
before a second call site ever needs to send real content is the operator's
call, not something this bundle resolves.

## 3. DONE (2026-09-19) — `include_local_variables=False` on Sentry

**Decision** (prior session): keep Sentry, but its default frame-locals
capture is wrong for this codebase — a stack trace's local variables are
exactly where unredacted room content ends up if an exception fires with a
transcript still in scope, and that is a second, uncontrolled egress path next
to the one `trace_safe` exists to guard.

**Wired** in `nora/obs/errors.py`'s `sentry_sdk.init(...)` call (line 125).
The fix also corrected a mistaken assumption already live in the code and its
test: the existing comment and `tests/unit/test_errors_reporting.py` both
asserted that `send_default_pii=False` already kept a frame's local *values*
off the wire. Checked against the installed SDK (`sentry-sdk==2.44.0`,
`consts.py`): `include_local_variables` defaults to `True` and is independent
of `send_default_pii`, which only gates broader request/user PII — so the
threat the comment named was never actually mitigated until this change.

**Verification, stated precisely rather than as "passes."** The test extra
that installs `sentry_sdk` (`sentry`) is not part of the documented dev setup
(`uv sync --extra evals` — `Makefile:21`, `AGENTS.md`), and there is no CI
running these tests either way (`.github/workflows/` does not exist). Under
the default setup, both `sentry_sdk`-dependent tests in
`tests/unit/test_errors_reporting.py` are **skipped**, not passing — the
fix's own audit caught this. Re-run with `uv sync --extra evals --extra
sentry`: `7 passed, 0 skipped`, confirming `include_local_variables` and
`send_default_pii` are pinned as two independent assertions
(`test_errors_reporting.py:110-119`). The `sentry` extra was not made part of
the default dev setup as part of this fix — a future session touching this
file should install it explicitly to get a real green rather than a skip.

The released fragment for this fix is typed `security`
(`release-fragments/unreleased/sentry-frame-locals-were-never-redacted.json`),
not `fix` — an unintended PII egress that was live in production is this
repo's `security` case per `release-fragments/README.md`.

## 4. DEFERRED, RECORDED — Sentry has no release attached

`scripts/build-image.sh` builds unstamped on purpose (`--build-arg
NORA_VERSION=`, "deployments are not releases"), so an issue cannot answer "is
this new since the last deploy." `scripts/release.py` already stamps a value;
wiring it through `setup_errors(release=...)` (`nora/__main__.py:64-76`) is
the named follow-up, not yet done. Full incident detail —
`docs/observability.md`, Known Gaps.

## 5. TODO — an off-host `endpoint` detector for §1's precondition

§1's "doing nothing is defensible" argument rests on `endpoint` staying
`127.0.0.1`. Verified: nothing in `nora/config.py` or `nora/obs/tracing.py`
checks that precondition today — a config change to an off-host endpoint
would silently invalidate the confidentiality argument with no warning
anywhere. A three-line guard in `setup_tracing()` (log a warning, or record a
metric, when `exporter == "otlp_http"` and the resolved host is not
loopback/`host.docker.internal`) would make the "not blocking" position
self-policing instead of resting on nobody changing a config file. Not filed
as a GitHub issue yet — recorded here first since it is directly downstream
of §1's open decision, not independent of it.

## 6. CLEANUP, NOT YET DONE — debug spans in the production Langfuse project

`probe2`, `in-container-probe`, `nora-plain-span`, `fanout-test-span-2` and
others were still present in the production project as of 2026-09-09
(`docs/observability.md`, Known Gaps). No functional cost; a filed cleanup.

## 7. PORT-SHAPE DECISION, DEFERRED — vision reads cost zero tokens

`nora.infra` may not import `nora.obs`, so the vision client that answers a
media-comprehension call cannot annotate its own span with usage — recorded
structurally in `tests/architecture/
test_raw_model_calls_carry_their_own_machinery.py`'s `REQUIRED` map, which
exempts `nora/infra/vision/client.py` from the `usage` requirement it places
on the relevance gate. The alternative is widening `MediaDescription.
provenance` (which already travels the result back out) to carry token counts
so the caller can annotate. Weighed against "usage belongs on the span, never
in a return type" and left unresolved.
