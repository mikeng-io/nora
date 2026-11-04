# Nora roadmap review — 2026-11-02

## Scope and evidence

Operator brief: review the preceding one to two weeks of design, surface useful
capability/business directions, connect planning and completion skills, and
retire Nora Engineering. This changes planning documents and Project metadata only;
it implements none of the capabilities below, starts no paid model/benchmark run,
and changes no production flag, account, private configuration or data.

Source snapshot: `8485a8768ad0c74f370a28a021bf3239d355edaa`.
Window: 2026-10-19 through that snapshot on 2026-11-02, selected by git commit
time, not design frontmatter. The appendix inventories every changed design path,
including deletions/renames. Review used current bundle intent, migration and
acceptance, dated supersession notes, design commit history, existing open/closed
issues, and selected implementation seams. This is a portfolio/design review,
not a line-by-line correctness audit of every historical design or a production
acceptance report. Runtime, enablement and longitudinal model quality are not
revalidated here. A design's stale `not-built` field is not implementation proof.

## Capability directions

Each row maps to an outcome issue in the Nora Roadmap view. New homes are
[memory #518](https://github.com/mikeng-io/nora/issues/518),
[continuity #519](https://github.com/mikeng-io/nora/issues/519),
[Pi worker #520](https://github.com/mikeng-io/nora/issues/520),
[personalisation #521](https://github.com/mikeng-io/nora/issues/521) and
[Telegram #522](https://github.com/mikeng-io/nora/issues/522).
Source delivery and outcome acceptance remain different. New unscheduled ideas
start Proposed/Later; existing benchmark planning remains Planned/Next. Now
identifies current integration/validation attention, not a new execution order.

| Direction and user value | Canonical design / existing home | Disposition and next evidence |
|---|---|---|
| One memory and Cognition: recall, connect and revise understanding across turns | [one-session build](../../design/unified-memory/31-build-one-session.md), [graph](../../design/unified-memory/27-the-memory-is-a-graph.md), [Cognition](../../design/unified-memory/29-one-cognition-session.md) | Own outcome, In progress/Now. Recent source delivery includes graph, Register and Profile work; the November 2 snapshot explicitly leaves semantic longitudinal evidence, capture reliability, historical backfill preparation and cutover acceptance open. Do not make public benchmarks a cutover prerequisite. |
| Honest external memory-quality baseline | [existing outcome #435](https://github.com/mikeng-io/nora/issues/435), children #429–431 | Reuse Planned/Next. Keep LongMemEval-S → GroupMemBench → Mem2ActBench, one native delivery milestone, no duplicate umbrella in its count. No full-run result is claimed. |
| Continuity of decisions, arrangements and actual actions across rooms | [Notebook](../../design/notebook-memory-types/00-design.md), [one Standing](../../design/notebook-memory-types/20-standing.md), [turn records](../../design/standing-ledger/30-turn-records.md) | Own outcome, In progress/Now. Standing consolidation and typed Profile have merged source. Remaining scope is full memory-type migration and end-to-end continuity/actual-action evidence; never restore a separate shared-state block, conclusion tool or another cognitive writer. |
| Multi-step computer work delegated to Pi, with switchable worker and sandbox | [agent-worker intent](../../design/agent-worker/00-intent.md), [adapters](../../design/agent-worker/20-adapters.md), [sandbox port](../../design/agent-worker/30-sandbox-port.md), [verification](../../design/agent-worker/50-verification-and-migration.md) | One outcome, design reconciliation first (operator, 2026-11-04; issue #520); implementation not started. Proposed/Now reflects current design attention. Next scope: one VM per unit/canonical private-DM scope, reusable anonymous Chrome task tabs and direct screenshots with executor/model selection explicit before implementation; reuse of CDP/Flash is recommended, Jev/cua-s1 remain unselected alternatives. Existing Strands subagent stays Nora; Pi is the computer worker inside the sandbox. Streamed spawn, measured Pi adapter and scoped relay are delivery slices; ACP and another VM provider are later extensions, not new parallel projects. Current sbx microVM is not a future unbuilt capability. |
| Personalised replies and useful group recommendations | [personalisation](../../design/personalisation/00-intent.md), [migration](../../design/personalisation/60-migration.md) | Own outcome, Proposed/Later. Likes, constraints, one ranker, venues/games and feedback share one engine. Historical fit/recommendations bundles were renamed, not three engines to build. Refresh graph-backed inputs and Standing dependencies before a bounded venue/reply slice. |
| One private conversation across linked platforms with curated shared understanding | [existing #124](https://github.com/mikeng-io/nora/issues/124), [private DM](../../design/private-dm/00-contract.md), [cross-unit reads](../../design/private-dm/06-cross-unit-reads.md) | Reuse Proposed/Later. WhatsApp/Signal continuity, owner-map and backup prerequisites, default-unit writes and provenance-preserving publication remain the scope. Shared unit computer migration is already recorded implemented; do not repeat it as a prerequisite build. |
| Understand reactions, polls and room changes from captured evidence | [existing #433](https://github.com/mikeng-io/nora/issues/433), [observations](../../design/platform-observations/00-overview.md), [coverage](../../design/platform-observations/01-coverage.md) | Reuse Validating/Now. November 2 source includes typed observation foundation, read tools and Standing projections despite older proposed frontmatter. Complete platform-specific lifecycle/coverage and input readback; counts/reactions never become inferred agreement or consent. Telegram adoption is separate. |
| Keep receiving and recover work across application releases | [existing #422](https://github.com/mikeng-io/nora/issues/422), [bridges](../../design/platform-bridges/00-contract.md) | Reuse Validating/Now. Source includes standalone bridges/durable handoff. Retain pending failure-window, recreation, platform and installed-wrapper proof as acceptance; design frontmatter alone cannot roll delivery back to not-built. |
| Join Telegram through the same platform contracts | [observation coverage](../../design/platform-observations/01-coverage.md), [platform adoption questions](../../design/platform-observations/_index.yaml) | Own outcome, Proposed/Later. First decide bot versus client mode and required account permissions/capabilities. Then trace identity/policy, ingest, replies/media, observations and recovery end to end. No account creation, token request or implementation is authorized by this card. |
| Inspect Nora's behavior and cost across deployments | [existing #440](https://github.com/mikeng-io/nora/issues/440), [observability](../observability.md) | Reuse Validating/Now. SigNoz code migration is source-delivered; retained logs, four-producer correlation, usage/cost and installed telemetry acceptance remain evidence questions. Do not declare production cutover from merged source. |
| Consistent Mini/adult identity and usable modeled forms | [existing #245](https://github.com/mikeng-io/nora/issues/245), [representations](../../design/self-reference/07-native-and-embodied-form.md), [Mini](../../design/self-reference/09-mini08-balanced.md) | Reuse Planned/Later. Approved 2D references and recent social artwork are delivered source assets. Orthographic consistency, mesh, rig and animation remain separately measured deliverables. Retain current Mini runtime direction. |

These are candidates/ongoing directions, not authority to execute them all.
Implementation chooses a bounded slice against its canonical acceptance. Reopen
grouping when two parts have independently useful outcomes and separate acceptance;
do not split merely because there are two ports or directories.

## Complete inventory disposition

The changed paths fall into the following groups; all are accounted for without
making one roadmap card per file.

| Changed group | Treatment |
|---|---|
| unified-memory, cognition-loop, culture-tier, self-growth | Memory/Cognition outcome. Later one-session amendments supersede old separate adaptors; historical profile/vector/identity phases remain provenance. |
| notebook-memory-types, standing-ledger, dreamer-agent-loop | Continuity outcome. Retired dreamer/adaptor descriptions are history; one Standing owns current presentation. |
| agent-worker, subagent | Pi/computer-work outcome. Existing subagent and existing microVM are baseline, not the new work. |
| personalisation; removed fit and recommendations | One personalisation outcome. Renames do not create additional initiatives. |
| private-dm and old private-dm entry point | Reuse #124, latest cross-unit and publication amendments. |
| platform-observations and unified-memory observation pointer | Reuse #433; Telegram discovery is its own platform adoption outcome. |
| platform-bridges | Reuse #422; current source and future runtime receipts are separate. |
| computer-unit-scope, room-topology, self-awareness room tools, Discord notification routing | Existing unit computer/topology delivery and supporting work. Do not reopen completed multi-room migration; unresolved narrow ambiguity stays #360. |
| policy-reload and WhatsApp reload amendments | Delivered maintenance mechanism; no new capability card. |
| self-reference | Reuse #245, separate source art from actual 3D acceptance. |
| evaluation, media-read-lane briefs, tool-catalogue measurement, local-model evidence script | Enabling verification and implementation records; keep in relevant memory/worker acceptance or backlog, not standalone product promises. |
| root decision index, old stack brief, tracing/redaction | Historical cross-cutting records; current observability requirements go to #440. No resurrection of SQLite/old telemetry architecture. |
| media comprehension, grounding, presence collector, named places | Most recent changes record content-policy reversal and supporting capabilities. No speculative new initiative from those amendments. |
| version/announcement, capability-drop, Instagram read/search | Supporting delivered/tool behavior; no new roadmap umbrella from maintenance amendments. |
| old Honcho integration | Historical dependency architecture, superseded by the current memory outcome and its explicit cutover procedure. |

## Interaction and retirement

[Roadmap conventions](conventions.md#roadmap) are the maintained policy.
`nora-decision` handles planning: existing issue/design first, outcome admission,
reasoned lifecycle/horizon and native relations, then readback. `nora-land`
reconciles the linked outcome after acceptance; it never equates a merged child
with an achieved parent. Ordinary issues need no roadmap card. No extra planning
skill, database or status-sync service is introduced.

Universal Roadmap remains the cross-repository container. Its Nora Roadmap view
filters Outcome items to `mikeng-io/nora`; delivery detail remains in Deliverables.
Existing four views and all issue identities are preserved. Automatic sub-issue
addition is retained; other lifecycle automation stays off because closure is
not outcome acceptance. Start/Target dates remain uncommitted.

Retire Nora Engineering by closing it, preserving its cards and history. Update
its README and links in the benchmark parent/milestone and portfolio README.
The engineering controller and its tests remain unchanged. Its historical
board-dependent pilot is outside the roadmap flow; documentation marks it as
retired for new admission rather than introducing a replacement dispatch format.
No pilot flag is enabled and no worker is dispatched by this change.

## Five-C assessment

Coherence: capabilities have one outcome home; execution remains issues/runs.
Consistency: reuse native milestones/sub-issues and the existing two skills.
Conformance: follow the operator's Pi correction, high-level roadmap scope and
Engineering retirement; preserve no-spend/no-config boundaries.
Correctness: source-bound design review and live Project readback establish
planning state only; document checks establish mechanical conformance.
Completeness: all changed design groups are disposed above, and retirement sweeps
policy, skills, historical pilot documentation, linked planning text and the live board. This holds unless
new delivery evidence or an operator scope change requires a different grouping.

## Changed design path inventory

165 paths: 147 present, 18 removed/renamed at the source snapshot.

```text
design/08-stack-refactor-brief.md
design/09-decision-index.md
design/13-trace-content-and-redaction.md
design/19-media-comprehension.md
design/25-version-and-announcement.md
design/agent-worker/00-intent.md
design/agent-worker/10-worker-port.md
design/agent-worker/20-adapters.md
design/agent-worker/30-sandbox-port.md
design/agent-worker/40-models-egress-permissions.md
design/agent-worker/50-verification-and-migration.md
design/agent-worker/_index.yaml
design/artifact/00-design.md
design/capability-drop/01-when-stays-in-schema.md
design/cognition-loop/00-intent.md
design/cognition-loop/05-data-model.md
design/cognition-loop/10-tiers.md
design/cognition-loop/12-schemas.md
design/cognition-loop/15-map.md
design/cognition-loop/20-observe.md
design/cognition-loop/22-social-stream.md
design/cognition-loop/25-rollup.md
design/cognition-loop/30-cognition.md
design/cognition-loop/40-register.md
design/cognition-loop/50-character-and-standing.md
design/cognition-loop/55-self.md
design/cognition-loop/70-migration.md
design/cognition-loop/80-prior-art.md
design/cognition-loop/_index.yaml
design/computer-unit-scope/00-contract.md
design/computer-unit-scope/01-migration.md
design/computer-unit-scope/_index.yaml
design/context-plan/evidence/local_models.py
design/culture-tier/00-overview.md
design/discord-music/10-notification-routing.md
design/dreamer-agent-loop/02-instrument-blindness.md
design/dreamer-agent-loop/18-flows.md
design/dreamer-agent-loop/19-between-tiers.md
design/dreamer-agent-loop/26-standing.md
design/dreamer-agent-loop/27-note-dreamer.md
design/dreamer-agent-loop/33-note-dreamer-schedule.md
design/dreamer-agent-loop/34-note-dreamer-consolidation.md
design/dreamer-agent-loop/36-note-tier-review-draft-2026-09-03.md
design/dreamer-agent-loop/39-proactive-ooda-loop.md
design/dreamer-agent-loop/41-note-tier-time-is-optional.md
design/dreamer-agent-loop/42-note-format.md
design/evaluation/08-the-sdk-surface.md
design/evaluation/10-the-scenario-map.md
design/evaluation/11-the-sandbox-rebuild.md
design/fit/00-intent.md
design/fit/10-preference-model.md
design/fit/20-ranker.md
design/fit/30-recipes.md
design/fit/40-feedback.md
design/fit/50-verification.md
design/fit/60-migration.md
design/fit/_index.yaml
design/instagram-adoption/18-instagram-read-and-search.md
design/media-read-lane/BRIEF-impl-review.md
design/media-read-lane/BRIEF-timeout-derivation.md
design/notebook-memory-types/00-design.md
design/notebook-memory-types/10-relations.md
design/notebook-memory-types/20-shared-cognitive-state.md
design/notebook-memory-types/20-standing.md
design/notebook-memory-types/_index.yaml
design/personalisation/00-intent.md
design/personalisation/10-likes.md
design/personalisation/20-ranker.md
design/personalisation/25-personalisation.md
design/personalisation/30-recommendation-types.md
design/personalisation/40-feedback.md
design/personalisation/50-verification.md
design/personalisation/60-migration.md
design/personalisation/_index.yaml
design/platform-bridges/00-contract.md
design/platform-bridges/01-source-map.md
design/platform-bridges/02-wire-and-platforms.md
design/platform-bridges/03-ownership-and-recovery.md
design/platform-bridges/04-operations.md
design/platform-bridges/05-build-and-acceptance.md
design/platform-bridges/_index.yaml
design/platform-observations/00-overview.md
design/platform-observations/01-coverage.md
design/platform-observations/02-observation-contract.md
design/platform-observations/03-model-projections.md
design/platform-observations/04-build-and-acceptance.md
design/platform-observations/05-occasion-map.md
design/platform-observations/_index.yaml
design/policy-reload/00-design.md
design/private-dm.md
design/private-dm/00-contract.md
design/private-dm/01-current-flow.md
design/private-dm/02-storage.md
design/private-dm/03-execution.md
design/private-dm/04-build-and-acceptance.md
design/private-dm/05-cross-platform.md
design/private-dm/06-cross-unit-reads.md
design/private-dm/_index.yaml
design/proposed/14-grounding.md
design/proposed/19-presence-collector.md
design/proposed/23-named-places.md
design/proposed/24-honcho-integration-v2.md
design/recommendations/00-intent.md
design/recommendations/10-likes.md
design/recommendations/20-ranker.md
design/recommendations/30-recommendation-types.md
design/recommendations/40-feedback.md
design/recommendations/50-verification.md
design/recommendations/60-migration.md
design/recommendations/_index.yaml
design/room-topology/00-intent.md
design/room-topology/10-topology.md
design/room-topology/20-addressing.md
design/room-topology/30-what-reads-the-platform.md
design/room-topology/40-verification.md
design/room-topology/50-migration.md
design/room-topology/_index.yaml
design/self-awareness/00-overview.md
design/self-awareness/01-three-layers.md
design/self-awareness/07-room-tools-plan.md
design/self-growth/10-character.md
design/self-growth/20-self-block.md
design/self-growth/40-lessons.md
design/self-reference/01-appearance-doctrine.md
design/self-reference/02-canonical-assets.md
design/self-reference/05-visual-identity-spec.md
design/self-reference/07-native-and-embodied-form.md
design/self-reference/08-bauhinia-human-form.md
design/self-reference/09-mini08-balanced.md
design/self-reference/_index.yaml
design/standing-ledger/00-intent.md
design/standing-ledger/10-what-standing-holds.md
design/standing-ledger/20-the-ledger.md
design/standing-ledger/30-turn-records.md
design/standing-ledger/40-verification.md
design/standing-ledger/50-migration.md
design/standing-ledger/_index.yaml
design/subagent/00-design.md
design/tool-catalogue/05-measurement.md
design/unified-memory/00-intent.md
design/unified-memory/10-honcho-audit.md
design/unified-memory/15-memory-map.md
design/unified-memory/18-extraction.md
design/unified-memory/18-message-readings.md
design/unified-memory/25-memory-graph.md
design/unified-memory/27-the-memory-is-a-graph.md
design/unified-memory/28-provenance.md
design/unified-memory/29-one-cognition-session.md
design/unified-memory/30-identity-and-character.md
design/unified-memory/31-build-one-session.md
design/unified-memory/32-platform-observations.md
design/unified-memory/35-persona.md
design/unified-memory/40-prior-art.md
design/unified-memory/45-persona-adaptor.md
design/unified-memory/47-character-dimensions.md
design/unified-memory/50-learning-from-acts.md
design/unified-memory/60-build-step-3.md
design/unified-memory/65-build-step-3-vectors.md
design/unified-memory/70-honcho-cutover.md
design/unified-memory/75-typed-identity.md
design/unified-memory/76-typed-identity-build.md
design/unified-memory/_index.yaml
design/whatsapp-adoption/02-identity-and-policy.md
design/whatsapp-adoption/05-delegate-review.md
design/whatsapp-adoption/06-implementation-review.md
```
