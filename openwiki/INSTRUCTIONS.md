# Nora's public implementation wiki

Write for people trying to understand Nora, then for contributors and coding
agents. Explain connected behaviours and ownership, not a directory inventory.
Nora is an experimental, self-hosted social agent with continuity in persistent
state outside individual model invocations. The README owns her public framing.

Use current source and focused tests as implementation evidence. ARCHITECTURE.md
is the map; ADRs explain decisions. A design or historical measurement does not
prove current implementation, live enablement or effectiveness. Never inspect
live services or private files to generate this public wiki. Explain capability
flags and room grants without publishing deployed values. Record uncertainty.

Cover sessionless continuity; ingestion, participation and cognition; unified
memory and Persona's incomplete Honcho cutover; contextual learning; persistent
computer and browser work; configuration and contribution workflows. Keep these
as connected concepts with useful cross-links. English technical prose; any
Chinese text must be Traditional Chinese. The initial seven pages are an entry
point, not a scope limit. Expand and reorganize the wiki around meaningful
subsystems when the implementation warrants a dedicated explanation.

This is an implementation reference for engineers. An overview of capabilities
is not enough. Trace representative flows from their actual entrypoints through
decisions, tools, persistence and delivery. Explain concrete types, interfaces,
state transitions, ordering, ownership and failure paths, with source-grounded
Claims and focused tests. Use diagrams where they clarify those relationships;
use tables for state and configuration contracts. Prefer working examples from
public templates and clearly synthetic examples over vague feature summaries.

Research ingestion and normalization, context assembly and explicit reply
delivery, the participation window and Relevance Gate, stimulus scheduling and
bounded Cognition sessions, proactive occasions, Record and indexed retrieval,
Persona provenance and supersession, contextual learning, sandbox lifecycle,
computer and browser execution, policy/disclosure boundaries, observability,
evaluation and deployment. Follow callers and stores across these boundaries;
document what actually exists, and distinguish optional, incomplete and retired
paths. Explain relevant configuration keys and extension seams. Do not turn this
into a file inventory or pad it to meet a page or word count.

Keep quickstart concise and update its navigation map when pages change. Give
each substantial subsystem enough depth for a reader to trace a request,
diagnose a failure and locate the code and tests that establish the contract.

Never include credentials, personal messages, identities, account identifiers,
location histories, internal addresses or private stimuli. Do not infer facts
about a person. Synthetic examples must be clearly labelled. No unmeasured
comparisons with other AI products. No claim that all capabilities are enabled.

Maintain only openwiki/ Markdown and OpenWiki-owned metadata, plus the tool's
managed AGENTS.md and CLAUDE.md blocks. Do not change runtime source, workflows,
the README, doctrine, historical designs or this brief. Keep source-grounded
Claims for substantive behaviour and boundaries; cite actual source spans.
