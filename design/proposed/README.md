# Proposed — NOT the architecture

Everything in this directory describes something that **does not exist yet** —
with the exceptions recorded in the table below. Audited 2026-09-08: three of
these had been BUILT and shipped while still sitting here described as
intentions, and eight files in this directory were missing from the table
entirely. A directory whose own index disagrees with its contents is worse than
no index, because it is read as authoritative.

It was separated out on 2026-08-06 because `design/` had accumulated eighteen
documents in four different states — `current`, `proposed`, `implemented`,
`open-decision` — several of which contradicted each other, and a reader could
not tell which described Nora and which described an intention.

**To learn what Nora actually is, read `ARCHITECTURE.md`.** To learn why she is
that way, read `design/09-decision-index.md`. Neither points here.

A document leaves this directory when the thing it describes is built, and it
is edited into `ARCHITECTURE.md` rather than promoted wholesale — a proposal
argues for a change, and once the change exists the argument is history, not
description.

| doc | proposes | state |
|---|---|---|
| `14-grounding.md` | four kinds of grounding: live transit data, routing, web search, page fetch | partly measured; `transit` is next to build |
| `15-gif-replies.md` | Giphy as a second reply verb | signal-cli attachments verified; nothing built |
| `26-media-replies-and-signal-stickers.md` | adapter boundary for Giphy delivery and Signal sticker reading | elaboration; nothing built |
| `16-multi-message-replies.md` | relaxing R-2 so one tag can send several messages | mechanism measured; nothing built |
| `18-context-budget-and-handles.md` | rationing fetched content per turn; message handles | **contested** — a review argued the budget is a component where a constant would do, and that the live `limit_total_tokens` counter should be read first. Its cheap half (the `↳` snippet) shipped in `624955d`; the rest is deferred. |
| `27-presence-analytics.md` | the DERIVED layer of the presence stack as a consumer-neutral substrate, plus the six-tier programme above it | **Tiers 1–2–3–5 BUILT** — `nora/analytics/` ships (DEC-115) and `where_been` is a live tool. **Amended 2026-09-17:** §1.3 records that every Tier 1 stay was an artefact of the sampler until `511e7147`, and the corpus has been re-derived. §3.7 (DEC-190, `docs/adr/0014`) adds place identity — a tagged place outranks an auto-identified one, lifting the one-home-one-work ceiling that has 88 stays linked up to 12.1 km from where they happened; designed, not built. §4.1 supersedes Tier 4's next-place framing with time-anchored occupancy and states its own accuracy figures as measured-once and NOT re-derivable — no committed instrument exists, and forecast scoring is outside the evaluation rebuild's boundary. Tier 4 is blocked on §3.7, not on elapsed time. Tier 6 speculative (issues #5, #7, #8, #9). The P0 spec it cited was in the deleted `docs/superpowers/` tree and resolves only in git history. **The split this row describes — built history plus an unbuilt proposal in one file under `proposed/` — is the condition the 2026-09-08 audit flagged; it is owed a resolution.** |
| `28-relevance-classifier.md` | A3 true-ambient: single cheap flash completion on the ambient path deciding engage yes/no; off-path bounded queue; shadow mode first | **brief only** — nothing built; replaces `VoiceNotePolicy` for untagged voice notes when it lands |
| `29-image-generation.md` | provider-agnostic `ImageGenerator` port; first impl = xAI subscription lane via Nora's own OAuth (DEC-137) | **proposal only** — nothing built; ships OFF (`[image_gen] enabled = false`) |
| `19-presence-collector.md` | the presence ingest collector | **BUILT** — `nora/presence/` ships and is wired in the composition root (DEC-54/55). Argument is history; `ARCHITECTURE.md` describes what exists. |
| `20-postgres-migration.md` | moving the record store to Postgres | **BUILT** — Postgres is the production store throughout `ARCHITECTURE.md`; `nora/infra/sql/` is the implementation. |
| `21-media-fetch.md` | a `media_fetch` tool | **BUILT** — the tool is in `nora/agent/tools.py`. Issues #116/#117 carry the live work on top of it. |
| `22-search-fetch.md` | web search and page fetch as tools | **BUILT** — `web_search`/`web_fetch` are in `nora/agent/tools.py` (DEC-73, DEC-116; Exa is the current backend). |
| `23-named-places.md` | naming derived places | **not built** — no `NamedPlace` anywhere in `nora/`. Issue #10 carries the reverse-geocoding trade-off it depends on. |
| `24-honcho-integration-v2.md` | a second-generation Honcho integration | **partly overtaken** — `nora/infra/honcho/` exists and ADR 0001/0002 settled the questions this argued about. Read the ADRs first. |
| `30-media-architecture.md` | the media custody and description architecture | **BUILT** — `nora/domain/media.py` plus DEC-45; issues #116/#117 are the live continuation. |
| `31-recall-query-generation.md` | model-generated queries for recall tools | **BUILT** — landed in PR #92. |
| `32-shared-media-register.md` | framing a shared picture, clip or Instagram item as her own notes rather than a report to relay: the `media_fetch` and Instagram read notes, and one `conduct.md` paragraph | **built on a branch, not deployed** (2026-09-26); the study is the evidence, a live read-back after deploy confirms or reopens it |

Each carries corrections applied after an adversarial review on the day it was
written. Read the corrections — several are recorded as struck-through claims
with the reason, deliberately left visible so the same wrong turn is not taken
twice.
