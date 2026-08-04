---
kind: design
title: "Nora — Decision Index"
description: "One line per decision, across all eight identifier namespaces (D*, A*, R-*, DEC-*), pointing at the document that made it and the test that enforces it."
tags: [nora, design, decisions, index]
timestamp: '2026-08-04'
edges:
  - {dst: 00-overview, rel: indexes, provenance: asserted}
  - {dst: ../docs/superpowers/specs/2026-08-03-nora-strands-spec.md, rel: indexes, provenance: asserted}
  - {dst: ../docs/superpowers/specs/2026-08-04-nora-architecture-design.md, rel: indexes, provenance: asserted}
---

# Decision Index

Decisions live inside long documents under eight identifier namespaces with no
index (`D*`, `A*`, `R-*`, `S*`, `C.*`, `E.*`, `M*`, `DEC-*`), and production
modules used to cite section numbers of a spec since superseded. Rules are
versionless; section numbers are not. This file is the index for the four
namespaces that carry live, enforced decisions: `D*` (topology), `A*` (audit
amendments), `R-*` (guardrail rules), `DEC-*` (architecture). `S*`, `C.*`,
`E.*`, `M*` are structural/procedural section labels inside
`docs/superpowers/specs/2026-08-03-nora-strands-spec.md`, not decisions in
their own right, and are not indexed here.

See `ARCHITECTURE.md` for the map this index supports, and each identifier's
defining document for the full rationale — this file is deliberately terse.

## D — Topology (`design/00-overview.md`)

| ID | Statement | Enforcing test |
|---|---|---|
| D1 | Ambient ingest never passes through the Agent SDK; the SDK is constructed, invoked, and discarded once per tag. **Amended by A3** (see below): the ambient path may later host a lightweight relevance classifier, still not the full Agent SDK. | `test_ambient_never_constructs_agent` (R-1) |
| D2 | SQLite is an audit record, not the semantic store; Honcho is the semantic store (peer cards, relationships, durable recall). | no dedicated test — a schema-level property |
| D3 | The Strands Agents SDK is the tagging-path engine — no hand-rolled loop, context builder, or session store. | `test_strands_import_boundary` (structural: only `nora/agent/**` may import `strands`) |
| D4 | Every tag is a fresh, stateless agent invocation; continuity comes from SQLite + Honcho pre-filled into context, never SDK session persistence. **Amended by DEC-3**: the injection invariant holds, but an additive, TTL'd `Episode` concept is accepted for later — not implemented in stage 1. | no dedicated test (statelessness itself); `tests/integration/test_statelessness.py` exercises the invariant |
| D5 | Everything identity-related (trigger text, account, peer mapping, model, endpoints) is config, never hardcoded. | `tests/test_config.py` (`extra="forbid"`, no hardcoded identity fields) |
| D6 | Peer mapping is manual, one operator-maintained file — no service, no UI. **Amended by A2**: two-level mapping (`person_id` → name + per-platform sender IDs), one person = one Honcho peer across platforms. **`design/04`'s identity-discovery mechanism is further amended by DEC-9**: identity discovery moves onto the `PlatformAdapter` port (`resolve_identity()`), not read from a private adapter attribute. | `test_self_tag_ignored` (R-12, depends on identity resolution); `tests/test_persons.py` |

## A — Audit amendments (`design/00-05*.md`)

Numbered by the 2026-08-03 audit pass. A7 does not appear anywhere in the
design corpus — not an omission here, just never assigned.

| ID | Statement | Defining doc | Enforcing test |
|---|---|---|---|
| A1 | Platform abstraction seam (`PlatformAdapter`) so new platforms are extensions, not rebuilds. | `design/00-overview.md` §Platform Abstraction, `design/01-ambient-ingest.md` | none dedicated — `SignalAdapter`'s gap to the port is pinned by `test_signal_adapter_gap_to_platform_adapter_is_known` |
| A2 | Two-level peer mapping: `persons:` (`person_id` → name + per-platform sender IDs); one person = one Honcho peer across platforms. Amends D6. | `design/00-overview.md`, `design/04-config-identity.md` §Peer Mapping | `tests/test_persons.py` |
| A3 | The ambient path may later host a lightweight relevance classifier (a single cheap completion, not the Agent SDK) replacing the boolean trigger gate. Amends D1 in principle; deferred in practice — a future milestone, `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])` per DEC-1's staging (spec §7). | `design/00-overview.md`, `design/02-tagging-path.md` §True Ambient Hooks | not built — no test |
| A4 | Runtime isolation: dedicated system user / container, restricted egress to the three localhost endpoints, filesystem writes limited to `data/`, process supervision with auto-restart. | `design/01-ambient-ingest.md` §Environment & Isolation | none dedicated (deployment-level: `Dockerfile`, `docker-compose.yml`) |
| A5 | OTel tracing enabled by default — a config toggle, not a build flag. | `design/01-ambient-ingest.md`, `design/03-strands-reuse.md` | `tests/test_tracing.py` |
| A6 | Structured logs with per-tag correlation ID; non-optional `GET /health`; a metrics endpoint. | `design/01-ambient-ingest.md` §Observability | `tests/test_health.py`, `tests/test_logging.py` |
| A8 | Silent-failure rate (tags with no reply / total tags, target <1%) is the observability escape hatch for the silent-failure doctrine (R-5) — it makes "invisible when broken" measurable. | `design/05-evaluation.md` | metric only (`nora_silent_failures_total`); no assertion test |
| A9 | Record-first, send-after: the reply is audited (`is_nora=1`, `delivery_status=pending`) *before* `send_reply` runs, so a failed send never loses the reply from context. | `design/02-tagging-path.md` §4 | exercised by `test_silent_failure_matrix` (R-5) and `tests/test_guards_deliverer.py` |
| A10 | `strands-agents` is pinned in `pyproject.toml`; upgrades are manual and gated on the smoke test. | `design/03-strands-reuse.md` | none dedicated (a `pyproject.toml` constraint) |
| A11 | Tool-use policy: the pre-fill header states what context is already present, so the model doesn't burn a roundtrip on redundant tool calls. Feeds R-15. | `design/02-tagging-path.md`, `design/05-evaluation.md` | `test_header_states_what_is_already_present`, `test_no_redundant_read_fails_when_prefill_was_sufficient` (R-15) |
| A12 | CI fast gate (every change) runs deterministic evaluators + parser/db/tag unit tests + latency smoke. | `design/05-evaluation.md` | CI config itself (`.github/workflows/gates.yml`), not a single test |

## R-1…R-16 — Enforced guardrail rules

Source of truth for the rule statements and the assigned test names:
`docs/superpowers/specs/2026-08-03-nora-strands-spec.md` §B.1. The registry of
which named test satisfies which rule is `tests/test_acceptance_rules.py::RULE_TESTS`
— that dict, not this table, is what `test_every_enforced_rule_has_a_named_test`
checks against; this table is sourced from it, not the reverse.

| ID | Statement | Enforcing test |
|---|---|---|
| R-1 | Only the tagging path may cause a send; ambient traffic never reaches the agent. | `test_ambient_never_constructs_agent` |
| R-2 | `send_reply` executes at most once per tag, counting attempts, not successes. | `test_second_send_reply_is_cancelled` |
| R-2b | The `Deliverer` latch is single-shot, independent of `ReplyGuard`. | `test_deliverer_latch_is_single_shot` |
| R-3 | `send_reply` may only target the conversation that produced the tag. | `test_send_to_other_group_is_cancelled` |
| R-4 | Empty or whitespace-only reply text is never delivered. | `test_empty_reply_not_sent` |
| R-5 | No error text ever reaches the group — silent failure, always. | `test_silent_failure_matrix` |
| R-6 | No reply may contain the canary token or a verbatim ≥40-character span of the assembled persona text. | `test_leak_guard_blocks_canary` |
| R-6b | ...and a verbatim persona span specifically. | `test_leak_guard_blocks_persona_verbatim` |
| R-6c | A normal, non-leaking reply is never blocked by the leak guard. | `test_leak_guard_allows_normal_reply` |
| R-7 | Per-tag tool-call budget (`read_buffer` ≤ 2, `read_peer_card` ≤ 1, `send_reply` ≤ 1); unknown tools default-deny. **DEC-10** replaces the hardcoded table with budgets injected from the tool registry — not built yet; unknown-tool default-deny is preserved either way. | `test_tool_call_budget_enforced_for_read_buffer` |
| R-8 | Hard invocation ceiling per tag (`turns`, `output_tokens`, `total_tokens`). | `test_limits_passed_through` |
| R-9 | Debounce: a second tag for the same conversation within `min_interval_seconds` is dropped. | `test_debounce_window` |
| R-10 | In-flight lock: at most one tag per conversation; released on every exit path including timeout/error. | `test_concurrent_tags_same_conversation` |
| R-10b | ...released specifically on timeout. | `test_lock_released_on_timeout` |
| R-10c | ...released specifically on an LLM error. | `test_lock_released_on_llm_error` |
| R-11 | Trigger validation — only genuine trigger text fires (word-boundary, code-span-aware, Unicode-normalized). | `test_trigger_matrix` |
| R-12 | Nora never triggers herself — `is_nora=1` rows and her own `sender_id` can never fire the trigger. **DEC-9** moves the identity source from a private adapter attribute (`adapter._account`) to `resolve_identity()` on the port, so this becomes platform-generic instead of Signal-specific. | `test_self_tag_ignored` |
| R-13 | All group-derived text is XML-escaped and enclosed in labelled untrusted blocks. | `test_prefill_escapes_angle_brackets` |
| R-13b | ...even a forged closing tag inside the text. | `test_forged_closing_tag_is_escaped` |
| R-14 | Prompt-injection behaviour (never reveal/paraphrase persona, config, keys; never adopt another identity). Behavioural — asserted by `guardrails.md`, backstopped mechanically by R-6, measured by the adversarial corpus. No hook can enforce a behavioural rule. | `evals/adversarial/*.yaml`, pinned present by `test_r14_is_covered_by_the_adversarial_corpus` |
| R-15 | Anti-redundant tool calls — the pre-fill header states what context is already present. | `test_no_redundant_read_fails_when_prefill_was_sufficient` |
| R-16 | The LLM API key value never appears in a log line, trace attribute, exception message, or `raw_json`. | `test_secret_never_logged_in_the_message` |

## DEC-1…DEC-13 — Architecture decisions (`docs/superpowers/specs/2026-08-04-nora-architecture-design.md`)

| ID | Statement | Enforcing test |
|---|---|---|
| DEC-1 | Pipeline + ports, light layering: composable stages behind `Protocol` ports, one composition root, registries from config. | structural — no single test; `nora/ingest/pipeline.py::IngestPipeline` (five stages) and `nora/engagement/pipeline.py::EngagementPipeline` (admission/context/invoke/deliver/feedback) are the pipelines this decision names, `nora/app/composition.py::build_dependencies` is the one composition root that wires them |
| DEC-2 | Hot path (Honcho, SQLite audit, platform send) is native; cold path (Maps, web search) is MCP. | not built (MCP providers are deferred, spec §11) |
| DEC-3 | Stateless invariant holds (amends D4); `Episode` (bounded, TTL'd working state) is **accepted but NOT implemented** in stage 1 — only the `ContextAssembler` port ships now. | `ContextAssembler` shape: `tests/test_ports.py`; no `Episode` test exists because no `Episode` exists |
| DEC-4 | Failure disclosure policy refining R-5: `INFRASTRUCTURE`/`GUARD_VETO` stay silent, `REQUESTED_TOOL` failures are disclosable opt-in per tool, with a separate counter. | `tests/test_domain_errors.py` (`FailureClass`, `NoraError`) |
| DEC-5 | Persona stays in git, not Langfuse-hosted; Langfuse is for traces and scores. Trace↔prompt linkage (`persona_sha256` on every span) is already implemented. | `tests/test_tracing.py`, `tests/test_persona_*` |
| DEC-6 | `strands-agents-evals` is the runner; Langfuse is the store for results, datasets, and dataset runs. | `tests/test_evals_*` (runner side); Langfuse wiring not built |
| DEC-7 | SQLAlchemy Core + Alembic, not Tortoise ORM — one table, no relations, no joins. | `tests/infra/test_alembic.py`, `tests/infra/test_audit_store.py`, `tests/contract/test_audit_store_contract.py` |
| DEC-8 | `config.toml` + `identity.toml`, both TOML; no `${VAR}` interpolation — secrets referenced by `*_env` naming. | `tests/test_config.py`, `tests/test_env_template.py` |
| DEC-9 | Adapters own platform identity discovery (`resolve_identity() -> PlatformIdentity`, `aliases: frozenset[str]`). Amends `design/04`'s mechanism (a private adapter attribute) with a port method. Nora's own account is never written to identity.toml. | `test_platform_identity_holds_aliases_not_one_id`; `SignalAdapter` does not implement `resolve_identity()` yet — pinned as a known gap by `test_signal_adapter_gap_to_platform_adapter_is_known` |
| DEC-10 | `ReplyGuard` takes per-tool budgets from the tool registry instead of a hardcoded table, so a new tool (or `end_episode`) isn't rejected before it runs. Unknown-tool default-deny is preserved. | not built (agent/tool-registry phase) |
| DEC-11 | `alembic check` alone is not the schema gate **[proven]** — blind to CHECK constraints and `DESC` ordering (its original claim of blindness to server defaults was **corrected 2026-08-05**: it does catch those). A dedicated schema-parity test against `sqlite_master.sql`/`PRAGMA index_xinfo` is the real guarantee. | `tests/infra/test_schema_parity.py` |
| DEC-12 | Mechanically enforced boundaries: mypy on `nora/domain`+`nora/config`+`nora/app`+`nora/infra` (hand-expanded flags equivalent to `--strict`, not the `--strict` shorthand itself — see note below), import-linter (domain framework-free; domain is the bottom layer of a `layers` contract; only `app`/`__main__` read config), ruff `select`, and contract tests covering 3 of the 5 ports (`PlatformAdapter`: a known-gap pin, not a conformance assertion; `MemoryStore`: method names only, not signatures; `AuditStore`: a real contract suite covering two implementations, `SqlAuditStore` and `FakeAuditStore`, in `tests/contract/test_audit_store_contract.py`). `EngagePolicy` (`nora/ingest/policy.py::TriggerTextPolicy`) and `ContextAssembler` (`nora/engagement/context.py::TranscriptInjection`) have concrete implementations as of the pipelines phase, but still no `tests/contract/` suite of their own — conformance for both is checked statically only, via `nora/app/composition.py::Dependencies`'s typed `policy`/`assembler` fields inside mypy's strict `nora.app` override. | `uv run mypy ...`, `uv run lint-imports`, `tests/test_boundaries.py`, `tests/contract/test_port_conformance.py`, `tests/contract/test_audit_store_contract.py` |
| DEC-13 | **[deferred to phase 6, recorded 2026-08-05]** Spec §4.5 names four module-level mutable singletons this refactor was to delete: `nora/agent/factory.py`'s `_CLIENT` (the process-wide `AsyncOpenAI` client) and `nora/obs/metrics.py`'s `METRICS`/`DECLARED`/`_BUCKETS`. Only `close_client_sync()` — the test-only helper CLAUDE.md's test-seam policy forbids outright, used solely by `tests/test_factory.py` — was deleted in this pass (`tests/test_factory.py` now resets via the real async `close_client()`). `_CLIENT` itself, and `METRICS`/`DECLARED`/`_BUCKETS`, stay: phase 6 (spec §11, "structlog/prometheus-client/OTLP") is where `METRICS` is replaced by `prometheus_client`'s own registry, which is the natural point to delete `DECLARED`/`_BUCKETS` alongside it and to decide `_CLIENT`'s replacement (likely an explicit lifecycle object threaded through `Dependencies` rather than a module global). Deleting them now, ahead of that swap, would mean rebuilding the same seam twice. Every new **engagement** call site (`pipeline.py`, `context.py`, `deliver.py`, `invoke.py`) now takes `metrics` as a required parameter — no `= METRICS` default — matching `nora.ingest`'s stages already did; only the module-level singleton itself (`obs/metrics.py`'s `METRICS = Metrics()`) is the deferred part, not the injection discipline at call sites. | not built — the deferral itself has no test; the five call sites' `metrics` parameter is now required, checked by every existing call site (`tests/unit/test_engagement_*.py`, `nora/app/composition.py`, `nora/app/offline.py`) passing it explicitly |

**On "mypy `--strict`":** `pyproject.toml`'s `[[tool.mypy.overrides]]` for `nora.domain.*`/`nora.config`/`nora.app.*` deliberately hand-expands `--strict`'s flag list instead of setting `strict = true`, working around a confirmed mypy 2.3.0 bug where `strict = true` inside an override section leaks strict mode to every module in the run, not just the matched ones (see the comment above that override in `pyproject.toml`). This is a frozen snapshot of what `--strict` meant as of mypy's pinned version — it must be re-diffed against `--strict`'s actual flag set on every mypy upgrade, or the override silently drifts from what "strict" means in the new version.
