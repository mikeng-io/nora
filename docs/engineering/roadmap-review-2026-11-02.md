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
| One memory and Cognition: recall, connect and revise understanding across turns | [one-session build](../design/cognition/graph.md), [graph](../design/cognition/graph.md), [Cognition](../design/cognition/graph.md) | Own outcome, In progress/Now. Recent source delivery includes graph, Register and Profile work; the November 2 snapshot explicitly leaves semantic longitudinal evidence, capture reliability, historical backfill preparation and cutover acceptance open. Do not make public benchmarks a cutover prerequisite. |
| Honest external memory-quality baseline | [existing outcome #435](https://github.com/mikeng-io/nora/issues/435), children #429–431 | Reuse Planned/Next. Keep LongMemEval-S → GroupMemBench → Mem2ActBench, one native delivery milestone, no duplicate umbrella in its count. No full-run result is claimed. |
| Continuity of decisions, arrangements and actual actions across rooms | [Notebook](../design/cognition/graph.md), [one Standing](../design/cognition/graph.md), [turn records](../design/cognition/context.md) | Own outcome, In progress/Now. Standing consolidation and typed Profile have merged source. Remaining scope is full memory-type migration and end-to-end continuity/actual-action evidence; never restore a separate shared-state block, conclusion tool or another cognitive writer. |
| Multi-step computer work delegated to Pi, with switchable worker and sandbox | [agent-worker intent](../design/agent-worker/00-intent.md), [adapters](../design/agent-worker/20-adapters.md), [sandbox port](../design/agent-worker/30-sandbox-port.md), [verification](../design/agent-worker/50-verification-and-migration.md) | One outcome, design reconciliation first (operator, 2026-11-04; issue #520); implementation not started. Proposed/Now reflects current design attention. Next scope: one VM per unit/canonical private-DM scope, reusable anonymous Chrome task tabs and direct screenshots with executor/model selection explicit before implementation; reuse of CDP/Flash is recommended, Jev/cua-s1 remain unselected alternatives. Existing Strands subagent stays Nora; Pi is the computer worker inside the sandbox. Streamed spawn, measured Pi adapter and scoped relay are delivery slices; ACP and another VM provider are later extensions, not new parallel projects. Current sbx microVM is not a future unbuilt capability. |
| Personalised replies and useful group recommendations | [personalisation](../design/cognition/emergence.md), [feedback](../design/cognition/emergence.md) | Own outcome, Proposed/Later. Likes, constraints, one ranker, venues/games and feedback share one engine. Historical fit/recommendations bundles were renamed, not three engines to build. Refresh graph-backed inputs and Standing dependencies before a bounded venue/reply slice. |
| One private conversation across linked platforms with curated shared understanding | [existing #124](https://github.com/mikeng-io/nora/issues/124), [private DM](../design/private-dm/README.md), [cross-unit reads](../design/private-dm/curation.md) | Reuse Proposed/Later. WhatsApp/Signal continuity, owner-map and backup prerequisites, default-unit writes and provenance-preserving publication remain the scope. Shared unit computer migration is already recorded implemented; do not repeat it as a prerequisite build. |
| Understand reactions, polls and room changes from captured evidence | [existing #433](https://github.com/mikeng-io/nora/issues/433), [observations](../design/platform-observations/README.md), [coverage](../design/platform-observations/observations.md) | Reuse Validating/Now. November 2 source includes typed observation foundation, read tools and Standing projections despite older proposed frontmatter. Complete platform-specific lifecycle/coverage and input readback; counts/reactions never become inferred agreement or consent. Telegram adoption is separate. |
| Keep receiving and recover work across application releases | [existing #422](https://github.com/mikeng-io/nora/issues/422), [bridges](../design/platform-bridges/00-contract.md) | Reuse Validating/Now. Source includes standalone bridges/durable handoff. Retain pending failure-window, recreation, platform and installed-wrapper proof as acceptance; design frontmatter alone cannot roll delivery back to not-built. |
| Join Telegram through the same platform contracts | [observation coverage](../design/platform-observations/observations.md), [platform adoption questions](../design/platform-observations/README.md) | Own outcome, Proposed/Later. First decide bot versus client mode and required account permissions/capabilities. Then trace identity/policy, ingest, replies/media, observations and recovery end to end. No account creation, token request or implementation is authorized by this card. |
| Inspect Nora's behavior and cost across deployments | [existing #440](https://github.com/mikeng-io/nora/issues/440), [observability](../operations/observability.md) | Reuse Validating/Now. SigNoz code migration is source-delivered; retained logs, four-producer correlation, usage/cost and installed telemetry acceptance remain evidence questions. Do not declare production cutover from merged source. |
| Consistent Mini/adult identity and usable modeled forms | [existing #245](https://github.com/mikeng-io/nora/issues/245), [representations](../design/self-reference/modeling.md), [Mini](../design/self-reference/identity.md) | Reuse Planned/Later. Approved 2D references and recent social artwork are delivered source assets. Orthographic consistency, mesh, rig and animation remain separately measured deliverables. Retain current Mini runtime direction. |

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
| notebook-memory-types, standing-ledger, cognition-loop | Continuity outcome. Retired dreamer/adaptor descriptions are history; one Standing owns current presentation. |
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
docs/design/08-stack-refactor-brief.md
docs/design/09-decision-index.md
docs/design/13-trace-content-and-redaction.md
docs/design/19-media-comprehension.md
docs/design/25-version-and-announcement.md
docs/design/agent-worker/00-intent.md
docs/design/agent-worker/10-worker-port.md
docs/design/agent-worker/20-adapters.md
docs/design/agent-worker/30-sandbox-port.md
docs/design/agent-worker/40-models-egress-permissions.md
docs/design/agent-worker/50-verification-and-migration.md
docs/design/agent-worker/_index.yaml
docs/design/artifact/README.md
docs/design/capability-drop/01-when-stays-in-schema.md
docs/design/cognition/loop.md
docs/design/cognition/loop.md
docs/design/cognition/loop.md
docs/design/cognition/loop.md
docs/design/computer-unit-scope/00-contract.md
docs/design/computer-unit-scope/01-migration.md
docs/design/computer-unit-scope/_index.yaml
scripts/context_decision_probe.py
docs/design/cognition/learning.md
docs/design/voice-channel/music.md
docs/design/cognition/loop.md
docs/design/cognition/graph.md
docs/design/evaluation/measurement.md
docs/design/evaluation/coverage.md
docs/design/evaluation/subject.md
docs/design/instagram-adoption/social.md
docs/design/media-read-lane/reading.md
docs/design/cognition/graph.md
docs/design/cognition/graph.md
docs/design/cognition/graph.md
docs/design/cognition/graph.md
docs/design/cognition/emergence.md
docs/design/cognition/emergence.md
docs/design/cognition/emergence.md
docs/design/cognition/emergence.md
docs/design/platform-bridges/00-contract.md
docs/design/platform-bridges/01-source-map.md
docs/design/platform-bridges/02-wire-and-platforms.md
docs/design/platform-bridges/03-ownership-and-recovery.md
docs/design/platform-bridges/04-operations.md
docs/design/platform-bridges/05-build-and-acceptance.md
docs/design/platform-bridges/_index.yaml
docs/design/platform-observations/README.md
docs/design/platform-observations/observations.md
docs/design/platform-observations/projections.md
docs/design/platform-observations/exposure.md
docs/design/policy-reload/README.md
docs/design/private-dm.md
docs/design/private-dm/README.md
docs/design/private-dm/admission.md
docs/design/private-dm/curation.md
docs/design/private-dm/workspaces.md
docs/design/system/grounding.md
docs/design/cognition/graph.md
docs/design/cognition/graph.md
docs/design/room-topology/00-intent.md
docs/design/room-topology/10-topology.md
docs/design/room-topology/20-addressing.md
docs/design/room-topology/30-what-reads-the-platform.md
docs/design/room-topology/40-verification.md
docs/design/room-topology/50-migration.md
docs/design/room-topology/_index.yaml
docs/design/self-awareness/README.md
docs/design/self-awareness/rooms.md
docs/design/cognition/learning.md
docs/design/self-reference/identity.md
docs/design/self-reference/references.md
docs/design/self-reference/modeling.md
docs/design/cognition/context.md
docs/design/cognition/context.md
docs/design/cognition/context.md
docs/design/cognition/context.md
docs/design/cognition/context.md
docs/design/cognition/context.md
docs/design/cognition/context.md
docs/design/subagent/00-design.md
docs/design/tool-catalogue/deferral.md
docs/design/cognition/graph.md
docs/design/cognition/graph.md
docs/design/whatsapp-adoption/identity.md
docs/design/whatsapp-adoption/README.md
```
